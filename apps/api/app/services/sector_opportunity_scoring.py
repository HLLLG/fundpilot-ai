from __future__ import annotations

"""通用板块方向机会打分（双轨：顺势 momentum / 蓄势 setup）。

原实现位于 discovery_sector_opportunity.py（荐基专用），2026-07 抽取为共享模块，
供日报（report_sector_opportunity.py）与荐基共用同一套打分口径，避免两条链路对
「同一板块当前是什么方向」给出不一致的结论。discovery_sector_opportunity.py 保留
为薄封装以维持向后兼容。

``sector_entry_maturity.2026-07.v2`` 在旧机会分之上增加三个彼此独立的判断：

* 方向潜力：20～60 个交易日的相对强度和趋势持续性；
* 形态成熟度：5/20 日资金、上涨广度和趋势是否共同改善；
* 入场成熟度：价格位置是否允许现在开始首批布局。

V2 只在完整 ``mainline_regime.v1`` 快照存在时启用。旧报告、日报单板块描述和
测试适配器仍保留旧字段语义，避免历史报告被新规则重新解释。
"""

from concurrent.futures import ThreadPoolExecutor, wait
from math import isfinite
from typing import Any

MOMENTUM_TRACK = "momentum"
SETUP_TRACK = "setup"

ENTRY_POLICY_VERSION = "sector_entry_maturity.2026-07.v2"
ENTRY_READY_TO_START = "ready_to_start"
ENTRY_READY_ON_PULLBACK = "ready_on_pullback"
ENTRY_FORMING = "forming"
ENTRY_INVALID = "invalid"

_ENTRY_STATE_PRIORITY = {
    ENTRY_READY_TO_START: 4,
    ENTRY_READY_ON_PULLBACK: 3,
    ENTRY_FORMING: 2,
    ENTRY_INVALID: 1,
}
_EVIDENCE_QUALITY_PRIORITY = {"complete": 2, "partial": 1, "insufficient": 0}

_DISTRIBUTION_PATTERNS = {"distribution", "weak_outflow"}
_SETUP_PATTERNS = {"accumulation", "multi_day_outflow_then_inflow", "flow_turning_positive"}
_MOMENTUM_PATTERNS = {"price_flow_aligned_up", "aligned_up"}

_SECTOR_GROUPS = {
    "半导体": "tmt",
    "半导体材料": "tmt",
    "存储芯片": "tmt",
    "CPO": "tmt",
    "人工智能": "tmt",
    "机器人": "tmt",
    "港股": "hongkong",
    "港股通": "hongkong",
    "恒生科技": "hongkong",
    "创新药": "healthcare",
    "港股医药": "healthcare",
    "医药": "healthcare",
    "医疗器械": "healthcare",
    "白酒": "consumer",
    "消费电子": "consumer",
    "银行": "finance",
    "证券": "finance",
    "有色金属": "cyclical",
    "新能源车": "manufacturing",
    "光伏": "manufacturing",
    "电网设备": "manufacturing",
}

# 数据源里会同时出现“市场别名”和“投资方向名”。两条记录可能复用同一指数
# 价格序列，只是资金广度口径略有区别；把它们同时推荐给 C 端用户会造成一种
# 方向占掉两个名额，也会放大组合暴露。这里只合并确定等价的宽基方向，不合并
# 半导体/半导体材料等确有不同成分的细分行业。
_EQUIVALENT_DIRECTION_LABELS = {
    "港股通": "港股",
}


