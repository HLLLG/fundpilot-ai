from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import get_settings
from app.models import Holding, NewsItem
from app.services.news_cache import NEWS_CACHE_STALE_SECONDS, get_cached_news, save_cached_news
from app.services.news_freshness import (
    CN_TZ,
    NewsPublishedTime,
    is_news_published_today,
    latest_news_published_at,
    normalize_news_now,
    parse_news_published_at,
)
from app.services.trading_session import build_trading_session

_SNIPPET_MAX_LEN = 200
_NEWS_CACHE_WINDOW = 50
_ANNOUNCEMENT_CACHE_PREFIX = "fund-announcement:"
_ANNOUNCEMENT_CACHE_SCOPE = "announcement-v1"
_TOPIC_ALIASES = ("人工智能", "电网设备", "半导体", "国防军工", "商业航天")
_TRACKING_QUERY_KEYS = {
    "from",
    "spm",
    "source",
    "track",
    "tracking_id",
}

logger = logging.getLogger(__name__)


class NewsService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def topics_from_holdings(
        self,
        holdings: list[Holding],
        max_topics: int | None = None,
    ) -> list[str]:
        seen: set[str] = set()
        topics: list[str] = []
        limit = max_topics if max_topics is not None else self.settings.news_max_topics

        for holding in holdings:
            candidates = [
                _normalize_topic(holding.sector_name),
                _keyword_from_name(holding.fund_name),
            ]
            for topic in candidates:
                if not topic or topic in seen:
                    continue
                seen.add(topic)
                topics.append(topic)

        sources = self.settings.news_source_set
        if holdings and "macro" in sources:
            macro = self.settings.news_macro_topic.strip()
            if macro and macro not in seen:
                topics.insert(0, macro)

        return topics[:limit]

    def search(
        self,
        topic: str,
        limit: int | None = None,
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        topic = topic.strip()
        if not topic or not self.settings.news_enabled:
            return []

        per_topic = limit if limit is not None else self.settings.news_per_topic
        per_topic = max(1, min(per_topic, _NEWS_CACHE_WINDOW))
        resolved_now = normalize_news_now(now)

        cache_date = resolved_now.date().isoformat()
        cached = get_cached_news(
            topic,
            cache_date=cache_date,
            max_age_seconds=_news_cache_max_age_seconds(resolved_now),
            now=resolved_now,
        )
        if cached is not None:
            return _prepare_news(cached, limit=per_topic, now=resolved_now)

        sources = self.settings.news_source_set
        items: list[NewsItem] = []

        if re.fullmatch(r"\d{6}", topic) and "announcement" in sources:
            items.extend(
                self._from_fund_announcements(
                    topic,
                    _NEWS_CACHE_WINDOW,
                    now=resolved_now,
                )
            )

        if "eastmoney" in sources or "macro" in sources:
            items.extend(
                self._from_eastmoney(
                    topic,
                    _NEWS_CACHE_WINDOW * 2,
                    now=resolved_now,
                )
            )

        if "cls" in sources:
            items.extend(
                self._from_cls(topic, _NEWS_CACHE_WINDOW, now=resolved_now)
            )

        # The cache key intentionally excludes the caller's requested limit.
        # Persist one bounded canonical window so a previous limit=1 lookup
        # cannot starve a later deep/oversampling request for the same topic.
        canonical = _prepare_news(
            items,
            limit=_NEWS_CACHE_WINDOW,
            now=resolved_now,
        )
        if canonical:
            save_cached_news(
                topic,
                canonical,
                cache_date=cache_date,
                now=resolved_now,
            )
        return canonical[:per_topic]

    def prefetch_topics(
        self,
        topics: list[str],
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        if not self.settings.news_enabled or not topics:
            return []

        limited = list(topics[: self.settings.news_max_topics])
        resolved_now = normalize_news_now(now)
        target_per_topic = max(1, min(int(self.settings.news_per_topic), 10))
        target_total = target_per_topic * len(limited)
        # Pull a bounded surplus per topic so a cross-topic duplicate is removed
        # before the final global Top-K and does not consume another topic's slot.
        oversample_per_topic = min(
            _NEWS_CACHE_WINDOW,
            max(target_per_topic * 2, target_total),
        )
        if len(limited) == 1:
            searched = self.search(
                limited[0], limit=target_per_topic, now=resolved_now
            )
            return _prepare_news(
                searched, limit=target_per_topic, now=resolved_now
            )

        import time
        from concurrent.futures import ThreadPoolExecutor, wait

        max_workers = min(5, len(limited))
        deadline = time.monotonic() + float(self.settings.news_prefetch_total_timeout_seconds)
        collected: list[NewsItem] = []
        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="news-prefetch",
        )
        try:
            futures = {
                executor.submit(
                    self.search,
                    topic,
                    oversample_per_topic,
                    now=resolved_now,
                ): topic
                for topic in limited
            }
            timeout = max(0.0, deadline - time.monotonic())
            done, pending = wait(futures, timeout=timeout)
            for future in pending:
                future.cancel()
            for future in done:
                try:
                    items = future.result()
                except Exception:
                    continue
                collected.extend(items)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return _prepare_news(collected, limit=target_total, now=resolved_now)

    def prefetch_for_holdings(
        self,
        holdings: list[Holding],
        max_topics: int | None = None,
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        topics = self.topics_from_holdings(holdings, max_topics=max_topics)
        return self.prefetch_topics(topics, now=now)

    def prefetch_fund_announcements(
        self,
        fund_codes: list[str],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Fetch fund announcements with an independent bounded evidence budget.

        The returned ``items`` remains directly consumable by existing news paths,
        while the surrounding fields preserve provider coverage and failure state.
        ``empty`` means the provider answered successfully with no usable rows;
        ``error`` and ``timeout`` are never cached as empty evidence.
        """
        import time as monotonic_time
        from concurrent.futures import ThreadPoolExecutor, wait

        resolved_now = normalize_news_now(now)
        eligible_codes = _normalize_fund_codes(fund_codes)
        max_funds = max(0, int(self.settings.news_announcement_max_funds))
        requested_codes = eligible_codes[:max_funds]
        fetched_at = resolved_now.isoformat(timespec="seconds")
        per_fund = max(
            1,
            min(int(self.settings.news_announcement_per_fund), 20),
        )
        deadline = monotonic_time.monotonic() + max(
            0.0,
            float(self.settings.news_announcement_prefetch_total_timeout_seconds),
        )
        # A fixed namespace makes TTL (rather than calendar rollover) the sole
        # freshness boundary. is_today is recomputed against ``resolved_now``.
        cache_date = _ANNOUNCEMENT_CACHE_SCOPE
        cache_ttl = max(0, int(self.settings.news_announcement_cache_ttl_seconds))
        outcomes: dict[str, tuple[str, list[NewsItem], bool]] = {}

        if not self.settings.news_enabled or "announcement" not in self.settings.news_source_set:
            outcomes = {code: ("error", [], False) for code in requested_codes}
            return _build_announcement_prefetch_result(
                requested_codes,
                outcomes,
                fetched_at=fetched_at,
                now=resolved_now,
                enabled=False,
                input_count=len(fund_codes),
                eligible_count=len(eligible_codes),
            )

        missing_codes: list[str] = []
        for code in requested_codes:
            cached = _get_cached_announcements(
                code,
                cache_date=cache_date,
                max_age_seconds=cache_ttl,
                now=resolved_now,
            )
            if cached is None:
                missing_codes.append(code)
                continue
            prepared = _prepare_news(cached, limit=per_fund, now=resolved_now)
            outcomes[code] = ("ok" if prepared else "empty", prepared, True)

        if missing_codes:
            executor = ThreadPoolExecutor(
                max_workers=min(5, len(missing_codes)),
                thread_name_prefix="announcement-prefetch",
            )
            try:
                futures = {
                    executor.submit(
                        _fetch_fund_announcement_outcome,
                        code,
                        per_fund,
                        resolved_now,
                    ): code
                    for code in missing_codes
                }
                remaining = max(0.0, deadline - monotonic_time.monotonic())
                done, pending = wait(futures, timeout=remaining)
                for future in pending:
                    code = futures[future]
                    future.cancel()
                    outcomes[code] = ("timeout", [], False)
                for future in done:
                    code = futures[future]
                    try:
                        status, items = future.result()
                    except Exception:
                        status, items = "error", []
                    prepared = _prepare_news(items, limit=per_fund, now=resolved_now)
                    # A provider-declared non-empty response that cannot produce
                    # one valid announcement is a schema/provider error, not empty.
                    if status == "ok" and not prepared:
                        status = "error"
                    outcomes[code] = (status, prepared, False)
                    if status in {"ok", "empty"}:
                        _save_cached_announcements(
                            code,
                            prepared,
                            cache_date=cache_date,
                            now=resolved_now,
                        )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        return _build_announcement_prefetch_result(
            requested_codes,
            outcomes,
            fetched_at=fetched_at,
            now=resolved_now,
            enabled=True,
            input_count=len(fund_codes),
            eligible_count=len(eligible_codes),
        )

    def _from_eastmoney(
        self,
        topic: str,
        limit: int,
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        from app.services.eastmoney_news_client import fetch_stock_news_em

        rows = fetch_stock_news_em(topic, limit=limit)
        if not rows:
            return []

        resolved_now = normalize_news_now(now)
        items: list[NewsItem] = []
        for row in rows:
            title = _cell(row, "新闻标题", "title")
            if not title:
                continue
            published = _optional_str(_cell(row, "发布时间", "date"))
            snippet = _cell(row, "新闻内容", "content")
            items.append(
                NewsItem(
                    topic=topic,
                    title=str(title).strip(),
                    published_at=published,
                    source=_optional_str(_cell(row, "文章来源", "mediaName")) or "eastmoney",
                    url=_optional_str(_cell(row, "新闻链接", "url")),
                    snippet=_truncate(snippet),
                    is_today=is_news_published_today(published, resolved_now),
                )
            )
        return items

    def _from_cls(
        self,
        topic: str,
        limit: int,
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        try:
            from app.services.cls_news_client import search_cls_news

            return search_cls_news(
                topic,
                limit=limit,
                now=normalize_news_now(now),
            )
        except Exception:
            return []

    def _from_fund_announcements(
        self,
        fund_code: str,
        limit: int,
        *,
        now: datetime | None = None,
    ) -> list[NewsItem]:
        from app.services.eastmoney_news_client import fetch_fund_announcement_report_em

        rows = fetch_fund_announcement_report_em(fund_code, limit=limit)
        if not rows:
            return []

        resolved_now = normalize_news_now(now)
        return _announcement_rows_to_items(
            fund_code,
            rows,
            now=resolved_now,
        )


def _announcement_rows_to_items(
    fund_code: str,
    rows: list[dict],
    *,
    now: datetime,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for row in rows:
        title = _cell(row, "公告标题", "title")
        if not title:
            continue
        published = _cell(row, "公告日期", "date")
        published_str = _optional_str(published)
        items.append(
            NewsItem(
                topic=fund_code,
                title=str(title).strip(),
                published_at=published_str,
                source="fund-announcement",
                url=None,
                snippet=_truncate(_cell(row, "基金名称", "fund_name")),
                is_today=is_news_published_today(published_str, now),
            )
        )
    return items


def _normalize_fund_codes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        code = str(value or "").strip()
        if code == "000000" or not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def _fetch_fund_announcement_outcome(
    fund_code: str,
    limit: int,
    now: datetime,
) -> tuple[str, list[NewsItem]]:
    from app.services.eastmoney_news_client import (
        fetch_fund_announcement_report_result_em,
    )

    outcome = fetch_fund_announcement_report_result_em(fund_code, limit=limit)
    status = str(outcome.status)
    if status not in {"ok", "empty", "error", "timeout"}:
        status = "error"
    if status != "ok":
        return status, []
    return status, _announcement_rows_to_items(fund_code, outcome.items, now=now)


def _announcement_cache_topic(fund_code: str) -> str:
    return f"{_ANNOUNCEMENT_CACHE_PREFIX}{fund_code}"


def _get_cached_announcements(
    fund_code: str,
    *,
    cache_date: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> list[NewsItem] | None:
    try:
        return get_cached_news(
            _announcement_cache_topic(fund_code),
            cache_date=cache_date,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    except Exception as exc:
        logger.debug("announcement cache read failed code=%s: %s", fund_code, exc)
        return None


def _save_cached_announcements(
    fund_code: str,
    items: list[NewsItem],
    *,
    cache_date: str,
    now: datetime | None = None,
) -> None:
    try:
        save_cached_news(
            _announcement_cache_topic(fund_code),
            items,
            cache_date=cache_date,
            now=now,
        )
    except Exception as exc:
        logger.debug("announcement cache write failed code=%s: %s", fund_code, exc)


def _build_announcement_prefetch_result(
    requested_codes: list[str],
    outcomes: dict[str, tuple[str, list[NewsItem], bool]],
    *,
    fetched_at: str,
    now: datetime,
    enabled: bool,
    input_count: int,
    eligible_count: int,
) -> dict[str, object]:
    counts = {"ok": 0, "empty": 0, "error": 0, "timeout": 0}
    funds: list[dict[str, object]] = []
    collected: list[NewsItem] = []
    for code in requested_codes:
        status, items, from_cache = outcomes.get(code, ("error", [], False))
        normalized_status = status if status in counts else "error"
        counts[normalized_status] += 1
        if normalized_status == "ok":
            collected.extend(items)
        funds.append(
            {
                "fund_code": code,
                "status": normalized_status,
                "item_count": len(items) if normalized_status == "ok" else 0,
                "latest_published_at": latest_news_published_at(items),
                "from_cache": from_cache,
            }
        )

    requested = len(requested_codes)
    skipped_by_limit = max(eligible_count - requested, 0)
    provider_responses = counts["ok"] + counts["empty"]
    coverage = round(provider_responses / requested, 4) if requested else 0.0
    evidence_coverage = round(counts["ok"] / requested, 4) if requested else 0.0
    # Do not run the cross-topic title fallback de-duplicator here: two distinct
    # funds often publish generic titles such as “季度报告提示性公告”.
    ordered_items = _rank_news_by_recency(collected, now=now)
    return {
        "items": ordered_items,
        "enabled": enabled,
        "input_count": input_count,
        "eligible_fund_count": eligible_count,
        "requested_codes": requested_codes,
        "requested": requested,
        "skipped_by_limit": skipped_by_limit,
        "budget_coverage": round(requested / eligible_count, 4) if eligible_count else 0.0,
        **counts,
        "coverage": coverage,
        "coverage_percent": round(coverage * 100, 1),
        "evidence_coverage": evidence_coverage,
        "evidence_coverage_percent": round(evidence_coverage * 100, 1),
        "coverage_basis": "provider_response_ok_or_empty/requested",
        "fetched_at": fetched_at,
        "funds": funds,
    }


def compact_announcement_fetch_status(result: dict[str, object]) -> dict[str, object]:
    """Return the bounded announcement retrieval state exposed to the LLM.

    The item list and per-fund details intentionally stay out of this projection:
    announcement titles already travel through the normal news payload, while
    these counters let the model distinguish a genuine empty provider response
    from missing evidence caused by timeout or error.
    """

    def count(name: str) -> int:
        try:
            return max(0, int(result.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    def ratio(name: str) -> float:
        try:
            return round(max(0.0, min(float(result.get(name) or 0.0), 1.0)), 4)
        except (TypeError, ValueError):
            return 0.0

    requested = count("requested")
    ok = count("ok")
    empty = count("empty")
    error = count("error")
    timeout = count("timeout")
    provider_responses = ok + empty
    explicit_status = str(result.get("status") or "")

    if explicit_status in {
        "disabled",
        "not_requested",
        "ok",
        "empty",
        "partial",
        "timeout",
        "error",
    } and "enabled" not in result:
        status = explicit_status
    elif result.get("enabled") is False:
        status = "disabled"
    elif requested == 0:
        status = "not_requested"
    elif error == 0 and timeout == 0 and provider_responses >= requested:
        status = "ok" if ok else "empty"
    elif provider_responses > 0:
        status = "partial"
    elif timeout > 0 and error == 0:
        status = "timeout"
    else:
        status = "error"

    return {
        "status": status,
        "requested": requested,
        "ok": ok,
        "empty": empty,
        "error": error,
        "timeout": timeout,
        "coverage": ratio("coverage"),
        "evidence_coverage": ratio("evidence_coverage"),
        "fetched_at": str(result.get("fetched_at") or ""),
    }


def announcement_fetch_facts(result: dict[str, object]) -> dict[str, object]:
    """Persist compact decision status plus auditable per-fund retrieval detail."""

    facts = compact_announcement_fetch_status(result)
    facts["details"] = {
        key: value
        for key, value in result.items()
        if key != "items"
    }
    return facts


def merge_market_news_with_announcements(
    market_news: list[NewsItem],
    announcement_items: list[NewsItem],
    *,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Merge both evidence streams into one decision-time, newest-first view."""

    return _prepare_news([*market_news, *announcement_items], now=now)


def _normalize_topic(topic: str | None) -> str | None:
    if not topic:
        return None
    cleaned = topic.strip()
    for prefix in ("中证", "国证", "上证"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned or None


def _keyword_from_name(name: str) -> str | None:
    cleaned = name.replace("...", "").replace(".", "").strip()
    for token in _TOPIC_ALIASES:
        if token in cleaned:
            return token
    return None


def _is_today(published: str | None, today: str) -> bool:
    try:
        calendar_date = date.fromisoformat(today)
    except ValueError:
        return False
    reference = datetime.combine(calendar_date, time.min, tzinfo=CN_TZ)
    return is_news_published_today(published, reference)


def _rank_news_by_recency(
    items: list[NewsItem],
    *,
    now: datetime | None = None,
) -> list[NewsItem]:
    resolved_now = normalize_news_now(now)
    normalized = [_normalize_news_item(item, resolved_now) for item in items]
    return sorted(normalized, key=lambda item: _news_sort_key(item, resolved_now))


def _prepare_news(
    items: list[NewsItem],
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Run the canonical decision-context pipeline before applying top-K."""
    resolved_now = normalize_news_now(now)
    prepared = _rank_news_by_recency(
        _dedupe_news(items, now=resolved_now),
        now=resolved_now,
    )
    if limit is None:
        return prepared
    return prepared[: max(0, limit)]


def _cell(row: object, *names: str) -> str | None:
    if isinstance(row, dict):
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None
    for name in names:
        if hasattr(row, "index") and name in row.index:  # type: ignore[attr-defined]
            value = row[name]  # type: ignore[index]
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _truncate(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if len(cleaned) <= _SNIPPET_MAX_LEN:
        return cleaned
    return cleaned[: _SNIPPET_MAX_LEN - 1] + "…"


def _dedupe_news(
    items: list[NewsItem],
    *,
    now: datetime | None = None,
) -> list[NewsItem]:
    resolved_now = normalize_news_now(now)
    grouped: dict[str, list[NewsItem]] = {}
    for raw_item in items:
        item = _normalize_news_item(raw_item, resolved_now)
        grouped.setdefault(_news_identity_key(item), []).append(item)

    unique: list[NewsItem] = []
    for key in sorted(grouped):
        members = sorted(
            grouped[key],
            key=lambda item: _news_sort_key(item, resolved_now),
        )
        canonical = members[0]
        related_topics = _merge_related_topics(members)
        unique.append(canonical.model_copy(update={"related_topics": related_topics}))
    # Several report/tool merge paths call the de-duplicator directly. Keep its
    # output in the same canonical decision order instead of leaking identity-key
    # group order back into the LLM context.
    return sorted(unique, key=lambda item: _news_sort_key(item, resolved_now))


def _normalize_news_item(item: NewsItem, now: datetime) -> NewsItem:
    parsed = parse_news_published_at(item.published_at)
    is_today = (
        parsed.calendar_date == now.date()
        if parsed.calendar_date is not None
        else bool(item.is_today)
    )
    related_topics = _merge_related_topics([item])
    return item.model_copy(
        update={
            "is_today": is_today,
            "related_topics": related_topics,
        }
    )


def _news_sort_key(item: NewsItem, now: datetime) -> tuple[object, ...]:
    parsed = parse_news_published_at(item.published_at)
    is_today = (
        parsed.calendar_date == now.date()
        if parsed.calendar_date is not None
        else bool(item.is_today)
    )
    calendar_ordinal = (
        parsed.calendar_date.toordinal() if parsed.calendar_date is not None else -1
    )
    published_value = _published_sort_value(parsed)
    return (
        0 if is_today else 1,
        0 if parsed.calendar_date is not None else 1,
        -calendar_ordinal,
        0 if parsed.has_time else 1,
        -published_value,
        _normalize_identity_text(item.source),
        _normalize_identity_text(item.title),
        _normalize_identity_text(item.topic),
        _normalize_url(item.url) or "",
    )


def _published_sort_value(parsed: NewsPublishedTime) -> float:
    if parsed.moment is not None:
        return parsed.moment.timestamp()
    if parsed.calendar_date is not None:
        return float(parsed.calendar_date.toordinal() * 86_400)
    return float("-inf")


def _news_identity_key(item: NewsItem) -> str:
    if _normalize_identity_text(item.source) == "fund-announcement":
        # Generic announcement titles (for example quarterly report notices)
        # are common across unrelated funds.  Fund code/topic is therefore part
        # of the identity even when a provider later starts returning URLs.
        normalized_url = _normalize_url(item.url)
        identity = normalized_url or _normalize_identity_text(item.title)
        return (
            f"fund-announcement:{_normalize_identity_text(item.topic)}:{identity}"
        )
    normalized_url = _normalize_url(item.url)
    if normalized_url:
        return f"url:{normalized_url}"
    return (
        f"title-source:{_normalize_identity_text(item.title)}:"
        f"{_normalize_identity_text(item.source)}"
    )


def _normalize_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return _normalize_identity_text(raw) or None

    if not parsed.scheme and not parsed.netloc:
        return _normalize_identity_text(raw.split("#", 1)[0]) or None

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            query,
            "",
        )
    )


def _normalize_identity_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.casefold().split())


def _merge_related_topics(items: list[NewsItem]) -> list[str]:
    topics: dict[str, str] = {}
    for item in items:
        for raw_topic in [item.topic, *item.related_topics]:
            topic = str(raw_topic or "").strip()
            normalized = _normalize_identity_text(topic)
            if not normalized:
                continue
            current = topics.get(normalized)
            if current is None or topic < current:
                topics[normalized] = topic
    return [topics[key] for key in sorted(topics)]


def _news_cache_max_age_seconds(now: datetime | None = None) -> int | None:
    session = build_trading_session(now)
    session_kind = str(session.get("session_kind") or "")
    if session_kind in {"trading_day_intraday", "trading_day_pre_open"}:
        return NEWS_CACHE_STALE_SECONDS
    return None
