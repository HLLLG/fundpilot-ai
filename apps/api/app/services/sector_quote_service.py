from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.database import get_sector_mapping, save_sector_mapping
from app.models import Holding, HoldingFieldWarning, SectorMappingCandidate, SectorQuoteMeta
from app.services.fund_primary_sector_service import (
    PrimarySectorBatchContext,
    associated_sector_is_page_visible,
    is_research_associated_sector_source,
    is_unthemed_allocation_fund,
    primary_sector_fields_for_holding,
)
from app.services.fund_profile import (
    FundProfileService,
    _is_valid_sector_label,
    match_profiles_to_holdings,
)
from app.services.sector_canonical import (
    get_canonical_sector,
    labels_need_spot_boards,
    prefetch_canonical_kline_quotes,
)
from app.services.sector_labels import normalize_sector_label
from app.services.sector_labels import sector_label_key
from app.services.sector_on_demand import fetch_sector_on_demand
from app.services.sector_quote_label import sector_display_label, sector_quote_lookup_label
from app.services.sector_quote_provider import SpotBoardFetchResult, fetch_spot_boards, fetch_spot_boards_result, load_spot_boards_from_cache_only
from app.services.sector_quote_resolver import (
    SectorResolveResult,
    mapping_record_from_result,
    resolve_sector_quote,
)
from app.services.trading_session import (
    build_trading_session,
    get_effective_trade_date,
    session_blocks_official_nav,
)
from app.services.fund_nav_service import get_official_nav_return
from app.services.holding_estimates import (
    _amount_includes_today_return,
    compute_daily_profit_from_rate,
    release_stale_official_nav_to_sector,
)
from app.services.eastmoney_trends_client import is_plausible_daily_change


