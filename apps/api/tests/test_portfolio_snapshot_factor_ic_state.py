from __future__ import annotations

from app.models import Holding
from app.services import factor_confidence as fc
from app.services import portfolio_snapshot as ps


def _holding(fund_code: str = "000001") -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name="测试基金",
        holding_amount=1000.0,
    )


def _factor_payload() -> dict:
    return {
        "available": True,
        "universe_size": 30,
        "funds": [
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "composite_grade": "B",
                "composite_score": 60.0,
                "factors": {
                    "momentum": {"percentile": 75.0},
                    "risk_adjusted": {"percentile": 65.0},
                    "drawdown": {"percentile": 55.0},
                    "size": {"percentile": 45.0},
                },
            }
        ],
    }


def _build_with_context(monkeypatch, context: dict) -> tuple[dict, int]:
    calls = 0

    def fake_load_ic_context() -> dict:
        nonlocal calls
        calls += 1
        return context

    monkeypatch.setattr(fc, "load_ic_context", fake_load_ic_context)
    monkeypatch.setattr(
        ps,
        "build_factor_scores_payload",
        lambda *_args, **_kwargs: _factor_payload(),
    )
    ps.clear_factor_facts_cache()
    result = ps.build_factor_scores_for_facts([_holding()])
    return result, calls


def test_unavailable_ic_state_is_persisted_and_uses_not_connected_basis(monkeypatch) -> None:
    result, calls = _build_with_context(
        monkeypatch,
        {
            "state": "unavailable",
            "status": {"available": False, "source": "unavailable"},
            "factors": {},
        },
    )

    assert calls == 1
    assert result["ic_status"] == {
        "available": False,
        "source": "unavailable",
        "state": "unavailable",
    }
    assert result["factor_reliability"]["momentum"]["basis"] == "IC 回测未接入"
    assert result["factor_reliability"]["risk_adjusted"]["basis"] == "IC 回测未接入"
    assert result["factor_reliability"]["size"]["basis"] == "规模因子未回测，仅供参考"


def test_stale_ic_state_is_persisted_and_excluded_from_reliability(monkeypatch) -> None:
    result, calls = _build_with_context(
        monkeypatch,
        {
            "state": "stale",
            "status": {
                "available": True,
                "stale": True,
                "source": "local_file",
                "age_days": 31,
            },
            "factors": {},
        },
    )

    assert calls == 1
    assert result["ic_status"]["state"] == "stale"
    assert result["ic_status"]["age_days"] == 31
    assert result["factor_reliability"]["drawdown"]["basis"] == "IC 回测已过期，暂不参与"
    assert result["factor_reliability"]["size"]["basis"] == "规模因子未回测，仅供参考"


def test_available_ic_state_uses_rows_and_missing_factor_basis(monkeypatch) -> None:
    result, calls = _build_with_context(
        monkeypatch,
        {
            "state": "available",
            "status": {
                "available": True,
                "stale": False,
                "source": "database",
                "run_date": "2026-07-11",
            },
            "factors": {
                "momentum": {
                    "mean_ic": 0.04,
                    "significant": True,
                }
            },
        },
    )

    assert calls == 1
    assert result["ic_status"]["state"] == "available"
    assert result["ic_status"]["run_date"] == "2026-07-11"
    assert result["factor_reliability"]["momentum"]["level"] == "高"
    assert result["factor_reliability"]["risk_adjusted"]["basis"] == "无回测数据"
    assert result["factor_reliability"]["size"]["basis"] == "规模因子未回测，仅供参考"


def test_cached_payload_recomposes_available_then_stale_ic_context(monkeypatch) -> None:
    payload_calls = 0
    contexts = iter(
        [
            {
                "state": "available",
                "status": {"available": True, "stale": False, "source": "database"},
                "factors": {
                    "momentum": {"mean_ic": 0.04, "significant": True},
                },
            },
            {
                "state": "stale",
                "status": {
                    "available": True,
                    "stale": True,
                    "source": "database",
                    "age_days": 30,
                },
                "factors": {},
            },
        ]
    )

    def fake_payload(*_args, **_kwargs) -> dict:
        nonlocal payload_calls
        payload_calls += 1
        return _factor_payload()

    monkeypatch.setattr(ps, "build_factor_scores_payload", fake_payload)
    monkeypatch.setattr(fc, "load_ic_context", lambda: next(contexts))
    ps.clear_factor_facts_cache()

    available = ps.build_factor_scores_for_facts([_holding()])
    stale = ps.build_factor_scores_for_facts([_holding()])

    assert payload_calls == 1
    assert available["ic_status"]["state"] == "available"
    assert available["factor_reliability"]["momentum"]["level"] == "高"
    assert stale["ic_status"]["state"] == "stale"
    assert stale["factor_reliability"]["momentum"]["basis"] == "IC 回测已过期，暂不参与"


def test_injected_ic_factors_bypass_context_and_preserve_status_contract(monkeypatch) -> None:
    def unexpected_context_load() -> dict:
        raise AssertionError("explicit ic_factors must bypass the shared IC context")

    monkeypatch.setattr(fc, "load_ic_context", unexpected_context_load)
    monkeypatch.setattr(
        ps,
        "build_factor_scores_payload",
        lambda *_args, **_kwargs: _factor_payload(),
    )
    ps.clear_factor_facts_cache()

    available = ps.build_factor_scores_for_facts(
        [_holding()],
        ic_factors={"momentum": {"mean_ic": 0.04, "significant": True}},
    )
    unavailable = ps.build_factor_scores_for_facts([_holding()], ic_factors={})

    assert available["ic_status"] == {
        "available": True,
        "source": "injected",
        "state": "available",
    }
    assert unavailable["ic_status"] == {
        "available": False,
        "source": "injected",
        "state": "unavailable",
    }
    assert unavailable["factor_reliability"]["momentum"]["basis"] == "IC 回测未接入"


