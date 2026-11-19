"""Register Deepgram client-side functions and run authorized meeting actions."""

import asyncio
import json

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

from app.meeting_tools import MeetingTools, ToolError


TOOL_SCHEMAS = [
    FunctionSchema("start_screenshare", "Show a requested public HTTPS webpage. Does not read it or post its link.",
                   {"url": {"type": "string", "description": "The public HTTPS URL to display."}}, ["url"]),
    FunctionSchema("send_chat_message", "Post requested text to everyone. Use send_current_page_link for the selected page.",
                   {"message": {"type": "string", "minLength": 1, "maxLength": 500}}, ["message"]),
    FunctionSchema("send_current_page_link", "Post the exact last successfully selected webpage URL, even after sharing stops.", {}, []),
    FunctionSchema("stop_screenshare", "Stop sharing the page. Keep the voice conversation and last page URL.", {}, []),
    FunctionSchema("leave_call", "Leave this meeting when explicitly asked to leave.", {}, []),
    FunctionSchema("mute_agent", "Mute speech immediately. Continue accepting requested tools.", {}, []),
    FunctionSchema("unmute_agent", "Enable new speech; never replay unheard responses.", {}, []),
    FunctionSchema("stop_listening", "Pause answers and actions until a separate addressed start-listening request. Preserve mute.", {}, []),
    FunctionSchema("start_listening", "Resume requests, preserving mute and discarding skipped requests.", {}, []),
]


def voice_agent_functions() -> list[dict]:
    return [{
        "name": schema.name,
        "description": schema.description,
        "parameters": {
            "type": "object",
            "properties": schema.properties,
            "required": schema.required,
        },
        "defer_until_eot": True,
    } for schema in TOOL_SCHEMAS]


class VoiceAgentTools:
    def __init__(self, tools: MeetingTools, finished: asyncio.Event):
        self._tools = tools
        self._finished = finished
        self._schemas = {schema.name: schema for schema in TOOL_SCHEMAS}
        self._lock = asyncio.Lock()
        self._actions: set[asyncio.Task[tuple[str, bool]]] = set()

    async def execute(
        self, generation: int, name: str, arguments: str, completed: dict[tuple[str, str], str],
    ) -> tuple[str, bool]:
        # Once a Recall request starts, an interruption must not abandon it.
        task = asyncio.create_task(self._execute(generation, name, arguments, completed))
        self._actions.add(task)
        task.add_done_callback(self._action_done)
        return await asyncio.shield(task)

    def _action_done(self, task: asyncio.Task[tuple[str, bool]]) -> None:
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
    ) -> tuple[str, bool]:
        schema = self._schemas.get(name)
        try:
            params = json.loads(arguments)
        except (TypeError, ValueError):
            params = None
        if (schema is None or not isinstance(params, dict)
                or set(params) != set(schema.required)
                or any(not isinstance(value, str) for value in params.values())):
            return "The tool arguments do not match a meeting action.", False

        key = (name, json.dumps(params, sort_keys=True))
        async with self._lock:
            state = self._tools.state
            if generation != state.generation or not state.is_listening or state.is_leaving:
                return "Request superseded. Do not execute or replay it.", False
            if key in completed:
                return completed[key], False
            # Reserve before any external request to stop duplicate calls.
            completed[key] = "The action was already attempted. Do not retry."
            try:
                result = await getattr(self._tools, name)(**params)
            except ToolError as error:
                result = str(error)
                succeeded = False
            else:
                succeeded = True
            completed[key] = result
            if state.is_leaving:
                self._finished.set()
            return result, succeeded
