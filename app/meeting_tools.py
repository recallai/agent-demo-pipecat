"""Meeting actions and their per-meeting state, independent of the agent framework."""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from app.recall_client import RecallClient, RecallError

logger = logging.getLogger("meeting-demo")


class ToolError(Exception):
    """An action failed or needs different input; safe to report to the participant."""


@dataclass
class MeetingState:
    is_muted: bool = False
    is_listening: bool = True
    current_page_url: str | None = None
    is_leaving: bool = False
    # Control changes invalidate any speech or requests captured in an older state.
    generation: int = 0
    listening_since: float | None = None


def public_webpage_url(value: str) -> str:
    """Validate the public HTTPS URL passed to Recall's webpage renderer."""
    url = value.strip()
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii")
        hostname = hostname.rstrip(".").lower()
        valid = (
            parsed.scheme == "https"
            and hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and "\\" not in url
            and "%" not in parsed.netloc
            and not any(character.isspace() for character in url)
            and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
        )
    except ValueError:
        valid = False
        hostname = ""
    if not valid:
        raise ToolError("Provide a public HTTPS webpage URL without login credentials.")
    try:
        is_public = ip_address(hostname).is_global
    except ValueError:
        labels = hostname.split(".")
        # Browsers parse hosts ending in a number as IPv4, including shorthand,
        # hexadecimal, and octal forms that ip_address() rejects. Require those
        # addresses to use the canonical form checked above.
        is_public = (
            len(labels) > 1
            and len(hostname) <= 253
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
            and not re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", labels[-1])
            and labels[-1] not in {"localhost", "local", "internal"}
        )
    if not is_public:
        raise ToolError("Use a public website, not a local or private network address.")
    return url


class MeetingTools:
    def __init__(
        self,
        client: RecallClient,
        bot_id: str,
        state: MeetingState,
        silence_output: Callable[[], Awaitable[None]],
    ):
        self.client = client
        self.bot_id = bot_id
        self.state = state
        self._silence_output = silence_output

    def _require_listening(self) -> None:
        if self.state.is_leaving:
            raise ToolError("The agent is already leaving the meeting.")
        if not self.state.is_listening:
            raise ToolError("Requests are paused. Say 'Assistant, start listening' first.")

    async def _post_status(self, message: str) -> None:
        try:
            await self.client.send_chat_message(self.bot_id, message)
        except RecallError:
            # Feedback failure must not undo a control or repeat the original action.
            logger.warning("Could not post tool status in chat; not retrying")

    async def _call_recall(self, request: Awaitable[None]) -> None:
        try:
            await request
        except RecallError as error:
            await self._post_status(str(error))
            raise ToolError(str(error)) from error

    async def start_screenshare(self, url: str) -> str:
        """Show a public webpage without reading it or automatically posting its URL."""
        self._require_listening()
        try:
            url = public_webpage_url(url)
        except ToolError as error:
            await self._post_status(str(error))
            raise
        await self._call_recall(self.client.start_screenshare(self.bot_id, url))
        self.state.current_page_url = url
        await self._post_status("Screen share requested. It may take a few seconds to appear.")
        return "Recall accepted the screen share request. Page visibility is unverified."

    async def send_chat_message(self, message: str) -> str:
        """Post requested text to everyone. Use the link tool for the current page."""
        self._require_listening()
        if not message.strip() or len(message) > 500:
            await self._post_status("Message not sent: use 1 to 500 characters.")
            raise ToolError("Google Meet messages must contain 1 to 500 characters.")
        await self._call_recall(self.client.send_chat_message(self.bot_id, message))
        return "Recall accepted the chat message."

    async def send_current_page_link(self) -> str:
        """Post the exact last accepted page URL, including after sharing stops."""
        self._require_listening()
        if self.state.current_page_url is None:
            message = "No webpage has been selected yet. Ask me to show one first."
            await self._post_status(message)
            raise ToolError(message)
        return await self.send_chat_message(self.state.current_page_url)

    async def stop_screenshare(self) -> str:
        """Stop sharing while preserving conversation and the last page URL."""
        self._require_listening()
        await self._call_recall(self.client.stop_screenshare(self.bot_id))
        await self._post_status("Stopped sharing.")
        return "Recall accepted the request to stop sharing."

    async def leave_call(self) -> str:
        """Post a departure message and remove only this bot from the meeting."""
        self._require_listening()
        await self._post_status("Leaving the call.")
        await self._call_recall(self.client.leave_call(self.bot_id))
        self.state.is_leaving = True
        self.state.generation += 1
        return "Recall accepted the request for this bot to leave."

    async def mute_agent(self) -> str:
        """Silence speech immediately; continue accepting requested meeting actions."""
        self._require_listening()
        self.state.is_muted = True
        self.state.generation += 1
        await self._silence_output()
        await self._post_status("Muted. Tools still work.")
        return "Speech muted."

    async def unmute_agent(self) -> str:
        """Enable new speech without replaying responses or changing listening."""
        self._require_listening()
        self.state.is_muted = False
        self.state.generation += 1
        await self._post_status("Unmuted.")
        return "Speech enabled."

    async def stop_listening(self) -> str:
        """Pause answers and actions until an addressed standalone start-listening request."""
        self._require_listening()
        self.state.is_listening = False
        self.state.generation += 1
        await self._silence_output()
        speech = "muted" if self.state.is_muted else "enabled"
        await self._post_status(f"Stopped listening. Speech remains {speech}.")
        return "Requests paused. Speech mute setting unchanged."

    async def start_listening(self) -> str:
        """Resume new requests while preserving mute and discarding skipped requests."""
        if self.state.is_leaving:
            raise ToolError("The agent is already leaving the meeting.")
        self.state.is_listening = True
        self.state.listening_since = time.time()
        self.state.generation += 1
        speech = "muted" if self.state.is_muted else "enabled"
        await self._post_status(f"Started listening. Speech is {speech}.")
        return "Listening. Speech mute setting unchanged."
