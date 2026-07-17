from __future__ import annotations

import re
from math import isfinite

from app.config import get_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    NewsItem,
    RiskAssessment,
    TopicBrief,
)
from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_LABELS as _BUCKET_TO_LABEL,
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
    ACTION_BUCKET_WATCH,
    append_unique as _append_unique,
    classify_action_bucket as _action_bucket,
    escalation_severity_rank as _escalation_severity_rank,
    fmt_num as _fmt_num,
    humanize_evidence_text as _humanize_evidence_text,
    normalize_confidence_label as _normalize_confidence,
    pattern_label as _pattern_label,
    resolve_escalation_floor,
    track_label as _track_label,
)
from app.services.market_signal import has_today_market_signal
from app.services.investment_presets import is_short_term_style
from app.services.signal_guard_policy import resolve_signal_guard_policy
from app.services.recommendations import (
    build_offline_fund_recommendation,
    suggest_trade_amount,
)
from app.services.risk import holding_weight_percent, resolve_weight_denominator
from app.services.sector_intraday_summary import summarize_sector_intraday_for_holding
from app.services.sector_momentum import build_sector_momentum_context
from app.services.daily_tradeability import (
    assess_holding_add_amount,
    build_holding_transaction_execution,
)

# 动作激进度 bucket：数值越低越保守。M2.2 起统一委托给
# decision_guard_shared.classify_action_bucket()（清仓评估=-2 < 大幅减仓评估=-1 <
# 减仓评估=0 < 观察=1 < 暂停追涨=2 < 分批加仓=3），本文件不再维护独立判定逻辑，
# 避免与 decision_guard_shared.py / report_judge.py 三处口径漂移。


_REPORT_HUMANIZE_TEXT_REPLACEMENTS = (
    ("sector_opportunity", "持仓板块方向判断"),
    ("sector_rotation", "板块轮动参考"),
    ("market_top", "更强轮动方向"),
    ("opportunity_available", "机会是否成立"),
    ("factor_reliability", "因子置信"),
    ("risk_metrics", "组合风险指标"),
    ("evidence_overview", "组合证据体检"),
)
_VALID_EVIDENCE_SOURCES = frozenset({"factor", "signal", "risk"})
_VALID_EVIDENCE_LEVELS = frozenset({"高", "中", "低", "不足"})
_VALID_IC_STATES = frozenset({"available", "unavailable", "stale"})