def refresh_holdings_sector_quotes(
    holdings: list[Holding],
    *,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
    cache_only: bool = False,
) -> dict:
    settings = get_settings()
    session = build_trading_session()
    session_kind = str(session.get("session_kind") or "")
    effective_trade_date = str(
        session.get("effective_trade_date") or get_effective_trade_date()
    )
    is_trading_hours = session_kind == "trading_day_intraday"
    intraday_blocks_official_nav = session_blocks_official_nav(session_kind)
    fetched_at = datetime.now(timezone.utc)

    if not settings.sector_quotes_enabled:
        return {
            "ok": False,
            "message": "板块实时行情已关闭",
            "holdings": [holding.model_dump() for holding in holdings],
            "items": [],
            "summary": {
                "matched": 0,
                "unresolved": len(holdings),
                "needs_mapping": 0,
                "estimate_fallback": 0,
                "board_matched": 0,
                "secid_matched": 0,
            },
            "session": session,
        }

    profile_service = FundProfileService()
    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    fetch_missing_benchmark = not cache_only
    # 精确刷新全量穿透；快刷只补「还没有关联板块」的主动基金，对齐养基宝自动建档。
    missing_sector = any(
        holding.fund_code not in {"", "000000"}
        and not _is_valid_sector_label(holding.sector_name)
        for holding in holdings
    )
    accurate = timeout_seconds is None
    fetch_holdings_infer = not cache_only and (accurate or missing_sector)
    infer_missing_only = fetch_holdings_infer and not accurate
    profiles_snapshot = profile_service.list_profiles()
    initial_profiles = match_profiles_to_holdings(holdings, profiles_snapshot)
    active_profile_codes = {
        profile.fund_code
        for profile in initial_profiles
        if profile is not None and profile.fund_code != "000000"
    }
    batch_context = PrimarySectorBatchContext.load(
        {
            *(holding.fund_code for holding in holdings),
            *active_profile_codes,
        },
        profiles=profiles_snapshot,
    )
    holdings = refresh_benchmark_sectors_for_holdings(
        holdings,
        fetch_missing_benchmark=fetch_missing_benchmark,
        fetch_holdings_infer=fetch_holdings_infer,
        infer_missing_only=infer_missing_only,
        batch_context=batch_context,
    )
    holdings, profiles = profile_service.resolve_holdings_with_profiles(
        holdings,
        fetch_benchmark=fetch_missing_benchmark,
        profiles_snapshot=profiles_snapshot,
        primary_sector_batch_context=batch_context,
    )
    lookup_labels = [
        sector_quote_lookup_label(
            holding,
            profile=profile,
        )
        for holding, profile in zip(holdings, profiles)
    ]

    boards: dict[str, dict[str, float]] = {
        "index": {},
        "concept": {},
        "industry": {},
    }
    if cache_only:
        fetch_result = load_spot_boards_from_cache_only()
        for board_type in ("index", "concept", "industry"):
            boards[board_type] = dict(fetch_result.boards.get(board_type) or {})
        kline_prefetched = 0
    else:
        kline_prefetched = prefetch_canonical_kline_quotes(
            lookup_labels,
            boards,
            timeout_seconds=timeout_seconds,
        )

        canonical_label_count = len(
            {
                normalize_sector_label(label)
                for label in lookup_labels
                if label and get_canonical_sector(label)
            }
        )
        need_spot_boards = labels_need_spot_boards(lookup_labels) or (
            canonical_label_count > 0 and kline_prefetched < canonical_label_count
        )

        if need_spot_boards:
            fetch_result = fetch_spot_boards_result(
                force_refresh=force_refresh,
                timeout_seconds=timeout_seconds,
            )
            for board_type in ("index", "concept", "industry"):
                merged = _merge_spot_board_under_canonical(
                    canonical=boards.get(board_type),
                    spot=fetch_result.boards.get(board_type),
                )
                boards[board_type] = merged
                fetch_result.boards[board_type] = merged
        else:
            fetch_result = SpotBoardFetchResult(
                boards=boards,
                provider_path="eastmoney_kline",
                live_attempted=True,
                elapsed_seconds=0.0,
            )

    if cache_only and not any(boards.values()):
        holdings = _overlay_holdings_daily_estimates(
            holdings,
            cache_only=True,
            accurate=accurate,
            profiles=profiles,
        )
        return {
            "ok": True,
            "message": "板块缓存未命中，后台将刷新",
            "holdings": [holding.model_dump() for holding in holdings],
            "items": [],
            "holding_warnings": [],
            "summary": {
                "matched": 0,
                "unresolved": len(holdings),
                "needs_mapping": 0,
                "estimate_fallback": 0,
                "board_matched": 0,
                "secid_matched": 0,
                "provider_path": fetch_result.provider_path,
                "from_stale_cache": fetch_result.from_stale_cache,
            },
            "session": session,
            "fetched_at": fetched_at.isoformat(),
            **_provider_meta(fetch_result, provider_path=fetch_result.provider_path),
        }

    if (
        not cache_only
        and not any(boards.values())
        and kline_prefetched == 0
    ):
        holdings = _overlay_holdings_daily_estimates(
            holdings,
            cache_only=cache_only,
            accurate=accurate,
            profiles=profiles,
        )
        if any(label and str(label).strip() for label in lookup_labels):
            return {
                "ok": False,
                "message": "板块行情拉取失败（网络/代理），且没有可用快照，请稍后重试",
                "holdings": [holding.model_dump() for holding in holdings],
                "items": [],
                "summary": {
                    "matched": 0,
                    "unresolved": len(holdings),
                    "needs_mapping": 0,
                    "estimate_fallback": 0,
                    "board_matched": 0,
                    "secid_matched": 0,
                    "provider_path": fetch_result.provider_path,
                    "from_stale_cache": fetch_result.from_stale_cache,
                },
                "session": session,
                "provider_failed": True,
                **_provider_meta(fetch_result, provider_path=fetch_result.provider_path),
            }
        return {
            "ok": True,
            "message": "已按重仓估算更新当日收益",
            "holdings": [holding.model_dump() for holding in holdings],
            "items": [],
            "holding_warnings": [],
            "summary": {
                "matched": 0,
                "unresolved": 0,
                "needs_mapping": 0,
                "estimate_fallback": 0,
                "board_matched": 0,
                "secid_matched": 0,
                "provider_path": fetch_result.provider_path,
                "from_stale_cache": fetch_result.from_stale_cache,
            },
            "session": session,
            "fetched_at": fetched_at.isoformat(),
            **_provider_meta(fetch_result, provider_path=fetch_result.provider_path),
        }

    updated: list[Holding] = []
    items: list[dict] = []
    warnings: list[HoldingFieldWarning] = []
    matched = 0
    unresolved = 0
    needs_mapping = 0
    secid_matched = 0
    mapping_cache: dict[str, dict[str, Any] | None] = {}

    for index, holding in enumerate(holdings):
        profile = profiles[index]
        if holding.sector_name and not _is_valid_sector_label(holding.sector_name):
            holding = holding.model_copy(update={"sector_name": None})
        repair_fields = primary_sector_fields_for_holding(
            holding,
            fetch_benchmark=fetch_missing_benchmark,
            fetch_holdings_infer=fetch_holdings_infer,
            batch_context=batch_context,
        )
        if repair_fields:
            holding = holding.model_copy(update=repair_fields)

        lookup_label = sector_quote_lookup_label(
            holding,
            profile=profile,
        )
        label_key = sector_label_key(lookup_label)
        persisted = None
        if not force_refresh and label_key:
            if label_key not in mapping_cache:
                mapping_cache[label_key] = get_sector_mapping(label_key)
            persisted = mapping_cache[label_key]
        result = resolve_sector_quote(
            holding.sector_name,
            boards,
            persisted_mapping=persisted,
            quote_label=lookup_label,
        )
        label_in_boards = bool(label_key) and any(
            label_key in (boards.get(board_type) or {})
            for board_type in ("concept", "industry", "index")
        )
        needs_on_demand = result.confidence not in {"high", "medium"} or (
            label_key
            and not label_in_boards
            and result.matched_name != label_key
        )
        if needs_on_demand and timeout_seconds is None and not cache_only:
            on_demand = fetch_sector_on_demand(lookup_label, boards)
            if on_demand is not None and on_demand.change_percent is not None:
                result = on_demand
                if on_demand.source_type and on_demand.matched_name:
                    boards.setdefault(on_demand.source_type, {})[on_demand.matched_name] = on_demand.change_percent

        used_secid_quote = False
        if (
            result.confidence in {"high", "medium"}
            and result.change_percent is not None
            and not is_plausible_daily_change(result.change_percent)
        ):
            result = SectorResolveResult(
                confidence="none",
                message=f"板块涨跌 {result.change_percent:+.2f}% 超出合理范围，已忽略",
            )
        elif result.message and result.message.startswith("东财K线"):
            used_secid_quote = True

        previous = holding.sector_return_percent
        meta = SectorQuoteMeta(
            source="ocr",
            provider=(
                "eastmoney-kline"
                if result.message and result.message.startswith("东财K线")
                else "eastmoney-akshare"
            ),
            confidence=result.confidence,
            matched_name=result.matched_name,
            source_type=result.source_type if result.source_type in {"index", "concept", "industry"} else None,
            source_code=result.source_code,
            fetched_at=fetched_at,
            previous_percent=previous,
            message=result.message,
        )

        new_holding = release_stale_official_nav_to_sector(
            holding,
            session_kind=session_kind,
            profile=profile,
        )
        if result.confidence in {"high", "medium"} and result.change_percent is not None:
            nav_return = None
            if holding.fund_code and not intraday_blocks_official_nav and not cache_only:
                nav_return = get_official_nav_return(
                    holding.fund_code,
                    effective_trade_date,
                )

            sector_source = "realtime" if is_trading_hours else "closing_estimate"
            update: dict = {}
            hide_research_board = _hide_research_associated_board(
                holding,
                batch_context=batch_context,
            )
            if hide_research_board:
                update["sector_name"] = None
                update["intraday_index_name"] = None
                update["sector_return_percent"] = None
                update["sector_return_percent_source"] = None
            display_sector = sector_display_label(holding)
            if not hide_research_board:
                if _is_valid_sector_label(display_sector) and not _is_valid_sector_label(
                    holding.sector_name
                ):
                    update["sector_name"] = display_sector
                elif (
                    _is_valid_sector_label(result.matched_name)
                    and not _is_valid_sector_label(holding.sector_name)
                ):
                    canonical = get_canonical_sector(result.matched_name or "")
                    update["sector_name"] = (
                        canonical.label if canonical else result.matched_name
                    )
            from app.services.profit_accrual_defer import is_profit_accrual_deferred

            amount = holding.settled_holding_amount or holding.holding_amount
            # 真实板块行情才写回 sector_return_percent。无主题灵活配置不写板块列，
            # 当日走循环结束后的季报重仓加权。
            if not hide_research_board:
                update["sector_return_percent"] = result.change_percent
                update["sector_return_percent_source"] = sector_source
            if nav_return is not None and not is_profit_accrual_deferred(profile):
                update["daily_return_percent"] = nav_return
                update["daily_profit"] = compute_daily_profit_from_rate(
                    amount,
                    nav_return,
                    amount_includes_today=_amount_includes_today_return(holding),
                )
                update["daily_return_percent_source"] = "official_nav"
            else:
                update["daily_return_percent"] = None
                update["daily_profit"] = None
                update["daily_return_percent_source"] = None
            new_holding = new_holding.model_copy(update=update)
            meta.source = "live"
            meta.delta_vs_previous = round(result.change_percent - previous, 4) if previous is not None else None
            matched += 1
            if used_secid_quote:
                secid_matched += 1
            record = mapping_record_from_result(lookup_label, result)
            if record is not None:
                saved_mapping = save_sector_mapping(record)
                if label_key:
                    mapping_cache[label_key] = saved_mapping or record
            if (
                nav_return is None
                and result.source_type in {"index", "concept", "industry"}
                and previous is not None
                and meta.delta_vs_previous is not None
                and abs(meta.delta_vs_previous) >= settings.sector_quotes_discrepancy_warn
            ):
                warnings.append(
                    HoldingFieldWarning(
                        index=index,
                        field="sector_return_percent",
                        code="sector_quote_discrepancy",
                        message=(
                            f"实时板块 {result.change_percent:+.2f}% 与 OCR {previous:+.2f}% "
                            f"相差 {meta.delta_vs_previous:+.2f} 个百分点"
                        ),
                        severity="info",
                    )
                )
        elif result.confidence == "low":
            meta.source = "ocr"
            needs_mapping += 1
            items.append(
                {
                    "index": index,
                    "fund_code": holding.fund_code,
                    "fund_name": holding.fund_name,
                    "sector_name": holding.sector_name,
                    "intraday_index_name": holding.intraday_index_name,
                    "sector_quote_label": lookup_label,
                    "sector_quote_meta": meta.model_dump(mode="json"),
                    "mapping_candidates": [
                        SectorMappingCandidate.model_validate(
                            {
                                "source_type": candidate.source_type,
                                "source_name": candidate.source_name,
                                "change_percent": candidate.change_percent,
                                "source_code": candidate.source_code,
                            }
                        ).model_dump(mode="json")
                        for candidate in result.candidates
                    ],
                }
            )
            updated.append(new_holding)
            continue
        else:
            unresolved += 1
            meta.source = "ocr"

        updated.append(new_holding)
        items.append(
            {
                "index": index,
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "sector_name": holding.sector_name,
                "intraday_index_name": holding.intraday_index_name,
                "sector_quote_label": lookup_label,
                "sector_quote_meta": meta.model_dump(mode="json"),
                "mapping_candidates": [],
            }
        )

    updated = _overlay_holdings_daily_estimates(
        updated,
        cache_only=cache_only,
        accurate=accurate,
        profiles=profiles,
    )
    provider_path = fetch_result.provider_path
    return {
        "ok": True,
        "message": _refresh_message(fetch_result, matched, needs_mapping, unresolved),
        "holdings": [holding.model_dump() for holding in updated],
        "items": items,
        "holding_warnings": [warning.model_dump() for warning in warnings],
        "summary": {
            "matched": matched,
            "unresolved": unresolved,
            "needs_mapping": needs_mapping,
            "estimate_fallback": 0,
            "board_matched": matched,
            "secid_matched": secid_matched,
            "provider_path": provider_path,
            "from_stale_cache": fetch_result.from_stale_cache,
        },
        "session": session,
        "fetched_at": fetched_at.isoformat(),
        **_provider_meta(fetch_result, provider_path=provider_path),
    }


