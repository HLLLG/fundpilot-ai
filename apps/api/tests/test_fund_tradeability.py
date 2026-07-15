from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_guard import apply_discovery_guards
from app.services.fund_tradeability import (
    apply_tradeability_to_quality_gate,
    assess_tradeability_for_amount,
    build_tradeability_profile,
    normalize_purchase_fee_tiers,
    normalize_purchase_limit,
    normalize_purchase_state,
    normalize_redemption_fee_tiers,
    parse_explicit_minimum_holding_days,
    parse_hold_horizon_min_days,
    resolve_profile_min_holding_days,
    resolve_purchase_fee,
    resolve_redemption_fee_percent,
)

CN_TZ = ZoneInfo("Asia/Shanghai")
DECISION_AT = datetime(2026, 7, 14, 10, 0, tzinfo=CN_TZ)


def _bulk(**overrides):
    row = {
        "fund_name": "测试基金A",
        "fund_type": "混合型-偏股",
        "purchase_status": "开放申购",
        "redemption_status": "开放赎回",
        "minimum_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": 100_000_000_000.0,
        "listed_platform_purchase_fee_percent": 0.15,
    }
    row.update(overrides)
    return row


def _bulk_snapshot(retrieved_at: str = "2026-07-14T09:59:00+08:00"):
    return {
        "retrieved_at": retrieved_at,
        "source": "akshare.fund_purchase_em",
        "source_url": "https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
        "rows": {},
    }


def _detail(**section_overrides):
    sections = {
        "交易状态": {"申购状态": "开放申购", "赎回状态": "开放赎回"},
        "申购与赎回金额": {
            "申购起点": "10.00元",
            "首次购买": "10.00元",
            "追加购买": "1.00元",
            "日累计申购限额": "无限额",
        },
        "交易确认日": {"买入确认日": "T+1", "卖出确认日": "T+1"},
        "运作费用": {
            "管理费率": "1.20%（每年）",
            "托管费率": "0.20%（每年）",
            "销售服务费率": "0.00%（每年）",
        },
        "申购费率": [
            {
                "condition": "小于50万元",
                "standard_rate": "1.50%",
                "platform_rate": "0.15%",
            },
            {
                "condition": "大于等于50万元，小于500万元",
                "standard_rate": "0.80%",
                "platform_rate": "0.08%",
            },
            {
                "condition": "大于等于500万元",
                "standard_rate": "每笔1000元",
                "platform_rate": None,
            },
        ],
        "赎回费率": [
            {"condition": "小于7天", "rate": "1.50%"},
            {"condition": "大于等于7天，小于30天", "rate": "0.50%"},
            {"condition": "大于等于30天", "rate": "0.00%"},
        ],
    }
    sections.update(section_overrides)
    return {
        "retrieved_at": "2026-07-14T09:59:30+08:00",
        "source": "eastmoney.fundf10_purchase_info",
        "source_url": "https://fundf10.eastmoney.com/jjfl_000001.html",
        "sections": sections,
    }


def _profile(*, bulk=None, detail=None):
    return build_tradeability_profile(
        "000001",
        bulk=_bulk() if bulk is None else bulk,
        bulk_snapshot=_bulk_snapshot(),
        detail=_detail() if detail is None else detail,
        decision_at=DECISION_AT,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("开放申购", "open"),
        ("限制大额申购", "limited"),
        ("限大额", "limited"),
        ("暂停申购", "suspended"),
        ("认购期", "subscription_period"),
        ("封闭期", "closed"),
        ("场内交易", "exchange_only"),
        ("", "unknown"),
    ],
)
def test_purchase_state_mapping_is_explicit(raw: str, expected: str) -> None:
    assert normalize_purchase_state(raw) == expected


@pytest.mark.parametrize(
    ("name", "expected_days"),
    [
        ("测试基金一年持有期混合A", 365),
        ("测试基金18个月持有债券C", 540),
        ("测试基金7天持有期A", 7),
        ("普通开放式基金A", None),
    ],
)
def test_explicit_holding_period_is_derived_only_from_affirmative_name(
    name: str,
    expected_days: int | None,
) -> None:
    assert parse_explicit_minimum_holding_days(name) == expected_days


