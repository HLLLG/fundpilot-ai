"""量价背离信号回测（M1.3）单测。

覆盖：K线/资金流按日期对齐（inner join）、已知答案统计口径（植入必然背离信号→
hit_rate≈100%+significant=True；随机噪声→不显著）、样本不足降级、
build_sector_flow_divergence_backtest 的 disabled/无映射/无资金流/缓存路径。
"""

from __future__ import annotations

from app.config import refresh_settings
from app.services.sector_flow_divergence_backtest import (
    DIVERGENCE_RULE_IDS,
    _align_kline_and_flow,
    backtest_flow_price_divergence,
    build_sector_flow_divergence_backtest,
)


def _kline_bar(date: str, change_percent: float) -> dict:
    return {"date": date, "change_percent": change_percent, "high_change_percent": None}


def _flow_point(date: str, main_force_net_yi: float) -> dict:
    return {"date": date, "main_force_net_yi": main_force_net_yi}


def test_align_kline_and_flow_inner_joins_by_date():
    kline = [_kline_bar("2026-06-01", 1.0), _kline_bar("2026-06-02", -0.5), _kline_bar("2026-06-03", 2.0)]
    flow = [_flow_point("2026-06-01", 5.0), _flow_point("2026-06-03", -3.0)]  # 缺 06-02

    aligned = _align_kline_and_flow(kline, flow)
    assert [row["date"] for row in aligned] == ["2026-06-01", "2026-06-03"]
    assert aligned[0]["change_percent"] == 1.0
    assert aligned[0]["main_force_net_yi"] == 5.0


def test_align_kline_and_flow_skips_rows_missing_values():
    kline = [_kline_bar("2026-06-01", 1.0)]
    flow = [{"date": "2026-06-01", "main_force_net_yi": None}]
    assert _align_kline_and_flow(kline, flow) == []


def _build_known_signal_series(days: int) -> tuple[list[dict], list[dict]]:
    """构造一个「必然验证」的背离信号：奇数日 distribution(涨+流出)→次日必跌；
    偶数日 accumulation(跌+流入)→次日必涨；其余日中性，避免规则误触发。"""
    import datetime

    base = datetime.date(2026, 1, 1)
    kline: list[dict] = []
    flow: list[dict] = []
    for i in range(days):
        day = (base + datetime.timedelta(days=i)).isoformat()
        if i % 4 == 0:
            # distribution 触发日：涨+流出
            kline.append(_kline_bar(day, 2.0))
            flow.append(_flow_point(day, -5.0))
        elif i % 4 == 1:
            # 上一天 distribution 的验证日：跌，符合 down_or_flat 预测
            kline.append(_kline_bar(day, -1.0))
            flow.append(_flow_point(day, 0.0))
        elif i % 4 == 2:
            # accumulation 触发日：跌+流入
            kline.append(_kline_bar(day, -2.0))
            flow.append(_flow_point(day, 5.0))
        else:
            # 上一天 accumulation 的验证日：涨，符合 up 预测
            kline.append(_kline_bar(day, 1.0))
            flow.append(_flow_point(day, 0.0))
    return kline, flow


def test_backtest_known_signal_is_significant_with_high_hit_rate():
    kline, flow = _build_known_signal_series(120)
    result = backtest_flow_price_divergence("BK9999", kline, flow, lookback_days=120)

    assert result["resolved"] is True
    by_rule = result["by_rule"]
    assert "flow_price_distribution" in by_rule
    assert "flow_price_accumulation" in by_rule

    dist = by_rule["flow_price_distribution"]
    accu = by_rule["flow_price_accumulation"]
    assert dist["hit_rate_percent"] >= 95.0
    assert accu["hit_rate_percent"] >= 95.0
    assert dist["trigger_count"] >= 25
    assert accu["trigger_count"] >= 25
    assert dist["significant"] is True
    assert accu["significant"] is True


def test_backtest_noise_series_is_not_significant():
    """涨跌与资金流方向随机配对（用固定伪随机种子模拟），不应产生显著背离信号。"""
    import datetime
    import random

    rng = random.Random(42)
    base = datetime.date(2026, 1, 1)
    kline = []
    flow = []
    for i in range(150):
        day = (base + datetime.timedelta(days=i)).isoformat()
        kline.append(_kline_bar(day, rng.uniform(-3, 3)))
        flow.append(_flow_point(day, rng.uniform(-10, 10)))

    result = backtest_flow_price_divergence("BK9998", kline, flow, lookback_days=150)
    by_rule = result["by_rule"]
    for rule_id in DIVERGENCE_RULE_IDS:
        bucket = by_rule.get(rule_id)
        if bucket is None:
            continue
        # 噪声数据下不应恰好产生「显著」结论（若触发次数太少，significant 天然为 False）。
        assert bucket["significant"] is False


def test_backtest_insufficient_aligned_days_returns_message():
    kline = [_kline_bar("2026-06-01", 1.0)]
    flow = [_flow_point("2026-06-01", 5.0)]
    result = backtest_flow_price_divergence("BK0001", kline, flow, lookback_days=100)
    assert result["resolved"] is True
    assert result["by_rule"] == {}
    assert "不足" in result["message"]


