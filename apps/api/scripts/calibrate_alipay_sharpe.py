"""用天天基金公示的风险指标反推夏普口径。

支付宝基金页的夏普/标准差与天天基金特色数据同源（东方财富 Choice）。
本脚本拉取复权日增长率，枚举年化天数、无风险利率、收益年化方式，对齐公示值。
"""
from __future__ import annotations

import math
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.akshare_subprocess import (
    _FUND_NAV_INDICATOR,
    _SUBPROCESS_TIMEOUT,
    _fund_nav_history_script,
    run_akshare_json_script,
)
from app.services.fund_factor_nav import build_total_return_index

TARGETS = {
    "000001": {
        "as_of": "2026-08-25",
        1: {"std": 35.46, "sharpe": 0.74},
        3: {"std": 25.88, "sharpe": 0.49},
    },
    "110011": {
        "as_of": "2026-08-25",
        1: {"std": 16.42, "sharpe": -1.44},
        3: {"std": 20.36, "sharpe": -0.54},
    },
}


def _load_rows(code: str) -> list[dict]:
    payload = run_akshare_json_script(
        _fund_nav_history_script(code, 900, _FUND_NAV_INDICATOR),
        label=f"calibrate_sharpe:{code}",
        timeout=_SUBPROCESS_TIMEOUT,
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"{code} 净值拉取失败: {payload}")
    return [row for row in rows if row.get("date")]


def _slice_trading_days(rows: list[dict], as_of: str, trading_days: int) -> list[dict]:
    eligible = [row for row in rows if str(row["date"])[:10] <= as_of]
    return eligible[-(trading_days + 1) :] if trading_days > 0 else eligible


def _slice_calendar_years(rows: list[dict], as_of: str, years: int) -> list[dict]:
    end = date.fromisoformat(as_of)
    start = date(end.year - years, end.month, end.day)
    return [
        row
        for row in rows
        if start <= date.fromisoformat(str(row["date"])[:10]) <= end
    ]


def _daily_returns(rows: list[dict], *, prefer_growth: bool) -> list[float]:
    series = build_total_return_index(rows)
    values = [value for _day, value in series.points]
    if prefer_growth:
        returns: list[float] = []
        for row in rows[1:]:
            growth = row.get("daily_growth")
            if growth is None:
                continue
            returns.append(float(growth) / 100.0)
        return returns
    return [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]


def _period_return(rows: list[dict]) -> float | None:
    series = build_total_return_index(rows)
    values = [value for _day, value in series.points]
    if len(values) < 2 or values[0] <= 0:
        return None
    return values[-1] / values[0] - 1.0


def _ann_return(period_return: float, rows: list[dict], mode: str) -> float:
    start = date.fromisoformat(str(rows[0]["date"])[:10])
    end = date.fromisoformat(str(rows[-1]["date"])[:10])
    calendar_days = max((end - start).days, 1)
    trading_days = max(len(rows) - 1, 1)
    if mode == "365_calendar":
        return (1.0 + period_return) ** (365.0 / calendar_days) - 1.0
    if mode == "250_trading":
        return (1.0 + period_return) ** (250.0 / trading_days) - 1.0
    if mode == "252_trading":
        return (1.0 + period_return) ** (252.0 / trading_days) - 1.0
    if mode == "arith_250":
        return period_return * (250.0 / trading_days)
    return period_return * (252.0 / trading_days)


def _ann_std(returns: list[float], factor: int, *, sample: bool) -> float | None:
    if len(returns) < 2:
        return None
    vol = statistics.stdev(returns) if sample else statistics.pstdev(returns)
    return vol * math.sqrt(factor)


def _weekly_returns(rows: list[dict]) -> list[float]:
    series = build_total_return_index(rows)
    by_week: dict[str, float] = {}
    for day, value in series.points:
        by_week[day[:10]] = value
    ordered = sorted(by_week)
    picked: list[tuple[str, float]] = []
    for day in ordered:
        current = date.fromisoformat(day)
        if not picked or (current - date.fromisoformat(picked[-1][0])).days >= 7:
            picked.append((day, by_week[day]))
    return [
        picked[index][1] / picked[index - 1][1] - 1.0
        for index in range(1, len(picked))
        if picked[index - 1][1] > 0
    ]


def main() -> None:
    loaded = {code: _load_rows(code) for code in TARGETS}
    one_year_windows = [
        ("td_242", 1, 242),
        ("td_244", 1, 244),
        ("td_246", 1, 246),
        ("td_248", 1, 248),
        ("td_250", 1, 250),
        ("td_252", 1, 252),
        ("cal_1y", 1, None),
    ]
    three_year_windows = [
        ("td_728", 3, 728),
        ("td_744", 3, 744),
        ("td_750", 3, 750),
        ("td_756", 3, 756),
        ("cal_3y", 3, None),
    ]
    ranked: list[tuple[float, dict]] = []
    for one_name, _one_years, one_days in one_year_windows:
        for three_name, _three_years, three_days in three_year_windows:
            for prefer_growth in (True, False):
                for sample in (True, False):
                    for std_factor in (250, 252):
                        for ret_mode in (
                            "365_calendar",
                            "250_trading",
                            "252_trading",
                        ):
                            for rf in (0.015, 0.02):
                                score = 0.0
                                preview = []
                                complete = True
                                for years, window_name, trading_days in (
                                    (1, one_name, one_days),
                                    (3, three_name, three_days),
                                ):
                                    for code, target in TARGETS.items():
                                        as_of = target["as_of"]
                                        rows = loaded[code]
                                        if trading_days is None:
                                            sliced = _slice_calendar_years(
                                                rows, as_of, years
                                            )
                                        else:
                                            sliced = _slice_trading_days(
                                                rows, as_of, trading_days
                                            )
                                        returns = _daily_returns(
                                            sliced, prefer_growth=prefer_growth
                                        )
                                        period = _period_return(sliced)
                                        std = _ann_std(
                                            returns, std_factor, sample=sample
                                        )
                                        if period is None or std in (None, 0):
                                            complete = False
                                            break
                                        ann_ret = _ann_return(
                                            period, sliced, ret_mode
                                        )
                                        sharpe = (ann_ret - rf) / std
                                        expected = target[years]
                                        score += abs(std * 100 - expected["std"])
                                        score += (
                                            abs(sharpe - expected["sharpe"]) * 10
                                        )
                                        preview.append(
                                            f"{code} {years}y n={len(sliced)} "
                                            f"std={std*100:.2f}/{expected['std']} "
                                            f"sharpe={sharpe:.2f}/{expected['sharpe']}"
                                        )
                                    if not complete:
                                        break
                                if not complete:
                                    continue
                                ranked.append(
                                    (
                                        score,
                                        {
                                            "key": (
                                                f"{one_name}+{three_name} "
                                                f"growth={prefer_growth} sample={sample} "
                                                f"std*{std_factor} ret={ret_mode} rf={rf}"
                                            ),
                                            "score": round(score, 4),
                                            "preview": preview,
                                        },
                                    )
                                )
    ranked.sort(key=lambda item: item[0])
    print("== 统一口径 Top 8 ==")
    for _score, payload in ranked[:8]:
        print(f"{payload['key']}  score={payload['score']}")
        for line in payload["preview"]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