def apply_recommendation_guards(
    fund_recs: list[FundRecommendation],
    portfolio_lines: list[str],
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
    facts: dict | None = None,
) -> tuple[list[str], list[FundRecommendation]]:
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1
    offline_map = _offline_by_holding(
        request,
        weight_denominator,
        market_news,
        nav_trends_by_code=nav_trends_by_code,
    )
    settings = get_settings()
    decision_style = request.profile.decision_style
    tactical = decision_style == "tactical"
    aggressive = decision_style == "aggressive"
    short_term = is_short_term_style(decision_style)
    guard_policy = (
        resolve_signal_guard_policy(
            request.holdings,
            lookback_reports=settings.tactical_prompt_tuning_lookback_reports,
            backtest_days=settings.sector_signal_backtest_days,
        )
        if settings.tactical_prompt_tuning_enabled or settings.sector_signal_backtest_enabled
        else {
            "tighten_tactical": False,
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "hints": [],
            "reason": None,
        }
    )
    tuning = guard_policy
    today_signal = has_today_market_signal(market_news, topic_briefs)
    ic_status = _factor_ic_status_from_facts(facts)
    portfolio_snapshot = (facts or {}).get("portfolio_snapshot")
    degraded_portfolio_snapshot = bool(
        isinstance(portfolio_snapshot, dict)
        and (
            portfolio_snapshot.get("stale")
            or not portfolio_snapshot.get("authoritative")
            or portfolio_snapshot.get("position_complete") is False
            or int(portfolio_snapshot.get("pending_transaction_count") or 0) > 0
        )
    )
    from app.services.decision_data_evidence import (
        contains_executable_decision_text,
        decision_evidence_allows_action,
        safe_blocked_points,
    )

    guarded: list[FundRecommendation] = []
    evidence_blocked_codes: dict[str, list[str]] = {}
    for rec in fund_recs:
        original_action = rec.action
        rec = _strip_untrusted_execution_text(rec)
        holding = _match_holding(rec, request.holdings)
        offline = None
        if holding is not None:
            offline = offline_map.get(holding.fund_code) or offline_map.get(holding.fund_name)

        normalized = normalize_action_text(rec.action)
        facts_row = _facts_row_for_holding(facts, holding) if holding is not None else None

        evidence_allowed, evidence_reasons = decision_evidence_allows_action(
            facts,
            scope="analysis",
            fund_code=(holding.fund_code if holding is not None else rec.fund_code),
            direction=_execution_direction(normalized),
        )
        execution_blocked = degraded_portfolio_snapshot or not evidence_allowed
        if execution_blocked:
            evidence_blocked_codes[rec.fund_code] = evidence_reasons

        snapshot_note = None
        if execution_blocked and _action_bucket(normalized) >= ACTION_BUCKET_ADD:
            normalized = "观察"
            snapshot_note = "持仓份额、成本或关键行情还未确认完整且为最新，因此暂不提供加减仓操作。"

        nav_trend = None
        if holding is not None and nav_trends_by_code:
            nav_trend = nav_trends_by_code.get(holding.fund_code)

        reversal_note = None
        if holding is not None and _reversal_signal_block(
            holding,
            nav_trend,
            enforce_reversal=bool(guard_policy.get("enforce_reversal_block", True)),
            enforce_pullback=bool(guard_policy.get("enforce_pullback_block", True)),
        ):
            if _action_bucket(normalized) >= 3 or _action_bucket(rec.action) >= 3:
                if tactical:
                    normalized = "观察"
                    reversal_note = "涨后回吐或盘中冲高回落，战术模式已限制追涨加仓。"
                else:
                    normalized = "暂停追涨"
                    reversal_note = "涨后回吐或盘中冲高回落，已限制追涨加仓（板块短线信号）。"
            elif tactical and tuning.get("tighten_tactical") and _action_bucket(normalized) >= 2:
                normalized = "观察"
                reversal_note = "历史涨后回吐命中率偏低，战术模式已自动收紧：回吐场景优先观察。"

        if offline is not None and not short_term and not reversal_note:
            normalized = conservative_action_text(normalized, offline.action)

        max_bucket = _max_allowed_bucket(
            risk, holding, request, tactical=tactical, aggressive=aggressive
        )
        if _action_bucket(normalized) > max_bucket:
            normalized = _BUCKET_TO_LABEL[max_bucket]

        sector_opportunity = (facts_row or {}).get("sector_opportunity")
        evidence = (facts_row or {}).get("evidence")

        weak_note = None
        if not reversal_note and _action_bucket(normalized) >= ACTION_BUCKET_ADD:
            weak_reasons = _weak_evidence_reasons(sector_opportunity, evidence, ic_status)
            if weak_reasons:
                normalized = "观察"
                max_bucket = min(max_bucket, ACTION_BUCKET_WATCH)
                weak_note = (
                    f"板块或基金证据不足（{'、'.join(weak_reasons)}），"
                    "已将加仓类动作降为「观察」。"
                )

        if (
            not short_term
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(normalized) >= ACTION_BUCKET_ADD
        ):
            normalized = "暂停追涨"
            max_bucket = min(max_bucket, ACTION_BUCKET_PAUSE)

        # M2.1 双向 guard：证据强烈指向风险升级时，即使前面几步的降级仍停在"观察"，
        # 这里作为最终的保守下限强制继续拉低（甚至拉到"减仓评估/大幅减仓评估/清仓评估"）。
        # 这是本次升级要修的核心缺陷——旧 guard 只会把"分批加仓"降到"观察"，
        # 不会在证据极强时进一步升级到减仓类动作。
        escalation = resolve_escalation_floor(
            sector_opportunity=sector_opportunity,
            evidence=evidence,
            market_breadth=(facts or {}).get("market_breadth"),
            over_concentration=bool((facts_row or {}).get("over_concentration")),
            has_unrealized_gain=((facts_row or {}).get("estimated_holding_return_percent") or 0) > 0,
            decision_style=decision_style,
        )
        escalation_note = None
        shadow_note = None
        min_bucket = escalation.get("min_bucket")
        escalation_would_trigger = min_bucket is not None and _escalation_severity_rank(
            _action_bucket(normalized)
        ) > _escalation_severity_rank(min_bucket)
        if escalation_would_trigger:
            would_be_action = _BUCKET_TO_LABEL[min_bucket]
            basis = str(escalation.get("basis") or "")
            if settings.decision_escalation_mode == "enforced":
                previous_action = normalized
                normalized = would_be_action
                escalation_note = (
                    f"量化证据显示风险已升级，系统已将「{previous_action}」上调为「{normalized}」"
                    f"（{basis}）。" if basis else f"量化证据显示风险已升级，系统已将「{previous_action}」上调为「{normalized}」。"
                )
            else:
                # M6：shadow 灰度期——不真正改变最终 action/仓位建议，只记录"若切换
                # enforced 会被系统升级为 XX"到 validation_notes，供
                # shadow_escalation_digest.py 聚合复盘、也供用户在报告详情里看到。
                shadow_note = (
                    f"【灰度提示，未生效】若启用新版守卫（enforced 模式），"
                    f"本条建议会被系统升级为「{would_be_action}」"
                    f"（{basis}）。" if basis else f"【灰度提示，未生效】若启用新版守卫（enforced 模式），本条建议会被系统升级为「{would_be_action}」。"
                )

        # A degraded portfolio cannot support any executable position change,
        # including a reduction inferred from stale weights. Keep only a neutral
        # observation, or a generic risk review when the independent portfolio
        # risk gate is already high.
        if execution_blocked:
            normalized = (
                "风控复核"
                if risk.level == "high" or risk.suggested_action == "risk_review"
                else "观察"
            )
            escalation_note = None
            shadow_note = None

        allowed_actions = {
            str(value).strip()
            for value in (facts or {}).get("allowed_actions") or []
            if str(value).strip()
        }
        if allowed_actions and normalized not in allowed_actions:
            normalized = "观察"
            escalation_note = None
            note_forbidden_action = "该动作不在本轮 allowed_actions 中，系统已降为观察。"
        else:
            note_forbidden_action = None

        (
            normalized,
            approved_amount_yuan,
            tradeability_review_required,
            tradeability_note,
            trusted_tradeability,
            trusted_transaction_execution,
        ) = _apply_holding_tradeability_guard(
            normalized,
            amount_yuan=(
                None
                if execution_blocked
                else (
                    _review_reduction_amount(
                        normalized,
                        holding=holding,
                        weight_denominator=weight_denominator,
                        request=request,
                        escalation=(
                            escalation
                            if settings.decision_escalation_mode == "enforced"
                            else {}
                        ),
                    )
                    if _execution_direction(normalized) == "reduce"
                    else rec.amount_yuan
                )
            ),
            holding=holding,
            facts_row=facts_row,
        )

        note = (
            note_forbidden_action
            or tradeability_note
            or escalation_note
            or snapshot_note
            or reversal_note
            or weak_note
        )
        if (
            not note
            and not short_term
            and settings.news_require_today_for_add
            and not today_signal
            and _action_bucket(rec.action.strip()) >= ACTION_BUCKET_ADD
            and normalized != rec.action.strip()
        ):
            note = "无当日可引用要闻，已限制激进加仓类动作（更贴盘面、防幻觉）。"
        elif not note and offline is not None and not short_term and normalized != rec.action.strip():
            note = f"已按风控规则将「{rec.action.strip()}」调整为「{normalized}」（对照本地规则：{offline.action}）。"
        elif not note and tactical and normalized != rec.action.strip():
            note = f"战术模式下保留模型动作「{normalized}」（未与离线规则取更保守值）。"
        elif not note and aggressive and normalized != rec.action.strip():
            note = f"激进波段模式保留模型动作「{normalized}」（对照离线规则：{offline.action if offline else '—'}）。"
        elif not note and normalized != rec.action.strip():
            note = f"已规范动作表述为「{normalized}」。"

        copy = rec.model_copy(
            update={
                "action": normalized,
                "amount_yuan": approved_amount_yuan,
                "tradeability": trusted_tradeability,
                "transaction_execution": trusted_transaction_execution,
            }
        )
        if execution_blocked:
            copy.amount_yuan = None
            copy.amount_note = "关键信息还不够完整或不够新，因此暂不提供买卖金额。"
            copy.suggested_position_change_percent = None
            copy.suggested_position_change_basis = "决策证据未达到时点可用条件，禁止据此计算仓位变化"
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "持仓份额、成本或关键行情尚未确认完整且为最新；本次不提供金额、权重和仓位动作。",
            ]
        if note:
            copy.points = [note, *copy.points]
        copy.confidence = _normalize_confidence(copy.confidence)
        if escalation_note is not None:
            # M2.3：LLM 负责解释、系统负责算数——仓位调整比例由规则表回填，覆盖 LLM 自行
            # 给出的任何数字（LLM 未给出该字段本就是默认 None，这里统一以系统计算为准）。
            copy.suggested_position_change_percent = escalation.get("suggested_position_change_percent")
            copy.suggested_position_change_basis = str(escalation.get("basis") or "")
        if tradeability_review_required:
            copy.amount_yuan = None
            copy.amount_note = None
            copy.suggested_position_change_percent = None
            copy.suggested_position_change_basis = ""
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "交易条件或逐笔持有期未达到自动执行条件；本条仅供人工复核。",
            ]
        _backfill_decision_fields(copy, holding, sector_opportunity, evidence, ic_status)
        _enforce_public_ic_evidence(copy, evidence, ic_status)
        if shadow_note is not None:
            # M6：灰度提示须始终可见（不受 `_backfill_decision_fields` 只在为空时才
            # 回填的规则影响），追加到 validation_notes 末尾，与其它校验备注共存。
            copy.validation_notes = [*copy.validation_notes, shadow_note]
        _sync_decision_path_with_final_action(copy)
        if execution_blocked:
            copy.points = safe_blocked_points(
                copy.points,
                fallback="关键信息还不够完整或不够新，先观察，等数据更新后再判断。",
            )
            copy.decision_path = "证据时点校验未通过，系统阻断仓位动作并降为观察/风险复核。"
            copy.sector_evidence = [
                value for value in copy.sector_evidence if not contains_executable_decision_text(value)
            ]
            copy.fund_evidence = [
                value for value in copy.fund_evidence if not contains_executable_decision_text(value)
            ]
            copy.validation_notes = [
                value for value in copy.validation_notes if not contains_executable_decision_text(value)
            ] + ["关键信息完整性与更新时间校验未通过，系统已暂时关闭仓位操作。"]
        _enforce_final_execution_projection(copy, original_action=original_action)
        _humanize_recommendation_text(copy)
        guarded.append(copy)

    portfolio = _guard_portfolio_lines(portfolio_lines, risk)
    from app.services.decision_data_evidence import contains_trade_instruction_text

    portfolio = [line for line in portfolio if not contains_trade_instruction_text(line)]
    if not portfolio:
        portfolio = ["组合级执行动作以逐基金卡片中的系统校验结果为准。"]
    if evidence_blocked_codes:
        hint = "部分持仓的份额、成本或关键行情还未确认完整且为最新：本次只做观察和风险提示，暂不显示仓位动作与金额。"
        safe_portfolio = [line for line in portfolio if not contains_executable_decision_text(line)]
        portfolio = [hint, *safe_portfolio[:1]]
    elif not short_term and settings.news_require_today_for_add and not today_signal:
        hint = "当日无已引用要闻支撑，组合级建议以观察/控风险为主，不宜激进加仓。"
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    elif aggressive:
        from app.services.investment_presets import take_profit_threshold_percent

        threshold = take_profit_threshold_percent(request.profile)
        hint = (
            f"激进波段模式：跌深分批买、持有收益达 {threshold:.1f}%（含手续费）优先止盈，"
            f"目标持有 {request.profile.hold_days_target} 天内，仍须遵守集中度与浮亏线。"
        )
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    elif tactical:
        hint = "战术短线模式：建议侧重当日/次日盘面与板块动能，仍须遵守集中度与风险复核线。"
        if tuning.get("tighten_tactical") and tuning.get("reason"):
            hint = str(tuning["reason"])
        if not portfolio or hint not in portfolio[0]:
            portfolio = [hint, *portfolio]
    if isinstance(facts, dict):
        facts["data_evidence_guard"] = {
            "execution_blocked": bool(evidence_blocked_codes),
            "blocked_fund_codes": sorted(evidence_blocked_codes),
            "reasons_by_fund": evidence_blocked_codes,
        }
    return portfolio, guarded


