from __future__ import annotations

"""方向状态的跨日持久化与滞回（`sector_entry_maturity.2026-08.v3`）。

## 为什么需要它

在此之前方向状态只有"今天"这一个观测：`entry_state` 全仓库没有任何持久化，于是

* 同一个板块今天 `ready_to_start`、明天掉回 `forming`、后天又上来，用户看到的
  「今日可布局方向」天天换人，而这种抖动大多来自阈值边界上的一两分之差；
* `invalidation_signals` 只是一段文案，没有任何机制真的在逐日跟踪它们；
* 无法区分「已连续 3 天满足」和「今天刚满足第 1 天」——对"近期机会比较大的方向"这个
  产品命题来说，这恰恰是最该说清楚的一件事。

## 滞回规则

* **进入** `ready_to_start` 在当日通过入场线后即可生成本次参考金额。退出仍使用更低趋势线，
  避免在阈值边界反复进出；高弹性策略不再人为延迟一个交易日。
* **退出**用一条更低的趋势线（``EXIT_TREND_THRESHOLD`` < 入场线），避免在阈值边界上
  反复进出。已经处于 ready 的方向只有跌破退出线才降级。
* 连续天数按**交易日相邻**判断：昨天没有记录（新板块、系统停机、非交易日）时从 1 重新起算，
  不假装中间那些天也达标了。

滞回只改变 `entry_state` 的时序行为，不改变任何分数，也不放宽入场线。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_INVALID,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    V3_GATE_THRESHOLDS,
)

SECTOR_DIRECTION_STATE_SCHEMA_VERSION = "sector_direction_state.v1"

#: 进入「可以开始布局」所需的连续达标交易日数。高弹性机会按当日确认执行。
READY_CONFIRMATION_DAYS = 1
#: 退出用的趋势线，低于入场线，形成滞回带。
EXIT_TREND_THRESHOLD = V3_GATE_THRESHOLDS["trend"] - 8.0


@dataclass(frozen=True)
class DirectionStateRecord:
    trade_date: str
    sector_label: str
    entry_state: str
    raw_entry_state: str
    qualifies_for_ready: bool
    consecutive_qualifying_days: int


def apply_direction_state_hysteresis(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: str | None,
    previous_trade_date: str | None = None,
    previous_states: Mapping[str, DirectionStateRecord] | None = None,
    ready_confirmation_days: int = READY_CONFIRMATION_DAYS,
    exit_trend_threshold: float = EXIT_TREND_THRESHOLD,
) -> list[dict[str, Any]]:
    """纯函数：给已打分的 v3 行套上滞回，返回新的行列表。

    ``previous_states`` 为 ``None`` 时不做任何滞回（首次运行、或存储不可用时的降级路径），
    此时 `consecutive_qualifying_days` 记为 1，且**不会**因此拒绝当天达标的方向——
    滞回是降低抖动的机制，不该在没有历史时变成一道额外的门。
    """
    history = previous_states or {}
    result: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        if str(item.get("score_policy_version") or "") != ENTRY_POLICY_VERSION_V3:
            result.append(item)
            continue

        raw_state = str(item.get("entry_state") or ENTRY_FORMING)
        raw_flow_probe = item.get("flow_improving_probe_eligible") is True
        raw_probability_probe = (
            item.get("probability_early_probe_eligible") is True
        )
        qualifies = raw_state == ENTRY_READY_TO_START
        label = str(item.get("sector_label") or "")
        previous = history.get(label)
        prior_days = (
            previous.consecutive_qualifying_days
            if previous is not None and previous.qualifies_for_ready
            else 0
        )
        consecutive = prior_days + 1 if qualifies else 0

        entry_state = raw_state
        if qualifies and previous_states is not None:
            if consecutive < ready_confirmation_days:
                was_ready = previous is not None and previous.entry_state == ENTRY_READY_TO_START
                if not was_ready:
                    # 首次达标当天只观察，不直接给买入动作。
                    entry_state = ENTRY_FORMING
        elif (
            not qualifies
            and previous is not None
            and previous.entry_state == ENTRY_READY_TO_START
            and raw_state not in {ENTRY_INVALID}
        ):
            trend = _num(item.get("trend_strength_score"))
            if trend is not None and trend >= exit_trend_threshold:
                # 滞回带内：趋势尚未跌破退出线，保持已确认的可布局状态而不是当天翻脸。
                entry_state = ENTRY_READY_TO_START
                consecutive = prior_days + 1

        item["entry_state"] = entry_state
        item["raw_entry_state"] = raw_state
        item["qualifies_for_ready"] = qualifies
        item["consecutive_qualifying_days"] = consecutive if qualifies else 0
        item["state_trade_date"] = trade_date
        item["state_previous_trade_date"] = previous_trade_date
        item["ready_confirmation_days"] = ready_confirmation_days
        flow_probe_active = bool(
            raw_flow_probe and entry_state == ENTRY_READY_ON_PULLBACK
        )
        probability_probe_active = bool(
            raw_probability_probe
            and entry_state in {ENTRY_FORMING, ENTRY_READY_ON_PULLBACK}
        )
        item["flow_improving_probe_active"] = flow_probe_active
        item["probability_early_probe_active"] = probability_probe_active
        item["execution_eligible"] = (
            entry_state == ENTRY_READY_TO_START
            or flow_probe_active
            or probability_probe_active
        )
        item["automatic_promotion_allowed"] = (
            entry_state == ENTRY_READY_TO_START
            or flow_probe_active
            or probability_probe_active
        )
        if entry_state == ENTRY_READY_TO_START:
            item["waiting_reason_code"] = None
        elif flow_probe_active:
            item["waiting_reason_code"] = "fund_entry_confirmation"
        elif probability_probe_active:
            item["waiting_reason_code"] = "probability_fund_confirmation"
        if entry_state == ENTRY_FORMING and qualifies:
            item["entry_reason"] = (
                f"今日首次通过入场线（已满足 {consecutive} 天）；"
                f"连续 {ready_confirmation_days} 天达标后才进入可布局，避免边界抖动。"
            )
            item["entry_hint"] = "今日首次达标，先观察一日"
            item["entry_triggers"] = [
                f"下一交易日继续通过入场线（当前已满足 {consecutive}/{ready_confirmation_days} 天）"
            ]
            if str(item.get("confidence") or "") == "高":
                item["confidence"] = "中"
        elif entry_state == ENTRY_READY_TO_START and not qualifies:
            item["entry_reason"] = (
                "趋势强度仍在退出线之上，维持可布局状态；跌破退出线才会降级。"
            )
            item["entry_hint"] = "已确认方向仍在滞回带内，本次投入保持小额"
            item["entry_triggers"] = [
                f"趋势强度保持在退出线 {exit_trend_threshold:g} 以上",
                "买入并录入持仓后，由日报根据资金参与度与价格位置决定是否加仓",
            ]
        result.append(item)
    return result


# --------------------------------------------------------------------------
# 存储（追加式，每交易日每板块一行）
# --------------------------------------------------------------------------


def load_previous_direction_states(
    previous_trade_date: str | None,
) -> dict[str, DirectionStateRecord] | None:
    """读取上一交易日的方向状态；不可用时返回 None（调用方据此跳过滞回）。"""
    if not previous_trade_date:
        return None
    try:
        from app.database import _connect

        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT sector_label, entry_state, raw_entry_state,
                       qualifies_for_ready, consecutive_qualifying_days
                FROM sector_direction_states
                WHERE trade_date = ?
                """,
                (previous_trade_date,),
            ).fetchall()
            if not rows:
                any_history = connection.execute(
                    """
                    SELECT 1 FROM sector_direction_states
                    WHERE trade_date <= ?
                    LIMIT 1
                    """,
                    (previous_trade_date,),
                ).fetchone()
                if any_history is None:
                    return None
    except Exception:  # noqa: BLE001 - 滞回是增强项，读不到只是退回"无历史"，不能拖垮扫描
        return None
    return {
        str(row["sector_label"]): DirectionStateRecord(
            trade_date=previous_trade_date,
            sector_label=str(row["sector_label"]),
            entry_state=str(row["entry_state"]),
            raw_entry_state=str(row["raw_entry_state"]),
            qualifies_for_ready=bool(row["qualifies_for_ready"]),
            consecutive_qualifying_days=int(row["consecutive_qualifying_days"] or 0),
        )
        for row in rows
        if row and row["sector_label"]
    }


