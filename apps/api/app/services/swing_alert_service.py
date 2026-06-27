from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import _connect, _uid
from app.models import (
    InvestorProfile,
    SwingAlertEvaluateRequest,
    SwingAlertEvaluateResponse,
    SwingAlertItem,
)
from app.services.discovery_sector_heat import build_sector_heat_ranking
from app.services.swing_alert_engine import evaluate_swing_alerts, should_evaluate_swing_alerts


def evaluate_and_record_swing_alerts(
    request: SwingAlertEvaluateRequest,
) -> SwingAlertEvaluateResponse:
    profile = request.profile
    enabled = should_evaluate_swing_alerts(profile)
    scope = request.monitor_scope or profile.swing_monitor_scope or "both"

    sector_heat: list[dict] | None = None
    if enabled and scope in {"full_market", "both"}:
        sector_heat = build_sector_heat_ranking(include_5d=False)

    items, trade_date, session_kind = evaluate_swing_alerts(
        request.holdings,
        profile,
        monitor_scope=scope,
        sector_heat=sector_heat,
    )

    if not enabled:
        return SwingAlertEvaluateResponse(
            trade_date=trade_date,
            session_kind=session_kind,
            alerts_enabled=False,
            items=[],
            new_count=0,
        )

    fired_keys = list_fired_alert_keys(trade_date)
    new_count = 0
    stamped: list[SwingAlertItem] = []
    for item in items:
        is_new = item.alert_key not in fired_keys
        if is_new:
            new_count += 1
            record_fired_alert(trade_date, item)
            fired_keys.add(item.alert_key)
        stamped.append(item.model_copy(update={"is_new": is_new}))

    return SwingAlertEvaluateResponse(
        trade_date=trade_date,
        session_kind=session_kind,
        alerts_enabled=True,
        items=stamped,
        new_count=new_count,
    )


def list_today_swing_alerts(trade_date: str | None = None) -> list[SwingAlertItem]:
    from app.services.trading_session import build_trading_session

    resolved_date = trade_date or str(build_trading_session().get("effective_trade_date") or "")
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM swing_alert_fired
            WHERE userId = ? AND trade_date = ?
            ORDER BY fired_at DESC
            """,
            (user_id, resolved_date),
        ).fetchall()
    items: list[SwingAlertItem] = []
    for row in rows:
        try:
            items.append(SwingAlertItem.model_validate(json.loads(row["payload"])))
        except (json.JSONDecodeError, ValueError):
            continue
    return items


def list_fired_alert_keys(trade_date: str) -> set[str]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT alert_key FROM swing_alert_fired
            WHERE userId = ? AND trade_date = ?
            """,
            (user_id, trade_date),
        ).fetchall()
    return {str(row["alert_key"]) for row in rows}


def record_fired_alert(trade_date: str, item: SwingAlertItem) -> None:
    user_id = _uid()
    now = datetime.now(timezone.utc).isoformat()
    payload = item.model_copy(update={"is_new": False}).model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO swing_alert_fired (
                userId, trade_date, alert_key, payload, fired_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, trade_date, item.alert_key, json.dumps(payload, ensure_ascii=False), now),
        )
        connection.commit()