def _reversal_signal_block(
    holding: Holding,
    nav_trend: dict | None,
    *,
    enforce_reversal: bool = True,
    enforce_pullback: bool = True,
) -> bool:
    if enforce_reversal:
        momentum = build_sector_momentum_context(holding, nav_trend)
        if momentum and momentum.get("pattern_label") == "two_day_reversal_down":
            return True
    if enforce_pullback:
        intraday = summarize_sector_intraday_for_holding(holding)
        if intraday and intraday.get("pattern_label") == "intraday_pullback":
            return True
    return False


def normalize_action_text(action: str) -> str:
    cleaned = (action or "").strip() or "观察"
    bucket = _action_bucket(cleaned)
    label = _BUCKET_TO_LABEL[bucket]
    if bucket == ACTION_BUCKET_REDUCE and ("复核" in cleaned or "风控" in cleaned):
        return "风控复核"
    return label


def _execution_direction(action: str) -> str:
    normalized = normalize_action_text(action)
    if normalized == "分批加仓":
        return "add"
    if any(token in normalized for token in ("减仓", "清仓")):
        return "reduce"
    return "none"


def _apply_holding_tradeability_guard(
    normalized_action: str,
    *,
    amount_yuan: float | None,
    holding: Holding | None,
    facts_row: dict | None,
) -> tuple[str, float | None, bool, str | None, dict, dict]:
    """Apply the server-owned daily transaction contract to one final action.

    Missing keys mean a historical pre-contract report and remain compatible.
    New preparation runs always include the key, even when the provider failed,
    so they fail closed rather than inheriting model-authored execution fields.
    """

    raw_tradeability = (
        facts_row.get("tradeability")
        if isinstance(facts_row, dict) and isinstance(facts_row.get("tradeability"), dict)
        else {}
    )
    has_tradeability_contract = bool(
        isinstance(facts_row, dict) and "tradeability" in facts_row
    )
    if not has_tradeability_contract:
        return normalized_action, amount_yuan, False, None, {}, {}

    holding_amount = holding.holding_amount if holding is not None else None
    transaction_execution = (
        dict(facts_row.get("transaction_execution"))
        if isinstance(facts_row.get("transaction_execution"), dict)
        else build_holding_transaction_execution(
            raw_tradeability,
            holding_amount_yuan=holding_amount,
        )
    )
    direction = _execution_direction(normalized_action)
    if direction == "add":
        amount_assessment = assess_holding_add_amount(
            raw_tradeability,
            holding_amount_yuan=holding_amount,
            amount_yuan=amount_yuan,
        )
        transaction_execution["amount_assessment"] = amount_assessment
        if not amount_assessment.get("executable"):
            return (
                "观察",
                None,
                True,
                "追加申购状态、追加起购额、单日限额或建议金额未通过核验，已降为观察。",
                dict(raw_tradeability),
                transaction_execution,
            )
        approved_amount = float(amount_assessment["approved_amount_yuan"])
        note = None
        if amount_assessment.get("amount_capped_by_daily_limit"):
            note = (
                f"建议金额已按已核验的单日申购限额下调为 {approved_amount:,.0f} 元；"
                "下单前仍需复核渠道剩余额度。"
            )
        return (
            normalized_action,
            approved_amount,
            False,
            note,
            dict(raw_tradeability),
            transaction_execution,
        )

    if direction == "reduce":
        if transaction_execution.get("redemption_status") != "eligible":
            return (
                "风控复核",
                None,
                True,
                "赎回状态未达到时点可执行条件，已降为人工风控复核。",
                dict(raw_tradeability),
                transaction_execution,
            )
        if (
            holding_amount is not None
            and holding_amount > 0
            and amount_yuan is not None
            and isfinite(float(amount_yuan))
            and float(amount_yuan) > 0
        ):
            review_amount = round(min(float(amount_yuan), float(holding_amount)), 2)
            transaction_execution["review_target_amount_yuan"] = review_amount
            transaction_execution["review_target_percent"] = round(
                review_amount / float(holding_amount) * 100,
                2,
            )
            transaction_execution["review_target_basis"] = (
                "系统按最终减仓档位与当前持仓测算，仅作为核验前的目标市值"
            )
        return (
            normalized_action,
            None,
            True,
            "目标减仓市值已按规则测算；逐笔持有期、锁定期与适用费率待核对。",
            dict(raw_tradeability),
            transaction_execution,
        )

    return (
        normalized_action,
        None if _execution_direction(normalized_action) == "none" else amount_yuan,
        False,
        None,
        dict(raw_tradeability),
        transaction_execution,
    )


