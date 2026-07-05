"""板块信号回测日K拉取并发化修复回归（2026-07-04）。

根因：`_build_sector_signal_backtest_impl` 逐板块拉日 K 线（东财 → 中继 → AkShare
逐级兜底，每级都有自己的超时）此前用 for 循环**串行**执行。喂 LLM 用的这条装配路径
只有 5 秒预算（`analysis_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS`），持仓关联的板块
数量哪怕只有 3~5 个，串行拉取就足以吃满预算——这是「量化证据缺失」故障的另一个
直接根因。
"""

from __future__ import annotations

import time

from app.services.sector_signal_backtest import build_sector_signal_backtest


def test_multi_sector_fetch_runs_concurrently_not_serially() -> None:
    """5 个板块、每个日K拉取耗时 0.2s：串行需 ~1.0s，并发应远小于此。"""
    labels = ["半导体", "商业航天", "人工智能", "电网设备", "白酒"]

    def fake_fetch_series(secid: str, source_code: str | None):
        time.sleep(0.2)
        return []  # 空序列 -> len(filtered) < 3，走"有效交易日不足"分支，不影响计时

    start = time.monotonic()
    result = build_sector_signal_backtest(
        labels,
        lookback_days=60,
        fetch_series=fake_fetch_series,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.6, f"expected concurrent execution (<0.6s), got {elapsed:.3f}s"
    assert len(result["sectors"]) == 5


def test_single_sector_fetch_still_works_without_thread_pool_overhead() -> None:
    """只有 1 个板块时走单次直调分支。"""
    calls: list[str] = []

    def fake_fetch_series(secid: str, source_code: str | None):
        calls.append(secid)
        return []

    result = build_sector_signal_backtest(
        ["半导体"],
        lookback_days=60,
        fetch_series=fake_fetch_series,
    )

    assert len(calls) == 1
    assert len(result["sectors"]) == 1


def test_concurrent_results_map_back_to_correct_sector_label() -> None:
    """并发拉取的结果必须按板块名正确对应回去，不能因完成顺序不同而错位——
    构造不同耗时（乱序完成）+ 不同内容（可辨认出处），验证 by_rule 统计的触发
    次数与各板块序列长度一一对应。"""
    from app.services.eastmoney_trends_client import DailyKlineBar

    # secid -> 板块名反查，用于让每个板块返回可辨认、且长度不同的日 K 序列。
    secid_to_label = {
        "SEC_A": "半导体",
        "SEC_B": "商业航天",
        "SEC_C": "人工智能",
    }
    delays = {"SEC_A": 0.06, "SEC_B": 0.01, "SEC_C": 0.03}  # 刻意乱序完成
    lengths = {"SEC_A": 40, "SEC_B": 35, "SEC_C": 38}

    def fake_canon_lookup(label: str):
        from app.services.sector_canonical import CanonicalSector

        secid = next(code for code, name in secid_to_label.items() if name == label)
        return CanonicalSector(
            label=label,
            source_type="concept",
            source_name=label,
            eastmoney_secid=secid,
            source_code=secid,
        )

    def fake_fetch_series(secid: str, source_code: str | None) -> list[DailyKlineBar]:
        time.sleep(delays[secid])
        # 用交替正负涨跌幅构造可回测的序列（避免全 0 触发无意义分支）。
        return [
            {
                "date": f"2026-05-{(i % 28) + 1:02d}",
                "change_percent": 1.0 if i % 2 == 0 else -1.0,
                "high_change_percent": None,
            }
            for i in range(lengths[secid])
        ]

    import app.services.sector_signal_backtest as module

    original_get_canonical = module.get_canonical_sector
    original_get_trade_date_set = module.get_trade_date_set
    try:
        module.get_canonical_sector = fake_canon_lookup  # type: ignore[assignment]
        # 交易日历过滤在这里不是测试重点（用 None 关闭，等价于「全部日期都算交易日」），
        # 避免依赖 conftest.py 里为其他测试固定的小型交易日集合截断合成数据。
        module.get_trade_date_set = lambda: None  # type: ignore[assignment]
        result = module.build_sector_signal_backtest(
            list(secid_to_label.values()),
            lookback_days=60,
            fetch_series=fake_fetch_series,
        )
    finally:
        module.get_canonical_sector = original_get_canonical  # type: ignore[assignment]
        module.get_trade_date_set = original_get_trade_date_set  # type: ignore[assignment]

    sample_days_by_label = {
        row["sector_label"]: row["sample_days"] for row in result["sectors"]
    }
    for secid, label in secid_to_label.items():
        assert sample_days_by_label[label] == lengths[secid], (
            f"{label} sample_days 应等于其自身序列长度 {lengths[secid]}，"
            f"实际 {sample_days_by_label[label]}（说明并发结果发生了错位）"
        )
