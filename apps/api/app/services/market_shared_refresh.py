"""全用户共享市场快照的后台刷新（A 股主题/大跌雷达 + 美股概览）。

A 股与美股交易时段独立判定：
- A 股活跃（9:30–15:00 intraday/pre_close）：每 20min 刷新
- 美股活跃（盘前/盘中/盘后）：每 20min 刷新
- 各自非活跃时段：每 3h 静默刷新一次（沿用 stale 缓存，避免用户请求打源）
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.services.trading_session import build_trading_session
from app.services.us_market_session import detect_us_session

logger = logging.getLogger(__name__)

_A_SHARE_LIVE_SESSIONS = frozenset({"trading_day_intraday", "trading_day_pre_close"})
_US_LIVE_SESSIONS = frozenset({"pre_market", "regular", "after_hours"})

# 轮询粒度须小于活跃刷新间隔，否则 20min 配置会被 30min 睡眠拖慢
_POLL_CAP_SECONDS = 60.0
_last_a_share_refresh_at = 0.0
_last_us_refresh_at = 0.0


def _refresh_enabled() -> bool:
    return bool(get_settings().theme_board_refresh_enabled)


def _live_interval_seconds() -> float:
    return float(max(60, int(get_settings().theme_board_refresh_interval_seconds)))


def _idle_interval_seconds() -> float:
    settings = get_settings()
    idle = getattr(settings, "market_shared_idle_interval_seconds", None)
    if idle is None:
        idle = settings.theme_board_refresh_idle_interval_seconds
    return float(max(300, int(idle)))


def _poll_seconds() -> float:
    """daemon 睡眠时长：不超过活跃间隔，默认每 60s 检查一次。"""
    return min(_POLL_CAP_SECONDS, _live_interval_seconds())


def refresh_a_share_market_snapshots() -> None:
    """主题板块 + 大跌雷达（3/5 日）。"""
    from app.services.dip_radar_snapshot import refresh_dip_radar_snapshots
    from app.services.theme_board_snapshot import refresh_theme_board_snapshot

    refresh_theme_board_snapshot()
    refresh_dip_radar_snapshots()


def refresh_us_market_snapshot() -> None:
    from app.services.us_market_service import get_us_market_snapshot

    get_us_market_snapshot(force_refresh=True)


def run_startup_market_refresh() -> None:
    """进程启动时同步刷新共享快照，覆盖 SQLite / 内存中的跨进程遗留缓存。"""
    global _last_a_share_refresh_at, _last_us_refresh_at

    now = time.monotonic()
    refresh_a_share_market_snapshots()
    _last_a_share_refresh_at = now
    refresh_us_market_snapshot()
    _last_us_refresh_at = now
    logger.info("market shared startup refresh completed")


def _maybe_refresh_a_share(now: float) -> None:
    global _last_a_share_refresh_at
    session_kind = build_trading_session().get("session_kind", "")
    interval = (
        _live_interval_seconds()
        if session_kind in _A_SHARE_LIVE_SESSIONS
        else _idle_interval_seconds()
    )
    if now - _last_a_share_refresh_at < interval:
        return
    refresh_a_share_market_snapshots()
    _last_a_share_refresh_at = now
    logger.debug(
        "market shared a-share refresh done session=%s interval=%ss",
        session_kind,
        int(interval),
    )


def _maybe_refresh_us(now: float) -> None:
    global _last_us_refresh_at
    session_kind = detect_us_session().get("session_kind", "")
    interval = (
        _live_interval_seconds()
        if session_kind in _US_LIVE_SESSIONS
        else _idle_interval_seconds()
    )
    if now - _last_us_refresh_at < interval:
        return
    refresh_us_market_snapshot()
    _last_us_refresh_at = now
    logger.debug(
        "market shared us refresh done session=%s interval=%ss",
        session_kind,
        int(interval),
    )


def market_shared_refresh_loop() -> None:
    """daemon：周期性刷新（启动同步刷新由 lifespan 调用 run_startup_market_refresh）。"""
    while True:
        time.sleep(_poll_seconds())
        now = time.monotonic()
        try:
            _maybe_refresh_a_share(now)
        except Exception as exc:
            logger.info("market shared a-share refresh failed: %s", exc)
        try:
            _maybe_refresh_us(now)
        except Exception as exc:
            logger.info("market shared us refresh failed: %s", exc)
