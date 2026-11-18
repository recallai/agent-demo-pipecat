"""Load local settings and validate the values used to connect a meeting."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

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


def realtime_reasoning_effort() -> str | None:
    effort = os.getenv("REALTIME_REASONING_EFFORT", "low").strip().lower()
    if effort == "default":
        return None
    if effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        raise ValueError(
            "REALTIME_REASONING_EFFORT must be minimal, low, medium, high, xhigh, or default."
        )
    return effort


def validate_agent_settings() -> None:
    for name in ("OPENAI_API_KEY", "SIMLI_API_KEY", "SIMLI_FACE_ID",
                 "REALTIME_MODEL", "REALTIME_VOICE"):
        require_env(name)
    public_origin()
    session_timeout()
    realtime_reasoning_effort()
