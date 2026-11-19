"""Load local settings and validate the values used to connect a meeting."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from aiortc import RTCIceServer
from dotenv import load_dotenv
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parent.parent
BOT_NAME = "pipecat demo"
load_dotenv(ROOT / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in .env before starting the demo.")
    return value


def public_origin() -> str:
    value = require_env("PIPECAT_PUBLIC_URL").rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("PIPECAT_PUBLIC_URL must be a public HTTPS origin without a path.")
    return value


def session_timeout() -> int:
    seconds = int(os.getenv("SESSION_TIMEOUT_SECONDS", "600"))
    if not 30 <= seconds <= 3600:
        raise ValueError("SESSION_TIMEOUT_SECONDS must be between 30 and 3600.")
    return seconds


def webrtc_ice_servers() -> list[RTCIceServer]:
    value = os.getenv("WEBRTC_ICE_SERVERS", '[{"urls":"stun:stun.l.google.com:19302"}]')
    try:
        servers = TypeAdapter(list[RTCIceServer]).validate_json(value)
        if not servers:
            raise ValueError
        for server in servers:
            urls = [server.urls] if isinstance(server.urls, str) else server.urls
            if not urls or any(not url.startswith(("stun:", "stuns:", "turn:", "turns:")) for url in urls):
                raise ValueError
        return servers
    except ValueError:
        # Validation errors can include TURN credentials; expose only the setting name.
        raise ValueError("WEBRTC_ICE_SERVERS must be a nonempty JSON array of STUN/TURN servers.") from None


def validate_agent_settings() -> None:
    for name in ("DEEPGRAM_API_KEY", "DEEPGRAM_AGENT_MODEL", "DEEPGRAM_AGENT_VOICE",
                 "SIMLI_API_KEY", "SIMLI_FACE_ID"):
        require_env(name)
    public_origin()
    session_timeout()
    webrtc_ice_servers()
