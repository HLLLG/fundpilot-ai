from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import date
from math import isfinite
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Any

from app.models import Holding
from app.services.board_fund_flow_history import (
    get_cached_board_flow_series,
    resolve_board_flow_code_for_sector,
)
from app.services.eastmoney_spot_client import fetch_eastmoney_current_board_flow
from app.services.sector_labels import normalize_sector_label
from app.services.trading_session import get_effective_trade_date


_FLOW_HISTORY_BUDGET_SECONDS = 0.3
_CURRENT_FLOW_BUDGET_SECONDS = 1.4
_FLOW_IO_MAX_WORKERS = 6
_FLOW_IO_MAX_IN_FLIGHT = 12

# Cold Eastmoney history calls can take several seconds. Keep them off the
# request thread and single-flight repeated calls for the same board/date. The
# semaphore bounds both running and queued work so timed-out requests cannot
# create an unbounded backlog of retries or threads.
_HISTORY_EXECUTOR = ThreadPoolExecutor(
    max_workers=_FLOW_IO_MAX_WORKERS,
    thread_name_prefix="sector-flow-history",
)
_CURRENT_FLOW_EXECUTOR = ThreadPoolExecutor(
    max_workers=_FLOW_IO_MAX_WORKERS,
    thread_name_prefix="sector-flow-current",
)
_HISTORY_SLOTS = BoundedSemaphore(_FLOW_IO_MAX_IN_FLIGHT)
_CURRENT_FLOW_SLOTS = BoundedSemaphore(_FLOW_IO_MAX_IN_FLIGHT)
_HISTORY_FUTURES: dict[tuple[str, str], Future[Any]] = {}
_CURRENT_FLOW_FUTURES: dict[tuple[str, str], Future[Any]] = {}
_HISTORY_FUTURES_LOCK = Lock()
_CURRENT_FLOW_FUTURES_LOCK = Lock()
_LIVE_FLOW_UNSET = object()
_THEME_SNAPSHOT_UNSET = object()


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _normalize_flow_series(
    points: list[dict[str, Any]] | None,
    trade_date: str,
) -> list[dict[str, Any]]:
    """Return one finite main-force point per date, sorted through ``trade_date``.

    Source history can contain duplicate/out-of-order rows and partially written
    non-finite values. Later valid rows win for a duplicate date; this also lets
    the live point replace a same-day history row when it is appended last.
    """
    try:
        cutoff = date.fromisoformat(str(trade_date).strip())
    except (TypeError, ValueError):
        return []

    by_date: dict[str, dict[str, Any]] = {}
    for raw_point in points or []:
        if not isinstance(raw_point, dict):
            continue
        raw_date = str(raw_point.get("date") or "").strip()
        try:
            point_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if point_date > cutoff:
            continue
        main_force = _finite_number(raw_point.get("main_force_net_yi"))
        if main_force is None:
            continue
        normalized = dict(raw_point)
        normalized["date"] = point_date.isoformat()
        normalized["main_force_net_yi"] = main_force
        by_date[normalized["date"]] = normalized
    return [by_date[key] for key in sorted(by_date)]