def record_direction_states(
    rows: Iterable[Mapping[str, Any]],
    *,
    trade_date: str | None,
) -> int:
    """把当天的方向状态落库（同一交易日同一板块幂等覆盖）。返回写入行数。"""
    if not trade_date:
        return 0
    payload = [
        (
            trade_date,
            str(row.get("sector_label") or ""),
            SECTOR_DIRECTION_STATE_SCHEMA_VERSION,
            ENTRY_POLICY_VERSION_V3,
            str(row.get("entry_state") or ENTRY_FORMING),
            str(row.get("raw_entry_state") or row.get("entry_state") or ENTRY_FORMING),
            1 if row.get("qualifies_for_ready") else 0,
            int(row.get("consecutive_qualifying_days") or 0),
            _num(row.get("trend_strength_score")),
            _num(row.get("participation_score")),
            _num(row.get("position_risk_score")),
            _num(row.get("direction_score")),
            datetime.now(timezone.utc).isoformat(),
        )
        for row in rows
        if str(row.get("score_policy_version") or "") == ENTRY_POLICY_VERSION_V3
        and str(row.get("sector_label") or "").strip()
    ]
    if not payload:
        return 0
    try:
        from app.database import _connect

        with _connect() as connection:
            if getattr(connection, "dialect", "sqlite") == "mysql":
                statement = """
                INSERT INTO sector_direction_states (
                    trade_date, sector_label, schema_version, policy_version,
                    entry_state, raw_entry_state, qualifies_for_ready,
                    consecutive_qualifying_days, trend_strength_score,
                    participation_score, position_risk_score, direction_score,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    schema_version = VALUES(schema_version),
                    policy_version = VALUES(policy_version),
                    entry_state = VALUES(entry_state),
                    raw_entry_state = VALUES(raw_entry_state),
                    qualifies_for_ready = VALUES(qualifies_for_ready),
                    consecutive_qualifying_days = VALUES(consecutive_qualifying_days),
                    trend_strength_score = VALUES(trend_strength_score),
                    participation_score = VALUES(participation_score),
                    position_risk_score = VALUES(position_risk_score),
                    direction_score = VALUES(direction_score),
                    recorded_at = VALUES(recorded_at)
                """
            else:
                statement = """
                INSERT INTO sector_direction_states (
                    trade_date, sector_label, schema_version, policy_version,
                    entry_state, raw_entry_state, qualifies_for_ready,
                    consecutive_qualifying_days, trend_strength_score,
                    participation_score, position_risk_score, direction_score,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, sector_label) DO UPDATE SET
                    entry_state = excluded.entry_state,
                    raw_entry_state = excluded.raw_entry_state,
                    qualifies_for_ready = excluded.qualifies_for_ready,
                    consecutive_qualifying_days = excluded.consecutive_qualifying_days,
                    trend_strength_score = excluded.trend_strength_score,
                    participation_score = excluded.participation_score,
                    position_risk_score = excluded.position_risk_score,
                    direction_score = excluded.direction_score,
                    recorded_at = excluded.recorded_at
                """
            cursor = connection.executemany(statement, payload)
            close_cursor = getattr(cursor, "close", None)
            if callable(close_cursor):
                close_cursor()
            connection.commit()
    except Exception:  # noqa: BLE001 - 写入失败只丢失滞回能力，不影响本次结论
        return 0
    return len(payload)


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "EXIT_TREND_THRESHOLD",
    "READY_CONFIRMATION_DAYS",
    "SECTOR_DIRECTION_STATE_SCHEMA_VERSION",
    "DirectionStateRecord",
    "apply_direction_state_hysteresis",
    "load_previous_direction_states",
    "record_direction_states",
]
