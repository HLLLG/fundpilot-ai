"""组合风险度量置信（模块4 竖切4）。

风险指标（夏普/回撤/Beta/HHI）的可信度本质是「历史够不够长」——样本充足度，
区别于信号(跑赢基线)/因子(IC 显著)。纯函数，消费模块1 PortfolioRiskMetrics 的
asdict（含 available + sample_days）。

现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / 金融评估与路径风险」。
"""
from __future__ import annotations

RISK_SAMPLE_HIGH = 120
RISK_SAMPLE_MEDIUM = 60
RISK_SAMPLE_MIN = 20  # 与模块1 portfolio_risk_metrics.MIN_SAMPLE_DAYS 对齐


def risk_metrics_confidence(metrics: dict | None) -> dict:
    """按样本交易日数映射 {level, basis}。"""
    if not metrics or not metrics.get("available"):
        return {"level": "不足", "basis": f"历史样本不足 {RISK_SAMPLE_MIN} 交易日，风险指标暂不可用"}

    n = int(metrics.get("sample_days") or 0)
    if n < RISK_SAMPLE_MIN:
        return {"level": "不足", "basis": f"历史样本不足 {RISK_SAMPLE_MIN} 交易日，风险指标暂不可用"}
    if n < RISK_SAMPLE_MEDIUM:
        return {"level": "低", "basis": f"仅 {n} 交易日样本，指标较毛糙，置信低"}
    if n < RISK_SAMPLE_HIGH:
        return {"level": "中", "basis": f"{n} 交易日样本，置信中"}
    return {"level": "高", "basis": f"{n} 交易日样本，置信高"}
