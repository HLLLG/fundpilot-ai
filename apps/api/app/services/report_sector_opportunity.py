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
import time
from typing import Any

from app.models import Holding
from app.services.sector_labels import normalize_sector_label
from app.services.sector_opportunity_scoring import (
    build_sector_divergence_map_for_opportunities,
    build_sector_flow_map_for_opportunities,
    describe_sector_opportunity,
)

SECTOR_FLOW_BUDGET_SECONDS = 4.0
SECTOR_DIVERGENCE_BUDGET_SECONDS = 4.0
# 主线 regime 只对"用户持有的那几个板块"联网取日线序列，实测 4 个板块 2.22 s；
# 分位分母走零网络缓存（78 个白名单板块 0.18 s），快照构建本身 0.012 s。
SECTOR_POSITION_BUDGET_SECONDS = 8.0
#: 分位分母走零网络缓存，实测 78 个白名单板块 0.18 s。原值 4.0 是 22 倍余量，而它直接
#: 计入总预算上限，等于让最坏墙钟为一个纯内存步骤多留 4 s。收到 2.0（11 倍余量）以压低
#: 总上限；真超了也只是分位分母退回持仓板块，不影响 regime 本身。
PERCENTILE_UNIVERSE_BUDGET_SECONDS = 2.0
#: 打分、去重与快照构建都是纯内存运算（实测 0.012 s）；留 1 s 覆盖线程调度与 GC 抖动。
_SCORING_MARGIN_SECONDS = 1.0

#: 本函数最坏情况下的总墙钟：资金流 / 背离 / 价格结构三段并发（取最大值），分位分母
#: 与打分在其后串行。
#:
#: **这个常量是给调用方的契约**——`analysis_facts` 的外层预算直接取它，两边因此不可能
#: 再漂移。历史缺陷是外层写死 5 s 而内层最坏 12 s+：网络稍慢就把已经跑到一半的方向证据
#: 整体丢掉，`held` 退化成 `{}`，日报当天彻底没有板块方向层。而且 `future.cancel()` 对
#: 已运行任务无效，被放弃的请求仍会跑完自己的预算，只是结果没人要——裁掉的是"等待"，
#: 不是"开销"，纯亏。
#:
#: 快乐路径不受影响（实测全链路约 3.6 s，`_enhancement_result` 一就绪就返回）；抬高的
#: 只是慢路径的上限，换来的是慢路径不再静默丢掉整层证据。
SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS = (
    max(
        SECTOR_FLOW_BUDGET_SECONDS,
        SECTOR_DIVERGENCE_BUDGET_SECONDS,
        SECTOR_POSITION_BUDGET_SECONDS,
    )
    + PERCENTILE_UNIVERSE_BUDGET_SECONDS
    + _SCORING_MARGIN_SECONDS
)
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
    return by_label, {
        "available": bool(by_label),
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
) -> dict[str, Any]:
    """返回 `{available, held: {sector_label: opportunity_row}, market_top: [opportunity_row]}`。

    `held` 按标准化后的板块 label 建索引，供 `analysis_facts.py` 按持仓行 `sector_name` 反查；
    `market_top` 是当前全市场机会分最高的若干方向（去掉已持有的，避免和 `held` 重复），
    用于日报叙述「相对更强的方向是哪些」（板块轮动参考）。

    `total_budget_seconds` 是本函数的**总墙钟上限**，各阶段预算从它派生（见
    `_StageBudget`）。默认 `SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS`，调用方应直接复用
    该常量作为自己的外层超时，不要另写一个数字。
    """
    budget = _StageBudget(
        total_budget_seconds
        if total_budget_seconds is not None
        else SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS
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
            budget_seconds=budget.stage(SECTOR_POSITION_BUDGET_SECONDS),
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
        # 主线复用的可用性必须显式披露：`entry_state` 在快照缺席时压根不会出现，
        # 下游（prompt / guard / 前端）需要能区分"方向尚未成熟"与"今天没有主线快照"。
        "mainline": resolved_mainline_meta,
    }
    if heat_error_reason is not None:
        result["reason"] = heat_error_reason
    return result


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
    """持仓板块的 20 日价格结构与相对强度（联网，逐板块用各自正确基准）。"""
    if not held_labels or budget_seconds <= 0:
        return {}
    try:
        if fetch_sector_position is not None:
            return fetch_sector_position(held_labels, trade_date) or {}
        from app.services.discovery_sector_position import (
            build_sector_position_map_for_opportunities,
        )

        return (
            build_sector_position_map_for_opportunities(
                held_labels,
                as_of_trade_date=trade_date,
                total_timeout_seconds=budget_seconds,
            )
            or {}
        )
    except Exception:  # noqa: BLE001 - best-effort，绝不阻塞日报
        return {}


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
