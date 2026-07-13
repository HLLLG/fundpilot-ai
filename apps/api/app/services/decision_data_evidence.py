from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from collections.abc import Mapping
from typing import Any

from app.models import DataEvidence, FundSnapshot, Holding
from app.request_context import try_get_request_user_id
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.trading_session import build_trading_session


class StalePortfolioSnapshotError(ValueError):
    """Raised when a decision would use a portfolio older than its trade date."""


@dataclass(frozen=True)
class PortfolioPreflightResult:
    holdings: list[Holding]
    context: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _position_payload(holding: Holding) -> dict[str, Any]:
    """Only hash position truth; intraday quotes must not create false mismatches."""
    # Holding currently has market value but no stable shares/ledger version.
    # Until that contract exists, member identity is the only non-valuation
    # position truth we can compare without false mismatches on every NAV move.
    return {"fund_code": holding.fund_code.strip().zfill(6)}


def holdings_fingerprint(holdings: list[Holding]) -> str:
    rows = sorted(
        (_position_payload(holding) for holding in holdings),
        key=lambda row: row["fund_code"],
    )
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compact_portfolio_position_truth(
    portfolio_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Expose decision-relevant truth without leaking the full ledger history."""

    if not isinstance(portfolio_context, Mapping):
        return None
    snapshot = portfolio_context.get("position_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    rows: list[dict[str, Any]] = []
    for raw in snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "fund_code": raw.get("fund_code"),
                "fund_name": raw.get("fund_name"),
                "settled_shares": raw.get("settled_shares"),
                "shares_quality": raw.get("shares_quality"),
                "market_value_yuan": raw.get("market_value_cny"),
                "cost_basis_total_yuan": raw.get("cost_basis_total_cny"),
                "cost_quality": raw.get("cost_quality"),
                "fee_complete": bool(raw.get("fee_complete")),
            }
        )
    cash = snapshot.get("cash") if isinstance(snapshot.get("cash"), Mapping) else {}
    completeness = (
        snapshot.get("completeness")
        if isinstance(snapshot.get("completeness"), Mapping)
        else {}
    )
    return {
        "schema_version": "portfolio_position_truth.compact.v1",
        "snapshot_id": snapshot.get("snapshot_id"),
        "ledger_version": snapshot.get("ledger_version"),
        "position_as_of": snapshot.get("position_as_of"),
        "position_complete": bool(snapshot.get("position_complete")),
        "position_truth_status": completeness.get("position_truth_status"),
        "pending_transaction_count": int(
            snapshot.get("pending_transaction_count") or 0
        ),
        "known_unsettled_transaction_count": int(
            snapshot.get("known_unsettled_transaction_count") or 0
        ),
        "conflict_count": int(completeness.get("conflict_count") or 0),
        "ledger_truncated": bool(
            snapshot.get("ledger_truncated") or completeness.get("ledger_truncated")
        ),
        "cash": {
            "balance_yuan": cash.get("balance_cny"),
            "known": bool(cash.get("known")),
            "quality": cash.get("quality") if cash.get("known") else "unknown",
        },
        "total_market_value_yuan": (snapshot.get("totals") or {}).get(
            "invested_market_value_cny"
        )
        if isinstance(snapshot.get("totals"), Mapping)
        else None,
        "positions": rows,
        "instruction": (
            "份额、现金和成本只可使用本对象；null/unknown 不得按 0 猜测。"
            "position_complete=false、ledger_truncated=true 或存在 pending/conflict 时"
            "不得生成可执行仓位金额。"
        ),
    }


def _valid_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_stale_snapshot(as_of_date: str | None, effective_trade_date: str) -> bool:
    as_of = _valid_iso_date(as_of_date)
    effective = _valid_iso_date(effective_trade_date)
    if as_of is None or effective is None:
        return True
    return as_of < effective


def _source_contract(source: str, *, authoritative: bool) -> tuple[str, str]:
    if source == "snapshot":
        return "portfolio_daily_snapshots", "first_party"
    if source in {"profiles", "profiles_recovered"}:
        return "fund_profiles", "first_party"
    if authoritative:
        return source or "portfolio_store", "first_party"
    return "analysis_request", "user_input"


def _evidence(
    *,
    fact_id: str,
    source: str,
    source_type: str,
    as_of_date: str | None,
    available_at: datetime | None,
    fetched_at: datetime,
    freshness: str,
    confidence: str,
    is_estimate: bool,
) -> dict[str, Any]:
    return DataEvidence(
        fact_id=fact_id,
        source=source,
        source_type=source_type,
        as_of_date=as_of_date,
        available_at=available_at,
        fetched_at=fetched_at,
        freshness=freshness,
        confidence=confidence,
        is_estimate=is_estimate,
    ).model_dump(mode="json")


def resolve_portfolio_preflight(
    requested_holdings: list[Holding],
    *,
    allow_stale: bool = False,
    now: datetime | None = None,
) -> PortfolioPreflightResult:
    """Resolve a server-owned portfolio snapshot before either decision pipeline.

    A persisted snapshot wins over the client body. A snapshot older than the
    session's effective trade date is blocked unless the caller explicitly opts
    into a degraded run. First-run client input remains supported, but is labelled
    as non-authoritative instead of being presented as first-party truth.
    """

    fetched_at = now or _utc_now()
    persisted, persisted_source, snapshot_date, refreshed_at = load_persisted_holdings(
        fetch_benchmark=False
    )
    # ``snapshot`` with an empty holdings array is still authoritative (for
    # example, the user deliberately cleared the portfolio). Never let a stale
    # client body resurrect positions merely because ``bool([])`` is false.
    authoritative = persisted_source in {"snapshot", "profiles", "profiles_recovered"}
    holdings = list(persisted if authoritative else requested_holdings)
    source = persisted_source if authoritative else "client_request"
    effective_trade_date = str(
        build_trading_session().get("effective_trade_date") or fetched_at.date().isoformat()
    )
    as_of_date = snapshot_date if authoritative and snapshot_date else effective_trade_date

    stale = bool(
        authoritative
        and persisted_source == "snapshot"
        and _is_stale_snapshot(snapshot_date, effective_trade_date)
    )
    if stale and not allow_stale:
        raise StalePortfolioSnapshotError(
            "持仓快照已过期："
            f"快照日期 {snapshot_date or '未知'}，当前有效交易日 {effective_trade_date}；"
            "请先刷新/重新确认持仓，或显式设置 allow_stale_portfolio_snapshot=true 接受降级分析。"
        )

    requested_fingerprint = holdings_fingerprint(requested_holdings)
    resolved_fingerprint = holdings_fingerprint(holdings)
    mismatch = bool(requested_holdings and authoritative and requested_fingerprint != resolved_fingerprint)
    degraded = stale or not authoritative
    freshness = "stale" if stale else "fresh"
    confidence = "low" if stale else ("high" if authoritative else "medium")
    evidence_source, source_type = _source_contract(source, authoritative=authoritative)
    evidence = _evidence(
        fact_id="portfolio.holdings",
        source=evidence_source,
        source_type=source_type,
        as_of_date=as_of_date,
        available_at=refreshed_at,
        fetched_at=fetched_at,
        freshness=freshness,
        confidence=confidence,
        is_estimate=False,
    )
    user_id = try_get_request_user_id()
    snapshot_seed = "|".join(
        [
            str(user_id or 0),
            source,
            as_of_date or "",
            _iso(refreshed_at) or "",
            resolved_fingerprint,
        ]
    )
    snapshot_id = hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:24]
    position_snapshot: dict[str, Any] | None = None
    position_snapshot_error: str | None = None
    try:
        from app.services.portfolio_ledger_service import capture_position_snapshot

        position_snapshot = capture_position_snapshot(
            holdings,
            position_as_of=as_of_date or effective_trade_date,
            captured_at=fetched_at,
            authoritative=authoritative,
            source=f"decision_preflight:{source}",
            legacy_recorded_at=refreshed_at or fetched_at,
        )
    except Exception as exc:  # noqa: BLE001 - preserve degraded first-run compatibility
        position_snapshot_error = type(exc).__name__
    position_incomplete = bool(
        position_snapshot and not position_snapshot.get("position_complete")
    )
    if position_incomplete:
        degraded = True
    return PortfolioPreflightResult(
        holdings=holdings,
        context={
            "schema_version": "1.0",
            "snapshot_id": snapshot_id,
            "source": source,
            "authoritative": authoritative,
            "as_of_date": as_of_date,
            "effective_trade_date": effective_trade_date,
            "captured_at": _iso(refreshed_at),
            "fetched_at": fetched_at.isoformat(),
            "holdings_fingerprint": resolved_fingerprint,
            "holdings_fingerprint_basis": "fund_code_membership",
            "position_snapshot": position_snapshot,
            "position_snapshot_id": (
                position_snapshot.get("snapshot_id") if position_snapshot else None
            ),
            "position_fingerprint": (
                position_snapshot.get("position_fingerprint") if position_snapshot else None
            ),
            "ledger_version": (
                position_snapshot.get("ledger_version") if position_snapshot else None
            ),
            "position_truth_status": (
                (position_snapshot.get("completeness") or {}).get(
                    "position_truth_status"
                )
                if position_snapshot
                else "unknown"
            ),
            "position_complete": bool(
                position_snapshot and position_snapshot.get("position_complete")
            ),
            "pending_transaction_count": int(
                (position_snapshot or {}).get("pending_transaction_count") or 0
            ),
            "known_unsettled_transaction_count": int(
                (position_snapshot or {}).get("known_unsettled_transaction_count") or 0
            ),
            "ledger_truncated": bool(
                (position_snapshot or {}).get("ledger_truncated")
            ),
            "position_snapshot_error": position_snapshot_error,
            "requested_holdings_fingerprint": requested_fingerprint,
            "client_snapshot_mismatch": mismatch,
            "holding_count": len(holdings),
            "stale": stale,
            "degraded": degraded,
            "freshness": freshness,
            "degradation_reason": (
                "stale_snapshot_explicitly_accepted"
                if stale
                else (
                    "no_server_snapshot"
                    if not authoritative
                    else (
                        "position_ledger_incomplete_or_unsettled"
                        if position_incomplete
                        else None
                    )
                )
            ),
            "evidence": evidence,
        },
    )


def _field_evidence(
    *,
    fact_id: str,
    source: str,
    source_type: str,
    as_of_date: str | None,
    fetched_at: datetime,
    confidence: str,
    is_estimate: bool,
    available: bool = True,
    freshness: str | None = None,
    available_at: datetime | None = None,
) -> dict[str, Any]:
    return _evidence(
        fact_id=fact_id,
        source=source if available else "unavailable",
        source_type=source_type,
        as_of_date=as_of_date,
        available_at=available_at,
        fetched_at=fetched_at,
        freshness=(freshness or "unknown") if available else "unavailable",
        confidence=confidence if available else "none",
        is_estimate=is_estimate,
    )


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _analysis_context_evidence(
    facts: dict[str, Any],
    effective_trade_date: str,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    """Evidence for the major non-portfolio inputs that can change an action."""

    items: list[dict[str, Any]] = []
    stock_flow = facts.get("stock_connect_flow")
    if isinstance(stock_flow, dict):
        stock_available = bool(
            stock_flow.get("available") and stock_flow.get("southbound_available")
        )
        flow_date = str(stock_flow.get("trade_date") or "")[:10] or None
        items.append(
            _field_evidence(
                fact_id="market.stock_connect.southbound_net_yi",
                source=str(stock_flow.get("source") or "stock_connect_flow"),
                source_type="third_party",
                as_of_date=flow_date,
                available_at=_coerce_datetime(stock_flow.get("fetched_at")),
                fetched_at=fetched_at,
                confidence="medium",
                is_estimate=False,
                available=stock_available,
                freshness=(
                    "stale"
                    if stock_flow.get("stale")
                    else (
                        "fresh"
                        if flow_date == effective_trade_date
                        else ("aging" if flow_date else "unknown")
                    )
                ),
            )
        )

    breadth = facts.get("market_breadth")
    if isinstance(breadth, dict):
        breadth_available = bool(breadth.get("available"))
        breadth_date = str(breadth.get("trade_date") or "")[:10] or None
        breadth_freshness = str(breadth.get("freshness_status") or "")
        if breadth.get("stale") is True or breadth_freshness == "stale":
            evidence_freshness = "stale"
        elif breadth_date == effective_trade_date:
            evidence_freshness = "fresh"
        else:
            evidence_freshness = "aging" if breadth_date else "unknown"
        items.append(
            _field_evidence(
                fact_id="market.market_breadth",
                source="market_breadth_pipeline",
                source_type="derived",
                as_of_date=breadth_date,
                available_at=fetched_at if breadth_available else None,
                fetched_at=fetched_at,
                confidence="medium",
                is_estimate=False,
                available=breadth_available,
                freshness=evidence_freshness,
            )
        )

    news = facts.get("news")
    if isinstance(news, dict):
        raw_label = str(news.get("freshness_label") or "unknown")
        news_available = raw_label not in {"empty", "unavailable"} and bool(
            news.get("total_items") or news.get("today_items")
        )
        freshness_map = {
            "fresh": "fresh",
            "aging": "aging",
            "stale": "stale",
            "empty": "unavailable",
        }
        confidence_map = {
            "fresh": "high",
            "aging": "medium",
            "stale": "low",
            "empty": "none",
        }
        as_of = str(news.get("as_of") or news.get("calendar_date") or "")[:10] or None
        items.append(
            _field_evidence(
                fact_id="news.market_news",
                source="news_pipeline",
                source_type="third_party",
                as_of_date=as_of,
                fetched_at=fetched_at,
                confidence=confidence_map.get(raw_label, "low"),
                is_estimate=False,
                available=news_available,
                freshness=freshness_map.get(raw_label, "unknown"),
            )
        )

    for key, fact_id in (
        ("factor_scores", "portfolio.factor_scores"),
        ("risk_metrics", "portfolio.risk_metrics"),
        ("candidate_factor_scores", "discovery.candidate_factor_scores"),
    ):
        value = facts.get(key)
        if not isinstance(value, dict):
            continue
        available = value.get("available") is not False
        items.append(
            _field_evidence(
                fact_id=fact_id,
                source=f"{key}_pipeline",
                source_type="derived",
                as_of_date=effective_trade_date,
                available_at=fetched_at if available else None,
                fetched_at=fetched_at,
                confidence="medium",
                is_estimate=True,
                available=available,
                freshness="fresh",
            )
        )

    for row in facts.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or "").strip().zfill(6)
        opportunity = row.get("sector_opportunity")
        if not code or not isinstance(opportunity, dict):
            continue
        flow = row.get("sector_fund_flow") or {}
        flow_date = str(flow.get("flow_date") or flow.get("trade_date") or "")[:10] or None
        available = bool(opportunity.get("available", True))
        items.append(
            _field_evidence(
                fact_id=f"holdings.{code}.sector_opportunity",
                source="sector_opportunity_pipeline",
                source_type="derived",
                as_of_date=flow_date,
                available_at=fetched_at if available else None,
                fetched_at=fetched_at,
                confidence=("medium" if available else "none"),
                is_estimate=True,
                available=available,
                freshness=(
                    "fresh"
                    if flow_date == effective_trade_date and flow.get("date_aligned") is not False
                    else "unknown"
                ),
            )
        )
    return items


def build_analysis_data_evidence(
    holdings: list[Holding],
    *,
    snapshots: list[FundSnapshot],
    facts: dict[str, Any],
    portfolio_context: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a compact field-level evidence registry persisted with a report."""

    fetched_at = now or _utc_now()
    session = facts.get("session") if isinstance(facts, dict) else None
    effective_trade_date = (
        str(session.get("effective_trade_date"))
        if isinstance(session, dict) and session.get("effective_trade_date")
        else str((portfolio_context or {}).get("effective_trade_date") or fetched_at.date().isoformat())
    )
    items: list[dict[str, Any]] = []
    if portfolio_context and isinstance(portfolio_context.get("evidence"), dict):
        items.append(dict(portfolio_context["evidence"]))

    position_source = "analysis_request"
    position_source_type = "user_input"
    position_as_of = effective_trade_date
    position_confidence = "medium"
    if portfolio_context:
        root = portfolio_context.get("evidence") or {}
        position_source = str(root.get("source") or position_source)
        position_source_type = str(root.get("source_type") or position_source_type)
        position_as_of = root.get("as_of_date") or position_as_of
        position_confidence = str(root.get("confidence") or position_confidence)
    position_freshness = str(
        ((portfolio_context or {}).get("evidence") or {}).get("freshness") or "unknown"
    )
    position_snapshot = (portfolio_context or {}).get("position_snapshot")
    position_rows = {
        str(row.get("fund_code") or "").strip().zfill(6): row
        for row in (
            position_snapshot.get("positions")
            if isinstance(position_snapshot, dict)
            else []
        )
        if isinstance(row, dict) and row.get("fund_code")
    }

    snapshot_by_code = {snapshot.fund_code: snapshot for snapshot in snapshots}
    for holding in holdings:
        prefix = f"holdings.{holding.fund_code}"
        position_row = position_rows.get(holding.fund_code)
        shares_quality = str((position_row or {}).get("shares_quality") or "unknown")
        shares_confirmed = shares_quality in {"user_confirmed", "platform_confirmed"}
        shares_available = bool(
            position_row and position_row.get("settled_shares") is not None
        )
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.holding_shares",
                source="portfolio_ledger",
                source_type="first_party" if shares_confirmed else "derived",
                as_of_date=position_as_of,
                fetched_at=fetched_at,
                confidence=("high" if shares_confirmed else ("low" if shares_available else "none")),
                is_estimate=not shares_confirmed,
                available=shares_available,
                freshness=position_freshness,
            )
        )
        cost_quality = str((position_row or {}).get("cost_quality") or "unknown")
        cost_confirmed = cost_quality in {"user_confirmed", "platform_confirmed"}
        cost_available = bool(
            position_row and position_row.get("cost_basis_total_cny") is not None
        )
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.cost_basis_total",
                source="portfolio_ledger",
                source_type="first_party" if cost_confirmed else "derived",
                as_of_date=position_as_of,
                fetched_at=fetched_at,
                confidence=("high" if cost_confirmed else ("low" if cost_available else "none")),
                is_estimate=not cost_confirmed,
                available=cost_available,
                freshness=position_freshness,
            )
        )
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.holding_amount",
                source=position_source,
                source_type=position_source_type,
                as_of_date=position_as_of,
                fetched_at=fetched_at,
                confidence=position_confidence,
                is_estimate=False,
                freshness=position_freshness,
            )
        )
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.holding_return_percent",
                source=position_source,
                source_type=position_source_type,
                as_of_date=position_as_of,
                fetched_at=fetched_at,
                confidence=position_confidence,
                is_estimate=False,
                available=holding.holding_return_percent is not None,
                freshness=position_freshness,
            )
        )

        snapshot = snapshot_by_code.get(holding.fund_code)
        daily_source = holding.daily_return_percent_source or "analysis_request"
        if daily_source == "official_nav":
            # FundSnapshot is fetched independently and is not written back to
            # Holding.daily_return_percent. Without a field-level as-of on the
            # holding, borrowing snapshot.nav_date would falsely certify old data.
            daily_source_type, daily_confidence, daily_estimate = "official", "medium", False
            daily_as_of, daily_freshness = None, "unknown"
        elif daily_source == "sector_estimate":
            daily_source_type, daily_confidence, daily_estimate = "derived", "low", True
            daily_as_of, daily_freshness = position_as_of, "unknown"
        elif daily_source == "pending_accrual":
            daily_source_type, daily_confidence, daily_estimate = "derived", "low", True
            daily_as_of, daily_freshness = position_as_of, "unknown"
        else:
            daily_source_type, daily_confidence, daily_estimate = "user_input", "medium", False
            daily_as_of, daily_freshness = position_as_of, position_freshness
        if position_freshness == "stale" and daily_source != "official_nav":
            daily_freshness = "stale"
            daily_confidence = "low"
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.daily_return_percent",
                source=daily_source,
                source_type=daily_source_type,
                as_of_date=daily_as_of,
                fetched_at=fetched_at,
                confidence=daily_confidence,
                is_estimate=daily_estimate,
                available=holding.daily_return_percent is not None,
                freshness=daily_freshness,
            )
        )

        sector_source = holding.sector_return_percent_source or "sector_quote"
        sector_available = holding.sector_return_percent is not None
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.sector_return_percent",
                source=sector_source,
                source_type="third_party",
                as_of_date=position_as_of,
                fetched_at=fetched_at,
                confidence=("high" if sector_source == "realtime" else "medium"),
                is_estimate=sector_source != "realtime",
                available=sector_available,
                freshness=("stale" if position_freshness == "stale" else "unknown"),
            )
        )

        nav_date = snapshot.nav_date if snapshot else None
        nav_freshness = (
            "fresh"
            if nav_date == effective_trade_date
            else ("aging" if nav_date else "unavailable")
        )
        items.append(
            _field_evidence(
                fact_id=f"{prefix}.latest_nav",
                source=(snapshot.source if snapshot else "unavailable"),
                source_type="third_party",
                as_of_date=nav_date,
                fetched_at=fetched_at,
                confidence=(
                    "high"
                    if snapshot and snapshot.latest_nav is not None and nav_freshness == "fresh"
                    else ("medium" if snapshot and snapshot.latest_nav is not None else "none")
                ),
                is_estimate=False,
                available=bool(snapshot and snapshot.latest_nav is not None),
                freshness=nav_freshness,
            )
        )

    items.extend(_analysis_context_evidence(facts, effective_trade_date, fetched_at))

    blocking_reasons: list[str] = []
    if portfolio_context and portfolio_context.get("stale"):
        blocking_reasons.append("stale_portfolio_snapshot")
    if portfolio_context and not portfolio_context.get("authoritative"):
        blocking_reasons.append("non_authoritative_portfolio")
    if portfolio_context and portfolio_context.get("position_complete") is False:
        blocking_reasons.append("incomplete_or_unsettled_position_ledger")
    return {
        "schema_version": "1.0",
        "generated_at": fetched_at.isoformat(),
        "decision_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "items": items,
    }


