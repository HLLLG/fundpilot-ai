from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.models import Holding
from app.services.board_fund_flow_history import (
    get_cached_board_flow_series,
    resolve_board_flow_code_for_sector,
)
from app.services.sector_labels import normalize_sector_label
from app.services.trading_session import get_effective_trade_date


def _sum_main_force(points: list[dict[str, Any]]) -> float | None:
    values = [
        float(point["main_force_net_yi"])
        for point in points
        if point.get("main_force_net_yi") is not None
    ]
    if not values:
        return None
    return round(sum(values), 2)


def _slice_tail(points: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    if len(points) <= days:
        return list(points)
    return points[-days:]


def _pick_flow_point(series: list[dict[str, Any]], trade_date: str) -> dict[str, Any] | None:
    """按 effective_trade_date 取当日资金流；缺失则取不晚于该日的最近一条。"""
    if not series or not trade_date:
        return series[-1] if series else None
    for point in reversed(series):
        if point.get("date") == trade_date:
            return point
    on_or_before = [
        point for point in series if str(point.get("date") or "") <= trade_date
    ]
    if on_or_before:
        return on_or_before[-1]
    return series[-1]


def _series_has_date(series: list[dict[str, Any]], trade_date: str) -> bool:
    return any(point.get("date") == trade_date for point in series)


def _main_force_direction(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0.05:
        return "inflow"
    if value < -0.05:
        return "outflow"
    return "flat"


def _load_flow_series(board_code: str, trade_date: str) -> list[dict[str, Any]]:
    series = get_cached_board_flow_series(board_code)
    if series and not _series_has_date(series, trade_date):
        refreshed = get_cached_board_flow_series(board_code, force_refresh=True)
        if refreshed:
            series = refreshed
    return series


def _live_today_flow_from_theme_board(board_code: str) -> dict[str, Any] | None:
    """从主题板块缓存（东财 clist 实时快照，与板块涨跌幅同一次请求）取当日主力净流入。

    东财历史资金流接口（``fflow/daykline``）只在收盘后才落定「今日」这一行，盘中
    调用时序列最新一条往往还是前一交易日的数据——这正是资金流日期对不上涨跌幅日期
    的根因（并非缓存过期或时区问题）。主题板块榜的 ``main_force_net_yi`` 字段和
    当日涨跌幅（``change_1d_percent``）来自同一次 ``fetch_eastmoney_clist_theme_metrics_by_code``
    实时快照请求，天然同日对齐，直接复用它作「今日」资金流的权威来源，而不是让
    daykline 历史序列的滞后值被误标成「今日」。

    该函数只读已有的主题板块缓存（`get_theme_board_snapshot` 命中时不发网络请求），
    成本可忽略；缓存未命中或该板块不在主题白名单内时返回 None，调用方据此回退到
    历史序列自身的（可能滞后的）取值。
    """
    try:
        from app.services.theme_board_snapshot import get_theme_board_snapshot_cache_only

        snapshot = get_theme_board_snapshot_cache_only()
    except Exception:  # noqa: BLE001 - best-effort，不可阻塞资金流上下文
        return None
    if not snapshot:
        return None
    for item in snapshot.get("items") or []:
        if str(item.get("flow_source_code") or "").strip() != board_code:
            continue
        main_force = item.get("main_force_net_yi")
        if main_force is None:
            return None
        return {
            "main_force_net_yi": main_force,
            "flow_tiers": item.get("flow_tiers"),
        }
    return None


def _ensure_today_point(
    series: list[dict[str, Any]],
    board_code: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """历史资金流序列缺当日数据时，拼接主题板块实时快照的当日值，保证与当日涨跌幅同源对齐。"""
    if _series_has_date(series, trade_date):
        return series
    live = _live_today_flow_from_theme_board(board_code)
    if live is None:
        return series
    return [*series, {"date": trade_date, **live}]


_TIER_STRUCTURE_THRESHOLD = 0.5


def _flow_structure_hint(flow_tiers: dict[str, Any] | None) -> str | None:
    """把当日超大单(机构)/大单/中单(大户)/小单(散户)四档净流入拆成一句机构 vs 散户
    资金结构解读，不依赖涨跌方向（此前该解读只嵌在「涨但主力流出」这一种 pattern
    分支里，其余分支即使四档结构同样出现机构与散户反向操作也不会提示）。"""
    tiers = flow_tiers or {}
    super_large = tiers.get("super_large_net_yi")
    large = tiers.get("large_net_yi")
    medium = tiers.get("medium_net_yi")
    small = tiers.get("small_net_yi")
    if super_large is None and large is None and medium is None and small is None:
        return None

    institutional = (super_large or 0.0) + (large or 0.0)
    retail = (medium or 0.0) + (small or 0.0)
    threshold = _TIER_STRUCTURE_THRESHOLD

    if institutional > threshold and retail < -threshold:
        return "超大单+大单（机构）净流入而中单+小单（大户/散户）净流出，机构资金主导。"
    if institutional < -threshold and retail > threshold:
        return "超大单+大单（机构）净流出而中单+小单（大户/散户）净流入，散户接盘特征明显。"
    if institutional > threshold and retail > threshold:
        return "机构与散户资金同向净流入，多方资金结构一致。"
    if institutional < -threshold and retail < -threshold:
        return "机构与散户资金同向净流出，多方资金结构一致偏弱。"
    return None


def _classify_flow_pattern(
    *,
    sector_return_percent: float | None,
    today_flow: float | None,
    cumulative_5d: float | None,
    flow_tiers: dict[str, Any] | None,
) -> dict[str, Any]:
    price = sector_return_percent
    flow = today_flow
    price_up = price is not None and price > 0.5
    price_down = price is not None and price < -0.5
    flow_in = flow is not None and flow > 0.5
    flow_out = flow is not None and flow < -0.5

    structure_hint = _flow_structure_hint(flow_tiers)

    if price_up and flow_out:
        hint = "板块收涨但主力净流出，警惕高位出货或诱多，不宜追涨。"
        if structure_hint:
            hint += structure_hint
        return {
            "pattern_label": "distribution",
            "pattern_hint": hint,
            "flow_structure_hint": structure_hint,
        }

    if price_down and flow_in:
        return {
            "pattern_label": "accumulation",
            "pattern_hint": "板块下跌但主力净流入，或在低位洗盘/吸筹，勿因单日下跌盲目止损。",
            "flow_structure_hint": structure_hint,
        }

    if price_up and flow_in:
        return {
            "pattern_label": "price_flow_aligned_up",
            "pattern_hint": "量价资金同向偏强，短线动能较好但需防过热。",
            "flow_structure_hint": structure_hint,
        }

    if price_down and flow_out:
        return {
            "pattern_label": "weak_outflow",
            "pattern_hint": "板块弱势且资金持续流出，短线加仓胜率通常不高。",
            "flow_structure_hint": structure_hint,
        }

    if cumulative_5d is not None and cumulative_5d > 3 and flow_out:
        return {
            "pattern_label": "multi_day_inflow_then_outflow",
            "pattern_hint": "近5日累计净流入后今日转出，关注是否阶段性兑现。",
            "flow_structure_hint": structure_hint,
        }

    if cumulative_5d is not None and cumulative_5d < -3 and flow_in:
        return {
            "pattern_label": "multi_day_outflow_then_inflow",
            "pattern_hint": "近5日累计净流出后今日回流，或为短暂反弹或资金回补。",
            "flow_structure_hint": structure_hint,
        }

    return {
        "pattern_label": "neutral",
        "pattern_hint": "量价与主力流向未形成明显背离，宜结合 news 与 nav_trend 综合判断。",
        "flow_structure_hint": structure_hint,
    }


def build_sector_fund_flow_context(
    sector_name: str | None,
    *,
    sector_return_percent: float | None = None,
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    target_trade_date = trade_date or get_effective_trade_date()

    board_code, resolved_label = resolve_board_flow_code_for_sector(label)
    if not board_code:
        return {
            "available": False,
            "sector_label": label,
            "message": "未解析到板块资金流代码",
        }

    series = _load_flow_series(board_code, target_trade_date)
    if not series:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块历史资金流",
        }
    series = _ensure_today_point(series, board_code, target_trade_date)

    point = _pick_flow_point(series, target_trade_date)
    if point is None:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块历史资金流",
        }

    flow_date = str(point.get("date") or "")
    date_aligned = flow_date == target_trade_date
    recent_5d = _slice_tail(series, 5)
    recent_20d = _slice_tail(series, 20)
    today_flow = point.get("main_force_net_yi")
    tiers = point.get("flow_tiers")
    cumulative_5d = _sum_main_force(recent_5d)
    cumulative_20d = _sum_main_force(recent_20d)

    if date_aligned:
        pattern = _classify_flow_pattern(
            sector_return_percent=sector_return_percent,
            today_flow=today_flow,
            cumulative_5d=cumulative_5d,
            flow_tiers=tiers if isinstance(tiers, dict) else None,
        )
    else:
        pattern = {
            "pattern_label": "flow_date_mismatch",
            "pattern_hint": (
                f"板块资金流为 {flow_date} 数据，与当日 sector_return_percent"
                f"（{target_trade_date}）不同日，勿做量价背离判断。"
            ),
        }

    return {
        "available": True,
        "sector_label": resolved_label or label,
        "board_code": board_code,
        "trade_date": target_trade_date,
        "flow_date": flow_date,
        "date_aligned": date_aligned,
        "today_main_force_net_yi": today_flow,
        "main_force_direction": _main_force_direction(
            float(today_flow) if today_flow is not None else None
        ),
        "cumulative_5d_net_yi": cumulative_5d,
        "cumulative_20d_net_yi": cumulative_20d,
        # 仅保留「今日」的机构/大单/中单/散户四档结构（flow_tiers），5d/20d 只给主力
        #净流入的汇总数字——不把每日逐档明细序列喂给 LLM（体积大且当前判断逻辑
        # 用不上逐日结构，只在最新一天做「机构 vs 散户」背离解读）。
        "flow_tiers": tiers,
        **pattern,
    }


def build_sector_fund_flow_map(
    holdings: list[Holding],
    *,
    trade_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """按 normalized sector 名去重拉取，供多只同板块基金复用。"""
    target_trade_date = trade_date or get_effective_trade_date()

    # 先去重出待拉取的板块标签（保序），再并发拉取——每个板块是独立的东财
    # 资金流历史 HTTP 请求（IO 密集），并发可显著压低多板块组合的耗时。
    unique_labels: list[str] = []
    seen: set[str] = set()
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in seen:
            continue
        seen.add(label)
        unique_labels.append(label)

    if not unique_labels:
        return {}

    return_by_label: dict[str, float | None] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if label and label not in return_by_label:
            return_by_label[label] = holding.sector_return_percent

    def _fetch(label: str) -> tuple[str, dict[str, Any] | None]:
        context = build_sector_fund_flow_context(
            label,
            sector_return_percent=return_by_label.get(label),
            trade_date=target_trade_date,
        )
        return label, context

    result: dict[str, dict[str, Any]] = {}
    if len(unique_labels) == 1:
        label, context = _fetch(unique_labels[0])
        if context is not None:
            result[label] = context
        return result

    max_workers = min(6, len(unique_labels))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for label, context in executor.map(_fetch, unique_labels):
            if context is not None:
                result[label] = context
    return result


def sector_fund_flow_for_holding(
    holding: Holding,
    flow_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    label = normalize_sector_label(holding.sector_name)
    if not label:
        return None
    cached = flow_map.get(label)
    if cached is not None:
        return cached
    return build_sector_fund_flow_context(
        label,
        sector_return_percent=holding.sector_return_percent,
        trade_date=get_effective_trade_date(),
    )