def test_tradeability_profile_keeps_standard_and_platform_rates_separate() -> None:
    profile = _profile()

    assert profile["data_status"] == "complete"
    assert profile["freshness"] == "fresh"
    assert profile["purchase_state"] == "open"
    assert profile["redemption_state"] == "open"
    assert profile["currency"] == "CNY"
    assert profile["minimum_purchase_yuan"] == 10.0
    assert profile["minimum_initial_purchase_yuan"] == 10.0
    assert profile["minimum_additional_purchase_yuan"] == 1.0
    assert profile["tradeability_gate"] == {
        "schema_version": "fund_tradeability_gate.v1",
        "status": "eligible",
        "effective_initial_min_purchase_yuan": 100.0,
        "effective_additional_min_purchase_yuan": 1.0,
        "effective_min_purchase_yuan": 100.0,
        "max_purchase_yuan": None,
        "max_purchase_unlimited": True,
        "max_period": "day",
        "max_scope": "eastmoney_channel_display_unknown_remaining",
        "revalidation_required": True,
        "reason_codes": [],
    }
    assert profile["daily_purchase_limit_yuan"] is None
    assert profile["daily_purchase_limit_unlimited"] is True
    assert profile["listed_platform_purchase_fee_percent"] == 0.15
    assert profile["listed_platform_fee_semantics"] == (
        "provider_listed_discount_not_standard_upper_bound"
    )
    assert profile["standard_purchase_fee_tiers"][0]["fee_percent"] == 1.5
    assert profile["standard_purchase_fee_tiers"][0]["source_rate"] == (
        "standard_undiscounted"
    )
    assert profile["share_class_fee_status"] == "standard_upper_bound_available"
    assert profile["sales_service_fee_annual_percent"] == 0.0
    assert profile["sales_service_fee_status"] == "known_zero"
    assert profile["fee_freshness"] == "fresh"


def test_fresh_status_does_not_mask_stale_fee_rules() -> None:
    detail = _detail()
    detail["retrieved_at"] = "2026-07-12T09:59:30+08:00"
    profile = _profile(detail=detail)

    long_horizon = assess_tradeability_for_amount(
        profile, amount_yuan=1000, hold_horizon="半年", minimum_holding_days=180
    )
    short_horizon = assess_tradeability_for_amount(
        profile, amount_yuan=1000, hold_horizon="14天", minimum_holding_days=14
    )

    assert profile["freshness"] == "fresh"
    assert profile["fee_freshness"] == "stale"
    assert profile["share_class_fee_status"] == "unverified"
    assert long_horizon["executable"] is False
    assert long_horizon["estimated_total_cost_upper_bound_percent"] is None
    assert "transaction_cost_incomplete" in long_horizon["block_reasons"]
    assert "short_horizon_cost_unverified" in short_horizon["block_reasons"]


def test_zero_minimum_is_unknown_not_free() -> None:
    detail = _detail(
        **{
            "申购与赎回金额": {
                "申购起点": "0元",
                "首次购买": "0元",
                "日累计申购限额": "1000元",
            }
        }
    )
    profile = _profile(
        bulk=_bulk(minimum_purchase_yuan=0.0, daily_purchase_limit_yuan=1000.0),
        detail=detail,
    )

    assert profile["minimum_purchase_yuan"] is None
    assert "minimum_purchase_yuan" in profile["missing_fields"]
    gated = apply_tradeability_to_quality_gate(
        {"quality_gate": {"status": "eligible", "reasons": []}, "tradeability": profile}
    )
    assert gated["quality_gate"]["status"] == "watch_only"


def test_conflicting_status_sources_fail_closed() -> None:
    profile = _profile(bulk=_bulk(purchase_status="暂停申购"))
    assert profile["source_conflict"] is True
    assert profile["purchase_state"] == "unknown"

    gated = apply_tradeability_to_quality_gate(
        {"quality_gate": {"status": "eligible", "reasons": []}, "tradeability": profile}
    )
    assert gated["quality_gate"]["status"] == "watch_only"
    assert any("冲突" in reason for reason in gated["quality_gate"]["reasons"])


def test_exchange_only_is_excluded_from_off_exchange_discovery() -> None:
    detail = _detail(**{"交易状态": {"申购状态": "场内交易", "赎回状态": "场内交易"}})
    profile = _profile(
        bulk=_bulk(purchase_status="场内交易", redemption_status="场内交易"),
        detail=detail,
    )
    gated = apply_tradeability_to_quality_gate(
        {"quality_gate": {"status": "eligible", "reasons": []}, "tradeability": profile}
    )
    assert gated["quality_gate"]["status"] == "excluded"


def test_suspended_redemption_is_watch_only() -> None:
    detail = _detail(**{"交易状态": {"申购状态": "开放申购", "赎回状态": "暂停赎回"}})
    profile = _profile(
        bulk=_bulk(redemption_status="暂停赎回"),
        detail=detail,
    )
    gated = apply_tradeability_to_quality_gate(
        {"quality_gate": {"status": "eligible", "reasons": []}, "tradeability": profile}
    )
    assert gated["quality_gate"]["status"] == "watch_only"
    assert any("赎回" in reason for reason in gated["quality_gate"]["reasons"])


