"""Run an admitted audio turn through Deepgram's Voice Agent."""

import asyncio
import logging
from collections import deque

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMContextUserTurnFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.time import time_now_iso8601

from app.addressed_audio import ADMISSION_SAMPLE_RATE, AddressedAudioFrame
from app.meeting_tools import MeetingState
from app.tool_registration import VoiceAgentTools
from app.voice_agent_connection import VoiceAgentConnection, VoiceAgentError, open_voice_agent

OUTPUT_SAMPLE_RATE = 24000
INPUT_CHUNK_BYTES = ADMISSION_SAMPLE_RATE * 2 * 80 // 1000
TRAILING_SILENCE = bytes(ADMISSION_SAMPLE_RATE * 2 * 18 // 10)
MAX_TURN_SECONDS = 60
REPLY_DRAIN_SECONDS = 0.4
logger = logging.getLogger("meeting-demo")


class DeepgramVoiceAgent(FrameProcessor):
    def __init__(self, api_key: str, settings: dict, state: MeetingState, tools: VoiceAgentTools):
        super().__init__()
        self._api_key = api_key
        self._settings = settings
        self._state = state
        self._tools = tools
        self._history: deque[dict] = deque(maxlen=24)
        self._turn_task: asyncio.Task | None = None

    def _current(self, turn: AddressedAudioFrame) -> bool:
        return (
            turn.generation == self._state.generation
            and self._state.is_listening
            and not self._state.is_leaving
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, AddressedAudioFrame):
            if self._current(frame):
                await self.push_frame(InterruptionFrame())
                await self._cancel_turn()
                if self._current(frame):
                    context_turn = LLMContextUserTurnFrame(
                        text=frame.text, timestamp=time_now_iso8601(),
                    )
                    context_turn.metadata["request_generation"] = frame.generation
                    await self.push_frame(context_turn)
                    self._turn_task = self.create_task(self._respond(frame))
            return
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self._cancel_turn()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel_turn()
        await super().cleanup()

    async def _cancel_turn(self):
        task, self._turn_task = self._turn_task, None
        if task is not None and not task.done():
            await self.cancel_task(task)

    async def _respond(self, turn: AddressedAudioFrame):
        if not self._current(turn):
            return
        # Voice Agent PCM has no turn ID. A separate socket per turn prevents
        # superseded audio and function calls crossing into a new reply.
        settings = {
            **self._settings,
            "agent": {
                **self._settings["agent"],
                "context": {"messages": list(self._history)},
            },
        }
        connected = False
        try:
            async with asyncio.timeout(MAX_TURN_SECONDS):
                async with open_voice_agent(self._api_key, settings) as voice:
                    connected = True
                    audio = turn.audio + TRAILING_SILENCE
                    for start in range(0, len(audio), INPUT_CHUNK_BYTES):
                        if not self._current(turn):
                            return
                        await voice.send_audio(audio[start:start + INPUT_CHUNK_BYTES])
                    if self._current(turn):
                        await self._stream_reply(voice, turn)
        except (VoiceAgentError, TimeoutError) as error:
            if self._current(turn):
                reason = str(error) if isinstance(error, VoiceAgentError) else "Deepgram Voice Agent timed out."
                if connected:
                    # A transport failure during a reply ends this request.
                    # The next admitted request opens a new socket.
                    logger.warning("%s", reason)
                else:
                    await self.push_error(reason, force_treat_as_permanent=True)
        except Exception as error:
            if self._current(turn):
                await self.push_error(
                    f"Deepgram reply handling failed ({type(error).__name__}).",
                    force_treat_as_permanent=True,
                )

    async def _stream_reply(self, voice: VoiceAgentConnection, turn: AddressedAudioFrame):
        spoken = False
        speaking = False
        finished_reply = False
        assistant_text = ""
        completed: dict[tuple[str, str], str] = {}
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            while self._current(turn):
                if finished_reply:
                    # A function request may follow a spoken acknowledgment.
                    try:
                        async with asyncio.timeout(REPLY_DRAIN_SECONDS):
                            event = await voice.receive()
                    except TimeoutError:
                        break
                else:
                    event = await voice.receive()
                if not self._current(turn):
                    break
                if isinstance(event, bytes):
                    finished_reply = False
                    if self._state.is_muted or not event or len(event) % 2:
                        continue
                    if not speaking:
                        await self.push_frame(TTSStartedFrame())
                        speaking = True
                        spoken = True
                    await self.push_frame(TTSAudioRawFrame(
                        audio=event, sample_rate=OUTPUT_SAMPLE_RATE, num_channels=1,
                    ))
                elif event["type"] == "FunctionCallRequest":
                    finished_reply = False
                    await self._run_functions(voice, event, turn, completed)
                elif event["type"] == "ConversationText" and event.get("role") == "assistant":
                    content = event.get("content")
                    if isinstance(content, str):
                        assistant_text = (assistant_text + " " + content).strip()[:2000]
                elif event["type"] == "AgentAudioDone":
                    if speaking:
                        await self.push_frame(TTSStoppedFrame())
                        speaking = False
                    finished_reply = True
        finally:
            if self._current(turn):
                if speaking:
                    await self.push_frame(TTSStoppedFrame())
                await self.push_frame(LLMFullResponseEndFrame())

        if finished_reply and spoken and assistant_text and self._current(turn):
            self._history.extend([
                {"type": "History", "role": "user", "content": turn.text[:2000]},
                {"type": "History", "role": "assistant", "content": assistant_text},
            ])

    async def _run_functions(
        self, voice: VoiceAgentConnection, event: dict, turn: AddressedAudioFrame,
        completed: dict[tuple[str, str], str],
    ):
        calls = event.get("functions")
        if not isinstance(calls, list):
            return
        for call in calls:
            if not self._current(turn):
                return
            response = await self._tools.execute_call(turn.generation, call, completed)
            if not self._current(turn):
                # A control or newer request ended this turn. Close its socket.
                return
            if response is not None:
                await voice.send_event(response)
