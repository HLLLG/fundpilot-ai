from __future__ import annotations

from app.services.fund_vehicle_quality import assess_candidate_vehicle_quality


def test_vehicle_gate_does_not_inherit_core_profile_gate_failure() -> None:
    assessed = assess_candidate_vehicle_quality(
        {
            "fund_code": "021535",
            "fund_name": "天弘中证软件服务ETF发起联接A",
            "fund_type": "股票型",
            "sector_match_kind": "tracking_exact",
            "fund_scale_yi": 0.7,
            "management_fee": "0.40%",
            "quality_gate": {
                "status": "watch_only",
                "eligible": False,
                "reasons": ["最新估算规模低于1亿元"],
            },
        }
    )

    assert assessed["vehicle_quality_score"] >= assessed["vehicle_quality_threshold"]
    assert assessed["vehicle_quality_status"] == "eligible"
    assert assessed["quality_gate"]["status"] == "watch_only"
