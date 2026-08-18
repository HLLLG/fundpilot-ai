"""离线兜底报告（`_offline_report`）契约回归。

`_build_final_report` 每次日报生成都会先构建这份离线 fallback 作对照，
LLM 未配置/失败时它还是唯一产出。2026-08 决策风格收敛把
`_apply_recommendation_guards_by_holding_order` 的 `topic_briefs` 参数删掉时，
主路径的调用点改了、这条 fallback 路径漏改，导致每次日报生成 TypeError——
而既有测试把 `_build_final_report` 整体 mock 掉，从未真正执行过这条链。

本文件不 mock 守卫链上的任何函数签名，直接把 `_offline_report` 从头跑到尾，
锁住"离线报告能完整走通守卫与 facts 收尾"这一契约。
"""
from __future__ import annotations

from app.models import AnalysisRequest, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import AnalysisFactsBundle
from app.services.deepseek_client import _offline_report


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            )
        ],
        profile=InvestorProfile(
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def _bundle() -> AnalysisFactsBundle:
    return AnalysisFactsBundle(
        session={
            "session_kind": "trading_day_after_close",
            "calendar_date": "2026-01-02",
        },
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={
            "holdings": [{"fund_code": "519674"}],
            "portfolio": {"holding_count": 1},
            "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        },
    )


def test_offline_report_runs_the_guard_chain_end_to_end(monkeypatch) -> None:
    # 守卫与离线构建器都会读分时/动能；两处 import 命名空间不同，各自断网。
    monkeypatch.setattr(
        "app.services.sector_intraday_summary.summarize_sector_intraday_for_holding",
        lambda _holding, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.sector_momentum.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )

    report = _offline_report(
        _request(),
        RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=1.2,
            alerts=[],
        ),
        snapshots=[],
        market_news=[],
        topic_briefs=[],
        analysis_bundle=_bundle(),
    )

    assert report.title
    assert len(report.fund_recommendations) == 1
    assert report.fund_recommendations[0].fund_code == "519674"
    # 守卫链真的跑过：guard 会把审计切片写回 facts。
    assert "daily_action_proposal" in report.analysis_facts
