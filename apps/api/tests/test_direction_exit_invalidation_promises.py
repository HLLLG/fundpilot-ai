"""买入时写明的失效条件必须被逐日核对，而不是停在展示文案。

回归背景：`sector_direction_exit` 与 `sector_direction_state` 两个模块的 docstring 都写着
同一句话——

    `invalidation_signals` 那三条（趋势与资金同时跌入低位／主线转退潮／跌破 20 日均线）
    至今只是一段展示文案，没有代码在逐日跟踪它们。

于是荐基在买入卡片上承诺的退出条件，和日报实际执行的退出规则是**两套东西**：日报自己另推
一套（趋势 vs 退出线 + 连续跌破天数），用户当初同意的那几条从来没人回来核对。

现在：
* `sector_opportunity_scoring` 为每条文案产出机器可判的 `invalidation_checks`
  （阈值全部复用既有常量，不新设）；
* 买入事件的冻结快照因此带上这些 code，`load_direction_entry_contracts` 读回成
  `promised_invalidation`；
* `assess_direction_exit` 把"承诺过的"与"今天触发的"对照成 `invalidation_status`，
  并在 `breached_entry_promises` 里给出交集。

档位刻意收敛：这些 code 复用的是**入场**门槛阈值，用作退出触发没有回测支撑，所以单独触发
最高只到「停止加仓」，不得据此清仓。
"""

from __future__ import annotations

import pytest

from app.services.sector_direction_exit import assess_direction_exit

_EXIT_LINE = 52.0


def _checks(**triggered: bool | None) -> list[dict]:
    """构造今天的 `invalidation_checks`（只关心 code 与 triggered）。"""
    return [
        {"code": code, "label": f"文案-{code}", "triggered": value, "detail": "d"}
        for code, value in triggered.items()
    ]


def _contract(*codes: str, entry_trend: float | None = 92.16) -> dict:
    return {
        "sector_label": "医疗",
        "entry_date": "2026-08-10",
        "entry_state": "momentum_confirmation",
        "entry_trend": entry_trend,
        "promised_invalidation": [
            {"code": code, "label": f"承诺-{code}"} for code in codes
        ],
    }


# --- 逐条对照 ---------------------------------------------------------------


def test_promised_and_triggered_is_reported_as_breached() -> None:
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=90.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("mainline_fading", "structure_broken"),
        invalidation_checks=_checks(mainline_fading=True, structure_broken=False),
    )
    assert result["breached_entry_promises"] == ["mainline_fading"]
    by_code = {row["code"]: row for row in result["invalidation_status"]}
    assert by_code["mainline_fading"] == {
        "code": "mainline_fading",
        "label": "承诺-mainline_fading",
        "promised": True,
        "triggered": True,
        "detail": "d",
    }
    assert by_code["structure_broken"]["triggered"] is False


def test_triggered_but_never_promised_is_not_a_breach() -> None:
    """今天触发但当初没承诺过的条件，不能算"违背了买入承诺"。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=90.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("mainline_fading"),
        invalidation_checks=_checks(mainline_fading=False, structure_broken=True),
    )
    assert result["breached_entry_promises"] == []
    by_code = {row["code"]: row for row in result["invalidation_status"]}
    assert by_code["structure_broken"]["promised"] is False
    assert by_code["structure_broken"]["triggered"] is True


def test_unknown_today_is_not_treated_as_not_triggered() -> None:
    """今天缺数据（triggered=None）既不算触发、也不算解除。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=90.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("doubly_weak"),
        invalidation_checks=_checks(doubly_weak=None),
    )
    assert result["breached_entry_promises"] == []
    assert result["invalidation_status"][0]["triggered"] is None


def test_promised_condition_no_longer_produced_is_flagged_unknown() -> None:
    """策略档位变了、今天不再产出这条判定时必须如实标为无法核对。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=90.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("formation_probability_below_probe_line"),
        invalidation_checks=_checks(doubly_weak=False),
    )
    by_code = {row["code"]: row for row in result["invalidation_status"]}
    promised = by_code["formation_probability_below_probe_line"]
    assert promised["promised"] is True
    assert promised["triggered"] is None
    assert "无法逐日核对" in promised["detail"]
    assert result["breached_entry_promises"] == []


def test_no_contract_means_nothing_to_reconcile() -> None:
    result = assess_direction_exit(
        sector_label="煤炭",
        entry_state="ready_to_start",
        trend_strength=80.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=None,
        invalidation_checks=_checks(mainline_fading=True),
    )
    assert result["breached_entry_promises"] == []
    assert result["invalidation_status"][0]["promised"] is False


# --- 档位影响 ---------------------------------------------------------------


def test_breach_on_a_healthy_trend_stops_adds_but_does_not_sell() -> None:
    """趋势还很强、但承诺的条件已触发：至少不再加仓，且不要求卖出。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",  # 否则本来就会 pause_add，测不出是谁生效
        trend_strength=93.14,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("mainline_fading"),
        invalidation_checks=_checks(mainline_fading=True),
    )
    assert result["exit_state"] == "pause_add"
    assert result["allows_add"] is False
    # 不得据未标定的入场阈值处置真实仓位。
    assert result["suggested_position_change_percent"] is None
    assert "承诺-mainline_fading" in result["reasons"][0]


def test_no_breach_on_a_healthy_trend_still_allows_adds() -> None:
    """没有触发任何承诺时不得凭空收紧——这是新增判定最容易踩的回归。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=93.14,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("mainline_fading"),
        invalidation_checks=_checks(mainline_fading=False),
    )
    assert result["exit_state"] == "hold"
    assert result["allows_add"] is True


def test_breach_is_named_inside_an_existing_reduce_tier() -> None:
    """趋势已跌破退出线时档位不变，但理由里要点名被触发的那条承诺。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_on_pullback",
        trend_strength=40.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract("mainline_fading"),
        invalidation_checks=_checks(mainline_fading=True),
    )
    assert result["exit_state"] == "reduce"
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    assert any("承诺-mainline_fading" in reason for reason in result["reasons"])


