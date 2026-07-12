from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo


LEDGER_SCHEMA_VERSION = "1.0"
GENESIS_HASH = "0" * 64
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

_CONFIRMED_STATUS = "confirmed"
_BASELINE_TYPES = frozenset({"opening_baseline", "position_baseline"})
_TRANSACTION_TYPES = frozenset({"buy", "sell"})
_QUALITY_RANK = {
    "platform_confirmed": 4,
    "user_confirmed": 3,
    "derived": 2,
    "estimated_legacy": 1,
    "unknown": 0,
}


class LedgerIntegrityError(ValueError):
    """Raised when append-only ledger identities or hashes are contradictory."""


def _decimal(value: object, *, field: str = "value") -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def canonical_decimal(value: object) -> str | None:
    """Return a non-exponent decimal string suitable for durable hashes."""

    parsed = _decimal(value)
    if parsed is None:
        return None
    if parsed == 0:
        return "0"
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _canonical_value(value: object) -> object:
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):
        return _iso_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        # Floats may enter through legacy payloads. Convert through ``str`` so a
        # binary representation artifact never becomes part of a ledger hash.
        return canonical_decimal(value)
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _coerce_datetime(value: datetime | date | str, *, end_of_day: bool = False) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if end_of_day else time.min)
    else:
        text_value = str(value or "").strip()
        if not text_value:
            raise ValueError("timestamp is required")
        if len(text_value) == 10:
            parsed_date = date.fromisoformat(text_value)
            parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min)
        else:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: datetime | date | str, *, end_of_day: bool = False) -> str:
    return _coerce_datetime(value, end_of_day=end_of_day).isoformat()


def _fund_code(value: object) -> str:
    code = str(value or "").strip()
    if not code or not code.isdigit() or len(code) > 6:
        raise ValueError("fund_code must contain at most six digits")
    return code.zfill(6)


def _non_negative(value: object, *, field: str, allow_zero: bool = True) -> Decimal | None:
    parsed = _decimal(value, field=field)
    if parsed is None:
        return None
    if parsed < 0 or (not allow_zero and parsed == 0):
        comparator = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {comparator}")
    return parsed


def _stable_event_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _base_event(
    *,
    event_type: str,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    event_id: str | None,
    source: str,
    source_ref: str | None,
    status: str = _CONFIRMED_STATUS,
    fund_code: str | None = None,
    supersedes_event_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event_payload = _canonical_value(dict(payload or {}))
    if not isinstance(event_payload, dict):
        raise ValueError("ledger event payload must be an object")
    body: dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "event_type": event_type,
        "fund_code": _fund_code(fund_code) if fund_code is not None else None,
        "effective_at": _iso_timestamp(effective_at),
        "recorded_at": _iso_timestamp(recorded_at),
        "status": status,
        "source": source,
        "source_ref": str(source_ref) if source_ref else None,
        "supersedes_event_id": supersedes_event_id,
        **event_payload,
    }
    resolved_event_id = str(event_id or _stable_event_id(event_type, body))
    body.update(
        {
            # ``logical_event_id`` / ``revision_no`` / ``payload`` are the
            # repository contract. ``event_id`` and flattened payload fields are
            # retained so the pure folding API remains convenient and backward
            # compatible for callers that have not persisted the event yet.
            "event_id": resolved_event_id,
            "logical_event_id": resolved_event_id,
            "revision_no": 1,
            "payload": {
                **event_payload,
                "supersedes_event_id": supersedes_event_id,
            },
        }
    )
    return _canonical_value(body)  # type: ignore[return-value]


