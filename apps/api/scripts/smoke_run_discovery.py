"""端到端 smoke：跑一次真实 run_discovery()，打印 stage 计时。

用法（需 .env 配 FUND_AI_DEEPSEEK_API_KEY 与正常 AkShare 环境）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_run_discovery.py --label job
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
from app.services import discovery_pipeline as discovery_pipeline_mod
from app.services.discovery_pipeline import run_discovery

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_INNER: list[tuple[str, float, str]] = []
_CAPTURED: dict[str, object] = {}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _install_inner_timers() -> None:
    """给候选池等内部步骤打点，stage 事件本身分不出 enrich / 基准。"""

    def _wrap(owner, name: str, annotate=None):
        original = getattr(owner, name)

        def wrapped(*args, **kwargs):
            started = time.monotonic()
            result = original(*args, **kwargs)
            elapsed = time.monotonic() - started
            note = annotate(result, *args, **kwargs) if annotate else ""
            _INNER.append((name, elapsed, note))
            print(
                f"[{_now()}]  inner {name:36s}  {elapsed:6.2f}s{note}",
                flush=True,
            )
            return result

        setattr(owner, name, wrapped)

    def _pool_note(result, *_args, **_kwargs) -> str:
        if isinstance(result, list):
            return f"  n={len(result)}"
        return ""

    _wrap(
        discovery_pipeline_mod,
        "fetch_discovery_fund_universe_cached",
        lambda result, *_a, **_k: f"  rows={len(result or [])}",
    )
    _wrap(discovery_pipeline_mod, "build_sector_heat_ranking")
    _wrap(discovery_pipeline_mod, "build_candidate_pool", _pool_note)
    _wrap(discovery_pipeline_mod, "enrich_candidates", _pool_note)
    _wrap(discovery_pipeline_mod, "finalize_candidate_pool", _pool_note)
    _wrap(discovery_pipeline_mod, "attach_descriptive_peer_research", _pool_note)
    _wrap(
        discovery_pipeline_mod,
        "prepare_finalist_research_context",
        lambda result, *_a, **_k: (
            f"  n={len(result[0])}" if isinstance(result, tuple) else ""
        ),
    )
    _wrap(
        discovery_pipeline_mod,
        "_prepare_candidate_benchmark_context",
        lambda result, *_a, **_k: (
            f"  n={len(result[0])}" if isinstance(result, tuple) else ""
        ),
    )
    _wrap(discovery_pipeline_mod, "save_discovery_report")

    original_targets = discovery_pipeline_mod.select_target_sectors

    def _capture_targets(*args, **kwargs):
        started = time.monotonic()
        result = original_targets(*args, **kwargs)
        elapsed = time.monotonic() - started
        _CAPTURED["first_layer_targets"] = list(result)
        _CAPTURED["flow_inflections"] = list(kwargs.get("flow_inflection_labels") or [])
        _INNER.append(("select_target_sectors", elapsed, f"  n={len(result)}"))
        print(
            f"[{_now()}]  inner {'select_target_sectors':36s}  {elapsed:6.2f}s  "
            f"n={len(result)}  {result}",
            flush=True,
        )
        return result

    discovery_pipeline_mod.select_target_sectors = _capture_targets

    original_score = discovery_pipeline_mod._score_select_and_persist_directions

    def _capture_score(*args, **kwargs):
        started = time.monotonic()
        result = original_score(*args, **kwargs)
        elapsed = time.monotonic() - started
        _CAPTURED["sector_opportunities"] = [dict(item) for item in result]
        _INNER.append(
            ("_score_select_and_persist_directions", elapsed, f"  n={len(result)}")
        )
        print(
            f"[{_now()}]  inner {'_score_select_and_persist_directions':36s}  "
            f"{elapsed:6.2f}s  n={len(result)}",
            flush=True,
        )
        return result

    discovery_pipeline_mod._score_select_and_persist_directions = _capture_score


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="default", help="trial 标签，便于对比冷热")
    parser.add_argument(
        "--mode",
        choices=["fast", "deep"],
        default="fast",
        help="analysis_mode，fast / deep",
    )
    args = parser.parse_args()
    _install_inner_timers()

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

    def progress(stage: str, label: str) -> None:
        elapsed = time.monotonic() - t0_wall
        stage_log.append((stage, elapsed))
        print(f"[{_now()}]  +{elapsed:6.2f}s   {stage:18s}  {label}", flush=True)

    print(
        f"\n=== run_discovery smoke trial={args.label} mode={args.mode} "
        f"holdings={len(holdings)} ==="
    )
    print(f"[{_now()}]  +  0.00s   {'start':18s}  开始计时")

    set_request_user_id(1)

    try:
        report = run_discovery(request, on_progress=progress)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0_wall
        print(f"\n!! 失败 @ +{elapsed:.2f}s: {type(exc).__name__}: {exc}")
        sys.exit(1)

    total = time.monotonic() - t0_wall
    print(f"[{_now()}]  +{total:6.2f}s   {'done':18s}  完成\n")

    print("=== stage first-seen durations ===")
    first_seen: list[tuple[str, float]] = []
    for stage, t in stage_log:
        if not first_seen or first_seen[-1][0] != stage:
            first_seen.append((stage, t))
    prev = 0.0
    for index, (stage, t) in enumerate(first_seen):
        end = first_seen[index + 1][1] if index + 1 < len(first_seen) else total
        print(f"  {stage:18s}  {end - t:6.2f}s   (进入 +{t:.2f}s)")
        prev = t
    if _INNER:
        print("\n=== inner steps ===")
        for name, elapsed, note in _INNER:
            print(f"  {name:36s}  {elapsed:6.2f}s{note}")

    print(f"\n=== report 概要 ===")
    print(f"  provider:           {report.provider}")
    print(f"  title:              {report.title[:80]}")
    print(f"  recommendations:    {len(report.recommendations)}")
    print(f"  candidate_pool:     {len(report.candidate_pool)}")
    print(f"  caveats:            {len(report.caveats)}")
    facts = report.discovery_facts if isinstance(report.discovery_facts, dict) else {}
    print(f"  peer_summary:       {bool(facts.get('candidate_peer_summary'))}")
    print(f"  benchmark_contract: {bool(facts.get('benchmark_research_contract'))}")
    contract = report.decision_contract if isinstance(report.decision_contract, dict) else {}
    prompt_contract = contract.get("prompt_contract") if isinstance(contract.get("prompt_contract"), dict) else {}
    print(f"  prompt_version:     {prompt_contract.get('template_version') or contract.get('prompt_version')}")
    for rec in report.recommendations:
        print(
            f"  - {rec.fund_code} {rec.fund_name}  {rec.action}  "
            f"{rec.sector_name}  amt={rec.suggested_amount_yuan}"
        )
    nav_expanded = 0
    peer_ready = 0
    for item in report.candidate_pool:
        family = item.get("share_family") if isinstance(item.get("share_family"), dict) else {}
        if family.get("selected_basis") == "prescreen_representative_nav_not_expanded":
            nav_expanded += 1
        if isinstance(item.get("peer_research"), dict) or isinstance(item.get("peer_rank"), dict):
            peer_ready += 1
    print(f"  family_nav_not_expanded: {nav_expanded}/{len(report.candidate_pool)}")
    print(f"  peer_attached_on_pool:   {peer_ready}/{len(report.candidate_pool)}")
    _print_sector_rule_effects(report, facts)


def _print_sector_rule_effects(report, facts: dict) -> None:
    """对照本次方向挑选五条改动，从实跑结果里抽出可核对的事实。"""

    from collections import Counter

    from app.database import get_discovery_report

    full = None
    if report.id:
        try:
            full = get_discovery_report(report.id)
        except Exception:  # noqa: BLE001 - 烟测诊断失败不应盖过主结果
            full = None
    full_facts = full.get("discovery_facts") if isinstance(full, dict) else None
    if isinstance(full_facts, dict):
        facts = full_facts
    opportunities = facts.get("sector_opportunities")
    if not isinstance(opportunities, list):
        opportunities = _CAPTURED.get("sector_opportunities") or []
    pool = []
    if isinstance(full, dict) and isinstance(full.get("candidate_pool"), list):
        pool = full["candidate_pool"]
    elif report.candidate_pool:
        pool = list(report.candidate_pool)
    scope = facts.get("recommendation_candidate_scope")
    if not isinstance(scope, dict):
        scope = {}

    print("\n=== 方向挑选对照 ===")
    first_layer = _CAPTURED.get("first_layer_targets") or []
    inflections = _CAPTURED.get("flow_inflections") or []
    print(f"  第一层目标:     {first_layer}")
    print(f"  资金拐点召回:   {inflections}")
    print(f"  最终召回方向:   {list(report.target_sectors)}")

    print("\n  入选方向（状态 / 证据 / 首仓 / 约束）")
    complete_auto = 0
    incomplete_auto = 0
    constrained = 0
    invalid_focus = 0
    for item in opportunities:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "")
        state = str(item.get("entry_state") or "?")
        quality = str(item.get("evidence_quality") or "?")
        scale = item.get("first_tranche_scale")
        scale_text = f"{scale:.0%}" if isinstance(scale, (int, float)) else "—"
        flags = []
        if item.get("constrained_ready_to_start") is True:
            constrained += 1
            flags.append("弱参与缩仓")
        if item.get("flow_improving_probe_eligible") is True:
            flags.append("资金拐点试仓")
        if item.get("probability_early_probe_eligible") is True:
            flags.append("提前试仓")
        if quality == "complete" and state != "invalid":
            complete_auto += 1
        elif quality != "complete" and state != "invalid":
            incomplete_auto += 1
        if state == "invalid":
            invalid_focus += 1
            flags.append("仅说明")
        print(
            f"  - {label:10s}  {state:18s}  {quality:12s}  "
            f"tranche={scale_text:4s}  {'/'.join(flags) or '—'}"
        )
    print(
        f"  证据完整自动席: {complete_auto}   "
        f"非完整仍入选: {incomplete_auto}   "
        f"弱参与缩仓: {constrained}   "
        f"invalid: {invalid_focus}"
    )

    counts = Counter(
        str(item.get("sector_label") or "")
        for item in pool
        if isinstance(item, dict)
    )
    print("\n  候选池分方向名额")
    for label, count in counts.most_common():
        opp = next(
            (
                item
                for item in opportunities
                if isinstance(item, dict) and item.get("sector_label") == label
            ),
            {},
        )
        state = str(opp.get("entry_state") or "无方向行")
        print(f"  - {label:10s}  {count:>2} 只  {state}")

    fallbacks = scope.get("theme_vehicle_fallbacks") or {}
    unmatched = scope.get("unmatched_actionable_sector_labels") or []
    print("\n  黄金回退")
    print(f"  unmatched_actionable: {unmatched}")
    print(f"  theme_vehicle_fallbacks: {fallbacks or '{}'}")
    if fallbacks:
        for code, meta in fallbacks.items():
            print(
                f"  - {code}  {meta.get('thesis_sector_label')} → "
                f"{meta.get('vehicle_sector_label')}  {meta.get('entry_path')}"
            )
    else:
        gold_states = [
            (
                str(item.get("sector_label") or ""),
                str(item.get("entry_state") or ""),
            )
            for item in opportunities
            if isinstance(item, dict)
            and str(item.get("sector_label") or "") in {"黄金", "黄金股"}
        ]
        if gold_states:
            print(f"  今日黄金相关方向: {gold_states}（未触发回退）")
        else:
            print("  今日未入选黄金/黄金股，回退条件未触发")


if __name__ == "__main__":
    main()