def _review_reduction_amount(
    normalized_action: str,
    *,
    holding: Holding | None,
    weight_denominator: float,
    request: AnalysisRequest,
    escalation: dict,
) -> float | None:
    """Return a deterministic, non-executable reduction target for manual review.

    The provider is still forbidden from authoring a sell amount when lot age is
    unknown.  This target comes from server-owned position rules and is stored
    under ``transaction_execution``; the executable ``amount_yuan`` field stays
    empty until the user records the platform-confirmed shares.
    """
    if _execution_direction(normalized_action) != "reduce" or holding is None:
        return None
    if holding.holding_amount <= 0:
        return None

    percent = escalation.get("suggested_position_change_percent")
    if percent is not None:
        try:
            numeric_percent = float(percent)
        except (TypeError, ValueError):
            numeric_percent = 0.0
        if isfinite(numeric_percent) and numeric_percent < 0:
            return round(
                min(
                    float(holding.holding_amount),
                    float(holding.holding_amount) * abs(numeric_percent) / 100,
                ),
                2,
            )

    action_bucket = _action_bucket(normalized_action)
    if action_bucket == ACTION_BUCKET_CLEAR_ALL:
        return round(float(holding.holding_amount), 2)
    if action_bucket == ACTION_BUCKET_DEEP_REDUCE:
        return round(float(holding.holding_amount) * 0.5, 2)

    amount, _note = suggest_trade_amount(
        holding,
        holding_weight_percent(holding, request.holdings, request.profile),
        weight_denominator,
        request.profile,
        normalized_action,
    )
    return amount


