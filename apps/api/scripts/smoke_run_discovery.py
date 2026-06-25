"""端到端 smoke：跑一次真实 stream_discovery()，打印 stage 计时与 TTFB。

用法（需 .env 配 FUND_AI_DEEPSEEK_API_KEY 与正常 AkShare 环境）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py --label stream

输出：每个 stage 进入时间戳、TTFB、skeleton、首条 recommendation partial、总耗时。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import DiscoveryRequest, Holding, InvestorProfile
from app.request_context import set_request_user_id
from app.services.discovery_streaming import stream_discovery

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="default", help="trial 标签，便于对比冷热")
    parser.add_argument(
        "--mode",
        choices=["fast", "deep"],
        default="fast",
        help="analysis_mode，fast / deep 均走流式",
    )
    args = parser.parse_args()

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

    request = DiscoveryRequest(
        holdings=holdings,
        profile=profile,
        analysis_mode=args.mode,
        focus_sectors=["半导体", "白酒"],
        scan_mode="full_market",
    )

    stage_log: list[tuple[str, float]] = []
    t0_wall = time.monotonic()

    print(
        f"\n=== stream_discovery smoke trial={args.label} mode={args.mode} "
        f"holdings={len(holdings)} ==="
    )
    print(f"[{_now()}]  +  0.00s   {'start':18s}  开始计时")

    set_request_user_id(1)

    first_byte_at: float | None = None
    skeleton_at: float | None = None
    first_partial_at: float | None = None
    first_rec_partial_at: float | None = None
    token_count = 0

    try:
        report = None
        for event in stream_discovery(request, user_id=1):
            elapsed = time.monotonic() - t0_wall
            etype = event.get("type", "?")
            if first_byte_at is None:
                first_byte_at = elapsed
            if etype == "stage":
                stage = str(event.get("stage", ""))
                label = str(event.get("label", ""))
                stage_log.append((stage, elapsed))
                print(f"[{_now()}]  +{elapsed:6.2f}s   {stage:18s}  {label}", flush=True)
            elif etype == "skeleton":
                skeleton_at = elapsed
                codes = event.get("fund_codes") or []
                print(
                    f"[{_now()}]  +{elapsed:6.2f}s   {'skeleton':18s}  candidates={len(codes)}",
                    flush=True,
                )
            elif etype == "token":
                token_count += 1
            elif etype == "report_partial":
                field = str(event.get("field", ""))
                if first_partial_at is None:
                    first_partial_at = elapsed
                if field == "recommendation" and first_rec_partial_at is None:
                    first_rec_partial_at = elapsed
                    value = event.get("value") or {}
                    code = value.get("fund_code", "?")
                    print(
                        f"[{_now()}]  +{elapsed:6.2f}s   {'partial':18s}  recommendation {code}",
                        flush=True,
                    )
                else:
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
                from app.models import FundDiscoveryReport

                report = FundDiscoveryReport.model_validate(payload)
            elif etype == "error":
                raise RuntimeError(str(event.get("message", "stream error")))
        if report is None:
            raise RuntimeError("stream 未返回 done 事件")
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

    print(f"\n=== stream 感知指标 ===")
    if first_byte_at is not None:
        print(f"  首字节 (TTFB):         {first_byte_at:.2f}s")
    if skeleton_at is not None:
        print(f"  skeleton:              {skeleton_at:.2f}s")
    if first_partial_at is not None:
        print(f"  首条 partial:            {first_partial_at:.2f}s")
    if first_rec_partial_at is not None:
        print(f"  首条 recommendation:   {first_rec_partial_at:.2f}s")
    else:
        print(f"  首条 recommendation:   n/a")
    if token_count:
        print(f"  LLM token chunks:      {token_count}")

    print(f"\n=== report 概要 ===")
    print(f"  provider:           {report.provider}")
    print(f"  title:              {report.title[:60]}")
    print(f"  recommendations:    {len(report.recommendations)}")
    print(f"  candidate_pool:     {len(report.candidate_pool)}")
    print(f"  caveats:            {len(report.caveats)}")


if __name__ == "__main__":
    main()
