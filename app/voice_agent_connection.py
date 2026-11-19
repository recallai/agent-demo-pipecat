"""Connect to Deepgram's Voice Agent WebSocket and exchange audio and events."""

import asyncio
import json
import logging
from contextlib import suppress

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


class VoiceAgentConnection:
    def __init__(self, api_key: str, settings: dict):
        self._api_key = api_key
        self._settings = {**settings, "type": "Settings"}
        self._connection: ClientConnection | None = None

    async def __aenter__(self):
        try:
            async with asyncio.timeout(SETUP_TIMEOUT):
                self._connection = await connect(
                    VOICE_AGENT_URL,
                    additional_headers={"Authorization": f"Token {self._api_key}"},
                    proxy=None,
                    compression=None,
                    close_timeout=CLOSE_TIMEOUT,
                    logger=wire_logger,
                )
                await self._wait_for("Welcome")
                await self.send_event(self._settings)
                await self._wait_for("SettingsApplied")
        except BaseException as error:
            await self._close()
            if isinstance(error, InvalidStatus):
                status = error.response.status_code
                raise VoiceAgentError(f"Deepgram Voice Agent rejected the connection (HTTP {status}).") from None
            if isinstance(error, TimeoutError):
                raise VoiceAgentError("Deepgram Voice Agent setup timed out.") from None
            if isinstance(error, (OSError, WebSocketException)):
                raise VoiceAgentError("Could not connect to Deepgram Voice Agent.") from None
            raise
        return self

    async def __aexit__(self, *_):
        await self._close()

    async def _close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            # websockets aborts the connection if its close handshake times out.
            with suppress(OSError, WebSocketException, TimeoutError):
                await connection.close()

    def _active_connection(self) -> ClientConnection:
        if self._connection is None:
            raise VoiceAgentError("Deepgram Voice Agent is not connected.")
        return self._connection

    async def _wait_for(self, event_type: str) -> None:
        while True:
            event = await self.receive()
            if isinstance(event, bytes):
                raise VoiceAgentError("Deepgram Voice Agent sent audio before setup completed.")
            if event["type"] == event_type:
                return

    async def _send(self, message: bytes | str) -> None:
        try:
            async with asyncio.timeout(WRITE_TIMEOUT):
                await self._active_connection().send(message)
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
            message = await self._active_connection().recv()
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
