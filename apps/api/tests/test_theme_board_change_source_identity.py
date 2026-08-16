"""涨跌幅口径必须与详情页分时同源：不得回落到资金流的 BK 板块代码。

回归背景：东财 clist 的指数池是 ``m:2``（中证系列 secid），查不到深市/沪市挂牌的中证
指数。医疗（0.399989 中证医疗）因此永远查不到自己，涨跌幅静默回落到 ``THEME_BOARD_FLOW``
里的 BK0727（东财医疗服务板块），而基金详情页分时走的是 0.399989。同一个"医疗"当日涨跌
在扫描报告里是 +2.77%、在详情页是 -0.41%，两个都自称"今日"。
"""

from __future__ import annotations

import pytest

import re

from app.services.eastmoney_spot_client import _CLIST_THEME_POOLS
from app.services.sector_registry_data import THEME_BOARD_INDEX, THEME_BOARD_WHITELIST
from app.services.theme_board_snapshot import (
    _clist_lookup_codes,
    _enrich_missing_5d_via_kline,
    _enrich_missing_changes_via_secid,
    _five_day_change_from_daily_bars,
    _lookup_clist_changes,
    list_theme_board_universe,
)

# 这些行情码不在东财 clist m:2（中证系列）。只拉 m:2 时 5 日必然空，必须走
# 沪深指数池 / ulist 精确 secid / 同标的日 K，不能回落资金流 BK。
_OUTSIDE_CSI_CLIST = re.compile(r"^(?:000\d{3}|399\d{3}|51\d{4}|HSTECH)$")


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


def test_flow_lookup_captures_change_from_the_same_row_as_flow() -> None:
    """量价判断的价格腿必须与主力净流入同一行（同一个成分篮子）。

    指数行（399989）与 BK 行（BK0727）同日涨跌可以反号；``flow_change_1d_percent``
    只能取提供 f62 的那一行的 f3，绝不能串到指数行。
    """
    from app.services.theme_board_snapshot import _lookup_clist_flow

    entry = _index_entry("医疗", "0.399989", "399989", "BK0727")
    by_code = {
        "399989": {
            "change_1d": 2.1,
            "change_5d": 4.0,
            "security_code": "399989",
            "security_name": "中证医疗",
        },
        "BK0727": {
            "change_1d": -0.8,
            "change_5d": -1.5,
            "main_force_net_yi": -3.2,
            "super_large_net_yi": -2.0,
            "large_net_yi": -1.2,
            "medium_net_yi": 1.4,
            "small_net_yi": 1.8,
            "security_code": "BK0727",
            "security_name": "医疗服务",
        },
    }

    flow = _lookup_clist_flow(entry, by_code)

    assert flow["main_force_net_yi"] == -3.2
    assert flow["flow_change_1d_percent"] == -0.8
    assert flow["flow_change_5d_percent"] == -1.5


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


def test_theme_clist_covers_shanghai_and_shenzhen_listed_indexes() -> None:
    """保险 399809、医疗 399989 在深市池；有色 000819 在沪市池。只拉 m:2 时 5 日为空。"""
    assert _CLIST_THEME_POOLS["index"]["fs"] == "m:2"
    assert _CLIST_THEME_POOLS["index_sz"]["fs"] == "m:0+t:5"
    assert _CLIST_THEME_POOLS["index_sh"]["fs"] == "m:1+t:1"