def test_clear_factor_facts_cache_forces_fresh_payload_and_ic_context(monkeypatch) -> None:
    payload_calls = 0
    context_calls = 0

    def fake_payload(*_args, **_kwargs) -> dict:
        nonlocal payload_calls
        payload_calls += 1
        return _factor_payload()

    def fake_context() -> dict:
        nonlocal context_calls
        context_calls += 1
        return {
            "state": "available",
            "status": {"available": True, "stale": False, "source": "database"},
            "factors": {},
        }

    monkeypatch.setattr(ps, "build_factor_scores_payload", fake_payload)
    monkeypatch.setattr(fc, "load_ic_context", fake_context)
    ps.clear_factor_facts_cache()

    first = ps.build_factor_scores_for_facts([_holding()])
    cached = ps.build_factor_scores_for_facts([_holding()])
    ps.clear_factor_facts_cache()
    refreshed = ps.build_factor_scores_for_facts([_holding()])

    assert cached is not first
    assert refreshed is not first
    assert payload_calls == 2
    assert context_calls == 3


def test_clear_during_uncached_build_prevents_stale_cache_repopulation(monkeypatch) -> None:
    payload_calls = 0

    def payload_that_clears_once(*_args, **_kwargs) -> dict:
        nonlocal payload_calls
        payload_calls += 1
        if payload_calls == 1:
            ps.clear_factor_facts_cache()
        return _factor_payload()

    monkeypatch.setattr(ps, "build_factor_scores_payload", payload_that_clears_once)
    monkeypatch.setattr(
        fc,
        "load_ic_context",
        lambda: {
            "state": "available",
            "status": {"available": True, "stale": False, "source": "database"},
            "factors": {},
        },
    )
    ps.clear_factor_facts_cache()

    ps.build_factor_scores_for_facts([_holding()])
    ps.build_factor_scores_for_facts([_holding()])

    assert payload_calls == 2


def test_generation_change_during_context_load_reloads_before_return(monkeypatch) -> None:
    context_calls = 0

    def context_that_clears_once() -> dict:
        nonlocal context_calls
        context_calls += 1
        if context_calls == 1:
            ps.clear_factor_facts_cache()
            return {
                "state": "stale",
                "status": {
                    "available": True,
                    "stale": True,
                    "source": "database",
                    "age_days": 30,
                },
                "factors": {},
            }
        return {
            "state": "available",
            "status": {"available": True, "stale": False, "source": "database"},
            "factors": {
                "momentum": {"mean_ic": 0.04, "significant": True},
            },
        }

    monkeypatch.setattr(
        ps,
        "build_factor_scores_payload",
        lambda *_args, **_kwargs: _factor_payload(),
    )
    monkeypatch.setattr(fc, "load_ic_context", context_that_clears_once)
    ps.clear_factor_facts_cache()

    result = ps.build_factor_scores_for_facts([_holding()])

    assert context_calls == 2
    assert result["ic_status"]["state"] == "available"
    assert result["factor_reliability"]["momentum"]["level"] == "高"


def test_factor_facts_cache_is_lru_bounded(monkeypatch) -> None:
    payload_calls: list[str] = []

    def fake_payload(holdings, **_kwargs) -> dict:
        payload_calls.append(holdings[0].fund_code)
        return _factor_payload()

    monkeypatch.setattr(ps, "_FACTOR_FACTS_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(ps.time, "time", lambda: 100.0)
    monkeypatch.setattr(ps, "build_factor_scores_payload", fake_payload)
    monkeypatch.setattr(
        fc,
        "load_ic_context",
        lambda: {
            "state": "unavailable",
            "status": {"available": False},
            "factors": {},
        },
    )
    ps.clear_factor_facts_cache()

    ps.build_factor_scores_for_facts([_holding("000001")])
    ps.build_factor_scores_for_facts([_holding("000002")])
    ps.build_factor_scores_for_facts([_holding("000001")])
    ps.build_factor_scores_for_facts([_holding("000003")])

    assert payload_calls == ["000001", "000002", "000003"]
    assert list(ps._FACTOR_FACTS_CACHE) == ["000001", "000003"]


def test_factor_facts_cache_prunes_expired_entries(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(ps.time, "time", lambda: now[0])
    monkeypatch.setattr(
        ps,
        "build_factor_scores_payload",
        lambda *_args, **_kwargs: _factor_payload(),
    )
    monkeypatch.setattr(
        fc,
        "load_ic_context",
        lambda: {
            "state": "unavailable",
            "status": {"available": False},
            "factors": {},
        },
    )
    ps.clear_factor_facts_cache()

    ps.build_factor_scores_for_facts([_holding("000001")])
    now[0] += ps._FACTOR_FACTS_TTL_SECONDS + 1
    ps.build_factor_scores_for_facts([_holding("000002")])

    assert list(ps._FACTOR_FACTS_CACHE) == ["000002"]
