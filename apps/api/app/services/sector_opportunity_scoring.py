from __future__ import annotations

"""通用板块方向机会打分（双轨：顺势 momentum / 蓄势 setup）。

原实现位于 discovery_sector_opportunity.py（荐基专用），2026-07 抽取为共享模块，
供日报（report_sector_opportunity.py）与荐基共用同一套打分口径，避免两条链路对
「同一板块当前是什么方向」给出不一致的结论。discovery_sector_opportunity.py 保留
为薄封装以维持向后兼容。

``sector_entry_maturity.2026-07.v2`` 在旧机会分之上增加三个彼此独立的判断：

* 方向潜力：20～60 个交易日的相对强度和趋势持续性；
* 形态成熟度：5/20 日资金、上涨广度和趋势是否共同改善；
* 入场成熟度：价格位置是否允许现在开始首批布局。

V2 只在完整 ``mainline_regime.v1`` 快照存在时启用。旧报告、日报单板块描述和
测试适配器仍保留旧字段语义，避免历史报告被新规则重新解释。
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from math import isfinite
from typing import Any

MOMENTUM_TRACK = "momentum"
SETUP_TRACK = "setup"

ENTRY_POLICY_VERSION = "sector_entry_maturity.2026-07.v2"
ENTRY_READY_TO_START = "ready_to_start"
ENTRY_READY_ON_PULLBACK = "ready_on_pullback"
ENTRY_FORMING = "forming"
ENTRY_INVALID = "invalid"

# 入场线阈值。抽成常量 + `classify_entry_state` 纯函数，是为了让离线方向回测
# （`sector_direction_backtest.py`）能用**完全同一份判定实现**做阈值敏感性扫描：
# 否则回测里必然出现一份 gate 的复制品，两边一旦漂移，回测结论就不再描述线上行为。
# 这些数值仍是 2026-07 的初始确定性策略，不是已证明的收益最优参数。
ENTRY_GATE_THRESHOLDS: dict[str, float] = {
    "direction": 55.0,
    "setup": 55.0,
    "entry": 60.0,
    "structure": 50.0,
}
PULLBACK_GATE_THRESHOLDS: dict[str, float] = {
    "direction": 55.0,
    "setup": 45.0,
}
_USABLE_EVIDENCE_QUALITIES = frozenset({"complete", "partial"})
_DIRECTIONAL_MAINLINE_STATUSES = frozenset({"forming", "confirmed"})
_DIRECTIONAL_MAINLINE_STATUSES_V3 = frozenset({"forming", "confirmed", "crowded"})

#: 两个方向的近 20 日日收益相关系数超过这个值时，视为同一笔风险暴露，只保留更强的一个。
MAX_DIRECTION_CORRELATION = 0.85
#: 少于这么多个共同交易日不做相关性判断（宁可不去重，也不用噪声去重）。
MIN_CORRELATION_SAMPLES = 15

# ---------------------------------------------------------------------------
# sector_entry_maturity.2026-08.v3
# ---------------------------------------------------------------------------
#
# v2 用三个"分数"（方向潜力 / 形态成熟 / 入场成熟）呈现给用户，但展开后它们高度共线：
# 趋势出现 2 次、市场结构出现 3 次，排序分实际只有约 1.5 个自由度，UI 上却像三重确认。
# 更严重的是 v2 的价格结构体系与 mainline 自己的 market_structure 方向相反（前者奖励
# "离 20 日高点 2~8%"，后者奖励"越贴近高点越好"），两者同时进入入场成熟度互相抵消。
#
# v3 做三件事，全部有离线实测依据（`sector_direction_backtest`，68 个 A 股板块 /
# 40 个决策日 / 2026-01~07）：
#
# 1. **三块正交**：每个原始分量只进入一次。
# 2. **按实测 Rank IC 比例定权**，不再手写：相对强度 .338 / 趋势持续 .328 /
#    资金 .064 / 市场结构 .066（T+5）。价格结构分实测 IC 为 -0.011 / +0.003 / -0.053，
#    ICIR -0.33 —— 它是无效甚至有害的，因此**整块删除**，不再有独立的价格结构评分。
# 3. **过热不再是硬排除**：实测 `overheated=True` 的方向前瞻超额为 +2.75%(t=+5.23,T+5)
#    / +5.31%(t=+3.99,T+20)，而 False 为 -0.49% / -0.95%。v2 把最强的正向信号当成了
#    淘汰条件。v3 把它降级为风险披露 + 首批仓位缩减。
#
# **刻意不做的事**：不把高涨幅、贴高点、远离 MA20 反转成加分项。上述观测来自单一
# 6 个月动量区间的 40 个决策日，在均值回归区间里符号会翻转。v3 只做"移除实测有害的
# 先验"，不做"押注相反的先验"——前者把未经验证的主观判断拿掉，后者只是换一个方向下注。
ENTRY_POLICY_VERSION_V3 = "sector_entry_maturity.2026-08.v3"
MATURITY_POLICY_VERSIONS = frozenset({ENTRY_POLICY_VERSION, ENTRY_POLICY_VERSION_V3})

#: 三个正交分块内部的权重。
V3_TREND_WEIGHTS: dict[str, float] = {
    "relative_strength": 0.55,
    "trend_persistence": 0.45,
}
V3_PARTICIPATION_WEIGHTS: dict[str, float] = {
    "fund_flow": 0.60,
    "breadth": 0.40,
}
#: 综合排序分的分块权重，按 T+5 实测 Rank IC 比例取整（0.338 : 0.064 : 0.066）。
V3_BLOCK_WEIGHTS: dict[str, float] = {
    "trend_strength": 0.70,
    "participation": 0.15,
    "position_risk": 0.15,
}
#: v3 入场线阈值。经 `scan_entry_gate_thresholds` 在 v3 分数上实测网格选取。
#:
#: 网格显示趋势阈值越高、去均值超额越高（trend=70 时 T+20 达 +10.19%），但那只是"最强的
#: 方向表现最好"这一 IC 事实的重述，且 n 会掉到 31（平均每天不到 1 个方向）。这里**刻意
#: 不取网格最大值**：取中段的 60，样本约每天 2 个方向，既保留区分度又不把参数钉在 40 个
#: 决策日的极值上。participation 在 35 与 55 之间实测几乎无差（+3.72 vs +3.78），取中间的
#: 45 表示"要求高于中性"。position 在网格里完全不起约束作用（最优行全部落在最低档），
#: 因此只作防止结构彻底破坏的下限，不假装它有区分力。
V3_GATE_THRESHOLDS: dict[str, float] = {
    "trend": 60.0,
    "participation": 45.0,
    "position": 25.0,
}
#: `invalid` 的判定阈值：趋势与参与度**同时**处于低位才算"不具备参与条件"。
#:
#: v2 用"5日与20日主力资金同时为负"作硬否决，实测把 91% 的观测打成 invalid，而该桶的
#: 前瞻超额与全市场平均完全相同（-0.06% vs 0）——它没有筛掉任何输家，只是拒绝了一切。
#: 根因是东财「主力净流入」对多数概念板块在多数交易日本身就是负的（大单净额在散户主导
#: 的板块里长期为负），拿它的绝对符号当门槛等于拿一个恒真条件当门槛。v3 改成横截面相对
#: 判断：资金分位已经内含在 participation 里。
V3_INVALID_TREND_CEILING = 40.0
V3_INVALID_PARTICIPATION_CEILING = 35.0
#: 过热标记数量 → 首批仓位缩放。过热方向不再被拒绝，但首批更小且不预先承诺后续。
V3_FIRST_TRANCHE_SCALE: dict[int, float] = {0: 1.0, 1: 0.6}
V3_FIRST_TRANCHE_SCALE_CROWDED = 0.4

_ENTRY_STATE_PRIORITY = {
    ENTRY_READY_TO_START: 4,
    ENTRY_READY_ON_PULLBACK: 3,
    ENTRY_FORMING: 2,
    ENTRY_INVALID: 1,
}
_EVIDENCE_QUALITY_PRIORITY = {"complete": 2, "partial": 1, "insufficient": 0}

_DISTRIBUTION_PATTERNS = {"distribution", "weak_outflow"}
_SETUP_PATTERNS = {"accumulation", "multi_day_outflow_then_inflow", "flow_turning_positive"}
_MOMENTUM_PATTERNS = {"price_flow_aligned_up", "aligned_up"}

_SECTOR_GROUPS = {
    "半导体": "tmt",
    "半导体材料": "tmt",
    "存储芯片": "tmt",
    "CPO": "tmt",
    "人工智能": "tmt",
    "机器人": "tmt",
    "港股": "hongkong",
    "港股通": "hongkong",
    "恒生科技": "hongkong",
    "创新药": "healthcare",
    "港股医药": "healthcare",
    "医药": "healthcare",
    "医疗器械": "healthcare",
    "白酒": "consumer",
    "消费电子": "consumer",
    "银行": "finance",
    "证券": "finance",
    "有色金属": "cyclical",
    "新能源车": "manufacturing",
    "光伏": "manufacturing",
    "电网设备": "manufacturing",
}

# 数据源里会同时出现“市场别名”和“投资方向名”。两条记录可能复用同一指数
# 价格序列，只是资金广度口径略有区别；把它们同时推荐给 C 端用户会造成一种
# 方向占掉两个名额，也会放大组合暴露。这里只合并确定等价的宽基方向，不合并
# 半导体/半导体材料等确有不同成分的细分行业。
_EQUIVALENT_DIRECTION_LABELS = {
    "港股通": "港股",
}


def select_sector_opportunities(
    sector_heat: list[dict],
    *,
    sector_flow_by_label: dict[str, dict] | None = None,
    sector_divergence_by_label: dict[str, dict] | None = None,
    mainline_by_label: dict[str, dict] | None = None,
    sector_position_by_label: Mapping[str, Mapping[str, Any]] | None = None,
    focus_sectors: list[str] | None = None,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> list[dict[str, Any]]:
    rows = score_sector_opportunity_rows(
        sector_heat,
        sector_flow_by_label=sector_flow_by_label,
        sector_divergence_by_label=sector_divergence_by_label,
        mainline_by_label=mainline_by_label,
        focus_sectors=focus_sectors,
        entry_policy_version=entry_policy_version,
    )
    return select_scored_sector_opportunities(
        rows,
        max_total=max_total,
        momentum_slots=momentum_slots,
        setup_slots=setup_slots,
        max_per_group=max_per_group,
        return_series_by_label=_return_series_from_positions(sector_position_by_label),
    )


def _return_series_from_positions(
    positions: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Sequence[float] | Mapping[str, float]]:
    result: dict[str, Sequence[float] | Mapping[str, float]] = {}
    for label, row in (positions or {}).items():
        if not isinstance(row, Mapping):
            continue
        dated = row.get("daily_returns_20d_by_date")
        if isinstance(dated, Mapping) and dated:
            values_by_date = {
                str(day): value
                for day, raw in dated.items()
                if (value := _num(raw)) is not None
            }
            if values_by_date:
                result[str(label)] = values_by_date
                continue
        series = row.get("daily_returns_20d")
        if isinstance(series, (list, tuple)) and series:
            values = [value for raw in series if (value := _num(raw)) is not None]
            if values:
                result[str(label)] = values
    return result


def score_sector_opportunity_rows(
    sector_heat: list[dict],
    *,
    sector_flow_by_label: dict[str, dict] | None = None,
    sector_divergence_by_label: dict[str, dict] | None = None,
    mainline_by_label: dict[str, dict] | None = None,
    focus_sectors: list[str] | None = None,
    drop_unavailable: bool = True,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> list[dict[str, Any]]:
    """对每个板块行打分，但**不做** slot / group / 去重选择。

    ``drop_unavailable=True``（默认，等于 `select_sector_opportunities` 的既有行为）
    丢弃 `entry_state == invalid` 或资金派发的行。离线方向回测传 ``False``：要验证
    「invalid 桶是否真的跑输」，就必须把被淘汰的方向也留在样本里，否则统计只覆盖
    模型自己认可的那部分，等于给结论开后门。

    ``entry_policy_version`` 允许显式回放旧口径（``sector_entry_maturity.2026-07.v2``），
    离线对比 v2/v3 时需要；线上默认 v3。
    """
    flow_by_label = sector_flow_by_label or {}
    divergence_by_label = sector_divergence_by_label or {}
    mainline_map = mainline_by_label or {}
    entry_policy_enabled = any(
        _supports_entry_maturity_v2(item) for item in mainline_map.values()
    )
    focus = {str(label).strip() for label in (focus_sectors or []) if str(label).strip()}
    scorer = _score_row if drop_unavailable else _describe_row
    scored = [
        scorer(
            row,
            flow_by_label.get(str(row.get("sector_label") or "").strip()),
            focus,
            divergence_backtest=divergence_by_label.get(str(row.get("sector_label") or "").strip()),
            mainline=mainline_map.get(str(row.get("sector_label") or "").strip()),
            entry_policy_enabled=entry_policy_enabled,
            entry_policy_version=entry_policy_version,
        )
        for row in sector_heat
    ]
    return [row for row in scored if row is not None]


def select_scored_sector_opportunities(
    rows: list[dict[str, Any]],
    *,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
    return_series_by_label: Mapping[
        str, Sequence[float] | Mapping[str, float]
    ] | None = None,
    max_correlation: float = MAX_DIRECTION_CORRELATION,
) -> list[dict[str, Any]]:
    """在已打分的行上执行排序、双轨名额与相关性去重（v2/v3 通用）。

    ``return_series_by_label`` 提供各板块近 20 日的日收益序列时，去重按**实测相关性**
    进行，而不是只靠手写的 `_SECTOR_GROUPS`。手写映射只覆盖 76 个白名单标签里的约 21 个，
    储能 / 锂电池 / 固态电池 / 锂矿 各自成组，完全可以同时输出 4 个高度相关的新能源方向，
    "分散"只是名义上的。手写映射保留为序列不可得时的兜底。
    """
    entry_policy_enabled = any(
        str(row.get("score_policy_version") or "") in MATURITY_POLICY_VERSIONS for row in rows
    )
    limiter = _CorrelationAwareLimiter(
        max_per_group=max_per_group,
        return_series_by_label=return_series_by_label or {},
        max_correlation=max_correlation,
    )

    if entry_policy_enabled:
        # 入场状态优先于分数：证据完整且可布局的方向必须排在热门但不可执行的
        # 方向之前；缺少 mainline 证据的方向不能再因为跳过混合评分而占便宜。
        ordered = sorted(rows, key=_entry_sort_score, reverse=True)
        return limiter.take(ordered, max_total, [])[:max_total]

    momentum = sorted(
        [row for row in rows if row["track"] == MOMENTUM_TRACK],
        key=_research_sort_score,
        reverse=True,
    )
    setup = sorted(
        [row for row in rows if row["track"] == SETUP_TRACK],
        key=_research_sort_score,
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    selected.extend(limiter.take(momentum, momentum_slots, selected))
    selected.extend(limiter.take(setup, setup_slots, selected))

    remaining = max_total - len(selected)
    if remaining > 0:
        selected_labels = {item["sector_label"] for item in selected}
        fallback = sorted(
            [row for row in rows if row["sector_label"] not in selected_labels],
            key=_research_sort_score,
            reverse=True,
        )
        selected.extend(limiter.take(fallback, remaining, selected))
    return selected[:max_total]


def build_sector_flow_map_for_opportunities(
    sector_heat: list[dict],
    sector_labels: list[str],
    *,
    trade_date: str | None = None,
    total_timeout_seconds: float = 6.0,
    max_workers: int = 5,
) -> dict[str, dict]:
    from app.services.sector_fund_flow_context import (
        build_sector_fund_flow_context,
        get_matching_theme_board_flow_snapshot,
    )

    heat_by_label = {
        str(row.get("sector_label") or "").strip(): row
        for row in sector_heat
        if str(row.get("sector_label") or "").strip()
    }
    labels = _unique_labels(sector_labels)
    if not labels:
        return {}

    snapshot_trade_date = trade_date
    if not snapshot_trade_date:
        from app.services.trading_session import get_effective_trade_date

        snapshot_trade_date = get_effective_trade_date()
    # Freeze one same-day theme snapshot for the whole opportunity pass. This
    # prevents sector workers from observing different refreshes and lets the
    # report reuse the exact flow facts that were used for opportunity scoring.
    shared_theme_snapshot = get_matching_theme_board_flow_snapshot(snapshot_trade_date)

    def load(label: str) -> tuple[str, dict | None]:
        heat = heat_by_label.get(label) or {}
        change_1d = _num(heat.get("change_1d_percent"))
        try:
            flow = build_sector_fund_flow_context(
                label,
                sector_return_percent=change_1d,
                trade_date=trade_date,
                theme_snapshot=shared_theme_snapshot,
            )
        except Exception:  # noqa: BLE001 - opportunity flow is best-effort
            return label, None
        return label, flow or None

    result: dict[str, dict] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="sector-opportunity-flow",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        done, pending = wait(futures, timeout=max(0.0, total_timeout_seconds))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                label, flow = future.result()
            except Exception:
                continue
            if flow:
                result[label] = flow
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def build_sector_divergence_map_for_opportunities(
    sector_labels: list[str],
    *,
    total_timeout_seconds: float = 6.0,
    max_workers: int = 4,
) -> dict[str, dict]:
    """并发跑量价背离回测（M1.3），供 `_confidence` 升级判定使用。

    比 `build_sector_flow_map_for_opportunities` 更重（涉及 K 线 + 完整资金流历史序列 +
    T→T+1 循环，而非单次资金流上下文查询），因此默认更低的 `max_workers`；结果本身有
    24h 缓存（见 `sector_flow_divergence_backtest.build_sector_flow_divergence_backtest`），
    该函数只是把「按需并发调用 + 总预算超时」这层封装起来，任一板块超时/失败都不影响其他
    板块，也不阻塞板块机会打分主流程（best-effort）。
    """
    from app.services.sector_flow_divergence_backtest import (
        build_sector_flow_divergence_backtest,
    )

    labels = _unique_labels(sector_labels)
    if not labels:
        return {}

    def load(label: str) -> tuple[str, dict | None]:
        try:
            result = build_sector_flow_divergence_backtest(label)
        except Exception:  # noqa: BLE001 - divergence backtest is best-effort
            return label, None
        return label, result if result and result.get("by_rule") else None

    result: dict[str, dict] = {}
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(labels))),
        thread_name_prefix="sector-opportunity-divergence",
    )
    futures = [executor.submit(load, label) for label in labels]
    try:
        done, pending = wait(futures, timeout=max(0.0, total_timeout_seconds))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                label, divergence = future.result()
            except Exception:
                continue
            if divergence:
                result[label] = divergence
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result


def describe_sector_opportunity(
    row: dict,
    flow: dict | None,
    *,
    focus: set[str] | None = None,
    divergence_backtest: dict | None = None,
) -> dict[str, Any] | None:
    """给单个板块的方向判断，即使该板块暂不构成「机会」也会返回结果。

    与 `select_sector_opportunities` 不同，这里不做 slot/max_per_group 限制、也不会因为
    分数不够或资金背离而整行丢弃——供「本来就持有该板块」的场景使用（日报），需要对已持有
    的方向给出判断，而不是只挑「值得关注的新方向」（荐基）。返回的 `opportunity_available`
    标注该板块当前是否构成一个值得加仓的机会；为 False 时仅作方向参考，不应作为加仓依据。

    `divergence_backtest`（M1.4 新增）：该板块的量价背离历史回测结果（见
    `sector_flow_divergence_backtest.build_sector_flow_divergence_backtest`），传入时若
    证据极强（distribution 规则 significant=True 且 edge_percent>=10）confidence 可升至
    「高」；不传入时行为与此前完全一致（confidence 上限仍为「中」）。
    """
    return _compute_opportunity_row(row, flow, focus or set(), divergence_backtest)


def _score_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    *,
    divergence_backtest: dict | None = None,
    mainline: dict | None = None,
    entry_policy_enabled: bool = False,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> dict[str, Any] | None:
    result = _compute_opportunity_row(
        row,
        flow,
        focus,
        divergence_backtest,
        mainline=mainline,
        entry_policy_enabled=entry_policy_enabled,
        entry_policy_version=entry_policy_version,
    )
    if result is None or not result["opportunity_available"]:
        return None
    return {key: value for key, value in result.items() if key != "opportunity_available"}


def _describe_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    *,
    divergence_backtest: dict | None = None,
    mainline: dict | None = None,
    entry_policy_enabled: bool = False,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> dict[str, Any] | None:
    """与 `_score_row` 同一份打分，但保留 `opportunity_available=False` 的行。"""
    return _compute_opportunity_row(
        row,
        flow,
        focus,
        divergence_backtest,
        mainline=mainline,
        entry_policy_enabled=entry_policy_enabled,
        entry_policy_version=entry_policy_version,
    )


def _compute_opportunity_row(
    row: dict,
    flow: dict | None,
    focus: set[str],
    divergence_backtest: dict | None = None,
    *,
    mainline: dict | None = None,
    entry_policy_enabled: bool = False,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> dict[str, Any] | None:
    label = str(row.get("sector_label") or "").strip()
    if not label:
        return None
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    heat_score = _num(row.get("heat_score")) or 0.0
    flow = flow or {}
    pattern = str(flow.get("pattern_label") or "").strip()
    date_aligned = flow.get("date_aligned") is not False
    # 资金流日期与涨跌幅日期不对齐（或资金流本身不可用）时，today_main_force_net_yi /
    # cumulative_5d_net_yi 实际上不代表「今日」资金流，不能再被当作当日证据参与打分
    # 或写进 evidence/返回字段——否则会出现下游文案一边写"资金日期需核验"、一边又
    # 言之凿凿地给出"今日主力净流入 XX 亿"这种自相矛盾的展示（真实回归案例：
    # 2026-07-03 日报把好几天前的旧资金流数字当成当日数据喂给用户/LLM）。
    flow_available = bool(flow.get("available")) and date_aligned
    raw_today_flow = _num(flow.get("today_main_force_net_yi"))
    raw_flow_5d = _num(flow.get("cumulative_5d_net_yi"))
    today_declared_available = (
        bool(flow.get("today_available"))
        if "today_available" in flow
        else raw_today_flow is not None
    )
    five_day_declared_available = (
        bool(flow.get("five_day_available"))
        if "five_day_available" in flow
        else raw_flow_5d is not None
    )
    today_available = flow_available and today_declared_available and raw_today_flow is not None
    five_day_available = (
        flow_available and five_day_declared_available and raw_flow_5d is not None
    )
    today_flow = raw_today_flow if today_available else None
    flow_5d = raw_flow_5d if five_day_available else None
    history_point_count = flow.get("history_point_count")
    five_day_source = str(flow.get("five_day_source") or "").strip() or None

    penalties: list[str] = []
    evidence: list[str] = []
    if pattern in _DISTRIBUTION_PATTERNS:
        penalties.append("资金背离或持续流出")
    if flow and not date_aligned:
        penalties.append("资金流日期未对齐")
    if change_1d is not None and change_1d >= 4.0:
        penalties.append("单日涨幅过热")

    focus_bonus = 6.0 if label in focus else 0.0
    flow_bonus = _positive_score(today_flow, scale=2.0, cap=12.0) + _positive_score(
        flow_5d,
        scale=1.0,
        cap=12.0,
    )
    if today_flow is not None and today_flow > 0:
        evidence.append("今日主力净流入")
    if flow_5d is not None and flow_5d > 0:
        evidence.append("5日主力净流入")

    momentum_score = (
        max(change_1d or 0.0, 0.0) * 5.0
        + max(change_5d or 0.0, 0.0) * 4.0
        + flow_bonus
        + heat_score * 0.15
        + focus_bonus
    )
    if pattern in _MOMENTUM_PATTERNS:
        momentum_score += 10.0
        evidence.append("价涨资金配合")
    if change_1d is not None and change_1d >= 4.0:
        momentum_score -= 12.0
    if pattern in _DISTRIBUTION_PATTERNS:
        momentum_score -= 30.0

    setup_score = (
        _setup_price_score(change_1d, change_5d)
        + flow_bonus * 1.15
        + heat_score * 0.08
        + focus_bonus
    )
    if pattern in _SETUP_PATTERNS:
        setup_score += 14.0
        evidence.append("资金拐点或吸筹形态")
    if pattern in _DISTRIBUTION_PATTERNS:
        setup_score -= 28.0

    disqualified = (
        pattern in _DISTRIBUTION_PATTERNS and (today_flow or 0.0) <= 0 and (flow_5d or 0.0) <= 0
    ) or max(momentum_score, setup_score) <= 0

    track = MOMENTUM_TRACK if momentum_score >= setup_score else SETUP_TRACK
    legacy_score = round(max(momentum_score, setup_score), 2)
    mainline_score = _num((mainline or {}).get("score"))
    mainline_status = str((mainline or {}).get("status") or "").strip()
    research_score = legacy_score
    if mainline_score is not None and mainline_status != "insufficient":
        research_score = round(
            min(max(legacy_score, 0.0), 100.0) * 0.55
            + min(max(mainline_score, 0.0), 100.0) * 0.45,
            2,
        )
    result = {
        "sector_label": label,
        "track": track,
        "score": legacy_score,
        "research_score": research_score,
        "mainline_regime": dict(mainline) if isinstance(mainline, dict) else None,
        "confidence": (
            "不足"
            if disqualified
            else _confidence(flow, date_aligned, penalties, divergence_backtest)
        ),
        "entry_hint": _entry_hint(track, change_1d, change_5d, penalties),
        "evidence": _unique_evidence(evidence)[:5],
        "penalties": penalties[:5],
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "today_available": today_available,
        "five_day_available": five_day_available,
        "five_day_source": five_day_source,
        "history_point_count": history_point_count,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": flow_5d,
        "pattern_label": pattern or None,
        "sector_group": _sector_group(label),
        "opportunity_available": not disqualified,
    }
    if entry_policy_enabled:
        scorer = (
            _entry_maturity_v3
            if entry_policy_version == ENTRY_POLICY_VERSION_V3
            else _entry_maturity_v2
        )
        maturity = scorer(
            label=label,
            track=track,
            legacy_score=legacy_score,
            change_1d=change_1d,
            change_5d=change_5d,
            today_flow=today_flow,
            flow_5d=flow_5d,
            pattern=pattern,
            date_aligned=date_aligned,
            mainline=mainline,
        )
        result.update(maturity)
        result["mainline_regime"] = dict(mainline) if isinstance(mainline, dict) else None
        result["opportunity_available"] = maturity["entry_state"] != ENTRY_INVALID
        result["confidence"] = maturity["confidence"]
        result["entry_hint"] = maturity["entry_hint"]
        result["evidence"] = _unique_evidence(
            [*maturity["evidence"], *evidence]
        )[:6]
        result["penalties"] = _unique_evidence(
            [*maturity["penalties"], *penalties]
        )[:6]
    return result


def _supports_entry_maturity_v2(mainline: object) -> bool:
    if not isinstance(mainline, dict):
        return False
    return bool(
        str(mainline.get("schema_version") or "").startswith("mainline_regime.")
        or mainline.get("feature_coverage") is not None
        or isinstance(mainline.get("component_scores"), dict)
    )


def classify_entry_state_v3(
    *,
    evidence_quality: str,
    mainline_status: str,
    trend_strength: float,
    participation: float,
    position_risk: float,
    structure_broken: bool,
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """v3 状态机：过热不再阻止布局，`invalid` 改为横截面双弱判定。

    与 v2 的三处实质差异：

    * **过热不进入门禁**。它在 `_entry_maturity_v3` 里只影响 `first_tranche_scale`
      与风险提示。实测过热方向的前瞻超额显著为正，把它当淘汰条件是把最强信号丢掉。
    * **`invalid` 不再用资金绝对符号判定**。v2 只要命中单日 distribution/weak_outflow，
      或 5/20 日资金同时为负，就判无效；实测 91% 的观测被打成 `invalid`，而该桶的表现
      与全市场平均完全一致——等于拒绝一切却没筛掉任何输家。v3 要求趋势与参与度**同时**
      处于横截面低位。
    * `ready_on_pullback` 的含义改为"趋势仍强，但资金参与度或价格位置尚不支持立即入场"，
      不再由"过热"触发。
    """
    gate = {**V3_GATE_THRESHOLDS, **(thresholds or {})}
    doubly_weak = bool(
        trend_strength < V3_INVALID_TREND_CEILING
        and participation < V3_INVALID_PARTICIPATION_CEILING
    )
    if doubly_weak or mainline_status == "fading" or structure_broken:
        return ENTRY_INVALID
    if evidence_quality not in _USABLE_EVIDENCE_QUALITIES:
        return ENTRY_FORMING
    if trend_strength < gate["trend"]:
        return ENTRY_FORMING
    if (
        mainline_status in _DIRECTIONAL_MAINLINE_STATUSES_V3
        and participation >= gate["participation"]
        and position_risk >= gate["position"]
    ):
        return ENTRY_READY_TO_START
    return ENTRY_READY_ON_PULLBACK


def classify_entry_state(
    *,
    evidence_quality: str,
    mainline_status: str,
    direction_score: float,
    setup_score: float,
    entry_score: float,
    structure_score: float,
    flow_confirmed: bool,
    flow_broadly_weak: bool,
    overheated: bool,
    flow_five_day_negative: bool = False,
    position_label: str = "",
    entry_thresholds: Mapping[str, float] | None = None,
    pullback_thresholds: Mapping[str, float] | None = None,
) -> str:
    """把已算好的分数与布尔证据映射成唯一入场状态。

    纯函数、无 IO，阈值可覆盖。生产链路用默认阈值调用它；离线方向回测用同一函数配
    不同 ``entry_thresholds`` 做敏感性扫描，从而保证「回测里评估的门禁」和「线上执行
    的门禁」是同一段代码，而不是两份会各自漂移的实现。
    """
    entry_gate = {**ENTRY_GATE_THRESHOLDS, **(entry_thresholds or {})}
    pullback_gate = {**PULLBACK_GATE_THRESHOLDS, **(pullback_thresholds or {})}

    hard_invalid = bool(
        flow_broadly_weak
        or mainline_status == "fading"
        or position_label == "weak_breakdown"
    )
    if hard_invalid:
        return ENTRY_INVALID
    usable_evidence = evidence_quality in _USABLE_EVIDENCE_QUALITIES
    if (
        usable_evidence
        and mainline_status in _DIRECTIONAL_MAINLINE_STATUSES
        and direction_score >= entry_gate["direction"]
        and setup_score >= entry_gate["setup"]
        and entry_score >= entry_gate["entry"]
        and structure_score >= entry_gate["structure"]
        and flow_confirmed
        and not overheated
    ):
        return ENTRY_READY_TO_START
    if (
        usable_evidence
        and direction_score >= pullback_gate["direction"]
        and setup_score >= pullback_gate["setup"]
        and not flow_broadly_weak
        # 此前只要求 not flow_broadly_weak（需要 5 日与 20 日**同时**转负）。于是
        # "近5日主力净流出、20日还没转负"的板块可以挂着「等待合适位置」进 UI，而这个
        # 标签对用户宣称的是「方向较强，等待过热缓解」——钱正在走的时候这句话是假的。
        and not flow_five_day_negative
        and overheated
    ):
        return ENTRY_READY_ON_PULLBACK
    return ENTRY_FORMING


def _entry_maturity_v2(
    *,
    label: str,
    track: str,
    legacy_score: float,
    change_1d: float | None,
    change_5d: float | None,
    today_flow: float | None,
    flow_5d: float | None,
    pattern: str,
    date_aligned: bool,
    mainline: dict | None,
) -> dict[str, Any]:
    """Turn research ranking evidence into one explicit entry state.

    The score is deliberately bounded and missing evidence is penalised.  This
    is the opposite of the old available-weight re-normalisation behaviour,
    where a direction with only one strong component could display a very high
    score and avoid the mainline blend entirely.
    """

    mainline_map = mainline if isinstance(mainline, dict) else {}
    components = (
        mainline_map.get("component_scores")
        if isinstance(mainline_map.get("component_scores"), dict)
        else {}
    )
    features = (
        mainline_map.get("features")
        if isinstance(mainline_map.get("features"), dict)
        else {}
    )
    status = str(mainline_map.get("status") or "insufficient").strip() or "insufficient"
    coverage = _clamp(_num(mainline_map.get("feature_coverage")) or 0.0, 0.0, 1.0)
    flow_20d = _num(features.get("cumulative_20d_net_yi"))
    distance_high = _num(features.get("distance_from_20d_high_percent"))
    distance_ma20 = _num(features.get("distance_from_ma20_percent"))
    return_5d = _num(features.get("return_5d_percent"))
    if return_5d is None:
        return_5d = change_5d
    position_label = str(features.get("position_label") or "").strip()

    evidence_quality = (
        "complete"
        if status != "insufficient" and coverage >= 0.80 and date_aligned
        else "partial"
        if status != "insufficient" and coverage >= 0.65 and date_aligned
        else "insufficient"
    )

    relative_score = _num(components.get("relative_strength"))
    trend_score = _num(components.get("trend_persistence"))
    structure_component = _num(components.get("market_structure"))
    flow_component = _num(components.get("fund_flow"))
    breadth_score = _num(components.get("breadth"))

    direction_score, direction_component_coverage = _weighted_neutral_fill_score(
        (
            (relative_score, 0.45),
            (trend_score, 0.40),
            (structure_component, 0.15),
        )
    )
    if direction_score is None or evidence_quality == "insufficient":
        # The fallback intentionally ignores most of the one-day jump.  It can
        # retain a research lead but can never make an entry-ready direction.
        c5 = _clamp(change_5d or 0.0, -8.0, 8.0)
        overheat = max((change_1d or 0.0) - 3.0, 0.0)
        direction_score = _clamp(35.0 + c5 * 1.5 - overheat * 4.0, 0.0, 45.0)
    else:
        direction_score += {
            "confirmed": 6.0,
            "forming": 2.0,
            "crowded": -8.0,
            "fading": -20.0,
            "neutral": -5.0,
        }.get(status, 0.0)
        direction_score = _clamp(direction_score, 0.0, 100.0)

    setup_score, setup_component_coverage = _weighted_neutral_fill_score(
        (
            (flow_component, 0.50),
            (breadth_score, 0.25),
            (trend_score, 0.15),
            (structure_component, 0.10),
        )
    )
    if setup_score is None:
        setup_score = 35.0
        if today_flow is not None and today_flow > 0:
            setup_score += 5.0
        if flow_5d is not None and flow_5d > 0:
            setup_score += 10.0
    if pattern in _SETUP_PATTERNS:
        setup_score += 10.0
    elif pattern in _MOMENTUM_PATTERNS and (flow_5d is None or flow_5d >= 0):
        setup_score += 6.0
    if flow_5d is not None and flow_5d < 0:
        setup_score -= 18.0
    if flow_20d is not None and flow_20d < 0:
        setup_score -= 12.0
    if pattern in _DISTRIBUTION_PATTERNS:
        setup_score -= 30.0
    if evidence_quality == "insufficient":
        setup_score = min(setup_score, 52.0)
    setup_score = _clamp(setup_score, 0.0, 100.0)

    structure_score = _entry_structure_score(
        base_score=structure_component,
        position_label=position_label,
        change_1d=change_1d,
        return_5d=return_5d,
        distance_high=distance_high,
        distance_ma20=distance_ma20,
    )
    entry_score = _clamp(
        direction_score * 0.35 + setup_score * 0.40 + structure_score * 0.25,
        0.0,
        100.0,
    )
    if evidence_quality == "insufficient":
        entry_score = min(entry_score, 49.0)

    flow_confirmed = bool(
        (flow_5d is not None and flow_5d > 0)
        or (
            pattern in _SETUP_PATTERNS
            and today_flow is not None
            and today_flow > 0
            and (flow_20d is None or flow_20d >= 0)
        )
    )
    flow_broadly_weak = bool(
        pattern in _DISTRIBUTION_PATTERNS
        or (flow_5d is not None and flow_5d < 0 and flow_20d is not None and flow_20d < 0)
    )
    flow_five_day_negative = bool(flow_5d is not None and flow_5d < 0)
    overheated = bool(
        (change_1d is not None and change_1d >= 4.0)
        or (return_5d is not None and return_5d >= 12.0)
        or status == "crowded"
        or (
            position_label == "high_extended"
            and (
                (return_5d is not None and return_5d >= 6.0)
                or (change_1d is not None and change_1d >= 3.0)
            )
        )
    )
    entry_state = classify_entry_state(
        evidence_quality=evidence_quality,
        mainline_status=status,
        direction_score=direction_score,
        setup_score=setup_score,
        entry_score=entry_score,
        structure_score=structure_score,
        flow_confirmed=flow_confirmed,
        flow_broadly_weak=flow_broadly_weak,
        flow_five_day_negative=flow_five_day_negative,
        overheated=overheated,
        position_label=position_label,
    )

    opportunity_score = _clamp(
        direction_score * 0.45 + setup_score * 0.35 + entry_score * 0.20,
        0.0,
        100.0,
    )
    research_score = _clamp(
        opportunity_score
        + {ENTRY_READY_TO_START: 8.0, ENTRY_READY_ON_PULLBACK: 3.0}.get(entry_state, 0.0),
        0.0,
        100.0,
    )
    confidence = (
        "高"
        if entry_state == ENTRY_READY_TO_START
        and evidence_quality == "complete"
        and coverage >= 0.85
        and status == "confirmed"
        and flow_5d is not None
        and flow_5d > 0
        and flow_20d is not None
        and flow_20d > 0
        else "中"
        if evidence_quality in {"complete", "partial"}
        else "低"
    )
    entry_hint = {
        ENTRY_READY_TO_START: "条件成熟，可小额首批布局",
        ENTRY_READY_ON_PULLBACK: "方向较强，等待过热缓解",
        ENTRY_FORMING: "条件形成中，暂不下单",
        ENTRY_INVALID: "趋势或资金未通过，暂不参与",
    }[entry_state]
    entry_reason = {
        ENTRY_READY_TO_START: "中期方向、资金确认和价格位置已同时通过入场线。",
        ENTRY_READY_ON_PULLBACK: "中期方向仍有优势，但当前价格位置偏热，不适合立即追入。",
        ENTRY_FORMING: "方向或资金已有苗头，但多周期证据尚未同时成熟。",
        ENTRY_INVALID: "资金持续转弱、趋势退潮或价格结构破坏，当前不具备布局条件。",
    }[entry_state]

    triggers = _entry_triggers(
        entry_state=entry_state,
        status=status,
        evidence_quality=evidence_quality,
        change_1d=change_1d,
        flow_5d=flow_5d,
        direction_score=direction_score,
        distance_high=distance_high,
    )
    invalidation_signals = _invalidation_signals(
        entry_state=entry_state,
        flow_5d=flow_5d,
        distance_ma20=distance_ma20,
    )
    evidence = [
        f"方向潜力 {direction_score:.1f} 分",
        f"形态成熟度 {setup_score:.1f} 分",
        f"入场成熟度 {entry_score:.1f} 分",
    ]
    penalties: list[str] = []
    if evidence_quality == "insufficient":
        penalties.append("20日价格结构或多维证据不足")
    if flow_5d is not None and flow_5d < 0:
        penalties.append("近5日主力资金净流出")
    if flow_20d is not None and flow_20d < 0:
        penalties.append("近20日主力资金净流出")
    if overheated:
        penalties.append("当前价格位置偏热")

    return {
        "score_policy_version": ENTRY_POLICY_VERSION,
        "legacy_score": legacy_score,
        "score": round(opportunity_score, 2),
        "research_score": round(research_score, 2),
        "direction_score": round(direction_score, 2),
        "setup_maturity_score": round(setup_score, 2),
        "entry_readiness_score": round(entry_score, 2),
        # 价格结构分占入场成熟度 25%，此前完全不出现在返回值里：入场成熟 = 87 分时
        # 无法判断它是被方向撑起来的还是被价格位置撑起来的。离线回测的阈值重扫也需要它。
        "price_structure_score": round(structure_score, 2),
        # 两个合成分数各自的实际可得权重占比。此前"哪个分量缺了"完全不可观测，
        # 而缺失又会被重归一化悄悄放大剩余分量的话语权。
        "component_coverage": {
            "direction": round(direction_component_coverage, 2),
            "setup": round(setup_component_coverage, 2),
        },
        "entry_gate_inputs": {
            "flow_confirmed": flow_confirmed,
            "flow_broadly_weak": flow_broadly_weak,
            "flow_five_day_negative": flow_five_day_negative,
            "overheated": overheated,
            "mainline_status": status,
            "position_label": position_label or None,
        },
        "data_coverage": round(coverage, 2),
        "evidence_quality": evidence_quality,
        "entry_state": entry_state,
        "entry_reason": entry_reason,
        "entry_triggers": triggers,
        "invalidation_signals": invalidation_signals,
        "execution_eligible": entry_state == ENTRY_READY_TO_START,
        "automatic_promotion_allowed": entry_state == ENTRY_READY_TO_START,
        "confidence": confidence,
        "entry_hint": entry_hint,
        "evidence": evidence,
        "penalties": penalties,
        "sector_label": label,
        "track": track,
    }


def _entry_maturity_v3(
    *,
    label: str,
    track: str,
    legacy_score: float,
    change_1d: float | None,
    change_5d: float | None,
    today_flow: float | None,
    flow_5d: float | None,
    pattern: str,
    date_aligned: bool,
    mainline: dict | None,
) -> dict[str, Any]:
    """三块正交、按实测 IC 定权、过热只披露不拦截。"""
    mainline_map = mainline if isinstance(mainline, dict) else {}
    components = (
        mainline_map.get("component_scores")
        if isinstance(mainline_map.get("component_scores"), dict)
        else {}
    )
    features = (
        mainline_map.get("features") if isinstance(mainline_map.get("features"), dict) else {}
    )
    status = str(mainline_map.get("status") or "insufficient").strip() or "insufficient"
    coverage = _clamp(_num(mainline_map.get("feature_coverage")) or 0.0, 0.0, 1.0)
    flow_20d = _num(features.get("cumulative_20d_net_yi"))
    distance_high = _num(features.get("distance_from_20d_high_percent"))
    distance_ma20 = _num(features.get("distance_from_ma20_percent"))
    return_5d = _num(features.get("return_5d_percent"))
    if return_5d is None:
        return_5d = change_5d
    position_label = str(features.get("position_label") or "").strip()

    evidence_quality = (
        "complete"
        if status != "insufficient" and coverage >= 0.80 and date_aligned
        else "partial"
        if status != "insufficient" and coverage >= 0.65 and date_aligned
        else "insufficient"
    )

    trend_strength, trend_coverage = _weighted_neutral_fill_score(
        (
            (_num(components.get("relative_strength")), V3_TREND_WEIGHTS["relative_strength"]),
            (_num(components.get("trend_persistence")), V3_TREND_WEIGHTS["trend_persistence"]),
        )
    )
    participation, participation_coverage = _weighted_neutral_fill_score(
        (
            (_num(components.get("fund_flow")), V3_PARTICIPATION_WEIGHTS["fund_flow"]),
            (_num(components.get("breadth")), V3_PARTICIPATION_WEIGHTS["breadth"]),
        )
    )
    position_component = _num(components.get("market_structure"))
    # 位置风险直接用 mainline 的 market_structure，不再叠加任何 pullback / base_building /
    # 贴高点惩罚。v2 那一套与 market_structure 本身方向相反，且实测 IC 为负。
    position_risk = position_component
    position_coverage = 1.0 if position_component is not None else 0.0

    if trend_strength is None or evidence_quality == "insufficient":
        # 证据不足时只保留一个受限的研究分，永远无法通过入场线。
        c5 = _clamp(change_5d or 0.0, -8.0, 8.0)
        trend_strength = _clamp(35.0 + c5 * 1.5, 0.0, 45.0)
        trend_coverage = 0.0
    if participation is None:
        participation = NEUTRAL_COMPONENT_SCORE
        participation_coverage = 0.0
    if position_risk is None:
        position_risk = NEUTRAL_COMPONENT_SCORE
        position_coverage = 0.0

    trend_strength = _clamp(trend_strength, 0.0, 100.0)
    participation = _clamp(participation, 0.0, 100.0)
    position_risk = _clamp(position_risk, 0.0, 100.0)
    direction_score = _clamp(
        trend_strength * V3_BLOCK_WEIGHTS["trend_strength"]
        + participation * V3_BLOCK_WEIGHTS["participation"]
        + position_risk * V3_BLOCK_WEIGHTS["position_risk"],
        0.0,
        100.0,
    )

    flow_persistently_weak = bool(
        flow_5d is not None and flow_5d < 0 and flow_20d is not None and flow_20d < 0
    )
    structure_broken = bool(
        position_label == "weak_breakdown"
        and distance_ma20 is not None
        and distance_ma20 < -4.0
    )
    overheat_flags = _overheat_flags(
        change_1d=change_1d,
        return_5d=return_5d,
        status=status,
        distance_high=distance_high,
    )
    entry_state = classify_entry_state_v3(
        evidence_quality=evidence_quality,
        mainline_status=status,
        trend_strength=trend_strength,
        participation=participation,
        position_risk=position_risk,
        structure_broken=structure_broken,
    )
    first_tranche_scale = (
        V3_FIRST_TRANCHE_SCALE_CROWDED
        if status == "crowded" or len(overheat_flags) >= 2
        else V3_FIRST_TRANCHE_SCALE.get(len(overheat_flags), V3_FIRST_TRANCHE_SCALE_CROWDED)
    )

    research_score = _clamp(
        direction_score
        + {ENTRY_READY_TO_START: 6.0, ENTRY_READY_ON_PULLBACK: 2.0}.get(entry_state, 0.0),
        0.0,
        100.0,
    )
    confidence = (
        "高"
        if entry_state == ENTRY_READY_TO_START
        and evidence_quality == "complete"
        and coverage >= 0.85
        and status == "confirmed"
        and not overheat_flags
        else "中"
        if evidence_quality in {"complete", "partial"}
        else "低"
    )
    entry_hint = {
        ENTRY_READY_TO_START: (
            "条件成熟，可小额首批布局" if not overheat_flags else "条件成熟但短期加速，首批更小"
        ),
        ENTRY_READY_ON_PULLBACK: "方向仍强，资金或结构尚不支持立即入场",
        ENTRY_FORMING: "条件形成中，暂不下单",
        ENTRY_INVALID: "资金持续转弱或趋势退潮，暂不参与",
    }[entry_state]
    entry_reason = {
        ENTRY_READY_TO_START: "中期趋势、市场参与度与价格位置已同时通过入场线。",
        ENTRY_READY_ON_PULLBACK: "中期趋势仍有优势，但资金参与度或价格位置尚未同时达标。",
        ENTRY_FORMING: "趋势强度或多周期证据尚未成熟。",
        ENTRY_INVALID: "趋势强度与资金参与度同时处于横截面低位，或主线退潮、价格结构破坏。",
    }[entry_state]

    penalties: list[str] = []
    if evidence_quality == "insufficient":
        penalties.append("20日价格结构或多维证据不足")
    if flow_5d is not None and flow_5d < 0:
        penalties.append("近5日主力资金净流出")
    if flow_20d is not None and flow_20d < 0:
        penalties.append("近20日主力资金净流出")
    penalties.extend(overheat_flags)
    if pattern in _DISTRIBUTION_PATTERNS:
        # 单日量价背离降级为风险提示：实测它在 5~20 日尺度上几乎没有预测力，
        # 但按 v2 的做法它会让 91% 的方向被判 invalid。
        penalties.append("当日量价背离，仅作风险提示")

    return {
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "legacy_score": legacy_score,
        "score": round(direction_score, 2),
        "research_score": round(research_score, 2),
        "direction_score": round(direction_score, 2),
        "trend_strength_score": round(trend_strength, 2),
        "participation_score": round(participation, 2),
        "position_risk_score": round(position_risk, 2),
        "block_weights": dict(V3_BLOCK_WEIGHTS),
        "component_coverage": {
            "trend": round(trend_coverage, 2),
            "participation": round(participation_coverage, 2),
            "position": round(position_coverage, 2),
        },
        "overheat_flags": overheat_flags,
        "first_tranche_scale": first_tranche_scale,
        "entry_gate_inputs": {
            "policy_version": ENTRY_POLICY_VERSION_V3,
            # 保留为可观测的风险事实；它不再参与 invalid 判定（见 classify_entry_state_v3）。
            "flow_persistently_weak": flow_persistently_weak,
            "structure_broken": structure_broken,
            "overheated": bool(overheat_flags),
            "mainline_status": status,
            "position_label": position_label or None,
        },
        "data_coverage": round(coverage, 2),
        "evidence_quality": evidence_quality,
        "entry_state": entry_state,
        "entry_reason": entry_reason,
        "entry_triggers": _entry_triggers_v3(
            entry_state=entry_state,
            status=status,
            evidence_quality=evidence_quality,
            trend_strength=trend_strength,
            participation=participation,
            position_risk=position_risk,
            overheat_flags=overheat_flags,
        ),
        "invalidation_signals": _invalidation_signals_v3(entry_state=entry_state),
        "execution_eligible": entry_state == ENTRY_READY_TO_START,
        "automatic_promotion_allowed": entry_state == ENTRY_READY_TO_START,
        "confidence": confidence,
        "entry_hint": entry_hint,
        "evidence": [
            f"趋势强度 {trend_strength:.1f} 分（权重 {V3_BLOCK_WEIGHTS['trend_strength']:.0%}）",
            f"资金参与度 {participation:.1f} 分（权重 {V3_BLOCK_WEIGHTS['participation']:.0%}）",
            f"价格位置 {position_risk:.1f} 分（权重 {V3_BLOCK_WEIGHTS['position_risk']:.0%}）",
        ],
        "penalties": penalties,
        "sector_label": label,
        "track": track,
    }


def _overheat_flags(
    *,
    change_1d: float | None,
    return_5d: float | None,
    status: str,
    distance_high: float | None,
) -> list[str]:
    """短期加速/拥挤的**风险披露**，不参与打分也不参与门禁。

    阈值沿用 v2，刻意不按实测结果调整：实测显示这些条件在样本区间里是正向信号，但那
    来自单一动量区间，反过来押注同样是未经验证的下注。这里只是把"拦截"改成"说明 +
    首批更小"。
    """
    flags: list[str] = []
    if change_1d is not None and change_1d >= 4.0:
        flags.append("单日涨幅超过4%，短期加速")
    if return_5d is not None and return_5d >= 12.0:
        flags.append("近5日涨幅超过12%，短期加速")
    if status == "crowded":
        flags.append("主线处于拥挤阶段")
    if (
        distance_high is not None
        and distance_high >= -1.5
        and return_5d is not None
        and return_5d >= 6.0
    ):
        flags.append("贴近20日高位且短期涨幅较大")
    return flags


def _entry_triggers_v3(
    *,
    entry_state: str,
    status: str,
    evidence_quality: str,
    trend_strength: float,
    participation: float,
    position_risk: float,
    overheat_flags: list[str],
) -> list[str]:
    if entry_state == ENTRY_READY_TO_START:
        triggers = ["首批后继续确认趋势强度与资金参与度，不预先承诺后续加仓"]
        if overheat_flags:
            triggers.append("当前处于短期加速，首批按更低比例执行")
        return triggers
    triggers: list[str] = []
    if evidence_quality == "insufficient":
        triggers.append("补齐20日价格结构与多维证据")
    if trend_strength < V3_GATE_THRESHOLDS["trend"]:
        triggers.append("20日相对强度与趋势持续性继续改善")
    if status not in _DIRECTIONAL_MAINLINE_STATUSES_V3:
        triggers.append("主线状态升至形成中、已确认或拥挤")
    if participation < V3_GATE_THRESHOLDS["participation"]:
        triggers.append("主力资金与上涨广度转为改善")
    if position_risk < V3_GATE_THRESHOLDS["position"]:
        triggers.append("价格结构修复（回撤收敛、重新靠近阶段高点）")
    return _unique_evidence(triggers)[:4]


def _invalidation_signals_v3(*, entry_state: str) -> list[str]:
    values = [
        "趋势强度与资金参与度同时跌入横截面低位",
        "主线状态转为退潮",
        "价格跌破20日均线且相对强度同步转弱",
    ]
    if entry_state == ENTRY_READY_ON_PULLBACK:
        values.append("等待过程中趋势强度跌破入场线")
    return values[:3]


def _entry_structure_score(
    *,
    base_score: float | None,
    position_label: str,
    change_1d: float | None,
    return_5d: float | None,
    distance_high: float | None,
    distance_ma20: float | None,
) -> float:
    score = base_score if base_score is not None else 45.0
    score += {
        "pullback_acceptance": 12.0,
        "base_building": 10.0,
        "early_breakout": 8.0,
        # 距离 20 日高点不足 2% 只代表“接近高位”，并不等于价格已经过热。
        # 是否需要等待回调应由短期涨速、拥挤度共同决定，否则稳定沿趋势
        # 运行的方向会被永久挡在入场线外。
        "high_extended": 0.0,
        "weak_breakdown": -35.0,
    }.get(position_label, 0.0)
    if change_1d is not None:
        if change_1d >= 7.0:
            score -= 35.0
        elif change_1d >= 4.0:
            score -= 20.0
        elif change_1d >= 3.0:
            score -= 10.0
    if return_5d is not None and return_5d >= 12.0:
        score -= 15.0
    if distance_high is not None:
        if -8.0 <= distance_high <= -2.0:
            score += 10.0
        elif (
            distance_high >= -1.5
            and position_label != "early_breakout"
            and (
                (change_1d is not None and change_1d >= 3.0)
                or (return_5d is not None and return_5d >= 6.0)
            )
        ):
            score -= 10.0
    if distance_ma20 is not None:
        if -1.0 <= distance_ma20 <= 6.0:
            score += 5.0
        elif distance_ma20 < -4.0:
            score -= 15.0
    return _clamp(score, 0.0, 100.0)


def _entry_triggers(
    *,
    entry_state: str,
    status: str,
    evidence_quality: str,
    change_1d: float | None,
    flow_5d: float | None,
    direction_score: float,
    distance_high: float | None,
) -> list[str]:
    triggers: list[str] = []
    if entry_state == ENTRY_READY_TO_START:
        return ["首批后继续确认5日资金与20日相对强度，不预先承诺后续加仓"]
    if evidence_quality == "insufficient":
        triggers.append("补齐20日价格结构与多维证据")
    if status not in {"forming", "confirmed"}:
        triggers.append("主线状态升至形成中或已确认")
    if flow_5d is None or flow_5d <= 0:
        triggers.append("近5日主力资金转为净流入")
    if direction_score < 55.0:
        triggers.append("20日相对强度与趋势继续改善")
    if change_1d is not None and change_1d >= 4.0:
        triggers.append("单日涨幅回落至3%以内")
    if distance_high is not None and distance_high >= -1.5:
        triggers.append("价格离开阶段极端高位并出现承接")
    return _unique_evidence(triggers)[:4]


def _invalidation_signals(
    *,
    entry_state: str,
    flow_5d: float | None,
    distance_ma20: float | None,
) -> list[str]:
    values = ["主线状态转为退潮或资金高位派发"]
    if flow_5d is None or flow_5d >= 0:
        values.append("近5日主力资金转为持续净流出")
    if distance_ma20 is None or distance_ma20 >= -4.0:
        values.append("价格跌破20日均线且相对强度同步转弱")
    if entry_state == ENTRY_READY_ON_PULLBACK:
        values.append("回调过程中资金继续流出而非缩量承接")
    return values[:3]


#: 缺失分量按"中性"计入，既不加分也不减分。
NEUTRAL_COMPONENT_SCORE = 50.0


def _weighted_neutral_fill_score(
    values: tuple[tuple[float | None, float], ...],
    *,
    neutral: float = NEUTRAL_COMPONENT_SCORE,
) -> tuple[float | None, float]:
    """跨分量加权，**缺失分量按中性值计入其原有权重**，返回 (分数, 可得权重占比)。

    这修掉了一处代码与自身注释、与 `docs/PROJECT_CONTEXT.md`「缺失证据不重分权重」
    同时相矛盾的行为：原实现 `_weighted_available_score` 做的正是可得权重重归一化，
    于是只有一个强分量的方向能拿到很高的分数。真实后果很具体——指数型板块拿不到
    上涨广度时，形态成熟度里资金的权重被从 0.50 放大到 0.50/0.75≈0.667，而覆盖率上
    完全看不出来（mainline 覆盖率仍是 0.90，证据等级仍是 complete）。

    中性填充同时自带一个正确的上限：可得权重占比 c 时，分数最高只能到
    ``neutral + (100 - neutral) * c``，不需要再额外写封顶规则。
    """
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None, 0.0
    available_weight = sum(weight for value, weight in values if value is not None)
    if available_weight <= 0:
        return None, 0.0
    score = (
        sum(
            (float(value) if value is not None else neutral) * weight
            for value, weight in values
        )
        / total_weight
    )
    return score, available_weight / total_weight


def _entry_sort_score(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(_ENTRY_STATE_PRIORITY.get(str(row.get("entry_state") or ""), 0)),
        float(_EVIDENCE_QUALITY_PRIORITY.get(str(row.get("evidence_quality") or ""), 0)),
        _num(row.get("research_score")) or 0.0,
        _num(row.get("entry_readiness_score")) or 0.0,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def _research_sort_score(row: dict[str, Any]) -> tuple[float, float]:
    return (
        _num(row.get("research_score")) or _num(row.get("score")) or 0.0,
        _num(row.get("score")) or 0.0,
    )


class _CorrelationAwareLimiter:
    """名额分配：标签去重 → 手写分组上限 → 实测相关性上限。"""

    def __init__(
        self,
        *,
        max_per_group: int,
        return_series_by_label: Mapping[
            str, Sequence[float] | Mapping[str, float]
        ],
        max_correlation: float,
    ) -> None:
        self._max_per_group = max_per_group
        self._series: dict[
            str, Sequence[float] | Mapping[str, float]
        ] = {}
        for label, series in return_series_by_label.items():
            if series is None:
                continue
            if isinstance(series, Mapping):
                normalized: Sequence[float] | Mapping[str, float] = {
                    str(day): float(value) for day, value in series.items()
                }
            else:
                normalized = [float(value) for value in series]
            if len(normalized) >= MIN_CORRELATION_SAMPLES:
                self._series[str(label)] = normalized
        self._max_correlation = max_correlation

    def take(
        self,
        rows: list[dict[str, Any]],
        limit: int,
        already_selected: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        picked: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        selected_labels: set[str] = set()
        selected_identities: set[str] = set()
        for item in already_selected:
            group = str(item.get("sector_group") or item.get("sector_label"))
            counts[group] = counts.get(group, 0) + 1
            selected_label = str(item["sector_label"])
            selected_labels.add(selected_label)
            selected_identities.add(_direction_identity(selected_label))
        for row in rows:
            if len(picked) >= limit:
                break
            label = str(row["sector_label"])
            identity = _direction_identity(label)
            if label in selected_labels or identity in selected_identities:
                continue
            group = str(row.get("sector_group") or label)
            if counts.get(group, 0) >= self._max_per_group:
                continue
            if self._too_correlated(label, selected_labels):
                continue
            picked.append(row)
            selected_labels.add(label)
            selected_identities.add(identity)
            counts[group] = counts.get(group, 0) + 1
        return picked

    def _too_correlated(self, label: str, selected_labels: set[str]) -> bool:
        series = self._series.get(label)
        if series is None:
            return False
        for other in selected_labels:
            other_series = self._series.get(other)
            if other_series is None:
                continue
            correlation = _pearson_correlation(series, other_series)
            if correlation is not None and correlation >= self._max_correlation:
                return True
        return False


def _pearson_correlation(
    left: Sequence[float] | Mapping[str, float],
    right: Sequence[float] | Mapping[str, float],
) -> float | None:
    """有日期时只用共同交易日；旧数组输入保持尾部对齐兼容。"""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        common_days = sorted(set(left) & set(right))
        if len(common_days) < MIN_CORRELATION_SAMPLES:
            return None
        xs = [float(left[day]) for day in common_days]
        ys = [float(right[day]) for day in common_days]
    elif isinstance(left, Mapping) or isinstance(right, Mapping):
        # 一条有日期、一条没有时无法证明点位一一对应，宁可不去重。
        return None
    else:
        size = min(len(left), len(right))
        if size < MIN_CORRELATION_SAMPLES:
            return None
        xs = list(left)[-size:]
        ys = list(right)[-size:]
    size = len(xs)
    if size < MIN_CORRELATION_SAMPLES:
        return None
    mean_x = sum(xs) / size
    mean_y = sum(ys) / size
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denominator = (
        sum(value * value for value in dx) ** 0.5
        * sum(value * value for value in dy) ** 0.5
    )
    if denominator <= 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denominator


def _direction_identity(label: str) -> str:
    normalized = str(label or "").strip()
    return _EQUIVALENT_DIRECTION_LABELS.get(normalized, normalized)


def _entry_hint(
    track: str,
    change_1d: float | None,
    change_5d: float | None,
    penalties: list[str],
) -> str:
    if "资金背离或持续流出" in penalties:
        return "资金背离，暂不入池"
    if change_1d is not None and change_1d >= 4.0:
        return "高位谨慎"
    if track == MOMENTUM_TRACK and change_1d is not None and change_1d < 0 and (change_5d or 0) > 0:
        return "回调承接观察"
    if track == SETUP_TRACK:
        return "蓄势观察"
    return "可分批关注"


_DIVERGENCE_EDGE_HIGH_THRESHOLD = 10.0


def _confidence(
    flow: dict,
    date_aligned: bool,
    penalties: list[str],
    divergence_backtest: dict | None = None,
) -> str:
    """板块方向置信度。

    M1.4 修复：此前该函数只有「低」（数据不可用/未对齐）与「中」（其余全部情况）两档，
    机制上就把"高"档位堵死了——无论证据多强都封顶在"中"，prompt 规则要求"中"只能措辞
    保留、不能作主理由，导致"果断"在架构层面不可能发生。现在当量价背离历史回测
    （`sector_flow_divergence_backtest.py`，M1.3）证据极强时允许升到"高"：
    证据强度决定档位，而不是机制性封顶。
    """
    if not flow or not flow.get("available"):
        return "低"
    if not date_aligned:
        return "低"
    if _divergence_evidence_is_strong(divergence_backtest, penalties):
        return "高"
    return "中"


def _divergence_evidence_is_strong(divergence_backtest: dict | None, penalties: list[str]) -> bool:
    if not divergence_backtest:
        return False
    by_rule = divergence_backtest.get("by_rule")
    if not isinstance(by_rule, dict):
        return False
    # 「资金背离或持续流出」命中时（distribution 模式），用 distribution 规则的历史回测
    # 证据判定；否则（当前方向偏多头）用 accumulation 规则。两者结构一致（均来自
    # signal_backtest_stats.finalize_bucket），只是预测方向相反。
    rule_id = (
        "flow_price_distribution"
        if "资金背离或持续流出" in penalties
        else "flow_price_accumulation"
    )
    bucket = by_rule.get(rule_id)
    if not isinstance(bucket, dict):
        return False
    edge = bucket.get("edge_percent")
    return bool(bucket.get("significant")) and edge is not None and float(edge) >= _DIVERGENCE_EDGE_HIGH_THRESHOLD


def _setup_price_score(change_1d: float | None, change_5d: float | None) -> float:
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    score = 0.0
    if -2.5 <= c1 <= 1.5:
        score += 8.0
    if -4.0 <= c5 <= 2.0:
        score += 8.0
    if c1 > 3.0 or c5 > 6.0:
        score -= 12.0
    return score


def _positive_score(value: float | None, *, scale: float, cap: float) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(cap, value / scale)


def _sector_group(label: str) -> str:
    return _SECTOR_GROUPS.get(label, label)


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _unique_labels(labels: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _unique_evidence(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
