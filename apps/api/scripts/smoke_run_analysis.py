"""端到端 smoke：跑一次真实 run_analysis() 或 stream_analysis()，打印每个 stage 计时。

用法（需 .env 配 FUND_AI_DEEPSEEK_API_KEY 与正常 AkShare 环境）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py [--mode fast|deep]
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode fast --stream
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_analysis.py --mode deep --stream

输出：每个 stage 进入/退出时间戳，最终总耗时与产出 report 概要。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 允许直接 `python scripts/smoke_run_analysis.py`（无需手动 PYTHONPATH）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import AnalysisRequest, Holding, InvestorProfile
from app.request_context import set_request_user_id
from app.services.analyze_pipeline import run_analysis
from app.services.analyze_streaming import stream_analysis


# 静音 httpx INFO 噪声，便于看 stage 时间戳
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast",
                        help="analysis_mode，默认 fast")
    parser.add_argument("--label", default="default", help="trial 标签，便于对比冷热")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="走 stream_analysis SSE 路径（fast / deep）",
    )
    args = parser.parse_args()

    if args.stream and args.mode not in {"fast", "deep"}:
        print("!! --stream 仅支持 fast / deep 模式")
        sys.exit(2)

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

    print(
        f"\n=== {'stream_analysis' if args.stream else 'run_analysis'} "
        f"smoke trial={args.label} mode={args.mode} holdings={len(holdings)} ==="
    )
    print(f"[{_now()}]  +  0.00s   {'start':18s}  开始计时")

    # 私有部署用户隔离：注入一个测试用户 ID（任何持仓元数据落库后会按此 ID 隔离）
    set_request_user_id(1)

    first_partial_at: float | None = None
    first_fund_partial_at: float | None = None
    first_byte_at: float | None = None
    first_tool_round_at: float | None = None
    tool_round_count = 0
    fetch_news_count = 0
    token_count = 0

    try:
        if args.stream:
            report = None
            for event in stream_analysis(request, user_id=1):
                elapsed = time.monotonic() - t0_wall
                etype = event.get("type", "?")
                if first_byte_at is None:
                    first_byte_at = elapsed
                if etype == "session":
                    print(
                        f"[{_now()}]  +{elapsed:6.2f}s   {'session':18s}  id={event.get('session_id')}",
                        flush=True,
                    )
                elif etype == "stage":
                    stage = str(event.get("stage", ""))
                    label = str(event.get("label", ""))
                    stage_log.append((stage, elapsed))
                    if stage.startswith("tool_round_") and first_tool_round_at is None:
                        first_tool_round_at = elapsed
                    if stage.startswith("tool_round_"):
                        tool_round_count += 1
                    if stage == "fetch_market_news":
                        fetch_news_count += 1
                    print(f"[{_now()}]  +{elapsed:6.2f}s   {stage:18s}  {label}", flush=True)
                elif etype == "token":
                    token_count += 1
                elif etype == "skeleton":
                    codes = event.get("fund_codes") or []
                    print(
                        f"[{_now()}]  +{elapsed:6.2f}s   {'skeleton':18s}  funds={len(codes)}",
                        flush=True,
                    )
                elif etype == "report_partial":
                    field = str(event.get("field", ""))
                    if first_partial_at is None:
                        first_partial_at = elapsed
                    if field == "fund_recommendation" and first_fund_partial_at is None:
                        first_fund_partial_at = elapsed
                    print(
                        f"[{_now()}]  +{elapsed:6.2f}s   {'partial':18s}  {field}",
                        flush=True,
                    )
                elif etype == "done":
                    payload = event.get("report") or {}
                    print(
                        f"[{_now()}]  +{elapsed:6.2f}s   {'done':18s}  report_id={event.get('report_id')}",
                        flush=True,
                    )
                    from app.models import Report

                    report = Report.model_validate(payload)
                elif etype == "error":
                    raise RuntimeError(str(event.get("message", "stream error")))
            if report is None:
                raise RuntimeError("stream 未返回 done 事件")
        else:
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

    if args.stream:
        print(f"\n=== stream 感知指标 ===")
        if first_byte_at is not None:
            print(f"  首字节 (TTFB):       {first_byte_at:.2f}s")
        if first_tool_round_at is not None:
            print(f"  首个 tool_round:     {first_tool_round_at:.2f}s  (共 {tool_round_count} 次 stage)")
            print(f"  fetch_market_news:   {fetch_news_count} 次")
        if token_count:
            print(f"  LLM token chunks:    {token_count}")
        if first_partial_at is not None:
            print(f"  首条 partial:          {first_partial_at:.2f}s")
        if first_fund_partial_at is not None:
            print(f"  首只持仓 partial:    {first_fund_partial_at:.2f}s")
        elif first_partial_at is not None:
            print(f"  首只持仓 partial:    n/a（仅有 title/summary partial）")
        else:
            print(f"  首只持仓 partial:    n/a")

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
