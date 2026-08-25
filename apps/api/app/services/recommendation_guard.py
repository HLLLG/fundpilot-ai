from __future__ import annotations

import re
from datetime import date
from math import floor, isfinite

from app.config import get_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
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
from app.services.daily_action_proposal import (
    DAILY_ACTION_PROPOSAL_SCHEMA_VERSION,
    DailyActionProposal,
    propose_daily_action,
)
from app.services.holding_lot_maturity import describe_reduction_lot_impact
from app.services.sector_labels import normalize_sector_label
from app.services.transaction_behavior_review import recent_transaction_conflict_note
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION_V3,
    EXIT_TREND_THRESHOLD,
    V3_BLOCK_WEIGHTS,
    V3_GATE_THRESHOLDS,
    V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE,
    _CORRELATION_DEDUP_EXEMPT_PAIRS,
    _SECTOR_GROUPS,
    _direction_identity,
    _sector_group,
    true_overheat_add_block_reason,
)
from app.services.signal_guard_policy import resolve_signal_guard_policy
from app.services.recommendations import build_offline_fund_recommendation
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
#: 场外基金短持惩罚赎回费窗口。同一只基金在窗口内再加一笔，一旦要走就要交 1.5%。
#: 取自然日而不是交易日，与赎回费规则一致，不另造未回测的间隔参数。
ADD_INTERVAL_CALENDAR_DAYS = 7


