"""Pipecat meeting agent: admit addressed native audio before replies or tools."""

import time
from collections import deque
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import LLMContextUserTurnFrame, LLMFullResponseEndFrame
from pipecat.services.openai.realtime import events
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.utils.time import time_now_iso8601

from app.meeting_tools import MeetingState
from app.request_filter import ADDRESS, RESUME


class AdmittedResponse(events.ResponseProperties):
    # Pipecat 1.16 does not expose the API's explicit response input yet.
    input: list[dict[str, str]]


class AdmittedResponseCreate(events.ResponseCreateEvent):
    response: AdmittedResponse


class MeetingAgent(OpenAIRealtimeLLMService):
    def __init__(self, state: MeetingState,
                 resume_listening: Callable[[], Awaitable[str]] | None = None, **kwargs):
        kwargs.setdefault("run_in_parallel", False)  # Pipecat serializes meeting tools.
        super().__init__(**kwargs)
        self.state = state
        self.resume_listening = resume_listening
        self._admitted_items: dict[str, None] = {}
        self._admitted_generation: int | None = None
        self._current_input_id: str | None = None
        self._turn_used_tools = False
        self._waiting_generation: int | None = None
        self._response_generations: dict[str, int] = {}
        self._created_generations: deque[int] = deque()
        self._function_generations: dict[str, int] = {}
        self._active_response_id: str | None = None
        self._handled_inputs: set[str] = set()
        self._speech_starts: dict[str, float | None] = {}
        self._last_accepted_start = float("-inf")
        self._audio_position_ms = 0.0
        self._audio_times: deque[tuple[float, float]] = deque(maxlen=3000)
        properties = self._settings.session_properties.model_copy(deep=True)
        properties.audio = properties.audio or events.AudioConfiguration()
        properties.audio.input = properties.audio.input or events.AudioInput()
        properties.audio.input.turn_detection = events.SemanticTurnDetection(
            create_response=False, interrupt_response=False,
        )
        # Explicit item references must not silently disappear through truncation.
        properties.truncation = "disabled"
        self._settings.session_properties = properties

    def function_call_generation(self, call_id: str) -> int:
        return self._function_generations.get(call_id, -1)

    def _can_respond(self) -> bool:
        return (self._context is not None and self.state.is_listening
                and not self.state.is_leaving
                and self._admitted_generation == self.state.generation)

    def _current_response(self, response_id: str | None) -> bool:
        return (response_id is not None and self._can_respond()
                and self._response_generations.get(response_id) == self.state.generation)

    async def _create_response(self):
        # Let Pipecat initialize context and tools; only an admitted turn may reply.
        if self._can_respond():
            await super()._create_response()

    async def send_client_event(self, event):
        if isinstance(event, events.ResponseCreateEvent):
            if not self._can_respond():
                # A control can invalidate the turn while native _create_response
                # awaits its start frame/metrics, before it sends anything.
                self._response_active = False
                self._response_create_owed = False
                self._results_awaiting_response = False
                self._waiting_generation = None
                await self.stop_all_metrics()
                await self.push_frame(LLMFullResponseEndFrame())
                return
            properties = event.response.model_dump(exclude_none=True) if event.response else {}
            speech = "muted" if self.state.is_muted else "enabled"
            properties["instructions"] = (
                f"{self._settings.system_instruction or ''}\n\n"
                f"Current application state: listening is enabled; speech playback is {speech}. "
                "This current state supersedes older control-state reports. Speech mute only "
                "controls audio playback: all meeting tools remain enabled while listening. "
                "Never refuse a requested tool because speech is muted, and never announce "
                "the mute state unless explicitly asked."
            )
            properties["input"] = [
                {"type": "item_reference", "id": item_id} for item_id in self._admitted_items
            ]
            event = AdmittedResponseCreate(
                event_id=event.event_id, response=AdmittedResponse(**properties),
            )
            # Only results included in this input snapshot have been consumed.
            self._results_awaiting_response = False
            self._created_generations.append(self.state.generation)
            self._waiting_generation = None
        await super().send_client_event(event)
        if isinstance(event, events.ConversationItemCreateEvent):
            item = event.item
            if item.type == "function_call_output" and item.call_id in self._function_generations:
                self._admitted_items[item.id] = None

    async def _send_user_audio(self, frame):
        if not self._api_session_ready:
            return  # Do not expose speech to the server's default automatic VAD.
        duration = len(frame.audio) / (frame.sample_rate * frame.num_channels * 2)
        self._audio_times.append((self._audio_position_ms, time.time() - duration))
        self._audio_position_ms += duration * 1000
        await super()._send_user_audio(frame)

    async def _handle_evt_speech_started(self, event):
        # Native VAD must not interrupt a reply merely because someone talks.
        self._speech_starts[event.item_id] = next((
            timestamp + (event.audio_start_ms - position) / 1000
            for position, timestamp in reversed(self._audio_times)
            if position <= event.audio_start_ms
        ), None)

    async def _discard_input(self, item_id: str):
        await self.send_client_event(events.ConversationItemDeleteEvent(item_id=item_id))

    async def handle_evt_input_audio_transcription_completed(self, event):
        if event.item_id in self._handled_inputs:
            return
        self._handled_inputs.add(event.item_id)
        started = self._speech_starts.pop(event.item_id, None)
        address = ADDRESS.match(event.transcript)
        request = event.transcript[address.end():].strip() if address else ""
        cutoff = self.state.listening_since
        stale = (cutoff is not None and (started is None or started <= cutoff))
        stale = stale or (started is not None and started < self._last_accepted_start)
        if not request or stale or self.state.is_leaving:
            await self._discard_input(event.item_id)
            return
        if not self.state.is_listening:
            if RESUME.fullmatch(request) and self.resume_listening is not None:
                await self.resume_listening()
                self._admitted_items[event.item_id] = None
                if started is not None:
                    self._last_accepted_start = started
                speech = "muted" if self.state.is_muted else "enabled"
                result = events.ConversationItem(type="message", role="system", content=[
                    events.ItemContent(type="input_text", text=(
                        "The start_listening tool has already succeeded. Requests and actions "
                        f"are enabled again; speech is {speech}. Tool feedback is handled "
                        "in chat. Do not repeat the tool, speak a confirmation, or replay "
                        "requests skipped while listening was paused."
                    )),
                ])
                await self.send_client_event(events.ConversationItemCreateEvent(item=result))
                self._admitted_items[result.id] = None
            else:
                await self._discard_input(event.item_id)
            return
        self._admitted_items[event.item_id] = None
        self._current_input_id = event.item_id
        self._turn_used_tools = False
        if started is not None:
            self._last_accepted_start = started
        self.state.generation += 1
        self._admitted_generation = self.state.generation
        self._waiting_generation = self.state.generation
        self._results_awaiting_response = True
        if self._response_active:
            await self.send_client_event(events.ResponseCancelEvent())
        await self._handle_interruption()
        await self.broadcast_interruption()
        turn = LLMContextUserTurnFrame(text=event.transcript, timestamp=time_now_iso8601())
        turn.metadata["request_generation"] = self.state.generation
        await self.push_frame(turn)
        await self._create_response()

    async def _handle_evt_response_created(self, event):
        generation = self._created_generations.popleft() if self._created_generations else -1
        self._response_generations[event.response.id] = generation
        self._active_response_id = event.response.id
        pending_results = self._results_awaiting_response
        await super()._handle_evt_response_created(event)
        if pending_results or self._waiting_generation == self.state.generation:
            self._results_awaiting_response = True

    async def _handle_evt_response_done(self, event):
        response_id = event.response.id
        completed = event.response.status == "completed"
        if self._current_response(response_id) and completed:
            if (self.state.is_muted and not self._turn_used_tools
                    and self._current_input_id in self._admitted_items):
                # A muted answer was never heard; do not leave its question
                # outstanding for the model to answer on a later user turn.
                self._admitted_items.pop(self._current_input_id)
                await self._discard_input(self._current_input_id)
            for item in event.response.output or []:
                if not self._current_response(response_id):
                    break
                if item.type == "function_call" or (
                    item.role == "assistant" and not self.state.is_muted
                ):
                    self._admitted_items[item.id] = None
        # Always settle the SDK's active/owed response bookkeeping, even if stale.
        await super()._handle_evt_response_done(event)
        self._response_generations.pop(response_id, None)
        if self._waiting_generation == self.state.generation and not self._response_active:
            await self._create_response()

    async def _handle_evt_conversation_item_added(self, event):
        if event.item.role == "assistant" and not self._current_response(self._active_response_id):
            return
        await super()._handle_evt_conversation_item_added(event)

    async def _handle_evt_audio_delta(self, event):
        if self._current_response(event.response_id) and not self.state.is_muted:
            await super()._handle_evt_audio_delta(event)
            # Keep streamed speech in context even if the next request interrupts
            # it. Pipecat truncates this native audio item at the interruption.
            self._admitted_items[event.item_id] = None

    async def _handle_evt_text_delta(self, event):
        if self._current_response(event.response_id):
            await super()._handle_evt_text_delta(event)

    async def _handle_evt_audio_transcript_delta(self, event):
        if self._current_response(event.response_id):
            await super()._handle_evt_audio_transcript_delta(event)

    async def _handle_evt_function_call_arguments_done(self, event):
        if not self._current_response(event.response_id):
            self._pending_function_calls.pop(event.call_id, None)
            return
        self._function_generations[event.call_id] = self.state.generation
        self._turn_used_tools = True
        self._admitted_items[event.item_id] = None
        await super()._handle_evt_function_call_arguments_done(event)

    async def _handle_failed_transcription(self, event):
        self._handled_inputs.add(event.item_id)
        self._speech_starts.pop(event.item_id, None)
        await self._discard_input(event.item_id)

    async def _receive_task_handler(self):
        try:
            ended = await self._receive_events()
            if ended and not self._disconnecting:
                await self.push_error(
                    "Realtime connection closed unexpectedly.", force_treat_as_permanent=True,
                )
        except Exception as error:
            if not self._disconnecting:
                await self.push_error(
                    f"Realtime connection failed ({type(error).__name__}).", exception=error,
                    force_treat_as_permanent=True,
                )

    async def _receive_events(self):
        # Pinned SDK 1.16 omits transcription.failed from its dispatch loop.
        # Route that event too so failed admission never leaves retained audio.
        # Ignore transcription deltas until a complete request can be admitted.
        handlers = {
            "session.created": self._handle_evt_session_created,
            "session.updated": self._handle_evt_session_updated,
            "response.created": self._handle_evt_response_created,
            "response.done": self._handle_evt_response_done,
            "response.output_audio.delta": self._handle_evt_audio_delta,
            "response.output_text.delta": self._handle_evt_text_delta,
            "response.output_audio_transcript.delta": self._handle_evt_audio_transcript_delta,
            "response.function_call_arguments.done": self._handle_evt_function_call_arguments_done,
            "conversation.item.added": self._handle_evt_conversation_item_added,
            "conversation.item.done": self._handle_evt_conversation_item_done,
            "conversation.item.retrieved": self._handle_conversation_item_retrieved,
            "conversation.item.input_audio_transcription.completed": self.handle_evt_input_audio_transcription_completed,
            "conversation.item.input_audio_transcription.failed": self._handle_failed_transcription,
            "input_audio_buffer.speech_started": self._handle_evt_speech_started,
        }
        async for message in self._websocket:
            event = events.parse_server_event(message)
            if event is None:
                continue
            if handler := handlers.get(event.type):
                await handler(event)
            elif event.type == "error":
                if await self._maybe_handle_evt_retrieve_conversation_item_error(event):
                    continue
                if event.error.code == "response_cancel_not_active":
                    continue
                await self.push_error(
                    "Realtime service rejected a request.", force_treat_as_permanent=True,
                )
                return False
        return True