def select_sector_opportunities(
    sector_heat: list[dict],
    *,
    sector_flow_by_label: dict[str, dict] | None = None,
    sector_divergence_by_label: dict[str, dict] | None = None,
    mainline_by_label: dict[str, dict] | None = None,
    focus_sectors: list[str] | None = None,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
) -> list[dict[str, Any]]:
    flow_by_label = sector_flow_by_label or {}
    divergence_by_label = sector_divergence_by_label or {}
    mainline_map = mainline_by_label or {}
    entry_policy_enabled = any(
        _supports_entry_maturity_v2(item) for item in mainline_map.values()
    )
    focus = {str(label).strip() for label in (focus_sectors or []) if str(label).strip()}
    scored = [
        _score_row(
            row,
            flow_by_label.get(str(row.get("sector_label") or "").strip()),
            focus,
            divergence_backtest=divergence_by_label.get(str(row.get("sector_label") or "").strip()),
            mainline=mainline_map.get(str(row.get("sector_label") or "").strip()),
            entry_policy_enabled=entry_policy_enabled,
        )
        for row in sector_heat
    ]
    rows = [row for row in scored if row is not None]

    if entry_policy_enabled:
        # 入场状态优先于分数：证据完整且可布局的方向必须排在热门但不可执行的
        # 方向之前；缺少 mainline 证据的方向不能再因为跳过混合评分而占便宜。
        ordered = sorted(rows, key=_entry_sort_score, reverse=True)
        return _take_with_group_limit(ordered, max_total, [], max_per_group)[:max_total]

    momentum = sorted(
        [row for row in rows if row["track"] == MOMENTUM_TRACK],
        key=_research_sort_score,
        reverse=True,
    )
    setup = sorted(
        [row for row in rows if row["track"] == SETUP_TRACK],
        key=_research_sort_score,
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    selected.extend(_take_with_group_limit(momentum, momentum_slots, selected, max_per_group))
    selected.extend(_take_with_group_limit(setup, setup_slots, selected, max_per_group))

    remaining = max_total - len(selected)
    if remaining > 0:
        selected_labels = {item["sector_label"] for item in selected}
        fallback = sorted(
            [row for row in rows if row["sector_label"] not in selected_labels],
            key=_research_sort_score,
            reverse=True,
        )
        selected.extend(_take_with_group_limit(fallback, remaining, selected, max_per_group))
    return selected[:max_total]


def build_sector_flow_map_for_opportunities(
    sector_heat: list[dict],
    sector_labels: list[str],
    *,
    trade_date: str | None = None,
    total_timeout_seconds: float = 6.0,
    max_workers: int = 5,
) -> dict[str, dict]:
    from app.services.sector_fund_flow_context import (
        build_sector_fund_flow_context,
        get_matching_theme_board_flow_snapshot,
    )

    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    labels = _unique_labels(sector_labels)
    if not labels:
        return {}

    snapshot_trade_date = trade_date
    if not snapshot_trade_date:
        from app.services.trading_session import get_effective_trade_date

        snapshot_trade_date = get_effective_trade_date()
    # Freeze one same-day theme snapshot for the whole opportunity pass. This
    # prevents sector workers from observing different refreshes and lets the
    # report reuse the exact flow facts that were used for opportunity scoring.
    shared_theme_snapshot = get_matching_theme_board_flow_snapshot(snapshot_trade_date)

    def load(label: str) -> tuple[str, dict | None]:
        heat = heat_by_label.get(label) or {}
        change_1d = _num(heat.get("change_1d_percent"))
        try:
            flow = build_sector_fund_flow_context(
                label,
                sector_return_percent=change_1d,
                trade_date=trade_date,
                theme_snapshot=shared_theme_snapshot,
            )
        except Exception:  # noqa: BLE001 - opportunity flow is best-effort
            return label, None
        return label, flow or None

    result: dict[str, dict] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="sector-opportunity-flow",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        done, pending = wait(futures, timeout=max(0.0, total_timeout_seconds))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                label, flow = future.result()
            except Exception:
                continue
            if flow:
                result[label] = flow
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def build_sector_divergence_map_for_opportunities(
    sector_labels: list[str],
    *,
    total_timeout_seconds: float = 6.0,
    max_workers: int = 4,
) -> dict[str, dict]:
    """并发跑量价背离回测（M1.3），供 `_confidence` 升级判定使用。

    比 `build_sector_flow_map_for_opportunities` 更重（涉及 K 线 + 完整资金流历史序列 +
    T→T+1 循环，而非单次资金流上下文查询），因此默认更低的 `max_workers`；结果本身有
    24h 缓存（见 `sector_flow_divergence_backtest.build_sector_flow_divergence_backtest`），
    该函数只是把「按需并发调用 + 总预算超时」这层封装起来，任一板块超时/失败都不影响其他
    板块，也不阻塞板块机会打分主流程（best-effort）。
    """
    from app.services.sector_flow_divergence_backtest import (
        build_sector_flow_divergence_backtest,
    )

    labels = _unique_labels(sector_labels)
    if not labels:
        return {}

    def load(label: str) -> tuple[str, dict | None]:
        try:
            result = build_sector_flow_divergence_backtest(label)
        except Exception:  # noqa: BLE001 - divergence backtest is best-effort
            return label, None
        return label, result if result and result.get("by_rule") else None

    result: dict[str, dict] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="sector-opportunity-divergence",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        done, pending = wait(futures, timeout=max(0.0, total_timeout_seconds))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                label, divergence = future.result()
            except Exception:
                continue
            if divergence:
                result[label] = divergence
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def describe_sector_opportunity(
    row: dict,
    flow: dict | None,
    *,
    focus: set[str] | None = None,
    divergence_backtest: dict | None = None,
) -> dict[str, Any] | None:
    """给单个板块的方向判断，即使该板块暂不构成「机会」也会返回结果。

    与 `select_sector_opportunities` 不同，这里不做 slot/max_per_group 限制、也不会因为
    分数不够或资金背离而整行丢弃——供「本来就持有该板块」的场景使用（日报），需要对已持有
    的方向给出判断，而不是只挑「值得关注的新方向」（荐基）。返回的 `opportunity_available`
    标注该板块当前是否构成一个值得加仓的机会；为 False 时仅作方向参考，不应作为加仓依据。

    `divergence_backtest`（M1.4 新增）：该板块的量价背离历史回测结果（见
    `sector_flow_divergence_backtest.build_sector_flow_divergence_backtest`），传入时若
    证据极强（distribution 规则 significant=True 且 edge_percent>=10）confidence 可升至
    「高」；不传入时行为与此前完全一致（confidence 上限仍为「中」）。
    """
    return _compute_opportunity_row(row, flow, focus or set(), divergence_backtest)


def _score_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    *,
    divergence_backtest: dict | None = None,
    mainline: dict | None = None,
    entry_policy_enabled: bool = False,
) -> dict[str, Any] | None:
    result = _compute_opportunity_row(
        row,
        flow,
        focus,
        divergence_backtest,
        mainline=mainline,
        entry_policy_enabled=entry_policy_enabled,
    )
    if result is None or not result["opportunity_available"]:
        return None
    return {key: value for key, value in result.items() if key != "opportunity_available"}


