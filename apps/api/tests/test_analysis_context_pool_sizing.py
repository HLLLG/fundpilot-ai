"""上下文线程池不得小于单份日报的增强项任务数。

回归背景（2026-08-11 14:30 线上实测）：

`sse_analysis_context_workers` 默认 **2**，而 `build_analysis_facts` 一次提交 **6** 个增强项
（signal_backtest / guard_policy / intraday / stock_connect_flow / sector_opportunity /
market_breadth），`sector_opportunity` 排在第 5 位。

每个增强项都有自己的超时预算，而 `_enhancement_result` 的 deadline 从"开始等待"起算——任务还在
队列里排队时预算就已经在流失。池子比任务数小，等于让那些超时数字变成"排队时间 + 数据源时间"
的混合物。更糟的是 `future.cancel()` 对**已在运行**的任务无效（代码注释里早有记录），被放弃的
超时任务仍占着 worker 跑完自己的预算，两个这样的任务就能让方向层压根没机会启动。

后果就是那次 `sector_rotation.reason=timeout`、`held={}`、6 只持仓被数据门禁全部降为观察。

实测排除了另外两个嫌疑：价格结构那段 6 个板块冷缓存 2.50 s、热缓存 0.13 s 且 6/6 命中（走零
网络的 board_fund_flow 缓存），端到端 build_holding_sector_opportunity_context 热态 1.15 s、
6/6 都有 entry_state。所以不是板块数，也不是价格结构。
"""

from __future__ import annotations

from app.config import get_settings
from app.services import shared_executors


def test_pool_floor_covers_one_reports_task_count() -> None:
    assert shared_executors.analysis_context_worker_floor() >= (
        shared_executors.ANALYSIS_ENHANCEMENT_TASK_COUNT
    )


def test_configured_default_is_not_below_the_floor() -> None:
    """默认配置本身就必须达标，不能靠运行时抬举来兜底。"""
    assert (
        get_settings().sse_analysis_context_workers
        >= shared_executors.analysis_context_worker_floor()
    )


def test_task_count_matches_what_build_analysis_facts_submits() -> None:
    """任务数是硬编码常量，加了增强项却忘了改它就会重演排队饿死。"""
    import inspect

    from app.services import analysis_facts

    source = inspect.getsource(analysis_facts.build_analysis_facts)
    submitted = source.count("enhancement_futures.append(")
    assert submitted == shared_executors.ANALYSIS_ENHANCEMENT_TASK_COUNT, (
        f"build_analysis_facts 提交了 {submitted} 个增强项，而 "
        f"ANALYSIS_ENHANCEMENT_TASK_COUNT={shared_executors.ANALYSIS_ENHANCEMENT_TASK_COUNT}；"
        "两者必须同步，否则后提交的任务会用超时预算去排队。"
    )


def test_config_cannot_shrink_the_pool_below_the_floor(monkeypatch) -> None:
    """把配置调小不该重新引入饿死；只能往上调。"""
    monkeypatch.setattr(shared_executors, "_analysis_context_executor", None)

    class _Settings:
        sse_analysis_context_workers = 1

    monkeypatch.setattr(shared_executors, "get_settings", lambda: _Settings())
    try:
        executor = shared_executors.get_analysis_context_executor()
        assert executor._max_workers >= shared_executors.analysis_context_worker_floor()
    finally:
        # 别把这个被降级的池子留给后续测试。
        shared_executors._analysis_context_executor = None


def test_config_can_still_raise_the_pool(monkeypatch) -> None:
    monkeypatch.setattr(shared_executors, "_analysis_context_executor", None)

    class _Settings:
        sse_analysis_context_workers = 64

    monkeypatch.setattr(shared_executors, "get_settings", lambda: _Settings())
    try:
        executor = shared_executors.get_analysis_context_executor()
        assert executor._max_workers == 64
    finally:
        shared_executors._analysis_context_executor = None
