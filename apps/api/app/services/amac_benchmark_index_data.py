"""中基协 155 指数要素库（静态 JSON）加载与 THEME_BOARD_INDEX 合并。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_LIBRARY_PATH = Path(__file__).resolve().parents[1] / "data" / "amac_benchmark_index_library.json"


@lru_cache(maxsize=1)
def load_amac_library() -> dict[str, Any]:
    if not _LIBRARY_PATH.is_file():
        return {"entries": [], "version": "missing", "total": 0, "resolved": 0}
    return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def amac_entries_with_code() -> tuple[dict[str, Any], ...]:
    return tuple(
        entry
        for entry in load_amac_library().get("entries", [])
        if entry.get("source_code")
    )


@lru_cache(maxsize=1)
def amac_code_to_entry() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in amac_entries_with_code():
        code = str(entry["source_code"]).upper()
        out[code] = entry
    return out


@lru_cache(maxsize=1)
def amac_code_to_theme_label() -> dict[str, str]:
    """指数代码 → 展示板块（仅含 AMAC 推断出的主题/行业类）。"""
    out: dict[str, str] = {}
    for entry in amac_entries_with_code():
        label = entry.get("theme_label")
        if not label:
            continue
        code = str(entry["source_code"]).upper()
        out[code] = str(label)
    return out


def amac_theme_label_for_code(index_code: str) -> str | None:
    return amac_code_to_theme_label().get(index_code.strip().upper())


@lru_cache(maxsize=1)
def amac_name_to_code_pairs() -> tuple[tuple[str, str], ...]:
    """指数全称/简称 → 代码（长匹配优先）。"""
    pairs: set[tuple[str, str]] = set()
    for entry in amac_entries_with_code():
        full_name = str(entry.get("index_full_name") or "").strip()
        code = str(entry["source_code"]).strip()
        if not full_name or not code:
            continue
        pairs.add((full_name, code))
        short = full_name.replace("指数", "").strip()
        if short and short != full_name:
            pairs.add((short, code))
        if "主题" not in full_name and entry.get("base_type") == "行业主题指数":
            pairs.add((f"{short}主题指数", code))
    return tuple(sorted(pairs, key=lambda item: len(item[0]), reverse=True))


def merge_amac_into_theme_board_index(
    base: dict[str, tuple[str, str, str]],
) -> dict[str, tuple[str, str, str]]:
    """将 AMAC 主题指数补入 THEME_BOARD_INDEX（不覆盖已有 label / source_code）。"""
    from app.services.sector_registry_data import THEME_BOARD_WHITELIST

    merged = dict(base)
    existing_codes = {spec[1].upper() for spec in base.values() if spec[1]}
    for entry in amac_entries_with_code():
        label = entry.get("theme_label")
        if not label or label not in THEME_BOARD_WHITELIST:
            continue
        if label in merged:
            continue
        code = str(entry["source_code"]).upper()
        secid = str(entry.get("eastmoney_secid") or "")
        if not secid or code in existing_codes:
            continue
        merged[label] = (secid, code, "index")
        existing_codes.add(code)
    return merged