def test_missing_five_day_change_is_filled_from_exact_secid(monkeypatch) -> None:
    """clist 没有 399809 / HSTECH 时，按 secid 补 5 日，不能去借资金流 BK 的涨跌。"""
    insurance = _index_entry("保险", "0.399809", "399809", "BK0474")
    hang_seng = _index_entry("恒生科技", "124.HSTECH", "HSTECH", None)
    items = [
        {
            "change_1d_percent": -1.60,
            "change_5d_percent": None,
        },
        {
            "change_1d_percent": -1.52,
            "change_5d_percent": None,
        },
    ]

    def fake_quotes(secids, **_kwargs):
        assert "0.399809" in secids
        assert "124.HSTECH" in secids
        return {
            "0.399809": {
                "security_code": "399809",
                "security_name": "保险主题",
                "change_percent": -1.60,
                "change_5d_percent": -3.33,
            },
            "124.HSTECH": {
                "security_code": "HSTECH",
                "security_name": "恒生科技指数",
                "change_percent": -1.52,
                "change_5d_percent": -1.18,
            },
            "90.BK0474": {
                "security_code": "BK0474",
                "security_name": "保险",
                "change_percent": 9.99,
                "change_5d_percent": 8.88,
            },
        }

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_quotes_by_secid",
        fake_quotes,
    )
    _enrich_missing_changes_via_secid(
        [(items[0], insurance), (items[1], hang_seng)]
    )
    assert items[0]["change_5d_percent"] == -3.33
    assert items[1]["change_5d_percent"] == -1.18


def test_every_listed_index_outside_csi_clist_is_still_quotable() -> None:
    """红利 000922、新能源 000941、保险 399809、黄金 518880、恒生 HSTECH… 全部登记在案。"""
    outside = {
        label
        for label in THEME_BOARD_WHITELIST
        if _OUTSIDE_CSI_CLIST.fullmatch(
            str(THEME_BOARD_INDEX.get(label, ("", "", ""))[1]).upper()
        )
    }
    assert outside == {
        "保险",
        "军工",
        "农业",
        "医疗",
        "国企改革",
        "恒生科技",
        "新能源",
        "有色金属",
        "智能家居",
        "环保",
        "红利",
        "黄金",
    }


def test_five_day_change_from_daily_bars_uses_close_ratio() -> None:
    bars = [
        {"close": 100.0, "change_percent": 1.0},
        {"close": 101.0, "change_percent": 1.0},
        {"close": 102.0, "change_percent": 1.0},
        {"close": 103.0, "change_percent": 1.0},
        {"close": 104.0, "change_percent": 1.0},
        {"close": 105.0, "change_percent": 1.0},
    ]
    assert _five_day_change_from_daily_bars(bars) == 5.0


def test_missing_five_day_change_falls_back_to_same_secid_kline(monkeypatch) -> None:
    dividend = _index_entry("红利", "1.000922", "000922", "BK1641")
    energy = _index_entry("新能源", "1.000941", "000941", None)
    items = [
        {"change_1d_percent": 0.26, "change_5d_percent": None},
        {"change_1d_percent": 0.21, "change_5d_percent": None},
    ]
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_quotes_by_secid",
        lambda *_args, **_kwargs: {},
    )

    def fake_kline(secid, **_kwargs):
        if secid == "1.000922":
            return [
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 99.48},
            ]
        if secid == "1.000941":
            return [
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 100.0},
                {"close": 101.33},
            ]
        return []

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_daily_kline_series",
        fake_kline,
    )
    _enrich_missing_5d_via_kline([(items[0], dividend), (items[1], energy)])
    assert items[0]["change_5d_percent"] == -0.52
    assert items[1]["change_5d_percent"] == 1.33


def test_secid_five_day_fallback_rejects_identity_mismatch(monkeypatch) -> None:
    entry = _index_entry("保险", "0.399809", "399809", "BK0474")
    item = {"change_1d_percent": -1.60, "change_5d_percent": None}
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_quotes_by_secid",
        lambda *_args, **_kwargs: {
            "0.399809": {
                "security_code": "399809",
                "security_name": "中证2000",
                "change_percent": 1.0,
                "change_5d_percent": 4.0,
            }
        },
    )
    _enrich_missing_changes_via_secid([(item, entry)])
    assert item["change_5d_percent"] is None


def test_no_board_entry_depends_on_flow_code_for_change() -> None:
    """守住上面那条收窄的前提：没有任何板块靠 flow_source_code 才查得到涨跌幅。"""
    leaking = [
        entry["sector_label"]
        for entry in list_theme_board_universe()
        if entry.get("board_kind") != "index"
        and str(entry.get("flow_source_code") or "") != str(entry.get("source_code") or "")
    ]
    assert leaking == []
