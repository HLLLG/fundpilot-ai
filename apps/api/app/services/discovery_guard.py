from __future__ import annotations

import re

from app.models import DiscoveryRecommendation, InvestorProfile, NewsItem, TopicBrief
from app.services.decision_guard_shared import (
    append_unique as _append_unique,
    as_float as _as_float,
    fmt_abs_num as _fmt_abs_num,
    fmt_num as _fmt_num,
    humanize_evidence_text as _humanize_evidence_text,
    normalize_confidence_label as _normalize_confidence,
    pattern_label as _pattern_label,
    track_label as _track_label,
)
from app.services.news_citation import _collect_citable_titles, _matches_known_title


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
) -> tuple[list[DiscoveryRecommendation], list[str]]:
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
    allocated_amount = 0.0

    for rec in recommendations:
        code = rec.fund_code.strip().zfill(6)
        if code not in allowed_codes:
            caveats.append(f"已剔除池外基金 {code}（{rec.fund_name}）。")
            continue
        if code in held_codes:
            caveats.append(f"已持有 {code}，不作为新买入推荐。")
            continue

        copy = rec.model_copy(deep=True)
        normalized_action = _normalize_discovery_action(copy.action)
        if normalized_action != copy.action:
            copy.points = [
                f"已将动作「{copy.action}」规范为「{normalized_action}」。",
                *copy.points,
            ]
            copy.action = normalized_action
        copy.confidence = _normalize_confidence(copy.confidence)
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
        if _should_downgrade_weak_evidence(copy, pool_item, opportunity):
            previous = copy.action
            copy.action = "建议关注"
            note = "方向或基金证据不足，系统已将动作从「分批买入」降为「建议关注」。"
            copy.points = [note, *copy.points]
            caveats.append(f"{code} 证据不足，已将动作从「{previous}」降为「建议关注」。")

        max_single = budget_yuan * profile.concentration_limit_percent / 100
        if copy.suggested_amount_yuan is not None and max_single > 0:
            if copy.suggested_amount_yuan > max_single:
                copy.suggested_amount_yuan = round(max_single, 0)
                copy.amount_note = (
                    f"示意金额已压至单只集中度上限约 {profile.concentration_limit_percent:.0f}%"
                )

        if copy.suggested_amount_yuan is not None and budget_yuan > 0:
            remaining = max(float(budget_yuan) - allocated_amount, 0.0)
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
        copy.news_bullish = _filter_news_titles(copy.news_bullish, titles)
        _humanize_recommendation_text(copy)
        guarded.append(copy)

    return guarded[:5], caveats


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
