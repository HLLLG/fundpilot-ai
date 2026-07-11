from __future__ import annotations

"""通用板块方向机会打分（双轨：顺势 momentum / 蓄势 setup）。

原实现位于 discovery_sector_opportunity.py（荐基专用），2026-07 抽取为共享模块，
供日报（report_sector_opportunity.py）与荐基共用同一套打分口径，避免两条链路对
「同一板块当前是什么方向」给出不一致的结论。discovery_sector_opportunity.py 保留
为薄封装以维持向后兼容。
"""

from concurrent.futures import ThreadPoolExecutor, wait
from math import isfinite
from typing import Any

MOMENTUM_TRACK = "momentum"
SETUP_TRACK = "setup"

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


def select_sector_opportunities(
    sector_heat: list[dict],
    *,
    sector_flow_by_label: dict[str, dict] | None = None,
    sector_divergence_by_label: dict[str, dict] | None = None,
    focus_sectors: list[str] | None = None,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
) -> list[dict[str, Any]]:
    flow_by_label = sector_flow_by_label or {}
    divergence_by_label = sector_divergence_by_label or {}
    focus = {str(label).strip() for label in (focus_sectors or []) if str(label).strip()}
    scored = [
        _score_row(
            row,
            flow_by_label.get(str(row.get("sector_label") or "").strip()),
            focus,
            divergence_backtest=divergence_by_label.get(str(row.get("sector_label") or "").strip()),
        )
        for row in sector_heat
    ]
    rows = [row for row in scored if row is not None]

    momentum = sorted(
        [row for row in rows if row["track"] == MOMENTUM_TRACK],
        key=lambda row: row["score"],
        reverse=True,
    )
    setup = sorted(
        [row for row in rows if row["track"] == SETUP_TRACK],
        key=lambda row: row["score"],
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
            key=lambda row: row["score"],
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
    from app.services.sector_fund_flow_context import build_sector_fund_flow_context

    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    labels = _unique_labels(sector_labels)
    if not labels:
        return {}

    def load(label: str) -> tuple[str, dict | None]:
        heat = heat_by_label.get(label) or {}
        change_1d = _num(heat.get("change_1d_percent"))
        try:
            flow = build_sector_fund_flow_context(
                label,
                sector_return_percent=change_1d,
                trade_date=trade_date,
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
) -> dict[str, Any] | None:
    result = _compute_opportunity_row(row, flow, focus, divergence_backtest)
    if result is None or not result["opportunity_available"]:
        return None
    return {key: value for key, value in result.items() if key != "opportunity_available"}


def _compute_opportunity_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    divergence_backtest: dict | None = None,
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
    return {
        "sector_label": label,
        "track": track,
        "score": round(max(momentum_score, setup_score), 2),
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
        "history_point_count": history_point_count,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": flow_5d,
        "pattern_label": pattern or None,
        "sector_group": _sector_group(label),
        "opportunity_available": not disqualified,
    }


def _take_with_group_limit(
    rows: list[dict[str, Any]],
    limit: int,
    already_selected: list[dict[str, Any]],
    max_per_group: int,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in already_selected:
        group = str(item.get("sector_group") or item.get("sector_label"))
        counts[group] = counts.get(group, 0) + 1
    for row in rows:
        if len(picked) >= limit:
            break
        if row["sector_label"] in {item["sector_label"] for item in [*already_selected, *picked]}:
            continue
        group = str(row.get("sector_group") or row["sector_label"])
        if counts.get(group, 0) >= max_per_group:
            continue
        picked.append(row)
        counts[group] = counts.get(group, 0) + 1
    return picked


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