def create_legacy_estimated_baseline(
    *,
    fund_code: str,
    shares: object,
    cost_basis_total: object | None,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    source_ref: str | None = None,
    event_id: str | None = None,
    supersedes_event_id: str | None = None,
) -> dict[str, Any]:
    """Create an opening observation without pretending inferred legacy data is truth."""

    absolute_shares = _non_negative(shares, field="shares")
    if absolute_shares is None:
        raise ValueError("shares is required for a legacy baseline")
    cost = _non_negative(cost_basis_total, field="cost_basis_total")
    return _base_event(
        event_type="opening_baseline",
        fund_code=fund_code,
        effective_at=effective_at,
        recorded_at=recorded_at,
        event_id=event_id,
        source="legacy_portfolio_profile",
        source_ref=source_ref,
        supersedes_event_id=supersedes_event_id,
        payload={
            "absolute_shares": canonical_decimal(absolute_shares),
            "absolute_cost_basis": canonical_decimal(cost),
            "shares_quality": "estimated_legacy",
            "cost_quality": "estimated_legacy" if cost is not None else "unknown",
            "baseline_kind": "legacy_estimated",
        },
    )


def create_user_confirmed_baseline(
    *,
    fund_code: str,
    confirmed_shares: object,
    cost_basis_total: object | None,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    source_ref: str | None = None,
    event_id: str | None = None,
    supersedes_event_id: str | None = None,
) -> dict[str, Any]:
    """Create the one-time/reconciliation baseline required for real share truth."""

    shares = _non_negative(confirmed_shares, field="confirmed_shares")
    if shares is None:
        raise ValueError("confirmed_shares is required")
    cost = _non_negative(cost_basis_total, field="cost_basis_total")
    return _base_event(
        event_type="opening_baseline",
        fund_code=fund_code,
        effective_at=effective_at,
        recorded_at=recorded_at,
        event_id=event_id,
        source="user_confirmation",
        source_ref=source_ref,
        supersedes_event_id=supersedes_event_id,
        payload={
            "absolute_shares": canonical_decimal(shares),
            "absolute_cost_basis": canonical_decimal(cost),
            "shares_quality": "user_confirmed",
            "cost_quality": "user_confirmed" if cost is not None else "unknown",
            "baseline_kind": "user_confirmed",
        },
    )


def create_cash_baseline(
    *,
    cash_balance: object | None,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    source_ref: str | None = None,
    event_id: str | None = None,
    quality: str = "user_confirmed",
    supersedes_event_id: str | None = None,
) -> dict[str, Any] | None:
    """Create an absolute cash baseline; unknown cash deliberately creates no event."""

    balance = _non_negative(cash_balance, field="cash_balance")
    if balance is None:
        return None
    _validate_quality(quality)
    return _base_event(
        event_type="cash_baseline",
        effective_at=effective_at,
        recorded_at=recorded_at,
        event_id=event_id,
        source="user_confirmation" if quality == "user_confirmed" else "cash_import",
        source_ref=source_ref,
        supersedes_event_id=supersedes_event_id,
        payload={
            "absolute_cash_balance": canonical_decimal(balance),
            "cash_quality": quality,
        },
    )


def create_cash_adjustment(
    *,
    cash_delta: object,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    source_ref: str | None = None,
    event_id: str | None = None,
    quality: str = "user_confirmed",
    supersedes_event_id: str | None = None,
) -> dict[str, Any]:
    delta = _decimal(cash_delta, field="cash_delta")
    if delta is None:
        raise ValueError("cash_delta is required")
    _validate_quality(quality)
    return _base_event(
        event_type="cash_adjustment",
        effective_at=effective_at,
        recorded_at=recorded_at,
        event_id=event_id,
        source="user_confirmation" if quality == "user_confirmed" else "cash_import",
        source_ref=source_ref,
        supersedes_event_id=supersedes_event_id,
        payload={"cash_delta": canonical_decimal(delta), "cash_quality": quality},
    )


