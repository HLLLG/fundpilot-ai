from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.fund_holdings_snapshot import build_fund_holdings_snapshot
from app.services.fund_holdings_snapshot_repository import (
    clear_fund_holdings_snapshot_refresh_state,
    resolve_fund_holdings_snapshot_at_decision,
)
from app.services.fund_lookthrough_context import build_fund_lookthrough_context
from app.services.fund_lookthrough_research import compact_fund_lookthrough_for_llm


CN = ZoneInfo("Asia/Shanghai")
DECISION = datetime(2026, 8, 31, 12, 0, tzinfo=CN)


def _snapshot(code: str, security: str = "600001", weight: float = 20.0) -> dict:
    rows = [
        {
            "fund_code": code,
            "report_period": "2026-Q2",
            "security_code": security,
            "security_name": f"Security {security}",
            "weight_percent": weight,
            "rank": 1,
            "scope": "top10",
        }
    ]
    return build_fund_holdings_snapshot(
        rows,
        [
            {
                "fund_code": code,
                "report_period": "2026-Q2",
                "title": "2026 Q2 top 10 holdings report",
                "published_at": "2026-07-20T09:00:00+08:00",
                "scope": "top10",
            }
        ],
        fund_code=code,
        decision_at=DECISION,
    )


def _portfolio_context() -> dict:
    return {
        "authoritative": True,
        "position_complete": True,
        "position_snapshot": {
            "schema_version": "portfolio_position_snapshot.v1",
            "snapshot_id": "portfolio-snapshot-1",
            "source": "decision_preflight:snapshot",
            "captured_at": "2026-08-31T11:00:00+08:00",
            "as_of_date": "2026-08-31",
            "position_complete": True,
            "pending_transaction_count": 0,
            "known_unsettled_transaction_count": 0,
            "ledger_truncated": False,
            "conflicts": [],
            "positions": [
                {
                    "fund_code": "000001",
                    "settled_shares": "100",
                    "market_value_cny": "1000",
                    "nav_date": "2026-08-31",
                    "valuation_source": "frozen_position_snapshot",
                }
            ],
            "cash": {"known": True, "balance_cny": "200", "quality": "confirmed"},
            "totals": {
                "invested_market_value_cny": "1000",
                "total_assets_cny": "1200",
            },
            "completeness": {
                "valuation_complete": True,
                "cash_complete": True,
            },
        },
    }


def _settings(*, timeout: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        fund_holdings_context_max_funds=40,
        fund_holdings_context_live_max_funds=8,
        fund_holdings_context_workers=2,
        fund_holdings_context_total_timeout_seconds=timeout,
        fund_holdings_context_fast_timeout_seconds=timeout,
        fund_holdings_refresh_retry_ttl_seconds=900,
    )


def _resolution(snapshot: dict, *, source: str = "append_only_store") -> dict:
    annotated = deepcopy(snapshot)
    audit = dict(annotated.get("audit") or {})
    existing_repository = audit.get("snapshot_repository")
    existing_repository = (
        existing_repository if isinstance(existing_repository, dict) else {}
    )
    audit["snapshot_repository"] = {
        "source": source,
        "live_attempted": source == "live_resolver_saved",
        "persistence_failed": False,
        "first_observed_at": annotated.get("first_observed_at")
        or existing_repository.get("first_observed_at")
        or "2026-07-20T09:05:00+08:00",
    }
    annotated["audit"] = audit
    return {
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "decision_at": DECISION.isoformat(),
        "source": source,
        "snapshot": annotated,
        "record": None,
    }


def test_daily_portfolio_only_is_a_qualified_requested_capability() -> None:
    existing = _snapshot("000001")

    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=9999)],
        [],
        decision_at=DECISION,
        analysis_mode="fast",
        portfolio_context=_portfolio_context(),
        resolver=lambda code, **_kwargs: _resolution(existing),
        settings=_settings(),
    )

    assert result["scope"] == "portfolio_only"
    assert result["status"] == "qualified"
    assert result["capabilities"]["candidate_overlap"]["status"] == "not_requested"
    assert result["portfolio"]["fund_holding_amount_yuan"] == 1000
    assert result["portfolio"]["whole_account_denominator_yuan"] == 1200
    assert result["resolution_audit"]["raw_holdings_included"] is False