def apply_sector_mapping_choice(
    holdings: list[Holding],
    *,
    index: int,
    source_type: str,
    source_name: str,
    source_code: str | None = None,
) -> dict:
    if index < 0 or index >= len(holdings):
        raise ValueError("持仓索引无效")

    boards = fetch_spot_boards(force_refresh=False)
    board = boards.get(source_type) or {}
    if source_name not in board:
        raise ValueError("所选映射在当前行情中不存在")

    holding = holdings[index]
    label_key = sector_label_key(sector_quote_lookup_label(holding))
    if not label_key:
        raise ValueError("该持仓缺少关联板块或场内指数名称")

    save_sector_mapping(
        {
            "sector_label": label_key,
            "source_type": source_type,
            "source_code": source_code,
            "source_name": source_name,
            "confidence": "high",
        }
    )

    updated = list(holdings)
    updated[index] = holding.model_copy(update={"sector_return_percent": board[source_name]})
    return refresh_holdings_sector_quotes(updated, force_refresh=False)


def _provider_meta(fetch_result: SpotBoardFetchResult, *, provider_path: str) -> dict:
    return {
        "provider_path": provider_path,
        "from_stale_cache": fetch_result.from_stale_cache,
        "provider_elapsed_seconds": fetch_result.elapsed_seconds,
        "joined_in_flight": fetch_result.joined_in_flight,
    }


