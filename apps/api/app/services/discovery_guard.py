from __future__ import annotations

import re
from math import isfinite

from app.config import get_settings
from app.models import DiscoveryRecommendation, EliminatedCandidate, InvestorProfile, NewsItem, TopicBrief
from app.services.decision_guard_shared import (
    append_unique as _append_unique,
    as_float as _as_float,
    fmt_abs_num as _fmt_abs_num,
    fmt_num as _fmt_num,
    humanize_evidence_text as _humanize_evidence_text,
    normalize_confidence_label as _normalize_confidence,
    pattern_label as _pattern_label,
    resolve_discovery_escalation,
    track_label as _track_label,
)
from app.services.news_citation import _collect_citable_titles, _matches_known_title


def _known_portfolio_cash_yuan(discovery_facts: dict | None) -> float | None:
    truth = (discovery_facts or {}).get("portfolio_position_truth")
    if not isinstance(truth, dict):
        return None
    cash = truth.get("cash")
    if not isinstance(cash, dict) or cash.get("known") is not True:
        # Unknown cash is not zero.  The explicit request budget remains the only
        # cap until the user confirms a cash baseline.
        return None
    value = _as_float(cash.get("balance_yuan"))
    if value is None or not isfinite(value):
        # A row claiming to be known but lacking a usable value is internally
        # inconsistent; fail closed for executable amounts.
        return 0.0
    return max(value, 0.0)