def _compute_opportunity_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    divergence_backtest: dict | None = None,
    *,
    mainline: dict | None = None,
    entry_policy_enabled: bool = False,
) -> dict[str, Any] | None:
    label = str(row.get("sector_label") or "").strip()
    if not label:
        return None
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    heat_score = _num(row.get("heat_score")) or 0.0
    flow = flow or {}
    pattern = str(flow.get("pattern_label") or "").strip()
    date_aligned = flow.get("date_aligned") is not False
    # 资金流日期与涨跌幅日期不对齐（或资金流本身不可用）时，today_main_force_net_yi /
    # cumulative_5d_net_yi 实际上不代表「今日」资金流，不能再被当作当日证据参与打分
    # 或写进 evidence/返回字段——否则会出现下游文案一边写"资金日期需核验"、一边又
    # 言之凿凿地给出"今日主力净流入 XX 亿"这种自相矛盾的展示（真实回归案例：
    # 2026-07-03 日报把好几天前的旧资金流数字当成当日数据喂给用户/LLM）。
    flow_available = bool(flow.get("available")) and date_aligned
    raw_today_flow = _num(flow.get("today_main_force_net_yi"))
    raw_flow_5d = _num(flow.get("cumulative_5d_net_yi"))
    today_declared_available = (
        bool(flow.get("today_available"))
        if "today_available" in flow
        else raw_today_flow is not None
    )
    five_day_declared_available = (
        bool(flow.get("five_day_available"))
        if "five_day_available" in flow
        else raw_flow_5d is not None
    )
    today_available = flow_available and today_declared_available and raw_today_flow is not None
    five_day_available = (
        flow_available and five_day_declared_available and raw_flow_5d is not None
    )
    today_flow = raw_today_flow if today_available else None
    flow_5d = raw_flow_5d if five_day_available else None
    history_point_count = flow.get("history_point_count")
    five_day_source = str(flow.get("five_day_source") or "").strip() or None

    penalties: list[str] = []
    evidence: list[str] = []
    if pattern in _DISTRIBUTION_PATTERNS:
        penalties.append("资金背离或持续流出")
    if flow and not date_aligned:
        penalties.append("资金流日期未对齐")
    if change_1d is not None and change_1d >= 4.0:
        penalties.append("单日涨幅过热")

    focus_bonus = 6.0 if label in focus else 0.0
    flow_bonus = _positive_score(today_flow, scale=2.0, cap=12.0) + _positive_score(
        flow_5d,
        scale=1.0,
        cap=12.0,
    )
    if today_flow is not None and today_flow > 0:
        evidence.append("今日主力净流入")
    if flow_5d is not None and flow_5d > 0:
        evidence.append("5日主力净流入")

    momentum_score = (
        max(change_1d or 0.0, 0.0) * 5.0
        + max(change_5d or 0.0, 0.0) * 4.0
        + flow_bonus
        + heat_score * 0.15
        + focus_bonus
    )
    if pattern in _MOMENTUM_PATTERNS:
        momentum_score += 10.0
        evidence.append("价涨资金配合")
    if change_1d is not None and change_1d >= 4.0:
        momentum_score -= 12.0
    if pattern in _DISTRIBUTION_PATTERNS:
        momentum_score -= 30.0

    setup_score = (
        _setup_price_score(change_1d, change_5d)
        + flow_bonus * 1.15
        + heat_score * 0.08
        + focus_bonus
    )
    if pattern in _SETUP_PATTERNS:
        setup_score += 14.0
        evidence.append("资金拐点或吸筹形态")
    if pattern in _DISTRIBUTION_PATTERNS:
        setup_score -= 28.0

    disqualified = (
        pattern in _DISTRIBUTION_PATTERNS and (today_flow or 0.0) <= 0 and (flow_5d or 0.0) <= 0
    ) or max(momentum_score, setup_score) <= 0

    track = MOMENTUM_TRACK if momentum_score >= setup_score else SETUP_TRACK
    legacy_score = round(max(momentum_score, setup_score), 2)
    mainline_score = _num((mainline or {}).get("score"))
    mainline_status = str((mainline or {}).get("status") or "").strip()
    research_score = legacy_score
    if mainline_score is not None and mainline_status != "insufficient":
        research_score = round(
            min(max(legacy_score, 0.0), 100.0) * 0.55
            + min(max(mainline_score, 0.0), 100.0) * 0.45,
            2,
        )
    result = {
        "sector_label": label,
        "track": track,
        "score": legacy_score,
        "research_score": research_score,
        "mainline_regime": dict(mainline) if isinstance(mainline, dict) else None,
        "confidence": (
            "不足"
            if disqualified
            else _confidence(flow, date_aligned, penalties, divergence_backtest)
        ),
        "entry_hint": _entry_hint(track, change_1d, change_5d, penalties),
        "evidence": _unique_evidence(evidence)[:5],
        "penalties": penalties[:5],
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "today_available": today_available,
        "five_day_available": five_day_available,
        "five_day_source": five_day_source,
        "history_point_count": history_point_count,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": flow_5d,
        "pattern_label": pattern or None,
        "sector_group": _sector_group(label),
        "opportunity_available": not disqualified,
    }
    if entry_policy_enabled:
        maturity = _entry_maturity_v2(
            label=label,
            track=track,
            legacy_score=legacy_score,
            change_1d=change_1d,
            change_5d=change_5d,
            today_flow=today_flow,
            flow_5d=flow_5d,
            pattern=pattern,
            date_aligned=date_aligned,
            mainline=mainline,
        )
        result.update(maturity)
        result["mainline_regime"] = dict(mainline) if isinstance(mainline, dict) else None
        result["opportunity_available"] = maturity["entry_state"] != ENTRY_INVALID
        result["confidence"] = maturity["confidence"]
        result["entry_hint"] = maturity["entry_hint"]
        result["evidence"] = _unique_evidence(
            [*maturity["evidence"], *evidence]
        )[:6]
        result["penalties"] = _unique_evidence(
            [*maturity["penalties"], *penalties]
        )[:6]
    return result