def test_whole_account_denominator_requires_complete_position_shares() -> None:
    existing = _snapshot("000001")
    context = _portfolio_context()
    context["position_snapshot"]["positions"][0].pop("settled_shares")

    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=9999)],
        [],
        decision_at=DECISION,
        analysis_mode="fast",
        portfolio_context=context,
        resolver=lambda code, **_kwargs: _resolution(existing),
        settings=_settings(),
    )

    portfolio = result["portfolio"]
    audit = result["resolution_audit"]["portfolio_input"]
    assert portfolio["fund_holding_amount_yuan"] == 1000
    assert portfolio["whole_account_denominator_yuan"] is None
    assert portfolio["whole_account_denominator_qualified"] is False
    assert audit["position_complete"] is False
    assert "settled_shares_missing:000001" in audit["reason_codes"]


def test_batch_timeout_is_partial_and_never_hides_missing_candidate() -> None:
    existing = _snapshot("000001")
    candidate = _snapshot("000002", security="600002")

    def resolver(code: str, **_kwargs) -> dict:
        if code == "000002":
            time.sleep(0.2)
            return _resolution(candidate)
        return _resolution(existing)

    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=1000)],
        [{"fund_code": "000002"}],
        decision_at=DECISION,
        analysis_mode="fast",
        portfolio_context=_portfolio_context(),
        resolver=resolver,
        settings=_settings(timeout=0.03),
    )

    assert result["scope"] == "portfolio_and_candidates"
    assert result["status"] == "partial"
    assert result["research_qualified"] is False
    assert result["capabilities"]["candidate_overlap"]["status"] == "partial"
    assert "candidate_snapshot_resolution_incomplete" in result["reason_codes"]
    assert result["resolution_audit"]["timed_out_count"] == 1


def test_partial_resolution_preserves_resolved_candidate_one_way_risk_guard() -> None:
    existing = _snapshot("000001")
    resolved_candidate = _snapshot("000002")
    missing_candidate = _snapshot("000003", security="600003")
    for snapshot in (existing, resolved_candidate, missing_candidate):
        audit = snapshot.setdefault("audit", {})
        audit["snapshot_repository"] = {
            "source": "append_only_store",
            "live_attempted": False,
            "persistence_failed": False,
            "first_observed_at": "2026-07-20T10:00:00+08:00",
        }

    def resolver(code: str, **_kwargs) -> dict:
        if code == "000003":
            time.sleep(0.2)
            return _resolution(missing_candidate)
        return _resolution(existing if code == "000001" else resolved_candidate)

    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=1000)],
        [{"fund_code": "000002"}, {"fund_code": "000003"}],
        decision_at=DECISION,
        analysis_mode="fast",
        portfolio_context=_portfolio_context(),
        resolver=resolver,
        settings=_settings(timeout=0.03),
    )

    by_code = {row["fund_code"]: row for row in result["candidates"]}
    resolved = by_code["000002"]
    assert result["status"] == "partial"
    assert result["decision_use"]["concentration_risk_guard_eligible"] is True
    assert result["decision_use"]["allocation_authorization_eligible"] is False
    assert resolved["overlap_evidence_state"] == (
        "positive_same_vintage_reported_overlap"
    )
    assert resolved["reported_as_of_disclosed_overlap_percent"] > 0
    assert resolved["decision_use"]["concentration_risk_guard_eligible"] is True
    assert resolved["decision_use"]["allocation_authorization_eligible"] is False


def test_compact_payload_never_contains_raw_snapshots_or_holdings() -> None:
    snapshot = _snapshot("000001")
    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=1000)],
        [],
        decision_at=DECISION,
        analysis_mode="fast",
        portfolio_context=_portfolio_context(),
        resolver=lambda code, **_kwargs: _resolution(snapshot),
        settings=_settings(),
    )

    compact = compact_fund_lookthrough_for_llm(result)
    serialized = str(compact)
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert compact["raw_holdings_included"] is False
    assert "raw_snapshots" not in serialized
    assert {"holdings", "snapshot", "source_refs", "payload"}.isdisjoint(keys(compact))