def test_backtest_lookback_window_caps_sample_size():
    kline, flow = _build_known_signal_series(300)
    result = backtest_flow_price_divergence("BK9999", kline, flow, lookback_days=50)
    assert result["sample_days"] <= 51  # window + 1（保留验证日）


def test_build_sector_flow_divergence_backtest_disabled(monkeypatch):
    monkeypatch.setenv("FUND_AI_FLOW_DIVERGENCE_BACKTEST_ENABLED", "false")
    refresh_settings()
    try:
        result = build_sector_flow_divergence_backtest("半导体")
        assert result["enabled"] is False
    finally:
        monkeypatch.delenv("FUND_AI_FLOW_DIVERGENCE_BACKTEST_ENABLED", raising=False)
        refresh_settings()


def test_build_sector_flow_divergence_backtest_empty_label_returns_unresolved():
    result = build_sector_flow_divergence_backtest("   ")
    assert result["enabled"] is True
    assert result["resolved"] is False


def test_build_sector_flow_divergence_backtest_no_kline_mapping_is_best_effort():
    result = build_sector_flow_divergence_backtest(
        "半导体",
        fetch_kline=lambda _label: [],
        fetch_flow=lambda _label: ("BK1036", [_flow_point("2026-06-01", 1.0)]),
    )
    assert result["enabled"] is True
    assert result["resolved"] is False
    assert "K线" in result["message"] or "跳过" in result["message"]


def test_build_sector_flow_divergence_backtest_no_flow_data_is_best_effort():
    result = build_sector_flow_divergence_backtest(
        "半导体",
        fetch_kline=lambda _label: [_kline_bar("2026-06-01", 1.0)],
        fetch_flow=lambda _label: (None, []),
    )
    assert result["enabled"] is True
    assert result["resolved"] is False


def test_build_sector_flow_divergence_backtest_success_with_injected_fetchers():
    kline, flow = _build_known_signal_series(60)
    result = build_sector_flow_divergence_backtest(
        "半导体",
        lookback_days=60,
        fetch_kline=lambda _label: kline,
        fetch_flow=lambda _label: ("BK1036", flow),
    )
    assert result["enabled"] is True
    assert result["resolved"] is True
    assert result["sector_label"] == "半导体"
    assert result["by_rule"]


def test_build_sector_flow_divergence_backtest_caches_result(monkeypatch):
    from app.services import sector_flow_divergence_backtest as service

    service._BACKTEST_CACHE.clear()
    kline, flow = _build_known_signal_series(60)
    calls = {"count": 0}

    def _fetch_kline(_label):
        calls["count"] += 1
        return kline

    monkeypatch.setattr(service, "_default_fetch_kline", _fetch_kline)
    monkeypatch.setattr(service, "_default_fetch_flow", lambda _label: ("BK1036", flow))

    first = build_sector_flow_divergence_backtest("半导体", lookback_days=60)
    second = build_sector_flow_divergence_backtest("半导体", lookback_days=60)

    assert first == second
    assert calls["count"] == 1  # 第二次应命中缓存，未重新拉取


def test_backtest_cache_is_lru_bounded(monkeypatch):
    from app.services import sector_flow_divergence_backtest as service

    service._BACKTEST_CACHE.clear()
    monkeypatch.setattr(service, "_BACKTEST_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(service.time, "time", lambda: 100.0)
    kline, flow = _build_known_signal_series(60)
    calls: list[str] = []

    def _fetch_kline(label):
        calls.append(label)
        return kline

    monkeypatch.setattr(service, "_default_fetch_kline", _fetch_kline)
    monkeypatch.setattr(service, "_default_fetch_flow", lambda _label: ("BK1036", flow))

    for label in ("半导体", "芯片", "半导体", "算力"):
        service.build_sector_flow_divergence_backtest(label, lookback_days=60)

    assert calls == ["半导体", "芯片", "算力"]
    assert list(service._BACKTEST_CACHE) == [
        service._cache_key("半导体", 60),
        service._cache_key("算力", 60),
    ]


def test_backtest_cache_prunes_expired_result(monkeypatch):
    from app.services import sector_flow_divergence_backtest as service

    service._BACKTEST_CACHE.clear()
    now = [100.0]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    kline, flow = _build_known_signal_series(60)
    calls = {"count": 0}

    def _fetch_kline(_label):
        calls["count"] += 1
        return kline

    monkeypatch.setattr(service, "_default_fetch_kline", _fetch_kline)
    monkeypatch.setattr(service, "_default_fetch_flow", lambda _label: ("BK1036", flow))

    service.build_sector_flow_divergence_backtest("半导体", lookback_days=60)
    now[0] += service._BACKTEST_RESPONSE_TTL_SECONDS + 1
    service.build_sector_flow_divergence_backtest("芯片", lookback_days=60)

    assert calls["count"] == 2
    assert list(service._BACKTEST_CACHE) == [service._cache_key("芯片", 60)]
