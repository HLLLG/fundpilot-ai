#!/usr/bin/env python3
"""Compare legacy vs slim LLM user payload and optionally run one analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from app.models import AnalysisRequest, InvestorProfile, NewsItem  # noqa: E402
from app.services.analysis_payload import build_user_payload  # noqa: E402
from app.services.analyze_pipeline import run_analysis  # noqa: E402
from app.database import get_investor_profile  # noqa: E402
from app.services.fund_data import FundDataService  # noqa: E402
from app.services.fund_profile import FundProfileService  # noqa: E402
from app.services.news_service import NewsService  # noqa: E402
from app.services.portfolio_holdings_service import load_persisted_holdings  # noqa: E402
from app.services.risk import evaluate_portfolio_risk  # noqa: E402
from app.request_context import set_request_user_id  # noqa: E402


def _legacy_payload_shape(
    request: AnalysisRequest,
    risk,
    snapshots,
    news: list[NewsItem],
    nav_trends: dict,
) -> dict:
    from app.services.analysis_facts import build_analysis_facts
    from app.services.holding_metrics import HOLDING_RETURN_SEMANTICS, holding_analysis_payload
    from app.services.portfolio_snapshot import build_portfolio_trend_context
    from app.services.trading_session import build_trading_session

    session = build_trading_session()
    facts = build_analysis_facts(
        request.holdings,
        risk,
        snapshots,
        request.profile,
        [],
        nav_trends,
        news,
        session=session,
        portfolio_trend=build_portfolio_trend_context(),
    )
    return {
        "today": "legacy",
        "analysis_session": session["session_kind"],
        "session": session,
        "profile": request.profile.model_dump(),
        "holding_return_semantics": HOLDING_RETURN_SEMANTICS,
        "analysis_facts": facts,
        "holdings": [holding_analysis_payload(h) for h in request.holdings],
        "risk": risk.model_dump(),
        "fund_snapshots": [s.model_dump() for s in snapshots],
        "prefetched_news": [n.model_dump() for n in news],
        "requirements": ["x"] * 20,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-analysis", action="store_true", help="Call DeepSeek once (fast mode)")
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast")
    args = parser.parse_args()

    set_request_user_id(1)
    holdings, source, _snapshot_date = load_persisted_holdings()
    if not holdings:
        print("No persisted holdings found in database.")
        return 1

    profile = get_investor_profile() or InvestorProfile()
    resolved = FundProfileService().resolve_holdings(holdings)
    request = AnalysisRequest(holdings=resolved, profile=profile, analysis_mode=args.mode)
    risk = evaluate_portfolio_risk(resolved, profile)
    snapshots, nav_trends = FundDataService().get_snapshots_with_nav_trends(resolved)
    news = NewsService().prefetch_for_holdings(resolved)

    legacy = _legacy_payload_shape(request, risk, snapshots, news, nav_trends)
    slim = build_user_payload(
        request,
        risk,
        snapshots,
        news,
        [],
        nav_trends,
        analysis_mode=args.mode,
    )

    legacy_len = len(json.dumps(legacy, ensure_ascii=False))
    slim_len = len(json.dumps(slim, ensure_ascii=False))
    reduction = (1 - slim_len / legacy_len) * 100 if legacy_len else 0

    print(f"Holdings: {len(resolved)} (source={source})")
    print(f"News items prefetched: {len(news)}")
    print(f"Legacy user JSON chars: {legacy_len:,}")
    print(f"Slim user JSON chars:   {slim_len:,}")
    print(f"Reduction: {reduction:.1f}%")
    print(f"Slim top-level keys: {list(slim.keys())}")
    if slim["analysis_facts"]["holdings"]:
        sample = slim["analysis_facts"]["holdings"][0]
        print(
            "Sample holding keys:",
            sorted(sample.keys()),
        )
        if sample.get("sector_fund_gap_percent") is not None:
            print(f"  sector_fund_gap_percent: {sample['sector_fund_gap_percent']}")

    if args.run_analysis:
        print(f"\nRunning {args.mode} analysis via pipeline...")
        report = run_analysis(request)
        print(f"Report: {report.title}")
        print(f"Provider: {report.provider}")
        print(f"Summary: {report.summary[:200]}...")
        for rec in report.fund_recommendations:
            print(f"  - {rec.fund_code} {rec.action}: {rec.points[0] if rec.points else ''}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
