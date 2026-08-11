"""方向层预算必须随持仓板块数伸缩，且超时不得丢弃已算完的方向层。

回归背景（2026-08-11 线上实测）：

价格结构（20 日日线 + 相对强度）是**逐板块联网**取数，
`build_sector_position_map_for_opportunities` 内部 `max_workers=4`。而预算写死 8 s，等于
隐含了"持仓板块数 ≤ 4"这个前提。当天两个账号的对照非常干净：

  - 持 4 个板块的账号，四次日报全部正常；
  - 持 6 个板块的账号（黄金/煤炭/数字经济/半导体材料/稀土/医疗），14:30 那次整层超时
    （`sector_rotation.reason=timeout`），6 只持仓的方向证据全部缺席。

而超时的兜底是 `held={}`——把**已经算完**的持仓方向层一起丢掉。轮动参考与分位分母排在
方向层之后，它们慢一点就足以让整层归零，而 `held` 正是数据门禁、动作提议与退出判定唯一
依赖的那一份。

两条契约：
1. 预算按 `ceil(板块数 / 并发数)` 波数伸缩，外层超时始终覆盖内层总预算；
2. 方向层一算完就发布到 `progress`，外层超时时取用它而不是归零。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import Holding
from app.services import analysis_facts as facts_mod
from app.services import report_sector_opportunity as sector_ctx


# --- 契约 1：预算伸缩与外层覆盖 ---------------------------------------------


@pytest.mark.parametrize(
    ("held_sector_count", "expected_position_budget"),
    [
        # 并发开到板块数（上限 8），所以 8 个以内都是一波：8 s 一波 + 8 s 缺口补齐。
        (0, 16.0),
        (1, 16.0),
        (4, 16.0),
        (6, 16.0),  # 线上出事的那个账号
        (8, 16.0),
        (9, 24.0),  # 超过并发上限才需要第二波
        (16, 24.0),
        (17, 32.0),
        (40, 48.0),  # 不封顶：宁可慢，也要把每个持仓板块的方向层拿到
    ],
)
def test_position_budget_scales_by_wave(
    held_sector_count: int,
    expected_position_budget: float,
) -> None:
    assert (
        sector_ctx.sector_position_budget_seconds(held_sector_count)
        == expected_position_budget
    )


def test_budget_covers_one_wave_plus_one_retry_round() -> None:
    """预算构成必须由常量派生：一波墙钟 + 一轮缺口补齐。

    这里刻意**不再**断言"与历史值一致"。持仓方向层缺一个板块，那只基金当天就没有
    `entry_state`，方向层退化成旧版机会分、退出判定一并失效；因此产品口径已明确选择
    "宁可牺牲速度也要取全"，默认预算从 8 s 抬到 16 s 是有意为之，不是回归。
    """
    assert sector_ctx.sector_position_budget_seconds() == (
        sector_ctx._SECTOR_POSITION_WAVE_SECONDS + sector_ctx._SECTOR_POSITION_RETRY_SECONDS
    )
    assert sector_ctx.SECTOR_POSITION_BUDGET_SECONDS == (
        sector_ctx.sector_position_budget_seconds()
    )
    assert sector_ctx.SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS == (
        sector_ctx.sector_opportunity_total_budget_seconds()
    )
    assert facts_mod.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS == (
        facts_mod.sector_opportunity_timeout_seconds()
    )


def test_budget_is_a_cap_not_a_spend(monkeypatch) -> None:
    """首轮就取全时不得多花那 8 s 补齐预算——快乐路径的耗时不能被这次改动拖长。"""
    calls: list[list[str]] = []

    def _one_shot(labels, *, as_of_trade_date=None, total_timeout_seconds=None, max_workers=None):
        calls.append(list(labels))
        return {label: {"label": label} for label in labels}

    monkeypatch.setattr(
        "app.services.discovery_sector_position.build_sector_position_map_for_opportunities",
        _one_shot,
    )
    labels = ["黄金", "煤炭", "数字经济", "半导体材料", "稀土", "医疗"]
    result = sector_ctx._fetch_sector_position_map(labels, "2026-08-11", None, budget_seconds=16.0)

    assert sorted(result) == sorted(labels)
    assert len(calls) == 1  # 没有第二轮


def test_missing_labels_are_retried_with_the_remaining_budget(monkeypatch) -> None:
    """单个板块的瞬时失败不该让它一整天没有方向层。"""
    calls: list[list[str]] = []

    def _flaky(labels, *, as_of_trade_date=None, total_timeout_seconds=None, max_workers=None):
        calls.append(list(labels))
        if len(calls) == 1:
            # 首轮只回来一半
            return {label: {"label": label} for label in list(labels)[:3]}
        return {label: {"label": label} for label in labels}

    monkeypatch.setattr(
        "app.services.discovery_sector_position.build_sector_position_map_for_opportunities",
        _flaky,
    )
    labels = ["黄金", "煤炭", "数字经济", "半导体材料", "稀土", "医疗"]
    result = sector_ctx._fetch_sector_position_map(labels, "2026-08-11", None, budget_seconds=16.0)

    assert sorted(result) == sorted(labels)
    assert len(calls) == 2
    # 第二轮只重试缺失的那些，不重复已拿到的。
    assert sorted(calls[1]) == sorted(labels[3:])


def test_retry_is_bounded_to_one_extra_round(monkeypatch) -> None:
    """数据源持续失败时重试只是把同一个错误多犯几次，必须有上限。"""
    calls: list[list[str]] = []

    def _always_partial(labels, *, as_of_trade_date=None, total_timeout_seconds=None, max_workers=None):
        calls.append(list(labels))
        return {}

    monkeypatch.setattr(
        "app.services.discovery_sector_position.build_sector_position_map_for_opportunities",
        _always_partial,
    )
    result = sector_ctx._fetch_sector_position_map(
        ["黄金", "煤炭"], "2026-08-11", None, budget_seconds=16.0
    )

    assert result == {}
    assert len(calls) == 2


def test_concurrency_is_opened_to_the_held_sector_count(monkeypatch) -> None:
    """一波跑完靠的是并发开到板块数，而不是把预算拉长。"""
    seen: dict[str, int] = {}

    def _spy(labels, *, as_of_trade_date=None, total_timeout_seconds=None, max_workers=None):
        seen["max_workers"] = int(max_workers)
        return {label: {"label": label} for label in labels}

    monkeypatch.setattr(
        "app.services.discovery_sector_position.build_sector_position_map_for_opportunities",
        _spy,
    )
    sector_ctx._fetch_sector_position_map(
        ["黄金", "煤炭", "数字经济", "半导体材料", "稀土", "医疗"],
        "2026-08-11",
        None,
        budget_seconds=16.0,
    )
    assert seen["max_workers"] == 6

    sector_ctx._fetch_sector_position_map(
        [f"板块{index}" for index in range(20)],
        "2026-08-11",
        None,
        budget_seconds=32.0,
    )
    assert seen["max_workers"] == sector_ctx._SECTOR_POSITION_MAX_WORKERS


@pytest.mark.parametrize("held_sector_count", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 40])
def test_outer_timeout_always_covers_the_inner_budget(held_sector_count: int) -> None:
    """外层写死过一次（5 s vs 内层 12 s+），代价是整层证据被静默丢掉。别再漂移。"""
    assert facts_mod.sector_opportunity_timeout_seconds(held_sector_count) >= (
        sector_ctx.sector_opportunity_total_budget_seconds(held_sector_count)
    )


@pytest.mark.parametrize("held_sector_count", [4, 6, 40])
def test_total_budget_is_derived_from_its_own_stages(held_sector_count: int) -> None:
    """总预算必须由各阶段派生：手写一个数字正是此前漂移的根因。"""
    expected = (
        max(
            sector_ctx.SECTOR_FLOW_BUDGET_SECONDS,
            sector_ctx.SECTOR_DIVERGENCE_BUDGET_SECONDS,
            sector_ctx.sector_position_budget_seconds(held_sector_count),
        )
        + sector_ctx.PERCENTILE_UNIVERSE_BUDGET_SECONDS
        + sector_ctx._SCORING_MARGIN_SECONDS
    )
    assert (
        sector_ctx.sector_opportunity_total_budget_seconds(held_sector_count) == expected
    )


def test_position_stage_receives_the_scaled_budget(monkeypatch) -> None:
    """伸缩必须真的传到联网那一段，而不是只体现在对外的常量上。"""
    seen: dict[str, float] = {}

    def _fetch_position(labels, trade_date):
        return {}

    def _spy(labels, trade_date, fetcher, *, budget_seconds):
        seen["budget"] = float(budget_seconds)
        return {}

    monkeypatch.setattr(sector_ctx, "_fetch_sector_position_map", _spy)
    monkeypatch.setattr(
        sector_ctx, "build_sector_flow_map_for_opportunities",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        sector_ctx, "build_sector_divergence_map_for_opportunities",
        lambda *_a, **_k: {},
    )

    labels = ["黄金", "煤炭", "数字经济", "半导体材料", "稀土", "医疗"]
    holdings = [
        Holding(
            fund_code=f"00000{index}",
            fund_name=f"基金{index}",
            sector_name=label,
            holding_amount=10_000.0,
        )
        for index, label in enumerate(labels)
    ]
    sector_ctx.build_holding_sector_opportunity_context(
        holdings,
        trade_date="2026-08-11",
        fetch_sector_heat=lambda: [
            {"sector_label": label, "heat_score": 1.0, "change_1d_percent": 1.0}
            for label in labels
        ],
        fetch_sector_position=_fetch_position,
    )

    # 6 个板块 → 两波 → 16 s；旧行为是恒定 8 s。
    assert seen["budget"] == pytest.approx(16.0)


# --- 契约 2：超时保留已算完的方向层 -----------------------------------------


def test_progress_publishes_the_held_layer(monkeypatch) -> None:
    """方向层算完就要立刻可见，否则外层超时时无从取用。"""
    monkeypatch.setattr(
        sector_ctx, "build_sector_flow_map_for_opportunities",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        sector_ctx, "build_sector_divergence_map_for_opportunities",
        lambda *_a, **_k: {},
    )

    progress: dict = {}
    holdings = [
        Holding(
            fund_code="011373",
            fund_name="招商前沿医疗保健股票A",
            sector_name="医疗",
            holding_amount=10_000.0,
        )
    ]
    sector_ctx.build_holding_sector_opportunity_context(
        holdings,
        trade_date="2026-08-11",
        fetch_sector_heat=lambda: [
            {"sector_label": "医疗", "heat_score": 1.0, "change_1d_percent": 1.0}
        ],
        fetch_sector_position=lambda _labels, _date: {},
        progress=progress,
    )

    assert progress.get("started_at") is not None
    assert "医疗" in (progress.get("held") or {})


def test_timeout_keeps_the_already_computed_held_layer() -> None:
    """外层 deadline 到点时用 progress 里的方向层，而不是把它清空。"""
    progress = {
        "held": {
            "医疗": {
                "sector_label": "医疗",
                "entry_state": "ready_to_start",
                "trend_strength_score": 93.14,
            }
        },
        "sector_flow_by_label": {"医疗": {"available": True}},
        "divergence_backtest": {},
        "heat_available": True,
    }

    def _overruns_the_outer_deadline():
        time.sleep(5.0)
        return {"available": True, "held": progress["held"], "market_top": ["..."]}

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_overruns_the_outer_deadline)
        result = facts_mod._enhancement_result(
            future,
            timeout_seconds=0.3,
            fallback_factory=lambda: facts_mod._sector_opportunity_from_progress(
                progress, []
            ),
        )

    assert result["reason"] == "timeout_partial_held_only"
    assert result["held"]["医疗"]["entry_state"] == "ready_to_start"
    # 轮动参考确实没跑完，如实为空——区分"方向层有"与"锦上添花没跑完"。
    assert result["market_top"] == []


def test_timeout_without_any_progress_still_reports_the_layer_as_absent() -> None:
    """真的什么都没算出来时不许假装有方向层。"""
    result = facts_mod._sector_opportunity_from_progress({}, [])
    assert result["available"] is False
    assert result["reason"] == "timeout"
    assert result["held"] == {}


def test_fallback_factory_is_not_called_on_the_happy_path() -> None:
    """兜底必须惰性求值：快乐路径下不得触发任何额外计算。"""
    calls: list[int] = []

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: {"available": True, "held": {"医疗": {}}})
        result = facts_mod._enhancement_result(
            future,
            timeout_seconds=5.0,
            fallback_factory=lambda: calls.append(1) or {},
        )

    assert result["available"] is True
    assert calls == []


# --- 契约 3：取不全时必须显式可见 -------------------------------------------


def _heat(labels: list[str]) -> list[dict]:
    return [
        {"sector_label": label, "heat_score": 1.0, "change_1d_percent": 1.0}
        for label in labels
    ]


def _holdings(labels: list[str]) -> list[Holding]:
    return [
        Holding(
            fund_code=f"00000{index}",
            fund_name=f"基金{index}",
            sector_name=label,
            holding_amount=10_000.0,
        )
        for index, label in enumerate(labels)
    ]


def test_partial_position_coverage_is_disclosed_not_hidden(monkeypatch) -> None:
    """"方向层可用"不能掩盖"其中两个板块其实没有"。"""
    monkeypatch.setattr(
        sector_ctx, "build_sector_flow_map_for_opportunities", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        sector_ctx, "build_sector_divergence_map_for_opportunities", lambda *_a, **_k: {}
    )
    labels = ["医疗", "煤炭", "稀土"]
    # 只有「医疗」拿到了价格结构。
    monkeypatch.setattr(
        sector_ctx,
        "_build_holding_mainline",
        lambda **kwargs: (
            {"医疗": {"status": "confirmed"}},
            {
                "available": True,
                "complete": False,
                "missing_labels": ["煤炭", "稀土"],
                "source": "report_computed",
            },
        ),
    )

    result = sector_ctx.build_holding_sector_opportunity_context(
        _holdings(labels),
        trade_date="2026-08-11",
        fetch_sector_heat=lambda: _heat(labels),
        fetch_sector_position=lambda _labels, _date: {"医疗": {}},
    )

    mainline = result["mainline"]
    assert mainline["complete"] is False
    assert sorted(mainline["missing_labels"]) == ["煤炭", "稀土"]


def test_full_coverage_reports_complete(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_ctx, "build_sector_flow_map_for_opportunities", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        sector_ctx, "build_sector_divergence_map_for_opportunities", lambda *_a, **_k: {}
    )
    labels = ["医疗", "煤炭"]
    monkeypatch.setattr(
        sector_ctx,
        "_build_holding_mainline",
        lambda **kwargs: (
            {label: {"status": "confirmed"} for label in labels},
            {
                "available": True,
                "complete": True,
                "missing_labels": [],
                "source": "report_computed",
            },
        ),
    )

    result = sector_ctx.build_holding_sector_opportunity_context(
        _holdings(labels),
        trade_date="2026-08-11",
        fetch_sector_heat=lambda: _heat(labels),
        fetch_sector_position=lambda _labels, _date: {label: {} for label in labels},
    )

    assert result["mainline"]["complete"] is True
    assert result["mainline"]["missing_labels"] == []