def test_repository_aging_refresh_failure_falls_back_and_is_throttled(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    stored = {
        "fund_code": "000001",
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "snapshot_hash": "a" * 64,
        "freshness": {"label": "aging"},
    }
    record = {
        "payload": stored,
        "first_observed_at": (now - timedelta(days=30)).isoformat(),
    }
    calls = 0

    monkeypatch.setattr(repository.database, "get_latest_fund_holdings_snapshot", lambda **_kwargs: record)
    monkeypatch.setattr(repository, "materialize_fund_holdings_snapshot_for_decision", lambda value, **_kwargs: deepcopy(value))

    def live(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "fund_code": "000001",
            "status": "unavailable",
            "qualified": False,
            "reason_codes": ["provider_timeout"],
            "freshness": {"label": "unavailable"},
        }

    monkeypatch.setattr(repository, "resolve_fund_holdings_snapshot", live)
    first = resolve_fund_holdings_snapshot_at_decision(
        "000001", decision_at=now, refresh_retry_ttl_seconds=600
    )
    second = resolve_fund_holdings_snapshot_at_decision(
        "000001", decision_at=now, refresh_retry_ttl_seconds=600
    )

    assert calls == 1
    assert first["source"] == "append_only_store_fallback"
    assert first["refresh"]["live_attempted"] is True
    assert second["source"] == "append_only_store_refresh_throttled"
    assert second["refresh"]["throttled"] is True


def test_repository_fresh_old_record_gets_scheduled_disclosure_recheck(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    stored = {
        "fund_code": "000001",
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "snapshot_hash": "a" * 64,
        "freshness": {"label": "fresh"},
    }
    refreshed = {
        **stored,
        "snapshot_hash": "b" * 64,
    }
    old_record = {
        "payload": stored,
        "first_observed_at": (now - timedelta(hours=12)).isoformat(),
    }
    calls = 0

    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: deepcopy(old_record),
    )
    monkeypatch.setattr(
        repository,
        "materialize_fund_holdings_snapshot_for_decision",
        lambda value, **_kwargs: deepcopy(value),
    )

    def live(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return deepcopy(refreshed)

    monkeypatch.setattr(repository, "resolve_fund_holdings_snapshot", live)
    monkeypatch.setattr(
        repository.database,
        "save_fund_holdings_snapshot",
        lambda value: {
            "inserted": True,
            "record": {
                "payload": deepcopy(value),
                "first_observed_at": now.isoformat(),
            },
        },
    )

    result = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=now,
        refresh_check_ttl_seconds=3600,
        refresh_retry_ttl_seconds=600,
    )

    assert calls == 1
    assert result["source"] == "live_resolver_saved"
    assert result["snapshot"]["snapshot_hash"] == "b" * 64
    assert result["refresh"]["reason"] == "scheduled_disclosure_recheck"


def test_repository_live_exception_is_explicit_stored_fallback(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    record = {
        "payload": {
            "fund_code": "000001",
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "snapshot_hash": "a" * 64,
            "freshness": {"label": "aging"},
        },
        "first_observed_at": (now - timedelta(days=30)).isoformat(),
    }
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: deepcopy(record),
    )
    monkeypatch.setattr(
        repository,
        "materialize_fund_holdings_snapshot_for_decision",
        lambda value, **_kwargs: deepcopy(value),
    )

    def fail(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(repository, "resolve_fund_holdings_snapshot", fail)

    result = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=now,
        refresh_retry_ttl_seconds=600,
    )

    repository_audit = result["snapshot"]["audit"]["snapshot_repository"]
    assert result["source"] == "append_only_store_fallback"
    assert repository_audit["live_attempted"] is True
    assert repository_audit["live_failure_reason_codes"] == [
        "live_snapshot_resolution_error",
        "TimeoutError",
    ]


def test_repository_unpersisted_live_falls_back_without_marking_refresh_success(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    stored = {
        "fund_code": "000001",
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "snapshot_hash": "a" * 64,
        "freshness": {"label": "fresh"},
    }
    record = {
        "payload": stored,
        "first_observed_at": (now - timedelta(hours=12)).isoformat(),
    }
    live_snapshot = {**stored, "snapshot_hash": "b" * 64}
    calls = 0
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: deepcopy(record),
    )
    monkeypatch.setattr(
        repository,
        "materialize_fund_holdings_snapshot_for_decision",
        lambda value, **_kwargs: deepcopy(value),
    )

    def live(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return deepcopy(live_snapshot)

    monkeypatch.setattr(repository, "resolve_fund_holdings_snapshot", live)
    monkeypatch.setattr(
        repository.database,
        "save_fund_holdings_snapshot",
        lambda _value: {"inserted": False, "record": None},
    )

    first = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=now,
        refresh_check_ttl_seconds=3600,
        refresh_retry_ttl_seconds=0,
    )
    second = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=now,
        refresh_check_ttl_seconds=3600,
        refresh_retry_ttl_seconds=0,
    )

    assert calls == 2
    assert first["source"] == second["source"] == "append_only_store_fallback"
    assert first["snapshot"]["audit"]["snapshot_repository"]["persistence_failed"] is True
    assert "live_snapshot_persistence_failed" in first["reason_codes"]


