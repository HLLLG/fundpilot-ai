"""基金风格回归（价值/成长暴露）测试。

设计文档：docs/superpowers/specs/2026-06-24-factor-style-and-universe-design.md（3C）。
"""
from __future__ import annotations

import random

from app.services.fund_style_regression import (
    MIN_STYLE_SAMPLE_DAYS,
    align_returns,
    compute_style_exposure,
)


def _dates(n: int) -> list[str]:
    return [f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)]


# ---------------------------------------------------------------------------
# 对齐
# ---------------------------------------------------------------------------


def test_align_returns_intersects_dates():
    f = {"2026-01-01": 1.0, "2026-01-02": 2.0, "2026-01-03": 3.0}
    v = {"2026-01-02": 0.2, "2026-01-03": 0.3}
    g = {"2026-01-02": 0.5, "2026-01-03": 0.6, "2026-01-04": 0.7}
    fr, vr, gr = align_returns(f, v, g)
    assert fr == [2.0, 3.0]  # 仅公共日期 01-02, 01-03，升序
    assert vr == [0.2, 0.3]
    assert gr == [0.5, 0.6]


# ---------------------------------------------------------------------------
# 回归
# ---------------------------------------------------------------------------


def test_value_fund_tilts_value():
    rng = random.Random(1)
    n = 120
    value = [rng.uniform(-2, 2) for _ in range(n)]
    growth = [rng.uniform(-2, 2) for _ in range(n)]
    fund = list(value)  # 基金收益完全 = 价值指数
    res = compute_style_exposure(fund, value, growth)
    assert res.available is True
    assert res.beta_value is not None and abs(res.beta_value - 1.0) < 0.05
    assert res.beta_growth is not None and abs(res.beta_growth) < 0.05
    assert res.style_tilt > 0.5
    assert res.label == "偏价值"
    assert res.r_squared is not None and res.r_squared > 0.95


def test_growth_fund_tilts_growth():
    rng = random.Random(2)
    n = 120
    value = [rng.uniform(-2, 2) for _ in range(n)]
    growth = [rng.uniform(-2, 2) for _ in range(n)]
    fund = list(growth)
    res = compute_style_exposure(fund, value, growth)
    assert res.available is True
    assert res.style_tilt < -0.5
    assert res.label == "偏成长"


def test_insufficient_sample_unavailable():
    short = [0.1] * (MIN_STYLE_SAMPLE_DAYS - 1)
    res = compute_style_exposure(short, short, short)
    assert res.available is False
    assert res.message is not None


def test_collinear_styles_unavailable():
    rng = random.Random(3)
    n = 120
    value = [rng.uniform(-2, 2) for _ in range(n)]
    growth = list(value)  # 价值 == 成长 → 共线，无法分离
    fund = [v * 0.8 for v in value]
    res = compute_style_exposure(fund, value, growth)
    assert res.available is False


# ---------------------------------------------------------------------------
# runner 离线
# ---------------------------------------------------------------------------


def test_runner_offline_writes_summary(tmp_path):
    import json

    from scripts.run_style_factor import build_style_report

    rng = random.Random(7)
    n = 200
    days = _dates(n)
    value_prices = [100.0]
    growth_prices = [100.0]
    for _ in range(1, n):
        value_prices.append(value_prices[-1] * (1 + rng.uniform(-0.01, 0.012)))
        growth_prices.append(growth_prices[-1] * (1 + rng.uniform(-0.012, 0.01)))

    def fetch_index(symbol, trading_days):
        prices = value_prices if symbol == "V" else growth_prices
        return list(zip(days, prices))

    def fetch_rank(limit):
        return [
            {"fund_code": "000001", "fund_name": "价值基金"},
            {"fund_code": "000002", "fund_name": "成长基金"},
        ]

    def fetch_nav(code, trading_days):
        prices = value_prices if code == "000001" else growth_prices
        return list(zip(days, prices))

    out = build_style_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        fetch_index=fetch_index,
        out_dir=str(tmp_path),
        universe_size=2,
        nav_days=n,
        value_index="V",
        growth_index="G",
    )
    assert out["style_data_available"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "report.txt").exists()
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    labels = {f["fund_code"]: f.get("label") for f in data["funds"]}
    assert labels["000001"] == "偏价值"
    assert labels["000002"] == "偏成长"
