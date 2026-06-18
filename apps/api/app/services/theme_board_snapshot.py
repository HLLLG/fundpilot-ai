from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Literal

from app.models import Holding
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.akshare_spot_client import fetch_akshare_board_records, fetch_boards_via_akshare
from app.services.eastmoney_spot_client import fetch_eastmoney_board_records
from app.services.sector_board_snapshot import get_sector_board_snapshot
from app.services.sector_canonical import (
    CanonicalSector,
    get_canonical_sector,
    get_quote_canonical_sector,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

SortMode = Literal["change", "streak"]
BoardKind = Literal["industry", "concept", "index"]

_LIVE_TTL_SECONDS = 60.0
_CLOSED_TTL_SECONDS = 3600.0
_CACHE_VERSION = "v3"
_REFRESH_BUDGET_SECONDS = 120.0
_SERIES_TIMEOUT = 8.0
_MAX_WORKERS = 8

# 对标小倍养基「今日板块涨幅榜」的粗粒度精选清单（固定白名单，2026-06-18 截图口径）。
# 东财 m:90 t:2/t:3 含 ~500 细分行业/概念，过碎；此处只取小倍式粗板块。
# 解析优先级：canonical → 别名 secid → 东财概念/行业精确名匹配 → 跳过。
_THEME_BOARD_WHITELIST: tuple[str, ...] = (
    "人工智能", "消费电子", "半导体", "5G", "电子", "通信技术", "稀土", "创新药",
    "云计算", "信创", "CPO", "MLCC", "存储芯片", "计算机", "半导体材料", "智能家居",
    "PCB", "机器人", "医药", "算力租赁", "软件", "电网设备", "传媒", "脑机接口",
    "可控核聚变", "商业航天", "体育", "低空经济", "军工", "动漫游戏", "固态电池",
    "钢铁", "有色金属", "黄金", "机械设备", "储能", "锂电池", "国企改革", "中药",
    "汽车", "房地产", "新能源车", "光伏", "新能源", "金融科技", "环保", "畜牧养殖",
    "农业", "基建", "交通运输", "红利", "食品饮料", "贵金属", "化工", "银行", "锂矿",
    "白酒", "黄金股", "建材", "证券", "煤炭", "电力", "证券保险", "保险",
)

# 小倍名 → 东财近义板块（canonical/精确名都匹配不到时用）：(secid, source_code, board_kind)
_THEME_BOARD_ALIAS: dict[str, tuple[str, str, str]] = {
    "软件": ("90.BK0737", "BK0737", "industry"),        # 软件开发
    "算力租赁": ("90.BK1134", "BK1134", "concept"),       # 算力概念
    "脑机接口": ("90.BK0706", "BK0706", "concept"),       # 人脑工程
    "体育": ("90.BK0708", "BK0708", "concept"),          # 体育产业
    "动漫游戏": ("90.BK0509", "BK0509", "concept"),       # 网络游戏
    "储能": ("90.BK0989", "BK0989", "concept"),          # 储能概念
    "国企改革": ("90.BK0683", "BK0683", "concept"),       # 央国企改革
    "中药": ("90.BK0615", "BK0615", "concept"),          # 中药概念
    "金融科技": ("90.BK0637", "BK0637", "concept"),       # 互联网金融
    "畜牧养殖": ("90.BK1259", "BK1259", "industry"),      # 养殖业
    "农业": ("90.BK0433", "BK0433", "industry"),         # 农林牧渔
    "基建": ("90.BK1247", "BK1247", "industry"),         # 基础建设
    "红利": ("90.BK1641", "BK1641", "concept"),          # 红利股
    "化工": ("90.BK1206", "BK1206", "industry"),         # 基础化工
    "锂矿": ("90.BK1173", "BK1173", "concept"),          # 锂矿概念
    "黄金股": ("90.BK0547", "BK0547", "concept"),         # 黄金概念
    "建材": ("90.BK1208", "BK1208", "industry"),         # 建筑材料
    "保险": ("90.BK0474", "BK0474", "industry"),         # 保险Ⅱ
}

_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}


# ---------------------------------------------------------------------------
# 连涨天数
# ---------------------------------------------------------------------------
def compute_consecutive_up_days(
    series: list[dict],
    trade_date: str | None,
) -> int | None:
    """从有效交易日 bar 向前统计 change_percent > 0 的连续天数。"""
    if not series:
        return None

    bars = _bars_through_trade_date(series, trade_date)
    if not bars:
        return None

    latest_change = _as_float(bars[-1].get("change_percent"))
    if latest_change is None:
        return None
    if latest_change <= 0:
        return 0

    streak = 0
    for bar in reversed(bars):
        change = _as_float(bar.get("change_percent"))
        if change is None:
            break
        if change > 0:
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# 板块全集（行业全量 + canonical 概念/指数，去重）
# ---------------------------------------------------------------------------
def _board_kind_from_source_type(source_type: str) -> BoardKind:
    if source_type in {"industry", "concept", "index"}:
        return source_type  # type: ignore[return-value]
    return "concept"


