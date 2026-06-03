from __future__ import annotations

from datetime import datetime, timezone

from app.config import get_settings
from app.database import get_sector_mapping, save_sector_mapping
from app.models import Holding, HoldingFieldWarning, SectorMappingCandidate, SectorQuoteMeta
from app.services.sector_labels import sector_label_key
from app.services.sector_on_demand import fetch_sector_on_demand
from app.services.sector_quote_provider import fetch_spot_boards
from app.services.sector_quote_label import sector_quote_lookup_label
from app.services.sector_quote_resolver import (
    mapping_record_from_result,
    resolve_sector_quote,
)
from app.services.trading_session import build_trading_session


def refresh_holdings_sector_quotes(
    holdings: list[Holding],
    *,
    force_refresh: bool = False,
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
            "summary": {"matched": 0, "unresolved": len(holdings), "needs_mapping": 0},
            "session": session,
        }

    boards = fetch_spot_boards(force_refresh=force_refresh)
    if not any(boards.values()):
        return {
            "ok": False,
            "message": "板块行情拉取失败（网络/代理），当前显示的是上次快照数据，请稍后重试",
            "holdings": [holding.model_dump() for holding in holdings],
            "items": [],
            "summary": {"matched": 0, "unresolved": len(holdings), "needs_mapping": 0},
            "session": session,
            "provider_failed": True,
        }

    updated: list[Holding] = []
    items: list[dict] = []
    warnings: list[HoldingFieldWarning] = []
    matched = 0
    unresolved = 0
    needs_mapping = 0

    for index, holding in enumerate(holdings):
        lookup_label = sector_quote_lookup_label(holding)
        label_key = sector_label_key(lookup_label)
        persisted = None if force_refresh else (get_sector_mapping(label_key) if label_key else None)
        result = resolve_sector_quote(
            holding.sector_name,
            boards,
            persisted_mapping=persisted,
            quote_label=lookup_label,
        )
        label_boards = (boards.get("concept") or {}) | (boards.get("industry") or {}) | (
            boards.get("index") or {}
        )
        needs_on_demand = result.confidence not in {"high", "medium"} or (
            label_key
            and label_key not in label_boards
            and result.matched_name != label_key
        )
        if needs_on_demand:
            on_demand = fetch_sector_on_demand(lookup_label, boards)
            if on_demand is not None and on_demand.change_percent is not None:
                result = on_demand
                if on_demand.source_type and on_demand.matched_name:
                    boards.setdefault(on_demand.source_type, {})[
                        on_demand.matched_name
                    ] = on_demand.change_percent
        previous = holding.sector_return_percent

        meta = SectorQuoteMeta(
            source="ocr",
            confidence=result.confidence,
            matched_name=result.matched_name,
            source_type=result.source_type,
            source_code=result.source_code,
            fetched_at=fetched_at,
            previous_percent=previous,
            message=result.message,
        )

        new_holding = holding
        if result.confidence in {"high", "medium"} and result.change_percent is not None:
            new_holding = holding.model_copy(update={"sector_return_percent": result.change_percent})
            meta.source = "live"
            meta.delta_vs_previous = (
                round(result.change_percent - previous, 4)
                if previous is not None
                else None
            )
            matched += 1
            record = mapping_record_from_result(lookup_label, result)
            if record is not None:
                save_sector_mapping(record)
            if (
                previous is not None
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
                    "sector_quote_meta": meta.model_dump(mode="json"),
                    "mapping_candidates": [
                        SectorMappingCandidate.model_validate(
                            {
                                "source_type": c.source_type,
                                "source_name": c.source_name,
                                "change_percent": c.change_percent,
                                "source_code": c.source_code,
                            }
                        ).model_dump(mode="json")
                        for c in result.candidates
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
                "sector_quote_meta": meta.model_dump(mode="json"),
                "mapping_candidates": [],
            }
        )

    return {
        "ok": True,
        "message": f"已刷新 {matched} 只，{needs_mapping} 只需选择映射，{unresolved} 只未匹配",
        "holdings": [holding.model_dump() for holding in updated],
        "items": items,
        "holding_warnings": [warning.model_dump() for warning in warnings],
        "summary": {
            "matched": matched,
            "unresolved": unresolved,
            "needs_mapping": needs_mapping,
        },
        "session": session,
        "fetched_at": fetched_at.isoformat(),
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
