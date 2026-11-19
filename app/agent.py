"""Connect meeting audio to Pipecat, the meeting tools, and a speaking avatar."""

import asyncio
import logging

from pipecat.frames.frames import OutputImageRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.processors.idle_frame_processor import IdleFrameProcessor
from pipecat.services.simli.video import SimliVideoService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from app.addressed_audio import ADMISSION_SAMPLE_RATE, AddressedAudio
from app.config import require_env, session_timeout
from app.meeting_agent import OUTPUT_SAMPLE_RATE, DeepgramVoiceAgent
from app.meeting_tools import MeetingState, MeetingTools
from app.recall_client import RecallClient, RecallError
from app.speech_output import AudioGate
from app.tool_registration import VOICE_AGENT_FUNCTIONS, VoiceAgentTools

logger = logging.getLogger("meeting-demo")
# Preserve the preset's native framing; a smaller canvas crops its face.
AVATAR_SIZE = 768

INSTRUCTIONS = """You are pipecat demo, a concise meeting assistant that hears audio only.
Act only on direct requests addressed to 'Assistant' or 'pipecat demo', optionally
after a greeting such as 'Hey'. Name mentions, quotations, discussion and
hypothetical commands are not requests.
Join silently. A chat introduction is posted automatically; never repeat it aloud.
Speak naturally and clearly, with a warm, conversational voice. Answer directly
and use the context of earlier addressed requests. Give enough detail to be useful:
usually a few sentences, with more explanation when the question needs it. Ask a
brief clarification when a request is ambiguous. Avoid unsolicited advice and
repeated offers of help. Understand natural phrasing rather than requiring command names.

Use tools only for requested actions. Speak after all required tool results
arrive, and keep acknowledgments brief. Each tool also posts chat feedback.
Starting a share does not authorize posting its link. When both are requested,
wait for the share to succeed, then call send_current_page_link.
Never repeat an action or retry an
uncertain or failed action. An in-progress tool is already running; do not call it
again. After all requested actions finish, a short confirmation is enough.
Report only what tools confirm. Never claim a page visibly loaded, read unseen
page contents, or invent a URL. Ask for a URL if the intended page is unclear.

Mute controls speech only; tools continue while muted. Stop listening separately
pauses answers and actions until an addressed standalone start-listening command.
Both preserve the mute setting. Never replay skipped requests or unheard answers.
"""

INTRO_CHAT_MESSAGE = (
    "Start requests with 'Assistant, [command]'.\n\n"
    "See the full list of commands here:\n"
    "https://github.com/recallai/agent-demo-pipecat#6-try-the-agent"
)


def voice_agent_settings() -> dict:
    return {
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": ADMISSION_SAMPLE_RATE},
            "output": {"encoding": "linear16", "sample_rate": OUTPUT_SAMPLE_RATE, "container": "none"},
        },
        "agent": {
            "language": "en",
            "listen": {"provider": {
                "type": "deepgram", "version": "v2", "model": "flux-general-en",
                "eot_timeout_ms": 1000,
            }},
            "think": {
                "provider": {"type": "open_ai", "model": require_env("DEEPGRAM_AGENT_MODEL")},
                "prompt": INSTRUCTIONS,
                "functions": list(VOICE_AGENT_FUNCTIONS),
            },
            "speak": {"provider": {
                "type": "deepgram", "model": require_env("DEEPGRAM_AGENT_VOICE"),
            }},
        },
    }


async def run_agent(connection: SmallWebRTCConnection, bot_id: str) -> None:
    finished = asyncio.Event()
    state = MeetingState()
    deepgram_key = require_env("DEEPGRAM_API_KEY")
    transport = SmallWebRTCTransport(connection, TransportParams(
        audio_in_enabled=True, audio_out_enabled=True, video_out_enabled=True,
        video_out_width=AVATAR_SIZE, video_out_height=AVATAR_SIZE,
        # Sample the latest avatar frame without buffering every generated frame.
        video_out_is_live=False,
        video_out_framerate=15,
    ))
    before_avatar = AudioGate(state)
    after_avatar = AudioGate(state)
    pipeline_failed = False

    async def on_avatar_stalled(_processor):
        nonlocal pipeline_failed
        pipeline_failed = True
        logger.error("The required avatar stopped producing video; ending the session")
        finished.set()

    async with RecallClient(
        require_env("RECALL_API_KEY"), require_env("RECALL_API_BASE_URL"),
    ) as client:
        tools = MeetingTools(client, bot_id, state, before_avatar.silence)
        admission = AddressedAudio(
            api_key=deepgram_key, state=state, resume_listening=tools.start_listening,
        )
        tool_bridge = VoiceAgentTools(tools, finished)
        voice = DeepgramVoiceAgent(deepgram_key, voice_agent_settings(), state, tool_bridge)
        avatar = SimliVideoService(
            api_key=require_env("SIMLI_API_KEY"), face_id=require_env("SIMLI_FACE_ID"),
            max_session_length=session_timeout(), max_idle_time=session_timeout(),
            framing=SimliVideoService.Framing(streaming_width=AVATAR_SIZE, streaming_height=AVATAR_SIZE),
        )
        pipeline = Pipeline([
            transport.input(),
            admission,
            voice,
            before_avatar,
            avatar,
            IdleFrameProcessor(callback=on_avatar_stalled, timeout=15, types=[OutputImageRawFrame]),
            after_avatar,  # Simli can still return audio it received before a mute.
            transport.output(),
        ])
        # Native RTVI handles Recall's ready handshake and playback interruptions.
        worker = PipelineWorker(pipeline, params=PipelineParams(
            audio_in_sample_rate=ADMISSION_SAMPLE_RATE, audio_out_sample_rate=OUTPUT_SAMPLE_RATE,
        ), idle_timeout_secs=None, enable_rtvi=True,
            rtvi_observer_params=RTVIObserverParams(ignored_sources=[admission]),
        )

        @transport.event_handler("on_client_connected")
        async def on_connected(_transport, _peer):
            try:
                await client.send_chat_message(bot_id, INTRO_CHAT_MESSAGE)
            except RecallError:
                logger.warning("Could not post the introductory chat message; not retrying")

        @transport.event_handler("on_client_disconnected")
        async def on_disconnected(_transport, _peer):
            finished.set()

        @worker.event_handler("on_pipeline_error")
        async def on_pipeline_error(_worker, frame):
            nonlocal pipeline_failed
            pipeline_failed = True
            logger.error(
                "The meeting pipeline failed in %s; ending the session",
                type(frame.processor).__name__,
            )
            finished.set()

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        runner_task = asyncio.create_task(runner.run())
        finished_task = asyncio.create_task(finished.wait())
        try:
            await asyncio.wait((runner_task, finished_task), return_when=asyncio.FIRST_COMPLETED)
        finally:
            finished_task.cancel()
            await worker.cancel()
            results = await asyncio.gather(runner_task, finished_task, return_exceptions=True)
            await tool_bridge.drain()
        if isinstance(results[0], Exception):
            raise RuntimeError("The agent worker failed.") from results[0]
        if pipeline_failed:
            raise RuntimeError("The voice or avatar pipeline failed.")
        if not state.is_leaving:
            # A lost media connection can leave a silent bot in the meeting.
            # Signal the session owner to request departure during cleanup.
            raise RuntimeError("The meeting connection ended.")
