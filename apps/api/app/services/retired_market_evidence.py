"""Remove retired market evidence from newly generated user-facing decisions."""

from __future__ import annotations

import re
from typing import Any


_RETIRED_EVIDENCE_PATTERN = re.compile(r"北向|north[\s_-]*bound", re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;\n])")


def strip_retired_market_evidence_text(value: object) -> str:
    """Drop sentences that cite a permanently retired evidence family."""

    text = str(value or "")
    if not _RETIRED_EVIDENCE_PATTERN.search(text):
        return text
    kept = [
        part
        for part in _SENTENCE_BOUNDARY.split(text)
        if part and not _RETIRED_EVIDENCE_PATTERN.search(part)
    ]
    return "".join(kept).strip()


def sanitize_retired_market_evidence(value: Any) -> Any:
    """Recursively remove retired fields and LLM prose before model validation."""

    if isinstance(value, dict):
        return {
            key: sanitize_retired_market_evidence(item)
            for key, item in value.items()
            if not _RETIRED_EVIDENCE_PATTERN.search(str(key))
        }
    if isinstance(value, list):
        sanitized: list[Any] = []
        for item in value:
            cleaned = sanitize_retired_market_evidence(item)
            if isinstance(cleaned, str) and not cleaned.strip():
                continue
            sanitized.append(cleaned)
        return sanitized
    if isinstance(value, str):
        return strip_retired_market_evidence_text(value)
    return value
