"""canonical 日 K 的取值顺序与兜底：东财 kline 端点整体不可用时不能整条为空。

回归背景（2026-08-11 线上实测，逐源探针）：

| 源 | 状态 |
|---|---|
| 东财 kline（push2his / 79.push2 / push2） | 0.03 s 直接 TCP 断连 |
| 东财 kline（push2delay） | http=200 但 `klines=0` |
| sector-relay | 未配置，立即返回空 |
| akshare 指数日线 | 5.4 s 后返回空 |
| akshare 板块日 K | 立即 None |
| 新浪指数日线 | **只覆盖沪深挂牌的 000xxx/399xxx** |
| 板块资金流日线（BK） | **可用**，白名单 77 个板块里 71 个 ≥30 根，且零网络 |
| 新浪港股日线 | **可用**（HSI / HSTECH / HSCEI 实测可取） |

后果：`fetch_canonical_daily_kline_series` 对「软件(H30202)」「煤炭」「黄金(AU9999)」等全部返回
空，10 个抽样板块只有「医疗(399989)」靠新浪活着（1/10，累计 51.41 s）。连带板块信号回测 6 个
持仓板块里 5 个 `rules=0 sample=0`，量价背离 1/6——两者都是 `confidence` 升级判定的输入。

接进资金流日线与恒生日线之后实测 **10/10，累计 15.44 s**。

口径纪律：概念/行业板块的资金流日线是**同源**（canonical 本身就是那个 BK 板块）；指数类主题用它
只是**代理**（成分篮子不同），所以放在所有真指数源之后，并在 bar 上标 `source`。恒生系列只认板块
自己的 `source_code`，不拿 HSI 当各港股子板块的替身——那正是「BK0727 冒充中证医疗」那类错配。
"""

from __future__ import annotations

import pytest

from app.services import sector_daily_kline_provider as mod
from app.services.sector_canonical import CanonicalSector


def _flow_rows(days: int) -> list[dict]:
    return [
        {
            "date": f"2026-06-{index + 1:02d}",
            "change_percent": 0.5 + index * 0.01,
            "close_price": 100.0 + index,
            "main_force_net_yi": 1.0,
        }
        for index in range(days)
    ]


def _canon(source_type: str, *, secid: str, code: str, label: str = "测试板块") -> CanonicalSector:
    return CanonicalSector(
        label=label,
        source_type=source_type,
        source_name=label,
        eastmoney_secid=secid,
        source_code=code,
    )


#: conftest 有一个 autouse fixture 把 `fetch_canonical_daily_kline_series` 整体打成返回 `[]`
#: （为了让其它测试彻底离线）。本文件测的就是这个函数自身的取值顺序，所以必须先把真实实现
#: 还回来，否则测的是那个桩。模块 import 时抓取原函数——那时 conftest 的逐测试 patch 还没生效。
_REAL_FETCH = mod.fetch_canonical_daily_kline_series


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """还原被 conftest 打桩的本体，并掐断所有联网源，模拟线上"东财/relay/akshare 全灭"。"""
    monkeypatch.setattr(mod, "fetch_canonical_daily_kline_series", _REAL_FETCH)
    monkeypatch.setattr(mod, "fetch_eastmoney_daily_kline_series", lambda *_a, **_k: [])
    monkeypatch.setattr(mod, "fetch_daily_kline_via_relay", lambda *_a, **_k: [])
    monkeypatch.setattr(mod, "fetch_board_daily_kline_series", lambda *_a, **_k: [])
    monkeypatch.setattr(mod, "fetch_index_daily_via_akshare", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "fetch_index_daily_via_index_client", lambda *_a, **_k: None)


# --- 合成本体 ---------------------------------------------------------------


def test_bars_from_board_flow_carry_source_and_sort() -> None:
    bars = mod.daily_bars_from_board_flow_series(list(reversed(_flow_rows(5))))
    assert [bar["date"] for bar in bars] == sorted(bar["date"] for bar in bars)
    assert bars[0]["source"] == "eastmoney_board_fund_flow_daily"
    # 资金流日线没有当日最高价；新浪指数那条兜底同样给 None，不构成新退化。
    assert all(bar["high_change_percent"] is None for bar in bars)


def test_bars_from_board_flow_respect_max_days() -> None:
    bars = mod.daily_bars_from_board_flow_series(_flow_rows(50), max_days=20)
    assert len(bars) == 20
    assert bars[-1]["date"] == "2026-06-50"[:10] or len(bars) == 20


# --- 概念/行业：同源首选 ----------------------------------------------------


