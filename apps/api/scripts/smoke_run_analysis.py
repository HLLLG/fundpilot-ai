"""端到端 smoke：跑一次真实 run_analysis()，打印每个 stage 计时。

用法（需 .env 配 FUND_AI_DEEPSEEK_API_KEY 与正常 AkShare 环境）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py [--mode fast|deep]

输出：每个 stage 进入/退出时间戳，最终总耗时与产出 report 概要。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from app.models import AnalysisRequest, Holding, InvestorProfile
from app.request_context import set_request_user_id
from app.services.analyze_pipeline import run_analysis


# 静音 httpx INFO 噪声，便于看 stage 时间戳
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast",
                        help="analysis_mode，默认 fast")
    parser.add_argument("--label", default="default", help="trial 标签，便于对比冷热")
    args = parser.parse_args()

    # 真实持仓样本：3 只覆盖不同板块的 holding（按 PROJECT_CONTEXT 全局种子）
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长A",
            sector_name="半导体",
            holding_amount=10000.0,
        ),
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            sector_name="商业航天",
            holding_amount=8000.0,
        ),
        Holding(
            fund_code="161725",
            fund_name="招商中证白酒",
            sector_name="白酒",
            holding_amount=5000.0,
        ),
    ]

    profile = InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=35,
        expected_investment_amount=30000,
    )

    request = AnalysisRequest(
        holdings=holdings,
        profile=profile,
        analysis_mode=args.mode,
    )

    stage_log: list[tuple[str, float]] = []
    t0_wall = time.monotonic()

    def progress(stage: str, label: str) -> None:
        elapsed = time.monotonic() - t0_wall
        stage_log.append((stage, elapsed))
        print(f"[{_now()}]  +{elapsed:6.2f}s   {stage:18s}  {label}", flush=True)

    print(f"\n=== run_analysis smoke trial={args.label} mode={args.mode} holdings={len(holdings)} ===")
    print(f"[{_now()}]  +  0.00s   {'start':18s}  开始计时")

    # 私有部署用户隔离：注入一个测试用户 ID（任何持仓元数据落库后会按此 ID 隔离）
    set_request_user_id(1)

    try:
        report = run_analysis(request, on_progress=progress)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0_wall
        print(f"\n!! 失败 @ +{elapsed:.2f}s: {type(exc).__name__}: {exc}")
        sys.exit(1)

    total = time.monotonic() - t0_wall
    print(f"[{_now()}]  +{total:6.2f}s   {'done':18s}  完成\n")

    print("=== stage gaps ===")
    prev = 0.0
    for stage, t in stage_log:
        gap = t - prev
        print(f"  {stage:18s}  Δ {gap:6.2f}s   (累计 {t:.2f}s)")
        prev = t
    print(f"  {'(after stages)':18s}  Δ {total - prev:6.2f}s   (累计 {total:.2f}s)")

    print(f"\n=== report 概要 ===")
    print(f"  provider:           {report.provider}")
    print(f"  title:              {report.title[:60]}")
    print(f"  fund_recs:          {len(report.fund_recommendations)}")
    print(f"  market_news:        {len(report.market_news)}")
    print(f"  topic_briefs:       {len(report.topic_briefs)}")
    print(f"  caveats:            {len(report.caveats)}")
    if report.analysis_facts:
        pipeline = report.analysis_facts.get("pipeline") or {}
        print(f"  llm_judge_applied:  {pipeline.get('llm_judge_applied')}")
        print(f"  model:              {pipeline.get('model')}")


if __name__ == "__main__":
    main()