def _strip_untrusted_execution_text(rec: FundRecommendation) -> FundRecommendation:
    """Remove free-text trade instructions before deterministic notes are added."""
    from app.services.decision_data_evidence import (
        contains_high_risk_trade_instruction_text,
        contains_trade_instruction_text,
    )

    copy = rec.model_copy(deep=True)
    copy.points = [
        value for value in copy.points if not contains_trade_instruction_text(value)
    ]
    copy.sector_evidence = [
        value
        for value in copy.sector_evidence
        if not contains_trade_instruction_text(value)
    ]
    copy.fund_evidence = [
        value
        for value in copy.fund_evidence
        if not contains_trade_instruction_text(value)
    ]
    copy.validation_notes = [
        value
        for value in copy.validation_notes
        if not contains_trade_instruction_text(value)
    ]
    copy.risks = [
        value for value in copy.risks if not contains_trade_instruction_text(value)
    ]
    if contains_high_risk_trade_instruction_text(copy.decision_path):
        copy.decision_path = ""
    copy.amount_note = None
    copy.suggested_position_change_percent = None
    copy.suggested_position_change_basis = ""
    return copy


def _enforce_final_execution_projection(
    rec: FundRecommendation,
    *,
    original_action: str,
) -> None:
    """Project every user-visible execution field from the final guarded action."""
    final_direction = _execution_direction(rec.action)
    original_direction = _execution_direction(original_action)
    if final_direction == "none" or final_direction != original_direction:
        rec.amount_yuan = None
        rec.amount_note = None
    if final_direction == "none":
        rec.suggested_position_change_percent = None
        rec.suggested_position_change_basis = ""

    amount = rec.amount_yuan
    if amount is not None and (not isfinite(float(amount)) or float(amount) <= 0):
        rec.amount_yuan = None
        rec.amount_note = None
    if rec.amount_yuan is not None:
        verb = "加仓" if final_direction == "add" else "减仓"
        rec.amount_note = (
            f"系统校验后的示意{verb}金额约 {float(rec.amount_yuan):,.0f} 元；"
            "实际操作前请核对净值、费用与可用资金。"
        )

    position = rec.suggested_position_change_percent
    if position is not None:
        valid_sign = (final_direction == "add" and position > 0) or (
            final_direction == "reduce" and position < 0
        )
        if not valid_sign or not isfinite(float(position)):
            rec.suggested_position_change_percent = None
            rec.suggested_position_change_basis = ""
        else:
            rec.suggested_position_change_basis = (
                f"系统依据最终动作「{rec.action}」及确定性规则计算，非模型自由给值"
            )

    rec.risks = rec.risks or _build_default_risks(rec, None)

    projection = f"系统校验后的最终动作：{rec.action}。"
    if rec.amount_yuan is not None:
        projection += f"示意金额约 {float(rec.amount_yuan):,.0f} 元。"
    rec.points = [*rec.points, projection]
    if rec.decision_path:
        rec.decision_path = (
            f"{rec.decision_path.rstrip('。')}。系统校验后的最终动作：{rec.action}。"
        )
    else:
        rec.decision_path = f"确定性守卫完成身份、证据与风险校验；最终动作：{rec.action}。"
    rec.validation_notes.append("执行字段已按最终动作重新投影，原始模型指令不直接作为执行依据。")


def conservative_action_text(llm_action: str, offline_action: str) -> str:
    llm_bucket = _action_bucket(normalize_action_text(llm_action))
    offline_bucket = _action_bucket(normalize_action_text(offline_action))
    chosen = min(llm_bucket, offline_bucket)
    if chosen == ACTION_BUCKET_REDUCE and ("复核" in offline_action or "风控" in offline_action):
        return "风控复核"
    return _BUCKET_TO_LABEL[chosen]