def test_breach_never_escalates_beyond_pause_add_on_its_own() -> None:
    """单独触发承诺不得越过停止加仓——阈值本是入场门槛，未经退出侧回测。"""
    result = assess_direction_exit(
        sector_label="医疗",
        entry_state="ready_to_start",
        trend_strength=93.14,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract(
            "mainline_fading", "structure_broken", "doubly_weak"
        ),
        invalidation_checks=_checks(
            mainline_fading=True, structure_broken=True, doubly_weak=True
        ),
    )
    assert result["exit_state"] == "pause_add"
    assert result["suggested_position_change_percent"] is None
    assert result["thresholds_validated"] is False


def test_breach_survives_a_sector_label_drift() -> None:
    """分类漂移让相对基线用不上，但承诺仍属于那笔买入，照样要核对。"""
    contract = _contract("mainline_fading")
    contract["sector_label"] = "信创"
    result = assess_direction_exit(
        sector_label="数字经济",
        entry_state="ready_to_start",
        trend_strength=73.32,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=contract,
        invalidation_checks=_checks(mainline_fading=True),
    )
    # 基线用不上、但承诺核对没丢。
    assert result["entry_reference"] is None
    assert result["entry_reference_note"] is not None
    assert result["breached_entry_promises"] == ["mainline_fading"]
    assert result["allows_add"] is False


# --- 承诺的冻结与读回 -------------------------------------------------------


def test_promised_codes_are_read_back_from_the_frozen_snapshot() -> None:
    """买入事件的冻结快照里已经带着整行 v3 数据，承诺就从那里读回，不新增存储。"""
    from app.services.sector_direction_exit import _entry_contract_from_event

    event = {
        "event_id": "discovery:abc:0:011373",
        "decision_date": "2026-08-10",
        "payload": {
            "recommendation": {
                "sector_name": "医疗",
                "entry_path": "momentum_confirmation",
                "entry_tranche_scale": 0.4,
            },
            "replay_bundle": {
                "facts_snapshot": {
                    "sector_opportunities": [
                        {"sector_label": "软件", "trend_strength_score": 10.0},
                        {
                            "sector_label": "医疗",
                            "trend_strength_score": 92.16,
                            "participation_score": 97.78,
                            "position_risk_score": 94.44,
                            "invalidation_checks": [
                                {
                                    "code": "doubly_weak",
                                    "label": "趋势强度与资金参与度同时跌入横截面低位",
                                    "triggered": False,
                                },
                                {
                                    "code": "mainline_fading",
                                    "label": "主线状态转为退潮",
                                    "triggered": False,
                                },
                            ],
                        },
                    ]
                }
            },
        },
    }

    contract = _entry_contract_from_event(event)
    assert contract is not None
    assert contract["entry_trend"] == pytest.approx(92.16)
    assert [row["code"] for row in contract["promised_invalidation"]] == [
        "doubly_weak",
        "mainline_fading",
    ]
    # 文案一并冻结：以后策略改了措辞，历史那笔仍按当时写下的说法复核。
    assert contract["promised_invalidation"][1]["label"] == "主线状态转为退潮"


def test_old_events_without_checks_degrade_to_an_empty_promise() -> None:
    """本次改动之前的买入事件没有这个字段，必须优雅退化而不是报错。"""
    from app.services.sector_direction_exit import _entry_contract_from_event

    contract = _entry_contract_from_event(
        {
            "event_id": "discovery:old:0:000711",
            "decision_date": "2026-07-01",
            "payload": {
                "recommendation": {"sector_name": "医疗", "entry_path": "confirmed_entry"},
                "replay_bundle": {
                    "facts_snapshot": {
                        "sector_opportunities": [
                            {"sector_label": "医疗", "trend_strength_score": 70.0}
                        ]
                    }
                },
            },
        }
    )
    assert contract is not None
    assert contract["promised_invalidation"] == []


def test_daily_report_threads_todays_checks_into_the_exit_judgement(monkeypatch) -> None:
    """端到端：日报把当天方向行的 invalidation_checks 交给退出判定。"""
    from app.models import Holding
    from app.services import report_sector_opportunity as sector_ctx
    from app.services import sector_direction_exit as exit_mod

    monkeypatch.setattr(exit_mod, "load_direction_trend_history", lambda *_a, **_k: {})
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {
            "011373": {
                "sector_label": "医疗",
                "entry_date": "2026-08-10",
                "entry_trend": 92.16,
                "promised_invalidation": [
                    {"code": "mainline_fading", "label": "主线状态转为退潮"}
                ],
            }
        },
    )

    held = {
        "医疗": {
            "sector_label": "医疗",
            "entry_state": "ready_to_start",
            "trend_strength_score": 93.14,
            "invalidation_checks": [
                {"code": "mainline_fading", "label": "主线状态转为退潮", "triggered": True}
            ],
        }
    }
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[
            Holding(
                fund_code="011373",
                fund_name="招商前沿医疗保健股票A",
                sector_name="医疗",
                holding_amount=10_000.0,
            )
        ],
        trade_date="2026-08-11",
    )

    for exit_row in (held["医疗"]["direction_exit"], by_code["011373"]):
        assert exit_row["breached_entry_promises"] == ["mainline_fading"]
        assert exit_row["allows_add"] is False
        assert exit_row["exit_state"] == "pause_add"