def apply_discovery_guards(
    recommendations: list[DiscoveryRecommendation],
    *,
    candidate_pool: list[dict],
    held_codes: set[str],
    profile: InvestorProfile,
    budget_yuan: float,
    sector_heat: list[dict],
    discovery_facts: dict | None = None,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    scan_mode: str = "full_market",
) -> tuple[list[DiscoveryRecommendation], list[str], list[EliminatedCandidate]]:
    allowed_codes = {str(item.get("fund_code", "")).zfill(6) for item in candidate_pool}
    pool_by_code = {
        str(item.get("fund_code", "")).zfill(6): item for item in candidate_pool
    }
    heat_by_sector = {
        str(row.get("sector_label", "")): row.get("change_1d_percent")
        for row in sector_heat
    }
    opportunity_by_sector = _sector_opportunities_by_label(discovery_facts or {})
    titles = _collect_citable_titles(market_news or [], topic_briefs or [])
    caveats: list[str] = []
    guarded: list[DiscoveryRecommendation] = []
    eliminated: list[EliminatedCandidate] = []
    allocated_amount = 0.0
    requested_budget_yuan = max(_as_float(budget_yuan) or 0.0, 0.0)
    known_cash_yuan = _known_portfolio_cash_yuan(discovery_facts)
    spendable_budget_yuan = (
        min(requested_budget_yuan, known_cash_yuan)
        if known_cash_yuan is not None
        else requested_budget_yuan
    )
    if known_cash_yuan == 0:
        caveats.append("已确认可用现金为 0，本次仅保留观察候选，不生成可执行买入金额。")
    elif known_cash_yuan is not None and known_cash_yuan < requested_budget_yuan:
        caveats.append(
            f"示意买入总额已按已确认可用现金 {known_cash_yuan:.2f} 元封顶。"
        )
    # M6：与日报 analysis_facts.holdings[].escalation 同一思路——把每只候选"是否触发了
    # M4 双向升级判定"的结构化结果记录下来（无论 shadow/enforced 都记录，且不管最终
    # 是否真的生效），供 shadow_escalation_digest.py 聚合复盘读取，避免正则解析 caveats
    # 文本。写回 discovery_facts（按引用传入，最终会随 FundDiscoveryReport.discovery_facts
    # 一并落库），仅在真正传入了 dict 时才写（None 表示调用方本就没打算存 facts）。
    escalation_hints: dict[str, dict] = {}
    portfolio_snapshot = (discovery_facts or {}).get("portfolio_snapshot")
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
    if degraded_portfolio_snapshot:
        caveats.append("持仓快照未达到权威可执行条件，本次已禁止买入动作与示意金额，仅保留观察候选。")
    evidence_blocked_codes: dict[str, list[str]] = {}

    for rec in recommendations:
        code = rec.fund_code.strip().zfill(6)
        if code not in allowed_codes:
            caveats.append(f"已剔除池外基金 {code}（{rec.fund_name}）。")
            continue
        if code in held_codes:
            caveats.append(f"已持有 {code}，不作为新买入推荐。")
            continue

        copy = rec.model_copy(deep=True)
        evidence_allowed, evidence_reasons = decision_evidence_allows_action(
            discovery_facts,
            scope="discovery",
            fund_code=code,
        )
        execution_blocked = degraded_portfolio_snapshot or not evidence_allowed
        if execution_blocked:
            evidence_blocked_codes[code] = evidence_reasons
        normalized_action = _normalize_discovery_action(copy.action)
        if normalized_action != copy.action:
            copy.points = [
                f"已将动作「{copy.action}」规范为「{normalized_action}」。",
                *copy.points,
            ]
            copy.action = normalized_action
        copy.confidence = _normalize_confidence(copy.confidence)
        if execution_blocked:
            if copy.action == "分批买入":
                copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "持仓快照过期，未生成可执行金额"
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "持仓快照过期或尚未服务端确认；组合缺口、集中度与预算只可作背景，不具备买入执行条件。",
            ]
        pool_item = pool_by_code.get(code, {})
        if pool_item:
            corrected = _align_candidate_identity(copy, pool_item)
            if corrected:
                caveats.append(f"已按候选池校正基金名称/板块：{code}。")
        sector_move = heat_by_sector.get(copy.sector_name)
        chase_threshold = 6.0 if profile.decision_style == "aggressive" else 4.0
        if profile.avoid_chasing and sector_move is not None and sector_move >= chase_threshold:
            if copy.action == "分批买入":
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"板块当日 {sector_move:+.2f}% 偏热，拒绝追高模式下建议等待回调。"
                ]

        if profile.avoid_chasing and copy.action == "分批买入":
            r1y = pool_item.get("return_1y_percent")
            nav_trend = pool_item.get("nav_trend") or {}
            dist_high = nav_trend.get("distance_from_high_percent")
            if r1y is not None and float(r1y) >= 100.0:
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"近1年涨幅 {float(r1y):+.1f}% 偏高，拒绝追高模式下建议等待回调。"
                ]
            elif dist_high is not None and float(dist_high) > -5.0:
                copy.action = "等待回调"
                copy.points = list(copy.points) + [
                    f"净值距区间高点仅 {float(dist_high):+.1f}%，短线追高风险偏高。"
                ]

        if scan_mode == "dip_swing" and copy.action == "分批买入":
            nav_trend = pool_item.get("nav_trend") or {}
            recent_1d = _recent_1d_change_percent(nav_trend, pool_item)
            if recent_1d is not None and recent_1d > 3.0:
                copy.action = "建议关注"
                copy.points = list(copy.points) + [
                    f"近1日净值涨幅 {recent_1d:+.2f}% 偏高，短线抄底模式避免追涨。"
                ]

        opportunity = opportunity_by_sector.get(copy.sector_name)

        # M4 双向 guard：与日报 resolve_escalation_floor 同一套"量价背离显著"入口，
        # 但荐基语义不同——负向共振时整条剔除候选池（而非降级动作文字），正向共振时
        # 允许突破常规预算上限的软约束（而非日报的仓位百分比）。两个方向都要求板块
        # 与基金质量分同时印证，只命中一个维度时交由既有的弱证据降级/常规金额上限处理。
        escalation = resolve_discovery_escalation(
            sector_opportunity=opportunity,
            pool_item=pool_item,
        )
        if escalation.get("action"):
            escalation_hints[code] = escalation
        # M6：灰度开关——shadow 模式下不真正剔除/提额，只标注"若切换 enforced 会怎样"，
        # 供 shadow_escalation_digest.py 聚合复盘（与日报 recommendation_guard.py 的
        # 灰度处理同一套开关、同一种"仅提示不生效"的语义）。
        enforced = get_settings().decision_escalation_mode == "enforced"
        if escalation.get("action") == "exclude" and not execution_blocked:
            basis = str(escalation.get("basis") or "")
            if enforced:
                caveats.append(f"已从候选池剔除 {code}（{copy.fund_name}）：{basis}。")
                eliminated.append(
                    EliminatedCandidate(
                        fund_code=code,
                        fund_name=copy.fund_name,
                        sector_name=copy.sector_name,
                        reasons=list(escalation.get("reasons") or []),
                        basis=basis,
                    )
                )
                continue
            copy.validation_notes = [
                *copy.validation_notes,
                f"【灰度提示，未生效】若启用新版守卫（enforced 模式），"
                f"{code}（{copy.fund_name}）会被系统从候选池剔除：{basis}。",
            ]

        if _should_downgrade_weak_evidence(copy, pool_item, opportunity):
            previous = copy.action
            copy.action = "建议关注"
            note = "方向或基金证据不足，系统已将动作从「分批买入」降为「建议关注」。"
            copy.points = [note, *copy.points]
            caveats.append(f"{code} 证据不足，已将动作从「{previous}」降为「建议关注」。")

        amount_boost_multiplier = 1.0
        if escalation.get("action") == "boost" and not execution_blocked:
            basis = str(escalation.get("basis") or "")
            if enforced:
                amount_boost_multiplier = float(escalation.get("amount_multiplier") or 1.0)
                copy.points = [
                    f"量价背离与基金质量共振积极，系统已允许提高建议买入金额上限（{basis}）。",
                    *copy.points,
                ]
                caveats.append(f"{code} 证据强烈支持该方向，已提高建议金额上限。")
            else:
                copy.validation_notes = [
                    *copy.validation_notes,
                    f"【灰度提示，未生效】若启用新版守卫（enforced 模式），"
                    f"{code} 的建议买入金额上限会被系统提高：{basis}。",
                ]

        max_single = (
            spendable_budget_yuan
            * profile.concentration_limit_percent
            / 100
            * amount_boost_multiplier
        )
        if copy.suggested_amount_yuan is not None and spendable_budget_yuan <= 0:
            copy.suggested_amount_yuan = None
            copy.amount_note = (
                "已确认可执行预算或可用现金为 0，本次未生成买入金额。"
            )
        if copy.suggested_amount_yuan is not None and max_single > 0:
            if copy.suggested_amount_yuan > max_single:
                copy.suggested_amount_yuan = round(max_single, 0)
                copy.amount_note = (
                    f"示意金额已压至单只集中度上限约 {profile.concentration_limit_percent:.0f}%"
                )

        if copy.suggested_amount_yuan is not None and spendable_budget_yuan > 0:
            remaining = max(spendable_budget_yuan - allocated_amount, 0.0)
            if copy.suggested_amount_yuan > remaining:
                adjusted = round(remaining, 0)
                copy.suggested_amount_yuan = adjusted if adjusted >= 100 else None
                copy.amount_note = _join_amount_note(
                    copy.amount_note,
                    f"示意金额已按总预算剩余额度压缩至约 {adjusted:.0f} 元",
                )
                caveats.append(f"{code} 示意金额已按总预算剩余额度压缩。")
            if copy.suggested_amount_yuan is not None:
                allocated_amount += float(copy.suggested_amount_yuan)

        if pool_item:
            _backfill_decision_fields(
                copy,
                pool_item,
                opportunity,
            )
        _sync_decision_path_with_final_action(copy)
        if execution_blocked:
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "字段级证据未达到时点可用条件，未生成可执行金额"
            copy.confidence = "低"
            copy.points = safe_blocked_points(
                copy.points,
                fallback="字段级证据未达到可执行条件，本条仅保留观察候选。",
            )
            copy.decision_path = "证据时点校验未通过，系统阻断买入动作并降为建议关注。"
            copy.sector_evidence = [
                value for value in copy.sector_evidence if not contains_executable_decision_text(value)
            ]
            copy.fund_evidence = [
                value for value in copy.fund_evidence if not contains_executable_decision_text(value)
            ]
            copy.validation_notes = [
                value for value in copy.validation_notes if not contains_executable_decision_text(value)
            ] + ["字段级证据时点校验未通过，买入动作与金额已被确定性阻断。"]
        copy.news_bullish = _filter_news_titles(copy.news_bullish, titles)
        _humanize_recommendation_text(copy)
        guarded.append(copy)

    if discovery_facts is not None:
        discovery_facts["escalation_hints"] = escalation_hints
        discovery_facts["decision_escalation_mode"] = get_settings().decision_escalation_mode
        discovery_facts["data_evidence_guard"] = {
            "execution_blocked": bool(evidence_blocked_codes),
            "blocked_fund_codes": sorted(evidence_blocked_codes),
            "reasons_by_fund": evidence_blocked_codes,
        }
    if evidence_blocked_codes and not degraded_portfolio_snapshot:
        caveats.append("部分候选的字段级证据时点不可用，已降为观察并清除买入金额。")

    return guarded[:5], caveats, eliminated