def create_transaction_event(
    *,
    event_id: str,
    fund_code: str,
    direction: str,
    status: str,
    effective_at: datetime | date | str,
    recorded_at: datetime | date | str,
    confirmed_shares: object | None = None,
    gross_amount: object | None = None,
    fee_yuan: object | None = None,
    nav: object | None = None,
    net_cash_delta: object | None = None,
    source_ref: str | None = None,
    source: str = "user_transaction",
    shares_quality: str | None = None,
    amount_quality: str = "derived",
    supersedes_event_id: str | None = None,
) -> dict[str, Any]:
    """Create a transaction event while preserving actual shares and unknown fees."""

    if direction not in _TRANSACTION_TYPES:
        raise ValueError("direction must be buy or sell")
    if status not in {"pending", "confirmed", "superseded", "skipped", "reversed"}:
        raise ValueError("unsupported transaction status")
    gross = _non_negative(gross_amount, field="gross_amount")
    fee = _non_negative(fee_yuan, field="fee_yuan")
    unit_nav = _non_negative(nav, field="nav", allow_zero=False)
    shares = _non_negative(confirmed_shares, field="confirmed_shares", allow_zero=False)
    quality = shares_quality
    if shares is not None:
        quality = quality or "user_confirmed"
    elif gross is not None and unit_nav is not None:
        shares = gross / unit_nav
        quality = quality or "derived"
    else:
        quality = quality or "unknown"
    _validate_quality(quality)
    _validate_quality(amount_quality)

    shares_delta = None
    if shares is not None:
        shares_delta = shares if direction == "buy" else -shares

    cash_delta = _decimal(net_cash_delta, field="net_cash_delta")
    if cash_delta is None and gross is not None and fee is not None:
        cash_delta = -(gross + fee) if direction == "buy" else gross - fee

    return _base_event(
        event_type=direction,
        fund_code=fund_code,
        effective_at=effective_at,
        recorded_at=recorded_at,
        event_id=event_id,
        source=source,
        source_ref=source_ref,
        status=status,
        supersedes_event_id=supersedes_event_id,
        payload={
            "shares_delta": canonical_decimal(shares_delta),
            "confirmed_shares": canonical_decimal(shares),
            "shares_quality": quality,
            "gross_amount": canonical_decimal(gross),
            "amount_quality": amount_quality,
            # ``None`` is semantically important: it means unknown, never zero.
            "fee_yuan": canonical_decimal(fee),
            "fee_known": fee is not None,
            "nav": canonical_decimal(unit_nav),
            "net_cash_delta": canonical_decimal(cash_delta),
        },
    )


def _validate_quality(quality: str) -> None:
    if quality not in _QUALITY_RANK:
        raise ValueError(f"unsupported quality: {quality}")


def _event_content(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in event.items()
        if key not in {"previous_hash", "event_hash"}
    }


