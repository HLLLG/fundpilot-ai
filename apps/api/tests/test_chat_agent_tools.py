from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import Settings
from app.models import Holding, InvestorProfile
from app.services.chat_agent_tools import (
    CHAT_AGENT_TOOL_MAX_ROUNDS,
    ChatAgentContext,
    execute_chat_tool,
    tool_specs_for,
)
from app.services.report_chat_runtime import resolve_report_chat_runtime


def _call(name: str, args: dict | None = None, *, context: ChatAgentContext | None = None) -> dict:
    payload = execute_chat_tool(
        {
            "id": "call-1",
            "function": {
                "name": name,
                "arguments": json.dumps(args or {}, ensure_ascii=False),
            },
        },
        context
        or ChatAgentContext(surface="report", report={}, news_enabled=False),
    )
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed


def test_fast_chat_has_no_agent_tools() -> None:
    runtime = resolve_report_chat_runtime(
        Settings(_env_file=None, news_enabled=True, news_tool_max_rounds=2),
        "fast",
    )
    assert runtime.agent_tool_max_rounds == 0
    assert runtime.news_tool_max_rounds == 0


def test_deep_chat_keeps_news_rounds_separate_from_agent_budget() -> None:
    runtime = resolve_report_chat_runtime(
        Settings(_env_file=None, news_enabled=False, news_tool_max_rounds=2),
        "deep",
    )
    assert runtime.news_tool_max_rounds == 0
    assert runtime.agent_tool_max_rounds == CHAT_AGENT_TOOL_MAX_ROUNDS


def test_report_tool_specs_include_jobs_and_optional_news() -> None:
    names = {
        spec["function"]["name"]
        for spec in tool_specs_for(surface="report", news_enabled=True)
    }
    assert names >= {
        "get_holdings",
        "explain_holding_decision",
        "lookup_fund",
        "get_sector_context",
        "fetch_market_news",
        "run_daily_report",
        "run_discovery_scan",
    }
    discovery_names = {
        spec["function"]["name"]
        for spec in tool_specs_for(surface="discovery", news_enabled=False)
    }
    assert "explain_candidate_decision" in discovery_names
    assert "explain_holding_decision" not in discovery_names
    assert "fetch_market_news" not in discovery_names


def test_unknown_tool_returns_error() -> None:
    result = _call("not_a_tool")
    assert result["ok"] is False
    assert "unknown tool" in result["error"]


def test_get_holdings_is_read_only_compact(monkeypatch) -> None:
    holding = Holding(
        fund_code="012345",
        fund_name="测试行业基金",
        holding_amount=8000,
        holding_return_percent=5.5,
        sector_name="半导体",
        sector_return_percent=1.2,
        daily_return_percent=0.8,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.load_persisted_holdings",
        lambda fetch_benchmark=False: (
            [holding],
            "snapshot",
            "2026-08-15",
            datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc),
        ),
    )
    result = _call("get_holdings")
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["holdings"][0]["fund_code"] == "012345"
    assert "shares" not in result["holdings"][0]
    assert "只读当前账本" in result["note"]


def test_explain_holding_decision_reads_report_only() -> None:
    report = {
        "fund_recommendations": [
            {
                "fund_code": "012345",
                "fund_name": "测试行业基金",
                "action": "减仓",
                "suggested_position_change_percent": -25,
                "confidence": "中",
                "decision_path": "方向转弱后下调",
                "points": ["趋势跌破退出线"],
            }
        ],
        "analysis_facts": {
            "holdings": [
                {
                    "fund_code": "012345",
                    "sector_name": "半导体",
                    "escalation": {"min_bucket": -25, "reasons": ["连续跌破退出线"]},
                    "direction_exit": {
                        "state": "reduce",
                        "consecutive_days_below_exit_line": 2,
                        "add_eligible": False,
                        "thresholds_validated": False,
                    },
                }
            ]
        },
    }
    result = _call(
        "explain_holding_decision",
        {"fund_code": "012345"},
        context=ChatAgentContext(surface="report", report=report),
    )
    assert result["ok"] is True
    assert result["recommendation"]["suggested_position_change_percent"] == -25
    assert result["holding_facts"]["direction_exit"]["add_eligible"] is False


def test_explain_holding_decision_missing_fund() -> None:
    result = _call("explain_holding_decision", {"fund_code": "099999"})
    assert result["ok"] is False


def test_run_daily_report_requires_confirm() -> None:
    result = _call("run_daily_report", {"confirm": False})
    assert result["ok"] is False
    assert "confirm=true" in result["error"]


def test_run_daily_report_enqueues_existing_job(monkeypatch) -> None:
    holding = Holding(fund_code="012345", fund_name="测试行业基金", holding_amount=8000)

    class _Preflight:
        holdings = [holding]

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.load_persisted_holdings",
        lambda fetch_benchmark=False: ([holding], "snapshot", "2026-08-15", None),
    )
    monkeypatch.setattr(
        "app.services.decision_data_evidence.resolve_portfolio_preflight",
        lambda *_args, **_kwargs: _Preflight(),
    )
    monkeypatch.setattr(
        "app.database.get_investor_profile",
        lambda: InvestorProfile(),
    )
    monkeypatch.setattr("app.database.get_analysis_role_prompt", lambda: None)
    monkeypatch.setattr(
        "app.services.job_store.create_analysis_job",
        lambda _request: "job-analysis-1",
    )
    context = ChatAgentContext(surface="report", report={})
    result = _call("run_daily_report", {"confirm": True}, context=context)
    assert result["ok"] is True
    assert result["job_id"] == "job-analysis-1"
    assert context.pending_jobs == [{"job_kind": "analysis", "job_id": "job-analysis-1"}]


def test_run_discovery_scan_reuses_report_scan_mode(monkeypatch) -> None:
    holding = Holding(fund_code="012345", fund_name="测试行业基金", holding_amount=8000)
    captured = {}

    class _Preflight:
        holdings = [holding]

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.load_persisted_holdings",
        lambda fetch_benchmark=False: ([holding], "snapshot", "2026-08-15", None),
    )
    monkeypatch.setattr(
        "app.services.decision_data_evidence.resolve_portfolio_preflight",
        lambda *_args, **_kwargs: _Preflight(),
    )
    monkeypatch.setattr("app.database.get_investor_profile", lambda: InvestorProfile())
    monkeypatch.setattr("app.database.get_discovery_role_prompt", lambda: None)

    def _create(request):
        captured["request"] = request
        return "job-discovery-1"

    monkeypatch.setattr(
        "app.services.discovery_job_store.create_discovery_job",
        _create,
    )
    result = _call(
        "run_discovery_scan",
        {"confirm": True},
        context=ChatAgentContext(
            surface="discovery",
            report={
                "focus_sectors": ["半导体"],
                "discovery_facts": {
                    "scan_mode": "portfolio_gap",
                    "available_budget_yuan": 3000,
                    "discovery_strategy": "opportunity_first",
                },
            },
        ),
    )
    assert result["ok"] is True
    assert captured["request"].scan_mode == "portfolio_gap"
    assert captured["request"].budget_yuan == 3000
    assert captured["request"].focus_sectors == ["半导体"]


def test_lookup_fund_code_path(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_code_resolver.lookup_fund_name_by_code",
        lambda code: "华夏测试" if code == "000751" else None,
    )
    result = _call("lookup_fund", {"query": "000751"})
    assert result["ok"] is True
    assert result["items"][0]["fund_name"] == "华夏测试"
    assert "不构成推荐" in result["note"]
