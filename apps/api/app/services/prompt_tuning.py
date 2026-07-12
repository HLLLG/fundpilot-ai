from __future__ import annotations

from app.services.recommendation_accuracy import build_recommendation_accuracy


def resolve_accuracy_tuning(*, lookback_reports: int = 30) -> dict:
    """读取实验性 T+N 复盘；当前硬禁用自动调参（不含板块回测）。"""
    accuracy = build_recommendation_accuracy(limit_reports=lookback_reports)
    # T+N 分母虽已重建，但基准、费率与足量样本校准尚未完成，不能进入自动
    # 决策。这里是配置开关之外的第二道硬门：即使有人显式把
    # FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED 打开，也必须由评价器明确声明 eligible
    # 才能生成调参提示；字段缺失同样按不合格处理。
    if accuracy.get("auto_tuning_eligible") is not True:
        return {
            "tighten_tactical": False,
            "reason": accuracy.get("warning"),
            "hints": [],
            "stats": {
                "disabled": True,
                "disabled_reason": "accuracy_not_eligible",
                "metric_status": accuracy.get("metric_status"),
                "paired_days": accuracy.get("paired_days"),
            },
        }
    tactical = (accuracy.get("by_style") or {}).get("tactical") or {}
    reversal = tactical.get("reversal") or {}

    up_then_down = int(reversal.get("up_then_down_count") or 0)
    aggressive_miss = int(reversal.get("up_then_down_aggressive_miss") or 0)
    tighten = False
    reason = None

    if up_then_down >= 2 and aggressive_miss / max(up_then_down, 1) >= 0.5:
        tighten = True
        reason = (
            f"近 {accuracy.get('paired_days', 0)} 组日报中，战术模式在 "
            f"{up_then_down} 次涨后回吐场景里有 {aggressive_miss} 次前日为追涨加仓，"
            "系统已自动收紧战术措辞。"
        )

    hints: list[str] = []
    if tighten:
        hints.extend(
            [
                "涨后回吐场景命中率偏低：战术模式下一交易日默认优先观察/减仓评估，慎用追涨加仓。",
                "若 sector_momentum=two_day_reversal_down 或 sector_intraday=intraday_pullback，禁止给出加仓类 action。",
            ]
        )

    return {
        "tighten_tactical": tighten,
        "reason": reason,
        "hints": hints,
        "stats": {
            "up_then_down_count": up_then_down,
            "aggressive_miss": aggressive_miss,
            "paired_days": accuracy.get("paired_days"),
        },
    }


def resolve_prompt_tuning_hints(*, lookback_reports: int = 30) -> dict:
    """兼容旧调用：仅使用实验性日报 T+N 复盘统计（自动调参仍关闭）。"""
    return resolve_accuracy_tuning(lookback_reports=lookback_reports)
