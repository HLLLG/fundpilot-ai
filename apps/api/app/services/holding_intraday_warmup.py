from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.models import Holding
from app.services.fund_profile import FundProfileService
from app.services.holding_detail_service import HoldingDetailDataContext
from app.services.portfolio_profit_analysis import _resolve_intraday_for_holding
from app.services.sector_intraday_provider import fetch_sector_intraday

logger = logging.getLogger(__name__)


def collect_unique_fund_codes(holdings: list[Holding]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for holding in holdings:
        code = str(holding.fund_code or "").strip()
        if not code or code == "000000" or code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def collect_intraday_queries(
    holdings: list[Holding],
    *,
    data_context: HoldingDetailDataContext | None = None,
) -> list[tuple[str, str]]:
    """去重后的 (source_type, source_name) 列表，供后台预热板块分时。"""
    eligible = [
        holding
        for holding in holdings
        if holding.fund_code and holding.fund_code != "000000"
    ]
    if not eligible:
        return []

    context = data_context or HoldingDetailDataContext()
    context.preload_profiles()
    profile_service = FundProfileService()
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for holding in eligible:
        profile = context.find_profile(holding, profile_service)
        query = _resolve_intraday_for_holding(holding, profile)
        if query is None:
            continue
        if query in seen:
            continue
        seen.add(query)
        ordered.append(query)
    return ordered


def warm_holdings_intraday(
    holdings: list[Holding],
    *,
    user_key: str = "global",
    data_context: HoldingDetailDataContext | None = None,
    force_refresh: bool = False,
) -> int:
    """Best-effort 预热持仓关联板块分时（走服务端全局 intraday 缓存）。"""
    if not get_settings().sector_quotes_enabled:
        return 0
    if not holdings:
        return 0

    try:
        queries = collect_intraday_queries(holdings, data_context=data_context)
    except Exception:  # noqa: BLE001
        logger.debug("intraday profile preload failed", exc_info=True)
        return 0
    if not queries:
        return 0

    warmed = 0
    for source_type, source_name in queries:
        try:
            points, *_rest = fetch_sector_intraday(
                source_type,
                source_name,
                force_refresh=force_refresh,
            )
            if points:
                warmed += 1
        except Exception:  # noqa: BLE001 — 预热失败不阻塞主流程
            logger.debug(
                "intraday warmup failed for %s:%s",
                source_type,
                source_name,
                exc_info=True,
            )
        time.sleep(0.15)
    logger.debug("intraday warmup user=%s queries=%d warmed=%d", user_key, len(queries), warmed)
    return warmed
