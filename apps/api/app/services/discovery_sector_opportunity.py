from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
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
    focus_sectors: list[str] | None = None,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
) -> list[dict[str, Any]]:
    flow_by_label = sector_flow_by_label or {}
    focus = {str(label).strip() for label in (focus_sectors or []) if str(label).strip()}
    scored = [
        _score_row(
            row,
            flow_by_label.get(str(row.get("sector_label") or "").strip()),
            focus,
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
            )
        except Exception:  # noqa: BLE001 - opportunity flow is best-effort
            return label, None
        return label, flow or None

    result: dict[str, dict] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="discovery-opportunity-flow",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        try:
            for future in as_completed(futures, timeout=max(0.0, total_timeout_seconds)):
                label, flow = future.result()
                if flow:
                    result[label] = flow
        except FutureTimeoutError:
            pass
        finally:
            for future in futures:
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def _score_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
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
    today_flow = _num(flow.get("today_main_force_net_yi"))
    flow_5d = _num(flow.get("cumulative_5d_net_yi"))

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

    if pattern in _DISTRIBUTION_PATTERNS and (today_flow or 0.0) <= 0 and (flow_5d or 0.0) <= 0:
        return None
    if max(momentum_score, setup_score) <= 0:
        return None

    track = MOMENTUM_TRACK if momentum_score >= setup_score else SETUP_TRACK
    return {
        "sector_label": label,
        "track": track,
        "score": round(max(momentum_score, setup_score), 2),
        "confidence": _confidence(flow, date_aligned, penalties),
        "entry_hint": _entry_hint(track, change_1d, change_5d, penalties),
        "evidence": _unique_evidence(evidence)[:5],
        "penalties": penalties[:5],
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": flow_5d,
        "pattern_label": pattern or None,
        "sector_group": _sector_group(label),
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


def _confidence(flow: dict, date_aligned: bool, penalties: list[str]) -> str:
    if not flow or not flow.get("available"):
        return "低"
    if not date_aligned:
        return "低"
    if penalties:
        return "中"
    return "中"


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
        return float(value)
    except (TypeError, ValueError):
        return None


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

