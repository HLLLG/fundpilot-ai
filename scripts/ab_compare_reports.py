#!/usr/bin/env python3
"""A/B compare legacy vs slim LLM payloads on report quality."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import get_investor_profile  # noqa: E402
from app.models import AnalysisRequest, InvestorProfile  # noqa: E402
from app.request_context import set_request_user_id  # noqa: E402
from app.services.analysis_payload import build_user_payload  # noqa: E402
from app.services.analysis_payload_legacy import (  # noqa: E402
    build_legacy_user_payload,
    legacy_system_news_hint,
)
from app.services.analysis_runtime import resolve_analysis_runtime  # noqa: E402
from app.services.deepseek_client import _parse_model_json, _system_prompt  # noqa: E402
from app.services.deepseek_http import (  # noqa: E402
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
)
from app.services.fund_data import FundDataService  # noqa: E402
from app.services.fund_profile import FundProfileService  # noqa: E402
from app.services.news_service import NewsService  # noqa: E402
from app.services.news_summarizer import summarize_all_topics  # noqa: E402
from app.services.portfolio_holdings_service import load_persisted_holdings  # noqa: E402
from app.services.recommendation_guard import normalize_action_text  # noqa: E402
from app.services.risk import evaluate_portfolio_risk  # noqa: E402

ALLOWED_ACTIONS = {"观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"}
NEXT_DAY_MARKERS = ("下一交易日", "次日", "明日", "周一", "周二", "周三", "周四", "周五")
DATA_MARKERS = (
    "sector_return",
    "板块",
    "nav_trend",
    "净值",
    "集中度",
    "weight",
    "权重",
    "estimated_daily",
    "估算",
    "holding_return",
    "持有收益",
    "sector_fund_gap",
    "背离",
)


@dataclass
class ReportScore:
    variant: str
    payload_chars: int
    title: str = ""
    summary_len: int = 0
    rec_count: int = 0
    coverage: float = 0.0
    valid_actions: int = 0
    next_day_points: int = 0
    data_aware_points: int = 0
    news_cited: int = 0
    risk_compliant: bool = True
    add_on_risk_review: int = 0
    total_points: int = 0
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)


def _legacy_system_prompt(request: AnalysisRequest, news_enabled: bool) -> str:
    """Pre-slim system prompt: no OUTPUT_REQUIREMENTS_SYSTEM append."""
    from app.services.analysis_prompt import resolve_role_prompt

    now = __import__("datetime").datetime.now()
    tactical = (request.profile.decision_style or "conservative") == "tactical"
    base = resolve_role_prompt(request.system_role_prompt)
    base += f"当前分析时点约为 {now.strftime('%Y-%m-%d %H:%M')}。"
    if news_enabled:
        base += legacy_system_news_hint()
    else:
        base += "若无新闻数据，须说明信息缺口并给出条件化方案。"
    if tactical:
        base += (
            "当前为战术短线模式：在遵守集中度与风险复核前提下，优先最大化当日收盘前与下一交易日的战术收益空间；"
            "须结合 sector_intraday（分时形态）、sector_momentum（涨后回吐等）、market_flow（北向资金）与 news.freshness_label；"
            "对「涨一天跌一天」场景须明确次日冲高回落时的止盈/观望条件，但仍不得承诺收益。"
        )
    else:
        base += "当前为稳健模式：偏保守，避免追涨，加仓需有当日要闻或明确盘面支撑。"
    base += "最终回复必须是完整 JSON，不要 Markdown，控制篇幅避免截断。"
    return base


def _call_deepseek_json(system: str, user_payload: dict, model: str) -> dict:
    settings = get_settings()
    response = httpx.post(
        deepseek_chat_url(settings),
        headers=deepseek_request_headers(settings),
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
            "max_tokens": settings.deepseek_max_tokens_report,
            "response_format": {"type": "json_object"},
        },
        timeout=deepseek_timeout(settings),
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or ""
    return _parse_model_json(content)


def _news_title_set(news: list, briefs: list) -> set[str]:
    titles: set[str] = set()
    for item in news:
        title = getattr(item, "title", None) or (item.get("title") if isinstance(item, dict) else None)
        if title:
            titles.add(str(title).strip())
    for brief in briefs:
        points = getattr(brief, "points", None) or (brief.get("points") if isinstance(brief, dict) else [])
        for point in points or []:
            source_titles = getattr(point, "source_titles", None) or point.get("source_titles") or []
            for title in source_titles:
                titles.add(str(title).strip())
    return titles


def score_parsed_report(
    variant: str,
    parsed: dict,
    *,
    holding_codes: set[str],
    risk_suggested_action: str,
    risk_level: str,
    known_titles: set[str],
    payload_chars: int,
) -> ReportScore:
    result = ReportScore(variant=variant, payload_chars=payload_chars)
    result.title = str(parsed.get("title") or "")
    result.summary_len = len(str(parsed.get("summary") or ""))

    recs = parsed.get("fund_recommendations") or []
    if not isinstance(recs, list):
        recs = []
    result.rec_count = len(recs)
    result.recommendations = [r for r in recs if isinstance(r, dict)]

    rec_codes = {str(r.get("fund_code", "")).strip() for r in result.recommendations}
    covered = holding_codes & rec_codes
    result.coverage = len(covered) / len(holding_codes) if holding_codes else 0.0
    if result.coverage < 1.0:
        missing = holding_codes - rec_codes
        result.issues.append(f"缺少基金建议: {', '.join(sorted(missing))}")

    add_bucket = 3

    def action_bucket(action: str) -> int:
        if any(token in action for token in ("减仓", "复核", "风控")):
            return 0
        if "暂停" in action:
            return 2
        if any(token in action for token in ("加仓", "定投", "分批")):
            return add_bucket
        return 1

    for rec in result.recommendations:
        action = normalize_action_text(str(rec.get("action") or "观察"))
        if action in ALLOWED_ACTIONS:
            result.valid_actions += 1
        else:
            result.issues.append(f"{rec.get('fund_code')} 非法 action: {action}")

        if risk_suggested_action == "risk_review" or risk_level == "high":
            if action_bucket(action) >= add_bucket:
                result.add_on_risk_review += 1
                result.risk_compliant = False

        points = rec.get("points") or []
        if not isinstance(points, list):
            points = []
        result.total_points += len(points)
        for point in points:
            text = str(point)
            if any(marker in text for marker in NEXT_DAY_MARKERS):
                result.next_day_points += 1
            if any(marker in text for marker in DATA_MARKERS):
                result.data_aware_points += 1

        bullish = rec.get("news_bullish") or []
        bearish = rec.get("news_bearish") or []
        if isinstance(bullish, str):
            bullish = [bullish]
        if isinstance(bearish, str):
            bearish = [bearish]
        for headline in list(bullish) + list(bearish):
            h = str(headline).strip()
            if h and h not in {"暂无明确利好", "暂无明确利空"}:
                if any(_title_matches(h, known) for known in known_titles):
                    result.news_cited += 1
                elif not h.startswith("暂无"):
                    result.issues.append(f"{rec.get('fund_code')} 新闻标题未在预取列表: {h[:40]}")

    # Weighted score 0-100
    weights = {
        "coverage": 25,
        "valid_actions": 15,
        "next_day": 20,
        "data_aware": 15,
        "news": 10,
        "risk": 10,
        "summary": 5,
    }
    n = max(len(result.recommendations), 1)
    result.score = (
        weights["coverage"] * result.coverage
        + weights["valid_actions"] * (result.valid_actions / n)
        + weights["next_day"] * min(1.0, result.next_day_points / n)
        + weights["data_aware"] * min(1.0, result.data_aware_points / n)
        + weights["news"] * min(1.0, result.news_cited / max(1, n))
        + (weights["risk"] if result.risk_compliant else 0)
        + weights["summary"] * min(1.0, result.summary_len / 120)
    )
    return result


def _title_matches(candidate: str, known: str) -> bool:
    if candidate == known:
        return True
    if candidate in known or known in candidate:
        return True
    return False


def _print_score(score: ReportScore) -> None:
    print(f"\n=== {score.variant} (payload {score.payload_chars:,} chars, score {score.score:.1f}) ===")
    print(f"Title: {score.title}")
    print(
        f"Coverage {score.coverage:.0%} | valid_actions {score.valid_actions}/{score.rec_count} | "
        f"next_day_points {score.next_day_points}/{score.total_points} | "
        f"data_aware {score.data_aware_points}/{score.total_points} | news_cited {score.news_cited}"
    )
    if score.issues:
        print("Issues:")
        for issue in score.issues[:8]:
            print(f"  - {issue}")
    for rec in score.recommendations:
        points = rec.get("points") or []
        first = points[0] if points else ""
        print(f"  {rec.get('fund_code')} | {rec.get('action')} | {first}")


def main() -> int:
    set_request_user_id(1)
    settings = get_settings()
    if not settings.deepseek_configured:
        print("DeepSeek API key not configured.")
        return 1

    mode = "fast"
    holdings, _source, _ = load_persisted_holdings()
    if not holdings:
        print("No holdings.")
        return 1

    profile = get_investor_profile() or InvestorProfile()
    resolved = FundProfileService().resolve_holdings(holdings)
    request = AnalysisRequest(holdings=resolved, profile=profile, analysis_mode=mode)
    risk = evaluate_portfolio_risk(resolved, profile)
    snapshots, nav_trends = FundDataService().get_snapshots_with_nav_trends(resolved)
    news = NewsService().prefetch_for_holdings(resolved)
    topic_briefs = summarize_all_topics(news, settings) if news else []
    runtime = resolve_analysis_runtime(settings, mode)

    legacy_payload = build_legacy_user_payload(
        request, risk, snapshots, news, topic_briefs, nav_trends
    )
    slim_payload = build_user_payload(
        request, risk, snapshots, news, topic_briefs, nav_trends, analysis_mode=mode
    )

    legacy_system = _legacy_system_prompt(request, runtime.news_enabled)
    slim_system = _system_prompt(
        runtime.news_enabled,
        request.profile.decision_style or "conservative",
        request.system_role_prompt,
    )

    print(f"Comparing {len(resolved)} holdings | mode={mode} | model={runtime.model}")
    print(f"Legacy payload: {len(json.dumps(legacy_payload, ensure_ascii=False)):,} chars")
    print(f"Slim payload:   {len(json.dumps(slim_payload, ensure_ascii=False)):,} chars")
    print("Calling DeepSeek (legacy)...")
    legacy_parsed = _call_deepseek_json(legacy_system, legacy_payload, runtime.model)
    print("Calling DeepSeek (slim)...")
    slim_parsed = _call_deepseek_json(slim_system, slim_payload, runtime.model)

    codes = {h.fund_code for h in resolved}
    titles = _news_title_set(news, topic_briefs)

    legacy_score = score_parsed_report(
        "legacy",
        legacy_parsed,
        holding_codes=codes,
        risk_suggested_action=risk.suggested_action,
        risk_level=risk.level,
        known_titles=titles,
        payload_chars=len(json.dumps(legacy_payload, ensure_ascii=False)),
    )
    slim_score = score_parsed_report(
        "slim",
        slim_parsed,
        holding_codes=codes,
        risk_suggested_action=risk.suggested_action,
        risk_level=risk.level,
        known_titles=titles,
        payload_chars=len(json.dumps(slim_payload, ensure_ascii=False)),
    )

    _print_score(legacy_score)
    _print_score(slim_score)

    delta = slim_score.score - legacy_score.score
    print(f"\n=== Verdict: slim - legacy = {delta:+.1f} points ===")
    if delta < -3:
        print("Slim underperforms — improvements needed.")
    elif delta > 3:
        print("Slim outperforms legacy on rubric.")
    else:
        print("Roughly equivalent on rubric; check qualitative points below.")

    out_path = ROOT / "data" / "ab_report_compare.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "legacy": {
                    "score": legacy_score.score,
                    "title": legacy_score.title,
                    "issues": legacy_score.issues,
                    "recommendations": legacy_score.recommendations,
                    "parsed": legacy_parsed,
                },
                "slim": {
                    "score": slim_score.score,
                    "title": slim_score.title,
                    "issues": slim_score.issues,
                    "recommendations": slim_score.recommendations,
                    "parsed": slim_parsed,
                },
                "delta": delta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Full JSON written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
