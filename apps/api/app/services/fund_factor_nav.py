"""从一段 NAV 切片算因子原始值（动量/Calmar/回撤）。

模块2（持仓不在榜的净值兜底）与模块3（因子 IC 回测）共用，避免重复。
设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md 第 5 章。

窗口口径与排行榜一致：3 月≈60、6 月≈120、1 年≈250 交易日；
最大回撤复用模块1 `portfolio_risk_metrics._max_drawdown` 保口径一致。
"""
from __future__ import annotations


def window_return_percent(navs: list[float], window: int) -> float | None:
    """升序净值序列近 window 个交易日区间收益(%)；不足则尽力从最早点算。"""
    if len(navs) < 2:
        return None
    base = navs[max(0, len(navs) - 1 - window)]
    if base <= 0:
        return None
    return (navs[-1] / base - 1.0) * 100.0


def factor_input_from_navs(code: str, name: str, navs: list[float]):
    """从一段升序净值算 FundFactorInput（return_3m/6m/1y + 1年最大回撤；规模 None）。"""
    from app.services.fund_factors import FundFactorInput
    from app.services.portfolio_risk_metrics import _max_drawdown

    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1] > 0]
    mdd = _max_drawdown(rets) * 100.0 if rets else None
    return FundFactorInput(
        fund_code=code,
        fund_name=name,
        return_3m_percent=window_return_percent(navs, 60),
        return_6m_percent=window_return_percent(navs, 120),
        return_1y_percent=window_return_percent(navs, 250),
        max_drawdown_1y_percent=mdd,
        fund_scale_yi=None,
    )
