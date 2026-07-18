from __future__ import annotations

from datetime import datetime, timezone
import json

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
    assert status["confidence_eligible"] is False
    assert status["confidence_block_reasons"] == [
        "factor_ic_contract_upgrade_required",
        "factor_ic_contract_invalid",
    ]
    assert status["expected_universe_size"] == 1500


def test_recent_legacy_local_snapshot_is_diagnostic_only(tmp_path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(
        json.dumps(
            {
                "available": True,
                "schema_version": 1,
                "run_date": "2026-07-10",
                "generated_at": "2026-07-10T07:52:47+00:00",
                "params": {
                    "universe_size": 25,
                    "nav_days": 600,
                    "rebalance_step": 21,
                    "forward_days": 20,
                    "factor_lookback": 250,
                },
                "universe_size": 25,
                "rebalance_count": 28,
                "forward_days": 20,
                "factors": [
                    {
                        "factor": "momentum",
                        "n_periods": 27,
                        "mean_ic": 0.9863,
                        "significant": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    context = factor_ic_snapshot.load_factor_ic_context(
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
        local_path=path,
        connection_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")),
    )

    assert context["state"] == "unavailable"
    assert context["status"]["available"] is True
    assert context["status"]["stale"] is False
    assert context["status"]["confidence_eligible"] is False
    assert context["status"]["confidence_block_reasons"] == [
        "factor_ic_contract_upgrade_required",
        "factor_ic_contract_invalid",
    ]


def test_unavailable_context_cannot_leak_research_model_to_consumers(monkeypatch) -> None:
    factor_confidence.clear_ic_summary_cache()
    monkeypatch.setattr(
        factor_confidence,
        "load_factor_ic_context",
        lambda **_kwargs: {
            "state": "unavailable",
            "status": {
                "available": True,
                "confidence_eligible": False,
                "upgrade_required": True,
            },
            "summary": {
                "factors": [
                    {
                        "factor": "momentum",
                        "mean_ic": 0.99,
                        "significant": True,
                    }
                ],
                "research_model": {"version": "factor_ic.v2"},
            },
        },
    )

    context = factor_confidence.load_ic_context()

    assert context["state"] == "unavailable"
    assert context["factors"] == {}
    assert context["research_model"] is None
    factor_confidence.clear_ic_summary_cache()


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
