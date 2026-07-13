from __future__ import annotations

from app.services.signal_synthesis import build_evidence_overview, build_holding_evidence


def _factor_scores(*, percentile: float = 88, level: str = "高", basis: str = "正向稳定") -> dict:
    return {
        "available": True,
        "ic_status": {"state": "available", "run_date": "2026-07-13"},
        "holdings": [
            {
                "fund_code": "000001",
                "applicable": True,
                "feature_completeness": 0.75,
                "factor_percentiles": {
                    "momentum": percentile,
                    "risk_adjusted": None,
                    "drawdown": None,
                },
                "factor_reliability": {
                    "momentum": {"level": level, "basis": basis},
                },
            }
        ],
    }


def _risk() -> dict:
    return {
        "available": True,
        "sample_days": 150,
        "max_drawdown_percent": -18,
        "hhi": 0.42,
        "confidence": {"level": "高", "basis": "150交易日样本"},
    }


def _typed_factor_scores(*, qualified: bool = True, percentile: float = 82) -> dict:
    payload = _factor_scores(percentile=60, level="中")
    row = payload["holdings"][0]
    row.update(
        {
            "typed_factor_applicable": True,
            "typed_feature_completeness": 1.0,
            "typed_factor_percentiles": {"downside_resilience": percentile},
            "typed_factor_reliability": {
                "downside_resilience": {
                    "level": "高",
                    "basis": "PIT净成本经济门槛通过",
                    "qualified": qualified,
                    "orientation": "higher_is_better",
                    "economic_significance": {"qualified": qualified},
                }
            },
        }
    )
    return payload


def _negative_signal(*, current: bool = True) -> dict:
    result = {
        "sample_days": 120,
        "freshness_status": "fresh",
        "as_of": "2026-07-13",
        "by_rule": {
            "sector_weak": {
                "label": "板块弱势",
                "trigger_count": 50,
                "edge_percent": 12,
                "confidence": {"level": "高", "score": 90, "basis": "显著负向"},
            }
        },
    }
    if current:
        result["current_signal"] = {
            "active": True,
            "rule_id": "sector_weak",
            "direction": "negative",
        }
    return result


def test_risk_is_a_guard_and_never_raises_positive_support() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=_factor_scores(),
        risk_metrics=_risk(),
    )

    assert evidence is not None
    assert evidence["schema_version"] == "quant_evidence.v2"
    assert evidence["composite"]["level"] == "高"
    assert evidence["composite"]["direction"] == "positive"
    assert evidence["composite"]["risk_guard_count"] == 1
    risk = next(item for item in evidence["components"] if item["source"] == "risk")
    assert risk["role"] == "risk_guard"
    assert risk["direction"] == "risk"
    for field in ("reliability", "direction", "effect_size", "coverage", "freshness"):
        assert field in risk


def test_risk_only_evidence_is_not_legacy_positive_backing() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=None,
        risk_metrics=_risk(),
    )

    assert evidence is not None
    assert evidence["composite"]["level"] == "不足"
    assert evidence["composite"]["score"] == 0
    assert evidence["composite"]["risk_guard_count"] == 1


def test_high_reliability_negative_signal_preserves_direction_without_becoming_support() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=_negative_signal(),
        factor_scores=None,
        risk_metrics=None,
    )

    assert evidence is not None
    signal = evidence["components"][0]
    assert signal["reliability"]["level"] == "高"
    assert signal["direction"] == "negative"
    assert evidence["composite"]["reliability"]["level"] == "高"
    assert evidence["composite"]["direction"] == "negative"
    assert evidence["composite"]["level"] == "不足"


def test_historical_rule_stats_do_not_invent_a_live_direction() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=_negative_signal(current=False),
        factor_scores=None,
        risk_metrics=None,
    )

    assert evidence is not None
    signal = evidence["components"][0]
    assert signal["role"] == "historical_validation"
    assert signal["direction"] == "unknown"
    assert evidence["composite"]["reliability"]["level"] == "不足"
    assert evidence["composite"]["direction"] == "unknown"
    assert evidence["composite"]["level"] == "不足"


def test_reverse_ic_inverts_factor_percentile_direction() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=_factor_scores(basis="样本外反向/均值回归"),
        risk_metrics=None,
    )

    assert evidence is not None
    assert evidence["components"][0]["direction"] == "negative"
    assert evidence["composite"]["level"] == "不足"


def test_qualified_type_factor_is_used_as_online_return_signal() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=_typed_factor_scores(),
        risk_metrics=None,
    )

    assert evidence is not None
    component = evidence["components"][0]
    assert component["factor_family"] == "fund_type_specific"
    assert component["factor_key"] == "downside_resilience"
    assert component["direction"] == "positive"
    assert "下行韧性" in component["basis"]


def test_unqualified_type_factor_is_ignored_and_common_factor_remains() -> None:
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=_typed_factor_scores(qualified=False, percentile=99),
        risk_metrics=None,
    )

    assert evidence is not None
    component = evidence["components"][0]
    assert component["factor_family"] == "common"
    assert component["factor_key"] == "momentum"


def test_stale_or_unavailable_ic_never_becomes_return_support() -> None:
    for state in ("stale", "unavailable"):
        scores = _factor_scores()
        scores["ic_status"] = {
            "state": state,
            "available": state == "stale",
            "stale": state == "stale",
            "run_date": "2026-06-01",
        }
        evidence = build_holding_evidence(
            fund_code="000001",
            signal_entry=None,
            factor_scores=scores,
            risk_metrics=_risk(),
        )

        assert evidence is not None
        assert all(
            component["source"] != "factor"
            for component in evidence["components"]
        )
        assert evidence["composite"]["level"] == "不足"
        assert evidence["composite"]["direction"] == "unknown"


def test_unknown_freshness_return_signal_does_not_count_as_backing() -> None:
    signal = _negative_signal()
    signal.pop("freshness_status")
    signal.pop("as_of")
    evidence = build_holding_evidence(
        fund_code="000001",
        signal_entry=signal,
        factor_scores=None,
        risk_metrics=None,
    )

    assert evidence is not None
    assert evidence["components"][0]["role"] == "return_signal"
    assert evidence["composite"]["level"] == "不足"
    assert evidence["composite"]["direction"] == "unknown"
    assert evidence["composite"]["stale_or_unknown_return_count"] == 1


def test_overview_counts_only_positive_support_as_backed_weight() -> None:
    positive = build_holding_evidence(
        fund_code="000001",
        signal_entry=None,
        factor_scores=_factor_scores(),
        risk_metrics=_risk(),
    )
    negative = build_holding_evidence(
        fund_code="000002",
        signal_entry=_negative_signal(),
        factor_scores=None,
        risk_metrics=None,
    )
    overview = build_evidence_overview(
        [
            {"holding_amount": 60, "evidence": positive},
            {"holding_amount": 40, "evidence": negative},
        ]
    )

    assert overview["backed_weight_percent"] == 60.0
    assert overview["direction_counts"]["negative"] == 1
    assert overview["risk_guard_weight_percent"] == 60.0