def test_repository_unpersisted_store_miss_is_audit_only_unavailable(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "materialize_fund_holdings_snapshot_for_decision",
        lambda value, **_kwargs: deepcopy(value),
    )
    monkeypatch.setattr(
        repository,
        "resolve_fund_holdings_snapshot",
        lambda *_args, **_kwargs: {
            "fund_code": "000001",
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "snapshot_hash": "b" * 64,
            "freshness": {"label": "fresh"},
        },
    )
    monkeypatch.setattr(
        repository.database,
        "save_fund_holdings_snapshot",
        lambda _value: {"inserted": False, "record": None},
    )

    result = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=now,
        refresh_retry_ttl_seconds=0,
    )

    assert result["status"] == "unavailable"
    assert result["qualified"] is False
    assert result["snapshot"] is None
    assert result["source"] == "live_resolver_unpersisted_audit_only"
    assert result["reason_codes"] == ["live_snapshot_persistence_failed"]
    assert result["refresh"]["persistence_failed"] is True


def test_repository_materialization_error_is_explicit_and_non_blocking(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    decision = datetime(2025, 7, 1, 12, 0, tzinfo=CN)
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: {
            "payload": {
                "fund_code": "000001",
                "status": "qualified",
                "qualified": True,
            },
            "first_observed_at": "2025-06-30T09:00:00+08:00",
        },
    )
    monkeypatch.setattr(
        repository,
        "materialize_fund_holdings_snapshot_for_decision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("malformed")),
    )
    monkeypatch.setattr(
        repository,
        "resolve_fund_holdings_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("historical materialization failure must remain store-only")
        ),
    )

    result = resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=decision,
    )

    assert result["status"] == "unavailable"
    assert result["qualified"] is False
    assert result["reason_codes"] == ["stored_snapshot_materialization_failed"]


