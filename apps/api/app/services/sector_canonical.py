from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.services.eastmoney_spot_client import (
    fetch_eastmoney_quote_by_secid,
)
from app.services.eastmoney_trends_client import (
    fetch_eastmoney_kline_close_percent,
    is_plausible_daily_change,
)
from app.services.trading_session import build_trading_session
from app.services.sector_labels import normalize_sector_label
from app.services.sector_registry import (
    get_sector_entry,
    list_discovery_sector_labels as _registry_discovery_labels,
)
from app.services.sector_quote_identity import (
    provider_identity_matches,
    requires_provider_identity_check,
)

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]

"""养基宝常见「关联板块」→ 东财标准板块（名称 + secid）。

板块涨跌与分时图统一走东财 push2delay K 线 / trends2（收盘相对昨收），不再依赖 AkShare/批量板块表/天天基金估值。
"""


@dataclass(frozen=True)
class CanonicalSector:
    label: str
    source_type: str  # concept | industry | index
    source_name: str
    eastmoney_secid: str
    source_code: str | None = None


def list_canonical_sector_labels() -> list[str]:
    """返回 registry 与兼容别名覆盖的板块/指数标签。"""
    from app.services.sector_registry_data import (
        CANONICAL_SECTORS,
        THEME_BOARD_INDEX,
    )

    return sorted(set(CANONICAL_SECTORS) | set(THEME_BOARD_INDEX))


def list_discovery_sector_labels() -> list[str]:
    return _registry_discovery_labels()


def get_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None

    # 行情身份只认 sector_registry：它会让 THEME_BOARD_INDEX（主题指数）优先于
    # CANONICAL_SECTORS（概念/行业兜底），避免同一标签存在多套冲突 secid。
    return _canonical_from_registry(label)


def _canonical_from_registry(label: str) -> CanonicalSector | None:
    entry = get_sector_entry(label)
    if entry is None or entry.market_quote is None:
        return None
    quote = entry.market_quote
    return CanonicalSector(
        label=entry.label,
        source_type=quote.source_type,
        source_name=quote.source_name,
        eastmoney_secid=quote.eastmoney_secid,
        source_code=quote.source_code,
    )


def get_intraday_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    """按统一 registry 解析分时标的；主题指数优先，概念/行业作兜底。"""
    return get_canonical_sector(sector_name)


def labels_need_spot_boards(labels: list[str | None]) -> bool:
    """是否存在无法走 canonical K 线的板块名（才需要拉全量板块表）。"""
    for raw in labels:
        label = normalize_sector_label(raw)
        if label and get_canonical_sector(label) is None:
            return True
    return False


@dataclass(frozen=True)
class CanonicalQuoteResult:
    change_percent: float
    matched_name: str
    source_type: str
    source_code: str | None = None
    message: str | None = None


def get_quote_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    """板块涨跌与分时图使用 registry 中的同一行情标的。"""
    return get_intraday_canonical_sector(sector_name) or get_canonical_sector(sector_name)


def fetch_canonical_sector_quote(
    sector_name: str | None,
    boards: dict[str, SpotBoard],
) -> CanonicalQuoteResult | None:
    """养基宝常见板块：东财 K 线收盘涨跌幅为准（与分时 15:00 一致）。"""
    canon = get_quote_canonical_sector(sector_name)
    if canon is None:
        return None

    verified_spot_change: float | None = None
    if requires_provider_identity_check(canon.label):
        provider_name, verified_spot_change = fetch_eastmoney_quote_by_secid(
            canon.eastmoney_secid,
            timeout=5.0,
            max_retries=1,
        )
        if not provider_identity_matches(
            canon.label,
            expected_source_code=canon.source_code,
            actual_security_name=provider_name,
            actual_security_code=canon.source_code,
        ):
            logger.error(
                "canonical sector identity mismatch label=%s secid=%s provider_name=%s",
                canon.label,
                canon.eastmoney_secid,
                provider_name,
            )
            return None

    trade_date = build_trading_session().get("effective_trade_date")
    kline_change = fetch_eastmoney_kline_close_percent(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        trade_date=trade_date,
    )
    if kline_change is not None and not is_plausible_daily_change(kline_change):
        logger.info(
            "canonical sector %s (%s) kline change %.4f out of range, ignored",
            canon.label,
            canon.eastmoney_secid,
            kline_change,
        )
        kline_change = None
    if kline_change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = kline_change
        return CanonicalQuoteResult(
            change_percent=kline_change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财K线收盘 {canon.eastmoney_secid}",
        )

    board = boards.get(canon.source_type) or {}
    if canon.source_name in board:
        return CanonicalQuoteResult(
            change_percent=board[canon.source_name],
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财K线缓存 {canon.source_name}",
        )

    change = verified_spot_change
    if change is None:
        _name, change = fetch_eastmoney_quote_by_secid(canon.eastmoney_secid)
    if change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = change
        return CanonicalQuoteResult(
            change_percent=change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财快照 {canon.eastmoney_secid}",
        )

    logger.info("canonical sector %s (%s) kline quote miss", canon.label, canon.eastmoney_secid)
    return None


def prefetch_canonical_kline_quotes(
    labels: list[str | None],
    boards: dict[str, SpotBoard],
    *,
    timeout_seconds: float | None = None,
) -> int:
    """并发拉取 canonical 板块东财 K 线收盘涨跌，写入 boards。"""
    unique_labels: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = normalize_sector_label(raw)
        if not label or label in seen or get_canonical_sector(label) is None:
            continue
        seen.add(label)
        unique_labels.append(label)
    if not unique_labels:
        return 0

    per_call_timeout = 12.0 if timeout_seconds is None else max(1.0, min(8.0, timeout_seconds * 0.45))
    max_workers = min(6, len(unique_labels))
    matched = 0
    trade_date = build_trading_session().get("effective_trade_date")

    def fetch_one(label: str) -> int:
        canon = get_quote_canonical_sector(label)
        if canon is None:
            return 0
        if requires_provider_identity_check(canon.label):
            provider_name, _spot_change = fetch_eastmoney_quote_by_secid(
                canon.eastmoney_secid,
                timeout=min(per_call_timeout, 5.0),
                max_retries=1,
            )
            if not provider_identity_matches(
                canon.label,
                expected_source_code=canon.source_code,
                actual_security_name=provider_name,
                actual_security_code=canon.source_code,
            ):
                logger.error(
                    "canonical prefetch identity mismatch label=%s secid=%s provider_name=%s",
                    canon.label,
                    canon.eastmoney_secid,
                    provider_name,
                )
                return 0
        change = fetch_eastmoney_kline_close_percent(
            canon.eastmoney_secid,
            source_code=canon.source_code,
            trade_date=trade_date,
            timeout=per_call_timeout,
            max_retries=1,
        )
        if change is not None and not is_plausible_daily_change(change):
            return 0
        if change is not None:
            boards.setdefault(canon.source_type, {})[canon.source_name] = change
            return 1
        return 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_one, label) for label in unique_labels]
        for future in as_completed(futures):
            try:
                matched += int(future.result())
            except Exception as exc:
                logger.info("prefetch canonical kline worker failed: %s", exc)
    return matched
