from __future__ import annotations

from unittest.mock import MagicMock

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    Report,
    RiskAssessment,
)
from app.services.chat_agent_loop import iter_chat_agent_events
from app.services.chat_agent_tools import ChatAgentContext
from app.services.decision_data_evidence import PortfolioPreflightResult
from app.services.langgraph_trace import get_run, list_runs
from app.services import analyze_pipeline


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self._provider_deadline = None

    def _chat_completion(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {
                            "name": "explain_holding_decision",
                            "arguments": '{"fund_code":"012345"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "这份日报对 012345 给出减仓。"}


def test_chat_followup_graph_emits_nodes_and_tokens(monkeypatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("app.services.chat_agent_loop.DeepSeekClient", lambda: fake)
    report = {
        "fund_recommendations": [
            {
                "fund_code": "012345",
                "fund_name": "测试行业基金",
                "action": "减仓",
                "suggested_position_change_percent": -25,
            }
        ]
    }
    events = list(
        iter_chat_agent_events(
            messages=[{"role": "user", "content": "为什么减仓？"}],
            tools=[{"type": "function", "function": {"name": "explain_holding_decision"}}],
            context=ChatAgentContext(surface="report", report=report),
            model="test-model",
            max_rounds=2,
        )
    )
    types = [event["type"] for event in events]
    assert "graph" in types
    assert "status" in types
    tokens = "".join(event["content"] for event in events if event["type"] == "token")
    assert "减仓" in tokens
    assert fake.calls == 2
    run_ids = [event.get("run_id") for event in events if event.get("type") == "graph"]
    assert run_ids
    detail = get_run(str(run_ids[0]))
    assert detail is not None
    nodes = {item.get("node") for item in detail["events"]}
    assert "llm_call" in nodes
    assert "tools" in nodes


def test_daily_report_graph_records_code_and_worker_nodes(monkeypatch) -> None:
    holdings = [Holding(fund_code="012345", fund_name="测试基金", holding_amount=1000)]
    request = AnalysisRequest(
        holdings=holdings,
        profile=InvestorProfile(expected_investment_amount=10_000),
        analysis_mode="fast",
    )
    report = Report(
        title="测试日报",
        risk=RiskAssessment(level="medium", suggested_action="watch", weighted_return_percent=0, alerts=[]),
        holdings=holdings,
        summary="ok",
        recommendations=[],
        caveats=[],
        fund_recommendations=[
            FundRecommendation(fund_code="012345", fund_name="测试基金", action="观察")
        ],
        analysis_facts={"pipeline": {}},
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: PortfolioPreflightResult(
            holdings=list(holdings),
            context={"authoritative": True, "stale": False},
        ),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda items: items),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "evaluate_portfolio_risk",
        lambda *_args: report.risk,
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "FundDataService",
        lambda: MagicMock(get_snapshots_with_nav_trends=lambda _holdings: ([], {})),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "DeepSeekClient",
        lambda: MagicMock(generate_report=lambda *_args, **_kwargs: report),
    )
    monkeypatch.setattr(analyze_pipeline, "save_report", lambda value: value)

    saved = analyze_pipeline.run_analysis(request)
    pipeline = (saved.analysis_facts or {}).get("pipeline") or {}
    assert pipeline.get("graph_name") == "daily_report"
    assert pipeline.get("graph_run_id")
    runs = list_runs(graph_name="daily_report", limit=5)
    assert runs
    detail = get_run(str(pipeline["graph_run_id"]))
    assert detail is not None
    nodes = {item.get("node") for item in detail["events"]}
    assert {"preflight", "fetch_fund_data", "generate_report", "save_report"} <= nodes
    owners = {
        item.get("node"): item.get("owner")
        for item in detail["events"]
        if item.get("event_type") == "node_end"
    }
    assert owners.get("preflight") == "code"
    assert owners.get("generate_report") == "worker"


def test_graph_runs_endpoint_lists_current_user_traces(client) -> None:
    listed = client.get("/api/diagnostics/graph-runs?limit=5")
    assert listed.status_code == 200
    body = listed.json()
    assert body["schema_version"] == "langgraph_trace.v1"
    assert isinstance(body["runs"], list)
    missing = client.get("/api/diagnostics/graph-runs/not-a-real-run")
    assert missing.status_code == 404
