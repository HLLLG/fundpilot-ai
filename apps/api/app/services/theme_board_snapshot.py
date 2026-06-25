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
from app.services.eastmoney_trends_client import fetch_eastmoney_kline_close_percent
from app.services.sector_canonical import (
    CanonicalSector,
    get_canonical_sector,
    get_quote_canonical_sector,
)
from app.services.sector_registry import list_theme_board_labels, resolve_market_quote
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

SortMode = Literal["change", "streak", "inflow"]
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
    "PCB", "机器人", "医药", "算力租赁", "软件", "医疗", "电网设备", "传媒", "脑机接口",
    "可控核聚变", "商业航天", "体育", "AI医疗", "低空经济", "军工", "动漫游戏", "固态电池",
    "钢铁", "有色金属", "黄金", "机械设备", "储能", "锂电池", "家电", "国企改革", "中药",
    "汽车", "房地产", "新能源车", "光伏", "新能源", "金融科技", "环保", "畜牧养殖",
    "农业", "基建", "交通运输", "红利", "食品饮料", "贵金属", "化工", "银行", "锂矿",
    "白酒", "黄金股", "建材", "证券", "煤炭", "电力", "证券保险", "保险",
)

# 指数主题涨跌幅用 _THEME_BOARD_INDEX；资金流用 _resolve_flow_source_code 解析到的东财 BK。
# 个别自动解析不准时在此覆盖（label → BKxxxx）。
# 个别指数主题在东财 spot 榜未命中时，显式指定 BK 资金流代码。
_THEME_BOARD_FLOW: dict[str, str] = {
    "医药": "BK0465",
    "贵金属": "BK0732",      # 工业金属/贵金属行业
    "化工": "BK1206",        # 基础化工
    "交通运输": "BK1210",    # 交通运输行业
}
_THEME_BOARD_ALIAS: dict[str, tuple[str, str, str]] = {
    "软件": ("90.BK0737", "BK0737", "industry"),        # 软件开发
    "医疗": ("90.BK0727", "BK0727", "industry"),        # 医疗服务
    "AI医疗": ("90.BK1170", "BK1170", "concept"),        # AI制药（医疗）
    "家电": ("90.BK0456", "BK0456", "industry"),        # 家用电器
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

# 小倍「今日板块涨幅榜」口径：优先中证/国证主题指数（2.xxx）；少数仍用东财 BK 行业。
# 经 2026-06-18 小倍全榜截图与 push2delay trends2 逐条比对后固化。
_THEME_BOARD_INDEX: dict[str, tuple[str, str, str]] = {
    "人工智能": ("2.931071", "931071", "index"),
    "半导体": ("2.H30184", "H30184", "index"),
    "5G": ("2.931079", "931079", "index"),
    "消费电子": ("2.931494", "931494", "index"),
    "电子": ("2.930652", "930652", "index"),
    "稀土": ("2.930598", "930598", "index"),
    "创新药": ("2.931152", "931152", "index"),
    "云计算": ("2.930851", "930851", "index"),
    "CPO": ("2.932357", "932357", "index"),
    "MLCC": ("2.930902", "930902", "index"),
    "存储芯片": ("2.H30552", "H30552", "index"),
    "计算机": ("2.930651", "930651", "index"),
    "半导体材料": ("2.931743", "931743", "index"),
    "智能家居": ("2.H50028", "H50028", "index"),
    "PCB": ("2.931837", "931837", "index"),
    "机器人": ("2.931594", "931594", "index"),          # 卫星产业
    "医药": ("2.H30054", "H30054", "index"),
    "软件": ("2.H30202", "H30202", "index"),
    "医疗": ("2.930720", "930720", "index"),
    "传媒": ("2.H30365", "H30365", "index"),
    "信创": ("2.931247", "931247", "index"),
    "体育": ("2.930790", "930790", "index"),
    "电网设备": ("2.931994", "931994", "index"),
    "钢铁": ("2.930606", "930606", "index"),
    "有色金属": ("2.H30015", "H30015", "index"),
    "机械设备": ("2.932078", "932078", "index"),
    "储能": ("2.H30057", "H30057", "index"),
    "锂电池": ("2.932444", "932444", "index"),
    "动漫游戏": ("2.930901", "930901", "index"),
    "汽车": ("2.931008", "931008", "index"),
    "房地产": ("2.931775", "931775", "index"),
    "新能源车": ("2.930997", "930997", "index"),
    "光伏": ("2.931151", "931151", "index"),
    "新能源": ("2.931151", "931151", "index"),
    "金融科技": ("2.930986", "930986", "index"),
    "环保": ("2.930614", "930614", "index"),
    "畜牧养殖": ("2.931946", "931946", "index"),
    "农业": ("2.931581", "931581", "index"),
    "基建": ("2.930608", "930608", "index"),
    "交通运输": ("2.H11043", "H11043", "index"),
    "红利": ("2.H30089", "H30089", "index"),
    "食品饮料": ("2.930653", "930653", "index"),
    "贵金属": ("2.932422", "932422", "index"),
    "化工": ("2.932422", "932422", "index"),
    "银行": ("2.H30022", "H30022", "index"),
    "锂矿": ("2.931454", "931454", "index"),
    "白酒": ("2.930622", "930622", "index"),
    "黄金股": ("2.931238", "931238", "index"),
    "建材": ("2.931009", "931009", "index"),
    "证券": ("2.931412", "931412", "index"),
    "煤炭": ("90.BK0437", "BK0437", "industry"),
    "电力": ("2.H30199", "H30199", "index"),
    "保险": ("2.399809", "399809", "index"),
    "军工": ("2.930749", "930749", "index"),
    "国企改革": ("2.931088", "931088", "index"),
    "中药": ("2.930641", "930641", "index"),
    "家电": ("2.931021", "931021", "index"),
    "可控核聚变": ("2.932000", "932000", "index"),
    "脑机接口": ("2.H11050", "H11050", "index"),
    "AI医疗": ("2.H30531", "H30531", "index"),
}


def _theme_board_whitelist() -> tuple[str, ...]:
    return tuple(list_theme_board_labels())


def _entry_from_quote(
    name: str,
    *,
    secid: str,
    source_code: str | None,
    board_kind: str,
    source_name: str,
    change_for_code,
    canon: CanonicalSector | None = None,
) -> dict[str, Any]:
    return {
        "sector_label": name,
        "secid": secid,
        "source_code": source_code,
        "board_kind": board_kind,
        "change_hint": change_for_code(source_code) if board_kind != "index" else None,
        "_canon": canon
        or CanonicalSector(
            label=name,
            source_type=board_kind,
            source_name=source_name,
            eastmoney_secid=secid,
            source_code=source_code,
        ),
    }


def _resolve_theme_board_entry(
    name: str,
    *,
    change_for_code,
    concept_by_name: dict[str, str],
    industry_by_name: dict[str, str],
) -> dict[str, Any] | None:
    """Registry-first theme board resolution; legacy dicts as transition fallback."""
    quote = resolve_market_quote(name)
    if quote is not None:
        return _entry_from_quote(
            name,
            secid=quote.eastmoney_secid,
            source_code=quote.source_code,
            board_kind=quote.source_type,
            source_name=quote.source_name,
            change_for_code=change_for_code,
        )

    if name in _THEME_BOARD_INDEX:
        secid, code, kind = _THEME_BOARD_INDEX[name]
        return _entry_from_quote(
            name,
            secid=secid,
            source_code=code,
            board_kind=kind,
            source_name=name,
            change_for_code=change_for_code,
        )

    canon = get_quote_canonical_sector(name) or get_canonical_sector(name)
    if canon is not None:
        return {
            "sector_label": name,
            "secid": canon.eastmoney_secid,
            "source_code": canon.source_code,
            "board_kind": _board_kind_from_source_type(canon.source_type),
            "change_hint": change_for_code(canon.source_code),
            "_canon": canon,
        }

    if name in _THEME_BOARD_ALIAS:
        secid, code, kind = _THEME_BOARD_ALIAS[name]
        return _entry_from_quote(
            name,
            secid=secid,
            source_code=code,
            board_kind=kind,
            source_name=name,
            change_for_code=change_for_code,
            canon=None,
        )

    if name in concept_by_name:
        code = concept_by_name[name]
        return {
            "sector_label": name,
            "secid": f"90.{code}",
            "source_code": code,
            "board_kind": "concept",
            "change_hint": change_for_code(code),
            "_canon": None,
        }

    if name in industry_by_name:
        code = industry_by_name[name]
        return {
            "sector_label": name,
            "secid": f"90.{code}",
            "source_code": code,
            "board_kind": "industry",
            "change_hint": change_for_code(code),
            "_canon": None,
        }

    return None


_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
}

