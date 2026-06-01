from __future__ import annotations

import re

from app.models import FundRecommendation, NewsItem

_NO_NEWS_BULLISH = "暂无明确利好"
_NO_NEWS_BEARISH = "暂无明确利空"
_PLACEHOLDER_MARKERS = ("暂无", "无明确", "未检索", "未能")


def apply_news_citation_guards(
    fund_recs: list[FundRecommendation],
    market_news: list[NewsItem] | None,
) -> list[FundRecommendation]:
    titles = _news_titles(market_news or [])
    guarded: list[FundRecommendation] = []
    for rec in fund_recs:
        copy = rec.model_copy(deep=True)
        copy.news_bullish = _sanitize_headlines(copy.news_bullish, titles, bullish=True)
        copy.news_bearish = _sanitize_headlines(copy.news_bearish, titles, bullish=False)
        guarded.append(copy)
    return guarded


def _news_titles(market_news: list[NewsItem]) -> list[str]:
    return [item.title.strip() for item in market_news if item.title.strip()]


def _sanitize_headlines(
    headlines: list[str],
    known_titles: list[str],
    *,
    bullish: bool,
) -> list[str]:
    cleaned: list[str] = []
    for headline in headlines:
        text = headline.strip()
        if not text:
            continue
        if _is_placeholder(text):
            continue
        if known_titles and not _matches_known_title(text, known_titles):
            continue
        if text not in cleaned:
            cleaned.append(text)

    if cleaned:
        return cleaned[:3]

    default = _NO_NEWS_BULLISH if bullish else _NO_NEWS_BEARISH
    if not known_titles:
        return [default]
    return [default]


def _is_placeholder(text: str) -> bool:
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)


def _matches_known_title(headline: str, known_titles: list[str]) -> bool:
    normalized = _normalize(headline)
    for title in known_titles:
        candidate = _normalize(title)
        if not candidate:
            continue
        if candidate in normalized or normalized in candidate:
            return True
        if _overlap_ratio(candidate, normalized) >= 0.55:
            return True
    return False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("（", "(").replace("）", ")"))


def _overlap_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    best = 0
    for size in range(len(shorter), 2, -1):
        for start in range(0, len(shorter) - size + 1):
            fragment = shorter[start : start + size]
            if fragment in longer:
                best = max(best, size)
                break
    return best / max(len(longer), 1)