def test_purchase_fee_tiers_resolve_percent_and_flat_fee() -> None:
    tiers = normalize_purchase_fee_tiers(_detail()["sections"]["申购费率"])

    small = resolve_purchase_fee(tiers, 10_000)
    large = resolve_purchase_fee(tiers, 5_000_000)

    assert small is not None
    assert small["fee_percent"] == 1.5
    assert small["fee_yuan"] == pytest.approx(147.78, abs=0.01)
    assert large == {
        "fee_type": "flat",
        "fee_percent": None,
        "flat_fee_yuan": 1000.0,
        "fee_yuan": 1000.0,
        "condition": "大于等于500万元",
        "source_rate": "standard_undiscounted",
    }


def test_redemption_fee_uses_conservative_minimum_horizon() -> None:
    tiers = normalize_redemption_fee_tiers(_detail()["sections"]["赎回费率"])
    assert resolve_redemption_fee_percent(tiers, 6) == 1.5
    assert resolve_redemption_fee_percent(tiers, 7) == 0.5
    assert resolve_redemption_fee_percent(tiers, 30) == 0.0


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("3-7天", 3),
        ("2-4周", 14),
        ("1-3个月", 30),
        ("半年到一年", 180),
        ("一年", 365),
        ("中长期", 180),
    ],
)
def test_horizon_parser_supports_product_labels(text: str, days: int) -> None:
    assert parse_hold_horizon_min_days(text) == days


def test_profile_horizon_not_llm_horizon_controls_cost_gate() -> None:
    conservative = InvestorProfile(
        horizon="半年到一年",
        investment_preset="conservative_hold",
        hold_days_target=7,
    )
    aggressive = InvestorProfile(
        horizon="半年到一年",
        investment_preset="aggressive_swing",
        hold_days_target=14,
    )
    assert resolve_profile_min_holding_days(conservative) == 180
    assert resolve_profile_min_holding_days(aggressive) == 14


def test_short_horizon_high_standard_cost_is_blocked() -> None:
    assessment = assess_tradeability_for_amount(
        _profile(),
        amount_yuan=10_000,
        hold_horizon="用户预设最短持有期 14 天",
        minimum_holding_days=14,
    )
    assert assessment["executable"] is False
    assert "short_horizon_cost_too_high" in assessment["block_reasons"]
    assert assessment["estimated_total_cost_upper_bound_percent"] > 1.0


def test_long_horizon_with_incomplete_fee_components_is_research_only() -> None:
    profile = _profile(detail={
        "retrieved_at": "2026-07-14T09:59:30+08:00",
        "source": "eastmoney.fundf10_purchase_info",
        "source_url": "https://fundf10.eastmoney.com/jjfl_000001.html",
        "sections": {
            "交易状态": {"申购状态": "开放申购", "赎回状态": "开放赎回"},
            "申购与赎回金额": {"申购起点": "10元", "日累计申购限额": "无限额"},
        },
    })
    assessment = assess_tradeability_for_amount(
        profile,
        amount_yuan=1000,
        hold_horizon="用户预设最短持有期 180 天",
        minimum_holding_days=180,
    )
    assert profile["sales_service_fee_status"] == "unknown"
    assert assessment["executable"] is False
    assert assessment["fee_status"] == "execution_verification_required"
    assert assessment["fee_components_complete"] is False
    assert assessment["estimated_total_cost_upper_bound_percent"] is None
    assert "transaction_cost_incomplete" in assessment["block_reasons"]
    assert "sales_service_fee_unknown" in assessment["block_reasons"]
    assert any("不能形成可执行总成本" in note for note in assessment["notes"])


def test_c_share_missing_sales_service_fee_never_assumes_zero() -> None:
    profile = _profile(
        bulk=_bulk(fund_name="测试基金C"),
        detail=_detail(
            **{
                "运作费用": {
                    "管理费率": "1.20%（每年）",
                    "托管费率": "0.20%（每年）",
                }
            }
        ),
    )

    assessment = assess_tradeability_for_amount(
        profile,
        amount_yuan=10_000,
        hold_horizon="半年",
        minimum_holding_days=180,
    )

    assert profile["sales_service_fee_annual_percent"] is None
    assert profile["sales_service_fee_status"] == "unknown"
    assert profile["share_class_fee_status"] == "unverified"
    assert "sales_service_fee_annual_percent" in profile["missing_fields"]
    assert assessment["sales_service_fee_status"] == "unknown"
    assert assessment["fee_component_status"]["sales_service_fee"] == "unknown"
    assert assessment["fee_components_complete"] is False
    assert assessment["cost_comparison_status"] == "incomplete"
    assert assessment["estimated_total_cost_upper_bound_percent"] is None
    assert assessment["executable"] is False
    assert "sales_service_fee_unknown" in assessment["block_reasons"]


