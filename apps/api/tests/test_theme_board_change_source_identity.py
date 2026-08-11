"""涨跌幅口径必须与详情页分时同源：不得回落到资金流的 BK 板块代码。

回归背景：东财 clist 的指数池是 ``m:2``（中证系列 secid），查不到深市/沪市挂牌的中证
指数。医疗（0.399989 中证医疗）因此永远查不到自己，涨跌幅静默回落到 ``THEME_BOARD_FLOW``
里的 BK0727（东财医疗服务板块），而基金详情页分时走的是 0.399989。同一个"医疗"当日涨跌
在扫描报告里是 +2.77%、在详情页是 -0.41%，两个都自称"今日"。
"""

from __future__ import annotations

import pytest

from app.services.theme_board_snapshot import (
    _clist_lookup_codes,
    _lookup_clist_changes,
    list_theme_board_universe,
)


def _index_entry(label: str, secid: str, source_code: str, flow_code: str) -> dict:
    return {
        "sector_label": label,
        "secid": secid,
        "source_code": source_code,
        "flow_source_code": flow_code,
        "board_kind": "index",
        "change_hint": None,
    }


@pytest.mark.parametrize(
    ("label", "secid", "source_code", "flow_code"),
    [
        ("医疗", "0.399989", "399989", "BK0727"),
        ("软件", "2.H30202", "H30202", "BK0737"),
        ("军工", "0.399967", "399967", "BK0490"),
    ],
)
def test_change_lookup_never_falls_back_to_flow_board_code(
    label: str,
    secid: str,
    source_code: str,
    flow_code: str,
) -> None:
    entry = _index_entry(label, secid, source_code, flow_code)

    assert _clist_lookup_codes(entry, prefer_flow=False) == [source_code]

    # clist 只回了 BK 板块行（指数码缺失）时，宁可让涨跌幅为空走日 K 兜底，
    # 也不能把另一个成分篮子的涨幅当成本主题的当日涨跌。
    by_code = {
        flow_code: {
            "change_1d": 2.77,
            "change_5d": 3.1,
            "security_code": flow_code,
            "security_name": f"{label}板块",
        }
    }
    assert _lookup_clist_changes(entry, by_code) == (None, None)


def test_flow_lookup_still_prefers_board_code() -> None:
    """资金流 f62 只有 BK 板块有，指数主题必须仍然能查到自己的 BK 码。"""
    entry = _index_entry("医疗", "0.399989", "399989", "BK0727")
    assert _clist_lookup_codes(entry, prefer_flow=True) == ["BK0727", "399989"]


def test_board_kind_entries_still_resolve_their_own_change() -> None:
    """概念/行业板块的 source_code 本身就是 BK 码，涨跌幅解析不受影响。"""
    entry = {
        "sector_label": "低空经济",
        "secid": "90.BK1174",
        "source_code": "BK1174",
        "flow_source_code": "BK1174",
        "board_kind": "concept",
        "change_hint": 1.1,
    }
    by_code = {
        "BK1174": {
            "change_1d": 1.23,
            "change_5d": 4.5,
            "security_code": "BK1174",
            "security_name": "低空经济",
        }
    }
    assert _lookup_clist_changes(entry, by_code) == (1.23, 4.5)


def test_no_board_entry_depends_on_flow_code_for_change() -> None:
    """守住上面那条收窄的前提：没有任何板块靠 flow_source_code 才查得到涨跌幅。"""
    leaking = [
        entry["sector_label"]
        for entry in list_theme_board_universe()
        if entry.get("board_kind") != "index"
        and str(entry.get("flow_source_code") or "") != str(entry.get("source_code") or "")
    ]
    assert leaking == []