def test_board_sectors_use_flow_daily_as_the_same_source(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_board_flow_daily_bars", lambda canon, *, max_days: mod.daily_bars_from_board_flow_series(_flow_rows(60), max_days=max_days)
    )
    series = mod.fetch_canonical_daily_kline_series(
        _canon("industry", secid="90.BK0727", code="BK0727", label="医疗服务"),
        max_days=60,
    )
    assert len(series) == 60
    assert series[0]["source"] == "eastmoney_board_fund_flow_daily"


def test_board_sectors_still_fall_through_when_flow_is_too_short(monkeypatch) -> None:
    """同源太短时不能就地返回，仍要走后面的链路。"""
    monkeypatch.setattr(
        mod, "_board_flow_daily_bars",
        lambda canon, *, max_days: mod.daily_bars_from_board_flow_series(_flow_rows(3)),
    )
    monkeypatch.setattr(
        mod, "fetch_board_daily_kline_series",
        lambda *_a, **_k: [{"date": "2026-06-01", "change_percent": 1.0}] * 20,
    )
    series = mod.fetch_canonical_daily_kline_series(
        _canon("concept", secid="90.BK1174", code="BK1174", label="低空经济"),
        max_days=60,
    )
    assert len(series) == 20


# --- 指数类：真指数源优先，资金流只作代理 -----------------------------------


def test_index_sectors_prefer_a_real_index_source(monkeypatch) -> None:
    """有真指数源时不得降级成板块代理——两个成分篮子不能混。"""
    monkeypatch.setattr(
        mod,
        "fetch_index_daily_via_index_client",
        lambda code, trading_days=0: {
            "data": [
                {"date": f"2026-06-{i + 1:02d}", "close": 100.0 + i} for i in range(60)
            ],
            "source": "csindex",
        },
    )
    called: list[str] = []
    monkeypatch.setattr(
        mod, "_board_flow_daily_bars",
        lambda canon, *, max_days: called.append(canon.label) or [],
    )
    series = mod.fetch_canonical_daily_kline_series(
        _canon("index", secid="0.399989", code="399989", label="医疗"),
        max_days=60,
    )
    assert series
    assert all(bar.get("source") != "eastmoney_board_fund_flow_daily" for bar in series)
    assert called == []  # 代理压根没被调用


def test_index_sectors_fall_back_to_the_board_flow_proxy(monkeypatch) -> None:
    """真指数源全灭时用板块代理，而不是让整条证据消失。"""
    monkeypatch.setattr(
        mod, "_board_flow_daily_bars",
        lambda canon, *, max_days: mod.daily_bars_from_board_flow_series(_flow_rows(60), max_days=max_days),
    )
    series = mod.fetch_canonical_daily_kline_series(
        _canon("index", secid="2.H30202", code="H30202", label="软件"),
        max_days=60,
    )
    assert len(series) == 60
    # 代理身份必须可见。
    assert series[0]["source"] == "eastmoney_board_fund_flow_daily"


def test_no_source_at_all_returns_empty(monkeypatch) -> None:
    """既没有真指数源也没有 BK 码时如实为空，不许编造。"""
    monkeypatch.setattr(mod, "_board_flow_daily_bars", lambda canon, *, max_days: [])
    series = mod.fetch_canonical_daily_kline_series(
        _canon("index", secid="2.931672", code="931672", label="风电"),
        max_days=60,
    )
    assert series == []


# --- 恒生系列 ---------------------------------------------------------------


def test_hk_index_uses_the_sectors_own_symbol(monkeypatch) -> None:
    seen: list[str] = []

    def _hk(symbol, trading_days=0):
        seen.append(symbol)
        return {
            "data": [
                {"date": f"2026-06-{i + 1:02d}", "close": 100.0 + i} for i in range(40)
            ],
            "source": "sina_hk_index_daily",
        }

    monkeypatch.setattr("app.services.akshare_subprocess.fetch_hk_index_daily_history", _hk)
    series = mod.fetch_canonical_daily_kline_series(
        _canon("index", secid="124.HSTECH", code="HSTECH", label="恒生科技"),
        max_days=40,
    )
    assert series
    assert seen == ["HSTECH"]  # 用板块自己的 symbol，不是 HSI


def test_hk_path_is_skipped_for_a_share_secids(monkeypatch) -> None:
    """非 124 前缀不得走港股源。"""
    seen: list[str] = []
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_hk_index_daily_history",
        lambda symbol, trading_days=0: seen.append(symbol) or None,
    )
    monkeypatch.setattr(mod, "_board_flow_daily_bars", lambda canon, *, max_days: [])
    mod.fetch_canonical_daily_kline_series(
        _canon("index", secid="2.H30202", code="H30202", label="软件"),
        max_days=40,
    )
    assert seen == []
