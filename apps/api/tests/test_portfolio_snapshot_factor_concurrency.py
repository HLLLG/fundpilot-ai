"""因子分净值兜底并发化修复回归（2026-07-04）。

根因：`build_factor_scores_payload` 对不在排行榜横截面里的持仓走 `_target_from_nav`
净值兜底——每次是一次独立的 AkShare 拉取（冷缓存时子进程 + 网络 IO，通常 1~3 秒），
此前用 for 循环逐只**串行**执行。喂 LLM 用的这条装配路径只有 4 秒预算
（`analysis_payload.FACTOR_SCORE_TIMEOUT_SECONDS`），持仓里哪怕只有几只基金不在
排行榜前 300 名内，串行拉取就必然超时——这是「量化证据缺失」故障的直接根因之一。
"""

from __future__ import annotations

import time

from app.models import Holding
from app.services.portfolio_snapshot import build_factor_scores_payload


def _holding(code: str) -> Holding:
    return Holding(fund_code=code, fund_name=f"基金{code}", holding_amount=1000.0)


def test_nav_fallback_lookups_run_concurrently_not_serially() -> None:
    """5 只持仓全部不在排行榜里、每只净值查询耗时 0.2s：串行需 ~1.0s，并发应远小于此。"""
    holdings = [_holding(code) for code in ("100001", "100002", "100003", "100004", "100005")]

    def fake_rank():
        return []  # 排行榜为空 -> 全部走净值兜底

    call_times: list[float] = []

    def fake_nav(code: str, name: str, trading_days: int):
        start = time.monotonic()
        time.sleep(0.2)
        call_times.append(time.monotonic() - start)
        return []  # 净值点不足 2 个 -> _target_from_nav 返回占位 FundFactorInput

    start = time.monotonic()
    build_factor_scores_payload(holdings, fetch_rank=fake_rank, fetch_nav=fake_nav)
    elapsed = time.monotonic() - start

    # 串行基线：5 * 0.2s = 1.0s；并发（max_workers=8，5 个任务一批跑完）应接近单次
    # 0.2s，留足余量给调度开销，但必须明显小于串行基线，证明确实是并发而非串行。
    assert elapsed < 0.6, f"expected concurrent execution (<0.6s), got {elapsed:.3f}s"
    assert len(call_times) == 5


def test_single_nav_fallback_still_works_without_thread_pool_overhead() -> None:
    """只有 1 只持仓需要净值兜底时走单次直调分支，不引入线程池。"""
    holdings = [_holding("100001")]

    def fake_rank():
        return []

    calls: list[str] = []

    def fake_nav(code: str, name: str, trading_days: int):
        calls.append(code)
        return []

    result = build_factor_scores_payload(holdings, fetch_rank=fake_rank, fetch_nav=fake_nav)

    assert calls == ["100001"]
    assert result["available"] is False  # universe 为空，样本不足


def test_mixed_rank_hit_and_nav_fallback_preserves_correct_targets() -> None:
    """部分持仓在排行榜命中、部分走净值兜底时，两类目标都要正确出现在结果里，
    且顺序/字段不因并发化而错位（并发结果按原始位置写回，不按完成顺序）。"""
    holdings = [_holding(code) for code in ("100001", "100002", "100003")]

    rank_rows = [
        {
            "fund_code": "100002",
            "fund_name": "排行榜基金100002",
            "return_3m_percent": 5.0,
            "return_6m_percent": 8.0,
            "return_1y_percent": 20.0,
            "max_drawdown_1y_percent": -10.0,
            "fund_scale_yi": 5.0,
        }
    ] + [
        {
            "fund_code": f"9{i:05d}",
            "fund_name": f"填充基金{i}",
            "return_3m_percent": float(i),
            "return_6m_percent": float(i) * 1.5,
            "return_1y_percent": float(i) * 2,
            "max_drawdown_1y_percent": -float(i),
            "fund_scale_yi": 10.0,
        }
        for i in range(1, 30)  # 凑够 MIN_UNIVERSE_SIZE=30
    ]

    def fake_rank():
        return rank_rows

    nav_call_codes: list[str] = []

    def fake_nav(code: str, name: str, trading_days: int):
        nav_call_codes.append(code)
        return []  # 净值不足 -> 占位 target，不参与打分但不报错

    result = build_factor_scores_payload(holdings, fetch_rank=fake_rank, fetch_nav=fake_nav)

    # 100001、100003 不在榜，走净值兜底；100002 命中排行榜，不应触发净值查询。
    assert sorted(nav_call_codes) == ["100001", "100003"]
    fund_codes_in_result = [fund["fund_code"] for fund in result["funds"]]
    assert set(fund_codes_in_result) == {"100001", "100002", "100003"}
