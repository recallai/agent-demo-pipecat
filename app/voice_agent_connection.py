"""Connect to Deepgram's Voice Agent WebSocket and exchange audio and events."""

import asyncio
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

VOICE_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"
SETUP_TIMEOUT = 15
WRITE_TIMEOUT = 5
CLOSE_TIMEOUT = 3

# WebSocket debug logs include credentials, audio, and conversation events.
wire_logger = logging.Logger("deepgram-voice-wire")
wire_logger.disabled = True


class VoiceAgentError(Exception):
    """A Voice Agent failure with a message safe to report."""


@asynccontextmanager
async def open_voice_agent(api_key: str, settings: dict):
    async with AsyncExitStack() as stack:
        try:
            async with asyncio.timeout(SETUP_TIMEOUT):
                socket = await stack.enter_async_context(connect(
                    VOICE_AGENT_URL,
                    additional_headers={"Authorization": f"Token {api_key}"},
                    proxy=None,
                    compression=None,
                    close_timeout=CLOSE_TIMEOUT,
                    logger=wire_logger,
                ))
                voice = VoiceAgentConnection(socket)
                await voice.wait_for("Welcome")
                await voice.send_event({**settings, "type": "Settings"})
                await voice.wait_for("SettingsApplied")
        except InvalidStatus as error:
            status = error.response.status_code
            raise VoiceAgentError(f"Deepgram Voice Agent rejected the connection (HTTP {status}).") from None
        except TimeoutError:
            raise VoiceAgentError("Deepgram Voice Agent setup timed out.") from None
        except (OSError, WebSocketException):
            raise VoiceAgentError("Could not connect to Deepgram Voice Agent.") from None
        yield voice


class VoiceAgentConnection:
    def __init__(self, socket: ClientConnection):
        self._socket = socket

    async def wait_for(self, event_type: str) -> None:
        while True:
            event = await self.receive()
            if isinstance(event, bytes):
                raise VoiceAgentError("Deepgram Voice Agent sent audio before setup completed.")
            if event["type"] == event_type:
                return

    async def _send(self, message: bytes | str) -> None:
        try:
            async with asyncio.timeout(WRITE_TIMEOUT):
                await self._socket.send(message)
        except (OSError, WebSocketException, TimeoutError):
            raise VoiceAgentError("Could not send data to Deepgram Voice Agent.") from None

    async def send_audio(self, audio: bytes) -> None:
        await self._send(audio)

    async def send_event(self, event: dict) -> None:
        try:
            message = json.dumps(event, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise VoiceAgentError("Could not encode Deepgram Voice Agent event.") from None
        await self._send(message)

    async def receive(self) -> bytes | dict:
        try:
            message = await self._socket.recv()
        except (OSError, WebSocketException):
            raise VoiceAgentError("Deepgram Voice Agent connection closed.") from None
        if isinstance(message, bytes):
            return message
        try:
            event = json.loads(message)
        except (ValueError, RecursionError):
            raise VoiceAgentError("Deepgram Voice Agent sent invalid JSON.") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise VoiceAgentError("Deepgram Voice Agent sent an invalid event.")
        if event["type"] == "Error":
            raise VoiceAgentError("Deepgram Voice Agent reported an error. Check the provider settings.")
        return event
