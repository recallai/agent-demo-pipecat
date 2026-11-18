"""Recognize direct requests and the standalone listening-resume command."""

import re

ADDRESS = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay)[,\s]+)?"
    r"(?:assistant|pipe\s*cat\s+demo)\b[\s,;:.!?–—-]*",
    re.IGNORECASE,
)
RESUME = re.compile(r"(?:please\s+)?start\s+listening(?:\s+please)?[.!?]*", re.IGNORECASE)
