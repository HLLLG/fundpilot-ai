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

from app.services.sector_labels import sector_family_relation, sector_family_root
from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_INVALID,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    EXIT_TREND_THRESHOLD,
    V3_GATE_THRESHOLDS,
)

SECTOR_DIRECTION_STATE_SCHEMA_VERSION = "sector_direction_state.v1"

#: 进入「可以开始布局」所需的连续达标交易日数。高弹性机会按当日确认执行。
READY_CONFIRMATION_DAYS = 1
#: `EXIT_TREND_THRESHOLD`（退出用的趋势线，低于入场线，形成滞回带）现由
#: `sector_opportunity_scoring` 定义——入场等待条件的措辞与成形信号分的档位标签也要用它，
#: 而那个模块不能 import 本模块（本模块 import 它）。此处按名字 re-export，既有导入方不变。


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
        if qualifies and previous_states is not None and ready_confirmation_days > 1:
            # `READY_CONFIRMATION_DAYS = 1` 下这一支恒不成立（consecutive 至少是 1），
            # 因此生产里不会延迟确认——高弹性机会按当日确认是刻意的。这里显式加上
            # `> 1` 的条件，让"这段只在把确认天数配成 ≥2 时才生效"写在代码里，而不是
            # 留一段读起来像有保护、实际永不执行的分支。
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
        # 滞回**保留**（而不是重新确认）ready_to_start 时，授权本轮投入的仍然是当日原始
        # 档位上开的那条提前试仓通道——`first_tranche_scale` 也是它算出来的，本函数不重算
        # 分数与比例。按 `entry_state` 判会把通道判成失活，却继续沿用它的比例，状态与比例
        # 变成两套口径（2026-08-13 线上：数字经济 flow_improving_probe_eligible=true、
        # _active=false，而 first_tranche_scale=0.4 正来自该通道）。
        hysteresis_held = entry_state == ENTRY_READY_TO_START and not qualifies
        probe_state = raw_state if hysteresis_held else entry_state
        flow_probe_active = bool(
            raw_flow_probe and probe_state == ENTRY_READY_ON_PULLBACK
        )
        probability_probe_active = bool(
            raw_probability_probe
            and probe_state in {ENTRY_FORMING, ENTRY_READY_ON_PULLBACK}
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
        # 与上面的延迟确认成对：只有把 ready_confirmation_days 配成 ≥2 时才会走到这里。
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
            # 提示语必须跟着 `first_tranche_scale` 走。此前一律写「本次投入保持小额」，
            # 而没有任何通道授权投入时该字段是 `None`（卡片同时显示「本轮不投入」）——
            # 同一行里一句说"保持小额"、一句说"不投入"，用户无从判断哪句为准。
            tranche_authorized = (
                _num(item.get("first_tranche_scale")) or 0.0
            ) > 0.0
            item["entry_hint"] = (
                "已确认方向仍在滞回带内，本次投入保持小额"
                if tranche_authorized
                else "已确认方向仍在滞回带内，仅持有观察，本轮不新增投入"
            )
            item["entry_triggers"] = [
                f"趋势强度保持在退出线 {exit_trend_threshold:g} 以上",
                "买入并录入持仓后，由日报根据资金参与度与价格位置决定是否加仓",
            ]
        result.append(item)
    return result


def annotate_family_direction_divergence(
    rows: Sequence[dict[str, Any]],
) -> Sequence[dict[str, Any]]:
    """同族板块（细分↔父行业，如 CXO↔医疗）当日入场状态相互矛盾时，互相标注对方状态。

    两个同族键的行情代理不同（CXO=BK1600、医疗=399989/BK0727），方向状态分开计算，
    在打分边界上完全可以同日向相反方向翻转——2026-08 线上实测：「医疗」判 invalid
    触发持仓大幅减仓的同日，「CXO」判 ready_to_start 给出分批买入，两张卡片没有任何
    一句话解释这不是自相矛盾。

    只标注**会产生相反动作**的组合：一侧可执行（ready 或试仓通道激活）、另一侧
    invalid。同为 forming/pullback 的正常差异不算矛盾，不标注。只加注解
    （``family_direction_divergence``），不改任何状态或分数——披露不是仲裁。

    必须在滞回**之后**、选择**之前**调用：invalid 的那一侧通常选不进方向名额，
    选完再看整个横截面就找不到它了。
    """
    by_root: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("sector_label") or "").strip()
        if not label:
            continue
        by_root.setdefault(sector_family_root(label), []).append(row)
    for group in by_root.values():
        if len(group) < 2:
            continue
        for row in group:
            label = str(row.get("sector_label") or "").strip()
            state = str(row.get("entry_state") or "")
            executable = (
                bool(row.get("execution_eligible")) or state == ENTRY_READY_TO_START
            )
            conflicts: list[dict[str, Any]] = []
            for other in group:
                if other is row:
                    continue
                other_label = str(other.get("sector_label") or "").strip()
                other_state = str(other.get("entry_state") or "")
                other_executable = (
                    bool(other.get("execution_eligible"))
                    or other_state == ENTRY_READY_TO_START
                )
                if (executable and other_state == ENTRY_INVALID) or (
                    state == ENTRY_INVALID and other_executable
                ):
                    conflicts.append(
                        {
                            "sector_label": other_label,
                            "entry_state": other_state,
                            "relation": sector_family_relation(label, other_label),
                        }
                    )
            if conflicts:
                row["family_direction_divergence"] = conflicts
    return rows


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
                  AND (source IS NULL OR source = 'captured')
                """,
                (previous_trade_date,),
            ).fetchall()
            if not rows:
                any_history = connection.execute(
                    """
                    SELECT 1 FROM sector_direction_states
                    WHERE trade_date <= ?
                      AND (source IS NULL OR source = 'captured')
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
            # 趋势证据覆盖度：证据不足时 v3 把趋势分兜底成 ≤45 的占位值（必然低于退出线
            # 52）并把该覆盖度置 0。不存它，退出侧就分不出「真跌破」与「当天没数据」，
            # 连续跌破天数会被无证据的日子灌水。
            _num((row.get("component_coverage") or {}).get("trend")),
            # 发现基金链路与每日捕获都是「当天真实算出来的」。回填走
            # `sector_direction_capture` 里独立的写入器并标 'backfilled'。
            "captured",
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
                    recorded_at, trend_evidence_coverage, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    recorded_at = VALUES(recorded_at),
                    trend_evidence_coverage = VALUES(trend_evidence_coverage),
                    source = VALUES(source)
                """
            else:
                statement = """
                INSERT INTO sector_direction_states (
                    trade_date, sector_label, schema_version, policy_version,
                    entry_state, raw_entry_state, qualifies_for_ready,
                    consecutive_qualifying_days, trend_strength_score,
                    participation_score, position_risk_score, direction_score,
                    recorded_at, trend_evidence_coverage, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, sector_label) DO UPDATE SET
                    entry_state = excluded.entry_state,
                    raw_entry_state = excluded.raw_entry_state,
                    qualifies_for_ready = excluded.qualifies_for_ready,
                    consecutive_qualifying_days = excluded.consecutive_qualifying_days,
                    trend_strength_score = excluded.trend_strength_score,
                    participation_score = excluded.participation_score,
                    position_risk_score = excluded.position_risk_score,
                    direction_score = excluded.direction_score,
                    recorded_at = excluded.recorded_at,
                    trend_evidence_coverage = excluded.trend_evidence_coverage,
                    source = excluded.source
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
    "annotate_family_direction_divergence",
    "apply_direction_state_hysteresis",
    "load_previous_direction_states",
    "record_direction_states",
]
