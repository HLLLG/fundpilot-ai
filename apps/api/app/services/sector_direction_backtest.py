from __future__ import annotations

"""板块方向模型的前瞻收益评估器（第 0 层「标尺」，离线研究用，绝不参与线上决策）。

## 这个模块解决什么问题

`sector_entry_maturity.2026-07.v2` / `sector_entry_maturity.2026-08.v3`
（`sector_opportunity_scoring.py`）与
`mainline_regime.v1`（`mainline_regime.py`）里的每一个权重、加减分和阈值都是手写常量，
`docs/PROJECT_CONTEXT.md`「荐基质量门与方向成熟度」已明确标注它们「是初始确定性策略，
不是已证明的收益最优参数」。在此之前，仓库里唯一的板块级验证是
`sector_flow_divergence_backtest.py`（只测单日量价背离的 T→T+1 命中率），**没有任何东西
把 `entry_state` / 三个成熟度分数与板块前瞻收益对齐**。也就是说，在这个模块存在之前，
任何"调权重"都只是用一组未验证的数字替换另一组未验证的数字。

本模块提供三件事，全部只读、纯离线：

1. `replay_sector_direction` —— 逐交易日 point-in-time 重放**生产打分器本身**，并为每个
   板块记录 T+5/10/20 的前瞻相对收益与路径最大不利偏移（MAE）。
2. `summarize_direction_replay` —— 按 `entry_state` 分桶统计，并对照三个基准：
   全板块等权、当日涨幅前 5（"拿热门凑数"的稻草人）、以及生产 `select_sector_opportunities`
   真正会展示给用户的那 8 个方向。
3. `compute_direction_factor_ic` / `scan_entry_gate_thresholds` —— 单因子 Rank IC + ICIR，
   以及入场线四个阈值的敏感性网格。这两者才是后续重定权重/改阈值的依据。

## 为什么直接调生产函数而不是重写一遍打分

重放链路复用 `summarize_sector_position`（本身已支持 `as_of_trade_date` 截断）、
`build_mainline_regime_snapshot`、`score_sector_opportunity_rows`、
`select_scored_sector_opportunities` 和 `classify_entry_state`。只要有任何一步是复制品，
回测结论就不再描述线上行为——这是这类评估器最常见也最致命的失效方式。

## 无前视（point-in-time）保证

* 决策时点固定为 D 日 15:30（收盘后），特征只用 ``date <= D`` 的行；
  `summarize_sector_position(as_of_trade_date=D)` 与资金流截断都由此保证。
* **建仓价是 D+1 的收盘价，不是 D 的收盘价。** D 收盘后才产生结论，当天已无法成交。
* 平仓价是建仓日之后第 h 个交易日的收盘价。
* 基准腿只使用同一段日期区间内基准自己的收盘价，绝不用未来日期外推。

## 已知数据缺口（诚实划界，不假装有）

以下缺口会让重放与线上行为存在**可量化的**差异，`DirectionReplay.caveats` 与
`feature_coverage` 会把它们原样带到结论里，不做隐藏或插值：

* **上涨广度无历史。** `advancing_ratio_percent` 只存在于实时主题榜快照里，全库没有任何
  历史持久化表。重放时它为 ``None``，而 `_entry_maturity_v2` 用的
  `_weighted_available_score` 会重归一化可得权重，于是形态成熟度里资金的权重被从 0.50
  放大到 0.50/0.75≈0.667。`breadth_by_label_date` 参数留作未来开始采集后的注入点。
* **日线无成交量。** `DailyKlineBar` 只有 date/close/change_percent，`volume_ratio_5d_vs_20d`
  为 ``None``，`market_structure` 因此丢掉它的 0.20 子权重。
* **资金四档结构无历史。** daykline 不含 flow_tiers，机构/散户拆分不可重建（只影响提示
  文案，不影响 pattern_label）。
* **资金流及时性偏乐观。** 生产盘中会把实时快照并入"今日"资金流；重放用的是收盘结算值。
  即重放对资金证据的及时性略优于真实线上环境。
* **本仓库沙箱出站到东财 `push2his` 全部被阻断**（见 `sector_flow_divergence_backtest`
  同一处划界）。因此默认 loader 无法在开发环境实测，内核 100% 可注入；真实结论必须在能
  访问上游的环境（生产/预发布）里跑一次 CLI 才成立。

## 统计口径

* 主指标是**按日横截面去均值后的超额**（``demeaned_excess``）。只看"相对沪深300 超额"
  仍会被"板块整体跑赢宽基"这一系统性溢价污染；去均值后衡量的才是"模型挑的方向是否好于
  当天所有板块的平均"。原始超额同时保留。
* t 检验在**决策日层面**做（先把同一天同一桶的观测取均值，再跨日检验），避免把同一天内
  高度相关的横截面观测当成独立样本。
* 前瞻窗口重叠（step=1 时 T+20 窗口逐日重叠）会让 IC/t 序列自相关、显著性被高估。
  想要不重叠样本请令 ``step >= max(horizons)``；两种情况都会在 caveats 里注明。
"""

from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import isfinite, sqrt
from statistics import fmean, median, pstdev
from typing import Any

from app.services.discovery_sector_heat import _heat_score
from app.services.mainline_regime import (
    build_mainline_regime_snapshot,
    mainline_regime_by_label,
)
from app.services.discovery_sector_position import summarize_sector_position
from app.services.sector_fund_flow_context import (
    _classify_flow_pattern,
    _flow_scale_yi,
    _normalized_flow,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_INVALID,
    ENTRY_POLICY_VERSION as ENTRY_POLICY_VERSION_V2,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    classify_entry_state,
    classify_entry_state_v3,
    score_sector_opportunity_rows,
    select_scored_sector_opportunities,
)
from app.services.sector_direction_state import (
    DirectionStateRecord,
    apply_direction_state_hysteresis,
)
from app.services.trading_session import CN_TZ

SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION = "sector_direction_backtest.v1"

DEFAULT_FORWARD_HORIZONS: tuple[int, ...] = (5, 10, 20)
#: `summarize_sector_position` 至少要 20 根日线才 available；60 日收益与相对强度要 61 根。
DEFAULT_WARMUP_DAYS = 61
#: 少于这么多个板块时当日横截面分位/Rank IC 没有意义。
MIN_LABELS_PER_DECISION_DAY = 8
#: 少于这么多个决策日不下"显著"结论（与 signal_backtest_stats 的样本量哲学一致）。
MIN_DECISION_DAYS_FOR_SIGNIFICANCE = 30
#: 决策日层面 t 统计量的显著门槛（双侧约 5%）。
T_STAT_SIGNIFICANCE_THRESHOLD = 2.0
#: 决策时点：D 日收盘后。用它构造 `build_trading_session` 需要的 decision_at。
DECISION_CLOCK = time(15, 30)

ENTRY_STATES: tuple[str, ...] = (
    ENTRY_READY_TO_START,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_FORMING,
    ENTRY_INVALID,
)

#: 参与 Rank IC 的因子。前 5 个是 `mainline_regime` 分量，后面是 V2 合成分数。
MAINLINE_COMPONENT_KEYS: tuple[str, ...] = (
    "relative_strength",
    "trend_persistence",
    "fund_flow",
    "breadth",
    "market_structure",
)
COMPOSITE_SCORE_KEYS: tuple[str, ...] = (
    "direction_score",
    # v3 的三个正交分块
    "trend_strength_score",
    "participation_score",
    "position_risk_score",
    # v2 遗留合成分数（回放 v2 口径时才有值）
    "setup_maturity_score",
    "entry_readiness_score",
    "price_structure_score",
    "research_score",
    "legacy_score",
)
FACTOR_KEYS: tuple[str, ...] = (*MAINLINE_COMPONENT_KEYS, *COMPOSITE_SCORE_KEYS)

BASELINE_ALL_SECTORS = "baseline_all_sectors"
BASELINE_TOP_CHANGE_1D = "baseline_top5_change_1d"
GROUP_PRODUCTION_SELECTION = "production_selection"

_BASELINE_TOP_N = 5

_HISTORICAL_GAP_CAVEATS: tuple[str, ...] = (
    "上涨广度（advancing_ratio_percent）无历史持久化：重放时为空，形态成熟度里资金权重被"
    "从 0.50 重归一化放大到约 0.667，与线上 A 股板块行为不同。",
    "日线无成交量：volume_ratio_5d_vs_20d 为空，market_structure 丢失其 0.20 子权重。",
    "资金四档结构（flow_tiers）无历史：机构/散户背离提示不可重建。",
    "生产盘中会并入实时资金流快照，重放使用收盘结算值，对资金及时性偏乐观。",
    "建仓价取 D+1 收盘、平仓取其后第 h 个交易日收盘；未计交易成本、基金申赎费与净值滞后。",
    "结论只描述被评估的板块指数本身，不等于可买到的基金收益（跟踪误差、费率、T+1 确认未计）。",
)


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForwardOutcome:
    """一个决策日 + 一个板块 + 一个持有期的前瞻结果。"""

    horizon: int
    entry_date: str
    exit_date: str
    sector_return_percent: float
    benchmark_return_percent: float | None
    excess_percent: float | None
    max_adverse_excess_percent: float | None
    benchmark_calendar_aligned: bool
    #: 持有期内逐日的**单日**超额（板块单日收益 − 基准单日收益），长度等于 horizon。
    #: 组合层指标必须从逐日路径构造：只有终点超额的话，无法算波动、回撤和信息比，
    #: 于是"放弃集中 beta 换分散"这类改动在标尺上永远只表现为平均超额下降。
    daily_excess_path: tuple[float, ...] = ()