def test_explicit_zero_and_positive_sales_service_fees_are_distinct() -> None:
    a_profile = _profile(bulk=_bulk(fund_name="测试基金A"))
    c_profile = _profile(
        bulk=_bulk(fund_name="测试基金C"),
        detail=_detail(
            **{
                "运作费用": {
                    "管理费率": "1.20%（每年）",
                    "托管费率": "0.20%（每年）",
                    "销售服务费率": "0.40%（每年）",
                }
            }
        ),
    )

    a_assessment = assess_tradeability_for_amount(
        a_profile, amount_yuan=10_000, hold_horizon="半年", minimum_holding_days=180
    )
    c_assessment = assess_tradeability_for_amount(
        c_profile, amount_yuan=10_000, hold_horizon="半年", minimum_holding_days=180
    )

    assert a_profile["sales_service_fee_status"] == "known_zero"
    assert c_profile["sales_service_fee_status"] == "known_positive"
    assert a_assessment["fee_components_complete"] is True
    assert c_assessment["fee_components_complete"] is True
    assert a_assessment["executable"] is True
    assert c_assessment["executable"] is True
    assert c_assessment["estimated_total_cost_upper_bound_percent"] > a_assessment[
        "estimated_total_cost_upper_bound_percent"
    ]


@pytest.mark.parametrize(
    ("annual_fee", "expected_status"),
    [(0.0, "known_zero"), (0.4, "known_positive")],
)
def test_legacy_payload_without_three_state_field_derives_explicit_numeric_fee(
    annual_fee: float,
    expected_status: str,
) -> None:
    profile = _profile()
    profile["sales_service_fee_annual_percent"] = annual_fee
    profile.pop("sales_service_fee_status")

    assessment = assess_tradeability_for_amount(
        profile, amount_yuan=10_000, hold_horizon="半年", minimum_holding_days=180
    )

    assert assessment["sales_service_fee_status"] == expected_status
    assert assessment["fee_components_complete"] is True
    assert assessment["cost_comparison_status"] == "complete"
    assert assessment["executable"] is True


@pytest.mark.parametrize("annual_fee", [None, -0.1, float("nan"), True])
def test_missing_or_invalid_legacy_sales_service_fee_is_unknown(
    annual_fee: object,
) -> None:
    profile = _profile()
    profile["sales_service_fee_annual_percent"] = annual_fee
    profile.pop("sales_service_fee_status")

    assessment = assess_tradeability_for_amount(
        profile, amount_yuan=10_000, hold_horizon="半年", minimum_holding_days=180
    )

    assert assessment["sales_service_fee_status"] == "unknown"
    assert assessment["fee_components_complete"] is False
    assert assessment["estimated_total_cost_upper_bound_percent"] is None
    assert assessment["executable"] is False
    assert "sales_service_fee_unknown" in assessment["block_reasons"]


def test_amount_must_meet_product_minimum_and_limit() -> None:
    detail = _detail(
        **{
            "申购与赎回金额": {
                "申购起点": "500元",
                "首次购买": "500元",
                "日累计申购限额": "1000元",
            }
        }
    )
    profile = _profile(
        bulk=_bulk(minimum_purchase_yuan=500, daily_purchase_limit_yuan=1000),
        detail=detail,
    )
    too_small = assess_tradeability_for_amount(
        profile, amount_yuan=499, hold_horizon="半年", minimum_holding_days=180
    )
    too_large = assess_tradeability_for_amount(
        profile, amount_yuan=1001, hold_horizon="半年", minimum_holding_days=180
    )
    assert "below_minimum_purchase" in too_small["block_reasons"]
    assert "above_daily_purchase_limit" in too_large["block_reasons"]


def test_system_initial_minimum_is_part_of_the_execution_gate() -> None:
    profile = _profile()

    too_small = assess_tradeability_for_amount(
        profile, amount_yuan=99, hold_horizon="半年", minimum_holding_days=180
    )
    executable = assess_tradeability_for_amount(
        profile, amount_yuan=100, hold_horizon="半年", minimum_holding_days=180
    )

    assert "below_minimum_purchase" in too_small["block_reasons"]
    assert executable["executable"] is True
    assert executable["minimum_purchase_yuan"] == 100.0