def apply_recommendation_guards(
    fund_recs: list[FundRecommendation],
    portfolio_lines: list[str],
    request: AnalysisRequest,
    risk: RiskAssessment,
    market_news: list[NewsItem] | None = None,
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
    guard_policy = (
        resolve_signal_guard_policy(
            request.holdings,
            backtest_days=settings.sector_signal_backtest_days,
        )
        if settings.sector_signal_backtest_enabled
        else {
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "hints": [],
            "reason": None,
        }
    )
    ic_status = _factor_ic_status_from_facts(facts)
    # 组合级判定，逐只持仓复用同一个结论，不必每条重算。
    drawdown_cap_reason = _portfolio_drawdown_cap_reason(facts, risk, request.profile)
    portfolio_snapshot = (facts or {}).get("portfolio_snapshot")
    portfolio_execution_reasons: list[str] = []
    if isinstance(portfolio_snapshot, dict):
        if portfolio_snapshot.get("stale"):
            portfolio_execution_reasons.append("stale_portfolio_snapshot")
        if not portfolio_snapshot.get("authoritative"):
            portfolio_execution_reasons.append("non_authoritative_portfolio")
    from app.services.decision_data_evidence import (
        contains_executable_decision_text,
        decision_evidence_allows_action,
        safe_blocked_points,
    )

    guarded: list[FundRecommendation] = []
    evidence_blocked_codes: dict[str, list[str]] = {}
    proposal_audit: list[dict] = []
    for rec in fund_recs:
        original_action = rec.action
        rec = _strip_untrusted_execution_text(rec)
        holding = _match_holding(rec, request.holdings)
        offline = None
        if holding is not None:
            offline = offline_map.get(holding.fund_code) or offline_map.get(holding.fund_name)

        llm_action = normalize_action_text(rec.action)
        facts_row = _facts_row_for_holding(facts, holding) if holding is not None else None

        # --- 确定性动作提议的输入：全部与"当前动作是什么"无关，因此可以先算 --------
        #
        # 这些门禁结果既喂给 `propose_daily_action()`，也被下面的 clamp 链原样复用，
        # 所以提议与否决用的是**同一批判定**，不存在两处各算一份的漂移。
        sector_opportunity = (facts_row or {}).get("sector_opportunity")
        evidence = (facts_row or {}).get("evidence")
        vehicle_quality = (facts_row or {}).get("vehicle_quality")
        sector_absence_reason = _sector_direction_absence_reason(
            sector_opportunity,
            holding,
        )
        nav_trend = None
        if holding is not None and nav_trends_by_code:
            nav_trend = nav_trends_by_code.get(holding.fund_code)
        reversal_blocked = holding is not None and _reversal_signal_block(
            holding,
            nav_trend,
            enforce_reversal=bool(guard_policy.get("enforce_reversal_block", True)),
            enforce_pullback=bool(guard_policy.get("enforce_pullback_block", True)),
        )
        max_bucket = _max_allowed_bucket(
            risk,
            holding,
            request,
            portfolio_drawdown_capped=drawdown_cap_reason is not None,
        )
        escalation = resolve_escalation_floor(
            sector_opportunity=sector_opportunity,
            evidence=evidence,
            market_breadth=(facts or {}).get("market_breadth"),
            over_concentration=bool((facts_row or {}).get("over_concentration")),
            has_unrealized_gain=((facts_row or {}).get("estimated_holding_return_percent") or 0) > 0,
            # 方向退出必须在 guard 侧也生效，否则模型可以把它说没了。取值优先用板块机会行
            # 上挂的那份（analysis_facts 透传过来的同一对象）。
            direction_exit=(
                sector_opportunity.get("direction_exit")
                if isinstance(sector_opportunity, dict)
                else None
            )
            or (facts_row or {}).get("direction_exit"),
            # 基金层第三源：与 analysis_facts._attach_escalation_to_holdings 传同一个
            # facts 键，guard 侧与 facts 侧的升级判定不得各看一套数据。
            nav_trend=(facts_row or {}).get("nav_trend"),
        )

        # `decision_evidence_allows_action` 只关心方向（add/reduce/none）。用 LLM 草案的
        # 方向做这次探测：提议还没生成，而数据门禁本身不区分"谁提出的动作"。
        evidence_allowed, evidence_reasons = decision_evidence_allows_action(
            facts,
            scope="analysis",
            fund_code=(holding.fund_code if holding is not None else rec.fund_code),
            direction=_execution_direction(llm_action),
            allow_incomplete_position_for_direction=True,
        )
        execution_reasons = list(
            dict.fromkeys([*portfolio_execution_reasons, *evidence_reasons])
        )
        execution_blocked = bool(portfolio_execution_reasons) or not evidence_allowed
        if execution_blocked:
            evidence_blocked_codes[rec.fund_code] = (
                execution_reasons or ["decision_evidence_not_ready"]
            )

        proposal = propose_daily_action(
            risk_level=risk.level,
            risk_suggested_action=risk.suggested_action,
            escalation_min_bucket=escalation.get("min_bucket"),
            max_allowed_bucket=max_bucket,
            entry_state=str((sector_opportunity or {}).get("entry_state") or "") or None,
            entry_state_block_reason=_entry_state_add_block_reason(sector_opportunity),
            sector_absence_reason=sector_absence_reason,
            opportunity_available=(sector_opportunity or {}).get("opportunity_available"),
            weak_evidence_reasons=_weak_evidence_reasons(
                sector_opportunity,
                evidence,
                ic_status,
                sector_absence_reason=sector_absence_reason,
            ),
            reversal_blocked=reversal_blocked,
            execution_blocked=execution_blocked,
            additional_add_blocks=_additional_add_blocks(
                vehicle_quality,
                facts_row,
                sector_opportunity,
                facts=facts,
                avoid_chasing=request.profile.avoid_chasing,
            ),
        )
        proposal_enforced = settings.daily_action_proposal_mode == "enforced"
        # 动作链的输入**始终**是 LLM 草案：既有的每一道 clamp 与它对应的解释文案都建立在
        # "模型提了什么、被什么规则改掉了"之上，直接把提议塞进来当输入会同时砸掉两件事——
        #
        #   1. `>= ACTION_BUCKET_ADD` 的那些分支不再触发，用户看不到"为什么不能加仓"；
        #   2. 模型提出的风险动作（如「减仓评估」）会被提议的中性基线**放松**成「观察」。
        #
        # 提议因此改为在链尾做一次**收口的提升**（见下方 `_promote_to_proposed_add`）：
        # 只把被动动作抬到加仓，绝不覆盖任何风险动作，也绝不绕过任何门禁。
        normalized = llm_action

        snapshot_note = None
        if execution_blocked and _action_bucket(normalized) >= ACTION_BUCKET_ADD:
            normalized = "观察"
            snapshot_note = "关键持仓或行情数据未达到时点可用条件，因此暂不提供加减仓操作。"

        reversal_note = None
        if reversal_blocked and (
            _action_bucket(normalized) >= 3 or _action_bucket(rec.action) >= 3
        ):
            normalized = "暂停追涨"
            reversal_note = "涨后回吐或盘中冲高回落，已限制追涨加仓（板块短线信号）。"

        if (
            offline is not None
            and not reversal_note
            and _offline_action_is_a_risk_veto(offline.action)
        ):
            normalized = conservative_action_text(normalized, offline.action)

        drawdown_note = None
        if _action_bucket(normalized) > max_bucket:
            if drawdown_cap_reason is not None and _action_bucket(normalized) >= ACTION_BUCKET_ADD:
                drawdown_note = drawdown_cap_reason
            normalized = _BUCKET_TO_LABEL[max_bucket]

        weak_note = None
        if not reversal_note and _action_bucket(normalized) >= ACTION_BUCKET_ADD:
            weak_reasons = _weak_evidence_reasons(
                sector_opportunity,
                evidence,
                ic_status,
                sector_absence_reason=sector_absence_reason,
            )
            if weak_reasons:
                normalized = "观察"
                max_bucket = min(max_bucket, ACTION_BUCKET_WATCH)
                # 措辞从「板块或基金证据不足」收紧为「板块方向证据不足」：A 之后
                # `_weak_evidence_reasons` 只在板块侧命中时才返回非空，所以"板块"必然
                # 成立。旧措辞那个"或"字让用户无法判断到底哪一路出了问题——实测
                # 011036 板块侧全部通过、只有基金侧的常量在拦，却也显示这句话。
                weak_note = (
                    f"板块方向证据不足（{'、'.join(weak_reasons)}），"
                    "已将加仓类动作降为「观察」。"
                )

        # M2.1 双向 guard：证据强烈指向风险升级时，即使前面几步的降级仍停在"观察"，
        # 这里作为最终的保守下限强制继续拉低（甚至拉到"减仓评估/大幅减仓评估/清仓评估"）。
        # 这是本次升级要修的核心缺陷——旧 guard 只会把"分批加仓"降到"观察"，
        # 不会在证据极强时进一步升级到减仓类动作。
        # `escalation` 已在循环开头算过（提议与 clamp 复用同一份判定），此处不重算。
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

        # Stale/non-authoritative holdings or unusable market evidence cannot
        # support a directional decision. An incomplete share ledger does not
        # enter this branch because percentage advice only needs holding value.
        if execution_blocked:
            normalized = (
                "风控复核"
                if risk.level == "high" or risk.suggested_action == "risk_review"
                else "观察"
            )
            escalation_note = None
            shadow_note = None

        # 确定性提议的唯一生效点：把"系统规则支持加仓、但结论仍停在被动动作"的情况抬起来。
        # 放在这里是因为上面所有 clamp 都已跑完（该拦的都拦了、该解释的都解释了），而下面
        # 的动作词表、仓位比例与交易门禁还没跑——提升后仍要过它们。
        proposal_note = None
        promoted = _promote_to_proposed_add(
            normalized,
            proposal=proposal,
            offline_action=offline.action if offline is not None else None,
        )
        if promoted is not None:
            if proposal_enforced:
                normalized = promoted
                # 措辞必须与实际跑过的门禁一致。旧文案一律写「量化证据与风险门禁均通过」，
                # 但基金侧量化证据自 2026-08-12 起**刻意不再单独否决**（弱只降档，不可用
                # 时连降档都不做），当前配置下 `reliability.usable` 恒为 false——那句话
                # 宣称通过的是一道压根没被咨询的门。用户读到「证据不足」却看到
                # 「证据均通过」，只会认为系统在自说自话。
                evidence_clause = (
                    "量化证据可用"
                    if _fund_evidence_is_usable(evidence)
                    else "基金侧量化证据本轮不可用、未参与结论"
                )
                proposal_note = (
                    f"系统按确定性规则（方向已就绪、{evidence_clause}、风险门禁通过）提议"
                    f"「{promoted}」，模型草案为「{llm_action}」，最终以系统提议为准。"
                )
            else:
                # 标记刻意与 M2.1 escalation 的「灰度提示」区分开：两套灰度机制可能同时
                # 在灰度期，共用一个标记会让用户（和聚合复盘）分不清是哪一层在提示。
                proposal_note = (
                    f"【动作提议灰度中，未生效】系统确定性规则本轮提议「{promoted}」，"
                    f"当前采用模型草案「{llm_action}」。"
                )

        # 提升之后再拦：载体不合格 / 加仓间隔 / 结构化过热不能被「门都开着」抬回去。
        vehicle_note = None
        interval_note = None
        overheat_note = None
        if _execution_direction(normalized) == "add" and _vehicle_quality_blocks_add(
            vehicle_quality
        ):
            normalized = "观察"
            vehicle_note = (
                "被动载体质量未达标，本轮不加现持仓；请评估同方向是否有更合适的载体。"
            )
            proposal_note = None
        interval_block_reason = _recent_buy_add_block_reason(
            facts_row, sector_opportunity, facts=facts
        )
        if _execution_direction(normalized) == "add" and interval_block_reason:
            normalized = "暂停追涨"
            interval_note = interval_block_reason
            proposal_note = None
        overheat_block_reason = true_overheat_add_block_reason(
            sector_opportunity,
            strict=request.profile.avoid_chasing,
        )
        if _execution_direction(normalized) == "add" and overheat_block_reason:
            normalized = "暂停追涨"
            overheat_note = overheat_block_reason
            proposal_note = None

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
            proposed_position_percent,
            proposed_position_basis,
            position_note,
        ) = _resolve_deterministic_position_change(
            normalized,
            holding=holding,
            profile=request.profile,
            weight_denominator=weight_denominator,
            sector_opportunity=sector_opportunity,
            evidence=evidence,
            vehicle_quality=vehicle_quality,
            # 与上面 `has_unrealized_gain` 取同一个数：加仓侧封档与减仓侧升档必须共用
            # 一套持有收益口径，否则同一份日报会出现"按浮亏封了加仓档、又按浮盈升了
            # 减仓档"这种自相矛盾的组合。
            holding_return_percent=_num(
                (facts_row or {}).get("estimated_holding_return_percent")
            ),
        )
        if (
            _execution_direction(normalized) == "add"
            and proposed_position_percent is None
        ):
            normalized = "观察"
        proposed_amount_yuan = _position_change_amount_yuan(
            holding,
            proposed_position_percent,
        )

        (
            normalized,
            approved_amount_yuan,
            tradeability_review_required,
            tradeability_note,
            trusted_tradeability,
            trusted_transaction_execution,
        ) = _apply_holding_tradeability_guard(
            normalized,
            amount_yuan=proposed_amount_yuan,
            holding=holding,
            facts_row=facts_row,
        )

        final_direction = _execution_direction(normalized)
        if final_direction == "add" and holding is not None and holding.holding_amount > 0:
            if approved_amount_yuan is not None and approved_amount_yuan > 0:
                approved_percent = floor(
                    approved_amount_yuan / holding.holding_amount * 1000
                ) / 10
                if approved_percent > 0:
                    if (
                        proposed_position_percent is not None
                        and approved_percent < proposed_position_percent
                    ):
                        proposed_position_basis = (
                            f"{proposed_position_basis.rstrip('；')}；"
                            "已按单日申购限额收紧"
                        )
                    proposed_position_percent = approved_percent
                else:
                    proposed_position_percent = None
                    proposed_position_basis = ""
        elif final_direction == "none":
            proposed_position_percent = None
            proposed_position_basis = ""

        note = (
            note_forbidden_action
            or tradeability_note
            or escalation_note
            or vehicle_note
            or interval_note
            or overheat_note
            or position_note
            or snapshot_note
            or reversal_note
            or weak_note
            or drawdown_note
        )
        if not note and proposal_enforced and proposal_note is not None:
            # enforced 模式下"系统提议与模型草案不一致"本身就是最该让用户看到的一句。
            note = proposal_note
        elif (
            not note
            and offline is not None
            and _offline_action_is_a_risk_veto(offline.action)
            and normalized != rec.action.strip()
        ):
            note = f"已按风控规则将「{rec.action.strip()}」调整为「{normalized}」（对照本地规则：{offline.action}）。"
        elif not note and normalized != rec.action.strip():
            note = f"已规范动作表述为「{normalized}」。"

        copy = rec.model_copy(
            update={
                "action": normalized,
                "amount_yuan": None,
                "amount_note": None,
                "tradeability": trusted_tradeability,
                "transaction_execution": trusted_transaction_execution,
                "suggested_position_change_percent": proposed_position_percent,
                "suggested_position_change_basis": proposed_position_basis,
            }
        )
        if execution_blocked:
            copy.amount_yuan = None
            copy.amount_note = "关键持仓或行情数据未达到时点可用条件，因此暂不提供买卖金额。"
            copy.suggested_position_change_percent = None
            copy.suggested_position_change_basis = "决策证据未达到时点可用条件，禁止据此计算仓位变化"
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "关键持仓或行情数据未确认完整且为最新；本次不提供金额、权重和仓位动作。",
            ]
        if note and not _is_redundant_user_point(note):
            copy.points = [note, *copy.points]
        copy.confidence = _normalize_confidence(copy.confidence)
        if escalation_note is not None:
            # M2.3：LLM 负责解释、系统负责算数——仓位调整比例由规则表回填，覆盖 LLM 自行
            # 给出的任何数字（LLM 未给出该字段本就是默认 None，这里统一以系统计算为准）。
            copy.suggested_position_change_percent = escalation.get("suggested_position_change_percent")
            escalation_basis = str(escalation.get("basis") or "").strip()
            copy.suggested_position_change_basis = (
                "相对当前估算持仓计算"
                + (f"；{escalation_basis}" if escalation_basis else "")
            )
        if tradeability_review_required:
            copy.amount_yuan = None
            copy.amount_note = None
            if _execution_direction(copy.action) == "none":
                copy.suggested_position_change_percent = None
                copy.suggested_position_change_basis = ""
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "交易条件或逐笔持有期仍需在实际操作前核对；建议比例仅用于风险规划。",
            ]
        _backfill_decision_fields(
            copy,
            holding,
            sector_opportunity,
            evidence,
            ic_status,
            vehicle_quality,
        )
        _enforce_public_ic_evidence(copy, evidence, ic_status, vehicle_quality)
        if sector_absence_reason:
            # 兜底披露：方向层证据缺失的持仓不只是"不能加仓"（弱证据降级已经拦了），
            # 它同时**收不到任何方向退出/确定性减仓信号**——退出侧的主语全是板块，没有
            # 板块方向证据的仓位在这条链路上是盲区。不说出来，"系统没让我卖"会被读成
            # "系统认为不用卖"。
            copy.validation_notes = [
                *copy.validation_notes,
                f"{sector_absence_reason}；方向退出与确定性减仓信号对该持仓不可用，"
                "涨跌风险需自行跟踪。",
            ]
        cross_note = _discovery_cross_reference_note(facts, holding)
        if cross_note:
            copy.validation_notes = [*copy.validation_notes, cross_note]
        family_divergence_note = _direction_exit_family_note(
            sector_opportunity, facts_row
        )
        if family_divergence_note:
            copy.validation_notes = [*copy.validation_notes, family_divergence_note]
        # 批次费用时机：最终动作是减仓类且有比例时，按先进先出判断触及的批次会不会
        # 撞上 7 天惩罚费窗口。纯事实披露——费用贵不构成回避减仓的理由，只帮用户在
        # "今天减"与"过窗后减"之间权衡。此时 action 与比例都已是终值（escalation
        # 的覆盖在前面完成）。
        if _execution_direction(copy.action) == "reduce":
            lot_note = describe_reduction_lot_impact(
                (facts_row or {}).get("lot_maturity"),
                copy.suggested_position_change_percent,
            )
            if lot_note:
                copy.validation_notes = [*copy.validation_notes, lot_note]
        # 最终动作与用户近几天的真实操作方向相反时（建议加仓而你刚卖过 / 建议减仓而
        # 你刚买过），把那笔操作摆出来让用户结合自己的意图权衡。只披露、不改动作：
        # 方向证据独立于用户的资金需求，系统无从知道那笔操作的动机。
        conflict_note = recent_transaction_conflict_note(
            (facts_row or {}).get("recent_transactions"),
            copy.action,
        )
        if conflict_note:
            copy.validation_notes = [*copy.validation_notes, conflict_note]
        if shadow_note is not None:
            # M6：灰度提示须始终可见（不受 `_backfill_decision_fields` 只在为空时才
            # 回填的规则影响），追加到 validation_notes 末尾，与其它校验备注共存。
            copy.validation_notes = [*copy.validation_notes, shadow_note]
        if proposal_note is not None and note is not proposal_note:
            # 提议与模型草案的分歧必须留痕：shadow 期靠它复盘"该不该切 enforced"，
            # enforced 期靠它说明"为什么最终动作和模型写的不一样"。
            copy.validation_notes = [*copy.validation_notes, proposal_note]
        _sync_decision_path_with_final_action(copy)
        if execution_blocked:
            copy.points = safe_blocked_points(
                copy.points,
                fallback=_blocked_points_fallback(execution_reasons),
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
        _enforce_final_execution_projection(
            copy,
            original_action=original_action,
            holding=holding,
        )
        _humanize_recommendation_text(copy)
        proposal_audit.append(
            {
                **proposal.to_dict(),
                "fund_code": copy.fund_code,
                "llm_action": llm_action,
                "final_action": copy.action,
            }
        )
        guarded.append(copy)

    _apply_correlated_add_dedup(guarded, facts)
    if isinstance(facts, dict) and proposal_audit:
        by_code = {rec.fund_code: rec.action for rec in guarded}
        for row in proposal_audit:
            final = by_code.get(str(row.get("fund_code") or ""))
            if final:
                row["final_action"] = final

    portfolio = _guard_portfolio_lines(portfolio_lines, risk)
    from app.services.decision_data_evidence import contains_trade_instruction_text

    portfolio = [line for line in portfolio if not contains_trade_instruction_text(line)]
    if not portfolio:
        portfolio = ["组合级执行动作以逐基金卡片中的系统校验结果为准。"]
    if evidence_blocked_codes:
        hint = "部分关键持仓或行情数据未达到时点可用条件：本次只做观察和风险提示，暂不显示仓位动作与金额。"
        safe_portfolio = [line for line in portfolio if not contains_executable_decision_text(line)]
        portfolio = [hint, *safe_portfolio[:1]]
    if isinstance(facts, dict):
        facts["data_evidence_guard"] = {
            "execution_blocked": bool(evidence_blocked_codes),
            "blocked_fund_codes": sorted(evidence_blocked_codes),
            "reasons_by_fund": evidence_blocked_codes,
        }
        # 灰度期要靠这份切片回答"切 enforced 会改变多少条结论、都是往哪个方向改"。
        facts["daily_action_proposal"] = {
            "schema_version": DAILY_ACTION_PROPOSAL_SCHEMA_VERSION,
            "mode": settings.daily_action_proposal_mode,
            "divergence_count": sum(
                1 for row in proposal_audit if row["action"] != row["llm_action"]
            ),
            "by_fund": proposal_audit,
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


#: 阻断原因 → 用户能读懂、且能据此判断"要不要等一会儿再看"的那句话。
#:
#: 此前所有阻断共用一句"关键信息还不够完整或不够新"。它落在 `points[0]`，而前端的
#: 「核心理由」渲染的正是 `points[0]`，于是 6 只持仓的核心理由一字不差——用户既不知道
#: 缺的是哪一类数据，也无法判断是"等下一轮就好"还是"这只基金真有问题"。
_BLOCKED_POINT_REASON_TEXT: tuple[tuple[str, str], ...] = (
    (
        "stale_portfolio_snapshot",
        "持仓快照还是上一交易日的，先观察，等快照刷新后再判断。",
    ),
    (
        "non_authoritative_portfolio",
        "本次用的是请求内的临时持仓、非服务端权威快照，先观察。",
    ),
    (
        "holding_amount_not_point_in_time_usable",
        "持仓金额未确认为最新，暂不给出金额与仓位比例，先观察。",
    ),
    (
        "directional_evidence_not_point_in_time_usable",
        "本轮板块方向证据未取到，暂不支持加仓判断；减仓与风险提示不受影响。",
    ),
    (
        "holding_purchase_execution_not_point_in_time_usable",
        "申购可执行状态未核实，本次不给加仓动作。",
    ),
    (
        "holding_redemption_execution_not_point_in_time_usable",
        "赎回可执行状态未核实，本次不给减仓动作。",
    ),
)
_BLOCKED_POINT_FALLBACK_DEFAULT = "关键信息还不够完整或不够新，先观察，等数据更新后再判断。"


def _blocked_points_fallback(reasons: list[str] | tuple[str, ...]) -> str:
    """按真实阻断原因给出兜底首句；认不出的原因才退回通用文案。"""
    codes = {str(reason) for reason in reasons or ()}
    matched = [text for code, text in _BLOCKED_POINT_REASON_TEXT if code in codes]
    if not matched:
        return _BLOCKED_POINT_FALLBACK_DEFAULT
    return matched[0] if len(matched) == 1 else matched[0] + "（另有其他数据项待确认）"


def _execution_direction(action: str) -> str:
    normalized = normalize_action_text(action)
    bucket = _action_bucket(normalized)
    if bucket >= ACTION_BUCKET_ADD:
        return "add"
    # 风控复核与减仓评估同属 REDUCE 档，不能只认「减仓/清仓」字样，
    # 否则超限持仓会停在「复核」且没有可执行比例。
    if bucket <= ACTION_BUCKET_REDUCE:
        return "reduce"
    return "none"


#: 四个加仓档位的**比例值**（相对当前估算持仓）。这四个数字是既有产品策略，本次不动——
#: 动的是"用哪个分数、按哪些阈值落到这四档"。保持档位离散也让
#: `_tier_percent_one_step_down`（基金证据/载体质量各降一级）语义不变。
_ADD_TIER_PERCENTS: tuple[float, ...] = (20.0, 15.0, 10.0, 5.0)

# 旧口径：阈值 85/70/50 手写，且作用在 `research_score` 上。两个问题：
#
# 1. **阈值没有回测依据**。仓库里唯一做过阈值标定的是荐基的方向门槛
#    （`V3_GATE_THRESHOLDS`，`sector_direction_backtest` 网格实测），加仓分档没有。
# 2. **分数本身奖励追涨**。没有主线快照时 `research_score == legacy_score`，那是一个
#    不封顶的动量加权和（`max(change_1d,0)*5 + max(change_5d,0)*4 + 资金 + 热度*0.15`），
#    只在读取时才被 clamp 到 100。一个当日 +4%、五日 +10% 的板块轻松越过 85 拿满档，
#    而荐基 V3 恰恰因为实测 Rank IC 为 -0.011/-0.053 把整个价格结构块**删掉**了。
#
# 因此这份阶梯降级为**兜底**：只在拿不到 V3 方向成熟度层时使用（此时确实只有旧机会分
# 可用）。主路径见 `_V3_ADD_TIER_THRESHOLDS`。
_ADD_POSITION_PERCENT_TIERS = (
    (85.0, _ADD_TIER_PERCENTS[0], "强机会档"),
    (70.0, _ADD_TIER_PERCENTS[1], "较强机会档"),
    (50.0, _ADD_TIER_PERCENTS[2], "中等机会档"),
    (float("-inf"), _ADD_TIER_PERCENTS[3], "小机会试探档"),
)


def _v3_gate_direction_score() -> float:
    """三块**恰好卡在标定入场线**时的合成分（= 可加仓的下边界）。

    `direction_score` 的合成方式与 `V3_BLOCK_WEIGHTS` 完全一致，所以把
    `V3_GATE_THRESHOLDS` 代进同一组权重，得到的就是"刚好够格"对应的分数。当前取值
    0.70*60 + 0.15*35 + 0.15*25 = 51.0。

    两组常量都带实测出处：权重按 T+5 实测 Rank IC 比例（0.338 : 0.064 : 0.066）取整，
    门槛经 `scan_entry_gate_thresholds` 在 v3 分数上网格选取。这里不引入任何新数字。
    """
    return (
        V3_GATE_THRESHOLDS["trend"] * V3_BLOCK_WEIGHTS["trend_strength"]
        + V3_GATE_THRESHOLDS["participation"] * V3_BLOCK_WEIGHTS["participation"]
        + V3_GATE_THRESHOLDS["position"] * V3_BLOCK_WEIGHTS["position_risk"]
    )


#: 满档对应的合成分：三块都到 85 分。因为 `V3_BLOCK_WEIGHTS` 权重和为 1，三块同值时
#: 合成分等于该值，所以这个上锚点就是 85.0——它是"强但可达"的水平，而不是理论上限 100
#: （三块同时满分现实中不出现，拿 100 当上锚会让满档永远取不到）。
_V3_ADD_TIER_TOP_SCORE = 85.0


def _v3_add_tier_thresholds() -> tuple[float, ...]:
    """把 (标定入场线 → 满档锚点) 这段区间**均分**成四档的下界。

    刻意用等分而不是再写三个阈值：等分是"在标定合成分上均匀"，不引入新的主观数字。
    当前取值约 (76.5, 68.0, 59.5, -inf)——注意这些数字作用在 `direction_score` 上，
    量纲与旧的 85/70/50（作用在不封顶的动量和上）完全不同，不可互相套用。
    """
    gate = _v3_gate_direction_score()
    span = _V3_ADD_TIER_TOP_SCORE - gate
    rungs = len(_ADD_TIER_PERCENTS)
    # 区间 [gate, top] 均分成 `rungs` 段，第 i 档（从高到低）的下界是上面第 rungs-1-i 个
    # 分割点；最低档兜底，没有下界。
    return tuple(
        gate + span * (rungs - 1 - index) / rungs for index in range(rungs - 1)
    ) + (float("-inf"),)


#: 四档的展示名，与 `_ADD_TIER_PERCENTS` 一一对应（复用旧阶梯的用词，避免用户面前换词）。
_ADD_TIER_LABELS: tuple[str, ...] = tuple(
    label for _threshold, _percent, label in _ADD_POSITION_PERCENT_TIERS
)


def _resolve_sector_add_tier(sector_opportunity: dict | None) -> tuple[float, str]:
    """板块层决定的加仓档位，返回 `(比例, 依据文案)`。

    优先用荐基已标定的方向合成分（`direction_score`，按实测 Rank IC 定权的三块合成），
    阈值也从标定入场线派生。拿不到 V3 层时才退回旧机会分阶梯——那条路上确实只有旧分数
    可用，但它的阈值与量纲都没有回测支撑，所以只作兜底并在文案里说清楚。
    """
    direction_score = _v3_direction_score(sector_opportunity)
    if direction_score is not None:
        for threshold, percent, label in zip(
            _v3_add_tier_thresholds(),
            _ADD_TIER_PERCENTS,
            _ADD_TIER_LABELS,
        ):
            if direction_score >= threshold:
                return percent, (
                    f"方向合成分 {direction_score:g}（趋势/资金参与度/价格位置按实测 "
                    f"IC 定权），对应{label} {percent:g}%"
                )
    opportunity_score = _opportunity_score(sector_opportunity)
    percent, label = _add_position_percent_for_score(opportunity_score)
    if opportunity_score is None:
        return percent, f"板块机会分暂缺，采用{label} {percent:g}%"
    return percent, (
        f"板块机会分 {opportunity_score:g}，对应{label} {percent:g}%（旧口径兜底）"
    )


def _v3_direction_score(sector_opportunity: dict | None) -> float | None:
    """仅当该行真的带 V3 方向成熟度层时返回标定合成分，否则 None。

    判据是 `score_policy_version`，不是"有没有 direction_score 这个键"——旧口径行也可能
    因为其它原因带上同名键，用版本号判断才不会把两套量纲混起来。
    """
    if not isinstance(sector_opportunity, dict):
        return None
    if str(sector_opportunity.get("score_policy_version") or "") != ENTRY_POLICY_VERSION_V3:
        return None
    value = _num(sector_opportunity.get("direction_score"))
    if value is None:
        return None
    return round(min(max(value, 0.0), 100.0), 2)

# 只有基金自身拿到「高」正向量化支持时，才允许用满板块机会分对应的档位；否则沿同一
# 档位阶梯下调一级。
#
# 加仓比例此前完全由板块机会分决定，于是同一板块里两只基金——一只因子分位靠前、一路
# 证据都指向正向，另一只只有中等支持——会拿到完全相同的比例。板块负责回答「这个方向
# 值不值得加」，基金自身证据负责回答「加在这只上靠不靠得住」，不该由一个分数同时承担。
#
# 三个设计选择：
#
# * **只下调不提额**：`evidence.composite` 的 level 表示"正向收益支持"，按既有契约量化
#   证据「只可增加置信度」，不得作为提额依据。
# * **下调一级而不是绝对封顶**：`evidence` 为 None（`build_holding_evidence` 一个分量都
#   凑不出）在因子 IC 未就绪、组合历史不足 20 交易日时很常见，新用户几乎必然如此。
#   按绝对值封到最小档会把所有人的加仓砍到 5%，那是"证据缺失"而非"基金更弱"，超出了
#   本次要解决的问题。沿阶梯降一级是单步收紧，且复用了既有档位语言。
# * **缺失与「不足」同档处理**：两者都表示拿不到正向支持，把"没算出来"排到"算出来但
#   不足"之后会让更差的证据反而拿到更大仓位。绝大多数「低/不足」本就已被
#   `_weak_evidence_reasons` 降级为观察，走不到分档这一步，残留人群很小。
_FUND_EVIDENCE_FULL_TIER_LEVEL = "高"


def _tier_percent_one_step_down(percent: float) -> float:
    """沿 `_ADD_POSITION_PERCENT_TIERS` 的档位阶梯取更低一级；已在最低档则不变。"""
    lower = [
        tier_percent
        for _threshold, tier_percent, _label in _ADD_POSITION_PERCENT_TIERS
        if tier_percent < percent
    ]
    return max(lower) if lower else percent


def _fund_evidence_add_percent(
    sector_percent: float,
    evidence: dict | None,
) -> tuple[float, str | None]:
    """按基金自身正向量化支持决定是否把板块档位下调一级。

    **证据不可用不降档。** 本仓既有原则是「证据缺失 ≠ 基金更弱」（见本文件里
    "绝对封顶等于把「证据缺失」当成「基金更弱」" 那条注释），但此前这里只判
    `level != 高` 就降一档，等于把"没有可用证据"也算成短板。

    这在当前配置下不是边缘情形而是全体命中：因子可靠性由
    `factor_confidence._research_factor_confidence` 按 peer_group 给出，
    `cohort_mode="current_survivors"` 时**天花板只有「中」**（该函数注释写明），
    而这里要求「高」才用满档 —— 于是**任何持仓在任何一天都必然被降一档**，
    一个恒定的降档不携带任何信息，只是伪装成证据推理的全局保守系数。

    现在分三种情形：
      * 有可用证据且为「高」→ 满档；
      * 有可用证据但偏弱     → 降一档（这才是真的"基金更弱"）；
      * 没有可用证据         → 不动档位（可靠性不达标时该路压根没产出结论）。
    """
    level = _composite_level(evidence)
    if str(level or "") == _FUND_EVIDENCE_FULL_TIER_LEVEL:
        return sector_percent, None
    if not _fund_evidence_is_usable(evidence):
        return sector_percent, None
    stepped = _tier_percent_one_step_down(sector_percent)
    if stepped >= sector_percent:
        return sector_percent, None
    reason = (
        f"基金自身正向量化支持{level}"
        if level
        else "基金自身量化支持暂缺"
    )
    return stepped, f"{reason}，档位下调至 {stepped:g}%"


def _fund_evidence_is_usable(evidence: dict | None) -> bool:
    """是否存在**可靠性放行**的收益类证据。

    `reliability.usable` 由 `signal_synthesis._reliability_block` 写入（可靠性 ∈ {高, 中}）。
    没有这类分量时，基金侧量化证据这一路没有产出可用结论——既不该背书，也不该当短板。
    """
    if not isinstance(evidence, dict):
        return False
    components = evidence.get("components")
    if not isinstance(components, (list, tuple)):
        return False
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("role") != "return_signal":
            continue
        reliability = component.get("reliability")
        if isinstance(reliability, dict) and reliability.get("usable") is True:
            return True
    return False


# 方向成熟度（`sector_entry_maturity.2026-08.v3`）在日报侧的两处消费。
#
# 这一层此前在日报**完全不存在**：`describe_sector_opportunity` 的 `entry_policy_enabled`
# 默认 False，所以日报一直只拿旧版机会分。接入后同一份方向数据在荐基和日报有了同一个口径
# ——此前荐基对一个过热方向只会按 40% 试仓，日报却给满档加仓，两个界面对同一天同一板块
# 给出互相矛盾的仓位。
_ENTRY_STATE_READY = "ready_to_start"
#: 这些档位默认不构成"现在可以加"的方向依据。`forming` 是"条件形成中"，
#: 但 `probability_early_probe_eligible` 时视为潜伏/蓄势，允许按试仓系数小额提前布局。
#: `invalid` 是"趋势或资金未通过"，`ready_on_pullback` 是"方向成立但当前位置不宜追"。
_ENTRY_STATES_BLOCKING_ADD = {
    "forming": "板块方向条件仍在形成中",
    "invalid": "板块趋势或资金未通过入场线",
    "ready_on_pullback": "板块方向成立但当前位置不宜追高",
}


def _direction_continuity_evidence(sector_opportunity: dict | None) -> str | None:
    """把跨日滞回的连续达标天数写成人话，供 `sector_evidence` 展示。

    这是「今天刚满足」和「至少连续 3 天满足」的唯一区分点。日报此前完全拿不到它——
    方向状态账本只被荐基读写，日报每天看到的都是当日原始档位，同一板块的方向结论会
    随阈值边界上的一两分之差来回翻。

    两条披露纪律：

    * **天数是下界**。账本由荐基写入，荐基没跑的那天没有记录，streak 会从 1 重新起算
      （见 `report_sector_opportunity._HYSTERESIS_READ_ONLY_NOTE`），所以措辞用「至少」。
    * **滞回带内要说清楚**。`entry_state` 与 `raw_entry_state` 不一致时，是"此前已确认、
      今日未跌破退出线"，不是"今日重新确认"。混为一谈会让用户以为方向今天又被验证了一次。
    """
    if not isinstance(sector_opportunity, dict):
        return None
    days = _num(sector_opportunity.get("consecutive_qualifying_days"))
    entry_state = str(sector_opportunity.get("entry_state") or "")
    raw_state = str(sector_opportunity.get("raw_entry_state") or "")
    if raw_state and entry_state and raw_state != entry_state:
        if entry_state == _ENTRY_STATE_READY:
            return "方向此前已确认，今日未跌破退出线（滞回带内保留，非今日重新确认）"
        return None
    if days is None or days < 2:
        return None
    return f"方向已至少连续 {int(days)} 个交易日通过入场线"


def _hysteresis_held_raw_entry_state(sector_opportunity: dict | None) -> str | None:
    """滞回把档位**保留**在 ready_to_start 时返回当日原始档位，否则 None。

    `apply_direction_state_hysteresis` 在"昨天已 ready 且趋势未跌破退出线"时把
    `entry_state` 抬回 `ready_to_start`，同时留下 `raw_entry_state`（当日原始档位）与
    `qualifies_for_ready=False`（今天并没有重新通过入场线）。三者同时出现就是滞回保留。

    判据要同时看 `qualifies_for_ready`：只比较 raw≠entry 会把"延迟确认"那条相反的分支
    （raw=ready_to_start 被压成 forming）也算进来，那种情况 entry_state 不是 ready，
    本函数已在第一步返回 None，但显式判 `qualifies_for_ready` 让语义不依赖分支顺序。
    """
    if not isinstance(sector_opportunity, dict):
        return None
    if str(sector_opportunity.get("entry_state") or "") != _ENTRY_STATE_READY:
        return None
    if sector_opportunity.get("qualifies_for_ready") is True:
        return None
    raw_state = str(sector_opportunity.get("raw_entry_state") or "")
    if not raw_state or raw_state == _ENTRY_STATE_READY:
        return None
    return raw_state


#: V3 三块入场门禁：`(行上的分数键, V3_GATE_THRESHOLDS 的键, 展示名)`。
_V3_GATE_LABELS: tuple[tuple[str, str, str], ...] = (
    ("trend_strength_score", "trend", "趋势强度"),
    ("participation_score", "participation", "资金参与度"),
    ("position_risk_score", "position", "价格位置"),
)


def _v3_failed_gates(
    sector_opportunity: dict | None,
) -> list[tuple[str, str, float, float]]:
    """今日未通过的 V3 入场门禁项：`(gate_key, 展示名, 实测值, 门槛)`。

    直接对着 `V3_GATE_THRESHOLDS` 逐项比，不复用
    `_ENTRY_STATES_BLOCKING_ADD[raw_state]` 那句话——`ready_on_pullback` 的既有文案是
    「当前位置不宜追高」，但 `classify_entry_state_v3` 里该档位由**资金参与度或价格位置**
    任一项未过触发（煤炭 2026-08-13 实测就是参与度 28.93<35，价格位置 84.6 远超门槛 25）。
    照搬那句话会把原因说反。

    返回结构化元组而不是拼好的字符串：调用方要按 `gate_key` 区分"趋势"与"非趋势"失败项，
    对中文文案做前缀/`in` 匹配的判据改一个字就会静默失效（本仓已有先例，见
    `signal_synthesis` 里删掉的符号翻转）。
    """
    if not isinstance(sector_opportunity, dict):
        return []
    failed: list[tuple[str, str, float, float]] = []
    for score_key, gate_key, label in _V3_GATE_LABELS:
        value = _num(sector_opportunity.get(score_key))
        threshold = _num(V3_GATE_THRESHOLDS.get(gate_key))
        if value is None or threshold is None:
            continue
        if value < threshold:
            failed.append((gate_key, label, value, threshold))
    return failed


def _format_failed_gates(failed: list[tuple[str, str, float, float]]) -> str:
    return "、".join(
        f"{label} {value:.1f}<{threshold:g}" for _key, label, value, threshold in failed
    )


#: 滞回态小额试探的试仓系数。**取既有常量，不引入新数字**：本仓所有"已授权试仓通道"
#: 用的都是 0.4（`V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE` == `V3_EARLY_PROBE_FIRST_TRANCHE_CAP`
#: == `V3_TREND_TRANCHE_SCALES` 的地板，后者的注释写明"刚过线只给 0.4，说明够格买不等于
#: 够格买满"）。系数说的是"这是一次试仓"，档位说的是"而且是最弱的一种"——见
#: `_hysteresis_probe_eligible`。
_HYSTERESIS_PROBE_TRANCHE_SCALE = V3_IMPROVING_FLOW_FIRST_TRANCHE_SCALE


def _hysteresis_probe_eligible(sector_opportunity: dict | None) -> bool:
    """滞回态是否够格给一次**最低档小额试探**（而不是完全不加）。

    四个条件全要满足：

    1. **确实是滞回保留**（`_hysteresis_held_raw_entry_state` 非空）；
    2. **方向层本轮没有任何已授权的投入比例**（`first_tranche_scale` 缺席或非正）。这一条
       划定了本通道的适用边界：它**只用来替代 fail-closed**，绝不盖过已标定的通道。
       2026-08-13 的数字经济同时满足前后几条，但它的 `flow_improving_probe_eligible=true`
       已经授权了 0.4——那是"今日资金确认转强"换来的、比本通道强的证据，若让试探档抢先
       封顶，它会从应得的 4% 被降到 2%，等于用更弱的判据覆盖更强的判据；
    3. **失败项里有非趋势门禁**——只有趋势在带内抖动的那种情况压根不走试探路径，它按
       `test_report_direction_hysteresis` 契约 3 拿的是正常加仓资格；
    4. **趋势本身仍在入场线之上**。滞回只保证趋势 ≥ 退出线 52，而"次要门禁滑了一下、我们
       真正信的那根轴还在过线"与"两根轴一起掉下来"是两回事，后者不给试探。这条不引入新
       阈值，用的还是 `V3_GATE_THRESHOLDS["trend"]`。

    **为什么档位要单独封顶、不能只靠系数。** 加仓档位由 `direction_score` 决定，而它给趋势
    的权重是 0.70（`V3_BLOCK_WEIGHTS`）。滞回态这批行的共同特征恰恰是"趋势强、参与度弱"，
    所以它们的 `direction_score` 天然偏高——2026-08-13 线上黄金 78.92（强机会档 20%）而
    参与度是 **0.0**。只乘一个系数的话，参与度最差的那只反而拿到最大的试探仓位（20%×0.4=8%
    > 数字经济那条已标定通道的 4%），倒挂原样保留。因此档位另外封到既有阶梯的最低档
    （`_ADD_TIER_PERCENTS[-1]`，本仓命名为「小机会试探档」），三只一律 5%×0.4=**2%**——
    刻意不做区分，因为我们并没有能区分它们的证据。

    这样排序才是对的：滞回态试探 2% < 已标定试仓通道 4%（数字经济，今日资金确认转强）
    < 当日三块全过 8%（医疗）。

    注意这道口子开得比看起来窄：`_weak_evidence_reasons` 的板块侧检查仍在它之后生效，
    所以 `pattern_label ∈ {distribution, weak_outflow}` 的方向照样被拦——2026-08-13 的
    黄金与稀土正是这样，最终只有资金形态中性的煤炭能拿到这 2%。
    """
    if _hysteresis_held_raw_entry_state(sector_opportunity) is None:
        return False
    assert isinstance(sector_opportunity, dict)  # 上一步已保证
    authorized_scale = _num(sector_opportunity.get("first_tranche_scale"))
    if authorized_scale is not None and authorized_scale > 0.0:
        return False
    failed = _v3_failed_gates(sector_opportunity)
    if not [row for row in failed if row[0] != "trend"]:
        return False
    trend = _num(sector_opportunity.get("trend_strength_score"))
    trend_gate = _num(V3_GATE_THRESHOLDS.get("trend"))
    return trend is not None and trend_gate is not None and trend >= trend_gate


def _hysteresis_hold_add_block_reason(sector_opportunity: dict) -> str | None:
    """滞回保留档位时，加仓是否仍被拦；滞回带覆盖得住就返回 None。

    **滞回带只对趋势定义。** `EXIT_TREND_THRESHOLD = V3_GATE_THRESHOLDS["trend"] - 8`，
    而 `apply_direction_state_hysteresis` 的保留条件也只校验一件事：
    `trend >= exit_trend_threshold`。所以这条带子只能为"趋势在入场线附近抖动"背书——那正
    是它被造出来的场景，也是 `test_report_direction_hysteresis` 契约 3 锁住的行为：趋势 55
    落在 [52, 60) 内、参与度与价格位置都过线时，方向在荐基与日报都应保持可加仓，否则同一
    板块两个界面结论相反。

    **它背书不了别的门禁。** 资金参与度或价格位置今天没过，与"趋势在带内"没有任何关系，
    可此前这里只读 `entry_state`，滞回于是把它们一并放过去：2026-08-13 线上煤炭趋势 67.28
    （在入场线之上、压根不在带内），失败项是参与度 28.93 < 35，就这样把模型草案「观察」
    抬成「分批加仓 +10%」，而同一行的 `first_tranche_scale` 是 `None`（卡片显示
    「本轮不投入」）。当天 5 只持仓有 4 只处于同样的滞回态，另外 3 只只是恰好被"板块资金流
    偏弱"或"涨后回吐"这些**无关**的门禁挡住。

    参与度略低于门槛本来就有专门的标定通道（`flow_improving_probe_eligible`，条件是
    `V3_IMPROVING_FLOW_PARTICIPATION_FLOOR <= participation < 门槛` 且今日资金转强），调用方
    在本函数之前先判它。同一天的数字经济（参与度 29.24）正是走那条通道拿到小额加仓，而煤炭
    / 黄金 / 稀土 不符合该通道的条件——系统本来就有正确的机制，缺陷是滞回把它绕过去了。

    **非趋势门禁未过时不再一律封死，改为最低档小额试探**（用户决策，2026-08-13）：条件与
    比例见 `_hysteresis_probe_eligible`，够格时本函数返回 None（放行），由
    `_resolve_deterministic_position_change` 把档位封到最低档并套上试仓系数。仍够不上试探
    的（趋势也掉到入场线以下、或命中非数值否决项）照旧封死。
    """
    if _hysteresis_probe_eligible(sector_opportunity):
        return None
    failed = _v3_failed_gates(sector_opportunity)
    non_trend_failed = [row for row in failed if row[0] != "trend"]
    trend = _num(sector_opportunity.get("trend_strength_score"))
    trend_gate = _num(V3_GATE_THRESHOLDS.get("trend"))
    trend_inside_band = (
        trend is not None
        and trend_gate is not None
        and EXIT_TREND_THRESHOLD <= trend < trend_gate
    )
    if not non_trend_failed and trend_inside_band:
        return None
    if non_trend_failed:
        # 走到这里说明试探也不够格——趋势同时掉到入场线以下，两根轴一起坏了。
        return (
            f"方向今日未重新通过入场线（{_format_failed_gates(non_trend_failed)}），"
            "且趋势已回落到入场线之下，滞回带不覆盖这些门禁"
        )
    # 三块分数都在门槛之上却仍未达标：`classify_entry_state_v3` 的非数值否决项
    # （证据质量不可用、主线状态非方向性）命中了。同样不构成"可以继续加"的依据。
    return "方向今日未重新通过入场线（滞回带内保留），本轮只支持持有、不支持加仓"


def _entry_state_add_block_reason(sector_opportunity: dict | None) -> str | None:
    """方向成熟度档位是否拦住加仓。快照缺席（无 entry_state）时返回 None，不拦。

    **滞回保留的 ready_to_start 不自动等于"今日可以继续加"。** 这是本函数最容易读错的一点：
    `apply_direction_state_hysteresis` 的职责是压掉方向标签在阈值边界上的抖动
    （「不改变任何分数，也不放宽入场线」是它自己的 docstring），而它的保留条件只校验趋势。
    此前这里只读 `entry_state`，于是滞回抬起来的档位被当成"今日三块入场门禁都已通过"，
    参与度与价格位置在滞回之后**再没有任何地方复查**——荐基对同一状态给出的
    `entry_triggers` 写的正是「买入并录入持仓后，由日报根据资金参与度与价格位置决定是否
    加仓」，复查本来就该由日报做。判定细节与实测证据见
    `_hysteresis_hold_add_block_reason`。
    """
    if not isinstance(sector_opportunity, dict):
        return None
    state = str(sector_opportunity.get("entry_state") or "")
    if not state:
        return None
    held_raw_state = _hysteresis_held_raw_entry_state(sector_opportunity)
    if held_raw_state is None and state == _ENTRY_STATE_READY:
        return None
    # 资金刚转强、以及潜伏期提前试仓，两条通道都已经过门槛标定。日报继续开门，
    # 否则同一板块会和荐基打架：荐基已经按 first_tranche_scale 试仓，日报却把现持仓
    # 锁死在观察。放在滞回判定之前是刻意的：通道由当日原始档位开出。
    if sector_opportunity.get("flow_improving_probe_eligible") is True:
        return None
    if sector_opportunity.get("probability_early_probe_eligible") is True:
        return None
    if held_raw_state is not None:
        return _hysteresis_hold_add_block_reason(sector_opportunity)
    return _ENTRY_STATES_BLOCKING_ADD.get(state)


def _apply_tranche_scale(
    current_percent: float,
    scale: float,
    *,
    detail: str | None = None,
) -> tuple[float, str | None]:
    """把试仓系数乘上去（只降不升），返回 `(比例, 依据文案)`。

    乘法与"向下取整到 0.1"只写一处：`first_tranche_scale` 与滞回态试探两条路径都要用它，
    各写一遍必然在取整口径上漂移。
    """
    scaled = floor(max(current_percent * scale, 0.0) * 10) / 10
    if scaled >= current_percent:
        return current_percent, None
    suffix = f"（{detail}）" if detail else ""
    return scaled, f"方向分段试仓系数 {scale:.0%}{suffix}，本次比例缩至 {scaled:g}%"


def _first_tranche_scaled_percent(
    current_percent: float,
    sector_opportunity: dict | None,
) -> tuple[float | None, str | None]:
    """按方向成熟度的 `first_tranche_scale` 缩小本次加仓比例。

    与 `_fund_evidence_add_percent` / `_vehicle_quality_add_percent` 的"沿档位阶梯降一级"
    不同，这里是**乘法缩放**——因为它本身就是荐基定义的"本次投入占计划仓位的比例"
    （过热 0.6、拥挤 0.4、概率不足按概率档）。把它转成档位下调会丢掉这个语义，
    也会与荐基对同一方向给出的金额不一致。

    同样只降不升：`scale >= 1` 时原样返回。

    **返回 `None` 表示本轮不授权加仓**（调用方据此把动作降为观察）。这条分支修的是一个
    静默失败：`describe_sector_opportunity` 在"没有任何入场通道授权投入"时发布
    `first_tranche_scale = None`（前端渲染成「本轮不投入」），而
    `sector_opportunity_scoring.py` 里选择发 `None` 而不是 `0.0` 的注释写着「两处都另有
    entry_state 门禁在前，本身不会走到」——滞回把 `entry_state` 抬成 ready_to_start 之后
    这个前提不成立了，于是 `None` 在这里被当成"没有可用的缩放系数"，**原样返回满档比例**。
    2026-08-13 线上实测：煤炭 `first_tranche_scale=None` 却发出「分批加仓 +10%」，
    同一天黄金（20% 档）与稀土（15% 档）也带着 `None`，只是被别的门禁挡住了。

    只在成熟度层**确实在场**（`score_policy_version` 为 V3）时 fail-closed。旧口径行没有
    这一层，缺 `first_tranche_scale` 是"本来就没有这个概念"而不是"没被授权"，行为必须与
    接入成熟度层之前完全一致（见 `test_absent_maturity_layer_changes_nothing`）。
    """
    if not isinstance(sector_opportunity, dict):
        return current_percent, None
    scale = _num(sector_opportunity.get("first_tranche_scale"))
    maturity_present = (
        str(sector_opportunity.get("score_policy_version") or "") == ENTRY_POLICY_VERSION_V3
    )
    if scale is None or scale <= 0.0:
        if maturity_present:
            return None, (
                "方向层本轮未授权任何投入比例（first_tranche_scale 缺席），"
                "不得据此加仓，已降为观察。"
            )
        return current_percent, None
    if scale >= 1.0:
        return current_percent, None
    overheat = [
        str(item).strip()
        for item in sector_opportunity.get("overheat_flags") or []
        if str(item).strip()
    ]
    return _apply_tranche_scale(
        current_percent,
        scale,
        detail="、".join(overheat[:2]) or None,
    )


def _vehicle_quality_add_percent(
    current_percent: float,
    vehicle_quality: dict | None,
) -> tuple[float | None, str | None]:
    """被动载体质量未达标时再把档位下调一级。

    与 `_fund_evidence_add_percent` 是两个独立维度：量化证据回答"这只基金接下来的收益
    支持强不强"，载体质量回答"这只工具本身合不合格"（规模、费率、跟没跟住基准）。两者
    都不达标就各降一级，下限仍是既有阶梯的最低档。

    三条纪律与加仓分档一致：

    * **只对 `applicable=True` 生效**。主动持仓拿到的是 `not_applicable`（日报没有经理
      业绩证据），缺失同样不触发——两者都是"没有可判断的证据"，不是"载体更差"。
    * **只下调不提额**：`eligible` 不会换来比板块档位更大的仓位。
    * **适用持仓硬拦加仓**。持仓侧评分已经去掉 `sector_match_kind` 身份硬门，只看规模、
      费率、跟踪误差；`applicable=True` 且 `watch_only` 表示工具本身不合格。这时正确
      动作是观察并评估同方向更好的载体，而不是给现持仓再加一档。主动持仓
      `not_applicable` 仍不触发——没有经理业绩证据不等于载体更差。
    """
    if not _vehicle_quality_blocks_add(vehicle_quality):
        return current_percent, None
    assert isinstance(vehicle_quality, dict)
    penalties = [
        str(item).strip()
        for item in vehicle_quality.get("penalties") or []
        if str(item).strip()
    ]
    detail = f"（{'、'.join(penalties[:2])}）" if penalties else ""
    return None, f"被动载体质量未达标{detail}，本轮不加现持仓"


def _vehicle_quality_blocks_add(vehicle_quality: object) -> bool:
    if not isinstance(vehicle_quality, dict):
        return False
    return (
        vehicle_quality.get("applicable") is True
        and str(vehicle_quality.get("status") or "") == "watch_only"
    )


def _additional_add_blocks(
    vehicle_quality: object,
    facts_row: dict | None,
    sector_opportunity: dict | None,
    *,
    facts: dict | None,
    avoid_chasing: bool = False,
) -> tuple[str, ...]:
    blocks: list[str] = []
    if _vehicle_quality_blocks_add(vehicle_quality):
        blocks.append("vehicle_quality_not_eligible")
    if _recent_buy_add_block_reason(facts_row, sector_opportunity, facts=facts):
        blocks.append("add_interval_not_elapsed")
    if true_overheat_add_block_reason(sector_opportunity, strict=avoid_chasing):
        blocks.append("true_overheat")
    return tuple(blocks)


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _recent_buy_add_block_reason(
    facts_row: dict | None,
    sector_opportunity: dict | None,
    *,
    facts: dict | None = None,
) -> str | None:
    """同一基金距上次买入未满短持惩罚费窗口、且未见回踩时，禁止再加。

    缺交易摘要或日期时不拦——"不知道"不等于"刚买过"。当日板块涨跌 < 0 视为一次
    可执行回踩，间隔放开。
    """
    recent = (facts_row or {}).get("recent_transactions")
    if not isinstance(recent, dict) or recent.get("available") is not True:
        return None
    last_buy = recent.get("last_buy")
    if not isinstance(last_buy, dict):
        return None
    buy_date = _parse_iso_date(last_buy.get("trade_date"))
    as_of = _parse_iso_date(recent.get("as_of_date"))
    if as_of is None and isinstance(facts, dict):
        session = facts.get("session")
        if isinstance(session, dict):
            as_of = _parse_iso_date(session.get("effective_trade_date"))
    if buy_date is None or as_of is None:
        return None
    elapsed = (as_of - buy_date).days
    if elapsed < 0 or elapsed >= ADD_INTERVAL_CALENDAR_DAYS:
        return None
    change_1d = _num((sector_opportunity or {}).get("change_1d_percent"))
    if change_1d is None and isinstance(sector_opportunity, dict):
        mainline = sector_opportunity.get("mainline_regime")
        features = mainline.get("features") if isinstance(mainline, dict) else None
        if isinstance(features, dict):
            change_1d = _num(features.get("change_1d_percent"))
    if change_1d is not None and change_1d < 0:
        return None
    remaining = ADD_INTERVAL_CALENDAR_DAYS - elapsed
    return (
        f"距 {buy_date.isoformat()} 买入未满 {ADD_INTERVAL_CALENDAR_DAYS} 个自然日"
        f"（还差 {remaining} 天出惩罚赎回费窗口），且未见可执行回踩，本轮不再加仓。"
    )


def _same_daily_add_risk(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if frozenset((a, b)) in _CORRELATION_DEDUP_EXEMPT_PAIRS:
        return False
    if _direction_identity(a) == _direction_identity(b):
        return True
    group_a = _sector_group(a)
    group_b = _sector_group(b)
    named_groups = set(_SECTOR_GROUPS.values())
    return bool(group_a == group_b and group_a in named_groups)


def _apply_correlated_add_dedup(
    guarded: list[FundRecommendation],
    facts: dict | None,
) -> None:
    """同一笔风险暴露上，当天只允许方向分最强的一只加仓，其余暂停追涨。"""
    if not isinstance(facts, dict) or len(guarded) < 2:
        return
    holdings = facts.get("holdings")
    if not isinstance(holdings, list):
        return
    rows_by_code: dict[str, dict] = {}
    for row in holdings:
        if isinstance(row, dict) and row.get("fund_code"):
            rows_by_code[str(row["fund_code"])] = row

    add_indexes = [
        index
        for index, rec in enumerate(guarded)
        if _execution_direction(rec.action) == "add"
    ]
    if len(add_indexes) < 2:
        return

    def _label_for(rec: FundRecommendation) -> str:
        row = rows_by_code.get(rec.fund_code) or {}
        opportunity = row.get("sector_opportunity")
        if isinstance(opportunity, dict) and opportunity.get("sector_label"):
            return str(opportunity["sector_label"])
        return str(row.get("sector_label") or rec.fund_name or rec.fund_code)

    def _strength(rec: FundRecommendation) -> tuple[float, float, float]:
        row = rows_by_code.get(rec.fund_code) or {}
        opportunity = row.get("sector_opportunity")
        if not isinstance(opportunity, dict):
            opportunity = {}
        return (
            _num(opportunity.get("direction_score")) or -1.0,
            _num(opportunity.get("trend_strength_score")) or -1.0,
            float(rec.suggested_position_change_percent or 0.0),
        )

    ranked = sorted(add_indexes, key=lambda index: _strength(guarded[index]), reverse=True)
    kept_labels: list[str] = []
    for index in ranked:
        rec = guarded[index]
        label = _label_for(rec)
        rival = next((other for other in kept_labels if _same_daily_add_risk(label, other)), None)
        if rival is None:
            kept_labels.append(label)
            continue
        rec.action = "暂停追涨"
        rec.suggested_position_change_percent = None
        rec.suggested_position_change_basis = ""
        note = (
            f"与已持仓方向「{rival}」同属一笔风险暴露，本轮只允许更强的一只加仓。"
        )
        rec.points = [note, *rec.points]
        rec.validation_notes = [*rec.validation_notes, note]
        _sync_decision_path_with_final_action(rec)


def _unrealized_loss_add_percent(
    current_percent: float,
    holding_return_percent: float | None,
) -> tuple[float, str | None]:
    """该仓自身还没转正时，把加仓档位封到阶梯最低档，返回 `(比例, 依据文案)`。

    **这道门禁量的是"你自己的成本"，与既有的任何一道都不重叠。** 板块层的
    `position_risk`（界面写作「结构修复度」）量的是**板块自己的价格位置**，`direction_score`
    量的是方向强度，`first_tranche_scale` 量的是"本轮授权投入多少"——三者都不知道**你**是在
    什么价位买进来的。此前 `estimated_holding_return_percent` 在本模块只被读过一次，而且只
    用在减仓侧（`resolve_escalation_floor` 的 `has_unrealized_gain`，决定 −1/4 还是 −1/3），
    加仓侧完全没有成本基准判据：一只已经浮亏 8% 的持仓，只要板块方向还在线上，就会拿到与
    浮盈 8% 那只完全相同的档位。

    **实测依据。** `scripts/run_position_sizing_backtest.py` 在同一批 PIT 入场信号上做配对
    比较（逐 episode 相减，两边共用同一批 episode 与同一套退出规则），本档位封顶口径相对
    现状：9 组 (最长持有期 × 止损幅度) 参数下**均值差全部为正**，7 组 |t| >= 2；20 日 / −10%
    那组为 +0.157%（t=2.90），87.2% 的 episode 上不劣于现状。同时它**更省**：平均投出
    47.5%（现状 51.4%）、费用 0.36%（现状 0.40%）。

    它不是靠提高胜率赚钱——中位差是 0.000%，绝大多数 episode 没有变化——而是砍掉了"在亏损
    里越买越多"那条尾巴。这也是它为什么被选中：收益是尾部保护，代价接近零。

    **两处刻意的保守。**

    1. 更强的口径（浮亏时**完全不加**）在同一份数据上更好（20 日 / −10% 为 +0.259%，
       t=3.03；相对本口径再 +0.101%，t=2.62），但它会把"方向仍然成立、只是买点略差"的仓位
       也一并冻住。这里先取弱口径，强口径留作后续决策。
    2. 判据用 `> 0` 而不是"> 往返费率"，因为回测验证的就是 `> 0`；换成费率门槛等于引入一个
       没测过的阈值。

    **样本限制必须一起读**：71 个 episode、2026-01~08 单一**下行**区间（等权基准 −6.59%、
    最大回撤 −19.55%），标的是板块指数而非可买到的基金。下行区间对"少加仓"这类结论天然友好，
    所以这里只取了受该偏置影响最小的那一条（条件规则，涨市里大部分时候压根不触发），并且
    没有据此调整任何档位数值本身。

    ``holding_return_percent`` 为 ``None`` 时**不封顶**：那表示"这一轮拿不到持有收益口径"，
    与"确实在亏"是两件事。生产路径由 `apply_recommendation_guards` 从 `analysis_facts` 的
    `estimated_holding_return_percent` 透传（与界面「持有」列、减仓侧 `has_unrealized_gain`
    同一个数），不在这里重算——`holding_estimates` 的权威实现需要逐持仓的 `FundProfile`，
    而 `analysis_facts` 特意用 `resolve_matched_profiles` 批量取过了。无成熟度层的旧口径行
    同样保持原样（见 `test_absent_maturity_layer_changes_nothing`）。
    """
    if holding_return_percent is None or holding_return_percent > 0:
        return current_percent, None
    floor_percent = _ADD_TIER_PERCENTS[-1]
    if current_percent <= floor_percent:
        return current_percent, None
    return floor_percent, (
        f"该仓估算持有收益 {holding_return_percent:+.2f}% 尚未转正，"
        f"档位封到最低档 {floor_percent:g}%"
    )


def _resolve_deterministic_position_change(
    action: str,
    *,
    holding: Holding | None,
    profile: InvestorProfile,
    weight_denominator: float,
    sector_opportunity: dict | None,
    evidence: dict | None = None,
    vehicle_quality: dict | None = None,
    holding_return_percent: float | None = None,
) -> tuple[float | None, str, str | None]:
    """Return a server-owned percentage relative to the estimated holding value.

    Daily reports intentionally avoid exact share or yuan sizing. The percentage
    remains useful when an OCR import only provides current market value, while
    concentration and transaction gates still have deterministic control.
    """

    direction = _execution_direction(action)
    if direction == "none" or holding is None or holding.holding_amount <= 0:
        note = None
        if direction == "add":
            note = "当前持仓估值不可用，系统无法计算可靠的相对调整比例，已改为观察。"
        return None, "", note

    current_amount = float(holding.holding_amount)
    if direction == "reduce":
        bucket = _action_bucket(action)
        if bucket <= ACTION_BUCKET_CLEAR_ALL:
            percent = -100.0
            tier = "减仓 1/1 风险档位"
        elif bucket <= ACTION_BUCKET_DEEP_REDUCE:
            percent = -50.0
            tier = "减仓 1/2 风险档位"
        else:
            percent = -25.0
            tier = "减仓 1/4 风险档位"
        return (
            percent,
            f"相对当前估算持仓计算；{tier}",
            None,
        )

    sector_percent, sector_tier_basis = _resolve_sector_add_tier(sector_opportunity)
    # 滞回态小额试探：档位封到既有阶梯最低档。必须在基金证据/载体质量降档之前封，否则
    # 「降一级」会从一个本不该出现的高档位起算（黄金 20% 降一级仍是 15%，而这批行本来就
    # 只配最低档）。理由与排序见 `_hysteresis_probe_eligible`。
    hysteresis_probe = _hysteresis_probe_eligible(sector_opportunity)
    if hysteresis_probe:
        probe_tier = _ADD_TIER_PERCENTS[-1]
        if probe_tier < sector_percent:
            failed = _format_failed_gates(
                [
                    row
                    for row in _v3_failed_gates(sector_opportunity)
                    if row[0] != "trend"
                ]
            )
            sector_percent = probe_tier
            sector_tier_basis = (
                f"方向今日未重新通过入场线（{failed}）但趋势仍在入场线之上，"
                f"滞回带内按最低档小额试探 {probe_tier:g}%"
            )
    # 该仓自身未转正时封到最低档。必须与滞回试探同样在基金证据/载体质量降档**之前**封，
    # 理由相同：「降一级」若从一个本不该出现的高档位起算，20% 降一级仍是 15%。
    sector_percent, loss_basis = _unrealized_loss_add_percent(
        sector_percent,
        holding_return_percent,
    )
    # 板块决定机会档位，基金自身证据与被动载体质量各自只能把它往下调一级，不能提额。
    base_percent, evidence_basis = _fund_evidence_add_percent(sector_percent, evidence)
    base_percent, vehicle_basis = _vehicle_quality_add_percent(
        base_percent,
        vehicle_quality,
    )
    if base_percent is None:
        return None, "", vehicle_basis
    # 方向成熟度的分段试仓系数最后作用（乘法缩放，语义是"本次投入占计划仓位的比例"）。
    # 返回 None 表示方向层本轮压根没授权投入——此时不能退回"不缩放"，必须放弃这次加仓。
    #
    # 滞回态试探自带系数：这批行的 `first_tranche_scale` 恰恰是 `None`（原始档位没开任何
    # 通道，正是它触发了 fail-closed），所以试探路径必须显式提供系数，否则会被自己的
    # fail-closed 挡回去。取值与"已授权试仓通道"同一个 0.4，档位那层已经把它压到最低档。
    if hysteresis_probe:
        base_percent, tranche_basis = _apply_tranche_scale(
            base_percent,
            _HYSTERESIS_PROBE_TRANCHE_SCALE,
            detail="滞回带内试探",
        )
    else:
        scaled_percent, tranche_basis = _first_tranche_scaled_percent(
            base_percent,
            sector_opportunity,
        )
        if scaled_percent is None:
            return None, "", tranche_basis
        base_percent = scaled_percent
    limit_ratio = max(min(float(profile.concentration_limit_percent), 100.0), 0.0) / 100
    max_add_amount: float | None
    if profile.expected_investment_amount is not None and profile.expected_investment_amount > 0:
        max_add_amount = max(
            float(profile.expected_investment_amount) * limit_ratio - current_amount,
            0.0,
        )
    elif limit_ratio >= 1:
        max_add_amount = None
    elif limit_ratio <= 0:
        max_add_amount = 0.0
    else:
        # Without a fixed target total, the post-purchase denominator also grows:
        # (current + x) / (portfolio + x) <= concentration_limit.
        max_add_amount = max(
            (limit_ratio * float(weight_denominator) - current_amount)
            / (1 - limit_ratio),
            0.0,
        )

    resolved_percent = base_percent
    capped = False
    if max_add_amount is not None:
        concentration_percent = max_add_amount / current_amount * 100
        if concentration_percent < resolved_percent:
            resolved_percent = concentration_percent
            capped = True
    resolved_percent = floor(max(resolved_percent, 0.0) * 10) / 10
    if resolved_percent < 0.1:
        return (
            None,
            "",
            "当前持仓已接近或达到单只集中度上限，本次不再增加仓位。",
        )

    basis = f"相对当前估算持仓计算；{sector_tier_basis}"
    if loss_basis:
        basis += f"；{loss_basis}"
    if evidence_basis:
        basis += f"；{evidence_basis}"
    if vehicle_basis:
        basis += f"；{vehicle_basis}"
    if tranche_basis:
        basis += f"；{tranche_basis}"
    if capped:
        basis += "；已按单只集中度上限收紧"
    return resolved_percent, basis, None


def _opportunity_score(sector_opportunity: dict | None) -> float | None:
    if not isinstance(sector_opportunity, dict):
        return None
    for key in ("research_score", "score"):
        raw = sector_opportunity.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if isfinite(value):
            return round(min(max(value, 0.0), 100.0), 2)
    return None


def _add_position_percent_for_score(
    opportunity_score: float | None,
) -> tuple[float, str]:
    score = opportunity_score if opportunity_score is not None else float("-inf")
    for threshold, percent, label in _ADD_POSITION_PERCENT_TIERS:
        if score >= threshold:
            return percent, label
    return 5.0, "小机会试探档"


def _position_change_amount_yuan(
    holding: Holding | None,
    percent: float | None,
) -> float | None:
    """Translate a percentage to an internal tradeability probe, never UI output."""

    if holding is None or percent is None or percent <= 0:
        return None
    amount = float(holding.holding_amount) * percent / 100
    return amount if isfinite(amount) and amount > 0 else None


def _estimated_position_change_amount_yuan(
    holding: Holding | None,
    percent: float | None,
) -> float | None:
    """Return the display-only notional implied by the final guarded ratio."""

    if holding is None or percent is None or percent == 0:
        return None
    holding_amount = float(holding.holding_amount)
    amount = holding_amount * abs(float(percent)) / 100
    if not isfinite(amount) or amount <= 0:
        return None
    return round(amount, 2)


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
                "建议比例已按已核验的单日申购限额收紧；"
                "实际操作前仍需复核渠道剩余额度。"
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
        return (
            normalized_action,
            None,
            True,
            "赎回开放已核验，但缺少逐笔申购时间，无法确认锁定期与适用赎回费；"
            "保留减仓比例用于风险规划，实际赎回前请核对持有期与费用。",
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
    copy.estimated_position_change_amount_yuan = None
    return copy


def _enforce_final_execution_projection(
    rec: FundRecommendation,
    *,
    original_action: str,
    holding: Holding | None,
) -> None:
    """Project every user-visible execution field from the final guarded action."""
    final_direction = _execution_direction(rec.action)
    _ = original_action  # Kept in the signature for historical callers/audit parity.
    # Daily recommendations are percentage-first. Exact yuan/share sizing is
    # intentionally never exposed because imported holdings are market-value
    # estimates and do not need a platform share confirmation to be useful.
    rec.amount_yuan = None
    rec.amount_note = None
    if final_direction == "none":
        rec.suggested_position_change_percent = None
        rec.suggested_position_change_basis = ""

    position = rec.suggested_position_change_percent
    if position is not None:
        valid_sign = (final_direction == "add" and position > 0) or (
            final_direction == "reduce" and position < 0
        )
        if not valid_sign or not isfinite(float(position)):
            rec.suggested_position_change_percent = None
            rec.suggested_position_change_basis = ""
        elif not rec.suggested_position_change_basis:
            rec.suggested_position_change_basis = (
                f"相对当前估算持仓计算；系统依据最终动作「{rec.action}」确定档位"
            )

    rec.estimated_position_change_amount_yuan = (
        _estimated_position_change_amount_yuan(
            holding,
            rec.suggested_position_change_percent,
        )
    )

    rec.risks = rec.risks or _build_default_risks(rec, None)

    rec.points = [point for point in rec.points if not _is_redundant_user_point(point)]
    if not rec.decision_path:
        rec.decision_path = f"确定性守卫完成身份、证据与风险校验；最终动作：{rec.action}。"


def _is_redundant_user_point(text: str | None) -> bool:
    """System copy that the action badge / professional banner already covers."""

    value = str(text or "").strip()
    return value.startswith("系统校验后的最终动作") or value.startswith(
        "赎回开放已核验，但缺少逐笔申购时间"
    )


#: 允许被确定性提议抬到「分批加仓」的起点动作。只有这两档是"没有风险结论、只是没有下文"
#: 的被动状态；风险动作（减仓/清仓/风控复核）与已经是加仓的情况都不在其中。
_PROMOTABLE_BUCKETS = frozenset({ACTION_BUCKET_WATCH, ACTION_BUCKET_PAUSE})


def _promote_to_proposed_add(
    current_action: str,
    *,
    proposal: DailyActionProposal,
    offline_action: str | None,
) -> str | None:
    """确定性提议支持加仓、而当前结论仍停在被动动作时，返回应提升到的动作，否则 None。

    这是整套提议机制**唯一**会让结论变得更积极的地方，因此条件收得很紧：

    * `proposal.supports_add` 为真意味着方向成熟度、机会成立性、量化证据、风险升级下限、
      风险上限、回吐信号、当日要闻九道门禁**全部**通过（见 `propose_daily_action`），
      所以这里不是绕过门禁，而是补上"门都开着却没人推门"的那一步。
    * **绝不覆盖风险动作**。当前动作只要是减仓/大幅减仓/清仓/风控复核，一律不动——
      系统可以比模型更果断地买，但不能比模型或规则更轻率地放松风险结论。
    * **尊重离线规则的风险否决**。离线引擎给出的非默认动作是它真的触发了条件，比加仓更
      保守时不得提升。
    * 提升后仍要过动作词表、仓位比例与交易门禁（调用点在它们之前）。
    """
    if not proposal.supports_add:
        return None
    if _action_bucket(current_action) not in _PROMOTABLE_BUCKETS:
        return None
    if (
        offline_action is not None
        and _offline_action_is_a_risk_veto(offline_action)
        and _action_bucket(normalize_action_text(offline_action)) < ACTION_BUCKET_ADD
    ):
        return None
    return _BUCKET_TO_LABEL[ACTION_BUCKET_ADD]


def _offline_action_is_a_risk_veto(offline_action: str) -> bool:
    """离线规则引擎这次是否真的**触发**了一条风险意见。

    离线构建器的 `action` 初值是「观察」，只有命中集中度超限、深亏定投
    等具体条件时才改写成别的动作。所以「观察」是它的**无意见默认值**，不是一个结论：
    离线给出非默认动作时才参与封顶（风险否决权）；停在「观察」时不参与，把判断交给
    方向层、证据层与风险层这些真正看得见证据的门禁。
    """
    action = normalize_action_text(offline_action)
    return _action_bucket(action) != ACTION_BUCKET_WATCH


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


_DRAWDOWN_GUARD_CONFIDENCE_LEVELS = frozenset({"高", "中"})


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _portfolio_drawdown_cap_reason(
    facts: dict | None,
    risk: RiskAssessment,
    profile: InvestorProfile,
) -> str | None:
    """真实峰谷最大回撤已验证超出容忍线、且当前仍在浮亏时，禁止继续加仓。

    此前只有「成本浮亏线」是硬约束（`risk.py` 的 `PORTFOLIO_COST_BASIS_LOSS`，代码里
    自己注明"不是组合历史峰值到谷值的最大回撤"）。`portfolio_risk_metrics` 早就算出了
    真实峰谷回撤，但没有接进任何封顶逻辑——一个从高点回撤 30% 却仍略有浮盈的组合，
    过去不会触发任何限制。

    两个条件必须同时成立，缺一不可：

    * **样本门槛**：`risk_metrics` 可用且置信为高/中。这与 facts instruction 里
      「风险指标按 confidence.level 表述，低/不足须声明样本有限、不得据此下强结论」
      同一口径，也与 `market_breadth` 要求 `decision_eligible` 的既有约定一致。
    * **不与浮亏线混用同一含义**：峰谷回撤衡量的是"这个组合回吐过多少"，浮亏线衡量
      "现在亏了多少"，两者量纲不同。一个曾经 +20% 回落到 +2% 的组合峰谷回撤有 -15%
      但仍在赚钱，若直接套用同一阈值几乎任何有历史的组合都会触发。因此额外要求当前
      确实处于浮亏——即"回撤能力已验证超限，而且现在正在亏"。完全跌破浮亏线的情形
      本就由 `risk.level=="high"` 封顶，这里补的是中间那段缺口。
    """

    metrics = (facts or {}).get("risk_metrics")
    if not isinstance(metrics, dict) or metrics.get("available") is not True:
        return None
    confidence = metrics.get("confidence")
    level = (
        str(confidence.get("level") or "")
        if isinstance(confidence, dict)
        else ""
    )
    if level not in _DRAWDOWN_GUARD_CONFIDENCE_LEVELS:
        return None
    drawdown = _num(metrics.get("max_drawdown_percent"))
    if drawdown is None:
        return None
    limit = abs(float(profile.max_drawdown_percent))
    if limit <= 0 or drawdown > -limit:
        return None
    if (risk.weighted_return_percent or 0) >= 0:
        return None
    return (
        f"组合真实峰谷回撤 {drawdown:.2f}% 已超过 {limit:.1f}% 容忍线，"
        f"且当前仍处于浮亏（{risk.weighted_return_percent:.2f}%），已限制加仓类动作。"
    )


def _max_allowed_bucket(
    risk: RiskAssessment,
    _holding,
    _request: AnalysisRequest,
    *,
    portfolio_drawdown_capped: bool = False,
) -> int:
    if risk.suggested_action == "risk_review":
        return 2
    if risk.level == "high":
        return 2
    # 峰谷回撤封顶：它衡量的是这个组合实际回吐过多少，与「是否愿意追当日涨幅」无关。
    if portfolio_drawdown_capped:
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


# 方向证据**整层**缺席时的降级原因。区分两种缺席是为了让用户和运维能分辨"这只持仓压根
# 没有板块"与"今天板块证据没取到"——前者要去补板块映射，后者等下一份日报就好。
def _discovery_cross_reference_note(facts: dict | None, holding) -> str | None:
    """当日发现基金对该持仓所属板块（或同族细分板块）推荐了新载体时的披露文案。

    只解释"两侧为什么不矛盾"，不搬动作：发现的推荐面向新资金（买哪只更好的载体），
    本卡片只对已持有的这只负责。命中多只时只点名第一只、带上数量——validation_notes
    是披露渠道，不是第二份推荐列表。

    同名板块与同族口径要说不同的话：同名时两侧共用同一行方向状态；同族（持仓「医疗」
    ← 推荐「CXO」这类细分↔父行业）是**两条分开计算的方向状态**（行情代理不同），
    同一天完全可以一边判退出、一边判可布局——必须把"这不是打脸"讲出来，否则用户
    看到的就是裸矛盾。
    """
    if not isinstance(facts, dict) or holding is None:
        return None
    cross = facts.get("discovery_cross_reference")
    if not isinstance(cross, dict) or not cross.get("available"):
        return None
    label = normalize_sector_label(getattr(holding, "sector_name", None))
    if not label:
        return None
    rows = (cross.get("buy_recommendations_by_sector") or {}).get(label) or []
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return None
    first = rows[0]
    name = str(first.get("fund_name") or "").strip() or str(first.get("fund_code") or "").strip()
    action = str(first.get("action") or "").strip() or "分批买入"
    extra = f" 等 {len(rows)} 只" if len(rows) > 1 else ""
    rec_label = normalize_sector_label(str(first.get("sector_label") or "")) or label
    if rec_label != label:
        return (
            f"发现基金今日报告对同主题的「{rec_label}」口径推荐了新的候选载体"
            f"（{name}{extra}，动作「{action}」）。「{label}」与「{rec_label}」是"
            "同一主题家族里两条分开计算的方向状态（行情代理不同），结论可以不一致："
            f"本卡片只按你持有的「{label}」口径给出加/减/退，发现基金回答的是"
            "细分口径的新资金能不能进。两侧都成立时请按同主题总敞口合并权衡，"
            "避免一边减仓一边开新仓放大同主题暴露。"
        )
    return (
        f"发现基金今日报告对「{label}」方向另推荐了新的候选载体（{name}{extra}，"
        f"动作「{action}」）。两侧共用同一套方向打分：方向仍成立时，本卡片只处理"
        "已持有这只载体（落后则停加，不等于卖掉方向）；发现基金回答的是有没有"
        "更好的新工具，不能理解成一边减仓一边开新仓。"
    )


def _direction_exit_family_note(
    sector_opportunity: dict | None,
    facts_row: dict | None,
) -> str | None:
    """方向退出判定携带同族口径分歧披露时，把它原样带到卡片 validation_notes。

    文案在 `report_sector_opportunity._attach_family_direction_divergence` 生成（同族
    口径当日在全局账本仍可布局，如「医疗」退出但「CXO」ready）。取值优先级与
    `resolve_escalation_floor` 的 `direction_exit` 完全一致——guard 与 facts 不得各看
    一套数据。纯披露：不改动作，也不参与任何档位合并。
    """
    exit_row = (
        sector_opportunity.get("direction_exit")
        if isinstance(sector_opportunity, dict)
        else None
    ) or ((facts_row or {}).get("direction_exit"))
    if not isinstance(exit_row, dict):
        return None
    note = str(exit_row.get("family_divergence_note") or "").strip()
    return note or None


_SECTOR_DIRECTION_ABSENT_NO_SECTOR = "该持仓未识别到所属板块，无法做方向判断"
_SECTOR_DIRECTION_ABSENT_UNAVAILABLE = "本轮板块方向证据未取到"


def _sector_direction_absence_reason(
    sector_opportunity: dict | None,
    holding: Holding | None,
) -> str | None:
    """方向证据整层缺席时返回降级原因，否则 None。

    这里补的是一个真实缺口：`analysis_facts` 的板块方向增强超时后 fallback 是
    `held={}`，于是每个持仓行的 `sector_opportunity` 都是 `None`，而
    `_weak_evidence_reasons` 里那一整块判定（机会不成立 / 置信偏低 / 资金流偏弱）都在
    `if sector_opportunity:` 里面——**整层证据消失反而什么都不拦**，只要基金侧量化证据
    凑合，加仓就能原样通过。

    与 `_entry_state_add_block_reason`「快照缺席不拦」的取舍不冲突，两者不是一回事：
    那里缺的是成熟度**子层**（旧版机会分仍在，方向问题仍能回答）；这里缺的是方向证据
    **整层**，prompt 与 guard 反复要求的"先看板块方向 → 再看基金证据 → 给动作"第一步
    直接无法执行。没有方向就不给加仓，是这套决策顺序的必然推论。

    只拦加仓：`_weak_evidence_reasons` 本身只在 `bucket >= ACTION_BUCKET_ADD` 时被调用，
    减仓与风控复核不受影响（风险动作不该因为少了一层证据就被放松）。
    """
    if isinstance(sector_opportunity, dict) and sector_opportunity:
        return None
    if holding is None or not normalize_sector_label(holding.sector_name):
        return _SECTOR_DIRECTION_ABSENT_NO_SECTOR
    return _SECTOR_DIRECTION_ABSENT_UNAVAILABLE


def _weak_evidence_reasons(
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
    *,
    sector_absence_reason: str | None = None,
) -> list[str]:
    """板块方向侧的弱项照旧拦加仓；**基金侧量化证据弱不再单独拦**。

    `sector_absence_reason` 由调用方通过 `_sector_direction_absence_reason` 计算后传入
    （它需要 `holding` 才能区分缺席原因，而本函数刻意只吃证据、不吃持仓）。

    改动背景（2026-08-12，用户决策）：此前实现把所有弱项收集起来，**任意一条**非空即降级，
    与本函数原 docstring 声称的「至少有一路站得住」直接矛盾。实测后果是加仓在任何一天对
    任何持仓都不可达——`_weak_quantitative_evidence_reason` 判的是
    `evidence.composite.level ∈ {低, 不足}`，而在旧口径下该值等于因子 IC 可靠性，
    是 peer_group 级常量且当前恒为「低」。011036 板块侧
    `entry_state=ready_to_start`、`confidence=高`、趋势 71.29 全部通过，模型也确实拟了
    「分批加仓」，仍被这个常量拦下。

    **不对称是刻意的。** 只解除基金侧的单独否决，板块侧照旧无条件拦：
      * 方向证据整层缺席、`opportunity_available=False`、置信偏低、资金流偏弱、
        `entry_state` 未通过入场线 —— 这些都是逐只逐日真实变化、且是"先看板块方向"这条
        决策顺序的前置条件，放松它们会让系统对一个没通过入场线的方向加仓，
        与荐基侧的既有硬门冲突；
      * 基金侧证据弱则改由 `_fund_evidence_add_percent` 的降一档承担（且证据不可用时
        连降档也不做，见该函数）。

    板块侧命中时，基金侧的弱项仍会一并列出作为补充说明——它解释"为什么不只是降档"。
    """
    sector_reasons: list[str] = []
    if sector_absence_reason:
        sector_reasons.append(sector_absence_reason)
    if sector_opportunity:
        if sector_opportunity.get("opportunity_available") is False:
            sector_reasons.append("持仓板块当前不构成机会")
        confidence = str(sector_opportunity.get("confidence") or "")
        if confidence in {"低", "不足"}:
            sector_reasons.append("板块方向置信偏低")
        pattern = str(sector_opportunity.get("pattern_label") or "")
        if pattern in {"distribution", "weak_outflow"}:
            sector_reasons.append("板块资金流偏弱")
    # 方向成熟度档位。只在当天有主线快照（因此存在 entry_state）时才可能拦，
    # 缺席不拦——那是"没有这层证据"，不是"方向不成立"。
    entry_state_reason = _entry_state_add_block_reason(sector_opportunity)
    if entry_state_reason:
        sector_reasons.append(entry_state_reason)

    if not sector_reasons:
        return []

    fund_reasons: list[str] = []
    weak_quantitative_reason = _weak_quantitative_evidence_reason(evidence, ic_status)
    if weak_quantitative_reason:
        fund_reasons.append(weak_quantitative_reason)
    return _append_unique([], [*sector_reasons, *fund_reasons], limit=4)


def _backfill_decision_fields(
    rec: FundRecommendation,
    holding: Holding | None,
    sector_opportunity: dict | None,
    evidence: dict | None,
    ic_status: dict | None = None,
    vehicle_quality: dict | None = None,
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
            _build_fund_evidence(evidence, ic_status, vehicle_quality),
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
    continuity = _direction_continuity_evidence(sector_opportunity)
    if continuity:
        evidence.append(continuity)
    if sector_opportunity.get("opportunity_available") is False:
        evidence.append("当前不构成加仓机会，仅供方向参考")
    evidence.extend(
        str(item) for item in sector_opportunity.get("evidence") or [] if str(item).strip()
    )
    return evidence


def _vehicle_quality_evidence_text(vehicle_quality: dict | None) -> str | None:
    """把载体质量判断转成「基金依据」栏里的一行人话。

    只在 `applicable=True` 时输出。主动持仓拿到的 `not_applicable` 不进这一栏——
    给每只主动基金都挂一句"载体质量不适用"是噪声，而且容易被读成某种缺陷。
    """
    if not isinstance(vehicle_quality, dict):
        return None
    if vehicle_quality.get("applicable") is not True:
        return None
    status = str(vehicle_quality.get("status") or "")
    if status == "watch_only":
        penalties = [
            str(item).strip()
            for item in vehicle_quality.get("penalties") or []
            if str(item).strip()
        ]
        detail = f"：{'、'.join(penalties[:2])}" if penalties else ""
        return f"被动载体质量未达标{detail}"
    if status == "eligible":
        reasons = [
            str(item).strip()
            for item in vehicle_quality.get("reasons") or []
            if str(item).strip()
        ]
        detail = f"：{'、'.join(reasons[:2])}" if reasons else ""
        return f"被动载体质量合格{detail}"
    return None


def _build_fund_evidence(
    evidence: dict | None,
    ic_status: dict | None = None,
    vehicle_quality: dict | None = None,
) -> list[str]:
    result: list[str] = []
    if evidence:
        result.append(_evidence_composite_summary(evidence, ic_status))
    # 排在分量之前：载体质量是对"这只工具本身"的总体判断，与 composite 同级；放到末尾会
    # 被 `_append_unique(limit=4)` 挤掉，等于对被动持仓白算一遍。
    vehicle_text = _vehicle_quality_evidence_text(vehicle_quality)
    if vehicle_text:
        result.append(vehicle_text)
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
    vehicle_quality: dict | None = None,
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
        # 这里是无条件重建，会盖掉 `_backfill_decision_fields` 的结果，所以载体质量
        # 必须同样传到这一层，否则它只在"既无量化证据也无 IC 状态"的少数路径下才活着。
        rec.fund_evidence = _append_unique(
            [],
            _build_fund_evidence(evidence, ic_status, vehicle_quality),
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
