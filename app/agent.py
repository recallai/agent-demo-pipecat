"""Connect meeting audio to Pipecat, the meeting tools, and a speaking avatar."""

import asyncio
import logging

from pipecat.frames.frames import LLMContextFrame, OutputImageRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.idle_frame_processor import IdleFrameProcessor
from pipecat.services.openai.realtime import events
from pipecat.services.simli.video import SimliVideoService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from app.browser_transport import BrowserTransport
from app.config import require_env, session_timeout
from app.meeting_agent import MeetingAgent
from app.meeting_tools import MeetingState, MeetingTools
from app.recall_client import RecallClient, RecallError
from app.speech_output import AudioGate
from app.tool_registration import register_meeting_tools

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

Use tools only for requested actions, without spoken filler or recaps. Each tool
posts its own chat feedback. Do not duplicate that feedback. Starting a share
does not authorize posting its link. When both are requested, wait for the share
to succeed, then call send_current_page_link. Never repeat an action or retry an
uncertain or failed action. An in-progress tool is already running; do not call it
again. After all explicitly requested actions finish, return no spoken text.
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


async def run_agent(connection, bot_id: str) -> None:
    finished = asyncio.Event()
    state = MeetingState()
    api_key = require_env("OPENAI_API_KEY")
    transport = BrowserTransport(connection, TransportParams(
        audio_in_enabled=True, audio_out_enabled=True, video_out_enabled=True,
        video_out_is_live=False, video_out_width=AVATAR_SIZE, video_out_height=AVATAR_SIZE,
        video_out_framerate=15,
    ))
    speech = AudioGate(state)
    pipeline_failed = False

    async def on_avatar_stalled(_processor):
        nonlocal pipeline_failed
        pipeline_failed = True
        logger.error("The required avatar stopped producing video; ending the session")
        finished.set()

    async with RecallClient(
        require_env("RECALL_API_KEY"), require_env("RECALL_API_BASE_URL"),
    ) as client:
        tools = MeetingTools(client, bot_id, state, speech.silence)
        llm = MeetingAgent(
            state=state, resume_listening=tools.start_listening, api_key=api_key,
            settings=MeetingAgent.Settings(
                model=require_env("REALTIME_MODEL"), system_instruction=INSTRUCTIONS,
                session_properties=events.SessionProperties(
                    output_modalities=["audio"], reasoning=events.Reasoning(effort="low"),
                    audio=events.AudioConfiguration(
                        input=events.AudioInput(
                            transcription=events.InputAudioTranscription(
                                model="gpt-4o-mini-transcribe", language="en",
                            ),
                        ),
                        output=events.AudioOutput(voice=require_env("REALTIME_VOICE")),
                    ),
                ),
            ),
        )
        context = LLMContext(tools=register_meeting_tools(llm, tools, finished))
        # Admission is controlled by our service, not automatic transcript turns.
        aggregators = LLMContextAggregatorPair(context, realtime_service_mode=False)
        avatar = SimliVideoService(
            api_key=require_env("SIMLI_API_KEY"), face_id=require_env("SIMLI_FACE_ID"),
            max_session_length=session_timeout(), max_idle_time=session_timeout(),
            framing=SimliVideoService.Framing(streaming_width=AVATAR_SIZE, streaming_height=AVATAR_SIZE),
        )
        pipeline = Pipeline([
            transport.input(),
            aggregators.user(),
            llm,
            speech,
            avatar,
            IdleFrameProcessor(callback=on_avatar_stalled, timeout=15, types=[OutputImageRawFrame]),
            AudioGate(state),
            transport.output(),
            aggregators.assistant(),
        ])
        worker = PipelineWorker(pipeline, params=PipelineParams(
            audio_in_sample_rate=16000, audio_out_sample_rate=24000,
        ), idle_timeout_secs=None)

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
        async def on_pipeline_error(_worker, _frame):
            nonlocal pipeline_failed
            pipeline_failed = True
            logger.error("A voice or avatar service failed; ending the meeting session")
            finished.set()

        await worker.queue_frame(LLMContextFrame(context))
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
        if isinstance(results[0], Exception):
            raise RuntimeError("The agent worker failed.") from results[0]
        if pipeline_failed:
            raise RuntimeError("The voice or avatar pipeline failed.")
        if not state.is_leaving:
            # A lost media connection can leave a silent bot in the meeting.
            # Signal the session owner to request departure during cleanup.
            raise RuntimeError("The meeting connection ended.")
