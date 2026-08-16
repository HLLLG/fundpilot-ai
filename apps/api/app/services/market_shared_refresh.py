"""全用户共享市场快照的后台刷新。

A 股与美股交易时段独立判定：
- A 股活跃（9:30–15:00 intraday/pre_close）：每 20min 刷新指数与主题板块
- 主题板块：收盘后锁一次收盘价；已对齐有效交易日则周末/盘前不再打源
- 基金涨跌分布：交易日盘中每 15min；收盘后等官方净值每 30min；已对齐有效交易日的官方净值不再打源
- 美股活跃（盘前/盘中/盘后）：每 20min 刷新
- A 股 / 美股指数休市：不再定时打源，沿用收盘缓存，等下一活跃时段再更新
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
import time

from app.config import get_settings
from app.services.trading_session import build_trading_session
from app.services.us_market_session import detect_us_session

logger = logging.getLogger(__name__)

_A_SHARE_LIVE_SESSIONS = frozenset({"trading_day_intraday", "trading_day_pre_close"})
_US_LIVE_SESSIONS = frozenset({"pre_market", "regular", "after_hours"})

# 轮询粒度须小于活跃刷新间隔，否则 20min 配置会被 30min 睡眠拖慢
_POLL_CAP_SECONDS = 60.0
_FUND_DISTRIBUTION_LIVE_INTERVAL_SECONDS = 15 * 60.0
_FUND_DISTRIBUTION_IDLE_INTERVAL_SECONDS = 30 * 60.0
_last_a_share_refresh_at = 0.0
_last_market_breadth_refresh_at = 0.0
_last_fund_return_distribution_refresh_at = 0.0
_last_us_refresh_at = 0.0
_MARKET_BREADTH_LEASE_PATH = Path(tempfile.gettempdir()) / "fundpilot-market-breadth-v2.lease"


def _refresh_enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.theme_board_refresh_enabled
        or settings.market_breadth_enabled
        or settings.fund_return_distribution_refresh_enabled
    )


def live_refresh_interval_seconds() -> float:
    """盘中 / 美股活跃：与 ``theme_board_refresh_interval_seconds`` 对齐（默认 20min）。"""
    return float(max(60, int(get_settings().theme_board_refresh_interval_seconds)))


def idle_refresh_interval_seconds() -> float:
    """休市：与 ``market_shared_idle_interval_seconds`` 对齐（默认 3h）。"""
    settings = get_settings()
    idle = getattr(settings, "market_shared_idle_interval_seconds", None)
    if idle is None:
        idle = settings.theme_board_refresh_idle_interval_seconds
    return float(max(300, int(idle)))


def _live_interval_seconds() -> float:
    return live_refresh_interval_seconds()


def _idle_interval_seconds() -> float:
    return idle_refresh_interval_seconds()


def _poll_seconds() -> float:
    """daemon 睡眠时长：不超过活跃间隔，默认每 60s 检查一次。"""
    breadth_interval = max(
        60,
        int(get_settings().market_breadth_live_refresh_interval_seconds),
    )
    return min(_POLL_CAP_SECONDS, _live_interval_seconds(), float(breadth_interval))


def _theme_board_is_settled() -> bool:
    from app.services.theme_board_snapshot import (
        get_theme_board_snapshot_cache_only,
        theme_board_snapshot_is_settled,
    )

    return theme_board_snapshot_is_settled(
        get_theme_board_snapshot_cache_only(),
        build_trading_session(),
    )


def refresh_a_share_market_snapshots(*, refresh_cn_index: bool | None = None) -> None:
    """刷新 A 股主题板块；宽基指数只在盘中或启动预热时打源。"""
    from app.services.cn_index_overview import get_cn_index_overview
    from app.services.theme_board_snapshot import refresh_theme_board_snapshot

    session_kind = str(build_trading_session().get("session_kind") or "")
    if not _theme_board_is_settled():
        refresh_theme_board_snapshot()
    should_refresh_cn = (
        True
        if refresh_cn_index is True
        else False
        if refresh_cn_index is False
        else session_kind in _A_SHARE_LIVE_SESSIONS
    )
    if should_refresh_cn:
        get_cn_index_overview(force_refresh=True)


def _try_acquire_market_breadth_lease(*, ttl_seconds: float) -> bool:
    """用原子文件创建为同一容器内的多 worker 提供 best-effort 刷新租约。"""
    now = time.time()
    lease_path = _MARKET_BREADTH_LEASE_PATH
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if now - lease_path.stat().st_mtime < ttl_seconds:
                return False
            lease_path.unlink()
        except FileNotFoundError:
            pass
        try:
            descriptor = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
    try:
        os.write(descriptor, str(now).encode("ascii"))
    finally:
        os.close(descriptor)
    return True


def refresh_market_breadth_snapshot() -> None:
    from app.services.market_breadth_signal import (
        build_market_breadth_signal,
        refresh_market_breadth_closing_background,
    )

    settings = get_settings()
    if not settings.market_breadth_enabled:
        return
    lease_ttl = float(
        max(60, int(getattr(settings, "market_breadth_live_refresh_interval_seconds", 300)))
    )
    if not _try_acquire_market_breadth_lease(ttl_seconds=lease_ttl):
        return
    # 租约保证同一刷新窗口只有一个 worker 打外源，因此持租约者可强制刷新，避免缓存 TTL
    # 与租约起点相差数秒后形成“隔一轮才真刷新”的约 10 分钟节奏。
    build_market_breadth_signal(force_refresh=True)
    refresh_market_breadth_closing_background()


def refresh_fund_return_distribution_snapshot() -> None:
    """预热全用户共享基金分布；请求路径只读这里写入的持久缓存。"""
    from app.services.fund_return_distribution import (
        refresh_fund_return_distribution_snapshot as refresh_snapshot,
    )

    refresh_snapshot()


def refresh_us_market_snapshot() -> None:
    from app.services.us_market_service import get_us_market_snapshot

    get_us_market_snapshot(force_refresh=True)


def run_startup_market_refresh() -> None:
    """进程启动时同步刷新共享快照，覆盖 SQLite / 内存中的跨进程遗留缓存。"""
    global _last_a_share_refresh_at
    global _last_fund_return_distribution_refresh_at
    global _last_market_breadth_refresh_at
    global _last_us_refresh_at

    now = time.monotonic()
    # 先预热用户可见的基金分布；其余市场任务较慢时也不让首位访问者承担聚合等待。
    if get_settings().fund_return_distribution_refresh_enabled:
        refresh_fund_return_distribution_snapshot()
        _last_fund_return_distribution_refresh_at = now
    if get_settings().theme_board_refresh_enabled:
        refresh_a_share_market_snapshots(refresh_cn_index=True)
        _last_a_share_refresh_at = now
    refresh_market_breadth_snapshot()
    _last_market_breadth_refresh_at = now
    if get_settings().theme_board_refresh_enabled:
        refresh_us_market_snapshot()
        _last_us_refresh_at = now
    logger.info("market shared startup refresh completed")


def _maybe_refresh_a_share(now: float) -> None:
    global _last_a_share_refresh_at
    if not get_settings().theme_board_refresh_enabled:
        return
    session_kind = build_trading_session().get("session_kind", "")
    if session_kind in _A_SHARE_LIVE_SESSIONS:
        interval = _live_interval_seconds()
    elif _theme_board_is_settled():
        return
    else:
        interval = 0.0
    if now - _last_a_share_refresh_at < interval:
        return
    refresh_a_share_market_snapshots()
    _last_a_share_refresh_at = now
    logger.debug(
        "market shared a-share refresh done session=%s interval=%ss",
        session_kind,
        int(interval),
    )


def _maybe_refresh_market_breadth(now: float) -> None:
    global _last_market_breadth_refresh_at
    settings = get_settings()
    if not settings.market_breadth_enabled:
        return
    session_kind = str(build_trading_session().get("session_kind") or "")
    interval = (
        float(max(60, int(settings.market_breadth_live_refresh_interval_seconds)))
        if session_kind in _A_SHARE_LIVE_SESSIONS
        else _idle_interval_seconds()
    )
    if now - _last_market_breadth_refresh_at < interval:
        return
    refresh_market_breadth_snapshot()
    _last_market_breadth_refresh_at = now
    logger.debug(
        "market shared breadth refresh done session=%s interval=%ss",
        session_kind,
        int(interval),
    )


def _is_fund_distribution_live_session(session: dict) -> bool:
    phase = str(session.get("market_phase") or "")
    return bool(
        session.get("is_trading_day")
        and session.get("effective_trade_date") == session.get("calendar_date")
        and str(session.get("session_kind") or "") != "trading_day_pre_open"
        and phase in {"continuous", "lunch_break"}
    )


def fund_distribution_refresh_interval_seconds(session: dict) -> float:
    """交易日盘中 15 分钟；收盘后等官方净值 30 分钟；非交易日不再按闲时刷同一份收盘数据。"""
    if _is_fund_distribution_live_session(session):
        return _FUND_DISTRIBUTION_LIVE_INTERVAL_SECONDS
    return _FUND_DISTRIBUTION_IDLE_INTERVAL_SECONDS


def _maybe_refresh_fund_return_distribution(now: float) -> None:
    global _last_fund_return_distribution_refresh_at
    if not get_settings().fund_return_distribution_refresh_enabled:
        return
    from app.services.fund_return_distribution import (
        build_fund_return_distribution,
        fund_return_distribution_is_settled,
    )

    session = build_trading_session()
    cached = build_fund_return_distribution(force_refresh=False)
    if fund_return_distribution_is_settled(cached, session):
        return
    interval = fund_distribution_refresh_interval_seconds(session)
    if now - _last_fund_return_distribution_refresh_at < interval:
        return
    refresh_fund_return_distribution_snapshot()
    _last_fund_return_distribution_refresh_at = now
    logger.debug(
        "market shared fund distribution refresh done phase=%s interval=%ss",
        session.get("market_phase"),
        int(interval),
    )


def _maybe_refresh_us(now: float) -> None:
    global _last_us_refresh_at
    if not get_settings().theme_board_refresh_enabled:
        return
    session_kind = detect_us_session().get("session_kind", "")
    if session_kind not in _US_LIVE_SESSIONS:
        return
    interval = _live_interval_seconds()
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
            _maybe_refresh_market_breadth(now)
        except Exception as exc:
            logger.info("market shared breadth refresh failed: %s", exc)
        try:
            _maybe_refresh_fund_return_distribution(now)
        except Exception as exc:
            logger.info("market shared fund distribution refresh failed: %s", exc)
        try:
            _maybe_refresh_us(now)
        except Exception as exc:
            logger.info("market shared us refresh failed: %s", exc)
        try:
            from app.services.sector_quote_cache import maybe_prune_durable_caches

            maybe_prune_durable_caches()
        except Exception as exc:
            logger.info("durable cache prune failed: %s", exc)
