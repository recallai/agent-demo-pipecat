"""Keep muted and interrupted speech out of meeting audio."""

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextUserTurnFrame,
    OutputAudioRawFrame,
    TextFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.meeting_tools import MeetingState


class AudioGate(FrameProcessor):
    """Keep only the current request's speech before and after Simli."""

    def __init__(self, state: MeetingState):
        super().__init__()
        self._state = state
        self._request_generation: int | None = None
        self._audio_generation: int | None = None
        self._blocked = True

    def _can_speak(self) -> bool:
        return (
            not self._blocked
            and self._request_generation == self._state.generation
            and not self._state.is_muted
            and self._state.is_listening
            and not self._state.is_leaving
        )

    async def silence(self) -> None:
        """Clear avatar and meeting output without cancelling a running tool."""
        self._audio_generation = None
        self._blocked = True
        await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.UPSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMContextUserTurnFrame):
            self._request_generation = frame.metadata.get("request_generation")
            self._audio_generation = None
            self._blocked = False
        elif isinstance(frame, InterruptionFrame):
            self._audio_generation = None
            self._blocked = True
        elif isinstance(frame, TTSStartedFrame):
            self._audio_generation = self._request_generation if self._can_speak() else None
        elif isinstance(frame, OutputAudioRawFrame):
            if not self._can_speak() or self._audio_generation != self._state.generation:
                return
        elif isinstance(frame, TextFrame) and not self._can_speak():
            return
        # Simli returns synchronized audio after the original TTSStoppedFrame.
        # Closing there would cut off valid speech; interruption closes it instead.
        await self.push_frame(frame, direction)
