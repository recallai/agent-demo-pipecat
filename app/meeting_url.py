"""Check direct meeting links before handing them to Recall."""

import re
from urllib.parse import urlsplit

TEAMS_HOSTS = {"teams.microsoft.com", "teams.cloud.microsoft", "teams.live.com"}
MEETING_URL_ERROR = "Enter a direct HTTPS meeting link from Zoom, Google Meet, Microsoft Teams, or Webex."


def is_webex_meeting(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "webex.com" or host.endswith(".webex.com")


def validate_meeting_url(value: str) -> str:
    url = value.strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError(MEETING_URL_ERROR) from None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or port not in (None, 443)
        or "\\" in url
        or any(char.isspace() or ord(char) < 32 for char in url)
        or not parsed.path.strip("/")
    ):
        raise ValueError(MEETING_URL_ERROR)

    host = parsed.hostname or ""
    if host == "meet.google.com":
        if parsed.fragment or not re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", parsed.path):
            raise ValueError(MEETING_URL_ERROR)
        return f"https://meet.google.com{parsed.path.rstrip('/')}"
    if host in TEAMS_HOSTS or host == "zoom.us" or host.endswith(".zoom.us") or is_webex_meeting(url):
        # Recall checks the invite format. Keep provider-specific passcodes and context intact.
        return url
    raise ValueError(MEETING_URL_ERROR)
