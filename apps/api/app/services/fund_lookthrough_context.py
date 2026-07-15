"""Bounded orchestration for PIT fund-disclosure look-through research.

The orchestration layer is deliberately best-effort: report generation keeps
going when a provider, store row, or individual fund times out.  Every worker
receives the same aware ``decision_at`` and only repository-qualified snapshots
are passed to deterministic research.  Resolution audit rows never contain raw
provider frames, snapshots, or holdings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.config import Settings, get_settings
from app.request_context import try_get_request_user_id
from app.services.fund_holdings_snapshot import CN_TZ
from app.services.fund_holdings_snapshot_repository import (
    resolve_fund_holdings_snapshot_at_decision,
)
from app.services.fund_lookthrough_research import build_fund_lookthrough_research
from app.services.pipeline_concurrency import run_with_request_user


LOOKTHROUGH_RESOLUTION_AUDIT_SCHEMA_VERSION = "fund_holdings_resolution_audit.v1"
SnapshotResolver = Callable[..., dict[str, Any]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Task:
    index: int
    fund_code: str
    role: str


@dataclass(frozen=True)
class _PortfolioInput:
    positions: list[dict[str, Any]]
    envelope: dict[str, Any]
    positions_complete: bool
    denominator_yuan: float | None
    denominator_source: dict[str, Any] | None
    audit: dict[str, Any]


def build_fund_lookthrough_context(
    holdings: Sequence[Any] | None,
    candidate_pool: Sequence[Mapping[str, Any]] | None,
    *,
    decision_at: str | datetime,
    analysis_mode: str,
    portfolio_context: Mapping[str, Any] | None,
    resolver: SnapshotResolver | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Resolve snapshots without allowing optional evidence to block a report."""

    try:
        return _build_fund_lookthrough_context(
            holdings,
            candidate_pool,
            decision_at=decision_at,
            analysis_mode=analysis_mode,
            portfolio_context=portfolio_context,
            resolver=resolver,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - this context is deliberately optional
        logger.exception("fund look-through context construction failed")
        return _unavailable_context(
            decision_at=decision_at,
            reason="lookthrough_context_error",
            detail=type(exc).__name__,
        )


def _build_fund_lookthrough_context(
    holdings: Sequence[Any] | None,
    candidate_pool: Sequence[Mapping[str, Any]] | None,
    *,
    decision_at: str | datetime,
    analysis_mode: str,
    portfolio_context: Mapping[str, Any] | None,
    resolver: SnapshotResolver | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build one bounded, persistence-safe research fact."""

    decision = _aware_datetime(decision_at)
    if decision is None:
        return _unavailable_context(
            decision_at=decision_at,
            reason="decision_at_timezone_required",
        )
    cfg = settings or get_settings()
    mode = "deep" if str(analysis_mode).strip().lower() == "deep" else "fast"
    portfolio = _portfolio_input(holdings or [], portfolio_context, decision=decision)
    existing_codes = _position_codes(portfolio.positions)
    if not existing_codes:
        existing_codes = _holding_codes(holdings or [])
    candidate_codes = [
        code
        for code in _candidate_codes(candidate_pool or [])
        if code not in set(existing_codes)
    ]
    tasks, omitted = _bounded_tasks(
        existing_codes,
        candidate_codes,
        max_funds=_bounded_int(cfg.fund_holdings_context_max_funds, 1, 200, 40),
    )
    timeout_seconds = (
        _bounded_float(cfg.fund_holdings_context_total_timeout_seconds, 0.1, 120.0, 18.0)
        if mode == "deep"
        else _bounded_float(cfg.fund_holdings_context_fast_timeout_seconds, 0.1, 30.0, 2.0)
    )
    allow_live = mode == "deep"
    batch_started = time.monotonic()
    store_timeout = min(
        timeout_seconds,
        _bounded_float(
            cfg.fund_holdings_context_fast_timeout_seconds,
            0.1,
            30.0,
            2.0,
        ),
    )
    resolutions, resolution_rows = _resolve_batch(
        tasks,
        decision=decision,
        allow_live=False,
        timeout_seconds=store_timeout,
        workers=_bounded_int(cfg.fund_holdings_context_workers, 1, 16, 4),
        refresh_retry_ttl_seconds=_bounded_float(
            cfg.fund_holdings_refresh_retry_ttl_seconds,
            0.0,
            86_400.0,
            900.0,
        ),
        resolver=resolver or resolve_fund_holdings_snapshot_at_decision,
    )
    refresh_deferred: list[dict[str, str]] = []
    if allow_live:
        refresh_tasks = [
            task
            for task in tasks
            if _live_refresh_required(resolutions.get(task.index))
        ]
        live_max = _bounded_int(
            getattr(cfg, "fund_holdings_context_live_max_funds", 8),
            0,
            40,
            8,
        )
        selected_refresh_tasks = refresh_tasks[:live_max]
        refresh_deferred = [
            {
                "fund_code": task.fund_code,
                "role": task.role,
                "reason": "live_refresh_max_funds_exceeded",
            }
            for task in refresh_tasks[live_max:]
        ]
        remaining_timeout = max(
            timeout_seconds - (time.monotonic() - batch_started),
            0.0,
        )
        if selected_refresh_tasks and remaining_timeout > 0:
            refreshed, refreshed_rows = _resolve_batch(
                selected_refresh_tasks,
                decision=decision,
                allow_live=True,
                timeout_seconds=remaining_timeout,
                workers=_bounded_int(cfg.fund_holdings_context_workers, 1, 16, 4),
                refresh_retry_ttl_seconds=_bounded_float(
                    cfg.fund_holdings_refresh_retry_ttl_seconds,
                    0.0,
                    86_400.0,
                    900.0,
                ),
                resolver=resolver or resolve_fund_holdings_snapshot_at_decision,
            )
            store_rows_by_index = {
                int(row["request_index"]): row for row in resolution_rows
            }
            for row in refreshed_rows:
                prior = store_rows_by_index.get(int(row["request_index"])) or {}
                row["store_phase_status"] = prior.get("status")
                row["store_phase_source"] = prior.get("source")
                row["resolution_phase"] = "live_refresh"
                store_rows_by_index[int(row["request_index"])] = row
            resolutions.update(refreshed)
            resolution_rows = sorted(
                store_rows_by_index.values(),
                key=lambda row: int(row["request_index"]),
            )
        elif selected_refresh_tasks:
            refresh_deferred.extend(
                {
                    "fund_code": task.fund_code,
                    "role": task.role,
                    "reason": "live_refresh_total_timeout_exhausted",
                }
                for task in selected_refresh_tasks
            )
    for row in resolution_rows:
        row.setdefault("resolution_phase", "store")
    snapshots_by_task: dict[tuple[str, str], dict[str, Any]] = {}
    for task in tasks:
        resolution = resolutions.get(task.index)
        if not isinstance(resolution, Mapping) or resolution.get("qualified") is not True:
            continue
        snapshot = resolution.get("snapshot") if isinstance(resolution, Mapping) else None
        if not isinstance(snapshot, Mapping):
            continue
        if _fund_code(snapshot.get("fund_code")) != task.fund_code:
            continue
        snapshots_by_task[(task.role, task.fund_code)] = deepcopy(dict(snapshot))

    existing_snapshots = [
        snapshots_by_task[("existing", code)]
        for code in existing_codes
        if ("existing", code) in snapshots_by_task
    ]
    candidate_snapshots = [
        snapshots_by_task[("candidate", code)]
        for code in candidate_codes
        if ("candidate", code) in snapshots_by_task
    ]
    current_run_observation = _current_run_observation_proof(
        [*existing_snapshots, *candidate_snapshots],
        decision=decision,
        allow_live=allow_live,
    )
    try:
        research = build_fund_lookthrough_research(
            existing_snapshots,
            portfolio.envelope,
            candidate_snapshots,
            decision_at=decision,
            portfolio_positions_complete=portfolio.positions_complete,
            portfolio_denominator_yuan=portfolio.denominator_yuan,
            portfolio_denominator_source=portfolio.denominator_source,
            current_run_observation=current_run_observation,
        )
    except Exception as exc:  # noqa: BLE001 - optional context must not block reports
        return _unavailable_context(
            decision_at=decision,
            reason="lookthrough_research_error",
            detail=type(exc).__name__,
            resolution_rows=resolution_rows,
            portfolio_audit=portfolio.audit,
        )

    resolved_existing_codes = {
        code for role, code in snapshots_by_task if role == "existing"
    }
    resolved_candidate_codes = {
        code for role, code in snapshots_by_task if role == "candidate"
    }
    missing_existing = [code for code in existing_codes if code not in resolved_existing_codes]
    missing_candidates = [
        code for code in candidate_codes if code not in resolved_candidate_codes
    ]
    research["scope"] = (
        "portfolio_and_candidates" if candidate_codes else "portfolio_only"
    )
    capabilities = research.get("capabilities")
    capabilities = dict(capabilities) if isinstance(capabilities, Mapping) else {}
    prior_portfolio_capability = capabilities.get("portfolio_lookthrough")
    prior_candidate_capability = capabilities.get("candidate_overlap")
    prior_portfolio_status = (
        str(prior_portfolio_capability.get("status") or "qualified")
        if isinstance(prior_portfolio_capability, Mapping)
        else "qualified"
    )
    prior_candidate_status = (
        str(prior_candidate_capability.get("status") or "qualified")
        if isinstance(prior_candidate_capability, Mapping)
        else "qualified"
    )
    capabilities["portfolio_lookthrough"] = {
        "status": (
            "partial"
            if missing_existing
            else prior_portfolio_status
        )
    }
    capabilities["candidate_overlap"] = {
        "status": (
            "not_requested"
            if not candidate_codes
            else "partial"
            if missing_candidates
            else prior_candidate_status
        )
    }
    research["capabilities"] = capabilities
    if missing_existing or missing_candidates or omitted:
        reasons = list(research.get("reason_codes") or [])
        if missing_existing:
            reasons.append("existing_snapshot_resolution_incomplete")
        if missing_candidates:
            reasons.append("candidate_snapshot_resolution_incomplete")
        if omitted:
            reasons.append("snapshot_resolution_max_funds_truncated")
        research["reason_codes"] = list(dict.fromkeys(str(value) for value in reasons))
        if research.get("status") == "qualified":
            research["status"] = "partial"
        research["research_qualified"] = False
        research["execution_qualified"] = False
        qualification = research.get("qualification")
        qualification = dict(qualification) if isinstance(qualification, Mapping) else {}
        qualification.update(
            {
                "research_qualified": False,
                "execution_qualified": False,
                "reason_codes": list(research["reason_codes"]),
            }
        )
        research["qualification"] = qualification
        decision_use = research.get("decision_use")
        if isinstance(decision_use, Mapping):
            decision_use = dict(decision_use)
            decision_use["allocation_authorization_eligible"] = False
            decision_use["reason_codes"] = list(
                dict.fromkeys(
                    [
                        *(str(value) for value in decision_use.get("reason_codes") or []),
                        *research["reason_codes"],
                    ]
                )
            )
            research["decision_use"] = decision_use

    timed_out = sum(row.get("status") == "timeout" for row in resolution_rows)
    missing = sum(row.get("snapshot_ref") is None for row in resolution_rows)
    research["resolution_audit"] = {
        "schema_version": LOOKTHROUGH_RESOLUTION_AUDIT_SCHEMA_VERSION,
        "decision_at": decision.isoformat(),
        "mode": mode,
        "live_policy": "current_refresh_allowed" if allow_live else "store_only",
        "resolution_strategy": "store_scan_then_bounded_live_refresh",
        "max_funds": _bounded_int(cfg.fund_holdings_context_max_funds, 1, 200, 40),
        "live_max_funds": _bounded_int(
            getattr(cfg, "fund_holdings_context_live_max_funds", 8),
            0,
            40,
            8,
        ),
        "worker_count": _bounded_int(cfg.fund_holdings_context_workers, 1, 16, 4),
        "total_timeout_seconds": timeout_seconds,
        "requested_count": len(tasks),
        "resolved_snapshot_count": len(snapshots_by_task),
        "missing_snapshot_count": missing,
        "timed_out_count": timed_out,
        "truncated_count": len(omitted),
        "truncated_funds": omitted,
        "live_refresh_deferred_count": len(refresh_deferred),
        "live_refresh_deferred_funds": refresh_deferred,
        "portfolio_input": portfolio.audit,
        "rows": resolution_rows,
        "raw_snapshots_included": False,
        "raw_holdings_included": False,
    }
    research["raw_snapshots_included"] = False
    research["raw_holdings_included"] = False
    research["research_hash"] = _research_hash(research)
    return research


def _resolve_batch(
    tasks: Sequence[_Task],
    *,
    decision: datetime,
    allow_live: bool,
    timeout_seconds: float,
    workers: int,
    refresh_retry_ttl_seconds: float,
    resolver: SnapshotResolver,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    if not tasks:
        return {}, []
    user_id = try_get_request_user_id()
    def resolve(task: _Task) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            return resolver(
                task.fund_code,
                decision_at=decision,
                allow_live=allow_live,
                refresh_retry_ttl_seconds=refresh_retry_ttl_seconds,
            )

        return work() if user_id is None else run_with_request_user(user_id, work)

    executor = ThreadPoolExecutor(
        max_workers=min(workers, len(tasks)),
        thread_name_prefix="fund-lookthrough",
    )
    futures: dict[Future[dict[str, Any]], _Task] = {
        executor.submit(resolve, task): task for task in tasks
    }
    done, pending = wait(set(futures), timeout=timeout_seconds)
    resolutions: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for future in done:
        task = futures[future]
        try:
            value = future.result()
            resolution = dict(value) if isinstance(value, Mapping) else {
                "status": "invalid",
                "reason_codes": ["repository_resolution_invalid"],
            }
        except Exception as exc:  # noqa: BLE001 - one fund cannot block the report
            resolution = {
                "status": "error",
                "qualified": False,
                "reason_codes": ["repository_resolution_error", type(exc).__name__],
                "source": "none",
                "snapshot": None,
            }
        resolutions[task.index] = resolution
        rows.append(
            _resolution_audit_row(
                task,
                resolution,
            )
        )
    for future in pending:
        task = futures[future]
        future.cancel()
        resolution = {
            "status": "timeout",
            "qualified": False,
            "reason_codes": ["snapshot_resolution_total_timeout"],
            "source": "none",
            "snapshot": None,
        }
        resolutions[task.index] = resolution
        rows.append(
            _resolution_audit_row(
                task,
                resolution,
            )
        )
    executor.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda row: int(row["request_index"]))
    return resolutions, rows


def _resolution_audit_row(
    task: _Task,
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = resolution.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    audit = snapshot.get("audit")
    repository = audit.get("snapshot_repository") if isinstance(audit, Mapping) else {}
    repository = repository if isinstance(repository, Mapping) else {}
    refresh = resolution.get("refresh")
    refresh = refresh if isinstance(refresh, Mapping) else {}
    freshness = snapshot.get("freshness")
    freshness = freshness if isinstance(freshness, Mapping) else {}
    snapshot_hash = str(snapshot.get("snapshot_hash") or "").strip().lower()
    target_matches = not snapshot or _fund_code(snapshot.get("fund_code")) == task.fund_code
    reasons = [str(value) for value in resolution.get("reason_codes") or [] if value]
    if not target_matches:
        reasons.append("snapshot_fund_code_mismatch")
        snapshot_hash = ""
    return {
        "request_index": task.index,
        "fund_code": task.fund_code,
        "role": task.role,
        "status": str(resolution.get("status") or "unavailable"),
        "qualified": bool(resolution.get("qualified") is True and target_matches),
        "source": str(resolution.get("source") or "none"),
        "freshness": freshness.get("label"),
        "reason_codes": list(dict.fromkeys(reasons)),
        "snapshot_ref": snapshot_hash[:12] if len(snapshot_hash) == 64 else None,
        "snapshot_hash": snapshot_hash if len(snapshot_hash) == 64 else None,
        "as_of_date": snapshot.get("as_of_date"),
        "available_at": snapshot.get("available_at"),
        "first_observed_at": repository.get("first_observed_at"),
        "live_attempted": bool(
            repository.get("live_attempted") is True
            or refresh.get("live_attempted") is True
        ),
        "persistence_failed": bool(
            repository.get("persistence_failed") is True
            or refresh.get("persistence_failed") is True
        ),
        "refresh_reason": repository.get("refresh_reason") or refresh.get("reason"),
        "refresh_throttled": bool(
            repository.get("refresh_throttled") is True
            or refresh.get("throttled") is True
        ),
    }


def _live_refresh_required(resolution: Mapping[str, Any] | None) -> bool:
    if not isinstance(resolution, Mapping):
        return True
    snapshot = resolution.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return True
    if resolution.get("qualified") is not True:
        return True
    freshness = snapshot.get("freshness")
    label = (
        str(freshness.get("label") or "unknown")
        if isinstance(freshness, Mapping)
        else "unknown"
    )
    if label != "fresh":
        return True
    refresh = resolution.get("refresh")
    refresh_reason = (
        str(refresh.get("reason") or "")
        if isinstance(refresh, Mapping)
        else ""
    )
    return refresh_reason in {
        "scheduled_disclosure_recheck",
        "stored_snapshot_invalid",
        "store_miss",
    }


def _current_run_observation_proof(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    decision: datetime,
    allow_live: bool,
) -> dict[str, Any] | None:
    """Bind post-decision observations to this frozen live-resolution run."""

    if not allow_live:
        return None
    rows: list[dict[str, str]] = []
    for snapshot in snapshots:
        audit = snapshot.get("audit")
        repository = (
            audit.get("snapshot_repository") if isinstance(audit, Mapping) else None
        )
        if (
            not isinstance(repository, Mapping)
            or repository.get("live_attempted") is not True
            or repository.get("source") != "live_resolver_saved"
            or repository.get("persistence_failed") is True
        ):
            continue
        first_observed = _aware_datetime(repository.get("first_observed_at"))
        snapshot_hash = str(snapshot.get("snapshot_hash") or "").strip().lower()
        if (
            first_observed is None
            or first_observed <= decision
            or len(snapshot_hash) != 64
        ):
            continue
        rows.append(
            {
                "snapshot_hash": snapshot_hash,
                "first_observed_at": first_observed.isoformat(),
            }
        )
    if not rows:
        return None
    rows.sort(key=lambda row: (row["snapshot_hash"], row["first_observed_at"]))
    observed_at = max(row["first_observed_at"] for row in rows)
    material = {
        "mode": "current_live_same_run",
        "decision_at": decision.isoformat(),
        "observed_at": observed_at,
        "snapshot_hashes": [row["snapshot_hash"] for row in rows],
        "observations": rows,
        "source": "fund_lookthrough_context.live_resolution",
    }
    return {
        **material,
        "ref_id": _research_hash(material),
    }


def _portfolio_input(
    holdings: Sequence[Any],
    context: Mapping[str, Any] | None,
    *,
    decision: datetime,
) -> _PortfolioInput:
    snapshot = context.get("position_snapshot") if isinstance(context, Mapping) else None
    if not isinstance(snapshot, Mapping):
        positions = _fallback_positions(holdings)
        envelope = {
            "positions": positions,
            "positions_complete": False,
            "available_at": _safe_past_time((context or {}).get("fetched_at"), decision),
            "as_of_date": (context or {}).get("as_of_date"),
            "source": "analysis_request",
            "ref_id": (context or {}).get("snapshot_id"),
        }
        return _PortfolioInput(
            positions=positions,
            envelope=envelope,
            positions_complete=False,
            denominator_yuan=None,
            denominator_source=None,
            audit={
                "status": "fund_holdings_only",
                "reason_codes": ["position_snapshot_missing"],
                "position_amount_source": "request_holding_amount",
                "whole_account_denominator_qualified": False,
            },
        )

    captured_at = _aware_datetime(snapshot.get("captured_at") or snapshot.get("snapshot_at"))
    as_of = _date_value(
        snapshot.get("as_of_date")
        or snapshot.get("snapshot_date")
        or snapshot.get("position_as_of")
    )
    source = str(snapshot.get("source") or snapshot.get("source_type") or "").strip()
    snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
    rows: list[dict[str, Any]] = []
    position_reasons: list[str] = []
    valuation_pit = True
    raw_rows = snapshot.get("positions")
    raw_rows = raw_rows if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)) else []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            position_reasons.append("position_row_invalid")
            continue
        code = _fund_code(raw.get("fund_code"))
        shares = _finite(raw.get("settled_shares"))
        if code is None:
            continue
        if shares is None:
            position_reasons.append(f"settled_shares_missing:{code}")
        elif shares <= 0:
            continue
        market_value = _finite(raw.get("market_value_cny"))
        if market_value is None or market_value < 0:
            position_reasons.append(f"market_value_missing:{code}")
            continue
        nav_date = _date_value(raw.get("nav_date"))
        valuation_source = str(raw.get("valuation_source") or "").strip()
        if nav_date is None or nav_date > decision.date() or not valuation_source:
            valuation_pit = False
            position_reasons.append(f"valuation_pit_unqualified:{code}")
        rows.append({"fund_code": code, "holding_amount": market_value})
    rows.sort(key=lambda row: row["fund_code"])

    completeness = snapshot.get("completeness")
    completeness = completeness if isinstance(completeness, Mapping) else {}
    conflicts = snapshot.get("conflicts")
    conflicts = conflicts if isinstance(conflicts, Sequence) and not isinstance(conflicts, (str, bytes)) else []
    time_pit = bool(
        captured_at is not None
        and captured_at <= decision
        and as_of is not None
        and as_of <= decision.date()
    )
    positions_complete = bool(
        isinstance(context, Mapping)
        and context.get("authoritative") is True
        and context.get("position_complete") is True
        and snapshot.get("position_complete") is True
        and completeness.get("valuation_complete") is True
        and valuation_pit
        and time_pit
        and source
        and snapshot_id
        and not position_reasons
        and not conflicts
        and int(snapshot.get("pending_transaction_count") or 0) == 0
        and int(snapshot.get("known_unsettled_transaction_count") or 0) == 0
        and not snapshot.get("ledger_truncated")
    )
    cash = snapshot.get("cash")
    cash = cash if isinstance(cash, Mapping) else {}
    totals = snapshot.get("totals")
    totals = totals if isinstance(totals, Mapping) else {}
    denominator = _finite(totals.get("total_assets_cny"))
    cash_qualified = bool(
        cash.get("known") is True
        and completeness.get("cash_complete") is True
        and _finite(cash.get("balance_cny")) is not None
    )
    fund_sum = sum(float(row["holding_amount"]) for row in rows)
    denominator_qualified = bool(
        positions_complete
        and cash_qualified
        and denominator is not None
        and denominator > 0
        and denominator + 1e-8 >= fund_sum
    )
    denominator_source = (
        {
            "source": source,
            "ref_id": snapshot_id,
            "available_at": captured_at.isoformat(),
            "as_of_date": as_of.isoformat(),
            "first_observed_at": captured_at.isoformat(),
        }
        if denominator_qualified and captured_at is not None and as_of is not None
        else None
    )
    envelope = {
        "positions": rows,
        "positions_complete": positions_complete,
        "available_at": captured_at.isoformat() if captured_at is not None else None,
        "as_of_date": as_of.isoformat() if as_of is not None else None,
        "source": source or None,
        "ref_id": snapshot_id or None,
        "first_observed_at": captured_at.isoformat() if captured_at is not None else None,
    }
    reasons = list(position_reasons)
    if not time_pit:
        reasons.append("position_snapshot_not_pit_qualified")
    if not positions_complete:
        reasons.append("position_snapshot_incomplete")
    if not cash_qualified:
        reasons.append("cash_not_qualified")
    return _PortfolioInput(
        positions=rows,
        envelope=envelope,
        positions_complete=positions_complete,
        denominator_yuan=denominator if denominator_qualified else None,
        denominator_source=denominator_source,
        audit={
            "status": "whole_account" if denominator_qualified else "fund_holdings_only",
            "reason_codes": list(dict.fromkeys(reasons)),
            "position_snapshot_ref": snapshot_id or None,
            "position_amount_source": "position_snapshot.market_value_cny",
            "position_complete": positions_complete,
            "valuation_pit_qualified": valuation_pit and time_pit,
            "cash_qualified": cash_qualified,
            "whole_account_denominator_qualified": denominator_qualified,
        },
    )


def _unavailable_context(
    *,
    decision_at: object,
    reason: str,
    detail: str | None = None,
    resolution_rows: Sequence[Mapping[str, Any]] | None = None,
    portfolio_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons = [reason, *([detail] if detail else [])]
    payload: dict[str, Any] = {
        "schema_version": "fund_lookthrough_research.v1",
        "decision_at": decision_at.isoformat() if isinstance(decision_at, datetime) else str(decision_at),
        "status": "unavailable",
        "scope": None,
        "research_qualified": False,
        "execution_qualified": False,
        "reason_codes": reasons,
        "qualification": {
            "research_qualified": False,
            "execution_qualified": False,
            "reason_codes": reasons,
        },
        "portfolio": None,
        "existing_funds": [],
        "candidates": [],
        "resolution_audit": {
            "schema_version": LOOKTHROUGH_RESOLUTION_AUDIT_SCHEMA_VERSION,
            "rows": [dict(row) for row in resolution_rows or []],
            "portfolio_input": dict(portfolio_audit or {}),
            "raw_snapshots_included": False,
            "raw_holdings_included": False,
        },
        "raw_snapshots_included": False,
        "raw_holdings_included": False,
    }
    payload["research_hash"] = _research_hash(payload)
    return payload


def _bounded_tasks(
    existing_codes: Sequence[str],
    candidate_codes: Sequence[str],
    *,
    max_funds: int,
) -> tuple[list[_Task], list[dict[str, str]]]:
    ordered = [
        *((code, "existing") for code in existing_codes),
        *((code, "candidate") for code in candidate_codes),
    ]
    tasks = [
        _Task(index=index, fund_code=code, role=role)
        for index, (code, role) in enumerate(ordered[:max_funds])
    ]
    omitted = [
        {"fund_code": code, "role": role, "reason": "max_funds_exceeded"}
        for code, role in ordered[max_funds:]
    ]
    return tasks, omitted


def _fallback_positions(holdings: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for holding in holdings:
        code = _fund_code(_value(holding, "fund_code"))
        amount = _finite(_value(holding, "holding_amount"))
        if code is not None and amount is not None and amount >= 0:
            rows.append({"fund_code": code, "holding_amount": amount})
    return _dedupe_positions(rows)


def _holding_codes(holdings: Sequence[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            code
            for code in (_fund_code(_value(item, "fund_code")) for item in holdings)
            if code is not None
        )
    )


def _candidate_codes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            code
            for code in (_fund_code(row.get("fund_code")) for row in rows)
            if code is not None
        )
    )


def _position_codes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(row["fund_code"]) for row in rows if float(row.get("holding_amount") or 0) > 0]


def _dedupe_positions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, float] = {}
    for row in rows:
        code = str(row["fund_code"])
        by_code[code] = by_code.get(code, 0.0) + float(row["holding_amount"])
    return [
        {"fund_code": code, "holding_amount": amount}
        for code, amount in sorted(by_code.items())
    ]


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if text.isdigit() and 1 <= len(text) <= 6:
        return text.zfill(6)
    return None


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(CN_TZ)


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(CN_TZ).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _safe_past_time(value: object, decision: datetime) -> str:
    parsed = _aware_datetime(value)
    return (parsed if parsed is not None and parsed <= decision else decision).isoformat()


def _bounded_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return min(max(parsed, minimum), maximum)


def _bounded_float(
    value: object,
    minimum: float,
    maximum: float,
    fallback: float,
) -> float:
    parsed = _finite(value)
    if parsed is None:
        return fallback
    return min(max(parsed, minimum), maximum)


def _research_hash(value: Mapping[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "research_hash"}
    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LOOKTHROUGH_RESOLUTION_AUDIT_SCHEMA_VERSION",
    "build_fund_lookthrough_context",
]
