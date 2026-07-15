from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import Settings
from app.models import AnalysisRequest, DiscoveryRequest, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import AnalysisFactsBundle
from app.services.deepseek_client import _offline_report
from app.services.deepseek_http import (
    ProviderFailure,
    classify_deepseek_failure,
)
from app.services.discovery_client import DiscoveryClient
from app.services.discovery_offline import build_offline_discovery_report
from app.services.decision_data_evidence import contains_executable_decision_text
from app.services.provider_fallback import (
    apply_provider_failure_to_facts,
    merge_pipeline_metadata,
)
from app.services.trading_session import build_trading_session


_CN_TZ = ZoneInfo("Asia/Shanghai")
_DECISION_AT = datetime(2026, 7, 14, 14, 59, 58, tzinfo=_CN_TZ)
_ATTEMPTED_MODEL = "deepseek-v4-pro"
_FAKE_KEY = "sk-" + "f" * 32


def _analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="示例持仓基金",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(expected_investment_amount=20_000),
        analysis_mode="deep",
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _analysis_bundle() -> AnalysisFactsBundle:
    session = build_trading_session(_DECISION_AT)
    return AnalysisFactsBundle(
        session=session,
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={
            "readonly": True,
            "session": session,
            "allowed_actions": ["观察", "风控复核"],
            "holdings": [
                {
                    "fund_code": "519674",
                    "fund_name": "示例持仓基金",
                    "weight_percent": 50,
                }
            ],
            "portfolio": {"weighted_return_percent": 0},
            "data_evidence_guard": {"execution_blocked": False},
        },
    )


def _candidate() -> dict:
    return {
        "fund_code": "000001",
        "fund_name": "示例候选基金",
        "sector_label": "半导体",
        "fund_quality_score": 88,
        "quality_gate": {"status": "eligible"},
    }


def _discovery_facts() -> dict:
    return {
        "readonly": True,
        "session": build_trading_session(_DECISION_AT),
        "profile": {},
        "portfolio_gap": {
            "available_budget_yuan": 5_000,
            "target_sectors": ["半导体"],
            "holdings_slim": [],
        },
        "portfolio_snapshot": {"authoritative": True, "stale": False},
        "data_evidence_guard": {"execution_blocked": False},
        "candidate_pool": [_candidate()],
        "sector_heat": [],
        "news": {"freshness_label": "empty"},
    }


def _rate_limit_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.invalid/chat")
    response = httpx.Response(
        429,
        request=request,
        text="secret-provider-body secret-user-appendix",
    )
    return httpx.HTTPStatusError(
        "secret-transport-message",
        request=request,
        response=response,
    )


def _assert_fail_closed_report(report, *, failure_category: str) -> None:
    facts = report.analysis_facts if hasattr(report, "analysis_facts") else report.discovery_facts
    pipeline = facts["pipeline"]

    assert report.provider == "offline-fallback"
    assert report.created_at == _DECISION_AT
    assert pipeline["provider"] == "offline-fallback"
    assert pipeline["provider_status"] == "fallback"
    assert pipeline["attempted_model"] == _ATTEMPTED_MODEL
    assert pipeline["provider_failure_category"] == failure_category
    assert pipeline["provider_failure"]["category"] == failure_category
    assert pipeline["execution_blocked"] is True
    assert facts["data_evidence_guard"]["execution_blocked"] is True

    recommendations = (
        report.fund_recommendations
        if hasattr(report, "fund_recommendations")
        else report.recommendations
    )
    assert recommendations
    for recommendation in recommendations:
        assert recommendation.confidence == "低"
        amount = (
            recommendation.amount_yuan
            if hasattr(recommendation, "amount_yuan")
            else recommendation.suggested_amount_yuan
        )
        assert amount is None
        assert recommendation.action in {"观察", "风控复核", "建议关注", "等待回调"}

    if hasattr(report, "fund_recommendations"):
        assert report.recommendations == [
            "模型服务暂不可用，本次仅保留观察与风险复核；请刷新数据后重新生成。"
        ]
        assert all(
            not contains_executable_decision_text(line)
            for line in report.recommendations
        )

    serialized = report.model_dump_json().lower()
    assert "secret-provider-body" not in serialized
    assert "secret-user-appendix" not in serialized
    assert "secret-transport-message" not in serialized


