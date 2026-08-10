"""每交易日把**全白名单**的方向状态落库，供退出侧读连续跌破天数。

## 为什么需要它

`sector_direction_states` 目前只在用户手动跑一次发现基金时才写。本地实测：整张表只有
2026-08-07 一天 78 行，而当天是 08-10 —— 也就是说退出侧的「连续跌破 N 个交易日才升级为
大幅减仓」在真实使用中攒不出数据，`consecutive_days_below_exit_line` 长期卡在 1，−50%
那一档实际不可达。这个脚本把捕获从「用户碰巧跑了扫描」改成「每个交易日必然发生」。

## 与发现基金链路的关系：共享打分，**刻意**不共享标的集合

打分、跨日滞回与落库整段复用 `discovery_pipeline._score_select_and_persist_directions`。
这一点不能妥协：如果这里另写一份打分，同一个板块同一天会有两个 `trend_strength_score`，
而退出侧要把「今天实算的分」与「历史落库的分」放在一条序列上比较，两套口径会让连续天数
彻底失去意义。用私有符号是有意的——它是方向状态的单一写入者。

**前台集合刻意不同。** 发现基金只对约 24 个预筛板块取联网证据（`_opportunity_flow_labels`
按热度与用户持仓/关注筛出来的），而打分对全部 78 个白名单板块执行且 `drop_unavailable=False`。
不在预筛集合里的板块拿不到 mainline，于是走 v3 的证据不足兜底：趋势分被写成
`35 + 5日涨跌×1.5` 并 clamp 到 **≤45**，而退出线是 52 —— 每个占位值都长得像「已跌破退出
线」（实测 08-07 落库里国防军工/电网设备恰为 45.0、黄金恰为 35.0，而真实实算值是
36.15 / 48.08 / 90.52）。

对定时捕获来说这是致命的：这张表**没有 userId**，一次捕获要服务所有用户，而「谁持有哪个
板块」是逐用户的。所以这里对**全白名单**取联网证据，任何用户的任何持仓方向都不会落到
占位值上。代价是联网量约为发现基金的 3 倍，而定时任务本来就不受请求超时约束——各 builder
的预算在这里统一放宽（见 `_FULL_UNIVERSE_BUDGETS`）。

同样的「共享 builder、各自决定前台集合」结构在日报侧已有先例：
`report_sector_opportunity._build_holding_mainline` 的 docstring 明确写着"分工照抄
discovery_pipeline，只是前台集合换成用户持有的 3～5 个板块"。

## 默认跳过量价背离回测

`build_sector_divergence_map_for_opportunities` 的结果只流向打分行的 `confidence` 键
（`sector_opportunity_scoring.py` 里 `divergence_backtest` 的唯一去处是
`_confidence(flow, date_aligned, penalties, divergence_backtest)`），而 `confidence`：

* **不在落库的 9 列里**（entry_state / raw_entry_state / qualifies_for_ready /
  consecutive_qualifying_days / trend_strength_score / participation_score /
  position_risk_score / direction_score / trend_evidence_coverage）；
* **不是** `classify_entry_state_v3` 或 `_entry_maturity_v3` 的入参，
  `opportunity_available` 也由 `entry_state` 推导而不是由它推导。

也就是说它对这张表的每一列都没有影响，却是本捕获里最贵的一段：本地实测全白名单 78 个
板块跑满 90 s 预算仍整段超时，而同一次捕获仍拿到 75/78 行趋势证据。默认关掉之后总耗时从
**103.5 s 降到约 13.5 s**。需要它时用 `include_divergence=True` 打开。

## 不做的事

* **不写发现基金报告、不调 LLM。** 这里只产状态，成本是纯取数。
* **不回填历史。** 见 `docs/PROJECT_CONTEXT.md`：回填要么伪造 participation/entry_state
  去污染发现基金自己的滞回账本，要么得另开一张表与第二条读路径。逐日捕获没有这些问题。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

SECTOR_DIRECTION_CAPTURE_POLICY_VERSION = "sector_direction_capture.2026-08.v1"

#: 全白名单（约 78 个板块）取证的预算。定时任务不受请求超时约束，因此比发现基金链路里
#: 那套面向请求的预算宽得多——宁可慢，也不要因为超时退化成占位值，那等于没捕获。
_FULL_UNIVERSE_BUDGETS: dict[str, float] = {
    "flow": 90.0,
    "divergence": 90.0,
    "position": 300.0,
    "percentile": 60.0,
}


def capture_sector_direction_states(
    *,
    trade_date: str | None = None,
    decision_at: Any = None,
    progress: Callable[[str], None] | None = None,
    budgets: dict[str, float] | None = None,
    include_divergence: bool = False,
) -> dict[str, Any]:
    """对全白名单算一遍方向状态并落库。返回可机读的捕获摘要。

    返回里的 `with_trend_evidence` / `degraded` 是这次捕获**到底有没有用**的判据：
    落库行数为 78 但趋势证据为 0 的话，表面成功、实际全是占位值，退出侧一行都用不上。
    """
    from app.services.discovery_pipeline import _score_select_and_persist_directions
    from app.services.discovery_sector_heat import build_sector_heat_ranking
    from app.services.discovery_sector_position import (
        build_sector_percentile_universe_positions,
        build_sector_position_map_for_opportunities,
    )
    from app.services.mainline_regime import (
        build_mainline_regime_snapshot,
        mainline_regime_by_label,
    )
    from app.services.sector_opportunity_scoring import (
        build_sector_divergence_map_for_opportunities,
        build_sector_flow_map_for_opportunities,
    )
    from app.services.trading_session import build_trading_session

    def _emit(stage: str) -> None:
        if progress is not None:
            progress(stage)

    limits = {**_FULL_UNIVERSE_BUDGETS, **(budgets or {})}
    timings: dict[str, float] = {}
    started = time.monotonic()

    def _lap(name: str, since: float) -> None:
        timings[name] = round(time.monotonic() - since, 2)

    _emit("sector_heat")
    mark = time.monotonic()
    sector_heat = build_sector_heat_ranking(decision_at=decision_at)
    _lap("sector_heat", mark)
    if not sector_heat:
        return _summary(
            trade_date=trade_date,
            ok=False,
            reason="sector_heat_unavailable",
            timings=timings,
            started=started,
        )

    resolved_trade_date = str(
        trade_date
        or build_trading_session(decision_at).get("effective_trade_date")
        or ""
    ).strip() or None
    if not resolved_trade_date:
        return _summary(
            trade_date=None,
            ok=False,
            reason="trade_date_unresolved",
            timings=timings,
            started=started,
        )

    # 全白名单：这是与发现基金链路唯一的刻意差异（见模块 docstring）。
    labels = _unique_labels(
        str(row.get("sector_label") or "").strip() for row in sector_heat
    )
    if not labels:
        return _summary(
            trade_date=resolved_trade_date,
            ok=False,
            reason="no_sector_labels",
            timings=timings,
            started=started,
        )

    _emit("sector_flow")
    mark = time.monotonic()
    sector_flow_by_label = build_sector_flow_map_for_opportunities(
        sector_heat,
        labels,
        trade_date=resolved_trade_date,
        total_timeout_seconds=limits["flow"],
    )
    _lap("sector_flow", mark)

    # 默认跳过：它只影响 `confidence`，而 confidence 不落库、也不参与 entry_state
    # 判定（见模块 docstring 的逐条论证），却是全流程最贵的一段。
    sector_divergence_by_label: dict[str, dict] = {}
    if include_divergence:
        _emit("sector_divergence")
        mark = time.monotonic()
        sector_divergence_by_label = build_sector_divergence_map_for_opportunities(
            labels,
            total_timeout_seconds=limits["divergence"],
        )
        _lap("sector_divergence", mark)

    _emit("sector_position")
    mark = time.monotonic()
    sector_position_by_label = build_sector_position_map_for_opportunities(
        labels,
        as_of_trade_date=resolved_trade_date,
        total_timeout_seconds=limits["position"],
    )
    _lap("sector_position", mark)

    # 分位分母走零网络缓存。前台已经是全白名单，这里 exclude 掉它们之后通常为空，
    # 保留调用是为了与发现基金/日报两条链路的分工保持一致（基准腿由联网行反推）。
    _emit("percentile_universe")
    mark = time.monotonic()
    percentile_position_by_label = build_sector_percentile_universe_positions(
        labels,
        exclude_labels=labels,
        reference_positions=sector_position_by_label,
        as_of_trade_date=resolved_trade_date,
        total_timeout_seconds=limits["percentile"],
    )
    _lap("percentile_universe", mark)

    _emit("mainline_regime")
    mark = time.monotonic()
    mainline_snapshot = build_mainline_regime_snapshot(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        sector_position_by_label=sector_position_by_label,
        sector_labels=labels,
        percentile_position_by_label=percentile_position_by_label,
        decision_at=decision_at,
    )
    mainline_by_label = mainline_regime_by_label(mainline_snapshot)
    _lap("mainline_regime", mark)

    _emit("score_and_persist")
    mark = time.monotonic()
    rows = _score_select_and_persist_directions(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        sector_divergence_by_label=sector_divergence_by_label,
        mainline_by_label=mainline_by_label,
        sector_position_by_label=sector_position_by_label,
        # 捕获不服务任何单个用户，没有"用户点名的方向"，因此不给排序加分。
        focus_sectors=[],
        effective_trade_date=resolved_trade_date,
    )
    _lap("score_and_persist", mark)

    return _summary(
        trade_date=resolved_trade_date,
        ok=True,
        reason=None,
        timings=timings,
        started=started,
        universe_size=len(labels),
        mainline_available=len(mainline_by_label),
        selected=len(rows or []),
    )


def _summary(
    *,
    trade_date: str | None,
    ok: bool,
    reason: str | None,
    timings: dict[str, float],
    started: float,
    universe_size: int = 0,
    mainline_available: int = 0,
    selected: int = 0,
) -> dict[str, Any]:
    persisted = _persisted_stats(trade_date) if trade_date else {}
    return {
        "policy_version": SECTOR_DIRECTION_CAPTURE_POLICY_VERSION,
        "ok": bool(ok),
        "reason": reason,
        "trade_date": trade_date,
        "universe_size": universe_size,
        "mainline_available": mainline_available,
        "selected_for_display": selected,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "timings": timings,
        **persisted,
    }


def _persisted_stats(trade_date: str) -> dict[str, Any]:
    """回读当天落库结果。

    `persisted` 单独看没有意义：证据不足时趋势分会被写成 ≤45 的占位值，行数照样是 78。
    因此必须同时报 `with_trend_evidence`（`trend_evidence_coverage > 0` 的行数）——它才是
    「这次捕获对退出侧有没有用」的判据。
    """
    try:
        from app.database import _connect

        with _connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS persisted,
                    SUM(CASE WHEN trend_evidence_coverage > 0 THEN 1 ELSE 0 END)
                        AS with_trend_evidence
                FROM sector_direction_states
                WHERE trade_date = ?
                """,
                (trade_date,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — 回读失败不改变捕获本身的成败
        # 必须与「落库 0 行」区分开：这次生产首跑就是因为 MySQL 侧缺列，落库与回读都
        # 静默失败（两处都是 best-effort），摘要只显示 None，从输出完全看不出是缺列。
        logger.warning("回读方向状态落库结果失败", exc_info=True)
        return {"persisted_readback_error": f"{type(exc).__name__}: {exc}"[:300]}
    if row is None:
        return {"persisted_readback_error": "no_row"}
    persisted = int(row["persisted"] or 0)
    with_evidence = int(row["with_trend_evidence"] or 0)
    return {
        "persisted": persisted,
        "with_trend_evidence": with_evidence,
        "degraded": max(0, persisted - with_evidence),
    }


def _unique_labels(labels) -> list[str]:
    seen: dict[str, None] = {}
    for label in labels:
        text = str(label or "").strip()
        if text and text not in seen:
            seen[text] = None
    return list(seen)


# ---------------------------------------------------------------------------
# 回填：按日线重算历史交易日的**趋势轴**
# ---------------------------------------------------------------------------

#: 回填行的来源标记。发现基金链路与每日捕获写 'captured'。
DIRECTION_STATE_SOURCE_BACKFILLED = "backfilled"


def backfill_sector_direction_trend(
    *,
    trade_dates: list[str],
    labels: list[str] | None = None,
    position_budget_seconds: float = 300.0,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """把若干历史交易日的趋势轴重算并落库，标记 `source='backfilled'`。

    ## 能重算什么、不能重算什么

    趋势轴的全部输入都是日线的纯函数——20 日收益、距 MA20、距 MA60、20 日上涨天数占比，
    加上相对强度的横截面分位。实测对 4 个历史交易日逐日取数，价格结构 6/6 命中、
    `component_coverage.trend` 全为 1.0，且数值逐日真实变化（半导体材料
    37.51 → 31.99 → 24.68 → 20.79）。所以「历史趋势分只能当天记、事后算不出来」是不成立的。

    **资金流不重算。** 历史资金流拿不回来，而 `date_aligned = flow.get("date_aligned")
    is not False` —— 喂一份日期错位的资金流会让 `evidence_quality` 掉成 `insufficient`，
    趋势分随之被兜底成 ≤45 的占位值，比不喂更糟。因此这里显式传空资金流：趋势轴不用它
    （它只进 participation 轴），而空值不会把 `date_aligned` 打成 False。

    代价是同一行里的 `participation_score` 只是中性填充，`entry_state` 由它派生因而**不可
    当作历史入场判断**。这正是要写 `source='backfilled'` 的原因：发现基金的滞回读取
    （`load_previous_direction_states`）过滤掉这些行，退出侧的趋势历史
    （`load_direction_trend_history`）才收。

    ## 两条硬约束

    * **绝不覆盖「已有可用趋势证据」的行。** 判据是 `trend_evidence_coverage > 0`，不是
      「这一天有没有行」。两者的区别很关键：迁移之前写入的行该列为 NULL，而它们的趋势分
      可能正是证据不足时的 ≤45 占位值（实测 08-07 那批 78 行里有证据的是 **0** 个）。
      这种行退出侧本来就拒收，而且因为读取侧遇无证据日会**停止回溯**，它们会像路障一样
      把更早的回填结果整段挡住——实测回填了 5 天 390 行，历史序列仍然读成空。用重算值
      替换它们只增加可用信息、不损失任何在用数据。真正当天捕获且有证据的行一律不动。

      副作用要说清楚：被替换的行 `source` 变成 `backfilled`，于是发现基金的滞回
      （`load_previous_direction_states` 只认 `captured`）不再把那天当作历史。影响限于
      紧邻的一天，且每日捕获上线后会自然长回来。
    * **滞回三列显式置零**（`qualifies_for_ready=0`、`consecutive_qualifying_days=0`），
      不假装算得出来。它们路径依赖于逐日真实资金流，重算不出可信值；反正读取侧会过滤。

    ## 仍存在的 point-in-time 偏差（如实标注）

    横截面分位的分母用的是**今天的**白名单板块集合。历史那天的白名单若不同，重算出的分位
    就不完全等于当天真实会算出的值。几天的回填可以忽略，长历史会带来幸存者偏差——所以这
    是给「让退出侧的连续天数尽快有序列可用」用的补数手段，不是回测数据源。
    """
    from app.services.discovery_sector_heat import build_sector_heat_ranking
    from app.services.discovery_sector_position import (
        build_sector_position_map_for_opportunities,
    )
    from app.services.mainline_regime import (
        build_mainline_regime_snapshot,
        mainline_regime_by_label,
    )
    from app.services.sector_opportunity_scoring import score_sector_opportunity_rows

    def _emit(stage: str) -> None:
        if progress is not None:
            progress(stage)

    dates = [str(d).strip() for d in trade_dates if str(d or "").strip()]
    if not dates:
        return {"ok": False, "reason": "no_trade_dates", "days": []}

    sector_heat = build_sector_heat_ranking()
    if not sector_heat:
        return {"ok": False, "reason": "sector_heat_unavailable", "days": []}

    whitelist = _unique_labels(
        str(row.get("sector_label") or "").strip() for row in sector_heat
    )
    targets = _unique_labels(labels) if labels else whitelist
    targets = [label for label in targets if label in set(whitelist)]
    if not targets:
        return {"ok": False, "reason": "no_matching_labels", "days": []}

    heat_rows = [
        row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip() in set(targets)
    ]

    days: list[dict[str, Any]] = []
    # 按交易日正序处理。滞回三列已显式置零、不依赖前一天，正序只是让日志与"历史是往前长
    # 出来的"这件事一致。
    for trade_date in sorted(dates):
        _emit(f"backfill {trade_date}")
        # 判据是「已有可用趋势证据」而不是「这天有行」：覆盖度为 NULL 的存量行会挡住
        # 更早的回填（读取侧遇无证据日停止回溯），必须允许被替换。
        existing = _labels_with_trend_evidence(trade_date)
        pending = [label for label in targets if label not in existing]
        if not pending:
            days.append(
                {
                    "trade_date": trade_date,
                    "written": 0,
                    "skipped_existing": len(targets),
                    "with_trend_evidence": 0,
                }
            )
            continue

        position_by_label = build_sector_position_map_for_opportunities(
            pending,
            as_of_trade_date=trade_date,
            total_timeout_seconds=position_budget_seconds,
        )
        snapshot = build_mainline_regime_snapshot(
            sector_heat,
            # 刻意空：历史资金流拿不回来，喂错位的比不喂更糟（见 docstring）。
            sector_flow_by_label={},
            sector_position_by_label=position_by_label,
            sector_labels=pending,
            percentile_position_by_label={},
        )
        mainline_by_label = mainline_regime_by_label(snapshot)
        rows = score_sector_opportunity_rows(
            [
                row
                for row in heat_rows
                if str(row.get("sector_label") or "").strip() in set(pending)
            ],
            sector_flow_by_label={},
            sector_divergence_by_label={},
            mainline_by_label=mainline_by_label,
            focus_sectors=None,
            drop_unavailable=False,
        )
        written, with_evidence = _record_backfilled_trend_rows(rows, trade_date=trade_date)
        days.append(
            {
                "trade_date": trade_date,
                "written": written,
                "skipped_existing": len(targets) - len(pending),
                "with_trend_evidence": with_evidence,
            }
        )

    return {
        "ok": True,
        "reason": None,
        "policy_version": SECTOR_DIRECTION_CAPTURE_POLICY_VERSION,
        "target_labels": len(targets),
        "days": days,
        "written_total": sum(int(day["written"]) for day in days),
        "with_trend_evidence_total": sum(
            int(day["with_trend_evidence"]) for day in days
        ),
    }


def _labels_with_trend_evidence(trade_date: str) -> set[str]:
    """该交易日**已有可用趋势证据**的板块，这些行不许动。

    刻意不返回「所有已有行」：覆盖度为 NULL/0 的行携带的是来源不明或证据不足的趋势分，
    退出侧本来就拒收，而且会挡住更早的回填。它们可以被重算值替换。
    """
    try:
        from app.database import _connect

        with _connect() as connection:
            rows = connection.execute(
                "SELECT sector_label FROM sector_direction_states "
                "WHERE trade_date = ? AND trend_evidence_coverage > 0",
                (trade_date,),
            ).fetchall()
    except Exception:  # noqa: BLE001 — 读不到就当没有，写入侧仍按唯一键幂等
        logger.warning("读取 %s 已有方向状态失败", trade_date, exc_info=True)
        return set()
    return {str(row["sector_label"]) for row in rows if row and row["sector_label"]}


def _record_backfilled_trend_rows(
    rows,
    *,
    trade_date: str,
) -> tuple[int, int]:
    """写入回填行。

    用 upsert 而非纯 INSERT：调用方已经用 `_labels_with_trend_evidence` 把「已有可用趋势
    证据」的板块排除在 `pending` 之外，所以这里能撞到的冲突行一定是覆盖度 NULL/0 的、
    允许被替换的行。那道前置过滤就是保护栏，不靠这里再判一次。
    """
    from datetime import datetime, timezone

    from app.services.sector_direction_state import (
        ENTRY_POLICY_VERSION_V3,
        SECTOR_DIRECTION_STATE_SCHEMA_VERSION,
    )

    payload = []
    with_evidence = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        label = str(row.get("sector_label") or "").strip()
        if not label:
            continue
        if str(row.get("score_policy_version") or "") != ENTRY_POLICY_VERSION_V3:
            continue
        coverage = _to_float((row.get("component_coverage") or {}).get("trend"))
        if coverage:
            with_evidence += 1
        payload.append(
            (
                trade_date,
                label,
                SECTOR_DIRECTION_STATE_SCHEMA_VERSION,
                ENTRY_POLICY_VERSION_V3,
                str(row.get("entry_state") or "forming"),
                str(row.get("raw_entry_state") or row.get("entry_state") or "forming"),
                # 滞回三列不假装算得出来：它们路径依赖于逐日真实资金流。
                0,
                0,
                _to_float(row.get("trend_strength_score")),
                _to_float(row.get("participation_score")),
                _to_float(row.get("position_risk_score")),
                _to_float(row.get("direction_score")),
                now,
                coverage,
                DIRECTION_STATE_SOURCE_BACKFILLED,
            )
        )
    if not payload:
        return 0, 0

    _COLUMNS = """
            trade_date, sector_label, schema_version, policy_version,
            entry_state, raw_entry_state, qualifies_for_ready,
            consecutive_qualifying_days, trend_strength_score,
            participation_score, position_risk_score, direction_score,
            recorded_at, trend_evidence_coverage, source
    """
    _UPDATES = (
        "entry_state",
        "raw_entry_state",
        "qualifies_for_ready",
        "consecutive_qualifying_days",
        "trend_strength_score",
        "participation_score",
        "position_risk_score",
        "direction_score",
        "recorded_at",
        "trend_evidence_coverage",
        "source",
    )
    written = 0
    try:
        from app.database import _connect

        with _connect() as connection:
            if getattr(connection, "dialect", "sqlite") == "mysql":
                assignments = ", ".join(f"{col} = VALUES({col})" for col in _UPDATES)
                statement = (
                    f"INSERT INTO sector_direction_states ({_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    f"ON DUPLICATE KEY UPDATE {assignments}"
                )
            else:
                assignments = ", ".join(f"{col} = excluded.{col}" for col in _UPDATES)
                statement = (
                    f"INSERT INTO sector_direction_states ({_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, sector_label) DO UPDATE SET "
                    f"{assignments}"
                )
            cursor = connection.executemany(statement, payload)
            close_cursor = getattr(cursor, "close", None)
            if callable(close_cursor):
                close_cursor()
            connection.commit()
            written = len(payload)
    except Exception:  # noqa: BLE001
        logger.warning("写入 %s 回填方向状态失败", trade_date, exc_info=True)
        return 0, 0
    return written, with_evidence


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


__all__ = [
    "DIRECTION_STATE_SOURCE_BACKFILLED",
    "SECTOR_DIRECTION_CAPTURE_POLICY_VERSION",
    "backfill_sector_direction_trend",
    "capture_sector_direction_states",
]