def list_theme_board_universe() -> list[dict[str, Any]]:
    """对标小倍的固定粗粒度板块白名单，解析到东财 secid。

    解析优先级（每个白名单名）：canonical → `_THEME_BOARD_ALIAS` 近义板块 →
    东财概念/行业**精确名**匹配 → 跳过（港股/指数类等无干净 A 股板块的名暂不纳入）。
    每项：``sector_label``、``secid``、``source_code``、``board_kind``、
    ``change_hint``（东财 spot f3，连涨拉取失败时兜底涨跌幅）、``_canon``。
    """
    concept_by_name, concept_by_code = _load_board_maps("concept")
    industry_by_name, industry_by_code = _load_board_maps("industry")

    def change_for_code(code: str | None) -> float | None:
        if not code:
            return None
        if code in concept_by_code:
            return concept_by_code[code]
        if code in industry_by_code:
            return industry_by_code[code]
        return None

    universe: list[dict[str, Any]] = []
    seen_secids: set[str] = set()

    for name in _THEME_BOARD_WHITELIST:
        entry: dict[str, Any] | None = None

        canon = get_quote_canonical_sector(name) or get_canonical_sector(name)
        if canon is not None:
            semantic = get_canonical_sector(name) or canon
            entry = {
                "sector_label": name,
                "secid": canon.eastmoney_secid,
                "source_code": canon.source_code,
                "board_kind": _board_kind_from_source_type(semantic.source_type),
                "change_hint": change_for_code(canon.source_code),
                "_canon": canon,
            }
        elif name in _THEME_BOARD_ALIAS:
            secid, code, kind = _THEME_BOARD_ALIAS[name]
            entry = {
                "sector_label": name,
                "secid": secid,
                "source_code": code,
                "board_kind": kind,
                "change_hint": change_for_code(code),
                "_canon": None,
            }
        elif name in concept_by_name:
            code = concept_by_name[name]
            entry = {
                "sector_label": name,
                "secid": f"90.{code}",
                "source_code": code,
                "board_kind": "concept",
                "change_hint": concept_by_code.get(code),
                "_canon": None,
            }
        elif name in industry_by_name:
            code = industry_by_name[name]
            entry = {
                "sector_label": name,
                "secid": f"90.{code}",
                "source_code": code,
                "board_kind": "industry",
                "change_hint": industry_by_code.get(code),
                "_canon": None,
            }
        else:
            logger.info("theme board whitelist name unresolved: %s", name)
            continue

        if entry["secid"] in seen_secids:
            continue
        seen_secids.add(entry["secid"])
        universe.append(entry)

    return universe


def _load_board_maps(board_type: str) -> tuple[dict[str, str], dict[str, float]]:
    """返回 (name→code, code→change_percent)；拉取失败时返回空表（降级用 canonical）。"""
    by_name: dict[str, str] = {}
    by_code: dict[str, float] = {}
    try:
        rows = fetch_eastmoney_board_records(board_type)
    except Exception as exc:
        logger.info("theme universe %s spot failed: %s", board_type, exc)
        return by_name, by_code
    for row in rows:
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if not name or not code:
            continue
        by_name.setdefault(name, code)
        change = _as_float(row.get("change_percent"))
        if change is not None:
            by_code[code] = change
    return by_name, by_code


# ---------------------------------------------------------------------------
# 后台刷新：同源拉日 K 算 change + streak，写缓存
# ---------------------------------------------------------------------------
def _fetch_universe_series(
    secid: str,
    source_code: str | None = None,
    *,
    canon: CanonicalSector | None = None,
    timeout: float = _SERIES_TIMEOUT,
) -> list[dict]:
    """按 secid 拉 push2delay 日 K 序列（→ relay → AkShare）。"""
    if canon is None:
        source_type = "index" if str(secid).startswith("2.") else "concept"
        canon = CanonicalSector(
            label=secid,
            source_type=source_type,
            source_name=secid,
            eastmoney_secid=secid,
            source_code=source_code,
        )
    return fetch_canonical_daily_kline_series(
        canon,
        max_days=20,
        timeout=timeout,
        allow_akshare=False,
    )


