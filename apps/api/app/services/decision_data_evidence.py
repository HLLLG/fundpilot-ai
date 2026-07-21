from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import isfinite
from collections.abc import Mapping
from typing import Any

from app.models import DataEvidence, FundSnapshot, Holding
from app.request_context import try_get_request_user_id
from app.services.portfolio_holdings_service import load_persisted_holdings
from app.services.trading_session import build_trading_session
from app.services.fund_tradeability import build_tradeability_gate
from app.services.daily_tradeability import build_holding_transaction_execution


class StalePortfolioSnapshotError(ValueError):
    """Raised when a decision would use a portfolio older than its trade date."""


def _as_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


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
            "不得生成固定金额或份额，但可基于持仓市值给出相对百分比方向。"
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
        build_trading_session(now).get("effective_trade_date")
        or fetched_at.date().isoformat()
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


def _snapshot_dependency_available_at(snapshot: Mapping[str, Any]) -> datetime | None:
    available_at = _coerce_datetime(snapshot.get("available_at"))
    first_observed_at = _coerce_datetime(snapshot.get("first_observed_at"))
    if available_at is None or first_observed_at is None:
        return None
    return max(available_at, first_observed_at)


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
    lookthrough = facts.get("fund_lookthrough")
    if isinstance(lookthrough, dict):
        lookthrough_status = str(lookthrough.get("status") or "unavailable")
        existing_rows = [
            row
            for row in lookthrough.get("existing_funds") or []
            if isinstance(row, dict)
        ]
        qualified_existing_rows = []
        for row in existing_rows:
            snapshot = row.get("snapshot")
            lookthrough_row = row.get("lookthrough")
            if (
                row.get("status") == "qualified"
                and isinstance(snapshot, dict)
                and snapshot.get("disclosed_overlap_lower_bound_eligible") is True
                and isinstance(lookthrough_row, dict)
                and (_as_float(lookthrough_row.get("identity_known_disclosed_mass_percent")) or 0)
                > 0
            ):
                qualified_existing_rows.append(row)
        existing_snapshots = [
            row.get("snapshot")
            for row in qualified_existing_rows
            if isinstance(row.get("snapshot"), dict)
        ]
        existing_as_of = {
            str(row.get("as_of_date") or "")[:10]
            for row in existing_snapshots
            if str(row.get("as_of_date") or "").strip()
        }
        portfolio_as_of = next(iter(existing_as_of)) if len(existing_as_of) == 1 else None
        existing_freshness = {
            str(row.get("current_freshness_label") or "unknown")
            for row in existing_snapshots
        }
        portfolio_freshness = (
            "stale"
            if "stale" in existing_freshness
            else (
                "aging"
                if len(existing_as_of) == 1 and existing_snapshots
                else "unknown"
            )
        )
        portfolio = lookthrough.get("portfolio")
        portfolio_identity_mass = (
            _as_float(portfolio.get("identity_known_security_mass_lower_bound_percent"))
            if isinstance(portfolio, dict)
            else None
        )
        portfolio_dependency_times = [
            _snapshot_dependency_available_at(snapshot)
            for snapshot in existing_snapshots
        ]
        portfolio_available_at = (
            max(value for value in portfolio_dependency_times if value is not None)
            if portfolio_dependency_times
            and all(value is not None for value in portfolio_dependency_times)
            else None
        )
        portfolio_available = bool(
            isinstance(portfolio, dict)
            and lookthrough_status in {"qualified", "partial"}
            and qualified_existing_rows
            and portfolio_identity_mass is not None
            and portfolio_identity_mass > 0
            and portfolio_available_at is not None
        )
        items.append(
            _field_evidence(
                fact_id="fund_lookthrough:portfolio",
                source="fund_lookthrough_research",
                source_type="derived",
                as_of_date=portfolio_as_of,
                available_at=portfolio_available_at,
                fetched_at=fetched_at,
                confidence="medium" if portfolio_available else "none",
                is_estimate=False,
                available=portfolio_available,
                freshness=portfolio_freshness,
            )
        )
        resolution_audit = lookthrough.get("resolution_audit")
        resolution_rows = (
            resolution_audit.get("rows")
            if isinstance(resolution_audit, dict)
            else []
        )
        for raw in resolution_rows or []:
            if not isinstance(raw, dict):
                continue
            raw_code = str(raw.get("fund_code") or "").strip()
            code = raw_code.zfill(6)
            snapshot_ref = str(raw.get("snapshot_ref") or "").strip().lower()
            if (
                re.fullmatch(r"\d{1,6}", raw_code) is None
                or code == "000000"
                or re.fullmatch(r"[0-9a-f]{12}", snapshot_ref) is None
            ):
                continue
            available = raw.get("qualified") is True
            snapshot_freshness = str(raw.get("freshness") or "unknown")
            if snapshot_freshness == "fresh":
                snapshot_freshness = "aging"
            items.append(
                _field_evidence(
                    fact_id=f"holdings_snapshot:{code}:{snapshot_ref}",
                    source="fund_holdings_snapshot_repository",
                    source_type="derived",
                    as_of_date=str(raw.get("as_of_date") or "")[:10] or None,
                    available_at=_coerce_datetime(
                        raw.get("first_observed_at") or raw.get("available_at")
                    ),
                    fetched_at=fetched_at,
                    confidence="medium" if available else "none",
                    is_estimate=False,
                    available=available,
                    freshness=snapshot_freshness,
                )
            )
        for raw in lookthrough.get("candidates") or []:
            if not isinstance(raw, dict):
                continue
            raw_code = str(raw.get("fund_code") or "").strip()
            code = raw_code.zfill(6)
            status = str(raw.get("status") or "unavailable")
            if re.fullmatch(r"\d{1,6}", raw_code) is None or code == "000000":
                continue
            candidate_snapshot = (
                raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
            )
            candidate_capabilities = raw.get("capabilities")
            candidate_available_at = _snapshot_dependency_available_at(
                candidate_snapshot
            )
            available = bool(
                status == "qualified"
                and isinstance(candidate_capabilities, dict)
                and candidate_capabilities.get("research_eligible") is True
                and candidate_available_at is not None
            )
            candidate_freshness_raw = str(
                candidate_snapshot.get("current_freshness_label") or "unknown"
            )
            candidate_freshness = (
                "stale"
                if candidate_freshness_raw == "stale"
                else (
                    "aging"
                    if candidate_freshness_raw in {"fresh", "aging"}
                    else "unknown"
                )
            )
            items.append(
                _field_evidence(
                    fact_id=f"fund_lookthrough:candidate:{code}",
                    source="fund_lookthrough_research",
                    source_type="derived",
                    as_of_date=str(candidate_snapshot.get("as_of_date") or "")[:10]
                    or None,
                    available_at=candidate_available_at,
                    fetched_at=fetched_at,
                    confidence="medium" if available else "none",
                    is_estimate=False,
                    available=available,
                    freshness=candidate_freshness,
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
    fact_rows = facts.get("holdings") if isinstance(facts.get("holdings"), list) else []
    for holding_index, holding in enumerate(holdings):
        prefix = f"holdings.{holding.fund_code}"
        fact_row = (
            fact_rows[holding_index]
            if holding_index < len(fact_rows) and isinstance(fact_rows[holding_index], dict)
            else {}
        )
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

        # New reports always carry a structured tradeability snapshot, including
        # explicit unavailable records. Older reports omit the key and remain
        # readable without being silently rewritten.
        if "tradeability" in fact_row:
            tradeability = (
                fact_row.get("tradeability")
                if isinstance(fact_row.get("tradeability"), dict)
                else {}
            )
            transaction_execution = (
                fact_row.get("transaction_execution")
                if isinstance(fact_row.get("transaction_execution"), dict)
                else build_holding_transaction_execution(
                    tradeability,
                    holding_amount_yuan=holding.holding_amount,
                )
            )
            tradeability_usable = bool(
                str(tradeability.get("schema_version") or "")
                == "fund_tradeability.v1"
                and str(tradeability.get("data_status") or "")
                in {"complete", "partial"}
                and str(tradeability.get("freshness") or "") == "fresh"
                and tradeability.get("source_conflict") is not True
                and str(tradeability.get("checked_at") or "").strip()
                and any(
                    str(source).strip()
                    for source in tradeability.get("source_ids") or []
                )
            )
            sources = "+".join(
                str(source)
                for source in tradeability.get("source_ids") or []
                if str(source).strip()
            )
            checked_at = _coerce_datetime(tradeability.get("checked_at"))
            items.append(
                _field_evidence(
                    fact_id=f"{prefix}.tradeability",
                    source=sources or "fund_tradeability_pipeline",
                    source_type="third_party",
                    as_of_date=effective_trade_date,
                    available_at=checked_at,
                    fetched_at=fetched_at,
                    confidence="high" if tradeability_usable else "none",
                    is_estimate=False,
                    available=tradeability_usable,
                    freshness=str(tradeability.get("freshness") or "unavailable"),
                )
            )
            add_usable = transaction_execution.get("add_status") == "eligible"
            items.append(
                _field_evidence(
                    fact_id=f"{prefix}.purchase_execution",
                    source=sources or "fund_tradeability_pipeline",
                    source_type="third_party",
                    as_of_date=effective_trade_date,
                    available_at=checked_at,
                    fetched_at=fetched_at,
                    confidence="high" if add_usable else "none",
                    is_estimate=False,
                    available=add_usable,
                    freshness=str(tradeability.get("freshness") or "unavailable"),
                )
            )
            redemption_usable = (
                transaction_execution.get("redemption_status") == "eligible"
            )
            items.append(
                _field_evidence(
                    fact_id=f"{prefix}.redemption_execution",
                    source=sources or "fund_tradeability_pipeline",
                    source_type="third_party",
                    as_of_date=effective_trade_date,
                    available_at=checked_at,
                    fetched_at=fetched_at,
                    confidence="high" if redemption_usable else "none",
                    is_estimate=False,
                    available=redemption_usable,
                    freshness=str(tradeability.get("freshness") or "unavailable"),
                )
            )
            items.append(
                _field_evidence(
                    fact_id=f"{prefix}.redemption_lot_cost",
                    source="portfolio_transaction_lots",
                    source_type="first_party",
                    as_of_date=effective_trade_date,
                    fetched_at=fetched_at,
                    confidence="none",
                    is_estimate=False,
                    available=False,
                    freshness="unavailable",
                )
            )

    items.extend(_analysis_context_evidence(facts, effective_trade_date, fetched_at))

    blocking_reasons: list[str] = []
    position_quality_reasons: list[str] = []
    if portfolio_context and portfolio_context.get("stale"):
        blocking_reasons.append("stale_portfolio_snapshot")
    if portfolio_context and not portfolio_context.get("authoritative"):
        blocking_reasons.append("non_authoritative_portfolio")
    if portfolio_context and portfolio_context.get("position_complete") is False:
        position_quality_reasons.append("incomplete_or_unsettled_position_ledger")
    return {
        "schema_version": "1.0",
        "generated_at": fetched_at.isoformat(),
        "decision_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "position_quality_reasons": position_quality_reasons,
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
        parsed_nav_date = _valid_iso_date(str(nav_date)[:10] if nav_date else None)
        parsed_effective_date = _valid_iso_date(effective_trade_date)
        candidate_metrics_available = bool(
            parsed_nav_date is not None
            and parsed_effective_date is not None
            and parsed_nav_date <= parsed_effective_date
        )
        candidate_freshness = (
            "fresh"
            if candidate_metrics_available and parsed_nav_date == parsed_effective_date
            else ("aging" if candidate_metrics_available else "unavailable")
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
                available=candidate_metrics_available,
                freshness=candidate_freshness,
            )
        )
        tradeability = candidate.get("tradeability")
        tradeability_gate = (
            build_tradeability_gate(tradeability)
            if isinstance(tradeability, dict)
            else build_tradeability_gate(None)
        )
        tradeability_available = bool(
            isinstance(tradeability, dict)
            and tradeability_gate.get("status") == "eligible"
        )
        tradeability_source = (
            "+".join(str(item) for item in tradeability.get("source_ids") or [])
            if isinstance(tradeability, dict)
            else ""
        )
        items.append(
            _field_evidence(
                fact_id=f"candidates.{code}.tradeability",
                source=tradeability_source or "fund_tradeability_pipeline",
                source_type="third_party",
                as_of_date=effective_trade_date,
                available_at=(
                    _coerce_datetime(tradeability.get("checked_at"))
                    if isinstance(tradeability, dict)
                    else None
                ),
                fetched_at=fetched_at,
                confidence="high" if tradeability_available else "none",
                is_estimate=False,
                available=tradeability_available,
                freshness=(
                    str(tradeability.get("freshness") or "unavailable")
                    if isinstance(tradeability, dict)
                    else "unavailable"
                ),
            )
        )
        benchmark_metrics = (
            candidate.get("benchmark_metrics")
            if isinstance(candidate.get("benchmark_metrics"), dict)
            else {}
        )
        if benchmark_metrics:
            alignment = (
                benchmark_metrics.get("alignment")
                if isinstance(benchmark_metrics.get("alignment"), dict)
                else {}
            )
            fund_series = (
                benchmark_metrics.get("fund_series")
                if isinstance(benchmark_metrics.get("fund_series"), dict)
                else {}
            )
            components = [
                row
                for row in benchmark_metrics.get("components") or []
                if isinstance(row, dict)
            ]
            benchmark_sources = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in [
                        fund_series.get("source"),
                        *(row.get("source") for row in components),
                    ]
                    if str(value or "").strip()
                )
            )
            available_moments = [
                moment
                for moment in [
                    _coerce_datetime(fund_series.get("available_at")),
                    *(
                        _coerce_datetime(row.get("available_at"))
                        for row in components
                    ),
                ]
                if moment is not None
            ]
            benchmark_as_of = str(
                alignment.get("last_common_date")
                or benchmark_metrics.get("effective_trade_date")
                or ""
            ).strip()
            benchmark_available = bool(
                benchmark_metrics.get("status") == "qualified"
                and benchmark_metrics.get("qualified") is True
                and benchmark_sources
                and benchmark_as_of
            )
            benchmark_freshness = (
                "fresh"
                if benchmark_as_of == effective_trade_date
                else ("aging" if benchmark_as_of else "unavailable")
            )
            items.append(
                _field_evidence(
                    fact_id=f"candidates.{code}.benchmark_metrics",
                    source="+".join(benchmark_sources)
                    or "fund_benchmark_research_pipeline",
                    source_type="derived",
                    as_of_date=benchmark_as_of or None,
                    available_at=max(available_moments) if available_moments else None,
                    fetched_at=fetched_at,
                    confidence="medium" if benchmark_available else "none",
                    is_estimate=False,
                    available=benchmark_available,
                    freshness=benchmark_freshness,
                )
            )
    items.extend(_analysis_context_evidence(facts, effective_trade_date, fetched_at))
    blocking_reasons: list[str] = []
    position_quality_reasons: list[str] = []
    if portfolio_context and portfolio_context.get("stale"):
        blocking_reasons.append("stale_portfolio_snapshot")
    if portfolio_context and not portfolio_context.get("authoritative"):
        blocking_reasons.append("non_authoritative_portfolio")
    if portfolio_context and portfolio_context.get("position_complete") is False:
        position_quality_reasons.append("incomplete_or_unsettled_position_ledger")
    enriched["data_evidence"] = {
        "schema_version": "1.0",
        "generated_at": fetched_at.isoformat(),
        "decision_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "position_quality_reasons": position_quality_reasons,
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
_TRADE_ACTION_RE = re.compile(r"(?:加仓|买入|申购|定投|投入|减仓|降仓|清仓|卖出|赎回)")
_TRADE_INSTRUCTION_CUE_RE = re.compile(
    r"(?:立即|马上|一次性|全仓|今日执行|执行|操作|建议|应当|应该|务必|"
    r"需要|需|请|计划|目标|考虑|优先|分批|动作|金额|仓位|\d[\d,.]*\s*元)"
)
_HIGH_RISK_TRADE_CUE_RE = re.compile(
    r"(?:立即|马上|一次性|全仓|今日执行|务必|\d[\d,.]*\s*元)"
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
    direction: str | None = None,
    allow_incomplete_position_for_direction: bool = False,
) -> tuple[bool, list[str]]:
    """Deterministic final gate for evidence required by an executable action.

    Reports created before DataEvidence v1 remain compatible. Once a registry is
    present, missing/unknown/stale critical evidence can no longer be bypassed by
    persuasive model text.
    """

    registry = (facts or {}).get("data_evidence")
    if not isinstance(registry, dict):
        return True, []
    raw_reasons = [str(item) for item in registry.get("blocking_reasons") or []]
    reasons = list(raw_reasons)
    if allow_incomplete_position_for_direction:
        # An unconfirmed share ledger does not invalidate fresh market direction
        # evidence or a percentage relative to the current estimated market
        # value. Exact shares/yuan remain outside the daily recommendation.
        reasons = [
            reason
            for reason in reasons
            if reason != "incomplete_or_unsettled_position_ledger"
        ]
    if registry.get("decision_ready") is False and (reasons or not raw_reasons):
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
        tradeability_required = f"candidates.{code}.tradeability"
        if not _usable_evidence(items.get(tradeability_required)):
            return False, ["candidate_tradeability_not_point_in_time_usable"]
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
    tradeability_id = f"holdings.{code}.tradeability"
    # The key is absent only on reports from before this evidence contract.
    # Newly prepared reports always emit it, even when the provider is down.
    if tradeability_id in items:
        if direction == "add" and not _usable_evidence(
            items.get(f"holdings.{code}.purchase_execution")
        ):
            reasons.append("holding_purchase_execution_not_point_in_time_usable")
        if direction == "reduce" and not _usable_evidence(
            items.get(f"holdings.{code}.redemption_execution")
        ):
            reasons.append("holding_redemption_execution_not_point_in_time_usable")
    return not reasons, reasons


def contains_executable_decision_text(value: object) -> bool:
    text = str(value or "")
    return any(token in text for token in _EXECUTABLE_TEXT_TOKENS)


def contains_trade_instruction_text(value: object) -> bool:
    """Detect an actionable instruction without flagging neutral fee/news facts."""
    text = str(value or "").strip()
    if not _TRADE_ACTION_RE.search(text):
        return False
    if _TRADE_INSTRUCTION_CUE_RE.search(text) is not None:
        return True
    return re.fullmatch(
        r"(?:建议|请)?(?:立即|分批|少量)?"
        r"(?:加仓|买入|申购|定投|投入|减仓|降仓|清仓|卖出|赎回)(?:评估)?",
        text,
    ) is not None


def contains_high_risk_trade_instruction_text(value: object) -> bool:
    text = str(value or "").strip()
    return bool(_TRADE_ACTION_RE.search(text) and _HIGH_RISK_TRADE_CUE_RE.search(text))


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
