"""Describe the nine meeting tools and register their Pipecat handlers."""

import asyncio
import json

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

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

CONTROL_TOOLS = {"mute_agent", "unmute_agent", "stop_listening", "start_listening", "leave_call"}


def register_meeting_tools(llm, tools: MeetingTools, finished: asyncio.Event) -> ToolsSchema:
    lock = asyncio.Lock()
    completed: dict[tuple[int, str, str], str] = {}
    actions = {schema.name: getattr(tools, schema.name) for schema in TOOL_SCHEMAS}
    schemas = {schema.name: schema for schema in TOOL_SCHEMAS}

    async def execute(params: FunctionCallParams):
        # The realtime service binds the call to the response that produced it.
        # Reading the current generation here could authorize a delayed old call.
        generation = llm.function_call_generation(params.tool_call_id)
        repeat = False
        async with lock:
            key = (generation, params.function_name, json.dumps(dict(params.arguments), sort_keys=True))
            try:
                if generation != tools.state.generation:
                    raise ToolError("Request superseded. Do not execute or replay it.")
                schema = schemas[params.function_name]
                if (set(params.arguments) != set(schema.required)
                        or any(not isinstance(value, str) for value in params.arguments.values())):
                    raise ToolError("The tool arguments do not match the requested action.")
                if key in completed:
                    repeat = True
                    result = completed[key]
                else:
                    # Reserve before sending: cancellation cannot make an uncertain
                    # external action eligible for another automatic attempt.
                    completed[key] = "Action already attempted; result unconfirmed. Do not retry."
                    result = await actions[params.function_name](**params.arguments)
                    completed[key] = result
            except ToolError as error:
                result = str(error)
                # Even an uncertain failure must never be retried automatically.
                completed[key] = result
                repeat = True
            # Let the model acknowledge a result or perform the next requested
            # action. Controls end the turn so muted/paused speech cannot replay.
            continue_request = (
                not repeat and params.function_name not in CONTROL_TOOLS
                and generation == tools.state.generation
            )
            await params.result_callback(
                result, properties=FunctionCallResultProperties(run_llm=continue_request),
            )
        if tools.state.is_leaving:
            finished.set()

    for schema in TOOL_SCHEMAS:
        # An interruption must not abandon a Recall request already in flight.
        llm.register_function(schema.name, execute, cancel_on_interruption=False, timeout_secs=90)
    return ToolsSchema(standard_tools=TOOL_SCHEMAS)
