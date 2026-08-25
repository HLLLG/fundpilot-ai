from __future__ import annotations

"""日报「持仓板块方向机会分 + 板块轮动参考」。

把荐基（discovery）验证过的双轨机会打分（`sector_opportunity_scoring.py`）接到日报：
- 给每个持仓板块一个方向判断（`held`，即使该板块暂不构成机会也会返回，标 `opportunity_available=False`）
- 给出当前全市场机会分最高的方向作为轮动参考（`market_top`），供 LLM 判断「持仓是否踏空更强方向」

数据源复用 `discovery_sector_heat.build_sector_heat_ranking_for_ui()`（市场 Tab 共享缓存，
秒级返回，无额外网络开销）与 `sector_opportunity_scoring.build_sector_flow_map_for_opportunities`
（板块资金流，带总预算超时）。全程 best-effort：任意异常/超时都不阻塞日报，返回
`{"available": False, ...}`。
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import math
import time
from typing import Any

from app.models import Holding
from app.services.sector_labels import (
    normalize_sector_label,
    sector_family_relation,
    sector_family_root,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_READY_TO_START,
    build_sector_divergence_map_for_opportunities,
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
)

logger = logging.getLogger(__name__)

SECTOR_FLOW_BUDGET_SECONDS = 4.0
SECTOR_DIVERGENCE_BUDGET_SECONDS = 4.0
#: 价格结构是**逐板块**联网取日线。`build_sector_position_map_for_opportunities` 的默认
#: `max_workers=4` 隐含了"持仓板块数 ≤ 4"这个前提：超过 4 个就要跑两波，而预算固定 8 s 一分
#: 没多。这里把并发开到板块数（`_fetch_sector_position_map` 传 max_workers），让持仓这一小批
#: 一波跑完，墙钟不再随板块数线性增长；再叠一轮缺口补齐，因为缺一个板块就等于那只基金当天
#: 没有 `entry_state`。
#:
#: **不要把这段当成 2026-08-11 14:30 超时的修复。** 线上实测这一段其实很便宜：6 个板块冷缓存
#: 2.50 s、热缓存 0.13 s，而且全部走 `eastmoney_board_fund_flow_daily_close` 这条零网络的缓存
#: 路径，6/6 命中。那次超时的真实原因在上下文线程池——`sse_analysis_context_workers=2` 而单份
#: 日报提交 6 个增强项，`sector_opportunity` 排在第 5 位，排队就把它的预算耗掉了
#: （见 `shared_executors.ANALYSIS_ENHANCEMENT_TASK_COUNT`）。本段的价值是**完整性**，不是延迟。
#:
#: 并发上限存在的意义只是防止持仓极度分散时同时打出几十个请求（逐板块会走 akshare 子进程
#: 与成分股代理，不是廉价调用）。超过上限才需要多跑一波，预算也才随之增加。
_SECTOR_POSITION_MAX_WORKERS = 8
#: 一波的墙钟预算。单板块实测约 2.2 s，8 s 是 3.6 倍余量。
_SECTOR_POSITION_WAVE_SECONDS = 8.0
#: 缺口补齐轮的额外预算：只对首轮没拿到的板块再跑一次，命中率优先于速度。
_SECTOR_POSITION_RETRY_SECONDS = 8.0
#: 未提供持仓板块数时按这个数推导默认预算，保持与历史常量完全一致（max(4,4,8+8-8)…见下）。
_DEFAULT_HELD_SECTOR_COUNT = 4
#: 分位分母走零网络缓存，实测 78 个白名单板块 0.18 s。原值 4.0 是 22 倍余量，而它直接
#: 计入总预算上限，等于让最坏墙钟为一个纯内存步骤多留 4 s。收到 2.0（11 倍余量）以压低
#: 总上限；真超了也只是分位分母退回持仓板块，不影响 regime 本身。
PERCENTILE_UNIVERSE_BUDGET_SECONDS = 2.0
#: 打分、去重与快照构建都是纯内存运算（实测 0.012 s）；留 1 s 覆盖线程调度与 GC 抖动。
_SCORING_MARGIN_SECONDS = 1.0

def sector_position_budget_seconds(held_sector_count: int | None = None) -> float:
    """价格结构预算：一波跑完 + 一轮缺口补齐。

    波数按**并发上限**算（不是默认的 4）：`_fetch_sector_position_map` 会把并发开到板块数，
    所以 8 个以内的持仓一波就能跑完。再叠一轮补齐预算——持仓方向层缺一个板块，那只基金当天
    就没有 `entry_state`，宁可多等 8 s 也要把缺口补上。
    """
    count = _DEFAULT_HELD_SECTOR_COUNT if held_sector_count is None else int(held_sector_count)
    waves = max(1, math.ceil(max(0, count) / _SECTOR_POSITION_MAX_WORKERS))
    return _SECTOR_POSITION_WAVE_SECONDS * waves + _SECTOR_POSITION_RETRY_SECONDS


def sector_opportunity_total_budget_seconds(held_sector_count: int | None = None) -> float:
    """本函数最坏情况下的总墙钟：三段并发取最大值，分位分母与打分在其后串行。

    **这是给调用方的契约**——`analysis_facts` 的外层超时直接从它派生，两边因此不可能
    再漂移。历史缺陷是外层写死 5 s 而内层最坏 12 s+：网络稍慢就把已经跑到一半的方向证据
    整体丢掉，`held` 退化成 `{}`，日报当天彻底没有板块方向层。而且 `future.cancel()` 对
    已运行任务无效，被放弃的请求仍会跑完自己的预算，只是结果没人要——裁掉的是"等待"，
    不是"开销"，纯亏。

    快乐路径不受影响（实测全链路约 3.6 s，`_enhancement_result` 一就绪就返回）；随板块数
    抬高的只是慢路径的上限。即便真的超了，`progress` 里已经装好的 `held` 也会被外层取用
    （见 `build_holding_sector_opportunity_context` 的 `progress` 参数），不再整层丢弃。
    """
    return (
        max(
            SECTOR_FLOW_BUDGET_SECONDS,
            SECTOR_DIVERGENCE_BUDGET_SECONDS,
            sector_position_budget_seconds(held_sector_count),
        )
        + PERCENTILE_UNIVERSE_BUDGET_SECONDS
        + _SCORING_MARGIN_SECONDS
    )


#: 兼容既有导入：等于 `sector_opportunity_total_budget_seconds()` 的默认值（11 s）。
SECTOR_POSITION_BUDGET_SECONDS = sector_position_budget_seconds()
SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS = sector_opportunity_total_budget_seconds()
MARKET_TOP_LIMIT = 5
MARKET_TOP_CANDIDATE_LIMIT = 8


class _StageBudget:
    """把「一个总预算」翻译成各阶段可用的剩余时间。

    每个阶段保留自己的默认上限（慢阶段不该因为总预算宽裕就无限等），但没有任何阶段
    能越过总 deadline。这样调用方只需要认 `SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS`
    一个数字，而不必自己去加总内层的四个常量——后者正是此前漂移的原因。
    """

    __slots__ = ("_deadline",)

    def __init__(self, total_seconds: float) -> None:
        self._deadline = time.monotonic() + max(0.0, float(total_seconds))

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def stage(self, default_seconds: float) -> float:
        """该阶段实际可用预算：默认上限与总剩余取小。"""
        return min(max(0.0, float(default_seconds)), self.remaining())

    def exhausted(self) -> bool:
        return self.remaining() <= 0.0


def _build_holding_mainline(
    *,
    sector_heat: list[dict],
    flow_by_label: dict[str, dict],
    position_by_label: dict[str, dict],
    held_labels: list[str],
    trade_date: str | None,
    percentile_budget_seconds: float = PERCENTILE_UNIVERSE_BUDGET_SECONDS,
) -> tuple[dict[str, dict], dict[str, Any]]:
    """按荐基同一套两段分工，为持仓板块算一份主线快照。

    分工照抄 `discovery_pipeline`，只是前台集合从"约 24 个预筛板块"换成"用户持有的
    3～5 个板块"，因此**比荐基更便宜**：

    * **regime 行**：对持仓板块联网取日线序列（每个板块自己的正确基准，A 股沪深300、
      港股恒生系列）；
    * **分位分母**：对全白名单走零网络缓存，基准腿由上一步的联网行反推
      （`build_sector_percentile_universe_positions`）。分母是全白名单而不是那几个持仓
      板块，所以不存在"在 3~5 个样本里排 83 分位"那种失真——`build_mainline_regime_snapshot`
      的 docstring 专门警告过这一点，荐基当初也是这样修的。
    """
    if not held_labels:
        return {}, {"available": False, "reason": "no_sector"}
    from app.services.discovery_sector_position import (
        build_sector_percentile_universe_positions,
    )
    from app.services.mainline_regime import (
        build_mainline_regime_snapshot,
        mainline_regime_by_label,
    )

    if not position_by_label:
        return {}, {"available": False, "reason": "sector_position_unavailable"}

    whitelist = _unique_labels(
        str(row.get("sector_label") or "").strip() for row in sector_heat
    )
    percentile_by_label: dict[str, dict] = {}
    # 预算已经耗尽时不再发起分位分母查询：它是"把分母从持仓板块扩到全白名单"的增强，
    # 缺席只会让分位失真一点，而硬撑着调用会让整段被外层判超时、连 regime 一起丢掉。
    if whitelist and percentile_budget_seconds > 0:
        try:
            percentile_by_label = build_sector_percentile_universe_positions(
                whitelist,
                exclude_labels=held_labels,
                reference_positions=position_by_label,
                as_of_trade_date=trade_date,
                total_timeout_seconds=percentile_budget_seconds,
            )
        except Exception:  # noqa: BLE001 - 分母扩不了就用较小分母，不阻塞日报
            percentile_by_label = {}

    try:
        snapshot = build_mainline_regime_snapshot(
            sector_heat,
            sector_flow_by_label=flow_by_label,
            sector_position_by_label=position_by_label,
            sector_labels=held_labels,
            percentile_position_by_label=percentile_by_label,
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        return {}, {"available": False, "reason": "mainline_snapshot_build_error"}

    by_label = mainline_regime_by_label(snapshot)
    # 哪些持仓板块今天没拿到 regime 行 —— 它们不会有 `entry_state`，方向层退化成旧版机会分，
    # 退出判定也一起失效。这必须显式可见，否则"方向层可用"会掩盖"其中两个板块其实没有"。
    missing_labels = [label for label in held_labels if label not in by_label]
    return by_label, {
        "available": bool(by_label),
        "complete": not missing_labels,
        "missing_labels": missing_labels,
        "source": "report_computed",
        "trade_date": snapshot.get("effective_trade_date") or trade_date,
        "schema_version": snapshot.get("schema_version"),
        "entry_policy_version": snapshot.get("entry_policy_version"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "sector_count": snapshot.get("sector_count"),
        "available_count": snapshot.get("available_count"),
        "percentile_universe_size": snapshot.get("percentile_universe_size"),
        # 滞回结果由 `_apply_read_only_hysteresis` 在打分之后统一覆盖这两个键，
        # 这里给出的是"还没套滞回"的初值。
        "hysteresis_applied": False,
        "hysteresis": {"applied": False, "reason": "not_resolved_yet"},
    }


def resolve_holding_mainline_context(
    trade_date: str | None,
) -> tuple[dict[str, dict], dict[str, Any]]:
    """回退路径：复用荐基当天已冻结的主线快照。

    日报现在默认**自己算**（见 `_build_holding_mainline`，实测全链路约 3.6 s，且热度与
    资金流与本函数所在请求共用、不重复拉取）。这条读取路径保留作为联网 position 取不到
    时的兜底：严格按 `effective_trade_date` 匹配，不用昨天的顶替——过期主线比没有主线
    更危险。
    """
    normalized_date = str(trade_date or "").strip()
    if not normalized_date:
        return {}, {"available": False, "reason": "no_trade_date"}
    try:
        from app.request_context import get_request_user_id
        from app.services.mainline_regime import mainline_regime_by_label
        from app.services.mainline_snapshot_repository import (
            load_mainline_snapshot_for_trade_date,
        )

        snapshot = load_mainline_snapshot_for_trade_date(
            user_id=get_request_user_id(),
            trade_date=normalized_date,
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        return {}, {"available": False, "reason": "mainline_snapshot_error"}
    if not isinstance(snapshot, dict) or not snapshot:
        return {}, {
            "available": False,
            "reason": "mainline_snapshot_missing_for_trade_date",
            "trade_date": normalized_date,
        }
    by_label = mainline_regime_by_label(snapshot)
    return by_label, {
        "available": True,
        "source": "discovery_frozen_snapshot",
        "trade_date": normalized_date,
        "schema_version": snapshot.get("schema_version"),
        "entry_policy_version": snapshot.get("entry_policy_version"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "sector_count": snapshot.get("sector_count"),
        "percentile_universe_size": snapshot.get("percentile_universe_size"),
        "hysteresis_applied": False,
        "hysteresis": {"applied": False, "reason": "not_resolved_yet"},
    }


#: 日报对方向状态账本是**只读**的。写入必须继续由荐基单方负责：那张表没有 userId 维度、
#: 是全局按 (交易日, 板块) 幂等覆盖的账本，而日报只看得见用户持有的 3～5 个板块——参与
#: 写入就会用一份窄得多的输入覆盖荐基对同一板块算好的状态。
#:
#: 但"不能写"不等于"不能读"。此前这里连读都不读，于是日报拿到的永远是当日原始档位：
#: 同一个板块今天 ready_to_start、明天掉回 forming、后天又上来，而这种抖动大多来自阈值
#: 边界上的一两分之差。荐基靠滞回把它压掉了，日报没有，两个界面对同一天同一板块因此给出
#: 不同的方向结论——这正是日报决策显得"善变"的来源之一。
#:
#: `apply_direction_state_hysteresis` 是纯函数（只读 rows + previous_states，返回新列表），
#: 所以只读接入是安全的。代价是「连续达标天数」的语义依赖荐基是否在过去若干交易日运行过：
#: 荐基停跑的那天没有记录，streak 会从 1 重新起算。因此它是一个**下界**，必须按下界披露，
#: 不能说成"该方向确实只连续满足了 N 天"。
_HYSTERESIS_READ_ONLY_NOTE = (
    "连续达标天数来自荐基的全局方向状态账本，日报只读不写；"
    "账本缺失某个交易日时该天数会从 1 重新起算，因此它是下界。"
)


def _load_direction_state_history(
    previous_trade_date: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """只读上一交易日的方向状态。返回 `(states, reason)`，states 为 None 表示无历史。"""
    if not previous_trade_date:
        return None, "no_previous_trade_date"
    try:
        from app.services.sector_direction_state import load_previous_direction_states

        states = load_previous_direction_states(previous_trade_date)
    except Exception:  # noqa: BLE001 - 滞回是增强项，读不到就退回当日原始档位
        return None, "direction_state_read_error"
    if states is None:
        return None, "no_direction_state_history"
    return states, "loaded"


def _apply_read_only_hysteresis(
    rows: list[dict[str, Any]],
    *,
    trade_date: str | None,
    previous_trade_date: str | None,
    previous_states: dict[str, Any] | None,
    history_reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """对已打分的行套上滞回（纯函数，绝不落库），并返回可披露的来源元数据。"""
    from app.services.sector_direction_state import (
        EXIT_TREND_THRESHOLD,
        READY_CONFIRMATION_DAYS,
        SECTOR_DIRECTION_STATE_SCHEMA_VERSION,
        apply_direction_state_hysteresis,
    )

    meta: dict[str, Any] = {
        "applied": False,
        "read_only": True,
        "schema_version": SECTOR_DIRECTION_STATE_SCHEMA_VERSION,
        "history_source": "discovery_global_direction_state_ledger",
        "history_trade_date": previous_trade_date,
        "history_status": history_reason,
        "ready_confirmation_days": READY_CONFIRMATION_DAYS,
        "exit_trend_threshold": EXIT_TREND_THRESHOLD,
        "consecutive_days_is_lower_bound": True,
        "note": _HYSTERESIS_READ_ONLY_NOTE,
    }
    if not rows:
        meta["reason"] = "no_scored_rows"
        return rows, meta
    try:
        smoothed = apply_direction_state_hysteresis(
            rows,
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            previous_states=previous_states,
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        meta["reason"] = "hysteresis_error"
        return rows, meta
    # `previous_states is None` 时 `apply_direction_state_hysteresis` 只做标注、不做平滑，
    # 所以「已套上滞回」必须以真的读到历史为条件，否则 hysteresis_applied 会撒谎。
    meta["applied"] = previous_states is not None
    meta["history_label_count"] = len(previous_states or {})
    if previous_states is None:
        meta["reason"] = history_reason
    return smoothed, meta


def build_holding_sector_opportunity_context(
    holdings: list[Holding],
    *,
    trade_date: str | None = None,
    fetch_sector_heat=None,
    fetch_sector_position=None,
    mainline_by_label: dict[str, dict] | None = None,
    mainline_meta: dict[str, Any] | None = None,
    total_budget_seconds: float | None = None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 `{available, held: {sector_label: opportunity_row}, market_top: [opportunity_row]}`。

    `held` 按标准化后的板块 label 建索引，供 `analysis_facts.py` 按持仓行 `sector_name` 反查；
    `market_top` 是当前全市场机会分最高的若干方向（去掉已持有的，避免和 `held` 重复），
    用于日报叙述「相对更强的方向是哪些」（板块轮动参考）。

    `total_budget_seconds` 是本函数的**总墙钟上限**，各阶段预算从它派生（见
    `_StageBudget`）。默认按持仓板块数推导（`sector_opportunity_total_budget_seconds`），
    调用方应直接复用同一个函数作为自己的外层超时，不要另写一个数字。

    `progress` 是调用方传入的可变字典，本函数会在**持仓方向层一算完就立刻写进去**
    （`progress["held"]`），供外层在自己的 deadline 到点时取用已完成的部分。这修的是一个
    真实的全损：轮动参考与分位分母排在方向层之后，它们慢一点就会让外层判超时，而外层的
    fallback 是 `held={}`——已经算好的持仓方向证据被连带丢掉，日报当天彻底没有方向层，
    随后 6 只持仓被数据门禁全部降为观察（2026-08-11 14:30 实测）。
    """
    tracker = progress if isinstance(progress, dict) else {}
    tracker["started_at"] = time.monotonic()
    budget = _StageBudget(
        total_budget_seconds
        if total_budget_seconds is not None
        else sector_opportunity_total_budget_seconds(
            len(
                _unique_labels(
                    normalize_sector_label(holding.sector_name) for holding in holdings
                )
            )
        )
    )
    held_labels = _unique_labels(
        normalize_sector_label(holding.sector_name) for holding in holdings
    )
    if not held_labels:
        return _unavailable("no_sector")

    heat_error_reason: str | None = None
    try:
        heat_fetcher = fetch_sector_heat or _default_fetch_sector_heat
        sector_heat = [
            row for row in (heat_fetcher() or []) if isinstance(row, dict)
        ]
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        sector_heat = []
        heat_error_reason = "sector_heat_error"

    usable_sector_heat = [
        row for row in sector_heat if _heat_has_usable_evidence(row)
    ]
    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in usable_sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    if not heat_by_label and heat_error_reason is None:
        heat_error_reason = "sector_heat_empty"

    fallback_heat_by_label = _held_fallback_heat_by_label(holdings)
    flow_heat = list(usable_sector_heat)
    flow_heat.extend(
        fallback_heat_by_label[label]
        for label in held_labels
        if label not in heat_by_label
    )

    top_by_heat = sorted(
        usable_sector_heat,
        key=lambda row: _num(row.get("heat_score")) or float("-inf"),
        reverse=True,
    )
    top_labels = [
        str(row.get("sector_label") or "").strip()
        for row in top_by_heat[:MARKET_TOP_CANDIDATE_LIMIT]
        if str(row.get("sector_label") or "").strip()
    ]
    flow_labels = _unique_labels([*held_labels, *top_labels])

    # 资金流（全部候选标签）与量价背离回测（仅已持有标签，M1.4 confidence 升级判定）并发
    # 拉取——两者是独立的板块级 IO，串行执行会让本函数最坏耗时翻倍。
    flow_by_label: dict[str, dict] = {}
    divergence_by_label: dict[str, dict] = {}
    position_by_label: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sector-opportunity-ctx") as executor:
        flow_future = executor.submit(
            build_sector_flow_map_for_opportunities,
            flow_heat,
            flow_labels,
            trade_date=trade_date,
            total_timeout_seconds=budget.stage(SECTOR_FLOW_BUDGET_SECONDS),
        )
        divergence_future = executor.submit(
            build_sector_divergence_map_for_opportunities,
            held_labels,
            total_timeout_seconds=budget.stage(SECTOR_DIVERGENCE_BUDGET_SECONDS),
        )
        # 主线 regime 的 20 日价格结构：只对持仓板块联网，与资金流/背离同批并发。
        # 它不依赖那两者，所以并发是安全的；分位分母与快照构建在三者都就位之后做。
        position_future = executor.submit(
            _fetch_sector_position_map,
            held_labels,
            trade_date,
            fetch_sector_position,
            # 逐板块联网、内部 4 并发：预算必须随板块数伸缩，否则 6 个板块跑两波却只给
            # 一波的时间，第二波必然被砍掉（进而 `position_by_label` 不全甚至为空）。
            budget_seconds=budget.stage(
                sector_position_budget_seconds(len(held_labels))
            ),
        )
        try:
            loaded_flow_by_label = flow_future.result()
            flow_by_label = (
                loaded_flow_by_label if isinstance(loaded_flow_by_label, dict) else {}
            )
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            flow_by_label = {}
        try:
            divergence_by_label = divergence_future.result() or {}
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            divergence_by_label = {}
        try:
            position_by_label = position_future.result() or {}
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            position_by_label = {}

    if mainline_by_label is None and mainline_meta is None:
        mainline_by_label, mainline_meta = _build_holding_mainline(
            sector_heat=usable_sector_heat,
            flow_by_label=flow_by_label,
            position_by_label=position_by_label,
            held_labels=held_labels,
            trade_date=trade_date,
            percentile_budget_seconds=budget.stage(PERCENTILE_UNIVERSE_BUDGET_SECONDS),
        )
        if not mainline_by_label:
            # 联网价格结构取不到时，退回复用荐基当天已冻结的快照（如果有）。
            fallback_by_label, fallback_meta = resolve_holding_mainline_context(trade_date)
            if fallback_by_label:
                mainline_by_label, mainline_meta = fallback_by_label, fallback_meta

    held: dict[str, dict[str, Any]] = {}
    for label in held_labels:
        has_market_heat = label in heat_by_label
        heat_row = heat_by_label.get(label) or fallback_heat_by_label[label]
        flow = flow_by_label.get(label)
        # 主线 regime 只在当天已有冻结快照时才有；拿不到就退回旧版机会分（不开成熟度层），
        # 而不是喂一个空 regime 让 classify_entry_state_v3 输出"看起来是结论、实际只反映
        # 没数据"的档位。
        mainline_row = (mainline_by_label or {}).get(label)
        try:
            opportunity = describe_sector_opportunity(
                heat_row,
                flow,
                # `focus` 的唯一作用是 +6 的 `focus_bonus`，语义是"用户点名要看的方向"。
                # 这里曾经传 `{label}`，等于给**每一个持仓板块**都恒定加 6 分，而荐基只给
                # 用户显式选择的关注方向加。同一个板块因此在日报里天然比在荐基里高 6 分，
                # 两个界面对同一天同一方向给出不同分数、进而不同档位。持有一个板块不是看多
                # 它的证据，不该换来分数加成。
                focus=None,
                divergence_backtest=divergence_by_label.get(label),
                mainline=mainline_row,
                entry_policy_enabled=mainline_row is not None,
            )
        except Exception:  # noqa: BLE001 - one row must not block the report
            opportunity = None
        if opportunity and not has_market_heat and not _flow_has_usable_evidence(flow):
            opportunity = {
                **opportunity,
                "confidence": "不足",
                "opportunity_available": False,
                "entry_hint": "数据不足，保持观察",
            }
        if opportunity:
            held[label] = opportunity

    previous_trade_date = _resolve_previous_trade_date(trade_date)
    previous_states, history_reason = _load_direction_state_history(previous_trade_date)

    # 持仓方向：滞回按 label 原地覆盖回 `held`。这是 guard 与 prompt 真正消费的那一份。
    held_rows, hysteresis_meta = _apply_read_only_hysteresis(
        [held[label] for label in held_labels if label in held],
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        previous_states=previous_states,
        history_reason=history_reason,
    )
    held = {
        str(row.get("sector_label") or ""): row
        for row in held_rows
        if str(row.get("sector_label") or "")
    }
    direction_exit_by_fund_code = _attach_direction_exit(
        held,
        holdings=holdings,
        trade_date=trade_date,
    )

    # 方向层到这里已经完备（含滞回与退出判定）。立刻交给调用方：后面的轮动参考与分位
    # 分母都是"锦上添花"，不该因为它们慢就让已经算好的持仓方向证据一起被外层丢掉。
    tracker["held"] = dict(held)
    tracker["direction_exit_by_fund_code"] = dict(direction_exit_by_fund_code)
    tracker["sector_flow_by_label"] = dict(flow_by_label)
    tracker["divergence_backtest"] = dict(divergence_by_label)
    tracker["heat_available"] = bool(heat_by_label)

    # 轮动参考：拆成「打分 → 滞回 → 选择」，与荐基 `_score_select_and_persist_directions`
    # 同一顺序。此前这里用的是复合入口 `select_sector_opportunities`（内部打分即选择），
    # 于是排序发生在滞回之前——而 `entry_state` 是 `_entry_sort_score` 的第一优先级，
    # 排序依据和最终展示的状态会是两套东西。荐基那边专门为这点留了注释："滞回必须在
    # **选择之前**生效"。
    try:
        from app.services.sector_opportunity_scoring import (
            score_sector_opportunity_rows,
            select_scored_sector_opportunities,
        )

        scored_rows = score_sector_opportunity_rows(
            usable_sector_heat,
            sector_flow_by_label=flow_by_label,
            sector_divergence_by_label=divergence_by_label,
            mainline_by_label=mainline_by_label or {},
            # 同上：`focus_sectors` 是"用户点名的方向"，不是"用户持有的方向"。轮动参考本来
            # 就要把已持有的剔掉，给它们加分只会让它们占掉名额再被丢弃。
            focus_sectors=None,
        )
        scored_rows, _ = _apply_read_only_hysteresis(
            scored_rows,
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            previous_states=previous_states,
            history_reason=history_reason,
        )
        selected = select_scored_sector_opportunities(
            scored_rows,
            max_total=MARKET_TOP_LIMIT + len(held_labels),
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        selected = []

    held_label_set = set(held_labels)
    market_top = [
        item for item in selected if item.get("sector_label") not in held_label_set
    ][:MARKET_TOP_LIMIT]

    resolved_mainline_meta = dict(
        mainline_meta or {"available": False, "reason": "mainline_snapshot_not_requested"}
    )
    resolved_mainline_meta["hysteresis"] = hysteresis_meta
    resolved_mainline_meta["hysteresis_applied"] = bool(hysteresis_meta.get("applied"))

    result = {
        "available": bool(heat_by_label),
        "held": held,
        "market_top": market_top,
        "sector_flow_by_label": flow_by_label,
        # M1 数据契约（design 第7节）：analysis_facts.holdings[].flow_divergence_backtest
        # 由 analysis_facts.py 从这里按持仓板块 label 反查，避免重复计算同一份回测。
        "divergence_backtest": divergence_by_label,
        # 逐基金的退出判定（只含拿到自己入场契约的那些）。板块行只能采用同方向最早那笔
        # 买入作为基线，这里让每只基金用回自己的那笔。
        "direction_exit_by_fund_code": direction_exit_by_fund_code,
        # 主线复用的可用性必须显式披露：`entry_state` 在快照缺席时压根不会出现，
        # 下游（prompt / guard / 前端）需要能区分"方向尚未成熟"与"今天没有主线快照"。
        "mainline": resolved_mainline_meta,
    }
    if heat_error_reason is not None:
        result["reason"] = heat_error_reason
    return result


def _attach_direction_exit(
    held: dict[str, dict],
    *,
    holdings: list[Holding],
    trade_date: str | None,
) -> dict[str, dict]:
    """给每个持仓方向挂上退出判定（key: `direction_exit`），原地修改。

    只在**已持有**的方向上算：退出是持仓语义，轮动参考里那些没买的方向不需要它（也是
    职责边界——发现基金负责能不能进，日报负责已持仓的加/减/退）。

    另外返回**按基金代码**的退出判定。`direction_exit` 挂在板块行上，而入场契约是**每只
    基金**一份：同一方向持有多只基金时，板块行只能采用其中一笔（按最早买入），其余基金
    自己的入场基线就永远用不上。返回值让 `analysis_facts` 给每个持仓行挂上属于它自己的
    那一份——guard 早就在读 `facts_row["direction_exit"]` 了，此前那里只是板块行的副本。

    整段 best-effort：退出判定是新增的增强项，任何一步失败都不能让日报生成不出来。
    """
    if not held:
        return {}
    try:
        from app.services.sector_direction_exit import (
            assess_direction_exit,
            load_direction_entry_contracts,
            load_direction_ledger_health,
            load_direction_trend_history,
        )
        from app.services.sector_direction_state import EXIT_TREND_THRESHOLD

        labels = list(held.keys())
        # 生产链路（analysis_facts）总会传 trade_date；这里补一层兜底，否则调用方省略时
        # 历史读不到、连续跌破天数恒为 1，「持续走坏」的升级会静默失效。
        history_cutoff = str(trade_date or "").strip()
        if not history_cutoff:
            from app.services.trading_session import get_effective_trade_date

            history_cutoff = get_effective_trade_date()
        history_by_label = load_direction_trend_history(
            labels,
            before_trade_date=history_cutoff,
        )
        # 账本捕获健康度随每份退出判定一起披露：连续跌破天数完全依赖 19:10 定时捕获，
        # 断更时天数停在 1、该升的档位安静地不升——必须让下游能看到"天数是下界"这件事
        # 有没有实据。整批持仓共用同一份健康度，只查一次。
        ledger_health = load_direction_ledger_health(history_cutoff)
        contracts_by_code = load_direction_entry_contracts(
            [holding.fund_code for holding in holdings],
        )
        contracts_by_code = _reconcile_contracts_with_holdings(
            contracts_by_code,
            holdings=holdings,
        )
        # 板块行的基线：同一方向可能对应多只基金，取最早那笔买入（先买的那笔才是"当初
        # 为什么进这个方向"）。这里按**基金当前所属板块**归并，而不是按契约里记录的板块
        # 名——015788 买入时记作「信创」、现在归到「数字经济」，用历史 label 当 key 会让
        # 契约压根进不了 `held` 的索引，`entry_reference` 直接为 null 且没有任何解释。
        # 归并后 `assess_direction_exit` 仍会因分类漂移拒绝相对模式（拿两个篮子的分数对比
        # 会给出错误基线），但会把原因作为 `entry_reference_note` 披露出来。
        current_label_by_code: dict[str, str] = {}
        for holding in holdings:
            label = normalize_sector_label(holding.sector_name)
            if label:
                current_label_by_code[str(holding.fund_code or "").strip()] = label

        contract_by_label: dict[str, dict] = {}
        for code, contract in contracts_by_code.items():
            label = current_label_by_code.get(str(code or "").strip()) or str(
                contract.get("sector_label") or ""
            ).strip()
            if not label:
                continue
            existing = contract_by_label.get(label)
            if existing is None or str(contract.get("entry_date") or "") < str(
                existing.get("entry_date") or ""
            ):
                contract_by_label[label] = contract

        gain_by_label: dict[str, bool] = {}
        gain_by_code: dict[str, bool] = {}
        for holding in holdings:
            in_gain = (holding.holding_profit or 0) > 0
            gain_by_code[str(holding.fund_code or "").strip()] = in_gain
            label = normalize_sector_label(holding.sector_name)
            if not label:
                continue
            if in_gain:
                gain_by_label[label] = True

        for label, row in held.items():
            exit_row = assess_direction_exit(
                sector_label=label,
                entry_state=row.get("entry_state"),
                trend_strength=row.get("trend_strength_score"),
                exit_trend_threshold=EXIT_TREND_THRESHOLD,
                trend_history=history_by_label.get(label, []),
                entry_contract=contract_by_label.get(label),
                has_unrealized_gain=gain_by_label.get(label, False),
                # 今天这一行的失效条件判定，用来和买入时冻结的承诺逐条对照。
                invalidation_checks=row.get("invalidation_checks"),
                # 反弹修复结构分：结构破坏是判废唯一触发条件且修复过线时放宽档位。
                structure_repair=row.get("structure_repair"),
            )
            exit_row["ledger_health"] = dict(ledger_health)
            row["direction_exit"] = exit_row

        # 卖出档退出 × 同族口径当日仍可布局：把分歧披露挂到退出判定上（只披露、不仲裁）。
        _attach_family_direction_divergence(held, trade_date=history_cutoff)

        # 逐基金：用**这只基金自己**的入场契约，对着它当前板块的方向行判一次。
        by_fund_code: dict[str, dict] = {}
        for holding in holdings:
            code = str(holding.fund_code or "").strip()
            label = current_label_by_code.get(code)
            row = held.get(label) if label else None
            if not code or row is None:
                continue
            contract = contracts_by_code.get(code)
            if contract is None:
                # 没有自己的契约时不必单独算：板块行那份已经是它能得到的最好判定。
                continue
            fund_exit_row = assess_direction_exit(
                sector_label=label,
                entry_state=row.get("entry_state"),
                trend_strength=row.get("trend_strength_score"),
                exit_trend_threshold=EXIT_TREND_THRESHOLD,
                trend_history=history_by_label.get(label, []),
                entry_contract=contract,
                has_unrealized_gain=gain_by_code.get(code, False),
                invalidation_checks=row.get("invalidation_checks"),
                structure_repair=row.get("structure_repair"),
            )
            fund_exit_row["ledger_health"] = dict(ledger_health)
            # 族内分歧是板块级事实（卖出档与否由共享的 entry_state/趋势决定，板块行与
            # 逐基金行必然同侧），逐基金那份判定同样要带上——guard 与卡片读的是
            # facts_row["direction_exit"]（即这一份），不带就等于只在板块行披露了一半。
            sector_exit = row.get("direction_exit") or {}
            for key in ("family_divergence", "family_divergence_note"):
                if sector_exit.get(key):
                    fund_exit_row[key] = sector_exit[key]
            by_fund_code[code] = fund_exit_row
        return by_fund_code
    except Exception:  # noqa: BLE001 — 绝不阻塞日报
        logger.warning("方向退出判定失败，本次跳过", exc_info=True)
        return {}


_FAMILY_RELATION_SCOPE = {"parent": "整体", "fine_theme": "细分", "sibling": "同族"}


def _attach_family_direction_divergence(
    held: dict[str, dict],
    *,
    trade_date: str | None,
) -> None:
    """持仓方向被判卖出档退出、而同族口径当日在全局账本仍可布局时，把分歧写上退出判定。

    同族口径（细分↔父行业，如 CXO↔医疗）行情代理不同、方向状态分开计算，同日一边判
    退出、一边判可布局完全可能（2026-08 线上实测：「医疗」invalid 触发 011373 大幅减仓
    的同日，荐基对「CXO」给出分批买入）。不披露，减仓卡就会被读成"系统否定了整个医药
    主题"，与同日的买入卡构成裸矛盾。

    数据源是荐基当日写入的全局方向状态账本（`sector_direction_states`，日报只读，与
    跨报告披露同一纪律：**只看当日**——旧交易日的状态只会制造新的矛盾；今天没跑荐基
    就没有当日行，如实跳过）。只披露、不仲裁：不改动作、比例或任何分数。
    """
    try:
        from app.services.sector_direction_exit import (
            EXIT_STATE_DEEP_REDUCE,
            EXIT_STATE_EXIT,
            EXIT_STATE_REDUCE,
        )

        sell_side = {EXIT_STATE_REDUCE, EXIT_STATE_DEEP_REDUCE, EXIT_STATE_EXIT}
        selling = {
            label: row
            for label, row in held.items()
            if isinstance(row.get("direction_exit"), dict)
            and row["direction_exit"].get("exit_state") in sell_side
        }
        if not selling or not trade_date:
            return
        from app.services.sector_direction_state import load_previous_direction_states

        # 函数名里的 previous 指"滞回读上一交易日"这个主用途；它按给定交易日读账本，
        # 传今天就是今天的行（荐基当日扫描写入的完整横截面，含 invalid）。
        states = load_previous_direction_states(trade_date)
        if not states:
            return
        for label, row in selling.items():
            root = sector_family_root(label)
            divergent = [
                {
                    "sector_label": record.sector_label,
                    "entry_state": record.entry_state,
                    "relation": sector_family_relation(label, record.sector_label),
                    "trade_date": trade_date,
                }
                for record in states.values()
                if normalize_sector_label(record.sector_label) != label
                and sector_family_root(record.sector_label) == root
                and record.entry_state == ENTRY_READY_TO_START
            ]
            if not divergent:
                continue
            first = divergent[0]
            scope = _FAMILY_RELATION_SCOPE.get(str(first.get("relation") or ""), "同族")
            exit_row = row["direction_exit"]
            exit_row["family_divergence"] = divergent
            exit_row["family_divergence_note"] = (
                f"同主题口径分歧：{scope}口径「{first['sector_label']}」今日在全局方向"
                f"账本中仍为可布局状态，而本退出判定针对的是「{label}」口径（两者行情"
                "代理不同、状态分开计算）。本判定不构成对整个主题的否定；若今日发现"
                "报告对该口径有买入推荐，请按同主题总敞口合并权衡。"
            )
    except Exception:  # noqa: BLE001 — 披露层，绝不阻塞日报
        logger.warning("同族方向分歧披露失败，本次跳过", exc_info=True)


def _reconcile_contracts_with_holdings(
    contracts_by_code: dict[str, dict],
    *,
    holdings: list[Holding],
) -> dict[str, dict]:
    """把每份入场契约与该持仓的购入日/首见日核对（确认成交闭环的读取侧）。

    契约是 discovery **推荐时**冻结的事件，不是成交回执；真实账户里用户可能推荐前就持有
    （契约根本不属于这笔持仓）、也可能拖了几周才买（推荐日的分数早已不代表买入决策）。
    核对逻辑与基线重定见 `reconcile_entry_contract_with_holding`；这里只负责取数——
    `fund_profiles` 的 `first_purchase_date`（用户购入日）与 `first_seen_date`
    （持仓首次出现日）由 OCR 导入链路自动维护，正是"用户实际什么时候有这笔仓"的最好证据。

    best-effort：档案读不到就原样返回契约（"不知道"不等于"错位"），绝不阻塞日报。
    """
    if not contracts_by_code:
        return contracts_by_code
    try:
        from app.services.holding_profile_batch import resolve_matched_profiles
        from app.services.sector_direction_exit import (
            load_trend_score_on_or_before,
            reconcile_entry_contract_with_holding,
        )

        profiles = resolve_matched_profiles(holdings)
        profile_by_code = {
            str(holding.fund_code or "").strip(): profile
            for holding, profile in zip(holdings, profiles)
            if profile is not None
        }
        reconciled: dict[str, dict] = {}
        for code, contract in contracts_by_code.items():
            profile = profile_by_code.get(str(code or "").strip())
            if profile is None:
                reconciled[code] = contract
                continue
            reconciled[code] = reconcile_entry_contract_with_holding(
                contract,
                first_purchase_date=profile.first_purchase_date,
                first_seen_date=profile.first_seen_date,
                rebase_score_loader=load_trend_score_on_or_before,
            )
        return reconciled
    except Exception:  # noqa: BLE001 — 核对是增强项，失败退回未核对的契约
        logger.warning("入场契约与持仓时间线核对失败，沿用原契约", exc_info=True)
        return contracts_by_code


def _resolve_previous_trade_date(trade_date: str | None) -> str | None:
    if not trade_date:
        return None
    try:
        from app.services.trading_session import get_previous_trade_date

        return get_previous_trade_date(trade_date)
    except Exception:  # noqa: BLE001 - 交易日历不可用时退回"无历史"，不阻塞日报
        return None


def _fetch_sector_position_map(
    held_labels: list[str],
    trade_date: str | None,
    fetch_sector_position=None,
    *,
    budget_seconds: float = SECTOR_POSITION_BUDGET_SECONDS,
) -> dict[str, dict]:
    """持仓板块的 20 日价格结构与相对强度（联网，逐板块用各自正确基准）。

    **持仓板块必须尽量取全**：缺一个板块就等于那只基金当天没有 `entry_state`，方向层退化成
    旧版机会分，退出判定也一起失效（`_build_holding_mainline` 只为拿到 position 的 label 出
    regime 行）。所以这里做两件事：

    1. 并发开到板块数，让持仓这一小批（通常 3～8 个）**一波跑完**，而不是 4 个一波；
    2. 首轮有缺口时，用剩余预算只对缺失的板块补一次——单个板块的瞬时失败不该让它一整天
       没有方向层。

    仍然以 `budget_seconds` 为硬停：数据源真挂了的时候，"无限等准确"不是准确，是把日报也
    一起拖死。取不全时如实缺席，由上层披露。
    """
    if not held_labels or budget_seconds <= 0:
        return {}
    if fetch_sector_position is not None:
        try:
            return fetch_sector_position(held_labels, trade_date) or {}
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            return {}

    from app.services.discovery_sector_position import (
        build_sector_position_map_for_opportunities,
    )

    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    resolved: dict[str, dict] = {}
    pending = list(held_labels)
    # 首轮 + 一次补齐。补齐次数刻意有上限：数据源持续失败时重试只是把同一个错误多犯几次。
    for attempt in range(2):
        if not pending:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            batch = (
                build_sector_position_map_for_opportunities(
                    pending,
                    as_of_trade_date=trade_date,
                    total_timeout_seconds=remaining,
                    max_workers=max(1, min(len(pending), _SECTOR_POSITION_MAX_WORKERS)),
                )
                or {}
            )
        except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
            break
        resolved.update(
            {label: row for label, row in batch.items() if isinstance(row, dict) and row}
        )
        pending = [label for label in pending if label not in resolved]
        if pending and attempt == 0:
            logger.info(
                "持仓板块价格结构首轮缺口 %s，用剩余预算补一次", ",".join(pending)
            )
    if pending:
        logger.warning(
            "持仓板块价格结构最终缺口 %s：这些方向今日没有 entry_state", ",".join(pending)
        )
    return resolved


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "held": {},
        "market_top": [],
        "sector_flow_by_label": {},
        "divergence_backtest": {},
        "mainline": {"available": False, "reason": reason},
    }