@dataclass(frozen=True)
class DirectionObservation:
    """一个决策日 + 一个板块的完整可复算记录。"""

    decision_date: str
    sector_label: str
    entry_state: str
    evidence_quality: str
    mainline_status: str
    confidence: str | None
    opportunity_available: bool
    change_1d_percent: float | None
    change_5d_percent: float | None
    factors: dict[str, float | None]
    gate_inputs: dict[str, Any]
    #: `mainline_regime` 的原始特征（离高点距离、position_label、20日超额…）。
    #: 分箱诊断需要它们：定权重和定分段效用必须有实测依据，不能再拍手写常量。
    features: dict[str, Any]
    selection_rank: int | None
    forward: dict[int, ForwardOutcome]

    def excess(self, horizon: int) -> float | None:
        outcome = self.forward.get(horizon)
        return outcome.excess_percent if outcome else None


@dataclass(frozen=True)
class SkippedDay:
    decision_date: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True)
class DirectionReplay:
    schema_version: str
    observations: list[DirectionObservation]
    decision_dates: list[str]
    labels: list[str]
    horizons: tuple[int, ...]
    step: int
    benchmark_label: str
    feature_coverage: dict[str, float]
    skipped_days: list[SkippedDay]
    caveats: list[str] = field(default_factory=list)
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3

    @property
    def observation_count(self) -> int:
        return len(self.observations)


# --------------------------------------------------------------------------
# 重放内核
# --------------------------------------------------------------------------


