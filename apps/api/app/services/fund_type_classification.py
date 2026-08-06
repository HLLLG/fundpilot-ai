from __future__ import annotations

import re


_NON_QDII_MARKER_RE = re.compile(r"(?:非|NON[\s_-]*)QDII", re.IGNORECASE)


def has_positive_qdii_marker(*values: object) -> bool:
    """Detect QDII while excluding explicit labels such as ``非QDII``."""

    text = " ".join(str(value or "").strip() for value in values)
    if not text:
        return False
    return "QDII" in _NON_QDII_MARKER_RE.sub("", text).upper()


__all__ = ["has_positive_qdii_marker"]
