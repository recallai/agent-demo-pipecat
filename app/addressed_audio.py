"""Use Flux to admit addressed turns before sending their PCM to the Voice Agent."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    DataFrame,
    EagerTranscriptionFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService

from app.meeting_tools import MeetingState
from app.request_filter import ADDRESS, RESUME

ADMISSION_SAMPLE_RATE = 16000
_BYTES_PER_SECOND = ADMISSION_SAMPLE_RATE * 2
_HISTORY_SECONDS = 30
_RESULT_TIMEOUT_SECONDS = 10
_audio_capture = ContextVar[tuple[float, bool] | None]("audio_capture", default=None)


@dataclass
class AddressedAudioFrame(DataFrame):
    """One verified request as mono, 16 kHz, signed 16-bit little-endian PCM."""

    audio: bytes = field(repr=False)
    text: str = field(repr=False)
    generation: int


@dataclass
class _AudioChunk:
    offset: int
    pcm: bytes = field(repr=False)
    start_time: float
    was_listening: bool

    @property
    def end(self) -> int:
        return self.offset + len(self.pcm)

    def trim_start(self, offset: int) -> None:
        skipped = offset - self.offset
        self.pcm = self.pcm[skipped:]
        self.start_time += skipped / _BYTES_PER_SECOND
        self.offset = offset


class AddressedAudio(DeepgramFluxSTTService):
    """Match finalized Flux word times to the PCM sent on that same connection."""

    def __init__(
        self,
        *,
        api_key: str,
        state: MeetingState,
        resume_listening: Callable[[], Awaitable[str]],
        **kwargs,
    ):
        super().__init__(
            api_key=api_key,
            sample_rate=ADMISSION_SAMPLE_RATE,
            audio_passthrough=False,
            should_interrupt=False,
            settings=self.Settings(model="flux-general-en"),
            **kwargs,
        )
        self._state = state
        self._resume_listening = resume_listening
        self._send_lock = asyncio.Lock()
        self._stream = None
        self._request_id: str | None = None
        self._last_turn_index = -1
        self._offset = 0
        self._history: deque[_AudioChunk] = deque()

    def _reset_history(self, stream: object | None) -> None:
        self._stream = stream
        self._request_id = None
        self._last_turn_index = -1
        self._offset = 0
        self._history.clear()

    async def _handle_connection_established(self, data: dict) -> None:
        async with self._send_lock:
            if self._stream is not self._websocket:
                self._reset_history(self._websocket)
                request_id = data.get("request_id")
                if isinstance(request_id, str):
                    self._request_id = request_id
        await super()._handle_connection_established(data)

    async def _disconnect_websocket(self) -> None:
        try:
            await super()._disconnect_websocket()
        finally:
            self._reset_history(None)

    async def process_audio_frame(self, frame: InputAudioRawFrame, direction: FrameDirection):
        # A new transcription stream must not reopen a turn buffered before reconnect.
        if direction != FrameDirection.DOWNSTREAM or self._reconnecting or frame.num_channels != 1:
            return
        capture = _audio_capture.set((time.time(), self._state.is_listening))
        try:
            await super().process_audio_frame(frame, direction)
        finally:
            _audio_capture.reset(capture)

    async def _transport_send_audio(self, audio: bytes) -> None:
        # The pinned Flux SDK sends both resampled audio and watchdog silence
        # through this hook. Keeping their order aligns Flux's sample offsets.
        async with self._send_lock:
            stream = self._websocket
            # Start the sample timeline on Flux's Connected event. Also stop
            # forwarding on a failed send until the SDK creates a fresh socket.
            if stream is None or self._stream is not stream or not self._request_id or not audio:
                return
            captured_at, was_listening = _audio_capture.get() or (time.time(), self._state.is_listening)
            chunk = _AudioChunk(
                self._offset, audio, captured_at - len(audio) / _BYTES_PER_SECOND, was_listening,
            )
            try:
                await super()._transport_send_audio(audio)
            except (Exception, asyncio.CancelledError):
                # A failed send may have written some bytes. This socket's
                # subsequent offsets are no longer safe to match.
                if self._stream is stream:
                    self._request_id = None
                    self._history.clear()
                raise
            if self._websocket is stream and self._stream is stream:
                self._offset = chunk.end
                self._history.append(chunk)
                cutoff = self._offset - _HISTORY_SECONDS * _BYTES_PER_SECOND
                while self._history[0].end <= cutoff:
                    self._history.popleft()
                if self._history[0].offset < cutoff:
                    self._history[0].trim_start(cutoff)

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if isinstance(frame, TranscriptionFrame):
            if direction == FrameDirection.DOWNSTREAM and frame.finalized:
                await self._admit(frame)
            return
        if isinstance(frame, (
            InterimTranscriptionFrame, EagerTranscriptionFrame,
            ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame,
        )):
            return
        await super().push_frame(frame, direction)

    async def _admit(self, transcript: TranscriptionFrame) -> None:
        address = ADDRESS.match(transcript.text)
        request = transcript.text[address.end():].strip() if address else ""
        data = transcript.result
        if not isinstance(data, dict) or data.get("event") != "EndOfTurn":
            return
        async with self._send_lock:
            index = data.get("turn_index")
            if (not self._request_id or data.get("request_id") != self._request_id
                    or type(index) is not int or index <= self._last_turn_index):
                return
            self._last_turn_index = index
            if not request or self._state.is_leaving:
                return
            matched = self._match_turn(data)
        if matched is None:
            return
        pcm, was_listening, started_at = matched
        cutoff = self._state.listening_since
        if cutoff is not None and started_at <= cutoff:
            return
        if not self._state.is_listening:
            if not was_listening and RESUME.fullmatch(request):
                await self._resume_listening()
            return
        if not was_listening:
            return

        self._state.generation += 1
        await self.push_frame(AddressedAudioFrame(pcm, transcript.text, self._state.generation))

    def _match_turn(self, data: dict) -> tuple[bytes, bool, float] | None:
        words = data.get("words")
        if not self._history or not isinstance(words, list) or not words:
            return None
        first_word, last_word = words[0], words[-1]
        if not isinstance(first_word, dict) or not isinstance(last_word, dict):
            return None
        times = (
            data.get("audio_window_start"), first_word.get("start"),
            last_word.get("end"), data.get("audio_window_end"),
        )
        latest = self._offset / _BYTES_PER_SECOND
        if any(type(t) not in (int, float) or not 0 <= t <= latest + 1 / ADMISSION_SAMPLE_RATE
               for t in times):
            return None
        window_start, start, end, window_end = (
            round(t * ADMISSION_SAMPLE_RATE) * 2 for t in times
        )
        # Use the words inside the turn window, excluding surrounding audio.
        if not (window_start <= start < end <= window_end
                and self._history[0].offset <= start and end <= self._offset):
            return None
        chunks = [chunk for chunk in self._history if chunk.offset < end and start < chunk.end]
        first, last = chunks[0], chunks[-1]
        ended_at = last.start_time + (end - last.offset) / _BYTES_PER_SECOND
        if time.time() - ended_at > _RESULT_TIMEOUT_SECONDS:
            return None
        started_at = first.start_time + (start - first.offset) / _BYTES_PER_SECOND
        pcm = b"".join(chunk.pcm[max(0, start - chunk.offset):end - chunk.offset] for chunk in chunks)
        return pcm, first.was_listening, started_at
