from __future__ import annotations

from app.models import FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.holding_metrics import (
    compute_estimated_daily_return_percent,
    holding_daily_return_is_estimated,
)


def build_analysis_facts(
    holdings: list[Holding],
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    profile: InvestorProfile,
) -> dict:
    total_amount = sum(item.holding_amount for item in holdings) or 0.0
    snapshot_by_code = {item.fund_code: item for item in snapshots}

    per_fund: list[dict] = []
    for holding in holdings:
        weight = (holding.holding_amount / total_amount * 100) if total_amount else 0.0
        estimated_daily = compute_estimated_daily_return_percent(holding)
        snapshot = snapshot_by_code.get(holding.fund_code)
        per_fund.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "holding_amount": round(holding.holding_amount, 2),
                "weight_percent": round(weight, 2),
                "holding_return_percent": holding.holding_return_percent
                if holding.holding_return_percent is not None
                else holding.return_percent,
                "sector_return_percent": holding.sector_return_percent,
                "daily_return_percent": holding.daily_return_percent,
                "estimated_daily_return_percent": estimated_daily,
                "daily_return_is_estimated": holding_daily_return_is_estimated(holding),
                "daily_profit": holding.daily_profit,
                "holding_profit": holding.holding_profit,
                "sector_name": holding.sector_name,
                "over_concentration": weight > profile.concentration_limit_percent,
                "latest_nav": snapshot.latest_nav if snapshot else None,
                "nav_date": snapshot.nav_date if snapshot else None,
                "fund_type": snapshot.fund_type if snapshot else None,
                "return_1y_percent": snapshot.return_1y_percent if snapshot else None,
                "max_drawdown_1y_percent": snapshot.max_drawdown_1y_percent if snapshot else None,
                "management_fee": snapshot.management_fee if snapshot else None,
                "fund_scale_yi": snapshot.fund_scale_yi if snapshot else None,
            }
        )

    return {
        "readonly": True,
        "instruction": "以下数字由系统计算，分析时不得改写；仅可基于它们做解释与建议。",
        "portfolio": {
            "total_amount": round(total_amount, 2),
            "holding_count": len(holdings),
            "weighted_return_percent": risk.weighted_return_percent,
            "risk_level": risk.level,
            "suggested_action": risk.suggested_action,
            "max_drawdown_limit_percent": profile.max_drawdown_percent,
            "concentration_limit_percent": profile.concentration_limit_percent,
        },
        "alerts": [alert.model_dump() for alert in risk.alerts],
        "holdings": per_fund,
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
