from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import logging

import httpx

from app.config import get_settings
from app.services.analysis_facts import build_analysis_facts  # noqa: F401  # 测试 patch 此符号
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_http import (
    deepseek_chat_url,
    deepseek_request_headers,
    deepseek_timeout,
)
from app.models import AnalysisRequest, FundSnapshot, RiskAssessment
from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    classify_action_bucket as _action_bucket,
)
from app.services.recommendation_guard import normalize_action_text

logger = logging.getLogger(__name__)

LLM_JUDGE_TIMEOUT_SECONDS = 10.0


def judge_parsed_report(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    snapshots: list[FundSnapshot],
    runtime: AnalysisRuntime,
    *,
    facts: dict,
) -> tuple[dict, dict]:
    """对 LLM 生成的 draft 报告做规则 + 可选 LLM 审校。

    facts 必填，由上游 prepare_analysis_bundle 计算并传入；judge 内部不再重算
    build_analysis_facts，深度模式可省 5~10s、快速可省 1~3s。

    M3.2（决策更准更果断升级）：deep 模式下的 LLM 审校角色由"单纯对齐 facts 的
    校对员"升级为"风控经理二次复核"——除了修正数字/风控矛盾，还要求它主动检查
    草案是否忽视了强空头/强多头证据（双向校验，不是只能变得更悲观），并把 M2.1
    `resolve_escalation_floor()` 算出的"系统最低动作档位"作为硬约束喂给它，要求
    最终动作不得比该档位更宽松。注意：即使 LLM 审校环节被跳过/超时/未纠正到位，
    `_build_final_report` 之后仍会重新调用 `apply_recommendation_guards()`——
    该 guard 内部同样调 `resolve_escalation_floor()` 强制封顶，所以这层 LLM 复核
    是"锦上添花的更聪明的复核"，不是唯一的风控红线（真正的硬约束在规则 guard）。
    """
    judged = _rule_judge(parsed, request, risk, facts)
    meta = {
        "rule_judge": True,
        "llm_judge_attempted": False,
        "llm_judge_applied": False,
        "llm_judge_timeout": False,
    }
    if runtime.mode != "deep" or not get_settings().deepseek_configured:
        return judged, meta
    # M6 安全边界：shadow 的含义是“只观察确定性 escalation 提示，不让二次
    # LLM 复核改变最终决策”。在发起请求前直接短路，既不依赖 Prompt 自觉，也
    # 避免产生一笔注定不会应用的模型调用与延迟。
    if get_settings().decision_escalation_mode != "enforced":
        meta["llm_judge_skipped_reason"] = "decision_escalation_shadow"
        return judged, meta
    meta["llm_judge_attempted"] = True
    escalation_floors = _escalation_floor_by_fund_code(judged, facts)
    reviewed, timed_out = _llm_judge_with_budget(judged, facts, escalation_floors)
    meta["llm_judge_timeout"] = timed_out
    if reviewed is not judged and reviewed.get("fund_recommendations"):
        meta["llm_judge_applied"] = True
        return reviewed, meta
    return judged, meta


def _escalation_floor_by_fund_code(parsed: dict, facts: dict) -> dict[str, dict]:
    """为每只持仓预先算好 M2.1 的最低动作档位，随 draft 一起喂给风控复核角色，
    让它有一个具体的、系统计算的"红线"可以对照，而不是空泛地要求"检查风控"。

    facts 里的持仓行若已经在 `analysis_facts._attach_escalation_to_holdings` 中
    挂了 `escalation` 字段（fast/deep 均会挂），直接复用即可，无需重复计算。
    """
    result: dict[str, dict] = {}
    for row in facts.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or "").strip()
        escalation = row.get("escalation")
        if code and isinstance(escalation, dict) and escalation.get("min_bucket") is not None:
            result[code] = escalation
    return result


def _llm_judge_with_budget(
    parsed: dict, facts: dict, escalation_floors: dict[str, dict]
) -> tuple[dict, bool]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-judge")
    future = executor.submit(_llm_judge, parsed, facts, escalation_floors)
    try:
        return future.result(timeout=LLM_JUDGE_TIMEOUT_SECONDS), False
    except FutureTimeoutError:
        future.cancel()
        logger.warning("llm judge timed out after %.1fs, using rule-judged report", LLM_JUDGE_TIMEOUT_SECONDS)
        return parsed, True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _rule_judge(
    parsed: dict,
    request: AnalysisRequest,
    risk: RiskAssessment,
    facts: dict,
) -> dict:
    weight_by_code = {
        item["fund_code"]: item["weight_percent"]
        for item in facts.get("holdings") or []
    }
    allowed = set(facts.get("allowed_actions") or [])

    raw_recs = parsed.get("fund_recommendations")
    if not isinstance(raw_recs, list):
        return parsed

    fixed_recs: list[dict] = []
    for entry in raw_recs:
        if not isinstance(entry, dict):
            continue
        copy = dict(entry)
        action = normalize_action_text(str(copy.get("action", "观察")))
        if action not in allowed:
            action = "观察"
        code = str(copy.get("fund_code", "")).strip()
        if risk.suggested_action == "risk_review" and _action_bucket(action) >= ACTION_BUCKET_ADD:
            action = "暂停追涨"
        if code in weight_by_code and weight_by_code[code] > request.profile.concentration_limit_percent:
            if _action_bucket(action) >= ACTION_BUCKET_ADD:
                action = "减仓评估"
        copy["action"] = action
        fixed_recs.append(copy)

    copy_parsed = dict(parsed)
    copy_parsed["fund_recommendations"] = fixed_recs

    summary = str(copy_parsed.get("summary", ""))
    if risk.suggested_action == "risk_review" and "加仓" in summary and "不宜" not in summary:
        copy_parsed["summary"] = (
            f"{summary}\n\n（系统复核：组合处于风险复核状态，今日不宜新增加仓。）"
        ).strip()

    return copy_parsed


