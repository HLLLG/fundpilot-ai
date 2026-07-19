"""只读基金研究详情。

搜索结果打开此详情时不得创建基金档案、持仓或板块映射。关联关系也按证据用途
分层：跟踪标的/持仓暴露可以提供行情参考，主动基金的业绩比较基准只能用于走势
对比，不能冒充基金净值或“主要关联板块”。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.database import (
    get_fund_primary_sector,
    get_fund_profile_by_code,
    get_most_recent_portfolio_snapshot,
)
from app.models import FundNavHistory
from app.services.fund_benchmark_sector import (
    fetch_fund_benchmark_text,
    get_fund_benchmark_fetch_metadata,
    parse_benchmark_index,
    resolve_sector_from_benchmark,
)
from app.services.fund_code_resolver import lookup_fund_name_by_code
from app.services.fund_data import FundDataService
from app.services.fund_diagnostics_cache import load_fund_diagnostics
from app.services.fund_primary_sector_global import load_fresh_global_sector
from app.services.index_daily_client import index_display_name
from app.services.sector_canonical import CanonicalSector, get_canonical_sector

_HOLDINGS_RELATION_SOURCES = frozenset({"holdings_infer", "precompute_holdings"})
_USER_RELATION_SOURCES = frozenset({"manual", "ocr_detail"})


def _normalized_fund_code(raw: str) -> str:
    value = str(raw or "").strip()
    if not value.isdigit() or len(value) > 6:
        raise ValueError("基金代码须为最多六位数字")
    return value.zfill(6)


def _is_passive_index_fund(fund_name: str, fund_type: str | None) -> bool:
    text = f"{fund_name} {fund_type or ''}".upper()
    return any(marker in text for marker in ("指数", "ETF", "联接", "LOF"))


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _period_return(history: FundNavHistory, trading_days: int) -> float | None:
    points = history.points
    if len(points) <= trading_days:
        return None
    start = points[-(trading_days + 1)]
    end = points[-1]
    if start.nav <= 0:
        return None
    return round((end.nav / start.nav - 1.0) * 100.0, 2)


def _holding_from_latest_snapshot(code: str) -> dict[str, Any] | None:
    snapshot = get_most_recent_portfolio_snapshot()
    rows = (snapshot or {}).get("holdings")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("fund_code") or "").strip().zfill(6) != code:
                continue
            try:
                amount = float(row.get("holding_amount") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                return dict(row)
    return None


def _canonical_for_row(row: Mapping[str, Any]) -> CanonicalSector | None:
    return get_canonical_sector(
        str(row.get("intraday_index_name") or row.get("sector_name") or "")
    )


def _persisted_relation(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    source = str(row.get("source") or "")
    if source not in _HOLDINGS_RELATION_SOURCES | _USER_RELATION_SOURCES:
        return None
    label = str(row.get("sector_name") or "").strip()
    if not label:
        return None
    canonical = _canonical_for_row(row)
    detail = _mapping(row.get("detail"))
    is_holdings = source in _HOLDINGS_RELATION_SOURCES
    return {
        "status": "available",
        "kind": "holdings_exposure" if is_holdings else "user_confirmed",
        "label": label,
        "source_type": canonical.source_type if canonical else None,
        "source_code": canonical.source_code if canonical else None,
        "source_name": canonical.source_name if canonical else None,
        "confidence": row.get("confidence"),
        "evidence_tier": "holdings_disclosure" if is_holdings else "user_supplied",
        "evidence_source": source,
        "as_of": row.get("updated_at") or row.get("resolved_at"),
        "price_proxy_eligible": canonical is not None,
        "note": (
            "基于最近披露持仓识别主要暴露，仅作板块行情参考，不等同基金净值。"
            if is_holdings
            else "来自用户确认或持仓详情资料，仅作行情参考，不等同基金净值。"
        ),
        "detail": dict(detail),
    }


def _benchmark_relation(
    *,
    fund_code: str,
    benchmark_text: str | None,
    fund_name: str,
    fund_type: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    passive = _is_passive_index_fund(fund_name, fund_type)
    parsed = parse_benchmark_index(benchmark_text or "") if benchmark_text else None
    metadata = (
        get_fund_benchmark_fetch_metadata(fund_code, benchmark_text or "")
        if benchmark_text
        else {}
    )
    benchmark: dict[str, Any] | None = None
    if parsed is not None:
        benchmark_name = parsed.index_name or index_display_name(parsed.index_code)
        benchmark = {
            "symbol": parsed.index_code if parsed.index_code.isdigit() else None,
            "name": benchmark_name,
            "kind": "tracking_reference" if passive else "benchmark_reference",
            "source": "third_party_reference",
            "benchmark_text": benchmark_text,
        }

    if passive and benchmark_text:
        resolved = resolve_sector_from_benchmark(benchmark_text)
        if resolved is not None:
            sector_label, intraday_name, match = resolved
            canonical = get_canonical_sector(intraday_name or sector_label)
            return (
                {
                    "status": "available",
                    "kind": "tracking_reference",
                    "label": sector_label,
                    "source_type": canonical.source_type if canonical else "index",
                    "source_code": match.index_code,
                    "source_name": (
                        canonical.source_name
                        if canonical
                        else match.index_name or index_display_name(match.index_code)
                    ),
                    "confidence": 0.68,
                    "evidence_tier": "third_party_reference",
                    "evidence_source": metadata.get(
                        "benchmark_text_source_kind", "unknown"
                    ),
                    "as_of": None,
                    "price_proxy_eligible": canonical is not None,
                    "note": "精确指数身份可作跟踪标的行情参考；基金净值仍以官方披露为准。",
                },
                benchmark,
            )

    return (
        {
            "status": "unavailable",
            "kind": "unavailable",
            "label": None,
            "source_type": None,
            "source_code": None,
            "source_name": None,
            "confidence": None,
            "evidence_tier": "insufficient",
            "evidence_source": None,
            "as_of": None,
            "price_proxy_eligible": False,
            "note": (
                "主动基金暂无可靠单一板块；业绩比较基准仅用于走势对比。"
                if not passive
                else "暂未取得可核验的精确跟踪指数，不展示板块涨幅。"
            ),
        },
        benchmark,
    )


def _type_benchmark(fund_type: str | None) -> dict[str, Any] | None:
    value = str(fund_type or "")
    if "股票" in value or "混合" in value:
        return {
            "symbol": "000300",
            "name": "沪深300（类型参考）",
            "kind": "type_reference",
            "source": "fund_type_fallback",
            "benchmark_text": None,
        }
    return None


def _safe_fund_diagnostics(fund_code: str) -> dict[str, Any]:
    try:
        return load_fund_diagnostics(fund_code)
    except Exception:
        return {}


def build_fund_public_overview(fund_code: str) -> dict[str, Any]:
    """构建搜索详情；仅执行读取和外部公共数据查询。"""
    code = _normalized_fund_code(fund_code)
    profile = get_fund_profile_by_code(code)
    fund_name = lookup_fund_name_by_code(code) or (profile.fund_name if profile else "")
    if not fund_name:
        raise LookupError("未找到该基金代码")

    with ThreadPoolExecutor(max_workers=3) as executor:
        nav_future = executor.submit(
            FundDataService().get_nav_history,
            code,
            fund_name,
            trading_days=260,
        )
        diagnostics_future = executor.submit(_safe_fund_diagnostics, code)
        benchmark_future = executor.submit(fetch_fund_benchmark_text, code)
        history = nav_future.result()
        diagnostics = diagnostics_future.result()
        benchmark_text = benchmark_future.result()

    fund_type = str(diagnostics.get("fund_type") or "").strip() or None
    user_relation = _persisted_relation(get_fund_primary_sector(code))
    global_relation = _persisted_relation(load_fresh_global_sector(code))
    relation, performance_benchmark = _benchmark_relation(
        fund_code=code,
        benchmark_text=benchmark_text,
        fund_name=fund_name,
        fund_type=fund_type,
    )
    relation = user_relation or global_relation or relation

    if benchmark_text:
        metadata = get_fund_benchmark_fetch_metadata(code, benchmark_text)
        relation = {
            **relation,
            **(
                {"evidence_source": metadata.get("benchmark_text_source_kind")}
                if relation.get("kind") == "tracking_reference"
                else {}
            ),
        }
    if performance_benchmark is None:
        performance_benchmark = _type_benchmark(fund_type)

    latest = history.points[-1] if history.points else None
    official_daily_return = latest.daily_return_percent if latest else None
    holding = _holding_from_latest_snapshot(code)
    if holding is None and profile and float(profile.holding_amount or 0) > 0:
        holding = {
            "fund_code": code,
            "fund_name": fund_name,
            "holding_amount": profile.holding_amount,
            "holding_profit": profile.holding_profit,
            "holding_return_percent": profile.holding_return_percent,
            "daily_profit": profile.daily_profit,
        }

    return {
        "fund_code": code,
        "fund_name": fund_name,
        "fund_type": fund_type,
        "latest_nav": history.latest_nav,
        "nav_date": history.latest_date,
        "official_daily_return_percent": official_daily_return,
        "official_return_status": "available" if official_daily_return is not None else "pending",
        "returns": {
            "one_month_percent": _period_return(history, 20),
            "three_month_percent": _period_return(history, 60),
            "six_month_percent": _period_return(history, 120),
            "one_year_percent": _period_return(history, 250),
        },
        "management_fee": diagnostics.get("management_fee"),
        "fund_scale_yi": diagnostics.get("fund_scale_yi"),
        "fund_scale_source": diagnostics.get("fund_scale_source"),
        "fund_scale_as_of": diagnostics.get("fund_scale_as_of"),
        "max_drawdown_1y_percent": diagnostics.get("max_drawdown_1y_percent"),
        "relation": relation,
        "performance_benchmark": performance_benchmark,
        "nav_history": history.model_dump(mode="json"),
        "is_held": holding is not None,
        "holding": holding,
        "data_note": "基金涨跌与收益率均来自官方净值序列；关联行情仅作参考。",
    }