def _refresh_message(
    fetch_result: SpotBoardFetchResult,
    matched: int,
    needs_mapping: int,
    unresolved: int,
) -> str:
    prefix = "已用上次快照更新" if fetch_result.from_stale_cache else "已刷新"
    return f"{prefix} {matched} 只，{needs_mapping} 只需选择映射，{unresolved} 只未匹配"


def _merge_spot_board_under_canonical(
    *,
    canonical: dict[str, float] | None,
    spot: dict[str, float] | None,
) -> dict[str, float]:
    """现货榜只补 canonical 没覆盖到的简称，**不覆盖**已有条目。

    `canonical` 是 `prefetch_canonical_kline_quotes` 写进来的那份：按 registry 的
    secid 逐个标的拉取，高风险 label 还额外过了 `provider_identity_matches`。
    `spot` 是全市场现货榜，只能按**简称**索引——而简称不唯一（东财对深证 399262
    与中证 931582 都显示「数字经济」）。

    此前这里是 `merged.update(spot)`，合并方向反了：一份没有身份保证的简称值会顶掉
    已经按 secid 校验过的值。生产表现就是持仓行的「数字经济」从中证 931582 的
    +1.54% 变成深证 399262 的 +2.29%，而同一份日报里走另一条路的 `sector_opportunity`
    仍是 1.35——同一天同一板块两个数字。

    这里的方向与 `sector_quote_provider._fill_missing_boards_from_akshare` 一致：
    兜底源只填空缺，不改写更可信的那一份。
    """
    return {**(spot or {}), **(canonical or {})}


def _hide_research_associated_board(
    holding: Holding,
    *,
    batch_context: PrimarySectorBatchContext | None,
) -> bool:
    if is_unthemed_allocation_fund(holding.fund_name):
        return True
    code = (holding.fund_code or "").strip()
    row = batch_context.user_row(code) if batch_context is not None and code else None
    if row is None and code and code != "000000":
        from app.database import get_fund_primary_sector

        row = get_fund_primary_sector(code)
    source = str((row or {}).get("source") or "")
    if not is_research_associated_sector_source(source):
        return False
    return not associated_sector_is_page_visible(
        fund_name=holding.fund_name,
        sector_name=(row or {}).get("sector_name") or holding.sector_name,
        source=source,
    )


def _overlay_holdings_daily_estimates(
    holdings: list[Holding],
    *,
    cache_only: bool,
    accurate: bool,
    profiles: list | None = None,
) -> list[Holding]:
    """主动基金当日：季报重仓加权优先于板块估算；官方净值仍锁定。"""

    from app.services.fund_holdings_return_estimate import overlay_holdings_daily_estimates

    return overlay_holdings_daily_estimates(
        holdings,
        allow_fetch=not cache_only,
        allow_live_snapshot=not cache_only and accurate,
        profiles=profiles,
    )
