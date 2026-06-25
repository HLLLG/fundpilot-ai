from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.database import get_sector_mapping, save_sector_mapping
from app.models import Holding, HoldingFieldWarning, SectorMappingCandidate, SectorQuoteMeta
from app.services.fund_primary_sector_service import primary_sector_fields_for_holding
from app.services.fund_profile import FundProfileService, _is_valid_sector_label
from app.services.fund_estimate_provider import fetch_fund_estimate_quotes
from app.services.sector_canonical import (
    get_canonical_sector,
    labels_need_spot_boards,
    prefetch_canonical_kline_quotes,
)
from app.services.sector_labels import normalize_sector_label
from app.services.sector_labels import sector_label_key
from app.services.sector_on_demand import fetch_sector_on_demand
from app.services.sector_quote_label import sector_display_label, sector_quote_lookup_label
from app.services.sector_quote_provider import SpotBoardFetchResult, fetch_spot_boards, fetch_spot_boards_result
from app.services.sector_quote_resolver import (
    SectorResolveResult,
    mapping_record_from_result,
    resolve_sector_quote,
)
from app.services.trading_session import build_trading_session, get_effective_trade_date
from app.services.fund_nav_service import get_official_nav_return
from app.services.holding_estimates import (
    _amount_includes_today_return,
    compute_daily_profit_from_rate,
)
from app.services.eastmoney_trends_client import is_plausible_daily_change


class _EstimateResult:
    def __init__(self, holding: Holding, estimate_quote: dict[str, Any]) -> None:
        self.confidence = "high"
        self.change_percent = estimate_quote.get("change_percent")
        self.matched_name = estimate_quote.get("fund_name") or holding.fund_name
        self.source_type = None
        self.source_code = holding.fund_code
        self.message = "天天基金估值"
        self.candidates = []


def _is_trading_hours() -> bool:
    session = build_trading_session()
    return session.get("session_kind") == "trading_day_intraday"


def _get_last_trade_date() -> str:
    """板块涨跌/官方净值所对应的有效交易日（开盘前与周末回溯上一交易日）。"""
    return get_effective_trade_date()


