"""同族板块（细分↔父行业）方向分歧的识别与披露。

背景（2026-08 线上实测）：011373 的主板块是「医疗」（399989/BK0727 口径），000960 因
季报穿透过了 BK1600 门槛被判「CXO」。两个键方向状态分开计算，同日「医疗」判 invalid
触发大幅减仓评估 −50%，而「CXO」判 ready_to_start 给出分批买入——两张卡片没有任何
一句话解释这不是自相矛盾，且既有的跨报告披露因**精确标签匹配**恰好漏掉这类组合。

契约：只披露、不仲裁。族内标注与两侧披露都不得修改任何动作、比例、分数或状态。
"""

from __future__ import annotations

from app.models import DiscoveryRecommendation
from app.services.discovery_guard import _family_direction_conflict_note
from app.services.recommendation_guard import _direction_exit_family_note
from app.services.report_sector_opportunity import _attach_family_direction_divergence
from app.services.sector_direction_state import (
    DirectionStateRecord,
    annotate_family_direction_divergence,
)
from app.services.sector_labels import (
    SECTOR_FAMILY_PARENT,
    same_sector_family,
    sector_family_relation,
    sector_family_root,
)

# --------------------------------------------------------------------------
# 词汇表：同族关系的唯一事实源
# --------------------------------------------------------------------------


def test_fine_themes_resolve_to_their_parent_root() -> None:
    assert sector_family_root("CXO") == "医疗"
    assert sector_family_root("医疗") == "医疗"
    assert sector_family_root("半导体材料") == "半导体"
    assert sector_family_root(" CXO ") == "医疗"  # 归一化后再查
    assert sector_family_root(None) == ""


def test_same_sector_family_covers_both_directions_and_rejects_strangers() -> None:
    assert same_sector_family("CXO", "医疗")
    assert same_sector_family("医疗", "CXO")
    assert same_sector_family("医疗", "医疗")
    assert not same_sector_family("医疗", "医药")  # 并列宽主题刻意不并族
    assert not same_sector_family("CXO", "半导体")
    assert not same_sector_family("", "医疗")


def test_family_relation_distinguishes_parent_and_fine_theme() -> None:
    assert sector_family_relation("CXO", "医疗") == "parent"
    assert sector_family_relation("医疗", "CXO") == "fine_theme"
    assert sector_family_relation("医疗", "医疗") is None
    assert sector_family_relation("医疗", "半导体") is None


def test_display_parent_map_is_the_same_object() -> None:
    """展示回退与决策披露必须用同一份映射，两处各写一份必然漂移。"""
    from app.services.fund_holdings_sector_infer import _FINE_THEME_DISPLAY_PARENT

    assert _FINE_THEME_DISPLAY_PARENT is SECTOR_FAMILY_PARENT


# --------------------------------------------------------------------------
# 打分侧标注（discovery 扫描的完整横截面）
# --------------------------------------------------------------------------


def _row(label: str, state: str, *, eligible: bool | None = None) -> dict:
    row: dict = {"sector_label": label, "entry_state": state}
    if eligible is not None:
        row["execution_eligible"] = eligible
    return row


def test_ready_vs_invalid_in_the_same_family_is_annotated_on_both_rows() -> None:
    rows = [
        _row("CXO", "ready_to_start", eligible=True),
        _row("医疗", "invalid"),
        _row("半导体", "ready_to_start", eligible=True),
    ]
    annotate_family_direction_divergence(rows)

    cxo = rows[0]["family_direction_divergence"]
    assert cxo == [
        {"sector_label": "医疗", "entry_state": "invalid", "relation": "parent"}
    ]
    medical = rows[1]["family_direction_divergence"]
    assert medical == [
        {
            "sector_label": "CXO",
            "entry_state": "ready_to_start",
            "relation": "fine_theme",
        }
    ]
    # 不同族的行不被波及。
    assert "family_direction_divergence" not in rows[2]


def test_probe_eligible_side_also_counts_as_executable() -> None:
    """试仓通道（execution_eligible）激活等同可执行：它同样会产出买入类动作。"""
    rows = [
        _row("CXO", "ready_on_pullback", eligible=True),
        _row("医疗", "invalid"),
    ]
    annotate_family_direction_divergence(rows)
    assert rows[0]["family_direction_divergence"][0]["sector_label"] == "医疗"


def test_non_contradictory_family_states_are_not_annotated() -> None:
    # forming vs invalid：没有一侧会产出买入动作，不构成"相反动作"素材。
    rows = [_row("CXO", "forming"), _row("医疗", "invalid")]
    annotate_family_direction_divergence(rows)
    assert all("family_direction_divergence" not in row for row in rows)

    # 双双可布局：正常同涨，不是矛盾。
    rows = [
        _row("CXO", "ready_to_start", eligible=True),
        _row("医疗", "ready_to_start", eligible=True),
    ]
    annotate_family_direction_divergence(rows)
    assert all("family_direction_divergence" not in row for row in rows)


def test_annotation_never_mutates_states_or_scores() -> None:
    rows = [
        _row("CXO", "ready_to_start", eligible=True),
        _row("医疗", "invalid"),
    ]
    annotate_family_direction_divergence(rows)
    assert rows[0]["entry_state"] == "ready_to_start"
    assert rows[1]["entry_state"] == "invalid"


# --------------------------------------------------------------------------
# 发现侧披露：买入卡点名同族口径的 invalid
# --------------------------------------------------------------------------


