from __future__ import annotations

from datetime import datetime, timezone

from app import main
from app.services import factor_confidence, factor_ic_snapshot


def test_legacy_factor_ic_status_requires_contract_upgrade() -> None:
    status = factor_ic_snapshot._build_factor_ic_status_from_loaded(
        {
            "available": True,
            "schema_version": 1,
            "run_date": "2026-07-10",
            "generated_at": "2026-07-10T07:52:47+00:00",
            "params": {"universe_size": 300, "universe_mode": "sampled"},
            "universe_size": 300,
            "factors": [],
        },
        "database",
        {},
        threshold=30,
        current=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert status["stale"] is False
    assert status["upgrade_required"] is True
    assert status["expected_universe_size"] == 1500


def test_portfolio_factor_endpoint_uses_current_peer_research_model(monkeypatch) -> None:
    research_model = {
        "version": "factor_ic.v2",
        "peer_distributions": {"hh": {"eligible_count": 677}},
    }
    captured: dict = {}

    monkeypatch.setattr(main, "load_persisted_holdings", lambda: ([], None, None))
    monkeypatch.setattr(
        factor_confidence,
        "load_ic_context",
        lambda: {
            "state": "available",
            "status": {"available": True, "stale": False, "schema_version": 2},
            "research_model": research_model,
            "factors": {},
        },
    )

    def fake_build(holdings, *, research_model=None):
        captured["research_model"] = research_model
        return {
            "available": True,
            "universe_size": 1500,
            "model_version": "factor_ic.v2",
            "funds": [{"peer_group": "hh"}],
        }

    monkeypatch.setattr(main, "build_factor_scores_payload", fake_build)
    monkeypatch.setattr(
        factor_confidence,
        "factor_reliability",
        lambda _factors, **kwargs: {
            "momentum": {
                "level": "中",
                "basis": f"peer={kwargs.get('segment')}",
            }
        },
    )

    payload = main.portfolio_factor_scores()

    assert captured["research_model"] is research_model
    assert payload["reliability_scope"] == "per_fund_peer_group"
    assert payload["funds"][0]["factor_reliability"]["momentum"]["basis"] == "peer=hh"
