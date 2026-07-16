from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.database import _connect, _decision_store_authority, list_fund_profiles
from app.models import ConfirmPortfolioLedgerBaselineRequest, FundProfile, Holding
from app.request_context import get_request_user_id
from app.services.decision_repository import (
    append_portfolio_ledger_event,
    canonical_hash,
    list_portfolio_ledger_events,
)
from app.services.portfolio_ledger import (
    build_position_snapshot_payload,
    create_cash_baseline,
    create_legacy_estimated_baseline,
    create_user_confirmed_baseline,
    fold_ledger_events,
)


_CN_TZ = ZoneInfo("Asia/Shanghai")
_LEDGER_READ_LIMIT = 10_000


class PositionTruthStoreUnavailable(RuntimeError):
    """Raised when an authoritative ledger write would land in local fallback."""


class PositionCloseConflict(RuntimeError):
    """Raised when removing a holding would leave a known future trade behind."""

    def __init__(self, transaction_ids: list[str]) -> None:
        super().__init__("该基金仍有待确认或未来生效交易，请先取消/处理交易后再删除持仓")
        self.transaction_ids = sorted(set(transaction_ids))


def has_user_confirmed_position_shares(fund_code: str) -> bool:
    """Return whether the active position contains user/platform-confirmed shares.

    A mixed position may be reported as the weakest aggregate quality, so checking
    only the folded row's ``shares_quality`` would miss a confirmed transaction on
    top of an estimated legacy baseline. Inspect the active source events as well.
    """

    code = (fund_code or "").strip().zfill(6)
    user_id = get_request_user_id()
    if user_id is None or not code or code == "000000":
        return False

    now = _utc_now()
    events = list_portfolio_ledger_events(
        user_id=user_id,
        fund_code=code,
        recorded_at_lte=now.isoformat(),
        limit=_LEDGER_READ_LIMIT,
    )
    if not events:
        return False

    state = fold_ledger_events(
        events,
        position_as_of=now.astimezone(_CN_TZ).date().isoformat(),
        known_at=now,
    )
    position = next(
        (
            row
            for row in state.get("positions") or []
            if str(row.get("fund_code") or "").strip().zfill(6) == code
            and (_decimal(row.get("settled_shares")) or Decimal("0")) > 0
        ),
        None,
    )
    if position is None:
        return False
    if str(position.get("shares_quality") or "unknown") in {
        "user_confirmed",
        "platform_confirmed",
    }:
        return True

    active_source_ids = {
        str(value) for value in position.get("source_event_ids") or [] if value
    }
    for raw in events:
        event = _event_payload(raw)
        if str(event.get("event_id") or "") not in active_source_ids:
            continue
        if str(event.get("shares_quality") or "unknown") in {
            "user_confirmed",
            "platform_confirmed",
        }:
            return True
    return False


def ensure_primary_position_store(connection: Any) -> None:
    """Position truth is fail-closed when configured MySQL is unavailable.

    Reports may be kept in a clearly non-audited fallback store, but confirming
    shares or transactions there would make the UI claim success and then lose
    that truth as soon as MySQL recovers.
    """

    if _decision_store_authority(connection) != "primary":
        raise PositionTruthStoreUnavailable(
            "主数据库暂不可用，未写入份额/交易真值；请稍后重试"
        )


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    payload = result.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            result.setdefault(str(key), value)
    result.setdefault("event_id", result.get("event_revision_id"))
    return result


def _active_fund_codes(holdings: Iterable[Holding]) -> list[str]:
    return sorted(
        {
            holding.fund_code.strip().zfill(6)
            for holding in holdings
            if holding.fund_code
            and holding.fund_code.strip().zfill(6) != "000000"
            and holding.holding_amount > 0
        }
    )