def refresh_theme_board_snapshot(*, trade_date: str | None = None) -> dict[str, Any]:
    """后台刷新主体：~100 板块并行拉日 K，算 change + streak，写缓存并返回快照。"""
    session = build_trading_session()
    resolved_date = trade_date or session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    universe = list_theme_board_universe()

    def enrich(entry: dict[str, Any]) -> dict[str, Any]:
        secid = entry["secid"]
        series = _fetch_universe_series(
            secid,
            entry.get("source_code"),
            canon=entry.get("_canon"),
        )
        change = _latest_change_percent(series, resolved_date) if series else None
        if change is None:
            change = entry.get("change_hint")  # 行业 spot f3 兜底（连涨拉取失败时）
        streak = compute_consecutive_up_days(series, resolved_date) if series else None
        return {
            "sector_label": entry["sector_label"],
            "board_kind": entry["board_kind"],
            "secid": secid,
            "change_1d_percent": change,
            "consecutive_up_days": streak,
        }

    def base_row(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "sector_label": entry["sector_label"],
            "board_kind": entry["board_kind"],
            "secid": entry["secid"],
            "change_1d_percent": entry.get("change_hint"),
            "consecutive_up_days": None,
        }

    items: list[dict[str, Any]] = []
    deadline = time.monotonic() + _REFRESH_BUDGET_SECONDS
    executor = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, max(len(universe), 1)))
    futures = {executor.submit(enrich, entry): entry for entry in universe}
    pending = set(futures)
    try:
        while pending and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            done, pending = wait(
                pending,
                timeout=min(0.5, max(0.05, remaining)),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                entry = futures[future]
                try:
                    items.append(future.result())
                except Exception as exc:
                    logger.debug("theme universe enrich failed: %s", exc)
                    items.append(base_row(entry))
        # 超预算未完成的板块用基础行补齐（change/streak=None）
        for future in pending:
            items.append(base_row(futures[future]))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # 日 K 缺失涨跌幅的板块，用行业现货榜按板块名兜底
    missing = [item for item in items if item["change_1d_percent"] is None]
    if missing:
        try:
            spot_changes = _load_theme_spot_changes()
        except Exception as exc:
            logger.debug("theme spot fallback failed: %s", exc)
            spot_changes = {}
        for item in missing:
            change = spot_changes.get(item["sector_label"])
            if change is not None:
                item["change_1d_percent"] = round(float(change), 2)

    snapshot = {
        "items": items,
        "trade_date": resolved_date,
        "session_kind": session_kind,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_spot_snapshot(f"theme:boards:{_CACHE_VERSION}:{resolved_date}", snapshot)
    return snapshot


# ---------------------------------------------------------------------------
# 持仓叠加 + payload
# ---------------------------------------------------------------------------
def _holding_secids(holdings: list[Holding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for holding in holdings:
        canon = get_quote_canonical_sector(holding.sector_name) or get_canonical_sector(
            holding.sector_name
        )
        if canon is None:
            continue
        counts[canon.eastmoney_secid] = counts.get(canon.eastmoney_secid, 0) + 1
    return counts


def apply_holdings_overlay(
    items: list[dict[str, Any]],
    holdings: list[Holding],
) -> list[dict[str, Any]]:
    held = _holding_secids(holdings or [])
    overlaid: list[dict[str, Any]] = []
    for item in items:
        count = held.get(str(item.get("secid")), 0)
        overlaid.append(
            {
                **item,
                "held_fund_count": count,
                "in_portfolio": count > 0,
            }
        )
    return overlaid


def build_theme_board_payload(
    items: list[dict[str, Any]],
    *,
    sort: SortMode,
    snapshot_meta: dict[str, Any],
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    overlaid = apply_holdings_overlay(items, holdings or [])
    sorted_items = _sort_theme_items(overlaid, sort=sort)
    ranked = [
        {**_strip_internal_theme_fields(row), "rank": index + 1}
        for index, row in enumerate(sorted_items)
    ]
    return {
        "trade_date": snapshot_meta.get("trade_date"),
        "session_kind": snapshot_meta.get("session_kind"),
        "available": snapshot_meta.get("available", False),
        "from_cache": snapshot_meta.get("from_cache", False),
        "stale": snapshot_meta.get("stale", False),
        "refreshed_at": snapshot_meta.get("refreshed_at"),
        "message": snapshot_meta.get("message"),
        "sort": sort,
        "items": ranked,
    }


def get_theme_board_snapshot(
    *,
    force_refresh: bool = False,
    holdings: list[Holding] | None = None,
    sort: SortMode = "change",
) -> dict[str, Any]:
    """只读缓存 + 持仓叠加；缓存为空或 force_refresh 时同步刷新一次兜底。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_key = f"theme:boards:{_CACHE_VERSION}:{trade_date}"

    cached: dict[str, Any] | None = None
    if not force_refresh:
        # 后台线程负责新鲜度；前台接受任意时段缓存，秒出。
        cached = get_spot_snapshot_any_age(cache_key)

    if cached is None or force_refresh:
        cached = refresh_theme_board_snapshot(trade_date=trade_date)
        from_cache = False
    else:
        from_cache = True

    items = list(cached.get("items") or [])
    available = bool(items)
    snapshot_meta = {
        "trade_date": cached.get("trade_date", trade_date),
        "session_kind": cached.get("session_kind", session_kind),
        "available": available,
        "from_cache": from_cache,
        "stale": False,
        "refreshed_at": cached.get("refreshed_at"),
        "message": None if available else "行情暂不可用，请稍后重试",
    }
    return build_theme_board_payload(
        items,
        sort=sort,
        snapshot_meta=snapshot_meta,
        holdings=holdings,
    )


# ---------------------------------------------------------------------------
# 后台刷新线程
# ---------------------------------------------------------------------------
def _refresh_enabled() -> bool:
    from app.config import get_settings

    return bool(get_settings().theme_board_refresh_enabled)


def theme_board_refresh_loop() -> None:
    """时段感知 daemon 循环：启动预热一次，盘中 15min / 收盘 1h 刷新。"""
    from app.config import get_settings

    try:
        refresh_theme_board_snapshot()
    except Exception as exc:
        logger.info("theme board initial refresh failed: %s", exc)

    while True:
        settings = get_settings()
        session_kind = build_trading_session().get("session_kind", "")
        interval = (
            settings.theme_board_refresh_interval_seconds
            if session_kind in _INTRADAY_SESSIONS
            else settings.theme_board_refresh_idle_interval_seconds
        )
        time.sleep(max(60, int(interval)))
        try:
            refresh_theme_board_snapshot()
        except Exception as exc:
            logger.info("theme board refresh failed: %s", exc)


# ---------------------------------------------------------------------------
# 现货榜兜底（仅日 K 全失败时）
# ---------------------------------------------------------------------------
def _load_theme_spot_changes() -> dict[str, float]:
    """批量现货涨跌幅：优先复用全市场板块缓存，失败再走 AkShare。"""
    changes: dict[str, float] = {}
    try:
        snapshot = get_sector_board_snapshot(force_refresh=False)
        for board_type in ("industry", "concept"):
            for row in snapshot.get(board_type) or []:
                name = str(row.get("name", "")).strip()
                change = row.get("change_percent")
                if name and change is not None:
                    changes[name] = float(change)
    except Exception as exc:
        logger.debug("theme spot from sector snapshot failed: %s", exc)

    if changes:
        return changes

    for board_type in ("industry", "concept"):
        try:
            for row in fetch_akshare_board_records(board_type):
                name = str(row.get("name", "")).strip()
                change = row.get("change_percent")
                if name and change is not None:
                    changes[name] = float(change)
        except Exception as exc:
            logger.debug("theme spot akshare %s failed: %s", board_type, exc)

    try:
        index_board = fetch_boards_via_akshare(include_index=True).get("index") or {}
        for name, change in index_board.items():
            cleaned = str(name).strip()
            if cleaned and change is not None:
                changes[cleaned] = float(change)
    except Exception as exc:
        logger.debug("theme spot index board failed: %s", exc)

    return changes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _strip_internal_theme_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def _sort_theme_items(items: list[dict[str, Any]], *, sort: SortMode) -> list[dict[str, Any]]:
    key_name = "change_1d_percent" if sort == "change" else "consecutive_up_days"

    def sort_key(item: dict[str, Any]) -> tuple[int, float]:
        value = item.get(key_name)
        if value is None:
            return (1, 0.0)
        return (0, float(value))

    return sorted(items, key=sort_key, reverse=True)


def _bars_through_trade_date(series: list[dict], trade_date: str | None) -> list[dict]:
    if not series:
        return []
    if trade_date:
        for index, bar in enumerate(series):
            if str(bar.get("date", ""))[:10] == str(trade_date)[:10]:
                return series[: index + 1]
    return list(series)


def _latest_change_percent(series: list[dict], trade_date: str | None) -> float | None:
    if not series:
        return None
    if trade_date:
        for bar in reversed(series):
            if str(bar.get("date", ""))[:10] == str(trade_date)[:10]:
                return _as_float(bar.get("change_percent"))
    return _as_float(series[-1].get("change_percent"))


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
