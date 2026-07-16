"""Point-in-time fund tradeability and share-class cost evidence.

The discovery pipeline used to know that a fund looked attractive, but not
whether the selected share could actually be purchased at the decision time.
This module keeps those concerns deterministic:

* the all-fund purchase-status table is fetched once and cached briefly;
* fund fee pages are fetched once per share and cached separately;
* public-platform discounts are never presented as the fund's standard fee;
* historical replays never backfill themselves with a newer live snapshot;
* the final executable amount is checked again against the minimum and limit;
* short-horizon ideas fail closed when the holding-period cost is unknown or
  too large for a conservative off-exchange-fund workflow.

The public East Money pages are a best-effort research source, not an order
router.  Every payload therefore retains source, retrieval time, freshness and
missing-field state so the caller can downgrade to research-only behavior.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Callable, Iterable, Mapping

from app.config import get_settings
from app.services.akshare_subprocess import run_akshare_json_script
from app.services.news_freshness import CN_TZ, normalize_news_now
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)

TRADEABILITY_SCHEMA_VERSION = "fund_tradeability.v1"
TRADEABILITY_GATE_SCHEMA_VERSION = "fund_tradeability_gate.v1"
COST_SCHEMA_VERSION = "fund_transaction_cost.v1"

_PURCHASE_CACHE_KEY = "fund:tradeability:purchase:v1"
_FEE_CACHE_PREFIX = "fund:tradeability:fee:v1"
# East Money currently emits both 100000000000 and 99999999999 for "unlimited".
_UNLIMITED_SENTINEL_YUAN = 99_999_999_999.0
_SHORT_HORIZON_DAYS = 30
_MINIMUM_SAFE_HOLD_DAYS = 7
_SHORT_HORIZON_COST_CEILING_PERCENT = 1.0
_SYSTEM_INITIAL_MINIMUM_PURCHASE_YUAN = 100.0
_SALES_SERVICE_FEE_STATUSES = frozenset(
    {"known_zero", "known_positive", "unknown"}
)

_PURCHASE_FETCH_SCRIPT = r"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import akshare as ak

    frame = ak.fund_purchase_em()
    rows = {}
    if frame is not None and not frame.empty:
        for _, row in frame.iterrows():
            code = str(row.get("基金代码", "")).strip().zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue

            def value(key):
                raw = row.get(key)
                text = str(raw).strip()
                if raw is None or text.lower() in ("", "nan", "nat", "--", "---"):
                    return None
                return raw

            def number(key):
                raw = value(key)
                if raw is None:
                    return None
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None

            rows[code] = {
                "fund_name": str(row.get("基金简称", "")).strip() or None,
                "fund_type": str(row.get("基金类型", "")).strip() or None,
                "nav_report_date": str(value("最新净值/万份收益-报告时间") or "") or None,
                "purchase_status": str(value("申购状态") or "") or None,
                "redemption_status": str(value("赎回状态") or "") or None,
                "next_open_date": str(value("下一开放日") or "") or None,
                "minimum_purchase_yuan": number("购买起点"),
                "daily_purchase_limit_yuan": number("日累计限定金额"),
                "listed_platform_purchase_fee_percent": number("手续费"),
            }
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    print(json.dumps({
        "schema_version": "fund_purchase_snapshot.v1",
        "retrieved_at": now,
        "source": "akshare.fund_purchase_em",
        "source_url": "https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
        "rows": rows,
    }, ensure_ascii=False, default=str))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
"""

_FEE_FETCH_SCRIPT = r"""
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

codes = __CODES_JSON__

def clean(value):
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text.lower() in ("", "nan", "nat") else text

def read_table(table):
    try:
        return pd.read_html(StringIO(str(table)))[0]
    except Exception:
        return None

def pair_map(tables):
    values = {}
    for table in tables:
        frame = read_table(table)
        if frame is None or frame.empty:
            continue
        for row in frame.itertuples(index=False, name=None):
            cells = list(row)
            for index in range(0, len(cells) - 1, 2):
                key = clean(cells[index])
                value = clean(cells[index + 1])
                if key and value is not None:
                    values[key] = value
    return values

def fee_rows(table, *, redemption=False):
    frame = read_table(table)
    if frame is None or frame.empty:
        return []
    columns = [clean(column) or str(index) for index, column in enumerate(frame.columns)]
    rows = []
    for values in frame.itertuples(index=False, name=None):
        row = {columns[index]: clean(value) for index, value in enumerate(values)}
        condition = next((value for value in row.values() if value), None)
        if not condition:
            continue
        if redemption:
            raw_rate = next(
                (value for key, value in row.items() if value and "费率" in key),
                None,
            )
            rows.append({"condition": condition, "rate": raw_rate})
            continue
        raw_rate = next(
            (
                value
                for key, value in row.items()
                if value and ("原费率" in key or key == "费率")
            ),
            None,
        )
        if raw_rate is None:
            raw_rate = next(
                (value for key, value in row.items() if value and "费率" in key),
                None,
            )
        parts = [part.strip() for part in str(raw_rate or "").split("|")]
        rows.append({
            "condition": condition,
            "standard_rate": parts[0] if parts and parts[0] else None,
            "platform_rate": parts[1] if len(parts) > 1 and parts[1] else None,
        })
    return rows

def fetch_one(code):
    url = f"https://fundf10.eastmoney.com/jjfl_{code}.html"
    response = requests.get(
        url,
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0 fundpilot-research/1.0"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, features="html.parser")
    sections = {}
    for heading in soup.find_all(name="h4", class_="t"):
        title = clean(heading.get_text(" ", strip=True))
        if not title:
            continue
        table_count = 2 if title == "申购与赎回金额" else 1
        tables = heading.find_all_next("table", limit=table_count)
        if title in {"交易状态", "申购与赎回金额", "交易确认日", "运作费用"}:
            sections[title] = pair_map(tables)
        elif title == "申购费率" and tables:
            sections[title] = fee_rows(tables[0])
        elif title == "赎回费率" and tables:
            sections[title] = fee_rows(tables[0], redemption=True)
    return code, {
        "retrieved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "source": "eastmoney.fundf10_purchase_info",
        "source_url": url,
        "sections": sections,
    }

results = {}
worker_count = max(1, min(6, len(codes)))
with ThreadPoolExecutor(max_workers=worker_count) as executor:
    futures = {executor.submit(fetch_one, code): code for code in codes}
    for future in as_completed(futures):
        code = futures[future]
        try:
            resolved_code, payload = future.result()
            results[resolved_code] = payload
        except Exception as exc:
            results[code] = {"error": str(exc)}

print(json.dumps({"data": results}, ensure_ascii=False, default=str))
"""