def _baseline_fund_codes(events: Iterable[Mapping[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for raw in events:
        event = _event_payload(raw)
        if event.get("event_type") not in {"opening_baseline", "position_baseline"}:
            continue
        code = str(event.get("fund_code") or "").strip().zfill(6)
        if code and code != "000000":
            codes.add(code)
    return codes


def _confirmed_transaction_fund_codes(
    events: Iterable[Mapping[str, Any]],
) -> set[str]:
    codes: set[str] = set()
    for raw in events:
        event = _event_payload(raw)
        if (
            event.get("event_type") not in {"buy", "sell"}
            or event.get("status") != "confirmed"
        ):
            continue
        code = str(event.get("fund_code") or "").strip().zfill(6)
        if code and code != "000000":
            codes.add(code)
    return codes


def _profile_cost_basis(profile: FundProfile, shares: Decimal) -> Decimal | None:
    unit_cost = _decimal(profile.holding_cost)
    if unit_cost is not None and unit_cost >= 0:
        return unit_cost * shares
    market_value = _decimal(profile.holding_amount)
    profit = _decimal(profile.holding_profit)
    if market_value is not None and profit is not None:
        inferred = market_value - profit
        return inferred if inferred >= 0 else None
    return None


def _legacy_events_for_missing_positions(
    holdings: list[Holding],
    persisted_events: list[Mapping[str, Any]],
    *,
    position_as_of: str,
    recorded_at: datetime | str,
) -> tuple[list[dict[str, Any]], list[str]]:
    active_codes = _active_fund_codes(holdings)
    if not active_codes:
        return [], []
    profiles = {profile.fund_code: profile for profile in list_fund_profiles()}
    existing = _baseline_fund_codes(persisted_events)
    transaction_codes = _confirmed_transaction_fund_codes(persisted_events)
    missing_truth: list[str] = []
    synthetic: list[dict[str, Any]] = []

    for code in active_codes:
        if code in existing:
            continue
        profile = profiles.get(code)
        shares = _decimal(profile.holding_shares) if profile is not None else None
        effective_at = (
            profile.shares_baseline_date
            if profile is not None and profile.shares_baseline_date
            else position_as_of
        )
        # A profile stores the absolute baseline only.  Never call the legacy
        # effective-shares helper here: it already adds confirmed transactions,
        # which the persisted ledger will fold again (100 + 10 became 120).
        # Equally, a baseline recorded after the requested as-of date must not
        # be projected backwards into a stale/historical snapshot.
        if effective_at[:10] > position_as_of[:10]:
            missing_truth.append(code)
            continue
        if shares is None or shares <= 0:
            # A new position can be fully established by its first confirmed
            # buy event starting from zero.  Adding a synthetic zero baseline
            # would incorrectly downgrade that user-confirmed transaction to
            # legacy-estimated quality.
            if code in transaction_codes:
                continue
            missing_truth.append(code)
            continue
        cost = _profile_cost_basis(profile, shares) if profile is not None else None
        synthetic.append(
            create_legacy_estimated_baseline(
                fund_code=code,
                shares=shares,
                cost_basis_total=cost,
                effective_at=effective_at,
                recorded_at=recorded_at,
                source_ref=f"legacy-current:{code}:{position_as_of}",
                event_id=f"legacy-current:{code}:{position_as_of}",
            )
        )
    return synthetic, missing_truth


def _valuation_map(
    holdings: list[Holding],
    events: list[Mapping[str, Any]],
    *,
    position_as_of: str,
    captured_at: datetime | str,
) -> dict[str, dict[str, Any]]:
    state = fold_ledger_events(
        events,
        position_as_of=position_as_of,
        known_at=captured_at,
    )
    shares_by_code = {
        str(row.get("fund_code")): _decimal(row.get("settled_shares"))
        for row in state.get("positions") or []
    }
    valuations: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        code = holding.fund_code.strip().zfill(6)
        shares = shares_by_code.get(code)
        amount = _decimal(
            holding.settled_holding_amount
            if holding.settled_holding_amount is not None
            else holding.holding_amount
        )
        if shares is None or shares <= 0 or amount is None or amount < 0:
            continue
        valuations[code] = {
            "nav": amount / shares,
            "nav_date": position_as_of[:10],
            "source": "derived_market_value_over_frozen_shares",
            "available_at": (
                captured_at.isoformat() if isinstance(captured_at, datetime) else str(captured_at)
            ),
            "is_estimate": True,
        }
    return valuations


def capture_position_snapshot(
    holdings: list[Holding],
    *,
    position_as_of: str,
    captured_at: datetime | str | None = None,
    authoritative: bool,
    source: str,
    legacy_recorded_at: datetime | str | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Freeze the best point-in-time position truth available to a decision.

    Persisted ledger events win. Missing funds are represented by in-memory legacy
    estimated baselines, so old users keep working without their derived shares
    being promoted to confirmed truth.
    """

    user_id = get_request_user_id()
    captured = captured_at or _utc_now()
    persisted_window = list_portfolio_ledger_events(
        user_id=user_id,
        recorded_at_lte=(captured.isoformat() if isinstance(captured, datetime) else str(captured)),
        limit=_LEDGER_READ_LIMIT + 1,
        connection=connection,
    )
    ledger_truncated = len(persisted_window) > _LEDGER_READ_LIMIT
    persisted = list(persisted_window[:_LEDGER_READ_LIMIT])
    synthetic, missing_truth = _legacy_events_for_missing_positions(
        holdings,
        persisted,
        position_as_of=position_as_of,
        recorded_at=legacy_recorded_at or captured,
    )
    events: list[Mapping[str, Any]] = [*persisted, *synthetic]
    valuations = _valuation_map(
        holdings,
        events,
        position_as_of=position_as_of,
        captured_at=captured,
    )
    snapshot = build_position_snapshot_payload(
        events,
        user_id=user_id,
        position_as_of=position_as_of,
        captured_at=captured,
        valuations=valuations,
        source=source,
    )
    names = {holding.fund_code.strip().zfill(6): holding.fund_name for holding in holdings}
    for row in snapshot.get("positions") or []:
        row["fund_name"] = names.get(str(row.get("fund_code")))

    active_codes = set(_active_fund_codes(holdings))
    snap_codes = {
        str(row.get("fund_code"))
        for row in snapshot.get("positions") or []
        if (_decimal(row.get("settled_shares")) or Decimal("0")) > 0
    }
    code_mismatch = sorted(active_codes.symmetric_difference(snap_codes))
    completeness = dict(snapshot.get("completeness") or {})
    unsettled_count = int(snapshot.get("known_unsettled_transaction_count") or 0)
    authoritative_empty = bool(
        authoritative
        and not active_codes
        and not snap_codes
        and not missing_truth
        and not code_mismatch
        and not snapshot.get("conflicts")
        and not snapshot.get("unresolved_event_ids")
        and unsettled_count == 0
        and not ledger_truncated
    )
    if authoritative_empty:
        # An explicitly empty, server-owned portfolio is positive truth, not a
        # missing-baseline error.  This is distinct from a ghost ledger position,
        # which is caught by ``code_mismatch`` above.
        completeness.update(
            {
                "settled_position_complete": True,
                "decision_position_complete": True,
                "position_complete": True,
                "position_truth_status": "user_confirmed",
            }
        )
    complete = bool(
        completeness.get(
            "decision_position_complete",
            completeness.get("position_complete"),
        )
    ) and not missing_truth and not code_mismatch and not ledger_truncated
    if ledger_truncated:
        # Values folded from a partial prefix are useful only for diagnosis.  They
        # must never be promoted to executable position truth.
        completeness.update(
            {
                "settled_position_complete": False,
                "position_truth_status": "unknown",
            }
        )
    completeness.update(
        {
            "position_complete": complete,
            "decision_position_complete": complete,
            "missing_position_truth_codes": sorted(missing_truth),
            "portfolio_code_mismatch": code_mismatch,
            "authoritative_portfolio_membership": authoritative,
            "ledger_truncated": ledger_truncated,
            "ledger_event_limit": _LEDGER_READ_LIMIT,
        }
    )
    snapshot.update(
        {
            "schema_version": "portfolio_position_snapshot.v1",
            "snapshot_date": position_as_of[:10],
            "as_of_date": position_as_of[:10],
            "snapshot_at": snapshot.get("captured_at"),
            "authoritative": authoritative,
            "source_type": source,
            "truth_status": completeness.get("position_truth_status", "unknown"),
            "position_complete": complete,
            "completeness": completeness,
            "legacy_estimated_event_count": len(synthetic),
            "persisted_ledger_event_count": len(persisted),
            "ledger_event_count_lower_bound": len(persisted_window),
            "ledger_event_limit": _LEDGER_READ_LIMIT,
            "ledger_truncated": ledger_truncated,
            "missing_position_truth_codes": sorted(missing_truth),
            "cash_yuan": (snapshot.get("cash") or {}).get("balance_cny"),
            "total_market_value_yuan": (snapshot.get("totals") or {}).get(
                "invested_market_value_cny"
            ),
        }
    )
    return snapshot


def _status_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    positions = [
        row
        for row in snapshot.get("positions") or []
        if (_decimal(row.get("settled_shares")) or Decimal("0")) > 0
    ]
    qualities = {str(row.get("shares_quality") or "unknown") for row in positions}
    if snapshot.get("position_complete") and not positions:
        status = "confirmed_empty"
        message = "当前为空仓，持仓成员真值已确认；现金未知时只限制可执行预算。"
    elif snapshot.get("position_complete") and qualities and qualities.issubset(
        {"user_confirmed", "platform_confirmed"}
    ):
        status = "confirmed"
        message = "实际份额基线已确认；后续决策会冻结当前账本版本。"
    elif positions and any(value in {"user_confirmed", "platform_confirmed"} for value in qualities):
        status = "partial"
        message = "部分基金已确认，仍有份额来自估算或缺失。"
    elif positions:
        status = "estimated"
        message = "当前份额来自历史金额/净值推算，请对照原平台确认。"
    else:
        status = "missing"
        message = "尚无可用份额基线。"
    cash = dict(snapshot.get("cash") or {})
    return {
        "schema_version": "portfolio_ledger_baseline.v1",
        "status": status,
        "ledger_version": snapshot.get("ledger_version"),
        "position_as_of": snapshot.get("position_as_of"),
        "captured_at": snapshot.get("captured_at"),
        "position_complete": snapshot.get("position_complete"),
        "cash": {
            "balance_cny": cash.get("balance_cny"),
            "status": "known" if cash.get("known") else "unknown",
            "quality": cash.get("quality") if cash.get("known") else "unknown",
        },
        "positions": positions,
        "completeness": snapshot.get("completeness"),
        "message": message,
    }


def get_portfolio_ledger_baseline_status() -> dict[str, Any]:
    from app.services.portfolio_holdings_service import load_persisted_holdings

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings(
        fetch_benchmark=False
    )
    now = _utc_now()
    # Baseline status is a current-account view. A stale valuation snapshot may be
    # older than a just-confirmed baseline and must not hide that ledger event.
    position_as_of = now.astimezone(_CN_TZ).date().isoformat()
    with _connect() as connection:
        store_authority = _decision_store_authority(connection)
        snapshot = capture_position_snapshot(
            list(holdings),
            position_as_of=position_as_of,
            captured_at=now,
            authoritative=source in {"snapshot", "profiles", "profiles_recovered"},
            source=f"ledger_baseline_status:{source}",
            legacy_recorded_at=refreshed_at or now,
            connection=connection,
        )
    payload = _status_payload(snapshot)
    payload["store_authority"] = store_authority
    if store_authority != "primary":
        payload.update(
            {
                "status": "unavailable",
                "position_complete": False,
                "message": "主数据库暂不可用；当前仅为本地降级视图，不能确认份额真值。",
            }
        )
    return payload


def _source_ref(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}:{canonical_hash(payload)[:24]}"


def _known_source_refs(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for row in rows:
        source = str(row.get("source") or "")
        source_ref = str(row.get("source_ref") or "")
        if source and source_ref:
            result.add((source, source_ref))
    return result


def _update_profile_baseline_in_connection(
    connection: Any,
    *,
    user_id: int,
    fund_code: str,
    shares: float,
    cost_basis_total: float | None,
    as_of_date: str,
) -> None:
    row = connection.execute(
        "SELECT payload FROM fund_profiles WHERE userId = ? AND fund_code = ?",
        (user_id, fund_code),
    ).fetchone()
    if row is None:
        raise LookupError(f"未找到当前持仓基金 {fund_code}")
    raw_payload = row["payload"] if not isinstance(row, tuple) else row[0]
    profile = FundProfile.model_validate(json.loads(raw_payload))
    # Once the user promotes shares to confirmed truth, an unconfirmed legacy
    # cost must not survive in the compatibility read model as if it were also
    # confirmed.  Keeping it would make downstream prompts silently mix a real
    # share count with an estimated cost basis.
    unit_cost = (
        round(float(cost_basis_total) / float(shares), 8)
        if cost_basis_total is not None and shares > 0
        else None
    )
    updated = profile.model_copy(
        update={
            "holding_shares": round(float(shares), 6),
            "holding_cost": unit_cost,
            "shares_baseline_date": as_of_date,
        }
    )
    connection.execute(
        "UPDATE fund_profiles SET payload = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE userId = ? AND fund_code = ?",
        (json.dumps(updated.model_dump(mode="json"), ensure_ascii=False), user_id, fund_code),
    )


def confirm_portfolio_ledger_baseline(
    request: ConfirmPortfolioLedgerBaselineRequest,
) -> dict[str, Any]:
    """Append a user-confirmed absolute baseline and update the legacy read model."""

    user_id = get_request_user_id()
    recorded_at = _utc_now()
    as_of_date = request.as_of_date.isoformat()
    if request.as_of_date > recorded_at.astimezone(_CN_TZ).date():
        raise ValueError("持仓生效日不能晚于今天")
    codes = [row.fund_code.strip().zfill(6) for row in request.positions]
    if len(codes) != len(set(codes)):
        raise ValueError("同一基金不能在账本基线中重复出现")

    with _connect() as connection:
        ensure_primary_position_store(connection)
        existing = list_portfolio_ledger_events(user_id=user_id, connection=connection)
        refs = _known_source_refs(existing)
        for row in request.positions:
            content = {
                "fund_code": row.fund_code,
                "confirmed_shares": row.confirmed_shares,
                "cost_basis_total_yuan": row.cost_basis_total_yuan,
                "as_of_date": as_of_date,
            }
            source_ref = _source_ref("manual-baseline", content)
            if ("user_confirmation", source_ref) not in refs:
                event = create_user_confirmed_baseline(
                    fund_code=row.fund_code,
                    confirmed_shares=row.confirmed_shares,
                    cost_basis_total=row.cost_basis_total_yuan,
                    effective_at=as_of_date,
                    recorded_at=recorded_at,
                    source_ref=source_ref,
                )
                append_portfolio_ledger_event(
                    user_id=user_id,
                    event=event,
                    connection=connection,
                )
            _update_profile_baseline_in_connection(
                connection,
                user_id=user_id,
                fund_code=row.fund_code,
                shares=row.confirmed_shares,
                cost_basis_total=row.cost_basis_total_yuan,
                as_of_date=as_of_date,
            )

        if request.cash_balance_yuan is not None:
            cash_content = {
                "cash_balance_yuan": request.cash_balance_yuan,
                "as_of_date": as_of_date,
            }
            cash_ref = _source_ref("manual-cash-baseline", cash_content)
            if ("user_confirmation", cash_ref) not in refs:
                cash_event = create_cash_baseline(
                    cash_balance=request.cash_balance_yuan,
                    effective_at=as_of_date,
                    recorded_at=recorded_at,
                    source_ref=cash_ref,
                )
                if cash_event is not None:
                    append_portfolio_ledger_event(
                        user_id=user_id,
                        event=cash_event,
                        connection=connection,
                    )

    return get_portfolio_ledger_baseline_status()


def close_portfolio_position(
    fund_code: str,
    *,
    effective_at: str | None = None,
    source_context: str = "portfolio-removal",
) -> dict[str, Any]:
    """Append an absolute zero baseline when the user removes a holding.

    Deleting only the legacy snapshot/profile would leave earlier ledger buys
    alive and resurrect a ghost position in the next decision preflight.
    """

    code = fund_code.strip().zfill(6)
    if not code or code == "000000":
        raise ValueError("fund_code 必须是有效的六位基金代码")
    user_id = get_request_user_id()
    recorded_at = _utc_now()
    resolved_date = effective_at or recorded_at.astimezone(_CN_TZ).date().isoformat()
    content = {
        "fund_code": code,
        "confirmed_shares": 0,
        "cost_basis_total_yuan": 0,
        "as_of_date": resolved_date,
        "source_context": source_context,
    }
    source_ref = _source_ref("portfolio-removal", content)
    event_id = f"portfolio-removal:{canonical_hash(content)[:32]}"
    with _connect() as connection:
        ensure_primary_position_store(connection)
        fund_events = list_portfolio_ledger_events(
            user_id=user_id,
            fund_code=code,
            connection=connection,
        )
        unsettled_state = fold_ledger_events(
            fund_events,
            position_as_of=resolved_date,
            known_at=recorded_at,
        )
        blocking_ids = [
            str(value)
            for value in unsettled_state.get("known_unsettled_event_ids") or []
            if value
        ]
        cursor = connection.execute(
            "SELECT id FROM fund_transactions "
            "WHERE userId = ? AND fund_code = ? "
            "AND (status = 'pending' OR (status = 'confirmed' AND confirm_date > ?))",
            (user_id, code, resolved_date),
        )
        for raw in cursor.fetchall():
            if isinstance(raw, Mapping):
                transaction_id = raw.get("id")
            else:
                try:
                    transaction_id = raw["id"]
                except (IndexError, KeyError, TypeError):
                    transaction_id = raw[0] if raw else None
            if transaction_id:
                blocking_ids.append(str(transaction_id))
        if blocking_ids:
            raise PositionCloseConflict(blocking_ids)

        known = _known_source_refs(fund_events)
        if ("user_confirmation", source_ref) in known:
            return {"fund_code": code, "source_ref": source_ref, "inserted": False}
        event = create_user_confirmed_baseline(
            fund_code=code,
            confirmed_shares=0,
            cost_basis_total=0,
            effective_at=resolved_date,
            recorded_at=recorded_at,
            source_ref=source_ref,
            event_id=event_id,
        )
        append_portfolio_ledger_event(
            user_id=user_id,
            event=event,
            connection=connection,
        )
    return {"fund_code": code, "source_ref": source_ref, "inserted": True}


def transaction_ledger_event_from_fund_transaction(
    tx: Any,
    *,
    status: str | None = None,
    supersedes_event_id: str | None = None,
) -> dict[str, Any]:
    resolved_status = status or str(tx.status)
    suffix = resolved_status
    event_id = f"fund-transaction:{tx.id}:{suffix}"
    confirmed_shares = tx.confirmed_shares
    if confirmed_shares is None and tx.shares_delta is not None:
        confirmed_shares = abs(float(tx.shares_delta))
    return create_user_transaction_event(
        event_id=event_id,
        fund_code=str(tx.fund_code),
        direction=str(tx.direction),
        status=resolved_status,
        effective_at=str(tx.confirm_date),
        recorded_at=(tx.confirmed_at or tx.created_at),
        confirmed_shares=confirmed_shares,
        gross_amount=tx.amount_yuan,
        fee_yuan=tx.fee_yuan,
        nav=tx.nav_on_confirm,
        source_ref=f"{tx.id}:{suffix}",
        shares_quality=(
            "user_confirmed" if tx.shares_source == "user_confirmed" else "derived"
        ),
        supersedes_event_id=supersedes_event_id,
    )


def create_user_transaction_event(**kwargs: Any) -> dict[str, Any]:
    # Kept behind a local wrapper so integration tests can patch one stable seam.
    from app.services.portfolio_ledger import create_transaction_event

    return create_transaction_event(**kwargs)
