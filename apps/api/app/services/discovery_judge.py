from __future__ import annotations

"""M4（决策更准更果断升级）：荐基 deep 模式风控复核角色。

对齐日报 `report_judge.py` 的 M3.2 设计（同一套"风控经理二次复核"思路），但荐基语义
不同——没有"减仓/清仓已持仓"的概念，复核结果只允许改变候选与动作；金额始终由
确定性 allocator 统一计算，任何 LLM 复核金额都会被忽略。

与日报 M3.1 相同的产品定位：fast 模式完全不调用本模块（零新增 LLM 调用）；调用方
（`DiscoveryClient.generate_report` / `discovery_streaming.stream_discovery`）只在
`analysis_mode == "deep"` 时才调用 `judge_parsed_discovery_report`。

无论这层 LLM 复核是否生效（超时/失败/未纠正到位），`apply_discovery_guards()`（M4
规则版）后续仍会用 `resolve_discovery_escalation()` 再校验一遍并强制剔除/限额——
真正的风控红线始终在规则 guard，这层 LLM 复核是"锦上添花的更聪明复核"，与日报
`report_judge.py` 的架构定位完全一致。
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import logging

from app.config import get_settings
from app.services.discovery_candidate_llm import slim_candidate_pool_for_llm
from app.services.decision_guard_shared import resolve_discovery_escalation
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
    get_deepseek_http_client,
)

logger = logging.getLogger(__name__)

LLM_JUDGE_TIMEOUT_SECONDS = 10.0

_RISK_REVIEW_SYSTEM_PROMPT = "你是严谨的基金荐基风控经理，正在复核分析师草拟的候选推荐。"

_RISK_REVIEW_TASK_PROMPT_ENFORCED = (
    "你是风控经理，正在复核荐基分析师草拟的推荐（draft_report.recommendations）。\n"
    "输入：draft_report（草案）+ candidate_pool（候选池，含 fund_quality_score/sector_fit_score）"
    "+ sector_opportunities（板块方向机会分，含 confidence/opportunity_available）"
    "+ escalation_hints（系统已按「板块量价背离信号」与「基金质量分」的共振情况计算出的"
    "剔除/提额建议，键为 fund_code，action 为 exclude 或 boost）。\n"
    "任务：\n"
    "1. 对草案中「建议关注/等待回调」的推荐，检查是否忽视了强多头证据（板块方向机会分高、"
    "sector_opportunities.confidence 为高、基金质量分也高）；若忽视，可考虑升级为「分批买入」，"
    "这是双向校验，不是只能变得更悲观。\n"
    "2. 对草案中「分批买入」的推荐，检查是否忽视了强空头证据（量价背离显著、板块方向不构成"
    "机会、基金质量分同样偏低）；若两项证据共振，须把该推荐从 recommendations 数组中"
    "整条移除——荐基没有「清仓」概念，负向共振的处理方式是移除候选，不是改写动作文字。\n"
    "3. 硬约束：escalation_hints 中 action=exclude 的 fund_code 必须从 recommendations 中"
    "移除；action=boost 只能影响是否升级为分批买入及解释，suggested_amount_yuan 必须为 null，"
    "金额由服务端确定性 allocator 统一计算。\n"
    "仅输出完整 JSON，结构同 draft_report（title、summary、market_view、recommendations、"
    "caveats），字段名与 draft_report 完全一致，不要新增或删除字段。"
)

# M6：灰度（shadow）期间的复核任务 prompt——与日报 report_judge.py 同一套灰度处理
# 思路：任务 1/2 的双向校验质量检查照常保留，任务 3 的"必须移除/必须提额"硬约束
# 降级为不具约束力的参考信息，避免模型自行遵照 escalation_hints 把候选剔除或改动
# 金额，导致 shadow 模式名不副实（真正的剔除仍完全由 discovery_guard.py 的规则层
# 按 FUND_AI_DECISION_ESCALATION_MODE 控制，这里只是不让 LLM 抢先执行）。
_RISK_REVIEW_TASK_PROMPT_SHADOW = (
    "你是风控经理，正在复核荐基分析师草拟的推荐（draft_report.recommendations）。\n"
    "输入：draft_report（草案）+ candidate_pool（候选池，含 fund_quality_score/sector_fit_score）"
    "+ sector_opportunities（板块方向机会分，含 confidence/opportunity_available）"
    "+ escalation_hints（系统按「板块量价背离信号」与「基金质量分」的共振情况计算出的、"
    "「若启用新版守卫后会建议的」剔除/提额判定，键为 fund_code，action 为 exclude 或"
    "boost——当前处于灰度观察期，这些判定仅供参考，不是必须执行的操作）。\n"
    "任务：\n"
    "1. 对草案中「建议关注/等待回调」的推荐，检查是否忽视了强多头证据（板块方向机会分高、"
    "sector_opportunities.confidence 为高、基金质量分也高）；若忽视，可考虑升级为「分批买入」，"
    "这是双向校验，不是只能变得更悲观。\n"
    "2. 对草案中「分批买入」的推荐，检查是否忽视了强空头证据（量价背离显著、板块方向不构成"
    "机会、基金质量分同样偏低），可在 points/risks 里如实提示，但不必移除该推荐。\n"
    "3. 当前是灰度观察期：escalation_hints 仅作参考信息，不是硬约束，"
    "不要求把 action=exclude 的 fund_code 从 recommendations 移除；action=boost 也不得"
    "改写金额，suggested_amount_yuan 保持 null。正常按你的专业判断输出即可。\n"
    "仅输出完整 JSON，结构同 draft_report（title、summary、market_view、recommendations、"
    "caveats），字段名与 draft_report 完全一致，不要新增或删除字段。"
)


def judge_parsed_discovery_report(
    parsed: dict,
    *,
    candidate_pool: list[dict],
    discovery_facts: dict,
    analysis_mode: str,
) -> tuple[dict, dict]:
    """对 LLM 生成的荐基 draft 报告做可选 LLM 风控复核（仅 deep 模式）。

    返回 `(reviewed_or_original_parsed, meta)`；`meta` 结构对齐 `report_judge.
    judge_parsed_report` 的返回（`llm_judge_attempted`/`llm_judge_applied`/
    `llm_judge_timeout`），供调用方写入 `discovery_facts.pipeline` 之类的诊断字段
    （M5 前端展示阶段可复用同一套字段名，此处先只保证语义一致）。
    """
    meta = {
        "llm_judge_attempted": False,
        "llm_judge_applied": False,
        "llm_judge_timeout": False,
    }
    if analysis_mode != "deep" or not get_settings().deepseek_configured:
        return parsed, meta
    if not isinstance(parsed.get("recommendations"), list):
        return parsed, meta
    # M6 安全边界：shadow 期间只观察确定性 escalation_hints，不允许二次
    # LLM 复核改写 action、候选集合或建议金额。调用前短路，避免无效费用与延迟。
    if get_settings().decision_escalation_mode != "enforced":
        meta["llm_judge_skipped_reason"] = "decision_escalation_shadow"
        return parsed, meta

    escalation_hints = _escalation_hints_by_fund_code(candidate_pool, discovery_facts)
    meta["llm_judge_attempted"] = True
    reviewed, timed_out = _llm_judge_with_budget(
        parsed, candidate_pool, discovery_facts, escalation_hints
    )
    meta["llm_judge_timeout"] = timed_out
    if reviewed is not parsed and isinstance(reviewed.get("recommendations"), list):
        meta["llm_judge_applied"] = True
        return reviewed, meta
    return parsed, meta


def _escalation_hints_by_fund_code(
    candidate_pool: list[dict], discovery_facts: dict
) -> dict[str, dict]:
    """为候选池里每只基金预先算好 M4 的剔除/提额判定，随 draft 一起喂给风控复核角色，
    让它有具体信号可以对照（而不是空泛地要求"检查证据"）。复用与 `discovery_guard.py`
    完全一致的判定入口，避免规则 guard 与 LLM 复核提示对同一只基金给出不同结论。"""
    opportunity_by_label: dict[str, dict] = {
        str(item.get("sector_label") or "").strip(): item
        for item in discovery_facts.get("sector_opportunities") or []
        if isinstance(item, dict) and str(item.get("sector_label") or "").strip()
    }
    result: dict[str, dict] = {}
    for item in candidate_pool:
        if not isinstance(item, dict):
            continue
        code = str(item.get("fund_code") or "").strip().zfill(6)
        if not code:
            continue
        sector_label = str(item.get("sector_label") or "").strip()
        opportunity = opportunity_by_label.get(sector_label)
        escalation = resolve_discovery_escalation(sector_opportunity=opportunity, pool_item=item)
        if escalation.get("action"):
            result[code] = escalation
    return result


def _llm_judge_with_budget(
    parsed: dict,
    candidate_pool: list[dict],
    discovery_facts: dict,
    escalation_hints: dict[str, dict],
) -> tuple[dict, bool]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery-judge")
    future = executor.submit(_llm_judge, parsed, candidate_pool, discovery_facts, escalation_hints)
    try:
        return future.result(timeout=LLM_JUDGE_TIMEOUT_SECONDS), False
    except FutureTimeoutError:
        future.cancel()
        logger.warning(
            "discovery llm judge timed out after %.1fs, using draft report",
            LLM_JUDGE_TIMEOUT_SECONDS,
        )
        return parsed, True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _llm_judge(
    parsed: dict,
    candidate_pool: list[dict],
    discovery_facts: dict,
    escalation_hints: dict[str, dict],
) -> dict:
    settings = get_settings()
    from app.services.analysis_payload import compact_discovery_draft_report_for_llm

    task_prompt = (
        _RISK_REVIEW_TASK_PROMPT_ENFORCED
        if settings.decision_escalation_mode == "enforced"
        else _RISK_REVIEW_TASK_PROMPT_SHADOW
    )
    payload = {
        "draft_report": compact_discovery_draft_report_for_llm(parsed),
        "candidate_pool": slim_candidate_pool_for_llm(
            candidate_pool,
            sector_heat=discovery_facts.get("sector_heat") or [],
            trade_date=(discovery_facts.get("session") or {}).get(
                "effective_trade_date"
            ),
        ),
        "sector_opportunities": discovery_facts.get("sector_opportunities") or [],
        "escalation_hints": escalation_hints,
        "task": task_prompt,
    }
    try:
        response = get_deepseek_http_client(settings).post(
            deepseek_chat_url(settings),
            headers=deepseek_request_headers(settings),
            json={
                "model": settings.deepseek_model_fast,
                "messages": [
                    {"role": "system", "content": _RISK_REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0.1,
                "max_tokens": min(settings.deepseek_max_tokens_report, 8000),
                "response_format": {"type": "json_object"},
            },
            timeout=deepseek_timeout(settings),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        from app.services.deepseek_client import _parse_model_json

        reviewed = _parse_model_json(content)
        if isinstance(reviewed.get("recommendations"), list):
            return reviewed
    except Exception as exc:
        logger.warning("discovery llm judge failed, using draft report: %s", exc)
    return parsed