def test_limit_below_system_initial_minimum_is_watch_only() -> None:
    detail = _detail(
        **{
            "申购与赎回金额": {
                "申购起点": "10元",
                "首次购买": "10元",
                "追加购买": "1元",
                "日累计申购限额": "50元",
            }
        }
    )
    profile = _profile(
        bulk=_bulk(minimum_purchase_yuan=10, daily_purchase_limit_yuan=50),
        detail=detail,
    )
    gated = apply_tradeability_to_quality_gate(
        {"quality_gate": {"status": "eligible", "reasons": []}, "tradeability": profile}
    )

    assert profile["tradeability_gate"]["status"] == "watch_only"
    assert "limit_below_effective_initial_minimum" in profile["tradeability_gate"][
        "reason_codes"
    ]
    assert gated["quality_gate"]["status"] == "watch_only"


def test_explicit_fund_holding_period_blocks_shorter_profile_horizon() -> None:
    profile = _profile(bulk=_bulk(fund_name="测试基金一年持有期混合A"))

    assessment = assess_tradeability_for_amount(
        profile,
        amount_yuan=1000,
        hold_horizon="半年",
        minimum_holding_days=180,
    )

    assert profile["explicit_minimum_holding_days"] == 365
    assert assessment["executable"] is False
    assert "below_fund_minimum_holding_period" in assessment["block_reasons"]


def test_short_horizon_requires_minimum_holding_period_evidence_even_with_zero_fees() -> None:
    detail = _detail(
        **{
            "申购费率": [
                {"condition": "全部", "standard_rate": "0.00%", "platform_rate": "0.00%"}
            ],
            "赎回费率": [{"condition": "大于等于0天", "rate": "0.00%"}],
        }
    )
    unknown_lock = _profile(detail=detail)
    verified_lock = _profile(
        bulk=_bulk(fund_name="测试基金7天持有期混合A"),
        detail=detail,
    )

    unknown_assessment = assess_tradeability_for_amount(
        unknown_lock, amount_yuan=1000, hold_horizon="14天", minimum_holding_days=14
    )
    verified_assessment = assess_tradeability_for_amount(
        verified_lock, amount_yuan=1000, hold_horizon="14天", minimum_holding_days=14
    )

    assert "short_horizon_minimum_holding_period_unverified" in unknown_assessment[
        "block_reasons"
    ]
    assert verified_assessment["executable"] is True


def test_final_guard_overwrites_llm_tradeability_and_uses_profile_horizon() -> None:
    tradeability = _profile()
    candidate = {
        "fund_code": "000001",
        "fund_name": "测试基金A",
        "sector_label": "半导体",
        "quality_gate": {"status": "eligible", "reasons": []},
        "tradeability": tradeability,
        "fund_quality_score": 90,
        "sector_fit_score": 90,
    }
    recommendation = DiscoveryRecommendation(
        fund_code="000001",
        fund_name="测试基金A",
        sector_name="半导体",
        action="分批买入",
        suggested_amount_yuan=10_000,
        hold_horizon="3-7天",  # untrusted model text
        tradeability={"purchase_state": "open", "minimum_purchase_yuan": 0},
    )
    facts = {
        "portfolio_position_truth": {
            "cash": {"known": True, "balance_yuan": 20_000},
            "position_complete": True,
            "ledger_truncated": False,
            "pending_transaction_count": 0,
            "conflict_count": 0,
            "positions": [],
        },
        "portfolio_gap": {
            "holdings_slim": [],
            "weight_denominator_yuan": 100_000,
            "total_amount": 0,
        },
    }
    profile = InvestorProfile(
        horizon="半年到一年",
        investment_preset="conservative_hold",
        expected_investment_amount=100_000,
        concentration_limit_percent=35,
    )

    guarded, _, _ = apply_discovery_guards(
        [recommendation],
        candidate_pool=[candidate],
        held_codes=set(),
        profile=profile,
        budget_yuan=20_000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].tradeability["minimum_purchase_yuan"] == 10.0
    assert guarded[0].cost_assessment["minimum_holding_days"] == 180
    assert guarded[0].cost_assessment["executable"] is True


def test_unlimited_sentinel_and_zero_limit_are_not_confused() -> None:
    assert normalize_purchase_limit(100_000_000_000.0) == (None, True)
    assert normalize_purchase_limit(99_999_999_999.0) == (None, True)
    assert normalize_purchase_limit("无限额") == (None, True)
    assert normalize_purchase_limit(0) == (0.0, False)
