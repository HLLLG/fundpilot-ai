from __future__ import annotations

"""二次 LLM 审校的运行状况摘要（`llm_judge_digest`）。

**为什么需要它**：`report_judge` / `discovery_judge` 的二次审校在 shadow 期是在发请求
**之前**直接短路的（`llm_judge_skipped_reason="decision_escalation_shadow"`），所以生产
从未真正执行过一次。切到 enforced 之后它第一次开始跑，而且每份深度报告都会多一次模型
调用与延迟。它自带预算，超时会静默降级放过——这意味着"每次都超时、每次都白花钱"和
"正常工作"在外部看起来完全一样。这份摘要就是把这两种情况区分开。

四个必须能回答的问题：

1. **它到底跑没跑**——`attempted` 占"有资格跑的报告"的比例；
2. **是不是每次都超时**——`timeout` 占 `attempted` 的比例；
3. **跑了有没有改动什么**——`applied` 占 `attempted` 的比例；
4. **没跑的话为什么**——`skipped_reason` 分布。

**分母必须是"有资格的报告"，不是全部报告。** fast 模式根本不调用审校
（`analysis_mode != "deep"` 时两个 judge 都直接返回），未配置 deepseek 同理。把 fast
报告算进分母，会让"50 份报告 0 次尝试"看起来像坏了，其实是压根不该跑。这与
`shadow_escalation_digest` 用 `decision_eligible` 划分母、以及峰谷回撤守卫要求
`confidence` 达标是同一条纪律：先说清"这条证据该不该出现"，再谈"它出现了没有"。

数据来源与 `shadow_escalation_digest` 共用 `list_*_decision_diagnostics()`——**不能**改用
`list_reports()`，那条路会把 `analysis_facts` 整个投影掉（见 `database.py` 注释）。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.database import (
    list_discovery_report_decision_diagnostics,
    list_report_decision_diagnostics,
)

DEFAULT_LOOKBACK_DAYS = 7
LLM_JUDGE_DIGEST_SCHEMA_VERSION = "llm_judge_digest.v1"

# 只有深度模式才会调用二次审校，fast 模式在两个 judge 入口就返回了。
_JUDGE_ELIGIBLE_ANALYSIS_MODE = "deep"


def build_llm_judge_digest(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    reports: list[dict[str, Any]] | None = None,
    discovery_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """聚合近 `lookback_days` 天内日报 + 荐基两条链路的二次审校执行情况。"""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    all_reports = (
        reports if reports is not None else list_report_decision_diagnostics()
    )
    all_discovery = (
        discovery_reports
        if discovery_reports is not None
        else list_discovery_report_decision_diagnostics()
    )

    report_surface = _summarize_surface(
        [row for row in all_reports if _within_window(row, cutoff)],
        facts_key="analysis_facts",
    )
    discovery_surface = _summarize_surface(
        [row for row in all_discovery if _within_window(row, cutoff)],
        facts_key="discovery_facts",
    )
    combined = _combine(report_surface, discovery_surface)

    return {
        "available": True,
        "schema_version": LLM_JUDGE_DIGEST_SCHEMA_VERSION,
        "lookback_days": lookback_days,
        # 审校是否可能发生，取决于这两项配置；摘要必须自带上下文，否则"0 次尝试"无法解读。
        "decision_escalation_mode": settings.decision_escalation_mode,
        "deepseek_configured": bool(settings.deepseek_configured),
        "report": report_surface,
        "discovery": discovery_surface,
        "combined": combined,
        "summary": _build_summary(
            lookback_days=lookback_days,
            escalation_mode=settings.decision_escalation_mode,
            combined=combined,
        ),
    }


def _within_window(row: object, cutoff: datetime) -> bool:
    if not isinstance(row, dict):
        return False
    parsed = _parse_datetime(row.get("created_at"))
    return parsed is not None and parsed >= cutoff


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _pipeline_of(row: dict[str, Any], facts_key: str) -> dict[str, Any]:
    facts = row.get(facts_key)
    facts_map = facts if isinstance(facts, dict) else {}
    pipeline = facts_map.get("pipeline")
    return pipeline if isinstance(pipeline, dict) else {}


def _analysis_mode_of(row: dict[str, Any], pipeline: dict[str, Any]) -> str:
    # pipeline 里的 analysis_mode 是生成时的权威值；报告顶层那份用于兼容更早的记录。
    return str(pipeline.get("analysis_mode") or row.get("analysis_mode") or "")


def _summarize_surface(
    rows: list[dict[str, Any]],
    *,
    facts_key: str,
) -> dict[str, Any]:
    report_count = len(rows)
    eligible = 0
    attempted = 0
    applied = 0
    timed_out = 0
    skipped_reasons: dict[str, int] = {}
    missing_telemetry = 0

    for row in rows:
        pipeline = _pipeline_of(row, facts_key)
        if _analysis_mode_of(row, pipeline) != _JUDGE_ELIGIBLE_ANALYSIS_MODE:
            continue
        eligible += 1
        # 老报告在 `llm_judge_timeout` / `llm_judge_skipped_reason` 落库之前生成，
        # 单独计数而不是当成 False，否则会把"没记录"读成"没超时"。
        if "llm_judge_timeout" not in pipeline:
            missing_telemetry += 1
        if pipeline.get("llm_judge_attempted") is True:
            attempted += 1
            if pipeline.get("llm_judge_applied") is True:
                applied += 1
            if pipeline.get("llm_judge_timeout") is True:
                timed_out += 1
            continue
        reason = pipeline.get("llm_judge_skipped_reason")
        key = str(reason) if reason else "unrecorded"
        skipped_reasons[key] = skipped_reasons.get(key, 0) + 1

    return {
        "report_count": report_count,
        "judge_eligible_count": eligible,
        "attempted_count": attempted,
        "applied_count": applied,
        "timeout_count": timed_out,
        "attempt_rate_percent": _rate(attempted, eligible),
        "timeout_rate_percent": _rate(timed_out, attempted),
        "applied_rate_percent": _rate(applied, attempted),
        "skipped_reasons": dict(
            sorted(skipped_reasons.items(), key=lambda item: item[1], reverse=True)
        ),
        "reports_without_judge_telemetry": missing_telemetry,
    }


def _combine(*surfaces: dict[str, Any]) -> dict[str, Any]:
    totals = {
        key: sum(int(surface.get(key) or 0) for surface in surfaces)
        for key in (
            "report_count",
            "judge_eligible_count",
            "attempted_count",
            "applied_count",
            "timeout_count",
            "reports_without_judge_telemetry",
        )
    }
    merged_reasons: dict[str, int] = {}
    for surface in surfaces:
        for reason, count in (surface.get("skipped_reasons") or {}).items():
            merged_reasons[reason] = merged_reasons.get(reason, 0) + int(count)
    return {
        **totals,
        "attempt_rate_percent": _rate(
            totals["attempted_count"], totals["judge_eligible_count"]
        ),
        "timeout_rate_percent": _rate(
            totals["timeout_count"], totals["attempted_count"]
        ),
        "applied_rate_percent": _rate(
            totals["applied_count"], totals["attempted_count"]
        ),
        "skipped_reasons": dict(
            sorted(merged_reasons.items(), key=lambda item: item[1], reverse=True)
        ),
        "health": _health(totals),
    }


def _health(totals: dict[str, int]) -> str:
    """给出一个可直接读的结论，而不是让人自己算比率。

    `degraded_always_timeout` 是最需要被看见的状态：审校每次都发了请求、每次都超时降级，
    确定性 guard 仍然兜底所以结果看起来正常，但每份报告都在白付一次调用的钱和延迟。
    """
    eligible = totals["judge_eligible_count"]
    attempted = totals["attempted_count"]
    timed_out = totals["timeout_count"]
    if eligible == 0:
        return "no_eligible_reports"
    if attempted == 0:
        return "never_attempted"
    if timed_out == attempted:
        return "degraded_always_timeout"
    if timed_out * 2 >= attempted:
        return "degraded_frequent_timeout"
    return "healthy"


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 2)


_HEALTH_TEXT = {
    "no_eligible_reports": "窗口内没有深度模式报告，二次审校本就不会运行",
    "never_attempted": "有深度模式报告但审校一次都没发起",
    "degraded_always_timeout": "审校每次都超时降级——确定性 guard 仍在兜底，但每份报告都白付一次调用",
    "degraded_frequent_timeout": "审校超时比例偏高",
    "healthy": "审校正常运行",
}


def _build_summary(
    *,
    lookback_days: int,
    escalation_mode: str,
    combined: dict[str, Any],
) -> str:
    parts = [
        f"近 {lookback_days} 天共 {combined['report_count']} 份报告，"
        f"其中 {combined['judge_eligible_count']} 份有资格触发二次审校"
    ]
    if combined["judge_eligible_count"]:
        parts.append(
            f"实际发起 {combined['attempted_count']} 次、"
            f"超时降级 {combined['timeout_count']} 次、"
            f"真正改写结果 {combined['applied_count']} 次"
        )
    if escalation_mode != "enforced":
        parts.append(
            f"当前 decision_escalation_mode={escalation_mode}，"
            "审校在发请求前即短路，不会产生模型调用"
        )
    parts.append(_HEALTH_TEXT.get(str(combined.get("health")), "状态未知"))
    return "；".join(parts) + "。"


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "LLM_JUDGE_DIGEST_SCHEMA_VERSION",
    "build_llm_judge_digest",
]
