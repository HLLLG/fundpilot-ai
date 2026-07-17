from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timezone

import httpx

from app.config import Settings, get_settings
from app.models import NewsItem, TopicBrief, TopicBriefPoint
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    get_deepseek_http_client,
)
from app.services.news_freshness import normalize_news_now


def group_news_by_topic(items: list[NewsItem]) -> dict[str, list[NewsItem]]:
    grouped: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        # Cross-topic de-duplication keeps one canonical article and records the
        # other associations in ``related_topics``. Every downstream topic brief
        # must still see that article, otherwise de-duplication silently removes
        # evidence from the non-canonical fund/sector.
        for topic in dict.fromkeys([item.topic, *item.related_topics]):
            if topic:
                grouped[topic].append(item)
    return dict(grouped)


def build_topic_briefs_offline(
    topic: str,
    items: list[NewsItem],
    *,
    now: datetime | None = None,
) -> TopicBrief:
    titles = [item.title for item in items[:5] if item.title]
    summary = (
        f"「{topic}」共 {len(items)} 条相关新闻（规则摘要，未调用模型）。"
        f"要点：{'；'.join(titles[:3]) or '暂无标题'}。"
    )
    points = [
        TopicBriefPoint(
            headline=title[:80],
            sentiment="neutral",
            is_today=item.is_today,
            source_titles=[item.title],
            source_urls=[item.url] if item.url else [],
        )
        for item, title in zip(items[:3], titles[:3])
    ]
    return TopicBrief(
        topic=topic,
        summary=summary[:300],
        points=points,
        news_count=len(items),
        summarized_at=normalize_news_now(now).astimezone(timezone.utc),
        provider="rule-fallback",
    )