def _sum_main_force(points: list[dict[str, Any]]) -> float | None:
    values = [
        number
        for point in points
        if (number := _finite_number(point.get("main_force_net_yi"))) is not None
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
    on_or_before = [point for point in series if str(point.get("date") or "") <= trade_date]
    if on_or_before:
        return max(on_or_before, key=lambda point: str(point.get("date") or ""))
    return None


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
    # A cached series that ends yesterday is still useful once today's exact
    # live point is merged. Forcing a refresh here turned that fast cache hit
    # into a multi-host cold network call and hid both pieces of evidence from
    # the outer report budget.
    return _normalize_flow_series(get_cached_board_flow_series(board_code), trade_date)


def _submit_singleflight(
    *,
    key: tuple[str, str],
    registry: dict[tuple[str, str], Future[Any]],
    registry_lock: Lock,
    slots: BoundedSemaphore,
    executor: ThreadPoolExecutor,
    loader,
    args: tuple[Any, ...],
    kwargs: dict[str, Any] | None = None,
) -> Future[Any] | None:
    """Submit bounded IO without ever waiting for worker cleanup in-request."""
    with registry_lock:
        existing = registry.get(key)
        if existing is not None and not existing.done():
            return existing
        if existing is not None:
            registry.pop(key, None)
        if not slots.acquire(blocking=False):
            return None
        try:
            future = executor.submit(loader, *args, **(kwargs or {}))
        except Exception:  # noqa: BLE001 - best-effort capacity guard
            slots.release()
            return None
        registry[key] = future

    def _cleanup(done: Future[Any]) -> None:
        with registry_lock:
            if registry.get(key) is done:
                registry.pop(key, None)
        slots.release()

    future.add_done_callback(_cleanup)
    return future


def _submit_history_load(board_code: str, trade_date: str) -> Future[Any] | None:
    return _submit_singleflight(
        key=(board_code, trade_date),
        registry=_HISTORY_FUTURES,
        registry_lock=_HISTORY_FUTURES_LOCK,
        slots=_HISTORY_SLOTS,
        executor=_HISTORY_EXECUTOR,
        loader=_load_flow_series,
        args=(board_code, trade_date),
    )


def _submit_current_flow_load(board_code: str, trade_date: str) -> Future[Any] | None:
    secid = board_code if "." in board_code else f"90.{board_code}"
    return _submit_singleflight(
        key=(board_code, trade_date),
        registry=_CURRENT_FLOW_FUTURES,
        registry_lock=_CURRENT_FLOW_FUTURES_LOCK,
        slots=_CURRENT_FLOW_SLOTS,
        executor=_CURRENT_FLOW_EXECUTOR,
        loader=fetch_eastmoney_current_board_flow,
        args=(secid,),
        kwargs={"trade_date": trade_date},
    )


def _future_result_before(
    future: Future[Any] | None,
    *,
    started_at: float,
    budget_seconds: float,
) -> Any:
    if future is None:
        return None
    remaining = max(0.0, started_at + max(0.0, budget_seconds) - monotonic())
    try:
        return future.result(timeout=remaining)
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001 - flow evidence is best-effort
        return None


def _future_result_if_done(future: Future[Any] | None) -> Any:
    """Read a late single-flight result without extending request latency."""
    if future is None or not future.done():
        return None
    try:
        return future.result(timeout=0)
    except Exception:  # noqa: BLE001 - flow evidence is best-effort
        return None


def _matching_theme_board_snapshot(trade_date: str) -> dict[str, Any] | None:
    try:
        from app.services.theme_board_snapshot import get_theme_board_snapshot_cache_only

        snapshot = get_theme_board_snapshot_cache_only()
    except Exception:  # noqa: BLE001 - cache-only evidence is best-effort
        return None
    if not isinstance(snapshot, dict) or snapshot.get("trade_date") != trade_date:
        return None
    return snapshot


def get_matching_theme_board_flow_snapshot(trade_date: str) -> dict[str, Any] | None:
    """Capture one same-day theme snapshot for all flow rows in a decision run."""
    return _matching_theme_board_snapshot(trade_date)


def _validated_theme_board_snapshot(
    snapshot: dict[str, Any] | None,
    trade_date: str,
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict) or snapshot.get("trade_date") != trade_date:
        return None
    return snapshot


def _live_today_flow_from_snapshot(
    snapshot: dict[str, Any] | None,
    board_code: str | None,
    *,
    sector_label: str | None = None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    normalized_label = normalize_sector_label(sector_label)
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_code = str(item.get("flow_source_code") or "").strip()
        item_label = normalize_sector_label(item.get("sector_label"))
        if not (
            (board_code and item_code == board_code)
            or (normalized_label and item_label == normalized_label)
        ):
            continue
        main_force = _finite_number(item.get("main_force_net_yi"))
        if main_force is None:
            return None
        return {
            "main_force_net_yi": main_force,
            "flow_tiers": item.get("flow_tiers"),
        }
    return None


def _five_day_rank_from_snapshot(
    snapshot: dict[str, Any] | None,
    board_code: str | None,
    trade_date: str,
    *,
    sector_label: str | None = None,
) -> float | None:
    """Use only a rank aggregate whose row-level f124 date exactly matches."""
    if snapshot is None:
        return None
    normalized_label = normalize_sector_label(sector_label)
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_code = str(item.get("flow_source_code") or "").strip()
        item_label = normalize_sector_label(item.get("sector_label"))
        if not (
            (board_code and item_code == board_code)
            or (normalized_label and item_label == normalized_label)
        ):
            continue
        if str(item.get("flow_data_date") or "").strip() != trade_date:
            return None
        return _finite_number(item.get("cumulative_5d_net_yi"))
    return None


def _live_today_flow_from_theme_board(
    board_code: str,
    trade_date: str,
) -> dict[str, Any] | None:
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
    return _live_today_flow_from_snapshot(
        _matching_theme_board_snapshot(trade_date),
        board_code,
    )


def _ensure_today_point(
    series: list[dict[str, Any]],
    board_code: str,
    trade_date: str,
    *,
    live: dict[str, Any] | None | object = _LIVE_FLOW_UNSET,
) -> list[dict[str, Any]]:
    """Merge the live snapshot as the authoritative point for ``trade_date``."""
    if live is _LIVE_FLOW_UNSET:
        live = _live_today_flow_from_theme_board(board_code, trade_date)
    candidates = list(series)
    if isinstance(live, dict):
        candidates.append({"date": trade_date, **live})
    return _normalize_flow_series(candidates, trade_date)


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
    theme_snapshot: dict[str, Any] | None | object = _THEME_SNAPSHOT_UNSET,
) -> dict[str, Any] | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    target_trade_date = trade_date or get_effective_trade_date()

    board_code, resolved_label = resolve_board_flow_code_for_sector(label)
    matching_snapshot = (
        _matching_theme_board_snapshot(target_trade_date)
        if theme_snapshot is _THEME_SNAPSHOT_UNSET
        else _validated_theme_board_snapshot(theme_snapshot, target_trade_date)
    )
    live = _live_today_flow_from_snapshot(
        matching_snapshot,
        board_code,
        sector_label=label,
    )
    rank_five_day = _five_day_rank_from_snapshot(
        matching_snapshot,
        board_code,
        target_trade_date,
        sector_label=label,
    )
    if not board_code and live is None:
        return {
            "available": False,
            "sector_label": label,
            "today_available": False,
            "five_day_available": False,
            "five_day_source": None,
            "history_point_count": 0,
            "message": "未解析到板块资金流代码",
        }

    io_started_at = monotonic()

    # Start both independent sources together. History gets only a small
    # request-time budget; a running cold fetch may finish and warm its cache,
    # but its cleanup is never awaited by this response.
    history_future = (
        _submit_history_load(board_code, target_trade_date)
        if board_code
        else None
    )
    # A matching bulk snapshot remains the preferred same-day authority. On a
    # cold cache, however, a report for the *current* effective trading day may
    # use the bounded targeted endpoint: both its parser and the merge below
    # still require the returned date to equal ``target_trade_date``. Explicitly
    # historical decisions never take this fallback, so today's live response
    # cannot leak into a past report.
    may_fetch_current = (
        board_code is not None
        and live is None
        and (
            matching_snapshot is not None
            or target_trade_date == get_effective_trade_date()
        )
    )
    current_flow_future = (
        _submit_current_flow_load(board_code, target_trade_date)
        if may_fetch_current
        else None
    )

    loaded_history = _future_result_before(
        history_future,
        started_at=io_started_at,
        budget_seconds=_FLOW_HISTORY_BUDGET_SECONDS,
    )
    series = loaded_history if isinstance(loaded_history, list) else []

    # Exact same-day history is already sufficient. Otherwise use the small
    # targeted fflow window, whose own parser and this caller both enforce the
    # requested date before labeling it as today's point.
    if live is None and not _series_has_date(series, target_trade_date):
        current_flow = _future_result_before(
            current_flow_future,
            started_at=io_started_at,
            budget_seconds=_CURRENT_FLOW_BUDGET_SECONDS,
        )
        if (
            isinstance(current_flow, dict)
            and current_flow.get("date") == target_trade_date
            and (
                main_force := _finite_number(current_flow.get("main_force_net_yi"))
            )
            is not None
        ):
            live = {
                "main_force_net_yi": main_force,
                "flow_tiers": current_flow.get("flow_tiers"),
            }
    # History may have completed while this request was already waiting for
    # the current-day lookup. Read that shared result only when done; never
    # cancel or add another wait because other callers may own the same future.
    late_history = _future_result_if_done(history_future)
    if isinstance(late_history, list):
        series = _normalize_flow_series([*series, *late_history], target_trade_date)

    series = _ensure_today_point(
        series,
        board_code or label,
        target_trade_date,
        live=live,
    )
    history_point_count = len(series)
    if not series:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "trade_date": target_trade_date,
            "today_available": False,
            "five_day_available": False,
            "five_day_source": None,
            "history_point_count": history_point_count,
            "message": "暂无板块历史资金流",
        }

    point = _pick_flow_point(series, target_trade_date)
    if point is None:
        return {
            "available": False,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "trade_date": target_trade_date,
            "today_available": False,
            "five_day_available": False,
            "five_day_source": None,
            "history_point_count": history_point_count,
            "message": "暂无板块历史资金流",
        }

    flow_date = str(point.get("date") or "")
    date_aligned = flow_date == target_trade_date
    today_available = _series_has_date(series, target_trade_date)
    history_five_day_available = today_available and history_point_count >= 5
    rank_five_day_available = (
        today_available
        and not history_five_day_available
        and rank_five_day is not None
    )
    five_day_available = history_five_day_available or rank_five_day_available
    recent_5d = _slice_tail(series, 5) if history_five_day_available else []
    recent_20d = _slice_tail(series, 20)
    today_flow = point.get("main_force_net_yi")
    tiers = point.get("flow_tiers")
    if history_five_day_available:
        cumulative_5d = _sum_main_force(recent_5d)
        five_day_source = "history"
    elif rank_five_day_available:
        cumulative_5d = round(float(rank_five_day), 2)
        five_day_source = "eastmoney_rank"
    else:
        cumulative_5d = None
        five_day_source = None
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
        "today_available": today_available,
        "five_day_available": five_day_available,
        "five_day_source": five_day_source,
        "history_point_count": history_point_count,
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
