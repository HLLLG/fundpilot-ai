from __future__ import annotations

from app.services.discovery_sector_context import (
    build_candidate_factor_scores,
    execution_qualified_fund_codes,
)
from app.services.factor_ic_research import EXECUTION_QUALIFICATION_METHOD


def _candidate(code: str, score: float) -> dict:
    return {
        "fund_code": code,
        "fund_name": f"fund-{code}",
        "fund_quality_score": score,
        "sector_fit_score": score,
        "quality_gate": {"status": "eligible"},
    }


def test_candidate_whitelist_separates_description_from_execution(monkeypatch) -> None:
    def fake_factor_scores(_holdings):
        return {
            "available": True,
            "ic_status": {
                "state": "available",
                "available": True,
                "stale": False,
            },
            "holdings": [
                {
                    "fund_code": "000001",
                    "applicable": True,
                    "descriptive_applicable": True,
                    "execution_qualified": False,
                    "execution_qualified_factor_keys": [],
                    "execution_qualification": {
                        "status": "insufficient",
                        "method": EXECUTION_QUALIFICATION_METHOD,
                        "reason": (
                            "no_statistically_and_economically_qualified_factor"
                        ),
                    },
                },
                {
                    "fund_code": "000002",
                    "applicable": True,
                    "descriptive_applicable": True,
                    "execution_qualified": True,
                    "execution_qualified_factor_keys": ["momentum"],
                    "execution_qualification": {
                        "status": "qualified",
                        "method": EXECUTION_QUALIFICATION_METHOD,
                        "reason": None,
                    },
                },
            ],
        }

    monkeypatch.setattr(
        "app.services.portfolio_snapshot.build_factor_scores_for_facts",
        fake_factor_scores,
    )

    result = build_candidate_factor_scores(
        [_candidate("000001", 90), _candidate("000002", 80)]
    )

    assert result["descriptive_applicable_fund_codes"] == ["000001", "000002"]
    assert result["execution_qualified_fund_codes"] == ["000002"]
    # The old field is retained only so the current Guard consumes the strict set.
    assert result["applicable_fund_codes"] == ["000002"]
    assert result["applicable_fund_codes_semantics"] == (
        "legacy_alias_of_execution_qualified_fund_codes"
    )


def test_execution_code_helper_never_trusts_legacy_applicable_alias() -> None:
    payload = {
        "available": True,
        "ic_status": {"state": "available", "available": True, "stale": False},
        "applicable_fund_codes": ["000001"],
        "holdings": [
            {
                "fund_code": "000001",
                "applicable": True,
                "execution_qualified": False,
            }
        ],
    }

    assert execution_qualified_fund_codes(payload) == []