# 仅 9:30–15:00 交易时段后台刷新；收盘后/开盘前/非交易日只读缓存
_MARKET_REFRESH_SESSIONS = _INTRADAY_SESSIONS


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


def _resolve_flow_source_code(
    name: str,
    entry: dict[str, Any],
    *,
    concept_by_name: dict[str, str],
    industry_by_name: dict[str, str],
) -> str | None:
    """涨跌幅 secid 与资金流 BK 解耦：指数主题仍返回东财 BK 代码供 clist 查 f62。"""
    if name in _THEME_BOARD_FLOW:
        return _THEME_BOARD_FLOW[name]

    secid = str(entry.get("secid", ""))
    if secid.startswith("90."):
        code = str(entry.get("source_code") or "").strip()
        return code or secid.split(".", 1)[1]

    if name in _THEME_BOARD_ALIAS:
        return _THEME_BOARD_ALIAS[name][1]

    canon = get_canonical_sector(name)
    if canon is not None and str(canon.eastmoney_secid).startswith("90."):
        code = str(canon.source_code or "").strip()
        if code:
            return code

    if name in concept_by_name:
        return concept_by_name[name]
    if name in industry_by_name:
        return industry_by_name[name]
    return None


def list_theme_board_universe() -> list[dict[str, Any]]:
    """对标小倍的固定粗粒度板块白名单，解析到东财 secid。

    解析优先级（每个白名单名）：``sector_registry.resolve_market_quote`` →
    legacy ``_THEME_BOARD_INDEX`` → canonical → 别名 → 东财概念/行业**精确名**匹配 → 跳过。
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
    seen_labels: set[str] = set()

    for name in _theme_board_whitelist():
        entry = _resolve_theme_board_entry(
            name,
            change_for_code=change_for_code,
            concept_by_name=concept_by_name,
            industry_by_name=industry_by_name,
        )
        if entry is None:
            logger.info("theme board whitelist name unresolved: %s", name)
            continue

        if entry["sector_label"] in seen_labels:
            continue
        seen_labels.add(entry["sector_label"])
        flow_code = _resolve_flow_source_code(
            name,
            entry,
            concept_by_name=concept_by_name,
            industry_by_name=industry_by_name,
        )
        entry["flow_source_code"] = flow_code
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
        change = _as_float(
            fetch_eastmoney_kline_close_percent(
                secid,
                source_code=entry.get("source_code"),
                trade_date=resolved_date,
                timeout=_SERIES_TIMEOUT,
            )
        )
        if change is None:
            change = entry.get("change_hint")
        return {
            "sector_label": entry["sector_label"],
            "board_kind": entry["board_kind"],
            "secid": secid,
            "source_code": entry.get("source_code"),
            "flow_source_code": entry.get("flow_source_code"),
            "change_1d_percent": change,
        }

    def base_row(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "sector_label": entry["sector_label"],
            "board_kind": entry["board_kind"],
            "secid": entry["secid"],
            "source_code": entry.get("source_code"),
            "flow_source_code": entry.get("flow_source_code"),
            "change_1d_percent": entry.get("change_hint"),
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
            # 指数主题已绑定 secid，禁止按展示名回退到同名概念板块（如 人工智能→BK0800）
            if item.get("board_kind") == "index":
                continue
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

    flow_codes = [
        str(item.get("flow_source_code") or "").strip()
        for item in items
        if item.get("flow_source_code")
    ]
    if flow_codes:
        try:
            import threading

            from app.services.board_fund_flow_history import prefetch_board_flow_histories

            threading.Thread(
                target=prefetch_board_flow_histories,
                args=(flow_codes,),
                kwargs={"max_workers": 1},
                daemon=True,
            ).start()
        except Exception as exc:
            logger.debug("board flow prefetch schedule failed: %s", exc)

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


def _load_board_flow_by_code() -> dict[str, dict[str, Any]]:
    """从全市场板块快照按 BK 代码索引资金流（与 sector_board_snapshot 同源）。"""
    by_code: dict[str, dict[str, Any]] = {}
    try:
        snapshot = get_sector_board_snapshot(force_refresh=False)
        for board_type in ("industry", "concept"):
            for row in snapshot.get(board_type) or []:
                code = str(row.get("code", "")).strip()
                if code:
                    by_code[code] = row
    except Exception as exc:
        logger.debug("theme board flow lookup failed: %s", exc)
    return by_code


def _flow_fields_from_board_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"main_force_net_yi": None, "flow_tiers": None}
    tiers = {
        "super_large_net_yi": row.get("super_large_net_yi"),
        "large_net_yi": row.get("large_net_yi"),
        "medium_net_yi": row.get("medium_net_yi"),
        "small_net_yi": row.get("small_net_yi"),
    }
    main_force = row.get("main_force_net_yi")
    has_any = main_force is not None or any(value is not None for value in tiers.values())
    return {
        "main_force_net_yi": main_force,
        "flow_tiers": tiers if has_any else None,
    }


def _resolve_theme_source_code(item: dict[str, Any]) -> str | None:
    code = str(item.get("source_code") or "").strip()
    if code:
        return code
    secid = str(item.get("secid") or "")
    if secid.startswith("90."):
        return secid.split(".", 1)[1]
    return None


def _resolve_theme_flow_code(item: dict[str, Any]) -> str | None:
    code = str(item.get("flow_source_code") or "").strip()
    if code:
        return code
    return _resolve_theme_source_code(item)


def apply_flow_to_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并东财 BK 主力/四档净流入；涨跌幅可走指数，资金流走 flow_source_code。"""
    by_code = _load_board_flow_by_code()
    enriched: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        code = _resolve_theme_flow_code(row)
        row.update(_flow_fields_from_board_row(by_code.get(code) if code else None))
        enriched.append(row)
    return enriched


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
    with_flow = apply_flow_to_items(items)
    overlaid = apply_holdings_overlay(with_flow, holdings or [])
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


def refresh_market_shared_snapshots(*, trade_date: str | None = None) -> dict[str, Any]:
    """刷新所有用户共享的市场快照：主题板块榜 + 全市场板块资金流。"""
    from app.services.sector_board_snapshot import refresh_sector_board_snapshot

    theme = refresh_theme_board_snapshot(trade_date=trade_date)
    try:
        refresh_sector_board_snapshot()
    except Exception as exc:
        logger.info("sector board shared refresh failed: %s", exc)
    return theme


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
        if force_refresh:
            cached = refresh_market_shared_snapshots(trade_date=trade_date)
        else:
            # 冷启动兜底：仅无缓存时同步拉一次，避免首用户长时间等待
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
    """兼容旧名：统一走 ``market_shared_refresh_loop``。"""
    from app.services.market_shared_refresh import market_shared_refresh_loop

    market_shared_refresh_loop()


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
    if sort == "inflow":
        key_name = "main_force_net_yi"
    elif sort == "streak":
        key_name = "consecutive_up_days"
    else:
        key_name = "change_1d_percent"

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