def _supports_entry_maturity_v2(mainline: object) -> bool:
    if not isinstance(mainline, dict):
        return False
    return bool(
        str(mainline.get("schema_version") or "").startswith("mainline_regime.")
        or mainline.get("feature_coverage") is not None
        or isinstance(mainline.get("component_scores"), dict)
    )


def _entry_maturity_v2(
    *,
    label: str,
    track: str,
    legacy_score: float,
    change_1d: float | None,
    change_5d: float | None,
    today_flow: float | None,
    flow_5d: float | None,
    pattern: str,
    date_aligned: bool,
    mainline: dict | None,
) -> dict[str, Any]:
    """Turn research ranking evidence into one explicit entry state.

    The score is deliberately bounded and missing evidence is penalised.  This
    is the opposite of the old available-weight re-normalisation behaviour,
    where a direction with only one strong component could display a very high
    score and avoid the mainline blend entirely.
    """

    mainline_map = mainline if isinstance(mainline, dict) else {}
    components = (
        mainline_map.get("component_scores")
        if isinstance(mainline_map.get("component_scores"), dict)
        else {}
    )
    features = (
        mainline_map.get("features")
        if isinstance(mainline_map.get("features"), dict)
        else {}
    )
    status = str(mainline_map.get("status") or "insufficient").strip() or "insufficient"
    coverage = _clamp(_num(mainline_map.get("feature_coverage")) or 0.0, 0.0, 1.0)
    flow_20d = _num(features.get("cumulative_20d_net_yi"))
    distance_high = _num(features.get("distance_from_20d_high_percent"))
    distance_ma20 = _num(features.get("distance_from_ma20_percent"))
    return_5d = _num(features.get("return_5d_percent"))
    if return_5d is None:
        return_5d = change_5d
    position_label = str(features.get("position_label") or "").strip()

    evidence_quality = (
        "complete"
        if status != "insufficient" and coverage >= 0.80 and date_aligned
        else "partial"
        if status != "insufficient" and coverage >= 0.65 and date_aligned
        else "insufficient"
    )

    relative_score = _num(components.get("relative_strength"))
    trend_score = _num(components.get("trend_persistence"))
    structure_component = _num(components.get("market_structure"))
    flow_component = _num(components.get("fund_flow"))
    breadth_score = _num(components.get("breadth"))

    direction_score = _weighted_available_score(
        (
            (relative_score, 0.45),
            (trend_score, 0.40),
            (structure_component, 0.15),
        )
    )
    if direction_score is None or evidence_quality == "insufficient":
        # The fallback intentionally ignores most of the one-day jump.  It can
        # retain a research lead but can never make an entry-ready direction.
        c5 = _clamp(change_5d or 0.0, -8.0, 8.0)
        overheat = max((change_1d or 0.0) - 3.0, 0.0)
        direction_score = _clamp(35.0 + c5 * 1.5 - overheat * 4.0, 0.0, 45.0)
    else:
        direction_score += {
            "confirmed": 6.0,
            "forming": 2.0,
            "crowded": -8.0,
            "fading": -20.0,
            "neutral": -5.0,
        }.get(status, 0.0)
        direction_score = _clamp(direction_score, 0.0, 100.0)

    setup_score = _weighted_available_score(
        (
            (flow_component, 0.50),
            (breadth_score, 0.25),
            (trend_score, 0.15),
            (structure_component, 0.10),
        )
    )
    if setup_score is None:
        setup_score = 35.0
        if today_flow is not None and today_flow > 0:
            setup_score += 5.0
        if flow_5d is not None and flow_5d > 0:
            setup_score += 10.0
    if pattern in _SETUP_PATTERNS:
        setup_score += 10.0
    elif pattern in _MOMENTUM_PATTERNS and (flow_5d is None or flow_5d >= 0):
        setup_score += 6.0
    if flow_5d is not None and flow_5d < 0:
        setup_score -= 18.0
    if flow_20d is not None and flow_20d < 0:
        setup_score -= 12.0
    if pattern in _DISTRIBUTION_PATTERNS:
        setup_score -= 30.0
    if evidence_quality == "insufficient":
        setup_score = min(setup_score, 52.0)
    setup_score = _clamp(setup_score, 0.0, 100.0)

    structure_score = _entry_structure_score(
        base_score=structure_component,
        position_label=position_label,
        change_1d=change_1d,
        return_5d=return_5d,
        distance_high=distance_high,
        distance_ma20=distance_ma20,
    )
    entry_score = _clamp(
        direction_score * 0.35 + setup_score * 0.40 + structure_score * 0.25,
        0.0,
        100.0,
    )
    if evidence_quality == "insufficient":
        entry_score = min(entry_score, 49.0)

    flow_confirmed = bool(
        (flow_5d is not None and flow_5d > 0)
        or (
            pattern in _SETUP_PATTERNS
            and today_flow is not None
            and today_flow > 0
            and (flow_20d is None or flow_20d >= 0)
        )
    )
    flow_broadly_weak = bool(
        pattern in _DISTRIBUTION_PATTERNS
        or (flow_5d is not None and flow_5d < 0 and flow_20d is not None and flow_20d < 0)
    )
    overheated = bool(
        (change_1d is not None and change_1d >= 4.0)
        or (return_5d is not None and return_5d >= 12.0)
        or status == "crowded"
        or (
            position_label == "high_extended"
            and (
                (return_5d is not None and return_5d >= 6.0)
                or (change_1d is not None and change_1d >= 3.0)
            )
        )
    )
    hard_invalid = bool(
        flow_broadly_weak
        or status == "fading"
        or position_label == "weak_breakdown"
    )

    if hard_invalid:
        entry_state = ENTRY_INVALID
    elif (
        evidence_quality in {"complete", "partial"}
        and status in {"forming", "confirmed"}
        and direction_score >= 55.0
        and setup_score >= 55.0
        and entry_score >= 60.0
        and structure_score >= 50.0
        and flow_confirmed
        and not overheated
    ):
        entry_state = ENTRY_READY_TO_START
    elif (
        evidence_quality in {"complete", "partial"}
        and direction_score >= 55.0
        and setup_score >= 45.0
        and not flow_broadly_weak
        and overheated
    ):
        entry_state = ENTRY_READY_ON_PULLBACK
    else:
        entry_state = ENTRY_FORMING

    opportunity_score = _clamp(
        direction_score * 0.45 + setup_score * 0.35 + entry_score * 0.20,
        0.0,
        100.0,
    )
    research_score = _clamp(
        opportunity_score
        + {ENTRY_READY_TO_START: 8.0, ENTRY_READY_ON_PULLBACK: 3.0}.get(entry_state, 0.0),
        0.0,
        100.0,
    )
    confidence = (
        "高"
        if entry_state == ENTRY_READY_TO_START
        and evidence_quality == "complete"
        and coverage >= 0.85
        and status == "confirmed"
        and flow_5d is not None
        and flow_5d > 0
        and flow_20d is not None
        and flow_20d > 0
        else "中"
        if evidence_quality in {"complete", "partial"}
        else "低"
    )
    entry_hint = {
        ENTRY_READY_TO_START: "条件成熟，可小额首批布局",
        ENTRY_READY_ON_PULLBACK: "方向较强，等待过热缓解",
        ENTRY_FORMING: "条件形成中，暂不下单",
        ENTRY_INVALID: "趋势或资金未通过，暂不参与",
    }[entry_state]
    entry_reason = {
        ENTRY_READY_TO_START: "中期方向、资金确认和价格位置已同时通过入场线。",
        ENTRY_READY_ON_PULLBACK: "中期方向仍有优势，但当前价格位置偏热，不适合立即追入。",
        ENTRY_FORMING: "方向或资金已有苗头，但多周期证据尚未同时成熟。",
        ENTRY_INVALID: "资金持续转弱、趋势退潮或价格结构破坏，当前不具备布局条件。",
    }[entry_state]

    triggers = _entry_triggers(
        entry_state=entry_state,
        status=status,
        evidence_quality=evidence_quality,
        change_1d=change_1d,
        flow_5d=flow_5d,
        direction_score=direction_score,
        distance_high=distance_high,
    )
    invalidation_signals = _invalidation_signals(
        entry_state=entry_state,
        flow_5d=flow_5d,
        distance_ma20=distance_ma20,
    )
    evidence = [
        f"方向潜力 {direction_score:.1f} 分",
        f"形态成熟度 {setup_score:.1f} 分",
        f"入场成熟度 {entry_score:.1f} 分",
    ]
    penalties: list[str] = []
    if evidence_quality == "insufficient":
        penalties.append("20日价格结构或多维证据不足")
    if flow_5d is not None and flow_5d < 0:
        penalties.append("近5日主力资金净流出")
    if flow_20d is not None and flow_20d < 0:
        penalties.append("近20日主力资金净流出")
    if overheated:
        penalties.append("当前价格位置偏热")

    return {
        "score_policy_version": ENTRY_POLICY_VERSION,
        "legacy_score": legacy_score,
        "score": round(opportunity_score, 2),
        "research_score": round(research_score, 2),
        "direction_score": round(direction_score, 2),
        "setup_maturity_score": round(setup_score, 2),
        "entry_readiness_score": round(entry_score, 2),
        "data_coverage": round(coverage, 2),
        "evidence_quality": evidence_quality,
        "entry_state": entry_state,
        "entry_reason": entry_reason,
        "entry_triggers": triggers,
        "invalidation_signals": invalidation_signals,
        "execution_eligible": entry_state == ENTRY_READY_TO_START,
        "automatic_promotion_allowed": entry_state == ENTRY_READY_TO_START,
        "confidence": confidence,
        "entry_hint": entry_hint,
        "evidence": evidence,
        "penalties": penalties,
        "sector_label": label,
        "track": track,
    }