_RISK_REVIEW_SYSTEM_PROMPT = "你是严谨的基金日报风控经理，正在复核基金经理草拟的日报建议。"

_RISK_REVIEW_TASK_PROMPT_ENFORCED = (
    "你是风控经理，正在复核基金经理草拟的日报建议（draft_report.fund_recommendations）。\n"
    "输入：draft_report（草案）+ facts（含量价背离回测 flow_divergence_backtest、"
    "大盘情绪 market_breadth、三路量化证据 evidence 等）+ escalation_floors"
    "（系统已按 facts 计算出的每只基金的最低动作档位，键为 fund_code）。\n"
    "任务：\n"
    "1. 对草案中「观察/分批加仓」的建议，检查是否忽视了强空头证据"
    "（量价背离显著、板块方向不构成机会、情绪骤冷、量化证据背书不足）；"
    "若忽视，须把该基金的 action 调整为至少与 escalation_floors 里对应的"
    "min_action_label 一样保守，并在 points 里补充理由。\n"
    "2. 对草案中「减仓评估」及以上的保守建议，检查是否有被忽视的强多头证据"
    "（板块方向机会分高、量化证据综合置信高、当日/5日主力资金净流入），"
    "避免反向过度悲观——这是双向校验，不是只能变得更悲观。\n"
    "3. 硬约束：若某基金在 escalation_floors 中出现，最终 action 对应的保守程度"
    "不得低于（即不得比）其 min_action_label 更宽松；risk_review 时仍禁止加仓类 action。\n"
    "仅输出完整 JSON，结构同 draft_report（title、summary、fund_recommendations、caveats），"
    "字段名与 draft_report 完全一致，不要新增或删除字段。"
)

# M6：灰度（shadow）期间的复核任务 prompt——与 enforced 版本共享任务 1/2（双向校验
# 本身是"复核质量"问题，不是"是否生效"问题，灰度期同样希望模型认真校验），但任务 3
# 的硬约束改为不具约束力的参考信息，避免模型"自觉遵守"escalation_floors 而绕开
# shadow 模式"先观察、不真的动"的本意——如果只挡住规则层（recommendation_guard.py）
# 而不挡住这里的措辞，deep 模式下最终展示的 action 仍可能因为模型自己听话而改变，
# shadow 模式就变得名不副实。
_RISK_REVIEW_TASK_PROMPT_SHADOW = (
    "你是风控经理，正在复核基金经理草拟的日报建议（draft_report.fund_recommendations）。\n"
    "输入：draft_report（草案）+ facts（含量价背离回测 flow_divergence_backtest、"
    "大盘情绪 market_breadth、三路量化证据 evidence 等）+ escalation_floors"
    "（系统按 facts 计算出的、每只基金"
    "「若启用新版守卫后会建议的」最低动作档位，键为 fund_code——当前处于灰度观察期，"
    "这些档位仅供参考，不是必须遵守的约束）。\n"
    "任务：\n"
    "1. 对草案中「观察/分批加仓」的建议，检查是否忽视了强空头证据"
    "（量价背离显著、板块方向不构成机会、情绪骤冷、量化证据背书不足）；"
    "若忽视，可参考 escalation_floors 里的 min_action_label 调整 action，"
    "并在 points 里补充理由（这是复核质量的建议，不是强制要求）。\n"
    "2. 对草案中「减仓评估」及以上的保守建议，检查是否有被忽视的强多头证据"
    "（板块方向机会分高、量化证据综合置信高、当日/5日主力资金净流入），"
    "避免反向过度悲观——这是双向校验，不是只能变得更悲观。\n"
    "3. 当前是灰度观察期：escalation_floors 仅作参考信息，不是硬约束，"
    "不要求最终 action 必须对齐 min_action_label；正常按你的专业判断给出 action 即可，"
    "risk_review 时仍应避免加仓类 action。\n"
    "仅输出完整 JSON，结构同 draft_report（title、summary、fund_recommendations、caveats），"
    "字段名与 draft_report 完全一致，不要新增或删除字段。"
)


def _llm_judge(
    parsed: dict,
    facts: dict,
    escalation_floors: dict[str, dict] | None = None,
) -> dict:
    settings = get_settings()
    task_prompt = (
        _RISK_REVIEW_TASK_PROMPT_ENFORCED
        if settings.decision_escalation_mode == "enforced"
        else _RISK_REVIEW_TASK_PROMPT_SHADOW
    )
    payload = {
        "facts": facts,
        "draft_report": parsed,
        "escalation_floors": escalation_floors or {},
        "task": task_prompt,
    }
    try:
        response = httpx.post(
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
        if reviewed.get("fund_recommendations"):
            return reviewed
    except Exception as exc:
        logger.warning("llm judge failed, using rule-judged report: %s", exc)
    return parsed