def _normalize_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Accept both pre-persistence events and decision_repository rows."""

    event = dict(raw)
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LedgerIntegrityError("ledger payload is not valid JSON") from exc
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            event.setdefault(str(key), value)
        event["payload"] = dict(payload)
    event_id = (
        event.get("event_id")
        # Supersession is expressed against the stable logical id.  Repository
        # rows also carry a physical revision id, but choosing that first makes
        # a confirmed transaction unable to supersede its pending counterpart.
        or event.get("logical_event_id")
        or event.get("event_revision_id")
        or (
            f"{event.get('logical_event_id')}:{event.get('revision_no', 1)}"
            if event.get("logical_event_id")
            else None
        )
    )
    if event_id:
        event["event_id"] = str(event_id)
    return _canonical_value(event)  # type: ignore[return-value]


def _event_sort_key(event: Mapping[str, Any]) -> tuple[str, int, str]:
    recorded = _iso_timestamp(str(event.get("recorded_at")))
    try:
        revision = int(event.get("ledger_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return recorded, revision, str(event.get("event_id") or "")


def _dedupe_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = _normalize_event(raw)
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise LedgerIntegrityError("ledger event_id is required")
        content = _event_content(event)
        previous = by_id.get(event_id)
        if previous is not None:
            if _canonical_json(_event_content(previous)) != _canonical_json(content):
                raise LedgerIntegrityError(f"conflicting ledger event_id: {event_id}")
            continue
        by_id[event_id] = event
    return sorted(by_id.values(), key=_event_sort_key)


def chain_ledger_events(
    events: Iterable[Mapping[str, Any]],
    *,
    genesis_hash: str = GENESIS_HASH,
) -> list[dict[str, Any]]:
    """Build a deterministic append-only hash chain without mutating inputs."""

    previous_hash = str(genesis_hash)
    chained: list[dict[str, Any]] = []
    for event in _dedupe_events(events):
        content = _event_content(event)
        digest = hashlib.sha256(
            (previous_hash + _canonical_json(content)).encode("utf-8")
        ).hexdigest()
        chained_event = {
            **content,
            "previous_hash": previous_hash,
            "event_hash": digest,
        }
        chained.append(chained_event)
        previous_hash = digest
    return chained


def _known_events(
    events: Iterable[Mapping[str, Any]],
    known_at: datetime | date | str | None,
) -> list[dict[str, Any]]:
    deduped = _dedupe_events(events)
    if known_at is None:
        return deduped
    cutoff = _coerce_datetime(known_at, end_of_day=True)
    return [
        event
        for event in deduped
        if _coerce_datetime(str(event.get("recorded_at"))) <= cutoff
    ]


def build_ledger_version(
    events: Iterable[Mapping[str, Any]],
    *,
    known_at: datetime | date | str | None = None,
) -> str:
    known = _known_events(events, known_at)
    stored_hashes = {
        str(event.get("event_hash"))
        for event in known
        if event.get("event_hash")
    }
    if len(stored_hashes) == len(known) and known:
        referenced = {
            str(event.get("previous_hash"))
            for event in known
            if event.get("previous_hash")
        }
        heads = stored_hashes - referenced
        if len(heads) == 1:
            return f"pl1:{len(known)}:{next(iter(heads))[:16]}"
    chained = chain_ledger_events(known)
    head = chained[-1]["event_hash"] if chained else GENESIS_HASH
    return f"pl1:{len(chained)}:{str(head)[:16]}"


def _combine_quality(current: str, incoming: str) -> str:
    _validate_quality(current)
    _validate_quality(incoming)
    return current if _QUALITY_RANK[current] <= _QUALITY_RANK[incoming] else incoming


def _new_position(code: str) -> dict[str, Any]:
    return {
        "fund_code": code,
        "shares": Decimal("0"),
        "cost_basis": Decimal("0"),
        "shares_quality": "unknown",
        "cost_quality": "unknown",
        "realized_profit": Decimal("0"),
        "realized_before_fee": Decimal("0"),
        "fee_complete": True,
        "source_event_ids": [],
        "baseline_event_id": None,
    }


def _active_events(
    known: list[dict[str, Any]],
    *,
    position_cutoff: datetime,
) -> tuple[list[dict[str, Any]], set[str]]:
    superseded = {
        str(event.get("supersedes_event_id"))
        for event in known
        if event.get("supersedes_event_id")
        and event.get("status") not in {"pending", "skipped"}
    }
    effective = [
        event
        for event in known
        if _coerce_datetime(str(event.get("effective_at"))) <= position_cutoff
    ]
    active = [event for event in effective if str(event.get("event_id")) not in superseded]
    active.sort(
        key=lambda event: (
            _iso_timestamp(str(event.get("effective_at"))),
            _iso_timestamp(str(event.get("recorded_at"))),
            str(event.get("event_id") or ""),
        )
    )
    return active, superseded


def _known_unsettled_transactions(
    known: list[dict[str, Any]],
    *,
    position_cutoff: datetime,
    superseded: set[str],
) -> list[dict[str, Any]]:
    """Return commitments already known but not settled into position truth."""

    unsettled: list[dict[str, Any]] = []
    for event in known:
        if event.get("event_type") not in _TRANSACTION_TYPES:
            continue
        if str(event.get("event_id") or "") in superseded:
            continue
        status = str(event.get("status") or "")
        effective_at = _coerce_datetime(str(event.get("effective_at")))
        if status == "pending" or (
            status == _CONFIRMED_STATUS and effective_at > position_cutoff
        ):
            unsettled.append(event)
    unsettled.sort(key=_event_sort_key)
    return unsettled


def _transaction_gross(event: Mapping[str, Any], shares: Decimal) -> Decimal | None:
    gross = _decimal(event.get("gross_amount"), field="gross_amount")
    if gross is not None:
        return gross
    nav = _decimal(event.get("nav"), field="nav")
    return abs(shares) * nav if nav is not None else None


def _apply_buy(position: dict[str, Any], event: Mapping[str, Any]) -> bool:
    delta = _decimal(event.get("shares_delta"), field="shares_delta")
    if delta is None or delta <= 0:
        return False
    prior_shares: Decimal = position["shares"]
    prior_cost: Decimal | None = position["cost_basis"]
    gross = _transaction_gross(event, delta)
    fee = _decimal(event.get("fee_yuan"), field="fee_yuan")

    if prior_cost is None or gross is None:
        position["cost_basis"] = None
        position["cost_quality"] = "unknown"
    else:
        # When fee is unknown, retain the ex-fee estimate while explicitly
        # degrading quality and fee completeness. Never substitute a numeric 0.
        addition = gross + fee if fee is not None else gross
        position["cost_basis"] = prior_cost + addition
        incoming_quality = str(event.get("amount_quality") or "derived")
        if fee is None or event.get("gross_amount") is None:
            incoming_quality = "derived"
        position["cost_quality"] = (
            incoming_quality
            if prior_shares == 0 or position["cost_quality"] == "unknown"
            else _combine_quality(position["cost_quality"], incoming_quality)
        )
    position["shares"] = prior_shares + delta
    incoming_shares_quality = str(event.get("shares_quality") or "unknown")
    position["shares_quality"] = (
        incoming_shares_quality
        if prior_shares == 0 or position["shares_quality"] == "unknown"
        else _combine_quality(position["shares_quality"], incoming_shares_quality)
    )
    if fee is None:
        position["fee_complete"] = False
    return True


def _apply_sell(position: dict[str, Any], event: Mapping[str, Any]) -> tuple[bool, str | None]:
    delta = _decimal(event.get("shares_delta"), field="shares_delta")
    if delta is None or delta >= 0:
        return False, "missing_or_invalid_shares"
    quantity = -delta
    prior_shares: Decimal = position["shares"]
    if quantity > prior_shares:
        return False, "oversell"
    if prior_shares <= 0:
        return False, "oversell"

    prior_cost: Decimal | None = position["cost_basis"]
    removed_cost = (
        prior_cost * quantity / prior_shares if prior_cost is not None else None
    )
    remaining_shares = prior_shares - quantity
    if prior_cost is None or removed_cost is None:
        position["cost_basis"] = None if remaining_shares > 0 else Decimal("0")
    else:
        position["cost_basis"] = (
            Decimal("0") if remaining_shares == 0 else prior_cost - removed_cost
        )
    position["shares"] = remaining_shares
    incoming_quality = str(event.get("shares_quality") or "unknown")
    position["shares_quality"] = _combine_quality(
        position["shares_quality"], incoming_quality
    )

    gross = _transaction_gross(event, delta)
    fee = _decimal(event.get("fee_yuan"), field="fee_yuan")
    if gross is None or removed_cost is None:
        position["realized_before_fee"] = None
        position["realized_profit"] = None
    else:
        before_fee = gross - removed_cost
        if position["realized_before_fee"] is not None:
            position["realized_before_fee"] += before_fee
        if (
            fee is None
            or position["realized_profit"] is None
            or not bool(position["fee_complete"])
        ):
            position["realized_profit"] = None
        else:
            position["realized_profit"] += before_fee - fee
    if fee is None:
        position["fee_complete"] = False
    return True, None


def _position_row(position: Mapping[str, Any]) -> dict[str, Any]:
    shares: Decimal = position["shares"]
    cost: Decimal | None = position["cost_basis"]
    average = cost / shares if cost is not None and shares > 0 else None
    return {
        "fund_code": position["fund_code"],
        "settled_shares": canonical_decimal(shares),
        "cost_basis_total_cny": canonical_decimal(cost),
        "average_unit_cost_cny": canonical_decimal(average),
        "shares_quality": position["shares_quality"],
        "cost_quality": position["cost_quality"] if cost is not None else "unknown",
        "realized_profit_total_cny": canonical_decimal(position["realized_profit"]),
        "realized_profit_before_fee_cny": canonical_decimal(
            position["realized_before_fee"]
        ),
        "fee_complete": bool(position["fee_complete"]),
        "position_status": "held" if shares > 0 else "closed",
        "baseline_event_id": position["baseline_event_id"],
        "source_event_ids": list(position["source_event_ids"]),
    }


def fold_ledger_events(
    events: Iterable[Mapping[str, Any]],
    *,
    position_as_of: datetime | date | str,
    known_at: datetime | date | str | None = None,
) -> dict[str, Any]:
    """Fold events using effective time and system-knowledge time independently."""

    raw_events = list(events)
    position_cutoff = _coerce_datetime(position_as_of, end_of_day=True)
    knowledge_cutoff = known_at if known_at is not None else position_as_of
    known = _known_events(raw_events, knowledge_cutoff)
    active, superseded = _active_events(known, position_cutoff=position_cutoff)

    unsettled = _known_unsettled_transactions(
        known,
        position_cutoff=position_cutoff,
        superseded=superseded,
    )
    pending = [event for event in unsettled if event.get("status") == "pending"]
    positions: dict[str, dict[str, Any]] = {}
    cash_balance: Decimal | None = None
    cash_quality = "unknown"
    conflicts: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for event in active:
        event_id = str(event.get("event_id") or "")
        event_type = str(event.get("event_type") or "")
        if event.get("status") != _CONFIRMED_STATUS:
            continue
        if event_type == "reversal":
            continue
        if event_type == "cash_baseline":
            balance = _non_negative(
                event.get("absolute_cash_balance"), field="absolute_cash_balance"
            )
            if balance is not None:
                cash_balance = balance
                cash_quality = str(event.get("cash_quality") or "unknown")
            continue
        if event_type == "cash_adjustment":
            delta = _decimal(event.get("cash_delta"), field="cash_delta")
            if cash_balance is None or delta is None:
                unresolved.append(event_id)
                cash_balance = None
                cash_quality = "unknown"
            else:
                cash_balance += delta
                cash_quality = _combine_quality(
                    cash_quality, str(event.get("cash_quality") or "unknown")
                )
            continue

        code_value = event.get("fund_code")
        if not code_value:
            unresolved.append(event_id)
            continue
        code = _fund_code(code_value)
        position = positions.setdefault(code, _new_position(code))

        if event_type in _BASELINE_TYPES:
            shares = _non_negative(event.get("absolute_shares"), field="absolute_shares")
            if shares is None:
                unresolved.append(event_id)
                continue
            cost = _non_negative(
                event.get("absolute_cost_basis"), field="absolute_cost_basis"
            )
            # A later reconciliation is an absolute opening state. Reset all
            # earlier transactions for this fund to prevent double counting.
            position.update(
                {
                    "shares": shares,
                    "cost_basis": cost,
                    "shares_quality": str(event.get("shares_quality") or "unknown"),
                    "cost_quality": (
                        str(event.get("cost_quality") or "unknown")
                        if cost is not None
                        else "unknown"
                    ),
                    "realized_profit": Decimal("0"),
                    "realized_before_fee": Decimal("0"),
                    "fee_complete": True,
                    "source_event_ids": [event_id],
                    "baseline_event_id": event_id,
                }
            )
            continue

        applied = False
        conflict_code: str | None = None
        if event_type == "buy":
            applied = _apply_buy(position, event)
            if not applied:
                conflict_code = "missing_or_invalid_shares"
        elif event_type == "sell":
            applied, conflict_code = _apply_sell(position, event)
        else:
            unresolved.append(event_id)
            continue

        if not applied:
            conflicts.append(
                {
                    "event_id": event_id,
                    "fund_code": code,
                    "code": conflict_code or "unresolved_transaction",
                }
            )
            continue
        position["source_event_ids"].append(event_id)

        if cash_balance is not None:
            cash_delta = _decimal(event.get("net_cash_delta"), field="net_cash_delta")
            if cash_delta is None:
                cash_balance = None
                cash_quality = "unknown"
            else:
                cash_balance += cash_delta

    rows = [_position_row(positions[code]) for code in sorted(positions)]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "position_as_of": _iso_timestamp(position_as_of, end_of_day=True),
        "known_at": _iso_timestamp(knowledge_cutoff, end_of_day=True),
        "positions": rows,
        "cash": {
            "balance_cny": canonical_decimal(cash_balance),
            "quality": cash_quality if cash_balance is not None else "unknown",
            "known": cash_balance is not None,
        },
        "pending_transaction_count": len(pending),
        "pending_event_ids": [str(event.get("event_id")) for event in pending],
        "known_unsettled_transaction_count": len(unsettled),
        "known_unsettled_event_ids": [
            str(event.get("event_id")) for event in unsettled
        ],
        "known_unsettled_buy_count": sum(
            1 for event in unsettled if event.get("event_type") == "buy"
        ),
        "known_unsettled_sell_count": sum(
            1 for event in unsettled if event.get("event_type") == "sell"
        ),
        "unresolved_event_ids": sorted(set(unresolved)),
        "conflicts": conflicts,
        "superseded_event_ids": sorted(superseded),
        "events_known_count": len(known),
    }


def position_fingerprint(
    state: Mapping[str, Any],
    *,
    ledger_version: str | None = None,
) -> str:
    active_positions = [
        {
            "fund_code": row.get("fund_code"),
            "settled_shares": row.get("settled_shares"),
            "cost_basis_total_cny": row.get("cost_basis_total_cny"),
            "shares_quality": row.get("shares_quality"),
            "cost_quality": row.get("cost_quality"),
        }
        for row in state.get("positions") or []
        if (_decimal(row.get("settled_shares")) or Decimal("0")) > 0
    ]
    payload = {
        "ledger_version": ledger_version,
        "positions": sorted(active_positions, key=lambda row: str(row["fund_code"])),
        "cash": state.get("cash")
        or {"balance_cny": None, "quality": "unknown", "known": False},
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _valuation_payload(valuations: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_code, raw in (valuations or {}).items():
        code = _fund_code(raw_code)
        normalized[code] = {
            "nav": canonical_decimal(raw.get("nav")),
            "nav_date": str(raw.get("nav_date") or "")[:10] or None,
            "source": raw.get("source"),
            "available_at": raw.get("available_at"),
            "is_estimate": bool(raw.get("is_estimate", False)),
        }
    return normalized


def build_position_snapshot_payload(
    events: Iterable[Mapping[str, Any]],
    *,
    user_id: int | str,
    account_id: str = "default",
    position_as_of: datetime | date | str,
    captured_at: datetime | date | str,
    valuations: Mapping[str, Mapping[str, Any]] | None = None,
    cash_balance: object | None = None,
    cash_quality: str = "unknown",
    source: str = "portfolio_ledger",
) -> dict[str, Any]:
    """Freeze a decision-ready position payload without mixing NAV into position truth."""

    event_rows = list(events)
    state = fold_ledger_events(
        event_rows,
        position_as_of=position_as_of,
        known_at=captured_at,
    )
    ledger_version = build_ledger_version(event_rows, known_at=captured_at)

    if cash_balance is not None:
        override = _non_negative(cash_balance, field="cash_balance")
        _validate_quality(cash_quality)
        state["cash"] = {
            "balance_cny": canonical_decimal(override),
            "quality": cash_quality,
            "known": True,
        }

    fingerprint = position_fingerprint(state, ledger_version=ledger_version)
    normalized_valuations = _valuation_payload(valuations)
    valuation_hash = hashlib.sha256(
        _canonical_json(normalized_valuations).encode("utf-8")
    ).hexdigest()
    valuation_version = f"val1:{valuation_hash[:16]}"

    position_rows: list[dict[str, Any]] = []
    invested_market_value = Decimal("0")
    valuation_complete = True
    active_rows = 0
    for raw_row in state["positions"]:
        row = dict(raw_row)
        shares = _decimal(row.get("settled_shares")) or Decimal("0")
        valuation = normalized_valuations.get(str(row.get("fund_code")))
        nav = _decimal((valuation or {}).get("nav"), field="nav")
        market_value = shares * nav if nav is not None else None
        if shares > 0:
            active_rows += 1
            if market_value is None:
                valuation_complete = False
            else:
                invested_market_value += market_value
        row.update(
            {
                "nav": canonical_decimal(nav),
                "nav_date": (valuation or {}).get("nav_date"),
                "valuation_source": (valuation or {}).get("source"),
                "market_value_cny": canonical_decimal(market_value),
                "valuation_is_estimate": (valuation or {}).get("is_estimate"),
            }
        )
        position_rows.append(row)

    cash = dict(state["cash"])
    cash_value = _decimal(cash.get("balance_cny"), field="cash_balance")
    total_assets = (
        invested_market_value + cash_value
        if valuation_complete and cash_value is not None
        else None
    )
    active_positions = [
        row
        for row in position_rows
        if (_decimal(row.get("settled_shares")) or Decimal("0")) > 0
    ]
    share_qualities = {str(row.get("shares_quality")) for row in active_positions}
    shares_complete = bool(active_positions) and "unknown" not in share_qualities
    truth_status = (
        "user_confirmed"
        if shares_complete
        and share_qualities.issubset({"user_confirmed", "platform_confirmed"})
        and not state["conflicts"]
        else ("estimated" if shares_complete else "unknown")
    )
    costs_complete = all(
        row.get("cost_basis_total_cny") is not None and bool(row.get("fee_complete"))
        for row in active_positions
    )
    fees_complete = all(bool(row.get("fee_complete")) for row in position_rows)
    settled_position_complete = shares_complete and not state["conflicts"]
    decision_position_complete = (
        settled_position_complete
        and int(state.get("known_unsettled_transaction_count") or 0) == 0
    )
    completeness = {
        "position_complete": decision_position_complete,
        "settled_position_complete": settled_position_complete,
        "decision_position_complete": decision_position_complete,
        "position_truth_status": truth_status,
        "cost_complete": costs_complete,
        "fee_complete": fees_complete,
        "cash_complete": bool(cash.get("known")),
        "valuation_complete": valuation_complete,
        "pending_transaction_count": state["pending_transaction_count"],
        "known_unsettled_transaction_count": state.get(
            "known_unsettled_transaction_count", 0
        ),
        "conflict_count": len(state["conflicts"]),
    }

    captured = _iso_timestamp(captured_at)
    snapshot_seed = {
        "user_id": str(user_id),
        "account_id": account_id,
        "captured_at": captured,
        "position_as_of": _iso_timestamp(position_as_of, end_of_day=True),
        "ledger_version": ledger_version,
        "position_fingerprint": fingerprint,
        "valuation_version": valuation_version,
    }
    snapshot_id = hashlib.sha256(
        _canonical_json(snapshot_seed).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "user_id": str(user_id),
        "account_id": account_id,
        "source": source,
        "captured_at": captured,
        "position_as_of": state["position_as_of"],
        "known_at": state["known_at"],
        "ledger_version": ledger_version,
        "position_fingerprint": fingerprint,
        # Compatibility name used by the current decision preflight contract.
        "holdings_fingerprint": fingerprint,
        "holdings_fingerprint_basis": "ledger_version+fund_code+shares+cost+cash",
        "valuation_version": valuation_version,
        "positions": position_rows,
        "cash": cash,
        "totals": {
            "invested_market_value_cny": (
                canonical_decimal(invested_market_value) if valuation_complete else None
            ),
            "total_assets_cny": canonical_decimal(total_assets),
            "active_position_count": active_rows,
        },
        "completeness": completeness,
        "position_complete": completeness["position_complete"],
        "pending_transaction_count": state["pending_transaction_count"],
        "known_unsettled_transaction_count": state.get(
            "known_unsettled_transaction_count", 0
        ),
        "known_unsettled_event_ids": state.get("known_unsettled_event_ids", []),
        "conflicts": state["conflicts"],
        "unresolved_event_ids": state["unresolved_event_ids"],
    }


__all__ = [
    "GENESIS_HASH",
    "LEDGER_SCHEMA_VERSION",
    "LedgerIntegrityError",
    "build_ledger_version",
    "build_position_snapshot_payload",
    "canonical_decimal",
    "chain_ledger_events",
    "create_cash_adjustment",
    "create_cash_baseline",
    "create_legacy_estimated_baseline",
    "create_transaction_event",
    "create_user_confirmed_baseline",
    "fold_ledger_events",
    "position_fingerprint",
]