def _entry_structure_score(
    *,
    base_score: float | None,
    position_label: str,
    change_1d: float | None,
    return_5d: float | None,
    distance_high: float | None,
    distance_ma20: float | None,
) -> float:
    score = base_score if base_score is not None else 45.0
    score += {
        "pullback_acceptance": 12.0,
        "base_building": 10.0,
        "early_breakout": 8.0,
        # 距离 20 日高点不足 2% 只代表“接近高位”，并不等于价格已经过热。
        # 是否需要等待回调应由短期涨速、拥挤度共同决定，否则稳定沿趋势
        # 运行的方向会被永久挡在入场线外。
        "high_extended": 0.0,
        "weak_breakdown": -35.0,
    }.get(position_label, 0.0)
    if change_1d is not None:
        if change_1d >= 7.0:
            score -= 35.0
        elif change_1d >= 4.0:
            score -= 20.0
        elif change_1d >= 3.0:
            score -= 10.0
    if return_5d is not None and return_5d >= 12.0:
        score -= 15.0
    if distance_high is not None:
        if -8.0 <= distance_high <= -2.0:
            score += 10.0
        elif (
            distance_high >= -1.5
            and position_label != "early_breakout"
            and (
                (change_1d is not None and change_1d >= 3.0)
                or (return_5d is not None and return_5d >= 6.0)
            )
        ):
            score -= 10.0
    if distance_ma20 is not None:
        if -1.0 <= distance_ma20 <= 6.0:
            score += 5.0
        elif distance_ma20 < -4.0:
            score -= 15.0
    return _clamp(score, 0.0, 100.0)


