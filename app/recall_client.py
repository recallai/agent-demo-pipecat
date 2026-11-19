"""The Recall requests used by the launcher and meeting tools."""

from urllib.parse import urlsplit
from uuid import UUID

import aiohttp

from app.config import BOT_NAME


class RecallError(Exception):
    pass


class RecallClient:
    def __init__(self, api_key: str, base_url: str):
        parsed = urlsplit(base_url)
        regions = {"us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1"}
        allowed_hosts = {f"{region}.recall.ai" for region in regions}
        if (
            parsed.scheme != "https"
            or parsed.netloc not in allowed_hosts
            or parsed.path.rstrip("/") != "/api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("RECALL_API_BASE_URL must be a Recall regional HTTPS /api/v1 URL.")
        if not api_key.strip():
            raise ValueError("Set RECALL_API_KEY before starting the demo.")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Token {self._api_key}"},
            timeout=aiohttp.ClientTimeout(total=20),
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(
        self, method: str, path: str, action: str, payload: dict | None = None,
        *, read_json: bool = False,
    ) -> dict | None:
        if self._session is None:
            raise RuntimeError("Use RecallClient inside an async with block.")
        try:
            async with self._session.request(
                method, f"{self.base_url}/{path}", json=payload, allow_redirects=False
            ) as response:
                if response.status >= 500:
                    raise RecallError(
                        f"Recall could not confirm {action} (HTTP {response.status}). Check the meeting before trying again.",
                    )
                if not 200 <= response.status < 300:
                    hint = "Check the request settings and Recall workspace configuration."
                    if response.status in (401, 403):
                        hint = "Check the Recall key, region, and workspace access."
                    elif response.status == 429:
                        hint = "Wait before trying again."
                    raise RecallError(f"Recall rejected {action} (HTTP {response.status}). {hint}")
                if read_json:
                    data = await response.json()
                    if not isinstance(data, dict):
                        raise ValueError("Unexpected response format")
                    return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise RecallError(
                f"Could not confirm {action}. It may have succeeded; check the meeting before trying again.",
            ) from error
        return None

    @staticmethod
    def _bot_path(bot_id: str, endpoint: str) -> str:
        return f"bot/{UUID(bot_id)}/{endpoint}/"

    async def create_bot(
        self, meeting_url: str, offer_url: str, *, session_token: str, transcript_url: str,
    ) -> str:
        result = await self._request(
            "POST", "bot/", "the meeting agent request",
            {
                "meeting_url": meeting_url,
                "bot_name": BOT_NAME,
                "variant": {"google_meet": "web_4_core"},
                "recording_config": {
                    "transcript": {"provider": {"deepgram_streaming": {"model": "nova-3"}}},
                    "realtime_endpoints": [{
                        "type": "webhook",
                        "url": transcript_url,
                        "events": ["transcript.data"],
                    }],
                },
                "output_media": {
                    "camera": {
                        "kind": "pipecat",
                        "config": {
                            "url": offer_url,
                            "body": {"session_token": session_token},
                        },
                    }
                },
            },
            read_json=True,
        )
        if result is None or not isinstance(result.get("id"), str):
            raise RecallError(
                "Recall accepted the request but returned no bot ID. Check the meeting before trying again.",
            )
        return result["id"]

    async def start_screenshare(self, bot_id: str, url: str) -> None:
        await self._request(
            "POST", self._bot_path(bot_id, "output_media"), "starting the screen share",
            {"screenshare": {"kind": "webpage", "config": {"url": url}}},
        )

    async def send_chat_message(self, bot_id: str, message: str) -> None:
        await self._request(
            "POST", self._bot_path(bot_id, "send_chat_message"), "the chat message",
            {"message": message, "to": "everyone", "pin": False},
        )

    async def stop_screenshare(self, bot_id: str) -> None:
        await self._request(
            "DELETE", self._bot_path(bot_id, "output_media"), "stopping the screen share",
            {"screenshare": True},
        )

    async def leave_call(self, bot_id: str) -> None:
        await self._request("POST", self._bot_path(bot_id, "leave_call"), "leaving the meeting")