def refresh_holdings_sector_quotes(
    holdings: list[Holding],
    *,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
) -> dict:
    settings = get_settings()
    session = build_trading_session()
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

    holdings = refresh_benchmark_sectors_for_holdings(holdings)
    holdings = [profile_service.resolve_holding(holding) for holding in holdings]
    lookup_labels = [
        sector_quote_lookup_label(
            holding,
            profile=profile_service._find_profile_for_holding(holding),
        )
        for holding in holdings
    ]

    boards: dict[str, dict[str, float]] = {
        "index": {},
        "concept": {},
        "industry": {},
    }
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
            merged = boards.get(board_type) or {}
            merged.update(fetch_result.boards.get(board_type) or {})
            boards[board_type] = merged
            fetch_result.boards[board_type] = merged
    else:
        fetch_result = SpotBoardFetchResult(
            boards=boards,
            provider_path="eastmoney_kline",
            live_attempted=True,
            elapsed_seconds=0.0,
        )

    estimate_quotes: dict[str, dict] = {}
    if need_spot_boards:
        estimate_quotes = _maybe_fetch_estimate_quotes(
            holdings,
            boards=boards,
            fetch_result=fetch_result,
            timeout_seconds=timeout_seconds,
        )
    estimate_quotes_loaded = (
        need_spot_boards
        and timeout_seconds is not None
        and _board_entry_count(fetch_result.boards) < 8
    )

    # 兜底：当没有任何板块/指数命中（例如全部持仓都是无关联板块的新基金，
    # 如「中航机遇领航混合发起C」）时，仍尝试用天天基金估值给出当日收益，
    # 避免直接硬失败 + 当日收益恒为 0。
    if not any(boards.values()) and not estimate_quotes and kline_prefetched == 0:
        has_real_fund_code = any(
            (holding.fund_code or "").strip() and holding.fund_code != "000000"
            for holding in holdings
        )
        if has_real_fund_code:
            estimate_quotes = fetch_fund_estimate_quotes(
                holdings,
                timeout_seconds=timeout_seconds,
            )
            estimate_quotes_loaded = True

    if not any(boards.values()) and not estimate_quotes and kline_prefetched == 0:
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

    updated: list[Holding] = []
    items: list[dict] = []
    warnings: list[HoldingFieldWarning] = []
    matched = 0
    unresolved = 0
    needs_mapping = 0
    estimate_fallback = 0
    secid_matched = 0

    for index, holding in enumerate(holdings):
        if holding.sector_name and not _is_valid_sector_label(holding.sector_name):
            holding = holding.model_copy(update={"sector_name": None})
        repair_fields = primary_sector_fields_for_holding(holding, allow_name_infer=True)
        if repair_fields:
            holding = holding.model_copy(update=repair_fields)

        lookup_label = sector_quote_lookup_label(
            holding,
            profile=profile_service._find_profile_for_holding(holding),
        )
        label_key = sector_label_key(lookup_label)
        persisted = None if force_refresh else (get_sector_mapping(label_key) if label_key else None)
        result = resolve_sector_quote(
            holding.sector_name,
            boards,
            persisted_mapping=persisted,
            quote_label=lookup_label,
        )
        label_boards = (boards.get("concept") or {}) | (boards.get("industry") or {}) | (boards.get("index") or {})
        needs_on_demand = result.confidence not in {"high", "medium"} or (
            label_key
            and label_key not in label_boards
            and result.matched_name != label_key
        )
        if needs_on_demand and timeout_seconds is None:
            on_demand = fetch_sector_on_demand(lookup_label, boards)
            if on_demand is not None and on_demand.change_percent is not None:
                result = on_demand
                if on_demand.source_type and on_demand.matched_name:
                    boards.setdefault(on_demand.source_type, {})[on_demand.matched_name] = on_demand.change_percent

        estimate_quote = None
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
        if result.confidence not in {"high", "medium"}:
            if timeout_seconds is not None and not estimate_quotes_loaded:
                estimate_quotes = fetch_fund_estimate_quotes(
                    holdings,
                    timeout_seconds=timeout_seconds,
                )
                estimate_quotes_loaded = True
            estimate_quote = estimate_quotes.get(holding.fund_code)
            if estimate_quote is not None and estimate_quote.get("change_percent") is not None:
                result = _EstimateResult(holding, estimate_quote)
        elif result.message and result.message.startswith("东财K线"):
            used_secid_quote = True

        previous = holding.sector_return_percent
        meta = SectorQuoteMeta(
            source="ocr",
            provider=(
                "tiantian-fund-estimate"
                if estimate_quote is not None
                else (
                    "eastmoney-kline"
                    if result.message and result.message.startswith("东财K线")
                    else "eastmoney-akshare"
                )
            ),
            confidence=result.confidence,
            matched_name=result.matched_name,
            source_type=result.source_type if result.source_type in {"index", "concept", "industry"} else None,
            source_code=result.source_code,
            fetched_at=fetched_at,
            previous_percent=previous,
            message=result.message,
        )

        new_holding = holding
        if result.confidence in {"high", "medium"} and result.change_percent is not None:
            trade_date = _get_last_trade_date()
            nav_return = get_official_nav_return(holding.fund_code, trade_date) if holding.fund_code else None

            sector_source = "realtime" if _is_trading_hours() else "closing_estimate"
            update: dict = {
                "sector_return_percent": result.change_percent,
                "sector_return_percent_source": sector_source,
            }
            display_sector = sector_display_label(holding)
            if _is_valid_sector_label(display_sector) and not _is_valid_sector_label(
                holding.sector_name
            ):
                update["sector_name"] = display_sector
            elif (
                estimate_quote is None
                and result.message != "天天基金估值"
                and _is_valid_sector_label(result.matched_name)
                and not _is_valid_sector_label(holding.sector_name)
            ):
                canonical = get_canonical_sector(result.matched_name or "")
                update["sector_name"] = (
                    canonical.label if canonical else result.matched_name
                )
            from app.services.profit_accrual_defer import is_profit_accrual_deferred

            profile = profile_service._find_profile_for_holding(holding)
            if nav_return is not None and not is_profit_accrual_deferred(profile):
                update["daily_return_percent"] = nav_return
                update["daily_profit"] = compute_daily_profit_from_rate(
                    holding.holding_amount,
                    nav_return,
                    amount_includes_today=_amount_includes_today_return(holding),
                )
                update["daily_return_percent_source"] = "official_nav"
            else:
                update["daily_return_percent"] = None
                update["daily_profit"] = None
                update["daily_return_percent_source"] = None
            new_holding = holding.model_copy(update=update)
            meta.source = "live"
            meta.delta_vs_previous = round(result.change_percent - previous, 4) if previous is not None else None
            matched += 1
            if estimate_quote is not None:
                estimate_fallback += 1
            elif used_secid_quote:
                secid_matched += 1
            record = mapping_record_from_result(lookup_label, result)
            if record is not None:
                save_sector_mapping(record)
            if (
                nav_return is None
                and estimate_quote is None
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

    provider_path = _effective_provider_path(fetch_result, estimate_fallback=estimate_fallback)
    return {
        "ok": True,
        "message": _refresh_message(fetch_result, matched, estimate_fallback, needs_mapping, unresolved),
        "holdings": [holding.model_dump() for holding in updated],
        "items": items,
        "holding_warnings": [warning.model_dump() for warning in warnings],
        "summary": {
            "matched": matched,
            "unresolved": unresolved,
            "needs_mapping": needs_mapping,
            "estimate_fallback": estimate_fallback,
            "board_matched": max(0, matched - estimate_fallback),
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
    }


def _refresh_message(
    fetch_result: SpotBoardFetchResult,
    matched: int,
    estimate_fallback: int,
    needs_mapping: int,
    unresolved: int,
) -> str:
    prefix = "已用上次快照更新" if fetch_result.from_stale_cache else "已刷新"
    suffix = f"，{estimate_fallback} 只用天天基金估值兜底" if estimate_fallback else ""
    return f"{prefix} {matched} 只{suffix}，{needs_mapping} 只需选择映射，{unresolved} 只未匹配"


def _maybe_fetch_estimate_quotes(
    holdings: list[Holding],
    *,
    boards: dict[str, dict[str, float]],
    fetch_result: SpotBoardFetchResult,
    timeout_seconds: float | None,
) -> dict[str, dict]:
    if timeout_seconds is None:
        return {}
    entry_count = _board_entry_count(boards)
    if entry_count >= 8:
        return {}
    if entry_count > 0 and fetch_result.provider_path in {
        "eastmoney_live",
        "relay_live",
        "browser_live",
        "fresh_cache",
        "stale_cache",
    }:
        return {}
    if entry_count > 0:
        return {}
    return fetch_fund_estimate_quotes(holdings, timeout_seconds=timeout_seconds)


def _effective_provider_path(
    fetch_result: SpotBoardFetchResult,
    *,
    estimate_fallback: int,
) -> str:
    if estimate_fallback > 0 and _board_entry_count(fetch_result.boards) == 0:
        return "fund_estimate_live"
    return fetch_result.provider_path


def _board_entry_count(boards: dict[str, dict[str, float]]) -> int:
    return sum(len(board or {}) for board in boards.values())
