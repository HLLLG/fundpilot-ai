"""「今日涨跌估算」必须带上板块快照的截止时刻，供用户与详情页分时对账。

背景：盘中扫描给出的估算是板块某一时刻的快照，不是收盘值。2026-08-11 用户在 14:31 扫描，
报告写「今日涨跌估算 +2.77%」，而那个数其实截止于 13:17，报告里没有任何时刻信息，用户
无从判断它已经过时。
"""

from __future__ import annotations

import pytest

from app.services.discovery_candidate_llm import (
    build_sector_change_as_of_index,
    build_sector_change_index,
    format_change_as_of_time,
    resolve_candidate_daily_estimate,
    slim_candidate_pool_for_llm,
    trim_sector_heat_for_llm,
)

# 2026-08-11T06:38:50Z == 北京时间 14:38
AS_OF_UTC = "2026-08-11T06:38:50.099430+00:00"
AS_OF_LOCAL = "14:38"


def _heat_rows() -> list[dict]:
    return [
        {
            "sector_label": "医疗",
            "change_1d_percent": -0.41,
            "change_5d_percent": 1.2,
            "heat_score": 0.1,
            "change_as_of": AS_OF_UTC,
        },
        {
            "sector_label": "软件",
            "change_1d_percent": -0.24,
            "change_5d_percent": -0.65,
            "heat_score": -0.4,
            "change_as_of": AS_OF_UTC,
        },
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (AS_OF_UTC, AS_OF_LOCAL),
        ("2026-08-11T06:38:50Z", AS_OF_LOCAL),
        ("2026-08-11T06:38:50", AS_OF_LOCAL),  # 无时区按 UTC 解释
        (None, None),
        ("", None),
        ("not-a-timestamp", None),
    ],
)
def test_format_change_as_of_time(value, expected):
    assert format_change_as_of_time(value) == expected


def test_as_of_index_covers_normalized_labels():
    index = build_sector_change_as_of_index(_heat_rows())
    assert index["医疗"] == AS_OF_LOCAL
    assert index["软件"] == AS_OF_LOCAL


def test_sector_estimate_carries_as_of():
    heat = _heat_rows()
    value, source, as_of = resolve_candidate_daily_estimate(
        fund_code="000711",
        sector_label="医疗",
        sector_change_index=build_sector_change_index(heat),
        trade_date=None,
        sector_change_as_of_index=build_sector_change_as_of_index(heat),
    )
    assert (value, source, as_of) == (-0.41, "sector_estimate", AS_OF_LOCAL)


def test_candidate_projection_exposes_as_of():
    rows = slim_candidate_pool_for_llm(
        [{"fund_code": "000711", "fund_name": "嘉实医疗保健股票", "sector_label": "医疗"}],
        sector_heat=_heat_rows(),
        trade_date=None,
    )
    assert rows[0]["estimated_daily_return_percent"] == -0.41
    assert rows[0]["daily_return_source"] == "sector_estimate"
    assert rows[0]["estimated_daily_return_as_of"] == AS_OF_LOCAL


def test_sector_heat_projection_replaces_utc_with_local_time():
    """LLM 视图里不应出现 UTC ISO，避免模型自己做时区换算。"""
    trimmed = trim_sector_heat_for_llm(
        _heat_rows(), target_sectors=["医疗"], focus_sectors=[]
    )
    row = next(item for item in trimmed if item["sector_label"] == "医疗")
    assert row["change_as_of_time"] == AS_OF_LOCAL
    assert "change_as_of" not in row


def _recommendation(**overrides):
    from app.models import DiscoveryRecommendation

    payload = {
        "fund_code": "000711",
        "fund_name": "嘉实医疗保健股票",
        "sector_name": "医疗",
        "action": "分批买入",
        "hold_horizon": "1-3个月",
        "confidence": "中",
        "points": ["今日涨跌估算 -0.41%（板块估算）"],
        "risks": ["波动偏高"],
    }
    payload.update(overrides)
    return DiscoveryRecommendation(**payload)


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        # 线上 08-11 报告的真实措辞
        (
            "今日涨跌估算+2.77%（板块估算），净值已收复近20日高点",
            "今日涨跌估算+2.77%（板块估算，截至 14:38），净值已收复近20日高点",
        ),
        (
            "今日涨幅估算（板块估算）约+2.77%，需结合实时确认",
            "今日涨幅估算（板块估算，截至 14:38）约+2.77%，需结合实时确认",
        ),
        (
            "今日涨跌估算 +2.77%（板块估算，非收盘值），板块涨幅领先",
            "今日涨跌估算 +2.77%（板块估算，非收盘值，截至 14:38），板块涨幅领先",
        ),
        ("板块估算显示今日上涨 2.77%", "板块估算（截至 14:38）显示今日上涨 2.77%"),
    ],
)
def test_guard_stamps_as_of_into_the_visible_point(point, expected):
    """时刻必须落在 points[0]——前端那行「核心理由」渲染的就是它。"""
    from app.services.discovery_guard import _annotate_daily_estimate_as_of

    rec = _recommendation(points=[point])
    _annotate_daily_estimate_as_of(rec, {"医疗": AS_OF_LOCAL})

    assert rec.points[0] == expected
    assert rec.validation_notes == []


def test_guard_falls_back_to_validation_note_when_no_anchor_in_text():
    """模型没写「板块估算」时无处插入，退到完整依据里至少可查。"""
    from app.services.discovery_guard import _annotate_daily_estimate_as_of

    rec = _recommendation(points=["基金净值估算今日涨 2.77%，近5日涨 17.53%"])
    _annotate_daily_estimate_as_of(rec, {"医疗": AS_OF_LOCAL})

    assert rec.points == ["基金净值估算今日涨 2.77%，近5日涨 17.53%"]
    assert any(AS_OF_LOCAL in note for note in rec.validation_notes)
    assert any("非收盘值" in note for note in rec.validation_notes)


def test_guard_stamps_only_the_first_occurrence():
    from app.services.discovery_guard import _annotate_daily_estimate_as_of

    rec = _recommendation(
        points=[
            "今日涨跌估算 -0.41%（板块估算）",
            "板块估算同时显示近5日走弱",
        ]
    )
    _annotate_daily_estimate_as_of(rec, {"医疗": AS_OF_LOCAL})

    assert rec.points[0] == "今日涨跌估算 -0.41%（板块估算，截至 14:38）"
    assert rec.points[1] == "板块估算同时显示近5日走弱"


def test_guard_does_not_duplicate_when_model_already_stated_it():
    """模型照 prompt 写了时刻就不再改动，避免出现两个「截至」。"""
    from app.services.discovery_guard import _annotate_daily_estimate_as_of

    original = f"今日涨跌估算 -0.41%（板块估算，截至 {AS_OF_LOCAL}）"
    rec = _recommendation(points=[original])
    _annotate_daily_estimate_as_of(rec, {"医疗": AS_OF_LOCAL})

    assert rec.points == [original]
    assert rec.validation_notes == []


def test_guard_skips_when_sector_has_no_as_of():
    """拿不到截止时刻时不得编一个。"""
    from app.services.discovery_guard import _annotate_daily_estimate_as_of

    rec = _recommendation(action="建议关注", confidence="低", points=["净值走势偏弱"])
    _annotate_daily_estimate_as_of(rec, {})

    assert rec.points == ["净值走势偏弱"]
    assert rec.validation_notes == []