def test_store_scan_keeps_later_stored_candidate_when_live_refresh_times_out() -> None:
    existing = _snapshot("000001")
    later_stored = _snapshot("000003", security="600003")
    release_live_refresh = threading.Event()

    def resolver(code: str, *, allow_live: bool, **_kwargs) -> dict:
        if not allow_live:
            if code == "000001":
                return _resolution(existing)
            if code == "000003":
                return _resolution(later_stored)
            return {
                "status": "unavailable",
                "qualified": False,
                "reason_codes": ["store_only_snapshot_missing"],
                "source": "append_only_store",
                "snapshot": None,
            }
        release_live_refresh.wait()
        return {
            "status": "unavailable",
            "qualified": False,
            "reason_codes": ["provider_timeout"],
            "source": "none",
            "snapshot": None,
        }

    settings = _settings(timeout=0.08)
    settings.fund_holdings_context_fast_timeout_seconds = 0.03
    settings.fund_holdings_context_workers = 1
    settings.fund_holdings_context_live_max_funds = 1
    try:
        result = build_fund_lookthrough_context(
            [SimpleNamespace(fund_code="000001", holding_amount=1000)],
            [{"fund_code": "000002"}, {"fund_code": "000003"}],
            decision_at=DECISION,
            analysis_mode="deep",
            portfolio_context=_portfolio_context(),
            resolver=resolver,
            settings=settings,
        )
    finally:
        release_live_refresh.set()

    rows = {
        row["fund_code"]: row for row in result["resolution_audit"]["rows"]
    }
    assert rows["000002"]["status"] == "timeout"
    assert rows["000003"]["status"] == "qualified"
    assert rows["000003"]["resolution_phase"] == "store"
    assert rows["000003"]["snapshot_ref"] is not None


def test_persisted_live_snapshot_is_hash_bound_to_current_run_research() -> None:
    live_snapshot = _snapshot("000001")
    live_snapshot["first_observed_at"] = (DECISION + timedelta(seconds=1)).isoformat()
    audit = live_snapshot.setdefault("audit", {})
    audit["snapshot_repository"] = {
        "source": "live_resolver_saved",
        "live_attempted": True,
        "persistence_failed": False,
        "first_observed_at": (DECISION + timedelta(seconds=1)).isoformat(),
    }

    def resolver(_code: str, *, allow_live: bool, **_kwargs) -> dict:
        if not allow_live:
            return {
                "status": "unavailable",
                "qualified": False,
                "reason_codes": ["store_only_snapshot_missing"],
                "source": "append_only_store",
                "snapshot": None,
            }
        return _resolution(live_snapshot, source="live_resolver_saved")

    result = build_fund_lookthrough_context(
        [SimpleNamespace(fund_code="000001", holding_amount=1000)],
        [],
        decision_at=DECISION,
        analysis_mode="deep",
        portfolio_context=_portfolio_context(),
        resolver=resolver,
        settings=_settings(),
    )

    snapshot = result["existing_funds"][0]["snapshot"]
    assert snapshot["observation_status"] == "current_live_same_run"
    assert snapshot["replay_eligible"] is False
    assert result["decision_use"]["allocation_authorization_eligible"] is False


def test_repository_singleflight_store_miss_calls_provider_once(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    clear_fund_holdings_snapshot_refresh_state()
    now = datetime.now(CN)
    state: dict[str, dict | None] = {"record": None}
    provider_started = threading.Event()
    release_provider = threading.Event()
    calls = 0
    live_snapshot = {
        "fund_code": "000001",
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "snapshot_hash": "b" * 64,
        "freshness": {"label": "fresh"},
    }

    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: deepcopy(state["record"]),
    )
    monkeypatch.setattr(repository, "materialize_fund_holdings_snapshot_for_decision", lambda value, **_kwargs: deepcopy(value))

    def live(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        provider_started.set()
        release_provider.wait(timeout=2)
        return deepcopy(live_snapshot)

    def save(value):
        state["record"] = {
            "payload": deepcopy(value),
            "first_observed_at": now.isoformat(),
        }
        return {"inserted": True, "record": deepcopy(state["record"])}

    monkeypatch.setattr(repository, "resolve_fund_holdings_snapshot", live)
    monkeypatch.setattr(repository.database, "save_fund_holdings_snapshot", save)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            resolve_fund_holdings_snapshot_at_decision,
            "000001",
            decision_at=now,
            refresh_retry_ttl_seconds=600,
        )
        assert provider_started.wait(timeout=1)
        second_future = executor.submit(
            resolve_fund_holdings_snapshot_at_decision,
            "000001",
            decision_at=now,
            refresh_retry_ttl_seconds=600,
        )
        release_provider.set()
        first = first_future.result(timeout=2)
        second = second_future.result(timeout=2)

    assert calls == 1
    assert first["snapshot"]["snapshot_hash"] == "b" * 64
    assert second["snapshot"]["snapshot_hash"] == "b" * 64