def _offline_by_holding(
    request: AnalysisRequest,
    weight_denominator: float,
    market_news: list[NewsItem] | None,
    *,
    nav_trends_by_code: dict[str, dict] | None = None,
) -> dict[str, FundRecommendation]:
    nav_trends = nav_trends_by_code or {}
    mapping: dict[str, FundRecommendation] = {}
    for holding in request.holdings:
        weight = holding_weight_percent(holding, request.holdings, request.profile)
        offline = build_offline_fund_recommendation(
            holding,
            weight,
            weight_denominator,
            request.profile,
            market_news=market_news,
            nav_trend=nav_trends.get(holding.fund_code),
        )
        mapping[holding.fund_code] = offline
        mapping[holding.fund_name] = offline
    return mapping


def _match_holding(rec: FundRecommendation, holdings: list[Holding]) -> Holding | None:
    for holding in holdings:
        if rec.fund_code != "000000" and holding.fund_code == rec.fund_code:
            return holding
        if holding.fund_name == rec.fund_name:
            return holding
    return None


def _max_allowed_bucket(
    risk: RiskAssessment,
    holding,
    request: AnalysisRequest,
    *,
    tactical: bool = False,
    aggressive: bool = False,
) -> int:
    if risk.suggested_action == "risk_review":
        return 2
    if risk.level == "high":
        return 2
    if (
        not tactical
        and not aggressive
        and holding is not None
        and request.profile.avoid_chasing
    ):
        sector = getattr(holding, "sector_return_percent", None)
        if sector is not None and sector > 5:
            return 2
    return 3


def _facts_row_for_holding(facts: dict | None, holding: Holding | None) -> dict | None:
    if not facts or holding is None:
        return None
    for row in facts.get("holdings") or []:
        if isinstance(row, dict) and row.get("fund_code") == holding.fund_code:
            return row
    return None


def _factor_ic_status_from_facts(facts: dict | None) -> dict | None:
    if not isinstance(facts, dict):
        return None
    factor_scores = facts.get("factor_scores")
    if not isinstance(factor_scores, dict) or "ic_status" not in factor_scores:
        return None
    ic_status = factor_scores.get("ic_status")
    return ic_status if isinstance(ic_status, dict) else {}


def _ic_state(ic_status: dict | None) -> str | None:
    if not isinstance(ic_status, dict):
        return None
    state = ic_status.get("state")
    return str(state) if state in _VALID_IC_STATES else None


def _composite_level(evidence: dict | None) -> str | None:
    if not isinstance(evidence, dict):
        return None
    composite = evidence.get("composite")
    if not isinstance(composite, dict):
        return None
    level = composite.get("level")
    return str(level) if level in _VALID_EVIDENCE_LEVELS else None


def _validated_evidence_components(
    evidence: dict | None,
    ic_status: dict | None,
) -> list[dict]:
    if not isinstance(evidence, dict):
        return []
    components = evidence.get("components")
    if not isinstance(components, (list, tuple)):
        return []

    state = _ic_state(ic_status)
    factor_may_participate = ic_status is None or state == "available"
    validated: list[dict] = []
    seen_sources: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        source = component.get("source")
        level = component.get("level")
        basis = component.get("basis")
        if source not in _VALID_EVIDENCE_SOURCES or level not in _VALID_EVIDENCE_LEVELS:
            continue
        if not isinstance(basis, str) or not basis.strip():
            continue
        if source == "factor" and not factor_may_participate:
            continue
        if source in seen_sources:
            continue
        seen_sources.add(source)
        validated.append(component)
    return validated


def _has_usable_factor_component(evidence: dict | None, ic_status: dict | None) -> bool:
    return any(
        component.get("source") == "factor"
        for component in _validated_evidence_components(evidence, ic_status)
    )


def _ic_participation_note(evidence: dict | None, ic_status: dict | None) -> str | None:
    state = _ic_state(ic_status)
    if state == "unavailable":
        return "IC 回测未接入，IC 未参与本次结论"
    if state == "stale":
        return "IC 回测已过期，IC 未参与本次结论"
    if _has_usable_factor_component(evidence, ic_status):
        return None
    if evidence or ic_status is not None:
        return "IC 回测未覆盖，IC 未参与本次结论"
    return None


def _weak_quantitative_evidence_reason(
    evidence: dict | None,
    ic_status: dict | None,
) -> str | None:
    if _composite_level(evidence) not in {"低", "不足"}:
        return None
    if _has_usable_factor_component(evidence, ic_status):
        return "量化证据背书弱"
    return _status_aware_low_confidence_reason(ic_status)


def _status_aware_low_confidence_reason(ic_status: dict | None) -> str:
    state = _ic_state(ic_status)
    if state == "unavailable":
        return "IC 回测未接入，现有非 IC 证据置信偏低"
    if state == "stale":
        return "IC 回测已过期，现有非 IC 证据置信偏低"
    return "IC 回测未覆盖，现有量化证据置信偏低"


def _evidence_composite_summary(evidence: dict | None, ic_status: dict | None) -> str:
    component_count = len(_validated_evidence_components(evidence, ic_status))
    level = _composite_level(evidence) or "不足"
    return f"{component_count}路已参与量化证据综合置信：{level}"