def test_provider_failure_marks_facts_fail_closed_and_preserves_existing_guard():
    facts = {
        "pipeline": {"analysis_mode": "deep"},
        "data_evidence_guard": {
            "execution_blocked": False,
            "blocked_fund_codes": ["000001"],
            "global_reasons": ["stale_snapshot"],
        },
    }
    failure = ProviderFailure(
        category="rate_limited",
        message="safe",
        retryable=True,
        status_code=429,
    )

    result = apply_provider_failure_to_facts(
        facts,
        failure=failure,
        attempted_model="deepseek-reasoner",
        prompt_contract={"schema_version": "prompt_contract.v1"},
    )

    assert result is facts
    assert result["pipeline"]["analysis_mode"] == "deep"
    assert result["pipeline"]["provider"] == "offline-fallback"
    assert result["pipeline"]["attempted_model"] == "deepseek-reasoner"
    assert result["pipeline"]["provider_failure_category"] == "rate_limited"
    assert result["pipeline"]["provider_failure_status_code"] == 429
    assert result["pipeline"]["prompt_contract"]["schema_version"] == "prompt_contract.v1"
    assert result["data_evidence_guard"]["execution_blocked"] is True
    assert result["data_evidence_guard"]["blocked_fund_codes"] == ["000001"]
    assert result["data_evidence_guard"]["global_reasons"] == [
        "stale_snapshot",
        "provider_failure:rate_limited",
    ]


def test_final_pipeline_merge_does_not_erase_failure_metadata():
    facts = {"pipeline": {"provider_failure_category": "timeout"}}

    merge_pipeline_metadata(
        facts,
        {"analysis_mode": "deep", "provider_failure_category": None},
    )

    assert facts["pipeline"] == {
        "analysis_mode": "deep",
        "provider_failure_category": "timeout",
    }


def test_daily_offline_provider_timeout_is_fixed_time_and_fail_closed(monkeypatch):
    failure = classify_deepseek_failure(
        httpx.ReadTimeout("secret-transport-message")
    )

    # Keep this contract test focused on the provider-fallback projection. The
    # deterministic guards have their own exhaustive suites and run before the
    # final provider-failure projection in production.
    monkeypatch.setattr(
        "app.services.deepseek_client._apply_recommendation_guards_by_holding_order",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_news_citation_guards",
        lambda recommendations, *_args, **_kwargs: recommendations,
    )

    report = _offline_report(
        _analysis_request(),
        _risk(),
        [],
        market_news=[],
        topic_briefs=[],
        analysis_bundle=_analysis_bundle(),
        provider_failure=failure,
        attempted_model=_ATTEMPTED_MODEL,
        decision_at=_DECISION_AT,
    )

    _assert_fail_closed_report(report, failure_category="timeout")


def test_discovery_offline_rate_limit_is_fixed_time_and_fail_closed():
    failure = classify_deepseek_failure(_rate_limit_error())

    report = build_offline_discovery_report(
        target_sectors=["半导体"],
        candidate_pool=[_candidate()],
        discovery_facts=_discovery_facts(),
        profile=InvestorProfile(),
        focus_sectors=["半导体"],
        analysis_mode="deep",
        provider_failure=failure,
        attempted_model=_ATTEMPTED_MODEL,
        decision_at=_DECISION_AT,
    )

    _assert_fail_closed_report(report, failure_category="rate_limited")


@pytest.mark.parametrize(
    ("failure_factory", "expected_category"),
    [
        (
            lambda: httpx.ReadTimeout("secret-transport-message"),
            "timeout",
        ),
        (_rate_limit_error, "rate_limited"),
    ],
    ids=["timeout", "rate-limited"],
)
def test_discovery_sync_provider_failure_returns_fail_closed_report(
    monkeypatch,
    failure_factory,
    expected_category: str,
):
    client = DiscoveryClient()
    client.settings = Settings(
        deepseek_api_key=_FAKE_KEY,
        deepseek_model=_ATTEMPTED_MODEL,
    )

    def fail_model(*_args, **_kwargs):
        raise failure_factory()

    monkeypatch.setattr(client, "_call_model", fail_model)

    report = client.generate_report(
        target_sectors=["半导体"],
        focus_sectors=["半导体"],
        scan_mode="full_market",
        candidate_pool=[_candidate()],
        discovery_facts=_discovery_facts(),
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=5_000,
        sector_heat=[],
        market_news=[],
        topic_briefs=[],
        analysis_mode="deep",
        decision_at=_DECISION_AT,
    )

    _assert_fail_closed_report(report, failure_category=expected_category)