def _entry_triggers(
    *,
    entry_state: str,
    status: str,
    evidence_quality: str,
    change_1d: float | None,
    flow_5d: float | None,
    direction_score: float,
    distance_high: float | None,
) -> list[str]:
    triggers: list[str] = []
    if entry_state == ENTRY_READY_TO_START:
        return ["首批后继续确认5日资金与20日相对强度，不预先承诺后续加仓"]
    if evidence_quality == "insufficient":
        triggers.append("补齐20日价格结构与多维证据")
    if status not in {"forming", "confirmed"}:
        triggers.append("主线状态升至形成中或已确认")
    if flow_5d is None or flow_5d <= 0:
        triggers.append("近5日主力资金转为净流入")
    if direction_score < 55.0:
        triggers.append("20日相对强度与趋势继续改善")
    if change_1d is not None and change_1d >= 4.0:
        triggers.append("单日涨幅回落至3%以内")
    if distance_high is not None and distance_high >= -1.5:
        triggers.append("价格离开阶段极端高位并出现承接")
    return _unique_evidence(triggers)[:4]


def _invalidation_signals(
    *,
    entry_state: str,
    flow_5d: float | None,
    distance_ma20: float | None,
) -> list[str]:
    values = ["主线状态转为退潮或资金高位派发"]
    if flow_5d is None or flow_5d >= 0:
        values.append("近5日主力资金转为持续净流出")
    if distance_ma20 is None or distance_ma20 >= -4.0:
        values.append("价格跌破20日均线且相对强度同步转弱")
    if entry_state == ENTRY_READY_ON_PULLBACK:
        values.append("回调过程中资金继续流出而非缩量承接")
    return values[:3]