def _rec(action: str = "分批买入", sector: str = "CXO") -> DiscoveryRecommendation:
    return DiscoveryRecommendation(
        fund_code="000960",
        fund_name="招商医药健康产业股票",
        sector_name=sector,
        action=action,
    )


def _cxo_opportunity_with_parent_invalid() -> dict:
    return {
        "sector_label": "CXO",
        "entry_state": "ready_to_start",
        "family_direction_divergence": [
            {"sector_label": "医疗", "entry_state": "invalid", "relation": "parent"}
        ],
    }


def test_buy_recommendation_discloses_invalid_parent_scope() -> None:
    note = _family_direction_conflict_note(_rec(), _cxo_opportunity_with_parent_invalid())
    assert note is not None
    assert "整体口径「医疗」" in note
    assert "不具备参与条件" in note
    assert "CXO" in note
    assert "总敞口" in note


def test_non_buy_actions_do_not_disclose_family_conflict() -> None:
    for action in ("建议关注", "等待回调"):
        assert (
            _family_direction_conflict_note(
                _rec(action), _cxo_opportunity_with_parent_invalid()
            )
            is None
        )


def test_no_disclosure_when_family_sibling_is_not_invalid() -> None:
    opportunity = {
        "sector_label": "CXO",
        "family_direction_divergence": [
            {"sector_label": "医疗", "entry_state": "forming", "relation": "parent"}
        ],
    }
    assert _family_direction_conflict_note(_rec(), opportunity) is None
    assert _family_direction_conflict_note(_rec(), {"sector_label": "CXO"}) is None
    assert _family_direction_conflict_note(_rec(), None) is None


# --------------------------------------------------------------------------
# 日报侧披露：卖出档退出 × 同族口径当日账本仍可布局
# --------------------------------------------------------------------------


def _ledger(states: dict[str, str]):
    def _load(trade_date: str | None):
        return {
            label: DirectionStateRecord(
                trade_date=str(trade_date),
                sector_label=label,
                entry_state=state,
                raw_entry_state=state,
                qualifies_for_ready=state == "ready_to_start",
                consecutive_qualifying_days=1 if state == "ready_to_start" else 0,
            )
            for label, state in states.items()
        }

    return _load


def _held_medical(exit_state: str = "deep_reduce") -> dict[str, dict]:
    return {
        "医疗": {
            "sector_label": "医疗",
            "entry_state": "invalid",
            "direction_exit": {
                "exit_state": exit_state,
                "reasons": ["方向「医疗」已判定为不具备参与条件"],
            },
        }
    }


def test_sell_side_exit_discloses_ready_family_sibling(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        _ledger({"CXO": "ready_to_start", "半导体": "ready_to_start"}),
    )
    held = _held_medical()

    _attach_family_direction_divergence(held, trade_date="2026-08-25")

    exit_row = held["医疗"]["direction_exit"]
    assert exit_row["family_divergence"] == [
        {
            "sector_label": "CXO",
            "entry_state": "ready_to_start",
            "relation": "fine_theme",
            "trade_date": "2026-08-25",
        }
    ]
    note = exit_row["family_divergence_note"]
    assert "细分口径「CXO」" in note
    assert "不构成对整个主题的否定" in note
    # 披露不改档位。
    assert exit_row["exit_state"] == "deep_reduce"


def test_hold_or_pause_states_do_not_look_up_the_ledger(monkeypatch) -> None:
    # 函数整体 best-effort（异常只会被吞掉记日志），所以用调用记录断言，不靠抛错。
    calls: list[str | None] = []

    def _spy(trade_date):
        calls.append(trade_date)
        return None

    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        _spy,
    )
    held = _held_medical(exit_state="pause_add")
    _attach_family_direction_divergence(held, trade_date="2026-08-25")
    assert calls == []
    assert "family_divergence" not in held["医疗"]["direction_exit"]


def test_no_disclosure_without_same_day_ledger_rows(monkeypatch) -> None:
    """今天没跑荐基就没有当日行：如实跳过，不引用旧交易日状态。"""
    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        lambda trade_date: None,
    )
    held = _held_medical()
    _attach_family_direction_divergence(held, trade_date="2026-08-25")
    assert "family_divergence" not in held["医疗"]["direction_exit"]


def test_family_sibling_not_ready_is_not_disclosed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        _ledger({"CXO": "forming"}),
    )
    held = _held_medical()
    _attach_family_direction_divergence(held, trade_date="2026-08-25")
    assert "family_divergence" not in held["医疗"]["direction_exit"]


def test_ledger_read_failure_never_raises(monkeypatch) -> None:
    def _boom(trade_date):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        _boom,
    )
    held = _held_medical()
    _attach_family_direction_divergence(held, trade_date="2026-08-25")
    assert "family_divergence" not in held["医疗"]["direction_exit"]


# --------------------------------------------------------------------------
# 日报守卫侧：把退出判定上的披露带到卡片
# --------------------------------------------------------------------------


def test_guard_passes_the_note_through_verbatim() -> None:
    exit_row = {"exit_state": "deep_reduce", "family_divergence_note": "同主题口径分歧：…"}
    assert (
        _direction_exit_family_note({"direction_exit": exit_row}, None)
        == "同主题口径分歧：…"
    )
    # facts_row 是第二优先级来源（与 resolve_escalation_floor 的取值一致）。
    assert (
        _direction_exit_family_note(None, {"direction_exit": exit_row})
        == "同主题口径分歧：…"
    )
    assert _direction_exit_family_note({"direction_exit": {"exit_state": "hold"}}, None) is None
    assert _direction_exit_family_note(None, None) is None