def replay_sector_direction(
    *,
    price_series_by_label: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_series: Sequence[Mapping[str, Any]],
    flow_series_by_label: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    breadth_by_label_date: Mapping[str, Mapping[str, float]] | None = None,
    forward_horizons: Sequence[int] = DEFAULT_FORWARD_HORIZONS,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    step: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
    price_source: str = "backtest_daily_kline",
    benchmark_label: str = "000300",
    selection_kwargs: Mapping[str, Any] | None = None,
    min_labels_per_day: int = MIN_LABELS_PER_DECISION_DAY,
    evidence_label_limit: int | None = None,
    expand_percentile_universe: bool = True,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> DirectionReplay:
    """逐日 point-in-time 重放生产打分器，并记录前瞻收益。

    ``price_series_by_label`` / ``benchmark_series`` 的行至少需要 ``date`` 与 ``close``；
    有 ``volume``/``amount`` 时会一并进入 `summarize_sector_position`（从而恢复量比这一维）。
    ``flow_series_by_label`` 的行需要 ``date`` 与 ``main_force_net_yi``。
    全部参数可注入，本函数不做任何网络或数据库访问。

    ``evidence_label_limit`` 复现生产的证据预筛：每天只有被
    `select_opportunity_evidence_labels` 选中的板块进入打分，其余板块的价格特征按
    ``expand_percentile_universe`` 决定是当分位分母（新行为）还是整体丢弃（旧行为）。
    两者对比即可单独量化"分位分母被预筛动量偏置污染"这一项的影响。
    """
    horizons = tuple(sorted({int(value) for value in forward_horizons if int(value) > 0}))
    if not horizons:
        raise ValueError("forward_horizons 至少需要一个正整数持有期")
    if step < 1:
        raise ValueError("step 必须 >= 1")

    prices = {
        label: _clean_price_rows(rows)
        for label, rows in price_series_by_label.items()
        if str(label).strip()
    }
    prices = {label: rows for label, rows in prices.items() if len(rows) >= warmup_days}
    if not prices:
        return DirectionReplay(
            schema_version=SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION,
            observations=[],
            decision_dates=[],
            labels=[],
            horizons=horizons,
            step=step,
            benchmark_label=benchmark_label,
            feature_coverage={},
            skipped_days=[],
            caveats=[
                f"没有任何板块的日线长度达到 warmup_days={warmup_days}，无法重放。",
                *_HISTORICAL_GAP_CAVEATS,
            ],
            entry_policy_version=entry_policy_version,
        )

    flows = {
        label: _clean_flow_rows(rows)
        for label, rows in (flow_series_by_label or {}).items()
        if str(label).strip()
    }
    benchmark_rows = _clean_price_rows(benchmark_series)
    benchmark_closes = {row["date"]: float(row["close"]) for row in benchmark_rows}
    benchmark_dates = sorted(benchmark_closes)

    index_by_label = {
        label: {row["date"]: position for position, row in enumerate(rows)}
        for label, rows in prices.items()
    }
    labels = sorted(prices)
    stateful_replay = entry_policy_version == ENTRY_POLICY_VERSION_V3
    processing_dates = _decision_calendar(
        prices,
        warmup_days=warmup_days,
        max_horizon=max(horizons),
        start_date=start_date,
        end_date=end_date,
        # V3 的跨日滞回必须逐交易日更新状态。即使统计抽样 step=20，也不能假装
        # 中间 19 个交易日不存在，否则回放的连续达标天数与线上策略不同。
        step=1 if stateful_replay else step,
    )
    sampled_dates = (
        set(processing_dates[::step]) if stateful_replay else set(processing_dates)
    )

    observations: list[DirectionObservation] = []
    skipped: list[SkippedDay] = []
    used_dates: list[str] = []
    selection_options = dict(selection_kwargs or {})
    previous_states: dict[str, DirectionStateRecord] | None = None
    previous_state_date: str | None = None

    for decision_date in processing_dates:
        sampled = decision_date in sampled_dates
        heat_rows: list[dict[str, Any]] = []
        position_map: dict[str, dict[str, Any]] = {}
        flow_map: dict[str, dict[str, Any]] = {}
        day_labels: list[str] = []

        for label in labels:
            rows = prices[label]
            cursor = index_by_label[label].get(decision_date)
            if cursor is None or cursor < warmup_days - 1:
                continue
            change_1d = _window_return_percent(rows, cursor, 1)
            change_5d = _window_return_percent(rows, cursor, 5)
            breadth = _lookup_breadth(breadth_by_label_date, label, decision_date)
            heat_rows.append(
                {
                    "sector_label": label,
                    "change_1d_percent": change_1d,
                    "change_5d_percent": change_5d,
                    "heat_score": _heat_score(change_1d, change_5d),
                    "rising_count": None,
                    "falling_count": None,
                    "flat_count": None,
                    "advancing_ratio_percent": breadth,
                }
            )
            position = summarize_sector_position(
                label,
                [dict(row) for row in rows[: cursor + 1]],
                benchmark_rows=[dict(row) for row in benchmark_rows],
                as_of_trade_date=decision_date,
            )
            position["source"] = price_source
            position["benchmark_code"] = benchmark_label
            position["benchmark_name"] = benchmark_label
            position["benchmark_source"] = "injected"
            position["benchmark_data_end_date"] = _latest_on_or_before(
                benchmark_dates, decision_date
            )
            position_map[label] = position
            flow_context = _flow_context_as_of(
                label,
                flows.get(label) or [],
                decision_date=decision_date,
                change_1d=change_1d,
            )
            if flow_context is not None:
                flow_map[label] = flow_context
            day_labels.append(label)

        if len(day_labels) < min_labels_per_day:
            if sampled:
                skipped.append(
                    SkippedDay(
                        decision_date,
                        "insufficient_cross_section",
                        f"只有 {len(day_labels)} 个板块具备完整特征，低于 {min_labels_per_day}",
                    )
                )
            if stateful_replay:
                previous_states = {}
                previous_state_date = decision_date
            continue

        evidence_labels = day_labels
        universe_positions: dict[str, dict[str, Any]] = {}
        if evidence_label_limit is not None:
            from app.services.discovery_sector_prefilter import (
                select_opportunity_evidence_labels,
            )

            selected = select_opportunity_evidence_labels(
                heat_rows, [], [], max_labels=evidence_label_limit
            )
            evidence_labels = [label for label in day_labels if label in set(selected)]
            if len(evidence_labels) < min_labels_per_day:
                if sampled:
                    skipped.append(
                        SkippedDay(
                            decision_date,
                            "insufficient_evidence_cross_section",
                            f"预筛后只剩 {len(evidence_labels)} 个板块",
                        )
                    )
                if stateful_replay:
                    previous_states = {}
                    previous_state_date = decision_date
                continue
            evidence_set = set(evidence_labels)
            if expand_percentile_universe:
                universe_positions = {
                    label: row
                    for label, row in position_map.items()
                    if label not in evidence_set
                }
            heat_rows = [row for row in heat_rows if row["sector_label"] in evidence_set]
            position_map = {
                label: row for label, row in position_map.items() if label in evidence_set
            }
            flow_map = {
                label: row for label, row in flow_map.items() if label in evidence_set
            }

        decision_at = datetime.combine(
            date.fromisoformat(decision_date), DECISION_CLOCK, tzinfo=CN_TZ
        )
        snapshot = build_mainline_regime_snapshot(
            heat_rows,
            sector_flow_by_label=flow_map,
            sector_position_by_label=position_map,
            sector_labels=evidence_labels,
            percentile_position_by_label=universe_positions or None,
            decision_at=decision_at,
        )
        effective = str(snapshot.get("effective_trade_date") or "")
        if effective != decision_date:
            # 交易日历缓存缺这一天时 `build_trading_session` 会把 effective_trade_date
            # 回滚到上一交易日，于是所有资金流都变成 date_aligned=False，打分被系统性
            # 压低。这种日子必须显式跳过并计数，不能静默产出被污染的观测。
            if sampled:
                skipped.append(
                    SkippedDay(
                        decision_date,
                        "trade_calendar_mismatch",
                        f"snapshot.effective_trade_date={effective or '空'}",
                    )
                )
            if stateful_replay:
                previous_states = {}
                previous_state_date = decision_date
            continue

        mainline_map = mainline_regime_by_label(snapshot)
        scored = score_sector_opportunity_rows(
            heat_rows,
            sector_flow_by_label=flow_map,
            mainline_by_label=mainline_map,
            drop_unavailable=False,
            entry_policy_version=entry_policy_version,
        )
        if not scored:
            if sampled:
                skipped.append(SkippedDay(decision_date, "no_scored_rows"))
            if stateful_replay:
                previous_states = {}
                previous_state_date = decision_date
            continue
        if stateful_replay:
            scored = apply_direction_state_hysteresis(
                scored,
                trade_date=decision_date,
                previous_trade_date=previous_state_date,
                previous_states=previous_states,
            )
            previous_states = {
                str(row.get("sector_label") or ""): DirectionStateRecord(
                    trade_date=decision_date,
                    sector_label=str(row.get("sector_label") or ""),
                    entry_state=str(row.get("entry_state") or ENTRY_FORMING),
                    raw_entry_state=str(
                        row.get("raw_entry_state") or row.get("entry_state") or ENTRY_FORMING
                    ),
                    qualifies_for_ready=bool(row.get("qualifies_for_ready")),
                    consecutive_qualifying_days=int(
                        row.get("consecutive_qualifying_days") or 0
                    ),
                )
                for row in scored
                if str(row.get("sector_label") or "").strip()
            }
            previous_state_date = decision_date
        if not sampled:
            continue

        selected = select_scored_sector_opportunities(
            [
                {key: value for key, value in row.items() if key != "opportunity_available"}
                for row in scored
                if row.get("opportunity_available")
            ],
            return_series_by_label={
                label: series
                for label, row in position_map.items()
                if isinstance(series := row.get("daily_returns_20d"), list) and series
            },
            **selection_options,
        )
        selection_rank = {
            str(row.get("sector_label")): rank for rank, row in enumerate(selected, start=1)
        }

        day_had_observation = False
        for row in scored:
            label = str(row.get("sector_label") or "").strip()
            if not label or label not in index_by_label:
                continue
            cursor = index_by_label[label].get(decision_date)
            if cursor is None:
                continue
            forward = _forward_outcomes(
                rows=prices[label],
                cursor=cursor,
                horizons=horizons,
                benchmark_closes=benchmark_closes,
                benchmark_dates=benchmark_dates,
            )
            if not forward:
                continue
            observations.append(
                DirectionObservation(
                    decision_date=decision_date,
                    sector_label=label,
                    entry_state=str(row.get("entry_state") or ENTRY_FORMING),
                    evidence_quality=str(row.get("evidence_quality") or "insufficient"),
                    mainline_status=str(
                        (mainline_map.get(label) or {}).get("status") or "insufficient"
                    ),
                    confidence=row.get("confidence"),
                    opportunity_available=bool(row.get("opportunity_available")),
                    change_1d_percent=_num(row.get("change_1d_percent")),
                    change_5d_percent=_num(row.get("change_5d_percent")),
                    factors=_extract_factors(row, mainline_map.get(label)),
                    gate_inputs=_extract_gate_inputs(row),
                    features=_extract_features(mainline_map.get(label)),
                    selection_rank=selection_rank.get(label),
                    forward=forward,
                )
            )
            day_had_observation = True
        if day_had_observation:
            used_dates.append(decision_date)

    caveats = list(_HISTORICAL_GAP_CAVEATS)
    if stateful_replay:
        caveats.insert(
            0,
            "V3 在生产选择前逐交易日应用连续达标确认与退出滞回；step 只控制统计抽样，"
            "不跳过中间交易日的状态更新。",
        )
    if step < max(horizons):
        caveats.insert(
            0,
            f"step={step} < 最长持有期 {max(horizons)}：前瞻窗口重叠，IC 与 t 序列自相关，"
            "显著性被高估。需要不重叠样本时令 step >= 最长持有期。",
        )
    else:
        caveats.insert(0, f"step={step} >= 最长持有期，前瞻窗口不重叠。")

    return DirectionReplay(
        schema_version=SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION,
        observations=observations,
        decision_dates=used_dates,
        labels=labels,
        horizons=horizons,
        step=step,
        benchmark_label=benchmark_label,
        feature_coverage=_feature_coverage(observations),
        skipped_days=skipped,
        caveats=caveats,
        entry_policy_version=entry_policy_version,
    )


# --------------------------------------------------------------------------
# 分桶统计与基准对照
# --------------------------------------------------------------------------


def summarize_direction_replay(
    replay: DirectionReplay,
    *,
    min_decision_days: int = MIN_DECISION_DAYS_FOR_SIGNIFICANCE,
) -> dict[str, Any]:
    """按 `entry_state` 分桶 + 三个基准对照，逐持有期给出统计。

    主指标是按日去均值后的超额；显著性在决策日层面检验。
    """
    demeaned = _demeaned_excess_index(replay)
    groups = _observation_groups(replay)

    result: dict[str, Any] = {
        "schema_version": replay.schema_version,
        "benchmark_label": replay.benchmark_label,
        "decision_day_count": len(replay.decision_dates),
        "observation_count": replay.observation_count,
        "label_count": len(replay.labels),
        "horizons": list(replay.horizons),
        "step": replay.step,
        "feature_coverage": replay.feature_coverage,
        "skipped_days": [
            {"decision_date": item.decision_date, "reason": item.reason, "detail": item.detail}
            for item in replay.skipped_days
        ],
        "caveats": list(replay.caveats),
        "groups": {},
    }
    for name, observations in groups.items():
        result["groups"][name] = {
            "horizons": {
                str(horizon): _group_stats(
                    observations,
                    horizon=horizon,
                    demeaned=demeaned,
                    min_decision_days=min_decision_days,
                )
                for horizon in replay.horizons
            },
        }
    result["verdict"] = _verdict(result, replay)
    return result


def _observation_groups(
    replay: DirectionReplay,
) -> dict[str, list[DirectionObservation]]:
    groups: dict[str, list[DirectionObservation]] = {
        state: [item for item in replay.observations if item.entry_state == state]
        for state in ENTRY_STATES
    }
    groups[BASELINE_ALL_SECTORS] = list(replay.observations)
    groups[GROUP_PRODUCTION_SELECTION] = [
        item for item in replay.observations if item.selection_rank is not None
    ]
    groups[BASELINE_TOP_CHANGE_1D] = _top_change_1d_baseline(replay)
    return groups


def _top_change_1d_baseline(replay: DirectionReplay) -> list[DirectionObservation]:
    """稻草人基准：每天取当日涨幅最高的 N 个板块（即"拿热门凑数"的做法）。"""
    by_date: dict[str, list[DirectionObservation]] = {}
    for item in replay.observations:
        by_date.setdefault(item.decision_date, []).append(item)
    picked: list[DirectionObservation] = []
    for _day, rows in by_date.items():
        ranked = sorted(
            (row for row in rows if row.change_1d_percent is not None),
            key=lambda row: (row.change_1d_percent or 0.0, row.sector_label),
            reverse=True,
        )
        picked.extend(ranked[:_BASELINE_TOP_N])
    return picked


def _demeaned_excess_index(
    replay: DirectionReplay,
) -> dict[tuple[str, int], float]:
    """每个 (决策日, 持有期) 的横截面平均超额，用于去掉市场与板块整体溢价。"""
    buckets: dict[tuple[str, int], list[float]] = {}
    for item in replay.observations:
        for horizon, outcome in item.forward.items():
            if outcome.excess_percent is None:
                continue
            buckets.setdefault((item.decision_date, horizon), []).append(
                outcome.excess_percent
            )
    return {key: fmean(values) for key, values in buckets.items() if values}


def _group_stats(
    observations: Sequence[DirectionObservation],
    *,
    horizon: int,
    demeaned: Mapping[tuple[str, int], float],
    min_decision_days: int,
) -> dict[str, Any]:
    raw: list[float] = []
    net: list[float] = []
    mae: list[float] = []
    by_day: dict[str, list[float]] = {}
    misaligned = 0

    for item in observations:
        outcome = item.forward.get(horizon)
        if outcome is None or outcome.excess_percent is None:
            continue
        cross_mean = demeaned.get((item.decision_date, horizon))
        if cross_mean is None:
            continue
        # 只统计真正进入样本的那些观测，否则 misaligned 会把被跳过的观测也算进去。
        if not outcome.benchmark_calendar_aligned:
            misaligned += 1
        value = outcome.excess_percent - cross_mean
        raw.append(outcome.excess_percent)
        net.append(value)
        by_day.setdefault(item.decision_date, []).append(value)
        if outcome.max_adverse_excess_percent is not None:
            mae.append(outcome.max_adverse_excess_percent)

    if not net:
        return {
            "observation_count": 0,
            "decision_day_count": 0,
            "available": False,
            "reason": "no_forward_observations",
        }

    day_means = [fmean(values) for values in by_day.values()]
    t_stat = _t_stat(day_means)
    significant = bool(
        len(day_means) >= min_decision_days
        and t_stat is not None
        and abs(t_stat) >= T_STAT_SIGNIFICANCE_THRESHOLD
    )
    return {
        "available": True,
        "observation_count": len(net),
        "decision_day_count": len(day_means),
        "benchmark_calendar_misaligned_count": misaligned,
        "hit_rate_percent": round(sum(value > 0 for value in net) / len(net) * 100, 1),
        "mean_excess_percent": round(fmean(raw), 3),
        "median_excess_percent": round(median(raw), 3),
        # 观测加权：每个观测是一次实际持仓，这是"平均每笔拿到多少超额"。
        "mean_demeaned_excess_percent": round(fmean(net), 3),
        "median_demeaned_excess_percent": round(median(net), 3),
        # 决策日加权：`decision_day_t_stat` 检验的就是这个均值。两者不相等（某些日子
        # 命中的方向多、某些日子只命中一个），显式并列输出，避免把 t 值读成前一个
        # 均值的显著性。
        "mean_decision_day_excess_percent": round(fmean(day_means), 3),
        "p10_demeaned_excess_percent": round(_percentile(net, 10.0), 3),
        "worst_demeaned_excess_percent": round(min(net), 3),
        "mean_max_adverse_excess_percent": round(fmean(mae), 3) if mae else None,
        "decision_day_t_stat": round(t_stat, 2) if t_stat is not None else None,
        "significant": significant,
    }


def _verdict(summary: Mapping[str, Any], replay: DirectionReplay) -> dict[str, Any]:
    """把统计压成一句可执行结论：入场线相对"全板块平均"到底有没有超额。"""
    groups = summary.get("groups") or {}
    ready = ((groups.get(ENTRY_READY_TO_START) or {}).get("horizons") or {})
    verdict: dict[str, Any] = {
        "policy_evaluated": replay.entry_policy_version,
        "auto_tuning_eligible": False,
        "by_horizon": {},
    }
    for horizon in replay.horizons:
        stats = ready.get(str(horizon)) or {}
        if not stats.get("available"):
            verdict["by_horizon"][str(horizon)] = "样本不足"
            continue
        if not stats.get("significant"):
            verdict["by_horizon"][str(horizon)] = (
                f"不显著（去均值超额 {stats['mean_demeaned_excess_percent']:+.2f}%，"
                f"决策日 {stats['decision_day_count']}）"
            )
            continue
        edge = stats["mean_demeaned_excess_percent"]
        direction = "正向" if edge > 0 else "反向"
        verdict["by_horizon"][str(horizon)] = f"{direction}显著（{edge:+.2f}%）"
    invalid = ((groups.get(ENTRY_INVALID) or {}).get("horizons") or {})
    verdict["invalid_bucket_underperforms"] = {
        str(horizon): (
            None
            if not (invalid.get(str(horizon)) or {}).get("available")
            else invalid[str(horizon)]["mean_demeaned_excess_percent"] < 0
        )
        for horizon in replay.horizons
    }
    return verdict


# --------------------------------------------------------------------------
# 组合层风险指标
# --------------------------------------------------------------------------

TRADING_DAYS_PER_YEAR = 252.0


def summarize_group_portfolio(
    replay: DirectionReplay,
    group_name: str,
    *,
    horizon: int,
    min_decision_days: int = MIN_DECISION_DAYS_FOR_SIGNIFICANCE,
    relative_to_universe: bool = True,
) -> dict[str, Any]:
    """把一个分组当成"每天等权买入、持有 h 日"的重叠窗口组合，给出风险调整后指标。

    **为什么必须有这一层**：逐观测的平均超额无法评价"分散"。相关方向同涨时，同时留下
    4 个高相关方向会抬高平均超额，但那正是集中风险；相关性去重在只看平均超额的标尺上
    必然表现为"退化"。年化波动、最大回撤、信息比与日均持仓相关性才能把这件事说清楚。

    构造：第 D 日开一个等权 cohort（该组当天选中的板块），持有 h 个交易日；组合在日历
    日 t 的单日超额 = 所有当日仍在持有的 cohort 的组内平均超额，再按 cohort 等权平均。

    ``relative_to_universe``（默认开）再减去"全板块等权"组合的同日序列，即多头该分组、
    空头等权全市场。**必须这么做**：这批东财概念板块整体相对沪深300 本身就有一个几十个
    百分点的系统性偏离，直接对标沪深300 的话，指标里绝大部分是"这个板块池是什么"而不是
    "模型挑得准不准"，各分组之间也就无法比较。这与逐观测主指标用按日去均值超额是同一个
    理由。
    """
    groups = _observation_groups(replay)
    observations = groups.get(group_name)
    if observations is None:
        raise ValueError(f"未知分组 {group_name}")

    trading_days = sorted({item.decision_date for item in replay.observations})
    day_index = {day: position for position, day in enumerate(trading_days)}

    cohort_paths, cohort_sizes, pairwise_correlations = _cohort_paths(
        observations, horizon=horizon, with_correlations=True
    )
    if not cohort_paths:
        return {
            "available": False,
            "reason": "no_daily_excess_paths",
            "group": group_name,
            "horizon": horizon,
        }

    contributions = _portfolio_contributions(cohort_paths, day_index)
    if not contributions:
        return {
            "available": False,
            "reason": "no_calendar_overlap",
            "group": group_name,
            "horizon": horizon,
        }

    universe_by_slot: dict[int, float] = {}
    if relative_to_universe:
        universe_paths, _sizes, _corr = _cohort_paths(
            groups[BASELINE_ALL_SECTORS], horizon=horizon, with_correlations=False
        )
        universe_contributions = _portfolio_contributions(universe_paths, day_index)
        universe_by_slot = {
            slot: fmean(values) for slot, values in universe_contributions.items()
        }

    ordered_slots = sorted(contributions)
    daily_excess = [
        fmean(contributions[slot]) - universe_by_slot.get(slot, 0.0)
        for slot in ordered_slots
    ]
    exposure_days = len(daily_excess)
    mean_daily = fmean(daily_excess)
    volatility_daily = pstdev(daily_excess) if len(daily_excess) > 1 else 0.0
    annualized_excess = mean_daily * TRADING_DAYS_PER_YEAR
    annualized_volatility = volatility_daily * sqrt(TRADING_DAYS_PER_YEAR)
    information_ratio = (
        annualized_excess / annualized_volatility if annualized_volatility > 0 else None
    )
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in daily_excess:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return {
        "available": True,
        "group": group_name,
        "horizon": horizon,
        "relative_to_universe": relative_to_universe,
        "decision_day_count": len(cohort_paths),
        "exposure_day_count": exposure_days,
        "mean_positions_per_day": round(fmean(cohort_sizes.values()), 2),
        "cumulative_excess_percent": round(cumulative, 2),
        "annualized_excess_percent": round(annualized_excess, 2),
        "annualized_excess_volatility_percent": round(annualized_volatility, 2),
        "information_ratio": round(information_ratio, 2)
        if information_ratio is not None
        else None,
        "max_excess_drawdown_percent": round(max_drawdown, 2),
        "mean_pairwise_correlation": round(fmean(pairwise_correlations), 3)
        if pairwise_correlations
        else None,
        "significant": bool(
            len(cohort_paths) >= min_decision_days
            and information_ratio is not None
            and abs(information_ratio) >= 0.5
        ),
    }


def _cohort_paths(
    observations: Sequence[DirectionObservation],
    *,
    horizon: int,
    with_correlations: bool,
) -> tuple[dict[str, list[tuple[float, ...]]], dict[str, int], list[float]]:
    paths: dict[str, list[tuple[float, ...]]] = {}
    for item in observations:
        outcome = item.forward.get(horizon)
        if outcome is None or not outcome.daily_excess_path:
            continue
        paths.setdefault(item.decision_date, []).append(outcome.daily_excess_path)
    sizes = {day: len(rows) for day, rows in paths.items()}
    correlations: list[float] = []
    if with_correlations:
        for rows in paths.values():
            for left in range(len(rows)):
                for right in range(left + 1, len(rows)):
                    value = _pearson(list(rows[left]), list(rows[right]))
                    if value is not None:
                        correlations.append(value)
    return paths, sizes, correlations


def _portfolio_contributions(
    cohort_paths: Mapping[str, Sequence[Sequence[float]]],
    day_index: Mapping[str, int],
) -> dict[int, list[float]]:
    contributions: dict[int, list[float]] = {}
    for day, paths in cohort_paths.items():
        start = day_index.get(day)
        if start is None or not paths:
            continue
        length = min(len(path) for path in paths)
        for offset in range(length):
            contributions.setdefault(start + 1 + offset, []).append(
                fmean(path[offset] for path in paths)
            )
    return contributions


# --------------------------------------------------------------------------
# 特征分箱诊断
# --------------------------------------------------------------------------


def analyze_feature_buckets(
    replay: DirectionReplay,
    *,
    horizon: int,
    numeric_features: Mapping[str, Sequence[float]] | None = None,
    categorical_features: Sequence[str] = (),
    boolean_gate_inputs: Sequence[str] = (),
    min_bucket_size: int = 20,
) -> dict[str, Any]:
    """按特征取值分箱，给出各箱的前瞻超额。

    这是给 v3 定权重与定分段效用的**实测依据**。此前 `_entry_structure_score` 里
    "回调 2~8% +12 分""离高点 2~8% +10 分""高位不加分" 这类数值都是先验，而 mainline 的
    market_structure 又同时在奖励"越靠近 20 日高点越好"——两者方向相反且都进入入场成熟度。
    只有把这些特征分箱看实际收益，才能决定该保留哪一个方向。

    ``numeric_features``: ``{特征名: 分箱右边界序列}``；``categorical_features``: 直接按
    取值分组；``boolean_gate_inputs``: 按 `entry_gate_inputs` 里的布尔值分 True/False。
    """
    demeaned = _demeaned_excess_index(replay)
    result: dict[str, Any] = {"horizon": horizon, "min_bucket_size": min_bucket_size}

    for feature, edges in (numeric_features or {}).items():
        buckets: dict[str, list[DirectionObservation]] = {}
        for item in replay.observations:
            value = _num(item.features.get(feature))
            if value is None:
                continue
            buckets.setdefault(_bucket_label(value, edges), []).append(item)
        result[feature] = _bucket_stats(
            buckets, horizon=horizon, demeaned=demeaned, min_bucket_size=min_bucket_size
        )

    for feature in categorical_features:
        buckets = {}
        for item in replay.observations:
            raw = item.features.get(feature)
            key = str(raw) if raw is not None else "缺失"
            buckets.setdefault(key, []).append(item)
        result[feature] = _bucket_stats(
            buckets, horizon=horizon, demeaned=demeaned, min_bucket_size=min_bucket_size
        )

    for feature in boolean_gate_inputs:
        buckets = {}
        for item in replay.observations:
            raw = item.gate_inputs.get(feature)
            key = "True" if raw else "False" if raw is not None else "缺失"
            buckets.setdefault(key, []).append(item)
        result[f"gate:{feature}"] = _bucket_stats(
            buckets, horizon=horizon, demeaned=demeaned, min_bucket_size=min_bucket_size
        )
    return result


def _bucket_label(value: float, edges: Sequence[float]) -> str:
    previous: float | None = None
    for edge in edges:
        if value < edge:
            return f"[{previous if previous is not None else '-inf'}, {edge})"
        previous = edge
    return f"[{previous}, +inf)"


def _bucket_stats(
    buckets: Mapping[str, Sequence[DirectionObservation]],
    *,
    horizon: int,
    demeaned: Mapping[tuple[str, int], float],
    min_bucket_size: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, observations in buckets.items():
        stats = _group_stats(
            observations,
            horizon=horizon,
            demeaned=demeaned,
            min_decision_days=3,
        )
        if not stats.get("available") or stats["observation_count"] < min_bucket_size:
            rows.append(
                {
                    "bucket": key,
                    "observation_count": stats.get("observation_count", 0),
                    "available": False,
                    "reason": "below_min_bucket_size",
                }
            )
            continue
        rows.append({"bucket": key, **stats})
    rows.sort(key=lambda row: str(row["bucket"]))
    return rows


# --------------------------------------------------------------------------
# Rank IC
# --------------------------------------------------------------------------


def compute_direction_factor_ic(
    replay: DirectionReplay,
    *,
    factors: Sequence[str] = FACTOR_KEYS,
    min_labels: int = MIN_LABELS_PER_DECISION_DAY,
    min_decision_days: int = MIN_DECISION_DAYS_FOR_SIGNIFICANCE,
) -> dict[str, Any]:
    """逐日横截面 Spearman Rank IC（因子 vs 前瞻超额），按持有期汇总 mean IC / ICIR / t。

    这是重定权重唯一站得住的依据：某个分量如果 IC 常年在 0 附近，给它 45% 权重就没有
    理由。单期 Rank IC 在 0.03~0.05 已属可用；明显更高通常意味着前视或样本污染。
    """
    by_date: dict[str, list[DirectionObservation]] = {}
    for item in replay.observations:
        by_date.setdefault(item.decision_date, []).append(item)

    result: dict[str, Any] = {
        "schema_version": replay.schema_version,
        "min_labels_per_day": min_labels,
        "horizons": list(replay.horizons),
        "factors": {},
        "caveats": list(replay.caveats),
    }
    for factor in factors:
        per_horizon: dict[str, Any] = {}
        for horizon in replay.horizons:
            series: list[float] = []
            for _day, rows in sorted(by_date.items()):
                pairs = [
                    (value, outcome.excess_percent)
                    for row in rows
                    if (value := row.factors.get(factor)) is not None
                    and (outcome := row.forward.get(horizon)) is not None
                    and outcome.excess_percent is not None
                ]
                if len(pairs) < min_labels:
                    continue
                ic = _spearman(
                    [pair[0] for pair in pairs],
                    [float(pair[1]) for pair in pairs],
                )
                if ic is not None:
                    series.append(ic)
            per_horizon[str(horizon)] = _ic_stats(
                series, min_decision_days=min_decision_days
            )
        result["factors"][factor] = per_horizon
    return result


def _ic_stats(series: Sequence[float], *, min_decision_days: int) -> dict[str, Any]:
    if not series:
        return {"n_periods": 0, "available": False, "reason": "no_valid_cross_sections"}
    mean_ic = fmean(series)
    std = pstdev(series) if len(series) > 1 else 0.0
    icir = mean_ic / std if std > 0 else None
    t_stat = icir * sqrt(len(series)) if icir is not None else None
    return {
        "available": True,
        "n_periods": len(series),
        "mean_ic": round(mean_ic, 4),
        "median_ic": round(median(series), 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 3) if icir is not None else None,
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "positive_ratio": round(sum(value > 0 for value in series) / len(series), 3),
        "significant": bool(
            len(series) >= min_decision_days
            and t_stat is not None
            and abs(t_stat) >= T_STAT_SIGNIFICANCE_THRESHOLD
        ),
    }


# --------------------------------------------------------------------------
# 入场线阈值敏感性
# --------------------------------------------------------------------------


#: 各 policy version 的阈值维度名与默认扫描网格。维度名必须与该 policy 自己的
#: threshold 键完全一致，否则 `classify_entry_state*` 会静默忽略传入的阈值，
#: 扫描出来的每一行都是同一组默认参数（曾经踩过这个坑）。
_THRESHOLD_GRIDS: dict[str, dict[str, tuple[float, ...]]] = {
    ENTRY_POLICY_VERSION_V2: {
        "direction": (45.0, 50.0, 55.0, 60.0, 65.0),
        "setup": (45.0, 50.0, 55.0, 60.0, 65.0),
        "entry": (55.0, 60.0, 65.0),
        "structure": (40.0, 50.0, 60.0),
    },
    ENTRY_POLICY_VERSION_V3: {
        "trend": (50.0, 55.0, 60.0, 65.0, 70.0),
        "participation": (35.0, 45.0, 55.0, 65.0),
        "position": (25.0, 35.0, 45.0, 55.0),
    },
}


def scan_entry_gate_thresholds(
    replay: DirectionReplay,
    *,
    horizon: int,
    grids: Mapping[str, Sequence[float]] | None = None,
    min_decision_days: int = MIN_DECISION_DAYS_FOR_SIGNIFICANCE,
    collapse_non_binding: bool = True,
) -> list[dict[str, Any]]:
    """对入场线阈值做同日原始门禁重扫，看 `ready_to_start` 桶如何变化。

    重扫复用生产的 `classify_entry_state`（同一份判定实现），只替换阈值；因此结果直接
    描述"把同日原始门禁改成这组会怎样"。V3 的连续达标与退出滞回依赖逐日状态路径，
    本网格没有为每组阈值重建整条状态路径，因此**不能**冒充完整线上状态机的参数回放。
    返回按去均值超额降序排列。

    ``collapse_non_binding``（默认开）把选出**完全相同样本集**的阈值组合合并成一行。
    四个阈值里通常只有一两个真正起约束作用，不合并的话结果里会出现大量数字一致的重复
    行，读起来像"多个独立最优点"。代表行保留**第一个被扫到的真实组合**（网格升序遍历，
    因此它是该等价类里最宽松的一个已验证组合）；这里刻意不去按维度取最小值合成一组
    "更宽松"的阈值——那样合成出来的组合可能从未被评估过，甚至会选出不同的样本集。
    ``equivalent_threshold_count`` 记录该等价类里共有多少组阈值。

    这是**研究输出**：网格搜索天生会过拟合，`decision_day_count` 小或 `significant`
    为 False 的行不能拿去改线上参数。
    """
    if horizon not in replay.horizons:
        raise ValueError(f"horizon={horizon} 不在重放结果的 {replay.horizons} 中")
    default_grids = _THRESHOLD_GRIDS.get(replay.entry_policy_version)
    if default_grids is None:
        raise ValueError(f"未知的 policy version {replay.entry_policy_version}")
    effective = {
        key: tuple(float(value) for value in (grids or {}).get(key, defaults))
        for key, defaults in default_grids.items()
    }
    unknown = set(grids or {}) - set(default_grids)
    if unknown:
        raise ValueError(
            f"{replay.entry_policy_version} 没有这些阈值维度：{sorted(unknown)}"
        )

    demeaned = _demeaned_excess_index(replay)
    collected: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    dimensions = list(effective)

    for combination in _threshold_combinations(effective, dimensions):
        thresholds = dict(combination)
        matched = [
            item
            for item in replay.observations
            if _restate(item, thresholds) == ENTRY_READY_TO_START
        ]
        if collapse_non_binding:
            identity = tuple(
                sorted((item.decision_date, item.sector_label) for item in matched)
            )
            existing = collected.get(identity)
            if existing is not None:
                existing["equivalent_threshold_count"] += 1
                continue
        stats = _group_stats(
            matched,
            horizon=horizon,
            demeaned=demeaned,
            min_decision_days=min_decision_days,
        )
        row = {
            "thresholds": thresholds,
            "horizon": horizon,
            "state_hysteresis_applied": False,
            "interpretation": "raw_same_day_entry_gate_sensitivity",
            "equivalent_threshold_count": 1,
            **stats,
        }
        results.append(row)
        if collapse_non_binding:
            collected[identity] = row
    results.sort(
        key=lambda row: (
            row.get("mean_demeaned_excess_percent")
            if row.get("available")
            else float("-inf")
        ),
        reverse=True,
    )
    return results


def _threshold_combinations(
    grids: Mapping[str, Sequence[float]],
    dimensions: Sequence[str],
) -> Iterable[tuple[tuple[str, float], ...]]:
    if not dimensions:
        yield ()
        return
    head, *rest = dimensions
    for value in grids[head]:
        for tail in _threshold_combinations(grids, rest):
            yield ((head, float(value)), *tail)


def _restate(item: DirectionObservation, thresholds: Mapping[str, float]) -> str:
    gate = item.gate_inputs
    if str(gate.get("policy_version") or "") == ENTRY_POLICY_VERSION_V3:
        return classify_entry_state_v3(
            evidence_quality=item.evidence_quality,
            mainline_status=item.mainline_status,
            trend_strength=item.factors.get("trend_strength_score") or 0.0,
            participation=item.factors.get("participation_score") or 0.0,
            position_risk=item.factors.get("position_risk_score") or 0.0,
            structure_broken=bool(gate.get("structure_broken")),
            thresholds=thresholds,
        )
    return classify_entry_state(
        evidence_quality=item.evidence_quality,
        mainline_status=item.mainline_status,
        direction_score=item.factors.get("direction_score") or 0.0,
        setup_score=item.factors.get("setup_maturity_score") or 0.0,
        entry_score=item.factors.get("entry_readiness_score") or 0.0,
        structure_score=item.factors.get("price_structure_score") or 0.0,
        flow_confirmed=bool(gate.get("flow_confirmed")),
        flow_broadly_weak=bool(gate.get("flow_broadly_weak")),
        flow_five_day_negative=bool(gate.get("flow_five_day_negative")),
        overheated=bool(gate.get("overheated")),
        position_label=str(gate.get("position_label") or ""),
        entry_thresholds=thresholds,
    )


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _clean_price_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按日期去重升序，只保留收盘价为正的行。"""
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        day = str(row.get("date") or "")[:10]
        close = _num(row.get("close"))
        if len(day) != 10 or close is None or close <= 0:
            continue
        by_date[day] = {**dict(row), "date": day, "close": close}
    return [by_date[day] for day in sorted(by_date)]


def _clean_flow_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        day = str(row.get("date") or "")[:10]
        value = _num(row.get("main_force_net_yi"))
        if len(day) != 10 or value is None:
            continue
        by_date[day] = {"date": day, "main_force_net_yi": value}
    return [by_date[day] for day in sorted(by_date)]


def _decision_calendar(
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    warmup_days: int,
    max_horizon: int,
    start_date: str | None,
    end_date: str | None,
    step: int,
) -> list[str]:
    """决策日 = 至少一个板块既有足够热身历史、又有足够未来数据可评价的交易日。"""
    eligible: set[str] = set()
    for rows in prices.values():
        last_index = len(rows) - 1 - max_horizon - 1
        for position in range(warmup_days - 1, last_index + 1):
            eligible.add(str(rows[position]["date"]))
    ordered = sorted(
        day
        for day in eligible
        if (start_date is None or day >= start_date) and (end_date is None or day <= end_date)
    )
    return ordered[::step]


def _window_return_percent(
    rows: Sequence[Mapping[str, Any]], cursor: int, span: int
) -> float | None:
    if cursor - span < 0:
        return None
    start = _num(rows[cursor - span].get("close"))
    end = _num(rows[cursor].get("close"))
    if start is None or start <= 0 or end is None or end <= 0:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def _lookup_breadth(
    breadth_by_label_date: Mapping[str, Mapping[str, float]] | None,
    label: str,
    decision_date: str,
) -> float | None:
    if not breadth_by_label_date:
        return None
    return _num((breadth_by_label_date.get(label) or {}).get(decision_date))


def _flow_context_as_of(
    label: str,
    series: Sequence[Mapping[str, Any]],
    *,
    decision_date: str,
    change_1d: float | None,
    flow_universe: str = "eastmoney_board",
) -> dict[str, Any] | None:
    """按 ``build_sector_fund_flow_context`` 的输出契约重建历史时点资金流上下文。

    字段名与语义必须与生产实现逐一对应（``available`` / ``date_aligned`` /
    ``today_available`` / ``five_day_available`` / ``pattern_label`` …），否则
    `mainline_regime._usable_flow` 与 `_compute_opportunity_row` 会按"资金不可用"处理，
    重放出来的分数就不再是线上口径。
    """
    points = [row for row in series if str(row["date"]) <= decision_date]
    if not points:
        return None
    latest = points[-1]
    flow_date = str(latest["date"])
    date_aligned = flow_date == decision_date
    today_flow = _num(latest.get("main_force_net_yi")) if date_aligned else None
    five_day_available = date_aligned and len(points) >= 5
    cumulative_5d = (
        round(sum(float(row["main_force_net_yi"]) for row in points[-5:]), 2)
        if five_day_available
        else None
    )
    cumulative_20d = round(sum(float(row["main_force_net_yi"]) for row in points[-20:]), 2)
    # 归一化与口径标注直接复用生产的 `_flow_scale_yi` / `_normalized_flow`，不另写一份，
    # 否则重放出来的资金分量就不再是线上口径。
    flow_scale = _flow_scale_yi([dict(row) for row in points])
    if date_aligned:
        pattern = _classify_flow_pattern(
            sector_return_percent=change_1d,
            today_flow=today_flow,
            cumulative_5d=cumulative_5d,
            flow_tiers=None,
        )
    else:
        pattern = {
            "pattern_label": "flow_date_mismatch",
            "pattern_hint": (
                f"板块资金流为 {flow_date} 数据，与决策日 {decision_date} 不同日。"
            ),
            "flow_structure_hint": None,
        }
    return {
        "available": True,
        "sector_label": label,
        "board_code": None,
        "trade_date": decision_date,
        "flow_date": flow_date,
        "date_aligned": date_aligned,
        "today_available": date_aligned,
        "five_day_available": five_day_available,
        "five_day_source": "history" if five_day_available else None,
        "history_point_count": len(points),
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": cumulative_5d,
        "cumulative_20d_net_yi": cumulative_20d,
        "flow_scale_yi": flow_scale,
        "flow_universe": flow_universe,
        "normalized_today_net": _normalized_flow(today_flow, flow_scale, 1),
        "normalized_5d_net": _normalized_flow(cumulative_5d, flow_scale, 5),
        "normalized_20d_net": _normalized_flow(cumulative_20d, flow_scale, 20),
        "flow_tiers": None,
        **pattern,
    }


def _forward_outcomes(
    *,
    rows: Sequence[Mapping[str, Any]],
    cursor: int,
    horizons: Sequence[int],
    benchmark_closes: Mapping[str, float],
    benchmark_dates: Sequence[str],
) -> dict[int, ForwardOutcome]:
    """建仓价 = D+1 收盘（D 收盘后才有结论，当天已不可成交）。"""
    entry_index = cursor + 1
    if entry_index >= len(rows):
        return {}
    entry_date = str(rows[entry_index]["date"])
    entry_close = float(rows[entry_index]["close"])
    entry_benchmark = _benchmark_close_on_or_before(
        benchmark_closes, benchmark_dates, entry_date
    )

    outcomes: dict[int, ForwardOutcome] = {}
    for horizon in horizons:
        exit_index = entry_index + horizon
        if exit_index >= len(rows):
            continue
        exit_date = str(rows[exit_index]["date"])
        exit_close = float(rows[exit_index]["close"])
        sector_return = (exit_close / entry_close - 1.0) * 100.0
        exit_benchmark = _benchmark_close_on_or_before(
            benchmark_closes, benchmark_dates, exit_date
        )
        benchmark_return: float | None = None
        excess: float | None = None
        mae: float | None = None
        daily_path: tuple[float, ...] = ()
        if entry_benchmark is not None and exit_benchmark is not None:
            benchmark_return = (exit_benchmark / entry_benchmark - 1.0) * 100.0
            excess = sector_return - benchmark_return
            mae, daily_path = _excess_path(
                rows=rows,
                entry_index=entry_index,
                exit_index=exit_index,
                entry_close=entry_close,
                entry_benchmark=entry_benchmark,
                benchmark_closes=benchmark_closes,
                benchmark_dates=benchmark_dates,
            )
        outcomes[horizon] = ForwardOutcome(
            horizon=horizon,
            entry_date=entry_date,
            exit_date=exit_date,
            sector_return_percent=round(sector_return, 3),
            benchmark_return_percent=(
                round(benchmark_return, 3) if benchmark_return is not None else None
            ),
            excess_percent=round(excess, 3) if excess is not None else None,
            max_adverse_excess_percent=round(mae, 3) if mae is not None else None,
            benchmark_calendar_aligned=bool(
                entry_date in benchmark_closes and exit_date in benchmark_closes
            ),
            daily_excess_path=daily_path,
        )
    return outcomes


def _excess_path(
    *,
    rows: Sequence[Mapping[str, Any]],
    entry_index: int,
    exit_index: int,
    entry_close: float,
    entry_benchmark: float,
    benchmark_closes: Mapping[str, float],
    benchmark_dates: Sequence[str],
) -> tuple[float | None, tuple[float, ...]]:
    """返回 (路径最大不利超额, 逐日单日超额序列)。

    最大不利超额衡量"拿得住吗"，而不只是终点收益；逐日序列供组合层指标使用。
    """
    worst: float | None = None
    daily: list[float] = []
    previous_close = entry_close
    previous_benchmark = entry_benchmark
    for index in range(entry_index + 1, exit_index + 1):
        day = str(rows[index]["date"])
        close = _num(rows[index].get("close"))
        if close is None or close <= 0:
            continue
        benchmark = _benchmark_close_on_or_before(benchmark_closes, benchmark_dates, day)
        if benchmark is None:
            continue
        cumulative = (close / entry_close - 1.0) * 100.0 - (
            benchmark / entry_benchmark - 1.0
        ) * 100.0
        if worst is None or cumulative < worst:
            worst = cumulative
        daily.append(
            (close / previous_close - 1.0) * 100.0
            - (benchmark / previous_benchmark - 1.0) * 100.0
        )
        previous_close = close
        previous_benchmark = benchmark
    return (min(worst, 0.0) if worst is not None else None), tuple(daily)


def _benchmark_close_on_or_before(
    benchmark_closes: Mapping[str, float],
    benchmark_dates: Sequence[str],
    target: str,
) -> float | None:
    """基准腿只向"不晚于目标日"的方向取值，绝不向未来外推。

    跨市场板块（港股走恒生系列）与沪深300 交易日历不同，精确同日常常取不到；此时取
    不晚于该日的最近一个基准交易日，并由 ``benchmark_calendar_aligned`` 标注该观测是
    近似对齐的，供统计侧计数而非静默吞掉。
    """
    direct = benchmark_closes.get(target)
    if direct is not None:
        return direct
    day = _latest_on_or_before(benchmark_dates, target)
    return benchmark_closes.get(day) if day else None


def _latest_on_or_before(dates: Sequence[str], target: str) -> str | None:
    candidate: str | None = None
    for day in dates:
        if day <= target:
            candidate = day
        else:
            break
    return candidate


def _extract_factors(
    row: Mapping[str, Any], mainline: Mapping[str, Any] | None
) -> dict[str, float | None]:
    components = (mainline or {}).get("component_scores")
    components = components if isinstance(components, Mapping) else {}
    factors: dict[str, float | None] = {
        key: _num(components.get(key)) for key in MAINLINE_COMPONENT_KEYS
    }
    for key in COMPOSITE_SCORE_KEYS:
        factors[key] = _num(row.get(key))
    return factors


def _extract_gate_inputs(row: Mapping[str, Any]) -> dict[str, Any]:
    gate = row.get("entry_gate_inputs")
    return dict(gate) if isinstance(gate, Mapping) else {}


def _extract_features(mainline: Mapping[str, Any] | None) -> dict[str, Any]:
    features = (mainline or {}).get("features")
    return dict(features) if isinstance(features, Mapping) else {}


def _feature_coverage(
    observations: Sequence[DirectionObservation],
) -> dict[str, float]:
    """每个因子有多少比例的观测真的拿到了值。缺口在结论里可见，而不是被静默补 0。"""
    if not observations:
        return {}
    total = len(observations)
    coverage = {
        key: round(
            sum(item.factors.get(key) is not None for item in observations) / total, 3
        )
        for key in FACTOR_KEYS
    }
    coverage["evidence_quality_complete"] = round(
        sum(item.evidence_quality == "complete" for item in observations) / total, 3
    )
    coverage["benchmark_calendar_aligned"] = round(
        sum(
            all(outcome.benchmark_calendar_aligned for outcome in item.forward.values())
            for item in observations
        )
        / total,
        3,
    )
    return coverage


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rank_x = _average_ranks(xs)
    rank_y = _average_ranks(ys)
    return _pearson(rank_x, rank_y)


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denominator = sqrt(sum(value * value for value in dx)) * sqrt(
        sum(value * value for value in dy)
    )
    if denominator <= 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denominator


#: 低于这个量级的收益差异视为浮点噪声，不做 t 检验。
#: 去均值口径下 `baseline_all_sectors` 每天的组内均值恒等于 0，但浮点误差会留下
#: ~1e-16 的残差；`mean / (std / sqrt(n))` 在两者同阶时会算出任意大的 t 值，从而给一个
#: 构造上恒为零的分组打上"显著"。收益以百分点计量，1e-9 个百分点没有任何经济含义。
_T_STAT_NOISE_FLOOR = 1e-9


def _t_stat(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    std = pstdev(values)
    mean = fmean(values)
    if std <= _T_STAT_NOISE_FLOOR or abs(mean) <= _T_STAT_NOISE_FLOOR:
        return None
    return mean / (std / sqrt(len(values)))


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


# --------------------------------------------------------------------------
# 数据装载层（默认走仓库既有 provider，全部可注入）
# --------------------------------------------------------------------------

#: 走恒生系列指数报价的板块。**单一来源**在 `sector_registry_data`，回测与生产共用
#: 同一份，避免两处各存一份靠注释维持同步。
from app.services.sector_registry_data import (  # noqa: E402
    HK_INDEX_SYMBOL_BY_SECTOR as _HK_INDEX_BY_SECTOR,
)

#: 港股类板块的对标基准。生产 `discovery_sector_position` 目前对所有板块（含港股）
#: 一律使用沪深300，导致港股的"相对强度"实际是跨市场跨货币比较；回测层显式支持按板块
#: 指定基准，正是为了量化这个口径缺陷的影响（`--hk-benchmark` 可开关）。
_HK_BENCHMARK_SYMBOL = "HSI"

BenchmarkKey = str


def load_direction_backtest_inputs(
    sector_labels: Sequence[str],
    *,
    trading_days: int = 400,
    fetch_price_series: Any = None,
    fetch_flow_series: Any = None,
    fetch_benchmark_series: Any = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """按板块拉取回测所需的三类序列。

    返回 ``{"price_series_by_label", "flow_series_by_label", "benchmark_by_label",
    "benchmark_series_by_key", "unavailable"}``。三个 fetch 均可注入，便于离线单测与
    在无外网环境下用落盘样本跑通链路。

    注意：本函数会发起网络请求（东财 / 新浪 / AkShare 子进程），**本仓库沙箱到东财
    `push2his` 的出站被阻断**，因此它只能在能访问上游的环境里真正取到数据。
    """
    price_fetch = fetch_price_series or _default_fetch_price_series
    flow_fetch = fetch_flow_series or _default_fetch_flow_series
    benchmark_fetch = fetch_benchmark_series or _default_fetch_benchmark_series

    labels = [str(label).strip() for label in sector_labels if str(label).strip()]
    labels = list(dict.fromkeys(labels))

    price_series_by_label: dict[str, list[dict[str, Any]]] = {}
    flow_series_by_label: dict[str, list[dict[str, Any]]] = {}
    unavailable: dict[str, str] = {}

    def load(label: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str | None]:
        try:
            prices = list(price_fetch(label, trading_days) or [])
        except Exception as exc:  # noqa: BLE001 - 离线研究，单板块失败不应中断全量
            return label, [], [], f"price_fetch_failed: {type(exc).__name__}"
        if len(prices) < DEFAULT_WARMUP_DAYS:
            return label, prices, [], f"insufficient_price_history: {len(prices)}"
        try:
            flows = list(flow_fetch(label) or [])
        except Exception:  # noqa: BLE001 - 资金流缺失只降低覆盖率，不淘汰板块
            flows = []
        return label, prices, flows, None

    with ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels) or 1)),
        thread_name_prefix="direction-backtest-load",
    ) as executor:
        for label, prices, flows, reason in executor.map(load, labels):
            if reason is not None:
                unavailable[label] = reason
                continue
            price_series_by_label[label] = prices
            if flows:
                flow_series_by_label[label] = flows

    benchmark_by_label = {
        label: (
            _HK_BENCHMARK_SYMBOL
            if label in _HK_INDEX_BY_SECTOR
            else "000300"
        )
        for label in price_series_by_label
    }
    benchmark_series_by_key: dict[BenchmarkKey, list[dict[str, Any]]] = {}
    for key in sorted(set(benchmark_by_label.values())):
        try:
            benchmark_series_by_key[key] = list(benchmark_fetch(key, trading_days) or [])
        except Exception:  # noqa: BLE001
            benchmark_series_by_key[key] = []

    return {
        "price_series_by_label": price_series_by_label,
        "flow_series_by_label": flow_series_by_label,
        "benchmark_by_label": benchmark_by_label,
        "benchmark_series_by_key": benchmark_series_by_key,
        "unavailable": unavailable,
        "trading_days_requested": trading_days,
    }


_FLOW_CACHE_PREFIX = "board-flow-hist:v2:"


def load_direction_backtest_inputs_from_flow_cache(
    sqlite_path: str,
    *,
    sector_labels: Sequence[str] | None = None,
    benchmark_series: Sequence[Mapping[str, Any]] | None = None,
    min_history_days: int = DEFAULT_WARMUP_DAYS,
) -> dict[str, Any]:
    """从项目自己的 ``sector_spot_cache`` 读取板块日资金流缓存作为回测输入（零网络）。

    ``board-flow-hist:v2:<BK码>`` 的每个点同时含 ``main_force_net_yi``、``close_price``
    与 ``change_percent``，也就是**价格与资金来自同一个东财 BK 板块**。这比生产链路的
    身份一致性更强：生产对同一个标签用中证主题指数取价格（如 半导体 → ``2.H30184``）、
    用东财 BK 板块取资金（半导体 → ``BK1036``），两者成分并不相同。用这份缓存回测可以
    把"口径不同源"这个变量先固定住，单独观察打分逻辑本身。

    代价是历史长度受缓存覆盖限制，且港股类板块没有 BK 码、完全不在这份数据里。
    ``unavailable`` 会逐条给出原因。未显式注入基准时，使用同一批缓存板块构造的
    日度等权指数作为横截面基准，保持本模式真正零网络；它不是沪深300，报告会以
    ``cached_equal_weight`` 明示。
    """
    import sqlite3

    from app.services.board_fund_flow_history import resolve_board_flow_code_for_sector

    if sector_labels is None:
        from app.services.sector_registry import list_theme_board_labels

        sector_labels = list(list_theme_board_labels())

    connection = sqlite3.connect(sqlite_path)
    try:
        cached: dict[str, list[dict[str, Any]]] = {}
        rows = connection.execute(
            "SELECT cache_key, payload FROM sector_spot_cache WHERE cache_key LIKE ?",
            (f"{_FLOW_CACHE_PREFIX}%",),
        ).fetchall()
    finally:
        connection.close()

    for cache_key, payload in rows:
        board_code = str(cache_key).rsplit(":", 1)[-1].strip().upper()
        try:
            parsed = _json_loads(payload)
        except ValueError:
            continue
        series = parsed.get("series") if isinstance(parsed, Mapping) else parsed
        if isinstance(series, list) and series:
            cached[board_code] = [row for row in series if isinstance(row, Mapping)]

    price_series_by_label: dict[str, list[dict[str, Any]]] = {}
    flow_series_by_label: dict[str, list[dict[str, Any]]] = {}
    unavailable: dict[str, str] = {}
    board_code_by_label: dict[str, str] = {}

    for raw_label in sector_labels:
        label = str(raw_label).strip()
        if not label:
            continue
        board_code, _resolved = resolve_board_flow_code_for_sector(label)
        if not board_code:
            unavailable[label] = "no_board_flow_code"
            continue
        series = cached.get(str(board_code).upper())
        if not series:
            unavailable[label] = f"not_in_flow_cache: {board_code}"
            continue
        prices = [
            {"date": str(row.get("date") or "")[:10], "close": close}
            for row in series
            if (close := _num(row.get("close_price"))) is not None and close > 0
        ]
        if len(prices) < min_history_days:
            unavailable[label] = f"insufficient_price_history: {len(prices)}"
            continue
        price_series_by_label[label] = prices
        flow_series_by_label[label] = [
            {
                "date": str(row.get("date") or "")[:10],
                "main_force_net_yi": value,
            }
            for row in series
            if (value := _num(row.get("main_force_net_yi"))) is not None
        ]
        board_code_by_label[label] = str(board_code).upper()

    if benchmark_series is None:
        benchmark_key = "cached_equal_weight"
        benchmark_series = _build_cached_equal_weight_benchmark(
            price_series_by_label
        )
    else:
        benchmark_key = "000300"

    return {
        "price_series_by_label": price_series_by_label,
        "flow_series_by_label": flow_series_by_label,
        "benchmark_by_label": {
            label: benchmark_key for label in price_series_by_label
        },
        "benchmark_series_by_key": {
            benchmark_key: list(benchmark_series or [])
        },
        "unavailable": unavailable,
        "board_code_by_label": board_code_by_label,
        "price_source": "eastmoney_board_fund_flow_daily_close",
    }


def _build_cached_equal_weight_benchmark(
    price_series_by_label: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Build a zero-network cross-sectional benchmark from the same cached boards."""
    returns_by_date: dict[str, list[float]] = {}
    for rows in price_series_by_label.values():
        cleaned = _clean_price_rows(rows)
        for previous, current in zip(cleaned, cleaned[1:]):
            previous_close = _num(previous.get("close"))
            current_close = _num(current.get("close"))
            if (
                previous_close is None
                or previous_close <= 0
                or current_close is None
                or current_close <= 0
            ):
                continue
            day = str(current.get("date") or "")
            returns_by_date.setdefault(day, []).append(
                current_close / previous_close - 1.0
            )

    level = 100.0
    result: list[dict[str, Any]] = []
    for day in sorted(returns_by_date):
        daily_returns = returns_by_date[day]
        if not daily_returns:
            continue
        level *= 1.0 + fmean(daily_returns)
        result.append({"date": day, "close": round(level, 8)})
    return result


def _json_loads(payload: object) -> Any:
    import json

    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        raise ValueError("unsupported payload type")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid cache payload") from exc


def _default_fetch_price_series(label: str, trading_days: int) -> list[dict[str, Any]]:
    """板块日线：港股类走恒生指数，其余走 canonical 日 K（与生产同源 provider）。"""
    symbol = _HK_INDEX_BY_SECTOR.get(label)
    if symbol:
        from app.services.akshare_subprocess import fetch_hk_index_daily_history

        payload = fetch_hk_index_daily_history(symbol, trading_days=trading_days) or {}
        return [dict(row) for row in payload.get("data") or [] if isinstance(row, dict)]

    from app.services.sector_canonical import get_canonical_sector
    from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series

    canon = get_canonical_sector(label)
    if canon is None:
        return []
    return [
        dict(bar)
        for bar in fetch_canonical_daily_kline_series(
            canon, max_days=trading_days, timeout=10.0
        )
    ]


def _default_fetch_flow_series(label: str) -> list[dict[str, Any]]:
    from app.services.board_fund_flow_history import (
        get_cached_board_flow_series,
        resolve_board_flow_code_for_sector,
    )

    board_code, _resolved = resolve_board_flow_code_for_sector(label)
    if not board_code:
        return []
    return [dict(row) for row in get_cached_board_flow_series(board_code)]


def _default_fetch_benchmark_series(key: str, trading_days: int) -> list[dict[str, Any]]:
    if key in {"HSI", "HSTECH"}:
        from app.services.akshare_subprocess import fetch_hk_index_daily_history

        payload = fetch_hk_index_daily_history(key, trading_days=trading_days) or {}
    else:
        from app.services.index_daily_client import fetch_index_daily_history

        payload = fetch_index_daily_history(key, trading_days=trading_days) or {}
    return [dict(row) for row in payload.get("data") or [] if isinstance(row, dict)]


def replay_with_per_label_benchmarks(
    *,
    price_series_by_label: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_series_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_by_label: Mapping[str, str],
    **kwargs: Any,
) -> dict[str, DirectionReplay]:
    """按基准分组分别重放，避免把跨市场板块塞进同一个横截面。

    横截面分位、去均值超额都只在**同一基准的板块组内**计算：把港股（恒生、港币、不同
    交易日历）与 A 股板块混进同一个分位池，本身就是当前生产实现的缺陷之一，回测层不
    应该复制它，否则无法把这一项的影响单独量化出来。
    """
    grouped: dict[str, list[str]] = {}
    for label in price_series_by_label:
        grouped.setdefault(str(benchmark_by_label.get(label) or "000300"), []).append(label)

    replays: dict[str, DirectionReplay] = {}
    for key, labels in sorted(grouped.items()):
        benchmark_rows = benchmark_series_by_key.get(key) or []
        if not benchmark_rows:
            continue
        replays[key] = replay_sector_direction(
            price_series_by_label={
                label: price_series_by_label[label] for label in labels
            },
            benchmark_series=benchmark_rows,
            benchmark_label=key,
            **kwargs,
        )
    return replays


__all__ = [
    "BASELINE_ALL_SECTORS",
    "BASELINE_TOP_CHANGE_1D",
    "DEFAULT_FORWARD_HORIZONS",
    "DEFAULT_WARMUP_DAYS",
    "DirectionObservation",
    "DirectionReplay",
    "FACTOR_KEYS",
    "ForwardOutcome",
    "GROUP_PRODUCTION_SELECTION",
    "SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION",
    "SkippedDay",
    "TRADING_DAYS_PER_YEAR",
    "analyze_feature_buckets",
    "compute_direction_factor_ic",
    "summarize_group_portfolio",
    "load_direction_backtest_inputs",
    "load_direction_backtest_inputs_from_flow_cache",
    "replay_sector_direction",
    "replay_with_per_label_benchmarks",
    "scan_entry_gate_thresholds",
    "summarize_direction_replay",
]