def _weighted_available_score(
    values: tuple[tuple[float | None, float], ...],
) -> float | None:
    available = [(value, weight) for value, weight in values if value is not None]
    total_weight = sum(weight for _, weight in available)
    if total_weight <= 0:
        return None
    return sum(float(value) * weight for value, weight in available) / total_weight


def _entry_sort_score(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(_ENTRY_STATE_PRIORITY.get(str(row.get("entry_state") or ""), 0)),
        float(_EVIDENCE_QUALITY_PRIORITY.get(str(row.get("evidence_quality") or ""), 0)),
        _num(row.get("research_score")) or 0.0,
        _num(row.get("entry_readiness_score")) or 0.0,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def _research_sort_score(row: dict[str, Any]) -> tuple[float, float]:
    return (
        _num(row.get("research_score")) or _num(row.get("score")) or 0.0,
        _num(row.get("score")) or 0.0,
    )


def _take_with_group_limit(
    rows: list[dict[str, Any]],
    limit: int,
    already_selected: list[dict[str, Any]],
    max_per_group: int,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    selected_labels: set[str] = set()
    selected_identities: set[str] = set()
    for item in already_selected:
        group = str(item.get("sector_group") or item.get("sector_label"))
        counts[group] = counts.get(group, 0) + 1
        selected_label = str(item["sector_label"])
        selected_labels.add(selected_label)
        selected_identities.add(_direction_identity(selected_label))
    for row in rows:
        if len(picked) >= limit:
            break
        label = str(row["sector_label"])
        identity = _direction_identity(label)
        if label in selected_labels or identity in selected_identities:
            continue
        group = str(row.get("sector_group") or label)
        if counts.get(group, 0) >= max_per_group:
            continue
        picked.append(row)
        selected_labels.add(label)
        selected_identities.add(identity)
        counts[group] = counts.get(group, 0) + 1
    return picked


def _direction_identity(label: str) -> str:
    normalized = str(label or "").strip()
    return _EQUIVALENT_DIRECTION_LABELS.get(normalized, normalized)


def _entry_hint(
    track: str,
    change_1d: float | None,
    change_5d: float | None,
    penalties: list[str],
) -> str:
    if "资金背离或持续流出" in penalties:
        return "资金背离，暂不入池"
    if change_1d is not None and change_1d >= 4.0:
        return "高位谨慎"
    if track == MOMENTUM_TRACK and change_1d is not None and change_1d < 0 and (change_5d or 0) > 0:
        return "回调承接观察"
    if track == SETUP_TRACK:
        return "蓄势观察"
    return "可分批关注"


_DIVERGENCE_EDGE_HIGH_THRESHOLD = 10.0


def _confidence(
    flow: dict,
    date_aligned: bool,
    penalties: list[str],
    divergence_backtest: dict | None = None,
) -> str:
    """板块方向置信度。

    M1.4 修复：此前该函数只有「低」（数据不可用/未对齐）与「中」（其余全部情况）两档，
    机制上就把"高"档位堵死了——无论证据多强都封顶在"中"，prompt 规则要求"中"只能措辞
    保留、不能作主理由，导致"果断"在架构层面不可能发生。现在当量价背离历史回测
    （`sector_flow_divergence_backtest.py`，M1.3）证据极强时允许升到"高"：
    证据强度决定档位，而不是机制性封顶。
    """
    if not flow or not flow.get("available"):
        return "低"
    if not date_aligned:
        return "低"
    if _divergence_evidence_is_strong(divergence_backtest, penalties):
        return "高"
    return "中"


def _divergence_evidence_is_strong(divergence_backtest: dict | None, penalties: list[str]) -> bool:
    if not divergence_backtest:
        return False
    by_rule = divergence_backtest.get("by_rule")
    if not isinstance(by_rule, dict):
        return False
    # 「资金背离或持续流出」命中时（distribution 模式），用 distribution 规则的历史回测
    # 证据判定；否则（当前方向偏多头）用 accumulation 规则。两者结构一致（均来自
    # signal_backtest_stats.finalize_bucket），只是预测方向相反。
    rule_id = (
        "flow_price_distribution"
        if "资金背离或持续流出" in penalties
        else "flow_price_accumulation"
    )
    bucket = by_rule.get(rule_id)
    if not isinstance(bucket, dict):
        return False
    edge = bucket.get("edge_percent")
    return bool(bucket.get("significant")) and edge is not None and float(edge) >= _DIVERGENCE_EDGE_HIGH_THRESHOLD


def _setup_price_score(change_1d: float | None, change_5d: float | None) -> float:
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    score = 0.0
    if -2.5 <= c1 <= 1.5:
        score += 8.0
    if -4.0 <= c5 <= 2.0:
        score += 8.0
    if c1 > 3.0 or c5 > 6.0:
        score -= 12.0
    return score


def _positive_score(value: float | None, *, scale: float, cap: float) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(cap, value / scale)


def _sector_group(label: str) -> str:
    return _SECTOR_GROUPS.get(label, label)


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _unique_labels(labels: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _unique_evidence(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
