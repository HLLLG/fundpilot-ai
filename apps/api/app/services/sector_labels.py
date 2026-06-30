from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.sector_registry_data import THEME_BOARD_INDEX

_TOPIC_ALIASES = (
    "人工智能",
    "电网设备",
    "半导体",
    "国防军工",
    "商业航天",
    "红利",
    "新能源",
    "传媒",
    "CPO",
)

_FUND_COMPANY_PREFIXES = (
    "华夏",
    "中欧",
    "天弘",
    "富国",
    "广发",
    "某某",
)

_SEMANTIC_NOISE_TOKENS = (
    "ETF",
    "发起式",
    "发起",
    "联接",
    "主题",
    "指数",
    "混合",
    "QDII",
    "人民币",
)

_GENERIC_SEMANTIC_LABELS = {
    "灵活配置",
    "成长精选股票",
    "稳健回报",
}


@dataclass(frozen=True)
class SemanticSectorCandidate:
    sector_name: str
    source: str = "semantic_name"
    confidence: float = 0.0
    reason: str = ""
    quote_key: str | None = None


def normalize_sector_label(label: str | None) -> str:
    if not label:
        return ""
    cleaned = re.sub(r"\.{2,}", "", label.strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


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


def sector_label_key(label: str | None) -> str:
    return normalize_sector_label(label).lower()


def _clean_semantic_fund_name(fund_name: str) -> str:
    cleaned = normalize_sector_label(fund_name.replace("...", ""))
    cleaned = re.sub(r"[（(]QDII[）)]", "", cleaned, flags=re.IGNORECASE)
    for prefix in _FUND_COMPANY_PREFIXES:
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    for prefix in ("中证", "国证", "上证", "深证"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.replace("科创板", "")
    for token in _SEMANTIC_NOISE_TOKENS:
        cleaned = re.sub(re.escape(token), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[A-Z]$", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _theme_board_match(normalized: str) -> SemanticSectorCandidate | None:
    names = set(THEME_BOARD_INDEX) | set(_TOPIC_ALIASES)
    for token in sorted(names, key=len, reverse=True):
        if token in normalized:
            row = THEME_BOARD_INDEX.get(token)
            quote_key = row[1] if row else None
            return SemanticSectorCandidate(
                sector_name=token,
                confidence=0.86 if quote_key else 0.72,
                reason="registered_theme_keyword",
                quote_key=quote_key,
            )
    return None


def infer_semantic_sector_from_fund_name(
    fund_name: str | None,
) -> SemanticSectorCandidate | None:
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name.replace("...", ""))
    if not normalized:
        return None

    is_overseas = bool(re.search(r"QDII|全球|海外", normalized, flags=re.IGNORECASE))
    if is_overseas and any(token in normalized for token in ("科技互联网", "科技先锋", "全球科技")):
        return SemanticSectorCandidate(
            sector_name="海外基金",
            confidence=0.62,
            reason="overseas_generic_technology",
        )

    registered = _theme_board_match(normalized)
    if registered is not None:
        return registered

    cleaned = _clean_semantic_fund_name(normalized)
    if (
        not cleaned
        or cleaned in _GENERIC_SEMANTIC_LABELS
        or all(token in cleaned for token in ("成长", "精选", "股票"))
    ):
        return None

    registered = _theme_board_match(cleaned)
    if registered is not None:
        return registered

    if is_overseas and cleaned.startswith("全球") and len(cleaned) >= 6:
        return SemanticSectorCandidate(
            sector_name=cleaned,
            confidence=0.61,
            reason="overseas_explicit_theme",
        )
    if cleaned.startswith("科创") and len(cleaned) >= 6:
        return SemanticSectorCandidate(
            sector_name=cleaned,
            confidence=0.59,
            reason="explicit_science_innovation_theme",
        )
    return None


def infer_sector_label_from_fund_name(fund_name: str | None) -> str | None:
    """总览 OCR 无关联板块时，从基金名称推断主题短名（如 国防军工混合 → 国防军工）。"""
    if not fund_name:
        return None
    normalized = normalize_sector_label(fund_name.replace("...", ""))
    if not normalized:
        return None
    semantic = infer_semantic_sector_from_fund_name(normalized)
    if semantic is not None and semantic.quote_key:
        return semantic.sector_name
    for token in sorted(_TOPIC_ALIASES, key=len, reverse=True):
        if token in normalized:
            return token
    return None