def resolve_fund_tradeability_profiles(
    fund_codes: Iterable[str],
    *,
    decision_at: datetime | None = None,
    purchase_fetcher: Callable[[], Mapping[str, Any] | None] | None = None,
    fee_fetcher: Callable[[list[str]], Mapping[str, Any] | None] | None = None,
    wall_now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a deterministic tradeability profile for each requested share."""

    codes = sorted({_normalize_code(code) for code in fund_codes if _normalize_code(code)})
    if not codes:
        return {}
    decision = normalize_news_now(decision_at)
    observed_now = normalize_news_now(wall_now)
    current_request = _is_current_decision(decision, observed_now)

    purchase_loader = purchase_fetcher or _fetch_purchase_snapshot
    fee_loader = fee_fetcher or _fetch_fee_snapshots
    status_ttl = max(
        1,
        int(get_settings().fund_tradeability_status_cache_ttl_seconds),
    )
    purchase_cache_fresh = bool(
        current_request
        and _valid_purchase_snapshot(
            get_spot_snapshot(_PURCHASE_CACHE_KEY, ttl_seconds=status_ttl)
        )
    )
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fund-tradeability") as executor:
        purchase_future = executor.submit(
            _load_purchase_snapshot,
            decision,
            current_request=current_request,
            fetcher=purchase_loader,
        )
        fee_future = executor.submit(
            _load_fee_snapshots,
            codes,
            decision,
            current_request=current_request,
            fetcher=fee_loader,
            refresh_detail_status=current_request and not purchase_cache_fresh,
        )
        purchase_snapshot = purchase_future.result()
        fee_snapshots = fee_future.result()

    # The fee page also carries purchase/redemption state, but its normal
    # 24-hour cache is intentionally much longer than the 15-minute execution
    # status window.  When the bulk status endpoint is unavailable, refresh
    # stale cached fee pages once so a healthy fallback source is not rejected
    # merely because it was loaded earlier for fee research.
    if current_request and not _valid_purchase_snapshot(purchase_snapshot):
        fee_snapshots = _refresh_stale_detail_status_snapshots(
            codes,
            fee_snapshots,
            decision_at=decision,
            fetcher=fee_loader,
        )

    rows = (
        purchase_snapshot.get("rows")
        if isinstance(purchase_snapshot, Mapping)
        and isinstance(purchase_snapshot.get("rows"), Mapping)
        else {}
    )
    output: dict[str, dict[str, Any]] = {}
    for code in codes:
        bulk = rows.get(code) if isinstance(rows, Mapping) else None
        detail = fee_snapshots.get(code)
        output[code] = build_tradeability_profile(
            code,
            bulk=bulk if isinstance(bulk, Mapping) else None,
            bulk_snapshot=purchase_snapshot,
            detail=detail if isinstance(detail, Mapping) else None,
            decision_at=decision,
        )
    return output


def build_tradeability_profile(
    fund_code: str,
    *,
    bulk: Mapping[str, Any] | None,
    bulk_snapshot: Mapping[str, Any] | None,
    detail: Mapping[str, Any] | None,
    decision_at: datetime,
) -> dict[str, Any]:
    """Merge the bulk status and per-share fee page conservatively."""

    sections = (
        detail.get("sections")
        if isinstance(detail, Mapping) and isinstance(detail.get("sections"), Mapping)
        else {}
    )
    status_pairs = _mapping(sections.get("交易状态"))
    amount_pairs = _mapping(sections.get("申购与赎回金额"))
    confirmation_pairs = _mapping(sections.get("交易确认日"))
    operating_pairs = _mapping(sections.get("运作费用"))

    bulk_purchase_status = _text((bulk or {}).get("purchase_status"))
    detail_purchase_status = _text(status_pairs.get("申购状态"))
    bulk_purchase_state = normalize_purchase_state(bulk_purchase_status)
    detail_purchase_state = normalize_purchase_state(detail_purchase_status)
    purchase_state, source_conflict = _merge_state(
        bulk_purchase_state,
        detail_purchase_state,
    )

    bulk_redemption_status = _text((bulk or {}).get("redemption_status"))
    detail_redemption_status = _text(status_pairs.get("赎回状态"))
    redemption_state, redemption_conflict = _merge_state(
        normalize_redemption_state(bulk_redemption_status),
        normalize_redemption_state(detail_redemption_status),
    )
    source_conflict = source_conflict or redemption_conflict

    initial_minimum_candidates = [
        _finite_nonnegative((bulk or {}).get("minimum_purchase_yuan")),
        parse_money_yuan(amount_pairs.get("申购起点")),
        parse_money_yuan(amount_pairs.get("首次购买")),
    ]
    # The provider emits zero for several incompatible states (missing,
    # unavailable and true zero).  Never reinterpret it as "no minimum".
    initial_minimum_values = [
        value
        for value in initial_minimum_candidates
        if value is not None and value > 0
    ]
    initial_minimum_purchase = (
        max(initial_minimum_values) if initial_minimum_values else None
    )
    additional_minimum_purchase = parse_money_yuan(amount_pairs.get("追加购买"))
    if additional_minimum_purchase is not None and additional_minimum_purchase <= 0:
        additional_minimum_purchase = None

    bulk_limit, bulk_unlimited = normalize_purchase_limit(
        (bulk or {}).get("daily_purchase_limit_yuan")
    )
    detail_limit, detail_unlimited = normalize_purchase_limit(
        amount_pairs.get("日累计申购限额")
    )
    finite_limits = [value for value in (bulk_limit, detail_limit) if value is not None]
    daily_limit = min(finite_limits) if finite_limits else None
    daily_limit_unlimited = not finite_limits and (bulk_unlimited or detail_unlimited)

    purchase_fee_tiers = normalize_purchase_fee_tiers(sections.get("申购费率"))
    redemption_fee_tiers = normalize_redemption_fee_tiers(sections.get("赎回费率"))
    sales_service_fee = parse_percent(operating_pairs.get("销售服务费率"))
    sales_service_fee_status = _resolve_sales_service_fee_status(
        sales_service_fee,
        declared_status=None,
    )
    management_fee = parse_percent(operating_pairs.get("管理费率"))
    custody_fee = parse_percent(operating_pairs.get("托管费率"))

    bulk_checked_at = _text((bulk_snapshot or {}).get("retrieved_at"))
    detail_checked_at = _text((detail or {}).get("retrieved_at"))
    checked_at = _latest_iso_datetime(bulk_checked_at, detail_checked_at)
    purchase_status_checked_at = _latest_iso_datetime(
        bulk_checked_at if bulk_purchase_state != "unknown" else None,
        detail_checked_at if detail_purchase_state != "unknown" else None,
    )
    redemption_status_checked_at = _latest_iso_datetime(
        bulk_checked_at
        if normalize_redemption_state(bulk_redemption_status) != "unknown"
        else None,
        detail_checked_at
        if normalize_redemption_state(detail_redemption_status) != "unknown"
        else None,
    )
    purchase_status_freshness = _profile_freshness(
        purchase_status_checked_at,
        decision_at,
    )
    redemption_status_freshness = _profile_freshness(
        redemption_status_checked_at,
        decision_at,
    )
    freshness = _required_status_freshness(
        purchase_status_freshness,
        redemption_status_freshness,
    )
    status_checked_at = _earliest_iso_datetime(
        purchase_status_checked_at,
        redemption_status_checked_at,
    )
    fee_freshness = _profile_freshness_for_ttl(
        detail_checked_at,
        decision_at,
        ttl_seconds=max(
            1,
            int(get_settings().fund_tradeability_fee_cache_ttl_seconds),
        ),
    )
    source_ids: list[str] = []
    source_urls: list[str] = []
    if bulk is not None and bulk_snapshot is not None:
        _append_unique(source_ids, _text(bulk_snapshot.get("source")))
        _append_unique(source_urls, _text(bulk_snapshot.get("source_url")))
    if detail is not None and not detail.get("error"):
        _append_unique(source_ids, _text(detail.get("source")))
        _append_unique(source_urls, _text(detail.get("source_url")))

    missing_fields: list[str] = []
    if purchase_state == "unknown":
        missing_fields.append("purchase_state")
    if initial_minimum_purchase is None:
        missing_fields.append("minimum_purchase_yuan")
    if daily_limit is None and not daily_limit_unlimited:
        missing_fields.append("daily_purchase_limit_yuan")
    if not purchase_fee_tiers:
        missing_fields.append("standard_purchase_fee_tiers")
    if not redemption_fee_tiers:
        missing_fields.append("redemption_fee_tiers")
    if sales_service_fee_status == "unknown":
        missing_fields.append("sales_service_fee_annual_percent")

    critical_missing = {
        "purchase_state",
        "minimum_purchase_yuan",
        "daily_purchase_limit_yuan",
    }.intersection(missing_fields)
    if not source_ids or purchase_state == "unknown":
        data_status = "unavailable"
    elif critical_missing or missing_fields:
        data_status = "partial"
    else:
        data_status = "complete"
    if freshness != "fresh" and data_status != "unavailable":
        data_status = "stale"

    purchase_status = _join_source_values(
        bulk_purchase_status,
        detail_purchase_status,
    )
    redemption_status = _join_source_values(
        bulk_redemption_status,
        detail_redemption_status,
    )
    can_purchase: bool | None
    if purchase_state in {"open", "limited"}:
        can_purchase = True
    elif purchase_state in {"suspended", "closed"}:
        can_purchase = False
    else:
        can_purchase = None

    fee_status = (
        "standard_upper_bound_available"
        if (
            purchase_fee_tiers
            and redemption_fee_tiers
            and sales_service_fee_status != "unknown"
            and fee_freshness == "fresh"
        )
        else "unverified"
    )
    fund_name = _text((bulk or {}).get("fund_name"))
    explicit_minimum_holding_days = parse_explicit_minimum_holding_days(fund_name)
    result = {
        "schema_version": TRADEABILITY_SCHEMA_VERSION,
        "fund_code": _normalize_code(fund_code),
        "data_status": data_status,
        "freshness": freshness,
        "status_checked_at": status_checked_at,
        "purchase_status_checked_at": purchase_status_checked_at,
        "purchase_status_freshness": purchase_status_freshness,
        "redemption_status_checked_at": redemption_status_checked_at,
        "redemption_status_freshness": redemption_status_freshness,
        "can_purchase": can_purchase,
        "purchase_state": purchase_state,
        "purchase_status": purchase_status,
        "redemption_state": redemption_state,
        "redemption_status": redemption_status,
        "currency": "CNY" if initial_minimum_purchase is not None else "unknown",
        "fund_name": fund_name,
        "minimum_purchase_yuan": _rounded_money(initial_minimum_purchase),
        "minimum_initial_purchase_yuan": _rounded_money(initial_minimum_purchase),
        "minimum_additional_purchase_yuan": _rounded_money(
            additional_minimum_purchase
        ),
        "minimums": {
            "initial_yuan": _rounded_money(initial_minimum_purchase),
            "additional_yuan": _rounded_money(additional_minimum_purchase),
            "status": "known" if initial_minimum_purchase is not None else "unknown",
        },
        "daily_purchase_limit_yuan": _rounded_money(daily_limit),
        "daily_purchase_limit_unlimited": daily_limit_unlimited,
        "daily_purchase_limit_scope": "eastmoney_channel_display_unknown_remaining",
        "purchase_limit": {
            "amount_yuan": _rounded_money(daily_limit),
            "kind": (
                "finite"
                if daily_limit is not None
                else "unlimited"
                if daily_limit_unlimited
                else "unknown"
            ),
            "period": "day",
            "scope": "eastmoney_channel_display_unknown_remaining",
        },
        "revalidation_required": True,
        "next_open_date": _clean_date((bulk or {}).get("next_open_date")),
        "purchase_confirmation": _text(confirmation_pairs.get("买入确认日")),
        "redemption_confirmation": _text(confirmation_pairs.get("卖出确认日")),
        "explicit_minimum_holding_days": explicit_minimum_holding_days,
        "minimum_holding_period_status": (
            "explicit_from_fund_name"
            if explicit_minimum_holding_days is not None
            else "unverified"
        ),
        "listed_platform_purchase_fee_percent": _finite_nonnegative(
            (bulk or {}).get("listed_platform_purchase_fee_percent")
        ),
        "listed_platform_fee_semantics": (
            "provider_listed_discount_not_standard_upper_bound"
            if (bulk or {}).get("listed_platform_purchase_fee_percent") is not None
            else None
        ),
        "standard_purchase_fee_tiers": purchase_fee_tiers,
        "redemption_fee_tiers": redemption_fee_tiers,
        "sales_service_fee_annual_percent": sales_service_fee,
        "sales_service_fee_status": sales_service_fee_status,
        "management_fee_annual_percent": management_fee,
        "custody_fee_annual_percent": custody_fee,
        "share_class_fee_status": fee_status,
        "fee_checked_at": detail_checked_at,
        "fee_freshness": fee_freshness,
        "source_conflict": source_conflict,
        "missing_fields": missing_fields,
        "source_ids": source_ids,
        "source_urls": source_urls,
        "checked_at": checked_at,
        "effective_at": decision_at.isoformat(),
        "instruction": (
            "申购状态、起点和限额是执行门禁；标准申购费率按未折扣上限估算。"
            "平台实际折扣未知时不得宣称最终成交费或最低成本份额。"
        ),
    }
    result["tradeability_gate"] = build_tradeability_gate(result)
    return result


def build_tradeability_gate(
    tradeability: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project raw provider facts into the only contract allocators may consume."""

    value = tradeability if isinstance(tradeability, Mapping) else {}
    raw_initial = _finite_positive(
        value.get("minimum_initial_purchase_yuan", value.get("minimum_purchase_yuan"))
    )
    raw_additional = _finite_positive(value.get("minimum_additional_purchase_yuan"))
    effective_initial = (
        max(_SYSTEM_INITIAL_MINIMUM_PURCHASE_YUAN, raw_initial)
        if raw_initial is not None
        else None
    )
    limit = _finite_positive(value.get("daily_purchase_limit_yuan"))
    unlimited = value.get("daily_purchase_limit_unlimited") is True
    purchase_state = str(value.get("purchase_state") or "unknown")
    redemption_state = str(value.get("redemption_state") or "unknown")
    data_status = str(value.get("data_status") or "unavailable")
    freshness = str(value.get("freshness") or "unavailable")
    reason_codes: list[str] = []

    if purchase_state == "exchange_only":
        status = "excluded"
        reason_codes.append("exchange_only")
    else:
        status = "eligible"
        if data_status not in {"complete", "partial"} or freshness != "fresh":
            reason_codes.append("tradeability_not_fresh")
        if value.get("source_conflict") is True:
            reason_codes.append("source_conflict")
        if purchase_state not in {"open", "limited"}:
            reason_codes.append("purchase_not_open")
        if redemption_state != "open":
            reason_codes.append("redemption_not_open")
        if str(value.get("currency") or "unknown") != "CNY":
            reason_codes.append("currency_not_verified_cny")
        if effective_initial is None:
            reason_codes.append("initial_minimum_unknown")
        if limit is None and not unlimited:
            reason_codes.append("purchase_limit_unknown")
        if (
            limit is not None
            and effective_initial is not None
            and limit < effective_initial
        ):
            reason_codes.append("limit_below_effective_initial_minimum")
        if reason_codes:
            status = "watch_only"

    return {
        "schema_version": TRADEABILITY_GATE_SCHEMA_VERSION,
        "status": status,
        "effective_initial_min_purchase_yuan": _rounded_money(effective_initial),
        "effective_additional_min_purchase_yuan": _rounded_money(raw_additional),
        # Compatibility alias. New allocators must use the explicit initial field.
        "effective_min_purchase_yuan": _rounded_money(effective_initial),
        "max_purchase_yuan": _rounded_money(limit),
        "max_purchase_unlimited": unlimited,
        "max_period": "day",
        "max_scope": str(
            value.get("daily_purchase_limit_scope")
            or "eastmoney_channel_display_unknown_remaining"
        ),
        "revalidation_required": True,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def apply_tradeability_to_quality_gate(row: dict[str, Any]) -> dict[str, Any]:
    """Fold execution-critical tradeability into the existing quality gate."""

    result = dict(row)
    gate = dict(result.get("quality_gate") or {})
    tradeability = result.get("tradeability")
    reasons = [str(item) for item in gate.get("reasons") or [] if str(item).strip()]
    status = str(gate.get("status") or "watch_only")
    if status not in {"eligible", "watch_only", "excluded"}:
        status = "watch_only"

    if not isinstance(tradeability, Mapping):
        status = _downgrade_gate(status)
        reasons.append("申购状态与购买起点未核验，仅保留研究观察")
        trade_status = "unavailable"
    else:
        projected_gate = build_tradeability_gate(tradeability)
        if not isinstance(tradeability.get("tradeability_gate"), Mapping):
            enriched_tradeability = dict(tradeability)
            enriched_tradeability["tradeability_gate"] = projected_gate
            result["tradeability"] = enriched_tradeability
            tradeability = enriched_tradeability
        trade_status = str(tradeability.get("data_status") or "unavailable")
        purchase_state = str(tradeability.get("purchase_state") or "unknown")
        freshness = str(tradeability.get("freshness") or "unavailable")
        source_conflict = tradeability.get("source_conflict") is True
        minimum_purchase = _finite_positive(
            projected_gate.get("effective_initial_min_purchase_yuan")
        )
        limit = _finite_nonnegative(tradeability.get("daily_purchase_limit_yuan"))
        unlimited = tradeability.get("daily_purchase_limit_unlimited") is True
        if source_conflict:
            status = _downgrade_gate(status)
            reasons.append("申购状态双源冲突，仅保留研究观察")
        elif freshness != "fresh" or trade_status in {"unavailable", "stale"}:
            status = _downgrade_gate(status)
            reasons.append("申购状态证据不可用或已过期，仅保留研究观察")
        elif purchase_state == "exchange_only":
            status = "excluded"
            reasons.append("当前为场内交易份额，不适用于本产品的场外申购链路")
        elif purchase_state not in {"open", "limited"}:
            status = _downgrade_gate(status)
            label = _text(tradeability.get("purchase_status")) or "不可申购"
            reasons.append(f"当前申购状态为“{label}”，未生成买入动作")
        elif str(tradeability.get("redemption_state") or "unknown") != "open":
            status = _downgrade_gate(status)
            reasons.append("赎回状态未开放或不可核验，仅保留研究观察")
        elif str(tradeability.get("currency") or "unknown") != "CNY":
            status = _downgrade_gate(status)
            reasons.append("申购币种不是已核验人民币，预算口径无法闭合")
        elif minimum_purchase is None:
            status = _downgrade_gate(status)
            reasons.append("最低申购金额不可核验，仅保留研究观察")
        elif limit is None and not unlimited:
            status = _downgrade_gate(status)
            reasons.append("单日申购限额不可核验，仅保留研究观察")
        elif limit is not None and limit < minimum_purchase:
            status = _downgrade_gate(status)
            reasons.append("当前单日限额低于最低申购金额，无法形成可执行买入")

    gate.update(
        {
            "eligible": status == "eligible",
            "status": status,
            "reasons": _unique_text(reasons),
            "tradeability_status": trade_status,
            "tradeability_checked_at": (
                tradeability.get("checked_at") if isinstance(tradeability, Mapping) else None
            ),
            "purchase_state": (
                tradeability.get("purchase_state") if isinstance(tradeability, Mapping) else "unknown"
            ),
            "tradeability_gate_status": (
                build_tradeability_gate(tradeability).get("status")
                if isinstance(tradeability, Mapping)
                else "watch_only"
            ),
        }
    )
    result["quality_gate"] = gate
    return result


def assess_tradeability_for_amount(
    tradeability: Mapping[str, Any] | None,
    *,
    amount_yuan: float | int | None,
    hold_horizon: str | None,
    minimum_holding_days: int | None = None,
) -> dict[str, Any]:
    """Assess the final, post-cap amount and conservative holding horizon."""

    amount = _finite_nonnegative(amount_yuan)
    horizon_days = (
        max(0, int(minimum_holding_days))
        if minimum_holding_days is not None
        else parse_hold_horizon_min_days(hold_horizon)
    )
    block_reasons: list[str] = []
    notes: list[str] = []
    if not isinstance(tradeability, Mapping):
        return _blocked_cost_assessment(
            amount,
            horizon_days,
            ["tradeability_unavailable"],
            ["申购状态与费用规则不可核验"],
        )

    data_status = str(tradeability.get("data_status") or "unavailable")
    freshness = str(tradeability.get("freshness") or "unavailable")
    purchase_state = str(tradeability.get("purchase_state") or "unknown")
    if data_status in {"unavailable", "stale"} or freshness != "fresh":
        block_reasons.append("tradeability_not_fresh")
        notes.append("申购状态证据不可用或已过期")
    if tradeability.get("source_conflict") is True:
        block_reasons.append("tradeability_source_conflict")
        notes.append("申购状态双源冲突")
    if purchase_state not in {"open", "limited"}:
        block_reasons.append("purchase_not_open")
        notes.append("当前份额未处于可申购状态")
    if str(tradeability.get("redemption_state") or "unknown") != "open":
        block_reasons.append("redemption_not_open")
        notes.append("当前份额未处于可赎回状态")
    if str(tradeability.get("currency") or "unknown") != "CNY":
        block_reasons.append("currency_not_verified_cny")
        notes.append("申购币种未核验为人民币")
    if amount is None or amount <= 0:
        block_reasons.append("invalid_amount")
        notes.append("建议金额不是有限正数")

    tradeability_gate = (
        tradeability.get("tradeability_gate")
        if isinstance(tradeability.get("tradeability_gate"), Mapping)
        else build_tradeability_gate(tradeability)
    )
    minimum = _finite_positive(
        tradeability_gate.get("effective_initial_min_purchase_yuan")
    )
    if minimum is None:
        block_reasons.append("minimum_purchase_unknown")
        notes.append("最低申购金额不可核验")
    elif amount is not None and amount < minimum:
        block_reasons.append("below_minimum_purchase")
        notes.append(f"建议金额低于最低申购金额 {minimum:.2f} 元")

    limit = _finite_nonnegative(tradeability.get("daily_purchase_limit_yuan"))
    unlimited = tradeability.get("daily_purchase_limit_unlimited") is True
    if limit is None and not unlimited:
        block_reasons.append("daily_purchase_limit_unknown")
        notes.append("单日申购限额不可核验")
    elif amount is not None and limit is not None and amount > limit:
        block_reasons.append("above_daily_purchase_limit")
        notes.append(f"建议金额超过单日申购限额 {limit:.2f} 元")

    if horizon_days is None:
        block_reasons.append("holding_horizon_unparseable")
        notes.append("持有期无法换算，不能核验赎回费")

    explicit_minimum_holding_days = _finite_nonnegative(
        tradeability.get("explicit_minimum_holding_days")
    )
    if (
        horizon_days is not None
        and explicit_minimum_holding_days is not None
        and horizon_days < explicit_minimum_holding_days
    ):
        block_reasons.append("below_fund_minimum_holding_period")
        notes.append(
            "用户预设最短持有期低于基金名称明确标示的"
            f" {int(explicit_minimum_holding_days)} 天持有期"
        )

    fee_freshness = str(
        tradeability.get("fee_freshness")
        or tradeability.get("freshness")
        or "unavailable"
    )
    fee_rules_usable = fee_freshness == "fresh"
    purchase_fee = resolve_purchase_fee(
        list(tradeability.get("standard_purchase_fee_tiers") or [])
        if fee_rules_usable
        else [],
        amount or 0.0,
    )
    redemption_fee_percent = resolve_redemption_fee_percent(
        list(tradeability.get("redemption_fee_tiers") or [])
        if fee_rules_usable
        else [],
        horizon_days,
    )
    sales_service_fee_status = _resolve_sales_service_fee_status(
        tradeability.get("sales_service_fee_annual_percent"),
        declared_status=tradeability.get("sales_service_fee_status"),
    )
    sales_service_annual = (
        _finite_nonnegative(tradeability.get("sales_service_fee_annual_percent"))
        if sales_service_fee_status != "unknown"
        else None
    )
    sales_service_period = (
        sales_service_annual * horizon_days / 365
        if sales_service_annual is not None and horizon_days is not None
        else None
    )
    fee_component_status = {
        "purchase_fee": "known" if purchase_fee is not None else "unknown",
        "redemption_fee": (
            "known" if redemption_fee_percent is not None else "unknown"
        ),
        "sales_service_fee": sales_service_fee_status,
    }
    fee_verified = bool(
        purchase_fee is not None
        and redemption_fee_percent is not None
        and sales_service_fee_status != "unknown"
        and sales_service_period is not None
    )
    if not fee_verified:
        block_reasons.append("transaction_cost_incomplete")
        notes.append("三类份额费用未全部核验，不能形成可执行总成本")
    if purchase_fee is None:
        block_reasons.append("purchase_fee_unverified")
        notes.append("标准申购费仍需执行前核验")
    if redemption_fee_percent is None:
        block_reasons.append("redemption_fee_unverified")
        notes.append("持有期赎回费仍需执行前核验")
    if sales_service_fee_status == "unknown":
        block_reasons.append("sales_service_fee_unknown")
        notes.append("销售服务费率未知，不得按 0 计入总成本")

    purchase_fee_yuan = purchase_fee.get("fee_yuan") if purchase_fee else None
    purchase_fee_equivalent = (
        purchase_fee_yuan / amount * 100
        if purchase_fee_yuan is not None and amount is not None and amount > 0
        else None
    )
    total_cost_percent = (
        purchase_fee_equivalent
        + redemption_fee_percent
        + sales_service_period
        if (
            fee_verified
            and purchase_fee_equivalent is not None
            and redemption_fee_percent is not None
            and sales_service_period is not None
        )
        else None
    )

    if horizon_days is not None and horizon_days < _MINIMUM_SAFE_HOLD_DAYS:
        block_reasons.append("holding_period_below_7_days")
        notes.append("最短持有期不足 7 天，短持赎回成本门禁未通过")
    if horizon_days is not None and horizon_days < _SHORT_HORIZON_DAYS:
        if explicit_minimum_holding_days is None:
            block_reasons.append("short_horizon_minimum_holding_period_unverified")
            notes.append("短周期方案尚无可核验的最低持有期证据")
        if not fee_verified:
            block_reasons.append("short_horizon_cost_unverified")
            notes.append("短周期方案的交易费用不可核验")
        elif total_cost_percent is not None and total_cost_percent >= _SHORT_HORIZON_COST_CEILING_PERCENT:
            block_reasons.append("short_horizon_cost_too_high")
            notes.append(
                f"按标准费率上限估算的短周期成本约 {total_cost_percent:.2f}% ，超过 1.00% 门槛"
            )

    return {
        "schema_version": COST_SCHEMA_VERSION,
        "executable": not block_reasons,
        "amount_yuan": _rounded_money(amount),
        "hold_horizon": _text(hold_horizon),
        "minimum_holding_days": horizon_days,
        "fund_minimum_holding_days": (
            int(explicit_minimum_holding_days)
            if explicit_minimum_holding_days is not None
            else None
        ),
        "minimum_purchase_yuan": _rounded_money(minimum),
        "tradeability_gate": dict(tradeability_gate),
        "daily_purchase_limit_yuan": _rounded_money(limit),
        "daily_purchase_limit_unlimited": unlimited,
        "purchase_fee_standard_upper_bound": purchase_fee,
        "redemption_fee_percent_at_minimum_horizon": redemption_fee_percent,
        "sales_service_fee_percent_for_minimum_horizon": (
            round(sales_service_period, 6) if sales_service_period is not None else None
        ),
        "sales_service_fee_status": sales_service_fee_status,
        "fee_component_status": fee_component_status,
        "fee_components_complete": fee_verified,
        "cost_comparison_status": "complete" if fee_verified else "incomplete",
        "estimated_total_cost_upper_bound_percent": (
            round(total_cost_percent, 6) if total_cost_percent is not None else None
        ),
        "fee_status": (
            "standard_upper_bound_available" if fee_verified else "execution_verification_required"
        ),
        "block_reasons": list(dict.fromkeys(block_reasons)),
        "notes": _unique_text(notes),
        "source_ids": list(tradeability.get("source_ids") or []),
        "checked_at": tradeability.get("checked_at"),
        "fee_checked_at": tradeability.get("fee_checked_at"),
        "fee_freshness": fee_freshness,
        "instruction": (
            "费用使用未折扣标准费率作保守上限；实际销售平台费率、到账日与最终成交条件仍须下单前核验。"
        ),
    }


def compact_tradeability_for_llm(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "data_status": "unavailable",
            "purchase_state": "unknown",
            "instruction": "申购状态不可核验，只能研究观察。",
        }
    keys = (
        "schema_version",
        "fund_code",
        "data_status",
        "freshness",
        "status_checked_at",
        "purchase_status_checked_at",
        "purchase_status_freshness",
        "redemption_status_checked_at",
        "redemption_status_freshness",
        "can_purchase",
        "purchase_state",
        "purchase_status",
        "redemption_state",
        "redemption_status",
        "currency",
        "fund_name",
        "minimum_purchase_yuan",
        "minimum_initial_purchase_yuan",
        "minimum_additional_purchase_yuan",
        "minimums",
        "daily_purchase_limit_yuan",
        "daily_purchase_limit_unlimited",
        "daily_purchase_limit_scope",
        "purchase_limit",
        "tradeability_gate",
        "explicit_minimum_holding_days",
        "minimum_holding_period_status",
        "revalidation_required",
        "next_open_date",
        "share_class_fee_status",
        "fee_checked_at",
        "fee_freshness",
        "sales_service_fee_annual_percent",
        "sales_service_fee_status",
        "source_conflict",
        "missing_fields",
        "checked_at",
        "effective_at",
        "source_ids",
    )
    compact = {key: value.get(key) for key in keys if value.get(key) is not None}
    compact["standard_purchase_fee_tiers"] = list(
        value.get("standard_purchase_fee_tiers") or []
    )[:5]
    compact["redemption_fee_tiers"] = list(value.get("redemption_fee_tiers") or [])[:6]
    compact["instruction"] = value.get("instruction")
    return compact


def normalize_purchase_state(value: object) -> str:
    text = _text(value) or ""
    if not text:
        return "unknown"
    if any(token in text for token in ("暂停", "停止", "不可申购")):
        return "suspended"
    if "场内交易" in text:
        return "exchange_only"
    if "认购期" in text:
        return "subscription_period"
    if "封闭" in text:
        return "closed"
    if any(token in text for token in ("限大额", "限制大额", "限额")):
        return "limited"
    if "开放" in text and "申购" in text:
        return "open"
    return "unknown"


def normalize_redemption_state(value: object) -> str:
    text = _text(value) or ""
    if not text:
        return "unknown"
    if any(token in text for token in ("暂停", "停止", "不可赎回")):
        return "suspended"
    if "场内交易" in text:
        return "exchange_only"
    if "封闭" in text:
        return "closed"
    if "开放" in text and "赎回" in text:
        return "open"
    return "unknown"


def parse_money_yuan(value: object) -> float | None:
    text = _text(value)
    if text is None or any(token in text for token in ("无限", "不限")):
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(亿元|万元|元)?", text.replace(",", ""))
    if match is None:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "元"
    multiplier = {"元": 1.0, "万元": 10_000.0, "亿元": 100_000_000.0}[unit]
    resolved = number * multiplier
    return resolved if isfinite(resolved) and resolved >= 0 else None


def normalize_purchase_limit(value: object) -> tuple[float | None, bool]:
    text = _text(value)
    if text and any(token in text for token in ("无限", "不限")):
        return None, True
    parsed = parse_money_yuan(value) if isinstance(value, str) else _finite_nonnegative(value)
    if parsed is not None and parsed >= _UNLIMITED_SENTINEL_YUAN:
        return None, True
    return parsed, False


def parse_percent(value: object) -> float | None:
    text = _text(value)
    if text is None or text in {"--", "---"}:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if match is None:
        return None
    parsed = float(match.group(1))
    return parsed if isfinite(parsed) and parsed >= 0 else None


def normalize_purchase_fee_tiers(value: object) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        condition = _text(row.get("condition"))
        raw_rate = _text(row.get("standard_rate"))
        if not condition or not raw_rate:
            continue
        bounds = _parse_money_bounds(condition)
        percent = parse_percent(raw_rate)
        flat = _parse_flat_fee(raw_rate)
        if percent is None and flat is None:
            continue
        output.append(
            {
                "condition": condition,
                **bounds,
                "fee_type": "percent" if percent is not None else "flat",
                "fee_percent": percent,
                "flat_fee_yuan": flat,
                "source_rate": "standard_undiscounted",
            }
        )
    return output


def normalize_redemption_fee_tiers(value: object) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        condition = _text(row.get("condition"))
        rate = parse_percent(row.get("rate"))
        if not condition or rate is None:
            continue
        output.append(
            {
                "condition": condition,
                **_parse_day_bounds(condition),
                "fee_percent": rate,
            }
        )
    return output


def resolve_purchase_fee(
    tiers: list[Mapping[str, Any]],
    amount_yuan: float,
) -> dict[str, Any] | None:
    amount = _finite_nonnegative(amount_yuan)
    if amount is None:
        return None
    for tier in tiers:
        if not _amount_in_tier(amount, tier):
            continue
        fee_type = str(tier.get("fee_type") or "")
        if fee_type == "percent":
            rate = _finite_nonnegative(tier.get("fee_percent"))
            if rate is None:
                return None
            fee_yuan = amount - amount / (1 + rate / 100)
        elif fee_type == "flat":
            fee_yuan = _finite_nonnegative(tier.get("flat_fee_yuan"))
            rate = None
            if fee_yuan is None:
                return None
        else:
            return None
        return {
            "fee_type": fee_type,
            "fee_percent": rate,
            "flat_fee_yuan": tier.get("flat_fee_yuan"),
            "fee_yuan": round(fee_yuan, 2),
            "condition": tier.get("condition"),
            "source_rate": "standard_undiscounted",
        }
    return None


def resolve_redemption_fee_percent(
    tiers: list[Mapping[str, Any]],
    holding_days: int | None,
) -> float | None:
    if holding_days is None or holding_days < 0:
        return None
    for tier in tiers:
        minimum = _int_or_none(tier.get("min_days"))
        maximum = _int_or_none(tier.get("max_days"))
        if minimum is not None and holding_days < minimum:
            continue
        if maximum is not None and holding_days >= maximum:
            continue
        return _finite_nonnegative(tier.get("fee_percent"))
    return None


def parse_explicit_minimum_holding_days(value: object) -> int | None:
    """Extract only an affirmative holding-period label; absence stays unknown."""

    text = _text(value)
    if text is None:
        return None
    normalized = _normalize_chinese_duration_text(text)
    matches = list(
        re.finditer(
            r"(\d+(?:\.\d+)?)\s*(年|个月|月|周|天|日)\s*(?:最短)?持有(?:期)?",
            normalized,
        )
    )
    if not matches:
        return None
    days = [
        int(float(match.group(1)) * _duration_multiplier(match.group(2)))
        for match in matches
    ]
    positive = [value for value in days if value > 0]
    return max(positive) if positive else None


def parse_hold_horizon_min_days(value: object) -> int | None:
    text = (_text(value) or "").replace("～", "-").replace("—", "-")
    if not text:
        return None
    text = _normalize_chinese_duration_text(text)
    range_match = re.search(
        r"(\d+)\s*(?:-|~|至|到)\s*(\d+)\s*(个?月|周|天|年)",
        text,
    )
    if range_match:
        return int(range_match.group(1)) * _duration_multiplier(range_match.group(3))
    single_match = re.search(r"(\d+)\s*(个?月|周|天|年)", text)
    if single_match:
        return int(single_match.group(1)) * _duration_multiplier(single_match.group(2))
    if "中长期" in text:
        return 180
    if "长期" in text:
        return 365
    durations = [
        int(number) * _duration_multiplier(unit)
        for number, unit in re.findall(r"(\d+)\s*(个?月|周|天|年)", text)
    ]
    if durations:
        return min(durations)
    return None


def resolve_profile_min_holding_days(profile: object) -> int | None:
    """Use deterministic user settings, never an LLM-written horizon, for fees."""

    preset = str(getattr(profile, "investment_preset", "") or "")
    target = _int_or_none(getattr(profile, "hold_days_target", None))
    if preset == "aggressive_swing" and target is not None and target > 0:
        return target
    parsed = parse_hold_horizon_min_days(getattr(profile, "horizon", None))
    if parsed is not None:
        return parsed
    if target is not None and target > 0:
        return target
    return None


def _load_purchase_snapshot(
    decision_at: datetime,
    *,
    current_request: bool,
    fetcher: Callable[[], Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    settings = get_settings()
    ttl = max(1, int(settings.fund_tradeability_status_cache_ttl_seconds))
    if current_request:
        cached = get_spot_snapshot(_PURCHASE_CACHE_KEY, ttl_seconds=ttl)
        if _valid_purchase_snapshot(cached):
            return cached
        fetched = fetcher()
        if _valid_purchase_snapshot(fetched):
            payload = dict(fetched)
            save_spot_snapshot(_PURCHASE_CACHE_KEY, payload)
            return payload
        return None
    cached = get_spot_snapshot_any_age(_PURCHASE_CACHE_KEY)
    if _snapshot_usable_for_decision(cached, decision_at, ttl_seconds=ttl):
        return cached
    return None


def _load_fee_snapshots(
    codes: list[str],
    decision_at: datetime,
    *,
    current_request: bool,
    fetcher: Callable[[list[str]], Mapping[str, Any] | None],
    refresh_detail_status: bool = False,
) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    ttl = max(1, int(settings.fund_tradeability_fee_cache_ttl_seconds))
    resolved: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for code in codes:
        key = f"{_FEE_CACHE_PREFIX}:{code}"
        cached = (
            get_spot_snapshot(key, ttl_seconds=ttl)
            if current_request
            else get_spot_snapshot_any_age(key)
        )
        usable = (
            _valid_fee_snapshot(cached)
            if current_request
            else _snapshot_usable_for_decision(cached, decision_at, ttl_seconds=ttl)
        )
        if usable and isinstance(cached, dict):
            resolved[code] = cached
        elif current_request:
            missing.append(code)
    refresh_codes = list(missing)
    if current_request and refresh_detail_status:
        status_ttl = max(
            1,
            int(get_settings().fund_tradeability_status_cache_ttl_seconds),
        )
        refresh_codes.extend(
            code
            for code in codes
            if code in resolved
            and not _detail_status_snapshot_fresh(
                resolved[code],
                decision_at=decision_at,
                ttl_seconds=status_ttl,
            )
        )
    refresh_codes = list(dict.fromkeys(refresh_codes))
    if refresh_codes:
        fetched = fetcher(refresh_codes)
        for code in refresh_codes:
            payload = fetched.get(code) if isinstance(fetched, Mapping) else None
            if not _valid_fee_snapshot(payload):
                continue
            normalized = dict(payload)
            save_spot_snapshot(f"{_FEE_CACHE_PREFIX}:{code}", normalized)
            resolved[code] = normalized
    return resolved


def _refresh_stale_detail_status_snapshots(
    codes: list[str],
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    decision_at: datetime,
    fetcher: Callable[[list[str]], Mapping[str, Any] | None],
) -> dict[str, dict[str, Any]]:
    """Refresh cached F10 pages only when they are the live status fallback."""

    resolved = {
        code: dict(payload)
        for code, payload in snapshots.items()
        if isinstance(payload, Mapping)
    }
    ttl = max(1, int(get_settings().fund_tradeability_status_cache_ttl_seconds))
    stale_codes = [
        code
        for code in codes
        if code in resolved
        and not _detail_status_snapshot_fresh(
            resolved[code],
            decision_at=decision_at,
            ttl_seconds=ttl,
        )
    ]
    if not stale_codes:
        return resolved

    fetched = fetcher(stale_codes)
    for code in stale_codes:
        payload = fetched.get(code) if isinstance(fetched, Mapping) else None
        if not _valid_fee_snapshot(payload):
            continue
        normalized = dict(payload)
        save_spot_snapshot(f"{_FEE_CACHE_PREFIX}:{code}", normalized)
        resolved[code] = normalized
    return resolved


def _detail_status_snapshot_fresh(
    payload: Mapping[str, Any],
    *,
    decision_at: datetime,
    ttl_seconds: int,
) -> bool:
    sections = _mapping(payload.get("sections"))
    statuses = _mapping(sections.get("交易状态"))
    if (
        normalize_purchase_state(statuses.get("申购状态")) == "unknown"
        or normalize_redemption_state(statuses.get("赎回状态")) == "unknown"
    ):
        return False
    return (
        _profile_freshness_for_ttl(
            _text(payload.get("retrieved_at")),
            decision_at,
            ttl_seconds=ttl_seconds,
        )
        == "fresh"
    )


def _fetch_purchase_snapshot() -> Mapping[str, Any] | None:
    settings = get_settings()
    payload = run_akshare_json_script(
        _PURCHASE_FETCH_SCRIPT,
        label="fund tradeability purchase status",
        timeout=max(1.0, float(settings.fund_tradeability_status_timeout_seconds)),
    )
    return payload if isinstance(payload, Mapping) else None


def _fetch_fee_snapshots(codes: list[str]) -> Mapping[str, Any] | None:
    if not codes:
        return {}
    settings = get_settings()
    script = _FEE_FETCH_SCRIPT.replace(
        "__CODES_JSON__",
        json.dumps(codes, ensure_ascii=True),
    )
    payload = run_akshare_json_script(
        script,
        label=f"fund fee rules:{len(codes)}",
        timeout=max(1.0, float(settings.fund_tradeability_fee_timeout_seconds)),
    )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    return data if isinstance(data, Mapping) else None


def _is_current_decision(decision_at: datetime, wall_now: datetime) -> bool:
    window = max(1, int(get_settings().fund_tradeability_current_window_seconds))
    return abs((wall_now - decision_at).total_seconds()) <= window


def _snapshot_usable_for_decision(
    payload: object,
    decision_at: datetime,
    *,
    ttl_seconds: int,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    retrieved_at = _parse_datetime(payload.get("retrieved_at"))
    if retrieved_at is None or retrieved_at > decision_at:
        return False
    age = (decision_at - retrieved_at).total_seconds()
    return 0 <= age <= ttl_seconds


def _valid_purchase_snapshot(payload: object) -> bool:
    return bool(
        isinstance(payload, Mapping)
        and _parse_datetime(payload.get("retrieved_at")) is not None
        and isinstance(payload.get("rows"), Mapping)
        and payload.get("rows")
    )


def _valid_fee_snapshot(payload: object) -> bool:
    return bool(
        isinstance(payload, Mapping)
        and not payload.get("error")
        and _parse_datetime(payload.get("retrieved_at")) is not None
        and isinstance(payload.get("sections"), Mapping)
        and payload.get("sections")
    )


def _profile_freshness(checked_at: str | None, decision_at: datetime) -> str:
    return _profile_freshness_for_ttl(
        checked_at,
        decision_at,
        ttl_seconds=max(
            1,
            int(get_settings().fund_tradeability_status_cache_ttl_seconds),
        ),
    )


def _profile_freshness_for_ttl(
    checked_at: str | None,
    decision_at: datetime,
    *,
    ttl_seconds: int,
) -> str:
    observed = _parse_datetime(checked_at)
    if observed is None:
        return "unavailable"
    settings = get_settings()
    current_window = timedelta(
        seconds=max(1, int(settings.fund_tradeability_current_window_seconds))
    )
    # A current request necessarily retrieves data a few seconds after its
    # immutable decision clock.  That bounded retrieval window is part of the
    # request snapshot; a later historical replay is not.
    if observed > decision_at + current_window:
        return "future"
    age = (decision_at - observed).total_seconds()
    return "fresh" if age <= max(1, int(ttl_seconds)) else "stale"


def _required_status_freshness(*values: str) -> str:
    if values and all(value == "fresh" for value in values):
        return "fresh"
    if "future" in values:
        return "future"
    if "unavailable" in values:
        return "unavailable"
    return "stale"


def _merge_state(first: str, second: str) -> tuple[str, bool]:
    known = [value for value in (first, second) if value != "unknown"]
    if not known:
        return "unknown", False
    if len(set(known)) == 1:
        return known[0], False
    return "unknown", True


def _parse_money_bounds(condition: str) -> dict[str, Any]:
    minimum: float | None = None
    maximum: float | None = None
    min_inclusive = True
    max_inclusive = False
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(亿元|万元|元)")
    for match in pattern.finditer(condition.replace(",", "")):
        number = float(match.group(1))
        multiplier = {"元": 1.0, "万元": 10_000.0, "亿元": 100_000_000.0}[
            match.group(2)
        ]
        amount = number * multiplier
        prefix = condition[max(0, match.start() - 7) : match.start()]
        if "大于等于" in prefix or "不少于" in prefix:
            minimum = amount
            min_inclusive = True
        elif "大于" in prefix or "超过" in prefix:
            minimum = amount
            min_inclusive = False
        if "小于等于" in prefix or "不超过" in prefix:
            maximum = amount
            max_inclusive = True
        elif "小于" in prefix:
            maximum = amount
            max_inclusive = False
    return {
        "min_amount_yuan": minimum,
        "max_amount_yuan": maximum,
        "min_inclusive": min_inclusive,
        "max_inclusive": max_inclusive,
    }


def _parse_day_bounds(condition: str) -> dict[str, Any]:
    minimum: int | None = None
    maximum: int | None = None
    for match in re.finditer(r"(\d+)\s*(天|日|个月|月|年)", condition):
        value = int(match.group(1)) * _duration_multiplier(match.group(2))
        prefix = condition[max(0, match.start() - 7) : match.start()]
        if "大于等于" in prefix or "不少于" in prefix:
            minimum = value
        elif "大于" in prefix or "超过" in prefix:
            minimum = value + 1
        if "小于等于" in prefix or "不超过" in prefix:
            maximum = value + 1
        elif "小于" in prefix:
            maximum = value
    return {"min_days": minimum, "max_days": maximum}


def _amount_in_tier(amount: float, tier: Mapping[str, Any]) -> bool:
    minimum = _finite_nonnegative(tier.get("min_amount_yuan"))
    maximum = _finite_nonnegative(tier.get("max_amount_yuan"))
    if minimum is not None:
        if tier.get("min_inclusive", True) and amount < minimum:
            return False
        if not tier.get("min_inclusive", True) and amount <= minimum:
            return False
    if maximum is not None:
        if tier.get("max_inclusive", False) and amount > maximum:
            return False
        if not tier.get("max_inclusive", False) and amount >= maximum:
            return False
    return True


def _parse_flat_fee(value: str) -> float | None:
    match = re.search(r"每\s*笔\s*(\d+(?:\.\d+)?)\s*元", value)
    if match is None:
        return None
    parsed = float(match.group(1))
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _blocked_cost_assessment(
    amount: float | None,
    horizon_days: int | None,
    reasons: list[str],
    notes: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": COST_SCHEMA_VERSION,
        "executable": False,
        "amount_yuan": _rounded_money(amount),
        "minimum_holding_days": horizon_days,
        "fee_status": "unavailable",
        "sales_service_fee_status": "unknown",
        "fee_component_status": {
            "purchase_fee": "unknown",
            "redemption_fee": "unknown",
            "sales_service_fee": "unknown",
        },
        "fee_components_complete": False,
        "cost_comparison_status": "incomplete",
        "block_reasons": reasons,
        "notes": notes,
    }


def _normalize_chinese_duration_text(value: str) -> str:
    text = value.replace("半年", "6个月")

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9}
        if token == "十":
            number = 10
        elif "十" in token:
            left, right = token.split("十", 1)
            number = (digits.get(left, 1) if left else 1) * 10 + (
                digits.get(right, 0) if right else 0
            )
        else:
            number = digits.get(token)
        return str(number) if number is not None else token

    return re.sub(r"([一二两三四五六七八九十]+)(?=个?月|周|天|年)", replace, text)


def _duration_multiplier(unit: str) -> int:
    if unit in {"天", "日"}:
        return 1
    if unit == "周":
        return 7
    if unit in {"月", "个月"}:
        return 30
    if unit == "年":
        return 365
    return 1


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _latest_iso_datetime(*values: str | None) -> str | None:
    parsed = [(_parse_datetime(value), value) for value in values if value]
    valid = [(moment, value) for moment, value in parsed if moment is not None]
    if not valid:
        return None
    return max(valid, key=lambda item: item[0])[1]


def _earliest_iso_datetime(*values: str | None) -> str | None:
    parsed = [(_parse_datetime(value), value) for value in values if value]
    valid = [(moment, value) for moment, value in parsed if moment is not None]
    if not valid:
        return None
    return min(valid, key=lambda item: item[0])[1]


def _join_source_values(first: str | None, second: str | None) -> str | None:
    values = list(dict.fromkeys(value for value in (first, second) if value))
    return " / ".join(values) if values else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _resolve_sales_service_fee_status(
    value: object,
    *,
    declared_status: object,
) -> str:
    """Resolve a three-state fee contract without treating missing as zero.

    Older frozen payloads predate ``sales_service_fee_status``.  They remain
    compatible when they contain an explicit finite numeric fee: ``0`` is a
    verified zero and a positive value is verified positive.  A missing,
    malformed, negative, or declaration/value-conflicting field is unknown.
    """

    parsed = None if isinstance(value, bool) else _finite_nonnegative(value)
    derived = (
        "known_zero"
        if parsed == 0
        else "known_positive"
        if parsed is not None and parsed > 0
        else "unknown"
    )
    declared = _text(declared_status)
    if declared is None:
        return derived
    if declared not in _SALES_SERVICE_FEE_STATUSES:
        return "unknown"
    if declared == "unknown":
        return "unknown"
    return declared if declared == derived else "unknown"


def _finite_nonnegative(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _finite_positive(value: object) -> float | None:
    parsed = _finite_nonnegative(value)
    return parsed if parsed is not None and parsed > 0 else None


def _rounded_money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _normalize_code(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    code = text.zfill(6)
    return code if code.isdigit() and len(code) == 6 and code != "000000" else ""


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in {"", "nan", "nat", "none"} else text


def _clean_date(value: object) -> str | None:
    text = _text(value)
    if text is None or text in {"NaT", "--", "---"}:
        return None
    return text[:10]


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _unique_text(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if str(value).strip()))


def _downgrade_gate(status: str) -> str:
    return "excluded" if status == "excluded" else "watch_only"


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
