from __future__ import annotations

import re
from typing import Optional

MENTION_PATTERN = re.compile(r"(?<!\w)@[\w\d_]+")
LINK_PATTERN = re.compile(r"(https?://)?t\.me/[\w\d_]+/?", re.IGNORECASE)


def sanitize(text: Optional[str]) -> Optional[str]:

    if text is None:
        return None
    cleaned = MENTION_PATTERN.sub("", text)
    cleaned = LINK_PATTERN.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip() or None
