from __future__ import annotations

import re

_TOPIC_ALIASES = ("人工智能", "电网设备", "半导体", "国防军工", "商业航天", "红利", "新能源")


def normalize_sector_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = re.sub(r"\.{2,}", "", label.strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def sector_label_key(label: str | None) -> str:
    return normalize_sector_label(label).lower()


def build_sector_candidates(label: str | None) -> list[str]:
    base = normalize_sector_label(label)
    if not base:
        return []

    seen: set[str] = set()
    candidates: list[str] = []

    def add(value: str) -> None:
        normalized = normalize_sector_label(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    add(base)
    for prefix in ("中证", "国证", "上证", "深证"):
        if base.startswith(prefix) and len(base) > len(prefix):
            add(base[len(prefix) :])
    for suffix in ("主题", "指数", "ETF", "板块"):
        if base.endswith(suffix) and len(base) > len(suffix):
            add(base[: -len(suffix)])
    for token in _TOPIC_ALIASES:
        if token in base:
            add(token)
    return candidates
