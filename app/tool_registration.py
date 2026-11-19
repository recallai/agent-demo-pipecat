"""Declare Voice Agent functions and dispatch approved calls to the meeting tools."""

import asyncio
import json

from app.meeting_tools import MeetingTools, ToolError


def _function(name: str, description: str, arguments: dict | None = None) -> dict:
    properties = arguments or {}
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
        "defer_until_eot": True,
    }


VOICE_AGENT_FUNCTIONS = (
    _function(
        "start_screenshare", "Show a requested public HTTPS webpage without reading it or posting its link.",
        {"url": {"type": "string", "description": "The public HTTPS URL to display."}},
    ),
    _function(
        "send_chat_message", "Post requested text to everyone. Use send_current_page_link for the selected page.",
        {"message": {"type": "string", "minLength": 1, "maxLength": 500}},
    ),
    _function("send_current_page_link", "Post the exact last accepted webpage URL, even after sharing stops."),
    _function("stop_screenshare", "Stop sharing the page. Keep the conversation and last page URL."),
    _function("leave_call", "Leave this meeting when explicitly asked to leave."),
    _function("mute_agent", "Mute speech immediately. Continue accepting requested tools."),
    _function("unmute_agent", "Enable new speech without replaying unheard responses."),
    _function("stop_listening", "Pause until a separate addressed start-listening request. Preserve mute."),
    _function("start_listening", "Resume new requests and preserve mute."),
)
REQUIRED_ARGUMENTS = {
    function["name"]: set(function["parameters"]["required"]) for function in VOICE_AGENT_FUNCTIONS
}


class VoiceAgentTools:
    def __init__(self, tools: MeetingTools, finished: asyncio.Event):
        self._tools = tools
        self._finished = finished
        self._lock = asyncio.Lock()
        self._actions: set[asyncio.Task[str]] = set()

    async def execute_call(
        self, generation: int, call: object, completed: dict[tuple[str, str], str],
    ) -> dict | None:
        if not isinstance(call, dict) or call.get("client_side") is not True:
            return None
        name = call.get("name")
        call_id = call.get("id")
        arguments = call.get("arguments")
        if (not isinstance(name, str) or len(name) > 80
                or not isinstance(call_id, str) or not 0 < len(call_id) <= 256
                or not isinstance(arguments, str) or len(arguments) > 4096):
            return None

        # Once a Recall request starts, an interruption must not abandon it.
        task = asyncio.create_task(self._execute(generation, name, arguments, completed))
        self._actions.add(task)
        task.add_done_callback(self._action_done)
        response = {
            "type": "FunctionCallResponse",
            "id": call_id,
            "name": name,
            "content": await asyncio.shield(task),
        }
        signature = call.get("thought_signature")
        if isinstance(signature, str):
            response["thought_signature"] = signature
        return response

    def _action_done(self, task: asyncio.Task[str]) -> None:
        self._actions.discard(task)
        if not task.cancelled():
            # The response task may have gone away. Do not log a detached
            # exception that could include meeting content.
            task.exception()

    async def drain(self) -> None:
        if self._actions:
            await asyncio.gather(*self._actions, return_exceptions=True)

    async def _execute(
        self, generation: int, name: str, arguments: str, completed: dict[tuple[str, str], str],
    ) -> str:
        try:
            params = json.loads(arguments)
        except (ValueError, RecursionError):
            params = None
        if (name not in REQUIRED_ARGUMENTS or not isinstance(params, dict)
                or set(params) != REQUIRED_ARGUMENTS[name]
                or any(not isinstance(value, str) for value in params.values())):
            return "The tool arguments do not match a meeting action."

        key = (name, json.dumps(params, sort_keys=True))
        async with self._lock:
            state = self._tools.state
            if generation != state.generation or not state.is_listening or state.is_leaving:
                return "Request superseded. Do not execute or replay it."
            if key in completed:
                return completed[key]
            # Reserve before any external request to stop duplicate calls.
            completed[key] = "The action was already attempted. Do not retry."
            try:
                result = await getattr(self._tools, name)(**params)
            except ToolError as error:
                result = str(error)
            completed[key] = result
            if state.is_leaving:
                self._finished.set()
            return result