def summarize_topic(
    topic: str,
    items: list[NewsItem],
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> TopicBrief:
    resolved = settings or get_settings()
    if not items:
        return TopicBrief(
            topic=topic,
            summary=f"「{topic}」暂无检索到新闻。",
            points=[],
            news_count=0,
            provider="empty",
        )

    if not resolved.news_summarize or not resolved.deepseek_configured:
        return build_topic_briefs_offline(topic, items, now=now)

    try:
        return _summarize_topic_with_flash(topic, items, resolved, now=now)
    except Exception:
        return build_topic_briefs_offline(topic, items, now=now)


def _summarize_topic_with_flash(
    topic: str,
    items: list[NewsItem],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> TopicBrief:
    max_points = max(1, min(settings.news_summarize_max_points, 5))
    payload_items = [
        {
            "title": item.title,
            "published_at": item.published_at,
            "snippet": item.snippet,
            "is_today": item.is_today,
            "source": item.source,
        }
        for item in items[:12]
    ]
    user_content = json.dumps(
        {
            "topic": topic,
            "today": normalize_news_now(now).date().isoformat(),
            "items": payload_items,
            "max_points": max_points,
            "rules": [
                "只根据 items 压缩，不得编造数字、涨跌幅、公司名",
                "合并重复事件",
                "每条 point 的 source_titles 必须来自 items 中的 title 原文",
                "输出 JSON：topic, summary, points[{headline,sentiment,is_today,source_titles}]",
            ],
        },
        ensure_ascii=False,
    )
    request_payload = {
        "model": settings.resolved_news_summarize_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是财经新闻编辑。将同一主题的多条新闻压缩为结构化摘要。"
                    "只输出合法 JSON，不要 Markdown。"
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    timeout = httpx.Timeout(
        connect=10,
        read=settings.news_summarize_timeout_seconds,
        write=30,
        pool=10,
    )
    response = get_deepseek_http_client(settings).post(
        deepseek_chat_url(settings),
        headers=deepseek_request_headers(settings),
        json=request_payload,
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or "{}"
    parsed = json.loads(content)
    return _parse_topic_brief_response(topic, items, parsed, settings, now=now)


def _parse_topic_brief_response(
    topic: str,
    items: list[NewsItem],
    parsed: dict,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> TopicBrief:
    known_titles = {item.title for item in items}
    title_to_url = {item.title: item.url for item in items if item.url}
    title_to_today: dict[str, bool] = {}
    for item in items:
        title_to_today[item.title] = title_to_today.get(item.title, False) or bool(
            item.is_today
        )

    points: list[TopicBriefPoint] = []
    for raw in parsed.get("points") or []:
        if not isinstance(raw, dict):
            continue
        headline = str(raw.get("headline", "")).strip()[:80]
        if not headline:
            continue
        source_titles = [
            str(title).strip()
            for title in raw.get("source_titles") or []
            if str(title).strip() in known_titles
        ]
        if not source_titles:
            continue
        sentiment = str(raw.get("sentiment", "neutral")).lower()
        if sentiment not in {"bullish", "bearish", "neutral"}:
            sentiment = "neutral"
        points.append(
            TopicBriefPoint(
                headline=headline,
                sentiment=sentiment,  # type: ignore[arg-type]
                # Freshness is a source fact, not a model judgment. Derive it
                # from validated citations so an LLM cannot promote stale news
                # (and strings such as "false" cannot become truthy).
                is_today=any(title_to_today.get(title, False) for title in source_titles),
                source_titles=source_titles[:3],
                source_urls=[
                    url for title in source_titles if (url := title_to_url.get(title))
                ],
            )
        )
        if len(points) >= settings.news_summarize_max_points:
            break

    summary = str(parsed.get("summary", "")).strip()[:300]
    if not summary:
        summary = build_topic_briefs_offline(topic, items, now=now).summary

    if not points:
        return build_topic_briefs_offline(topic, items, now=now)

    return TopicBrief(
        topic=topic,
        summary=summary,
        points=points,
        news_count=len(items),
        summarized_at=normalize_news_now(now).astimezone(timezone.utc),
        provider=settings.resolved_news_summarize_model,
    )


def summarize_all_topics(
    items: list[NewsItem],
    settings: Settings | None = None,
    *,
    offline_only: bool = False,
    now: datetime | None = None,
) -> list[TopicBrief]:
    resolved = settings or get_settings()
    resolved_now = normalize_news_now(now)
    if not items:
        return []

    grouped = group_news_by_topic(items)
    if not grouped:
        return []

    if offline_only or not resolved.news_summarize or not resolved.deepseek_configured:
        return [
            build_topic_briefs_offline(topic, group_items, now=resolved_now)
            for topic, group_items in sorted(grouped.items())
        ]

    briefs_by_topic: dict[str, TopicBrief] = {}
    timeout = max(0.0, float(resolved.news_summarize_timeout_seconds))
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = {
            executor.submit(
                summarize_topic,
                topic,
                group_items,
                resolved,
                now=resolved_now,
            ): topic
            for topic, group_items in grouped.items()
        }
        try:
            for future in as_completed(futures, timeout=timeout):
                topic = futures[future]
                group_items = grouped[topic]
                try:
                    briefs_by_topic[topic] = future.result()
                except Exception:
                    briefs_by_topic[topic] = build_topic_briefs_offline(
                        topic, group_items, now=resolved_now
                    )
        except TimeoutError:
            pass
        finally:
            for future in futures:
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for topic, group_items in grouped.items():
        if topic not in briefs_by_topic:
            briefs_by_topic[topic] = build_topic_briefs_offline(
                topic, group_items, now=resolved_now
            )

    return [briefs_by_topic[topic] for topic in sorted(briefs_by_topic)]


def merge_topic_briefs(
    existing: list[TopicBrief],
    items: list[NewsItem],
    settings: Settings | None = None,
) -> list[TopicBrief]:
    """仅对新增或条数变化的主题重新 Flash 摘要，其余保留已有 brief。"""
    resolved = settings or get_settings()
    if not items:
        return list(existing)

    grouped = group_news_by_topic(items)
    by_topic = {brief.topic: brief for brief in existing}
    merged: list[TopicBrief] = []

    for topic, group_items in sorted(grouped.items()):
        prior = by_topic.get(topic)
        if prior is not None and prior.news_count == len(group_items):
            merged.append(prior)
            continue
        merged.append(summarize_topic(topic, group_items, resolved))

    return merged
