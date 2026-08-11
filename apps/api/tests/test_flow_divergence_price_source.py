"""量价背离的价格序列必须复用同一份板块资金流日线，而不是另去联网拉指数日 K。

回归背景（2026-08-11 线上实测）：

背离回测原来用 `fetch_canonical_daily_kline_series` 单独联网取价格序列，而东财 kline 端点从这台
服务器不可用——48 种「主机 × ut × 参数」组合全部失败：`push2his` / `79.push2` / `push2` 直接
TCP 断连（RemoteDisconnected，0.03 s），`push2delay` 返回 http=200 但 `klines=0`。于是每个板块都要
把 akshare 子进程、sector-relay、新浪等兜底整条走一遍。

实测 6 个持仓板块：**成功 1/6，耗时 32.29 s**。而它在真实链路里的预算只有 4 s
（`SECTOR_DIVERGENCE_BUDGET_SECONDS`），等于每份日报固定烧掉 4 s 换回近乎为零的证据，
`confidence` 升级判定长期缺一路。

而回测真正消费的只有 `date` 与 `change_percent`（见 `_align_kline_and_flow`），这两项在它**本来
就要取的**资金流日线里每行都有（实测 6 个板块各 120 行，带涨跌与收盘各 120）。改用同一份行之后
实测 **6/6，0.00 s**。

顺带修掉两个正确性问题：
* 日期天然对齐 —— 指数日历与板块资金流日历不是同一份，而这是 T→T+1 回测，inner join 掉的每
  一天都是白丢的样本；
* 口径一致 —— 资金流是东财 BK 板块的，价格此前取中证指数的，是两个不同成分篮子（同一类错配
  已经在「医疗」上造成过 BK0727 与 399989 数字对不上）。
"""

from __future__ import annotations

import pytest

from app.services import sector_flow_divergence_backtest as mod


def _flow_rows(days: int = 60, *, with_change: bool = True) -> list[dict]:
    """构造带**背离**的序列：价格上涨而主力净流出（distribution 形态）。

    形态必须真的成立，否则 `by_rule` 为空——那测的就不是"价格序列来源"这件事了。
    """
    rows = []
    for index in range(days):
        rows.append(
            {
                "date": f"2026-05-{index + 1:02d}" if index < 31 else f"2026-06-{index - 30:02d}",
                # 持续净流出
                "main_force_net_yi": -3.0 - (index % 3),
                "close_price": 100.0 + index,
                # 持续上涨（与资金流反向）
                "change_percent": (1.2 + (index % 4) * 0.3) if with_change else None,
            }
        )
    return rows


# --- 合成本身 ---------------------------------------------------------------


def test_bars_are_synthesized_from_the_same_flow_rows() -> None:
    bars = mod._kline_bars_from_flow_series(_flow_rows(5))
    assert len(bars) == 5
    assert bars[0]["date"] == "2026-05-01"
    assert bars[0]["change_percent"] == pytest.approx(1.2)
    assert bars[0]["close"] == pytest.approx(100.0)
    # 回测不消费，但契约字段要在。
    assert bars[0]["high_change_percent"] is None


def test_rows_without_change_percent_are_dropped() -> None:
    rows = _flow_rows(4)
    rows[2]["change_percent"] = None
    bars = mod._kline_bars_from_flow_series(rows)
    assert len(bars) == 3
    assert all(bar["change_percent"] is not None for bar in bars)


def test_bars_are_sorted_by_date() -> None:
    rows = list(reversed(_flow_rows(6)))
    bars = mod._kline_bars_from_flow_series(rows)
    assert [bar["date"] for bar in bars] == sorted(bar["date"] for bar in bars)


def test_malformed_rows_do_not_raise() -> None:
    assert mod._kline_bars_from_flow_series([]) == []
    assert mod._kline_bars_from_flow_series(None) == []  # type: ignore[arg-type]
    bars = mod._kline_bars_from_flow_series(
        ["not-a-dict", {"date": "", "change_percent": 1.0}, {"date": "2026-05-01", "change_percent": "x"}]
    )
    assert bars == []


# --- 接进主流程 -------------------------------------------------------------


def test_backtest_uses_flow_prices_without_touching_the_network(monkeypatch) -> None:
    """资金流日线够用时，绝不调用那条又慢又常失败的联网 K 线兜底。"""
    calls: list[str] = []
    monkeypatch.setattr(
        mod, "_default_fetch_kline", lambda label: calls.append(label) or []
    )
    monkeypatch.setattr(
        mod,
        "_default_fetch_flow",
        lambda label: ("BK0727", _flow_rows(60)),
    )
    monkeypatch.setattr(mod, "_get_cached_backtest", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_set_cached_backtest", lambda *_a, **_k: None)

    result = mod.build_sector_flow_divergence_backtest("医疗")

    assert calls == []  # 一次联网都没有
    assert result["resolved"] is True
    assert result["price_series_source"] == "board_fund_flow_daily"
    assert result["by_rule"]


def test_short_flow_series_falls_back_to_the_networked_kline(monkeypatch) -> None:
    """资金流日线太短时仍然允许联网兜底——不能为了省网络牺牲样本量。"""
    monkeypatch.setattr(
        mod,
        "_default_fetch_flow",
        lambda label: ("BK0727", _flow_rows(10)),
    )
    fallback = [
        {"date": row["date"], "change_percent": row["change_percent"], "close": 1.0}
        for row in _flow_rows(60)
    ]
    monkeypatch.setattr(mod, "_default_fetch_kline", lambda label: fallback)
    monkeypatch.setattr(mod, "_get_cached_backtest", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_set_cached_backtest", lambda *_a, **_k: None)

    result = mod.build_sector_flow_divergence_backtest("医疗")

    assert result["price_series_source"] == "canonical_daily_kline"
    assert result["resolved"] is True


def test_missing_flow_skips_the_kline_fetch_entirely(monkeypatch) -> None:
    """资金流缺失时回测本来就做不了，不该再白花预算去打 K 线。"""
    calls: list[str] = []
    monkeypatch.setattr(
        mod, "_default_fetch_kline", lambda label: calls.append(label) or []
    )
    monkeypatch.setattr(mod, "_default_fetch_flow", lambda label: (None, []))
    monkeypatch.setattr(mod, "_get_cached_backtest", lambda *_a, **_k: None)

    result = mod.build_sector_flow_divergence_backtest("医疗")

    assert calls == []
    assert result["resolved"] is False
    assert "资金流" in result["message"]


def test_injected_kline_still_wins(monkeypatch) -> None:
    """注入口保持原语义，离线测试与既有调用方不受影响。"""
    injected = [
        {"date": row["date"], "change_percent": row["change_percent"], "close": 1.0}
        for row in _flow_rows(60)
    ]
    result = mod.build_sector_flow_divergence_backtest(
        "医疗",
        fetch_kline=lambda _label: injected,
        fetch_flow=lambda _label: ("BK0727", _flow_rows(60)),
    )
    assert result["price_series_source"] == "injected"
    assert result["resolved"] is True


def test_same_source_aligns_every_trading_day(monkeypatch) -> None:
    """同源的价值：inner join 不再因日历错位丢样本。"""
    flow = _flow_rows(60)
    aligned_same = mod._align_kline_and_flow(mod._kline_bars_from_flow_series(flow), flow)
    assert len(aligned_same) == len(flow)

    # 跨源：指数日历少了几天，样本随之减少。
    cross = [bar for bar in mod._kline_bars_from_flow_series(flow) if bar["date"][-2:] != "05"]
    aligned_cross = mod._align_kline_and_flow(cross, flow)
    assert len(aligned_cross) < len(aligned_same)