def _normalize_discovery_action(action: str) -> str:
    text = str(action or "").strip()
    if any(token in text for token in ("回调", "暂停", "追高", "等一等", "观望")):
        return "等待回调"
    if any(token in text for token in ("分批", "买入", "加仓", "少量", "定投", "试探")):
        return "分批买入"
    return "建议关注"


def _should_downgrade_weak_evidence(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> bool:
    if rec.action != "分批买入":
        return False
    weak_reasons = _weak_evidence_reasons(pool_item, opportunity)
    return bool(weak_reasons)


def _weak_evidence_reasons(pool_item: dict, opportunity: dict | None) -> list[str]:
    reasons: list[str] = []
    if opportunity:
        confidence = str(opportunity.get("confidence") or "").strip()
        if confidence in {"低", "不足"}:
            reasons.append("主方向置信低")
        score = _as_float(opportunity.get("score"))
        if score is not None and score < 60:
            reasons.append("板块机会分偏低")
        pattern = str(opportunity.get("pattern_label") or "")
        if pattern in {"flow_date_mismatch", "distribution", "weak_outflow"}:
            reasons.append("资金/价格信号偏弱")
        five_day_flow = _as_float(opportunity.get("cumulative_5d_net_yi"))
        if five_day_flow is not None and five_day_flow < 0:
            reasons.append("5日主力净流出")
    quality = _as_float(pool_item.get("fund_quality_score"))
    if quality is not None and quality < 55:
        reasons.append("基金质量分偏低")
    fit = _as_float(pool_item.get("sector_fit_score"))
    if fit is not None and fit < 18:
        reasons.append("板块匹配分偏低")
    penalties = " ".join(str(item) for item in pool_item.get("quality_penalties") or [])
    if "匹配置信偏低" in penalties or "板块匹配" in penalties:
        reasons.append("板块匹配置信偏低")
    return _append_unique([], reasons, limit=6)


def _sync_decision_path_with_final_action(rec: DiscoveryRecommendation) -> None:
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
    for candidate in ("分批买入", "建议关注", "等待回调", "少量买入"):
        if candidate != final_action and candidate in text:
            return True
    return False


def _strip_conflicting_action_clause(text: str, final_action: str) -> str:
    result = text
    action_terms = ("分批买入", "建议关注", "等待回调", "少量买入")
    for candidate in action_terms:
        if candidate == final_action:
            continue
        result = re.sub(rf"，?最后决定[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
        result = re.sub(rf"，?动作[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
    return result


def _join_amount_note(existing: str | None, addition: str) -> str:
    if existing:
        return f"{existing}；{addition}"
    return addition


def _sector_opportunities_by_label(facts: dict) -> dict[str, dict]:
    items = facts.get("sector_opportunities") or []
    result: dict[str, dict] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "").strip()
        if label:
            result[label] = item
    return result


def _align_candidate_identity(rec: DiscoveryRecommendation, pool_item: dict) -> bool:
    expected_name = str(pool_item.get("fund_name") or "").strip()
    expected_sector = str(
        pool_item.get("sector_label") or pool_item.get("sector_name") or ""
    ).strip()
    corrected = False
    if expected_name and rec.fund_name != expected_name:
        rec.fund_name = expected_name
        corrected = True
    if expected_sector and rec.sector_name != expected_sector:
        rec.sector_name = expected_sector
        corrected = True
    return corrected


def _backfill_decision_fields(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> None:
    if not rec.decision_path:
        rec.decision_path = _build_decision_path(rec, pool_item, opportunity)
    if not rec.sector_evidence:
        rec.sector_evidence = _append_unique([], _build_sector_evidence(opportunity), limit=4)
    if not rec.fund_evidence:
        rec.fund_evidence = _append_unique([], _build_fund_evidence(pool_item), limit=4)
    if not rec.validation_notes:
        rec.validation_notes = _append_unique(
            [],
            _build_validation_notes(pool_item, opportunity),
            limit=4,
        )


def _humanize_recommendation_text(rec: DiscoveryRecommendation) -> None:
    rec.decision_path = _humanize_evidence_text(rec.decision_path)
    rec.amount_note = _humanize_evidence_text(rec.amount_note) if rec.amount_note else rec.amount_note
    rec.sector_evidence = [_humanize_evidence_text(item) for item in rec.sector_evidence]
    rec.fund_evidence = [_humanize_evidence_text(item) for item in rec.fund_evidence]
    rec.validation_notes = [_humanize_evidence_text(item) for item in rec.validation_notes]
    rec.points = [_humanize_evidence_text(item) for item in rec.points]
    rec.risks = [_humanize_evidence_text(item) for item in rec.risks]


def _build_decision_path(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> str:
    sector = rec.sector_name or str(pool_item.get("sector_label") or "")
    quality = pool_item.get("fund_quality_score")
    fit = pool_item.get("sector_fit_score")
    if opportunity:
        track = opportunity.get("track") or "unknown"
        score = opportunity.get("score")
        if quality is not None and fit is not None:
            return (
                f"先判断板块方向：{sector}（{_track_label(track)}，机会分 {_fmt_num(score)}），"
                f"再在该方向内选择基金质量分 {_fmt_num(quality)}、"
                f"板块匹配分 {_fmt_num(fit)} 的候选基金，动作定为{rec.action}。"
            )
        return (
            f"先判断板块方向：{sector}（{_track_label(track)}，机会分 {_fmt_num(score)}），"
            f"再从候选池内选择匹配基金，动作定为{rec.action}。"
        )
    if quality is not None and fit is not None:
        return (
            f"先判断板块方向：{sector}，再选择基金质量分 {_fmt_num(quality)}、"
            f"板块匹配分 {_fmt_num(fit)} 的候选基金，动作定为{rec.action}。"
        )
    return f"先判断板块方向：{sector}，再从候选池内选择匹配基金，动作定为{rec.action}。"


def _build_sector_evidence(opportunity: dict | None) -> list[str]:
    if not opportunity:
        return []
    evidence: list[str] = []
    score = opportunity.get("score")
    track = opportunity.get("track")
    confidence = opportunity.get("confidence")
    if score is not None:
        text = f"机会分 {_fmt_num(score)}"
        if track:
            text += f"，{_track_label(track)}"
        if confidence:
            text += f"，置信度{confidence}"
        evidence.append(text)
    today_flow = opportunity.get("today_main_force_net_yi")
    five_day_flow = opportunity.get("cumulative_5d_net_yi")
    if today_flow is not None or five_day_flow is not None:
        parts = []
        if today_flow is not None:
            parts.append(f"今日主力净流入 {_fmt_num(today_flow)} 亿")
        if five_day_flow is not None:
            parts.append(f"5日主力净流入 {_fmt_num(five_day_flow)} 亿")
        evidence.append("，".join(parts))
    pattern = opportunity.get("pattern_label")
    if pattern:
        evidence.append(f"资金/价格信号：{_pattern_label(str(pattern))}")
    evidence.extend(str(item) for item in opportunity.get("evidence") or [] if str(item).strip())
    return evidence


def _build_fund_evidence(pool_item: dict) -> list[str]:
    evidence: list[str] = []
    quality = pool_item.get("fund_quality_score")
    fit = pool_item.get("sector_fit_score")
    if quality is not None or fit is not None:
        parts = []
        if quality is not None:
            parts.append(f"基金质量分 {_fmt_num(quality)}")
        if fit is not None:
            parts.append(f"板块匹配分 {_fmt_num(fit)}")
        evidence.append("，".join(parts))
    reasons = pool_item.get("quality_reasons") or []
    if reasons:
        evidence.append("质量理由：" + "；".join(str(item) for item in reasons[:3]))
    returns = []
    for key, label in (
        ("return_3m_percent", "近3月"),
        ("return_6m_percent", "近6月"),
        ("return_1y_percent", "近1年"),
    ):
        value = pool_item.get(key)
        if value is not None:
            returns.append(f"{label}{_fmt_num(value)}%")
    if returns:
        evidence.append("阶段收益：" + "，".join(returns))
    return evidence


def _build_validation_notes(pool_item: dict, opportunity: dict | None) -> list[str]:
    notes = [
        str(item)
        for item in pool_item.get("quality_penalties") or []
        if str(item).strip()
    ]
    if opportunity:
        notes.extend(
            str(item)
            for item in opportunity.get("penalties") or []
            if str(item).strip()
        )
    if pool_item.get("fund_quality_score") is None:
        notes.append("候选池缺少基金质量分，置信度需保守")
    return notes


def _recent_1d_change_percent(nav_trend: dict, pool_item: dict) -> float | None:
    daily = nav_trend.get("recent_5d_daily_change_percent")
    if isinstance(daily, list) and daily:
        try:
            return float(daily[-1])
        except (TypeError, ValueError):
            pass
    dip = pool_item.get("dip_drop_percent")
    if dip is not None:
        return None
    return None


def _filter_news_titles(headlines: list[str], known_titles: list[str]) -> list[str]:
    cleaned: list[str] = []
    for headline in headlines:
        text = headline.strip()
        if not text:
            continue
        if known_titles and not _matches_known_title(text, known_titles):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:3]