def _weak_evidence_reasons(
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
) -> list[str]:
    """加仓类动作要求「板块方向」与「基金自身证据」至少有一路站得住，否则视为证据不足。"""
    reasons: list[str] = []
    if sector_opportunity:
        if sector_opportunity.get("opportunity_available") is False:
            reasons.append("持仓板块当前不构成机会")
        confidence = str(sector_opportunity.get("confidence") or "")
        if confidence in {"低", "不足"}:
            reasons.append("板块方向置信偏低")
        pattern = str(sector_opportunity.get("pattern_label") or "")
        if pattern in {"distribution", "weak_outflow"}:
            reasons.append("板块资金流偏弱")
    weak_quantitative_reason = _weak_quantitative_evidence_reason(evidence, ic_status)
    if weak_quantitative_reason:
        reasons.append(weak_quantitative_reason)
    return _append_unique([], reasons, limit=4)


def _backfill_decision_fields(
    rec: FundRecommendation,
    holding: Holding | None,
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
) -> None:
    if not rec.decision_path:
        rec.decision_path = _build_decision_path(
            rec,
            holding,
            sector_opportunity,
            evidence,
            ic_status,
        )
    if not rec.sector_evidence:
        rec.sector_evidence = _append_unique([], _build_sector_evidence(sector_opportunity), limit=4)
    if not rec.fund_evidence:
        rec.fund_evidence = _append_unique(
            [],
            _build_fund_evidence(evidence, ic_status),
            limit=4,
        )
    if not rec.validation_notes:
        rec.validation_notes = _append_unique(
            [],
            _build_validation_notes(sector_opportunity, evidence, ic_status),
            limit=4,
        )
    if not rec.risks:
        rec.risks = _append_unique([], _build_default_risks(rec, sector_opportunity), limit=3)


def _build_decision_path(
    rec: FundRecommendation,
    holding: Holding | None,
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
) -> str:
    sector = (holding.sector_name if holding else None) or "该持仓板块"
    if sector_opportunity:
        track = sector_opportunity.get("track") or "unknown"
        confidence = sector_opportunity.get("confidence") or "中"
        sector_clause = f"先看持仓板块方向：{sector}（{_track_label(track)}，置信{confidence}）"
    else:
        sector_clause = f"先看持仓板块方向：{sector}（暂无独立方向信号）"
    if evidence:
        fund_clause = f"再看该基金自身量化证据（{_evidence_composite_summary(evidence, ic_status)}）"
    else:
        fund_clause = "再看该基金自身持仓与风控数据"
    ic_note = _ic_participation_note(evidence, ic_status)
    if ic_note:
        fund_clause = f"{fund_clause}；{ic_note}"
    return f"{sector_clause}，{fund_clause}，动作定为{rec.action}。"


def _build_sector_evidence(sector_opportunity: dict | None) -> list[str]:
    if not sector_opportunity:
        return []
    evidence: list[str] = []
    track = sector_opportunity.get("track")
    confidence = sector_opportunity.get("confidence")
    if track:
        text = _track_label(track)
        if confidence:
            text += f"，置信度{confidence}"
        evidence.append(text)
    today_flow = sector_opportunity.get("today_main_force_net_yi")
    five_day_flow = sector_opportunity.get("cumulative_5d_net_yi")
    if today_flow is not None or five_day_flow is not None:
        parts = []
        if today_flow is not None:
            parts.append(f"今日主力净流入 {_fmt_num(today_flow)} 亿")
        if five_day_flow is not None:
            parts.append(f"5日主力净流入 {_fmt_num(five_day_flow)} 亿")
        evidence.append("，".join(parts))
    pattern = sector_opportunity.get("pattern_label")
    if pattern:
        evidence.append(f"资金/价格信号：{_pattern_label(str(pattern))}")
    if sector_opportunity.get("opportunity_available") is False:
        evidence.append("当前不构成加仓机会，仅供方向参考")
    evidence.extend(
        str(item) for item in sector_opportunity.get("evidence") or [] if str(item).strip()
    )
    return evidence


def _build_fund_evidence(
    evidence: dict | None,
    ic_status: dict | None = None,
) -> list[str]:
    result: list[str] = []
    if evidence:
        result.append(_evidence_composite_summary(evidence, ic_status))
    for component in _validated_evidence_components(evidence, ic_status):
        basis = component.get("basis")
        if basis:
            result.append(str(basis))
    ic_note = _ic_participation_note(evidence, ic_status)
    if ic_note:
        result.append(ic_note)
    return result


def _build_validation_notes(
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
) -> list[str]:
    notes: list[str] = []
    weak_quantitative_reason = _weak_quantitative_evidence_reason(evidence, ic_status)
    if weak_quantitative_reason:
        notes.append(weak_quantitative_reason)
    ic_note = _ic_participation_note(evidence, ic_status)
    if ic_note:
        notes.append(ic_note)
    if sector_opportunity:
        notes.extend(
            str(item) for item in sector_opportunity.get("penalties") or [] if str(item).strip()
        )
    if not sector_opportunity:
        notes.append("暂无独立板块方向数据，方向判断仅供参考")
    return notes


def _factor_bases_to_exclude(evidence: dict | None, ic_status: dict | None) -> list[str]:
    if _has_usable_factor_component(evidence, ic_status) or not isinstance(evidence, dict):
        return []
    components = evidence.get("components")
    if not isinstance(components, (list, tuple)):
        return []
    result: list[str] = []
    for component in components:
        if not isinstance(component, dict) or component.get("source") != "factor":
            continue
        basis = component.get("basis")
        if isinstance(basis, str) and basis.strip() and basis not in result:
            result.append(basis)
    return result