def _daily_sync_timeout_report(monkeypatch):
    from app.services.deepseek_client import DeepSeekClient

    client = DeepSeekClient()
    client.settings = Settings(
        deepseek_api_key=_FAKE_KEY,
        deepseek_model=_ATTEMPTED_MODEL,
    )
    client.news_service.prefetch_for_holdings = lambda *_args, **_kwargs: []
    client.news_service.prefetch_fund_announcements = lambda *_args, **_kwargs: {
        "items": [],
        "requested": 0,
    }
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *_args, **_kwargs: _analysis_bundle(),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.build_analysis_chat_messages",
        lambda *_args, **_kwargs: [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "{}"},
        ],
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._apply_recommendation_guards_by_holding_order",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_news_citation_guards",
        lambda recommendations, *_args, **_kwargs: recommendations,
    )
    monkeypatch.setattr(
        client,
        "_chat_completion",
        lambda **_kwargs: (_ for _ in ()).throw(
            httpx.ReadTimeout("secret-transport-message")
        ),
    )
    return client.generate_report(
        _analysis_request(),
        _risk(),
        [],
        decision_at=_DECISION_AT,
    )


def test_daily_sync_provider_timeout_returns_fail_closed_report(monkeypatch):
    report = _daily_sync_timeout_report(monkeypatch)

    _assert_fail_closed_report(report, failure_category="timeout")
    contract = report.analysis_facts["pipeline"]["prompt_contract"]
    assert contract["schema_version"] == "prompt_contract.v1"
    assert contract["model"] == _ATTEMPTED_MODEL


def test_daily_background_provider_timeout_completes_with_fallback(
    monkeypatch,
):
    import app.services.job_store as job_store

    updates: list[dict] = []
    produced: dict = {}
    monkeypatch.setattr(job_store, "_load_request", lambda _job_id: _analysis_request())

    def run_analysis(_request, on_progress=None):
        report = _daily_sync_timeout_report(monkeypatch)
        produced["report"] = report
        return report

    monkeypatch.setattr(job_store, "run_analysis", run_analysis)
    monkeypatch.setattr(
        job_store,
        "_update_job",
        lambda _job_id, **values: updates.append(values),
    )

    job_store._run_job("daily-provider-fallback", 1)

    assert updates[-1]["status"] == "completed"
    assert updates[-1]["report_id"] == produced["report"].id
    assert not any(update.get("status") == "failed" for update in updates)
    _assert_fail_closed_report(produced["report"], failure_category="timeout")


def test_discovery_background_provider_rate_limit_completes_with_fallback(
    monkeypatch,
):
    import app.services.discovery_job_store as job_store

    client = DiscoveryClient()
    client.settings = Settings(
        deepseek_api_key=_FAKE_KEY,
        deepseek_model=_ATTEMPTED_MODEL,
    )
    monkeypatch.setattr(
        client,
        "_call_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_rate_limit_error()),
    )
    request = DiscoveryRequest(
        holdings=[],
        profile=InvestorProfile(expected_investment_amount=5_000),
        analysis_mode="deep",
    )
    updates: list[dict] = []
    produced: dict = {}
    monkeypatch.setattr(job_store, "_load_request", lambda _job_id: request)

    def run_discovery(_request, on_progress=None):
        report = client.generate_report(
            target_sectors=["半导体"],
            focus_sectors=["半导体"],
            candidate_pool=[_candidate()],
            discovery_facts=_discovery_facts(),
            profile=request.profile,
            held_codes=set(),
            budget_yuan=5_000,
            sector_heat=[],
            market_news=[],
            topic_briefs=[],
            analysis_mode="deep",
            decision_at=_DECISION_AT,
        )
        produced["report"] = report
        return report

    monkeypatch.setattr(job_store, "run_discovery", run_discovery)
    monkeypatch.setattr(
        job_store,
        "_update_job",
        lambda _job_id, **values: updates.append(values),
    )

    job_store._run_job("discovery-provider-fallback", 1)

    assert updates[-1]["status"] == "completed"
    assert updates[-1]["discovery_report_id"] == produced["report"].id
    assert not any(update.get("status") == "failed" for update in updates)
    _assert_fail_closed_report(produced["report"], failure_category="rate_limited")