def attach_analysis_data_evidence(
    facts: dict[str, Any],
    *,
    holdings: list[Holding],
    snapshots: list[FundSnapshot],
    portfolio_context: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(facts)
    if portfolio_context:
        enriched["portfolio_snapshot"] = dict(portfolio_context)
    position_truth = compact_portfolio_position_truth(portfolio_context)
    if position_truth:
        enriched["portfolio_position_truth"] = position_truth
    enriched["data_evidence"] = build_analysis_data_evidence(
        holdings,
        snapshots=snapshots,
        facts=enriched,
        portfolio_context=portfolio_context,
    )
    return enriched


def attach_discovery_data_evidence(
    facts: dict[str, Any],
    *,
    holdings: list[Holding],
    candidate_pool: list[dict[str, Any]],
    portfolio_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach provenance for the portfolio and the ranked discovery candidates."""

    enriched = dict(facts)
    fetched_at = _utc_now()
    if portfolio_context:
        enriched["portfolio_snapshot"] = dict(portfolio_context)
    position_truth = compact_portfolio_position_truth(portfolio_context)
    if position_truth:
        enriched["portfolio_position_truth"] = position_truth
    items: list[dict[str, Any]] = []
    if portfolio_context and isinstance(portfolio_context.get("evidence"), dict):
        items.append(dict(portfolio_context["evidence"]))
    position_snapshot = (portfolio_context or {}).get("position_snapshot")
    position_rows = {
        str(row.get("fund_code") or "").strip().zfill(6): row
        for row in (
            position_snapshot.get("positions")
            if isinstance(position_snapshot, dict)
            else []
        )
        if isinstance(row, dict) and row.get("fund_code")
    }
    effective_trade_date = str(
        ((facts.get("session") or {}).get("effective_trade_date"))
        or (portfolio_context or {}).get("effective_trade_date")
        or fetched_at.date().isoformat()
    )
    for holding in holdings:
        code = holding.fund_code.strip().zfill(6)
        row = position_rows.get(code)
        quality = str((row or {}).get("shares_quality") or "unknown")
        confirmed = quality in {"user_confirmed", "platform_confirmed"}
        available = bool(row and row.get("settled_shares") is not None)
        items.append(
            _field_evidence(
                fact_id=f"holdings.{code}.holding_shares",
                source="portfolio_ledger",
                source_type="first_party" if confirmed else "derived",
                as_of_date=(portfolio_context or {}).get("as_of_date") or effective_trade_date,
                fetched_at=fetched_at,
                confidence="high" if confirmed else ("low" if available else "none"),
                is_estimate=not confirmed,
                available=available,
                freshness=str((portfolio_context or {}).get("freshness") or "unknown"),
            )
        )
    for candidate in candidate_pool:
        code = str(candidate.get("fund_code") or "").strip().zfill(6)
        if not code or code == "000000":
            continue
        nav_trend = candidate.get("nav_trend") or {}
        nav_date = (
            candidate.get("nav_date")
            or candidate.get("latest_date")
            or (nav_trend.get("latest_date") if isinstance(nav_trend, dict) else None)
            or (nav_trend.get("latest_nav_date") if isinstance(nav_trend, dict) else None)
        )
        candidate_freshness = (
            "fresh"
            if nav_date and str(nav_date)[:10] == effective_trade_date
            else ("aging" if nav_date else "unknown")
        )
        items.append(
            _field_evidence(
                fact_id=f"candidates.{code}.candidate_metrics",
                source="fund_candidate_pipeline",
                source_type="derived",
                as_of_date=str(nav_date) if nav_date else None,
                fetched_at=fetched_at,
                confidence="medium",
                is_estimate=True,
                freshness=candidate_freshness,
            )
        )
    items.extend(_analysis_context_evidence(facts, effective_trade_date, fetched_at))
    blocking_reasons: list[str] = []
    if portfolio_context and portfolio_context.get("stale"):
        blocking_reasons.append("stale_portfolio_snapshot")
    if portfolio_context and not portfolio_context.get("authoritative"):
        blocking_reasons.append("non_authoritative_portfolio")
    if portfolio_context and portfolio_context.get("position_complete") is False:
        blocking_reasons.append("incomplete_or_unsettled_position_ledger")
    enriched["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": fetched_at.isoformat(),
        "decision_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "items": items,
    }
    return enriched


def portfolio_snapshot_caveats(facts: dict[str, Any]) -> list[str]:
    snapshot = facts.get("portfolio_snapshot") if isinstance(facts, dict) else None
    if not isinstance(snapshot, dict):
        return []
    caveats: list[str] = []
    if snapshot.get("client_snapshot_mismatch"):
        caveats.append("客户端持仓与服务端快照不一致，本次已采用服务端权威持仓并记录差异。")
    if snapshot.get("stale"):
        caveats.append(
            "本次显式接受了过期持仓快照，组合权重与金额证据已降为低置信度；"
            "执行任何动作前请重新确认持仓。"
        )
    elif not snapshot.get("authoritative"):
        caveats.append("尚无服务端持仓快照，本次使用请求内持仓并标记为非权威输入。")
    return caveats


_USABLE_FRESHNESS = frozenset({"fresh", "aging"})
_EXECUTABLE_TEXT_TOKENS = (
    "加仓",
    "买入",
    "申购",
    "减仓",
    "清仓",
    "卖出",
    "赎回",
    "投入",
    "金额",
    "仓位调整",
    "元",
)


def _usable_evidence(item: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("freshness") in _USABLE_FRESHNESS
        and item.get("confidence") not in {None, "none"}
    )


def decision_evidence_allows_action(
    facts: dict[str, Any] | None,
    *,
    scope: str,
    fund_code: str,
) -> tuple[bool, list[str]]:
    """Deterministic final gate for evidence required by an executable action.

    Reports created before DataEvidence v1 remain compatible. Once a registry is
    present, missing/unknown/stale critical evidence can no longer be bypassed by
    persuasive model text.
    """

    registry = (facts or {}).get("data_evidence")
    if not isinstance(registry, dict):
        return True, []
    reasons = [str(item) for item in registry.get("blocking_reasons") or []]
    if registry.get("decision_ready") is False:
        return False, reasons or ["decision_evidence_not_ready"]
    items = {
        str(item.get("fact_id")): item
        for item in registry.get("items") or []
        if isinstance(item, dict) and item.get("fact_id")
    }
    code = str(fund_code or "").strip().zfill(6)
    if scope == "discovery":
        pool_item = next(
            (
                item
                for item in (facts or {}).get("candidate_pool") or []
                if isinstance(item, dict)
                and str(item.get("fund_code") or "").strip().zfill(6) == code
            ),
            None,
        )
        quality_gate = (
            pool_item.get("quality_gate")
            if isinstance(pool_item, dict) and isinstance(pool_item.get("quality_gate"), dict)
            else None
        )
        if quality_gate is not None and quality_gate.get("status") != "eligible":
            status = str(quality_gate.get("status") or "watch_only")
            return False, [f"candidate_quality_gate_{status}"]
        required = f"candidates.{code}.candidate_metrics"
        if not _usable_evidence(items.get(required)):
            return False, ["candidate_metrics_not_point_in_time_usable"]
        return True, []

    amount_id = f"holdings.{code}.holding_amount"
    if not _usable_evidence(items.get(amount_id)):
        reasons.append("holding_amount_not_point_in_time_usable")
    directional_ids = (
        f"holdings.{code}.daily_return_percent",
        f"holdings.{code}.sector_return_percent",
        f"holdings.{code}.sector_opportunity",
    )
    if not any(_usable_evidence(items.get(fact_id)) for fact_id in directional_ids):
        reasons.append("directional_evidence_not_point_in_time_usable")
    return not reasons, reasons


def contains_executable_decision_text(value: object) -> bool:
    text = str(value or "")
    return any(token in text for token in _EXECUTABLE_TEXT_TOKENS)


def safe_blocked_points(values: list[str], *, fallback: str) -> list[str]:
    safe = [str(value) for value in values if not contains_executable_decision_text(value)]
    return [fallback, *safe[:2]]


def report_execution_blocked(facts: dict[str, Any] | None) -> bool:
    if not isinstance(facts, dict):
        return False
    snapshot = facts.get("portfolio_snapshot")
    if isinstance(snapshot, dict) and (
        snapshot.get("stale") or not snapshot.get("authoritative")
    ):
        return True
    guard = facts.get("data_evidence_guard")
    return bool(isinstance(guard, dict) and guard.get("execution_blocked"))