def _sanitize_public_ic_text(
    text: str,
    *,
    route_wording: str,
    weak_replacement: str | None,
    excluded_factor_bases: list[str],
    participation_note: str | None,
) -> str:
    result = str(text).replace("三路量化证据", route_wording)
    if weak_replacement:
        result = result.replace("量化证据背书弱", weak_replacement)
        result = result.replace("量化背书弱", weak_replacement)
    for basis in excluded_factor_bases:
        result = result.replace(basis, participation_note or "")
    return result.strip()


def _dedupe_text_items(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _append_participation_note_once(text: str, participation_note: str | None) -> str:
    if not participation_note:
        return text
    without_note = text.replace(participation_note, "")
    without_note = re.sub(r"([；;，,。])(?:\s*[；;，,。])+", r"\1", without_note)
    without_note = without_note.strip().rstrip("。；;，, ")
    if not without_note:
        return f"{participation_note}。"
    return f"{without_note}；{participation_note}。"


def _enforce_public_ic_evidence(
    rec: FundRecommendation,
    evidence: dict | None,
    ic_status: dict | None,
) -> None:
    validated_components = _validated_evidence_components(evidence, ic_status)
    route_wording = f"{len(validated_components)}路已参与量化证据"
    has_usable_factor = any(
        component.get("source") == "factor" for component in validated_components
    )
    weak_replacement = (
        None if has_usable_factor else _status_aware_low_confidence_reason(ic_status)
    )
    participation_note = _ic_participation_note(evidence, ic_status)
    excluded_factor_bases = _factor_bases_to_exclude(evidence, ic_status)

    def sanitize(text: str) -> str:
        return _sanitize_public_ic_text(
            text,
            route_wording=route_wording,
            weak_replacement=weak_replacement,
            excluded_factor_bases=excluded_factor_bases,
            participation_note=participation_note,
        )

    rec.points = _dedupe_text_items([sanitize(item) for item in rec.points])
    rec.decision_path = _append_participation_note_once(
        sanitize(rec.decision_path),
        participation_note,
    )

    if evidence is not None or ic_status is not None:
        rec.fund_evidence = _append_unique(
            [],
            _build_fund_evidence(evidence, ic_status),
            limit=4,
        )
    else:
        rec.fund_evidence = _dedupe_text_items(
            [sanitize(item) for item in rec.fund_evidence]
        )

    required_validation_notes: list[str] = []
    weak_reason = _weak_quantitative_evidence_reason(evidence, ic_status)
    if weak_reason:
        required_validation_notes.append(weak_reason)
    if participation_note:
        required_validation_notes.append(participation_note)
    optional_validation_notes = [
        sanitize(item)
        for item in rec.validation_notes
        if not any(basis in str(item) for basis in excluded_factor_bases)
    ]
    rec.validation_notes = _append_unique(
        [],
        [*required_validation_notes, *optional_validation_notes],
        limit=4,
    )


def _build_default_risks(rec: FundRecommendation, sector_opportunity: dict | None) -> list[str]:
    if "加仓" in rec.action or "分批" in rec.action or "定投" in rec.action:
        if sector_opportunity and sector_opportunity.get("opportunity_available") is False:
            return ["板块当前不构成机会，加仓后仍可能面临回调"]
        return ["板块或市场波动可能导致净值短期回撤"]
    if "清仓" in rec.action:
        return ["清仓后若板块反弹或情绪回暖，将完全错过修复行情，且丧失该赛道后续机会"]
    if "大幅减仓" in rec.action:
        return ["大幅减仓后若判断有误，恢复原仓位需承担新的交易成本和时点风险"]
    if "减仓" in rec.action or "复核" in rec.action:
        return ["减仓后若板块反弹可能错过修复行情"]
    return ["市场波动可能影响短期净值表现"]


def _sync_decision_path_with_final_action(rec: FundRecommendation) -> None:
    if not rec.decision_path:
        return
    action = rec.action
    if action in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    if "动作" not in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    text = _strip_conflicting_action_clause(rec.decision_path, action)
    text = text.rstrip("。；;，, ")
    rec.decision_path = f"{text}。系统校验后最终动作调整为{action}。"


def _contains_conflicting_action(text: str, final_action: str) -> bool:
    for candidate in _BUCKET_TO_LABEL.values():
        if candidate != final_action and candidate in text:
            return True
    return False


def _strip_conflicting_action_clause(text: str, final_action: str) -> str:
    result = text
    for candidate in _BUCKET_TO_LABEL.values():
        if candidate == final_action:
            continue
        result = re.sub(rf"，?最后决定[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
        result = re.sub(rf"，?动作[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
    return result


def _humanize_recommendation_text(rec: FundRecommendation) -> None:
    rec.decision_path = _humanize_evidence_text(
        rec.decision_path, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS
    )
    rec.amount_note = (
        _humanize_evidence_text(rec.amount_note, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        if rec.amount_note
        else rec.amount_note
    )
    rec.sector_evidence = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.sector_evidence
    ]
    rec.fund_evidence = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.fund_evidence
    ]
    rec.validation_notes = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.validation_notes
    ]
    rec.points = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.points
    ]
    rec.risks = [
        _humanize_evidence_text(item, extra_text_replacements=_REPORT_HUMANIZE_TEXT_REPLACEMENTS)
        for item in rec.risks
    ]


def _guard_portfolio_lines(lines: list[str], risk: RiskAssessment) -> list[str]:
    if risk.suggested_action != "risk_review":
        return lines

    mandatory = "组合已触发风险复核线，今日以控风险为先，不建议新增加仓。"
    if lines and mandatory in lines[0]:
        return lines
    return [mandatory, *lines]