def _held_fallback_heat_by_label(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    """Keep held flow evidence usable when the market heat ranking is unavailable."""
    result: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        label = normalize_sector_label(holding.sector_name)
        if not label or label in result:
            continue
        result[label] = {
            "sector_label": label,
            "change_1d_percent": holding.sector_return_percent,
        }
    return result


def _heat_has_usable_evidence(row: dict[str, Any]) -> bool:
    return any(
        _num(row.get(key)) is not None
        for key in ("change_1d_percent", "change_5d_percent", "heat_score")
    )


def _flow_has_usable_evidence(flow: dict[str, Any] | None) -> bool:
    if not isinstance(flow, dict) or not flow.get("available"):
        return False
    if flow.get("date_aligned") is False:
        return False

    today = _num(flow.get("today_main_force_net_yi"))
    five_day = _num(flow.get("cumulative_5d_net_yi"))
    today_available = (
        bool(flow.get("today_available"))
        if "today_available" in flow
        else today is not None
    )
    five_day_available = (
        bool(flow.get("five_day_available"))
        if "five_day_available" in flow
        else five_day is not None
    )
    return (today_available and today is not None) or (
        five_day_available and five_day is not None
    )


def _default_fetch_sector_heat() -> list[dict]:
    from app.services.discovery_sector_heat import build_sector_heat_ranking_for_ui

    return build_sector_heat_ranking_for_ui()


def _unique_labels(labels) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
