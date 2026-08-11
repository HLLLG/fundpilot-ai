from __future__ import annotations

from datetime import datetime, timezone

from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.sector_registry import list_theme_board_labels
from app.services.theme_board_snapshot import get_theme_board_snapshot
from app.services.trading_session import build_trading_session

_HEAT_LIVE_TTL_SECONDS = 60.0
_HEAT_CLOSED_TTL_SECONDS = 3600.0
_SECTOR_HEAT_CACHE_VERSION = "v3"
_LIVE_SESSION_KINDS = frozenset({"trading_day_intraday", "trading_day_pre_close"})
# 盘中主题快照允许的最大滞后。后台 daemon 默认 1200s 刷一次，而扫描侧读的是 any-age
# 缓存，所以扫描拿到的板块涨跌可以比用户点击时刻旧 20 分钟以上（daemon 未起来时更久）。
# 尾盘扫描据此给出的"今日涨跌估算"会停在早盘的数，故这里自行卡一道新鲜度阈值。
_HEAT_LIVE_SNAPSHOT_MAX_AGE_SECONDS = 180.0

# 荐基 UI 展示用别名 → 市场主题板块标签（共享 theme board 快照涨跌）
_UI_HEAT_LABEL_ALIASES: dict[str, str] = {
    "国防军工": "军工",
}


def _sector_heat_cache_key(trade_date: str | None) -> str:
    return f"discovery:sector_heat:{_SECTOR_HEAT_CACHE_VERSION}:{trade_date}"


def _fallback_theme_sector_heat_rows() -> list[dict]:
    """主题快照不可用时仍返回完整标签列表（无涨跌）。"""
    return [
        {
            "sector_label": label,
            "change_1d_percent": None,
            "change_5d_percent": None,
            "heat_score": None,
            "rising_count": None,
            "falling_count": None,
            "flat_count": None,
            "advancing_ratio_percent": None,
        }
        for label in list_theme_board_labels()
    ]


def build_sector_heat_ranking(
    *,
    force_refresh: bool = False,
    decision_at: datetime | None = None,
) -> list[dict]:
    """扫描 pipeline 板块热度，复用主题板块共享快照。"""
    session = build_trading_session(decision_at)
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_ttl = (
        _HEAT_LIVE_TTL_SECONDS
        if session_kind in {"trading_day_intraday", "trading_day_pre_close"}
        else _HEAT_CLOSED_TTL_SECONDS
    )
    cache_key = _sector_heat_cache_key(trade_date)

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached and cached.get("sectors"):
            return list(cached["sectors"])

    rows, change_as_of = _rows_from_theme_board_snapshot(
        live=session_kind in _LIVE_SESSION_KINDS
    )
    merged = list(rows) if rows else _fallback_theme_sector_heat_rows()

    merged = _append_alias_heat_rows(merged)
    merged = _sort_sector_heat_rows(merged)
    if change_as_of:
        merged = [{**row, "change_as_of": change_as_of} for row in merged]

    if merged and any(row.get("change_1d_percent") is not None for row in merged):
        save_spot_snapshot(
            cache_key,
            {
                "sectors": merged,
                "trade_date": trade_date,
                "session_kind": session_kind,
                "change_as_of": change_as_of,
            },
        )
    return merged


def build_sector_heat_ranking_for_ui() -> list[dict]:
    """推荐基金 Tab 关注方向：复用市场主题板块快照（白名单标签 + 当日涨跌），秒级返回。"""
    rows, _change_as_of = _rows_from_theme_board_snapshot(live=False)
    if rows:
        return _append_alias_heat_rows(rows)
    return _fallback_theme_sector_heat_rows()


def _snapshot_age_seconds(refreshed_at: object) -> float | None:
    text = str(refreshed_at or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _rows_from_theme_board_snapshot(*, live: bool) -> tuple[list[dict], str | None]:
    """从 theme board 共享缓存构建热度行，与 /api/market/theme-boards 口径一致。

    返回 ``(rows, change_as_of)``。``change_as_of`` 是这批涨跌幅的实际截止时刻，供上层
    标注口径——扫描报告里的"今日涨跌估算"此前是个没有截止时间的裸数字，用户在尾盘看到
    的其实可能是早盘的值。
    """
    try:
        snapshot = get_theme_board_snapshot(force_refresh=False, sort="change")
        if live:
            age = _snapshot_age_seconds(
                snapshot.get("refreshed_at") if isinstance(snapshot, dict) else None
            )
            if age is None or age > _HEAT_LIVE_SNAPSHOT_MAX_AGE_SECONDS:
                # 盘中滞后超阈值就自己刷一次，别把后台 daemon 的 20 分钟节奏当成
                # 决策数据的新鲜度上限。
                snapshot = get_theme_board_snapshot(force_refresh=True, sort="change")
    except Exception:
        return [], None
    theme_items = snapshot.get("items") if isinstance(snapshot, dict) else None
    change_as_of = (
        str(snapshot.get("refreshed_at") or "").strip() or None
        if isinstance(snapshot, dict)
        else None
    )
    if not isinstance(theme_items, list):
        return [], change_as_of

    by_label: dict[str, dict] = {}
    for item in theme_items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "").strip()
        if not label:
            continue
        change_1d = _as_float(item.get("change_1d_percent"))
        change_5d = _as_float(item.get("change_5d_percent"))
        by_label[label] = {
            "sector_label": label,
            "change_1d_percent": change_1d,
            "change_5d_percent": change_5d,
            "heat_score": _heat_score(change_1d, change_5d),
            "rising_count": _as_float(item.get("rising_count")),
            "falling_count": _as_float(item.get("falling_count")),
            "flat_count": _as_float(item.get("flat_count")),
            "advancing_ratio_percent": _as_float(item.get("advancing_ratio_percent")),
        }

    if not by_label:
        return [], change_as_of

    merged: list[dict] = []
    for label in list_theme_board_labels():
        merged.append(
            by_label.get(label)
            or {
                "sector_label": label,
                "change_1d_percent": None,
                "change_5d_percent": None,
                "heat_score": None,
                "rising_count": None,
                "falling_count": None,
                "flat_count": None,
                "advancing_ratio_percent": None,
            }
        )
    return _sort_sector_heat_rows(merged), change_as_of


def _append_alias_heat_rows(rows: list[dict]) -> list[dict]:
    """为 discovery 旧标签名补充一行（如 国防军工 ← 军工），便于历史 focus 与热度对齐。"""
    by_label = {str(row.get("sector_label") or ""): row for row in rows}
    extra: list[dict] = []
    for alias, target in _UI_HEAT_LABEL_ALIASES.items():
        if alias in by_label:
            continue
        source = by_label.get(target)
        if source is None:
            continue
        extra.append({**source, "sector_label": alias})
    return rows + extra


def _sort_sector_heat_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda item: (
            item["heat_score"] if item["heat_score"] is not None else -999,
            item["change_1d_percent"] if item["change_1d_percent"] is not None else -999,
        ),
        reverse=True,
    )


def _heat_score(change_1d: float | None, change_5d: float | None) -> float | None:
    if change_1d is None and change_5d is None:
        return None
    one_day = change_1d if change_1d is not None else change_5d or 0.0
    five_day = change_5d if change_5d is not None else one_day
    return round(one_day * 0.6 + five_day * 0.4, 2)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
