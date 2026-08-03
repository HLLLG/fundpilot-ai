#!/usr/bin/env python3
"""离线评估板块方向模型（V2/V3）的前瞻收益，产出人读报告 + summary.json。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_sector_direction_backtest.py --trading-days 400
    ./.venv/Scripts/python.exe scripts/run_sector_direction_backtest.py --step 20 --scan-thresholds

这是「第 0 层标尺」：在它给出结论之前，任何对 mainline_regime 权重或入场线阈值的调整都
只是用一组未验证的数字替换另一组未验证的数字。产出**只用于研究与人工复核**，不自动改动
任何线上权重、阈值、Prompt、Guard 或仓位。

诚实前提：本仓库沙箱到东财 `push2his` 的出站被阻断（见
`app/services/sector_flow_divergence_backtest` 同一处划界），因此本脚本必须在能访问上游
的环境（生产 / 预发布）里运行才能取到真实数据；在受限环境下它会如实报告
`unavailable` 板块并以非零退出码结束，而不是用空数据编造结论。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.sector_direction_backtest import (  # noqa: E402
    BASELINE_ALL_SECTORS,
    BASELINE_TOP_CHANGE_1D,
    DEFAULT_FORWARD_HORIZONS,
    DEFAULT_WARMUP_DAYS,
    FACTOR_KEYS,
    GROUP_PRODUCTION_SELECTION,
    SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION,
    compute_direction_factor_ic,
    load_direction_backtest_inputs,
    load_direction_backtest_inputs_from_flow_cache,
    replay_with_per_label_benchmarks,
    scan_entry_gate_thresholds,
    summarize_direction_replay,
)
from app.services.sector_opportunity_scoring import (  # noqa: E402
    ENTRY_FORMING,
    ENTRY_GATE_THRESHOLDS,
    ENTRY_INVALID,
    ENTRY_POLICY_VERSION,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    V3_GATE_THRESHOLDS,
)

_DEFAULT_OUT_DIR = str(API_ROOT / "var" / "sector_direction")

_GROUP_LABEL = {
    ENTRY_READY_TO_START: "可以开始布局",
    ENTRY_READY_ON_PULLBACK: "等待合适位置",
    ENTRY_FORMING: "条件形成中",
    ENTRY_INVALID: "暂不参与",
    GROUP_PRODUCTION_SELECTION: "生产实际展示",
    BASELINE_TOP_CHANGE_1D: "基准·当日涨幅前5",
    BASELINE_ALL_SECTORS: "基准·全板块等权",
}
_GROUP_ORDER = (
    ENTRY_READY_TO_START,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_FORMING,
    ENTRY_INVALID,
    GROUP_PRODUCTION_SELECTION,
    BASELINE_TOP_CHANGE_1D,
    BASELINE_ALL_SECTORS,
)

_FACTOR_LABEL = {
    "relative_strength": "相对强度",
    "trend_persistence": "趋势持续",
    "fund_flow": "资金强度",
    "breadth": "上涨广度",
    "market_structure": "市场结构",
    "direction_score": "方向潜力",
    "setup_maturity_score": "形态成熟",
    "entry_readiness_score": "入场成熟",
    "price_structure_score": "价格结构",
    "research_score": "研究排序分",
    "legacy_score": "旧机会分",
}


class DirectionBacktestUnavailable(RuntimeError):
    """上游序列不可得，无法形成任何有效观测。"""


def _fmt(value: object, spec: str = "+.2f") -> str:
    if value is None:
        return "—"
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return "—"


def _display_width(text: str) -> int:
    """等宽终端里 CJK 与全角标点占两格；用它对齐才不会让中文列参差不齐。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int, *, align: str = "left") -> str:
    padding = max(0, width - _display_width(text))
    return text + " " * padding if align == "left" else " " * padding + text


def _row(cells: list[tuple[str, int]], *, align: str = "right") -> str:
    first, first_width = cells[0]
    line = "    " + _pad(first, first_width)
    for text, width in cells[1:]:
        line += _pad(text, width, align=align)
    return line


_GROUP_COLUMNS = (18, 7, 8, 8, 12, 9, 9, 9, 8)


def _render_group_table(summary: dict, horizon: int) -> list[str]:
    headers = ["分组", "样本", "决策日", "胜率%", "去均值超额", "中位", "P10", "MAE", "t"]
    lines = [
        f"  持有 {horizon} 个交易日（主指标：按日横截面去均值后的超额）",
        _row(list(zip(headers, _GROUP_COLUMNS))) + "  结论",
    ]
    groups = summary.get("groups") or {}
    for name in _GROUP_ORDER:
        stats = ((groups.get(name) or {}).get("horizons") or {}).get(str(horizon)) or {}
        label = _GROUP_LABEL.get(name, name)
        if not stats.get("available"):
            cells = [label, *(["—"] * (len(_GROUP_COLUMNS) - 1))]
            lines.append(_row(list(zip(cells, _GROUP_COLUMNS))) + "  样本不足")
            continue
        cells = [
            label,
            str(stats["observation_count"]),
            str(stats["decision_day_count"]),
            f"{stats['hit_rate_percent']:.1f}",
            _fmt(stats["mean_demeaned_excess_percent"]),
            _fmt(stats["median_demeaned_excess_percent"]),
            _fmt(stats["p10_demeaned_excess_percent"]),
            _fmt(stats["mean_max_adverse_excess_percent"]),
            _fmt(stats["decision_day_t_stat"]),
        ]
        verdict = "显著" if stats.get("significant") else "不显著"
        lines.append(_row(list(zip(cells, _GROUP_COLUMNS))) + f"  {verdict}")
    return lines


def _render_ic_table(ic: dict, horizons: list[int]) -> list[str]:
    lines = ["  单因子 Rank IC（因子 vs 前瞻超额，逐日横截面 Spearman）"]
    widths = [14, *([11, 9, 7] * len(horizons))]
    headers = ["因子"]
    for horizon in horizons:
        headers.extend([f"IC@{horizon}", "ICIR", "n"])
    lines.append(_row(list(zip(headers, widths))))
    for factor in FACTOR_KEYS:
        per_horizon = (ic.get("factors") or {}).get(factor) or {}
        cells = [_FACTOR_LABEL.get(factor, factor)]
        for horizon in horizons:
            stats = per_horizon.get(str(horizon)) or {}
            if not stats.get("available"):
                cells.extend(["—", "—", "0"])
                continue
            mark = "*" if stats.get("significant") else ""
            cells.extend(
                [
                    _fmt(stats["mean_ic"], "+.4f") + mark,
                    _fmt(stats["icir"]),
                    str(stats["n_periods"]),
                ]
            )
        lines.append(_row(list(zip(cells, widths))))
    lines.append(
        "    * = 决策日样本足够且 |t| >= 2。单期 Rank IC 0.03~0.05 即属可用；"
        "接近 1 几乎一定是前视、样本污染或合成数据，不要当成模型很强。"
    )
    return lines


def _render_report(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"板块方向模型前瞻收益评估  运行: {payload['run_date']}")
    lines.append(f"被评估策略: {payload['policy_evaluated']}   schema: {payload['schema_version']}")
    lines.append(
        f"板块请求 {payload['requested_label_count']} 个 / 成功装载 {payload['loaded_label_count']} 个"
        f"   step={payload['params']['step']}   持有期={payload['params']['forward_horizons']}"
    )
    lines.append("⚠ 研究输出，shadow_record_only：不自动调整任何线上权重、阈值、Prompt、Guard 或仓位。")
    for group in payload.get("replays", []):
        lines.append("")
        lines.append("=" * 84)
        lines.append(
            f"基准 {group['benchmark_label']}   板块 {group['label_count']} 个"
            f"   决策日 {group['decision_day_count']}   观测 {group['observation_count']}"
        )
        if group.get("skipped_days"):
            reasons: dict[str, int] = {}
            for item in group["skipped_days"]:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
            lines.append(f"  跳过的决策日: {reasons}")
        coverage = group.get("feature_coverage") or {}
        if coverage:
            lines.append(
                "  因子覆盖率: "
                + ", ".join(
                    f"{_FACTOR_LABEL.get(key, key)} {value:.0%}"
                    for key, value in coverage.items()
                    if key in _FACTOR_LABEL
                )
            )
            lines.append(
                f"  证据完整占比 {coverage.get('evidence_quality_complete', 0):.0%}"
                f"   基准日历精确对齐占比 {coverage.get('benchmark_calendar_aligned', 0):.0%}"
            )
        for horizon in payload["params"]["forward_horizons"]:
            lines.append("")
            lines.extend(_render_group_table(group["summary"], horizon))
        lines.append("")
        lines.extend(_render_ic_table(group["factor_ic"], payload["params"]["forward_horizons"]))
        verdict = (group["summary"].get("verdict") or {}).get("by_horizon") or {}
        lines.append("")
        lines.append("  入场线结论: " + ("; ".join(f"T+{k}: {v}" for k, v in verdict.items()) or "—"))
        scan = group.get("threshold_scan") or []
        if scan:
            dimensions = list(scan[0]["thresholds"])
            widths = (*([9] * len(dimensions)), 8, 8, 13, 9, 11)
            lines.append("")
            lines.append(
                "  同日原始入场线阈值敏感性（未为每组阈值重建跨日滞回；"
                "前 8 组不同结果，按去均值超额降序）。"
                "选出同一批方向的阈值组合已合并为一行，代表行是该等价类里第一个被扫到的"
                "真实组合；网格搜索天生过拟合，这里只能作研究线索，不能直接改线上阈值。"
            )
            headers = [*dimensions, "样本", "决策日", "去均值超额", "t", "等价组数"]
            lines.append(_row(list(zip(headers, widths))))
            for row in scan[:8]:
                thresholds = row["thresholds"]
                cells = [
                    *(f"{thresholds[key]:.0f}" for key in dimensions),
                    str(row.get("observation_count", 0)),
                    str(row.get("decision_day_count", 0)),
                    _fmt(row.get("mean_demeaned_excess_percent")),
                    _fmt(row.get("decision_day_t_stat")),
                    str(row.get("equivalent_threshold_count", 1)),
                ]
                lines.append(_row(list(zip(cells, widths))))
            baseline = group.get("production_threshold_row")
            if baseline and baseline.get("available"):
                pairs = "/".join(
                    f"{key}={value:.0f}" for key, value in baseline["thresholds"].items()
                )
                lines.append(
                    f"    线上现行的同日原始门槛 {pairs}（未套跨日滞回）："
                    f"样本 {baseline['observation_count']}，"
                    f"去均值超额 {_fmt(baseline['mean_demeaned_excess_percent'])}%，"
                    f"t {_fmt(baseline.get('decision_day_t_stat'))}"
                    "。网格里若有明显更优的组合，先怀疑过拟合，再考虑参数。"
                )
    lines.append("")
    lines.append("-" * 84)
    lines.append("已知缺口与口径限制：")
    for caveat in payload.get("caveats", []):
        lines.append(f"  ⚠ {caveat}")
    if payload.get("unavailable"):
        lines.append("")
        lines.append("未能装载的板块：")
        for label, reason in sorted(payload["unavailable"].items()):
            lines.append(f"  · {label}: {reason}")
    return "\n".join(lines) + "\n"


def build_direction_backtest_report(
    *,
    sector_labels: list[str] | None = None,
    trading_days: int = 400,
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    step: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
    scan_thresholds: bool = False,
    scan_horizon: int | None = None,
    out_dir: str = _DEFAULT_OUT_DIR,
    max_workers: int = 4,
    inputs: dict | None = None,
    entry_policy_version: str = ENTRY_POLICY_VERSION_V3,
) -> dict:
    """装载 → 分基准重放 → 统计 / IC / 阈值扫描 → 落盘 report.txt + summary.json。"""
    if sector_labels is None:
        from app.services.sector_registry import list_theme_board_labels

        sector_labels = list(list_theme_board_labels())

    loaded = inputs or load_direction_backtest_inputs(
        sector_labels,
        trading_days=trading_days,
        max_workers=max_workers,
    )
    price_series_by_label = loaded["price_series_by_label"]
    if not price_series_by_label:
        raise DirectionBacktestUnavailable(
            f"没有任何板块拿到 >= {warmup_days} 根日线（请求 {len(sector_labels)} 个板块）"
        )

    replays = replay_with_per_label_benchmarks(
        price_series_by_label=price_series_by_label,
        benchmark_series_by_key=loaded["benchmark_series_by_key"],
        benchmark_by_label=loaded["benchmark_by_label"],
        flow_series_by_label=loaded.get("flow_series_by_label") or {},
        forward_horizons=forward_horizons,
        warmup_days=warmup_days,
        step=step,
        start_date=start_date,
        end_date=end_date,
        price_source=loaded.get("price_source") or "backtest_daily_kline",
        entry_policy_version=entry_policy_version,
    )
    if not replays:
        raise DirectionBacktestUnavailable("基准序列不可得，无法计算任何相对收益")

    horizons = list(forward_horizons)
    effective_scan_horizon = scan_horizon or max(horizons)
    caveats: list[str] = []
    replay_payloads: list[dict] = []
    total_observations = 0

    for key, replay in replays.items():
        summary = summarize_direction_replay(replay)
        factor_ic = compute_direction_factor_ic(replay)
        threshold_scan: list[dict] = []
        production_threshold_row: dict | None = None
        if scan_thresholds and effective_scan_horizon in replay.horizons:
            threshold_scan = scan_entry_gate_thresholds(
                replay, horizon=effective_scan_horizon
            )
            # 单独再算一次线上现行阈值这一点：合并等价组合后，现行阈值可能已经被
            # 更宽松的代表行吸收掉，网格里不一定还能原样找到它。
            live_thresholds = (
                V3_GATE_THRESHOLDS
                if replay.entry_policy_version == ENTRY_POLICY_VERSION_V3
                else ENTRY_GATE_THRESHOLDS
            )
            production_threshold_row = scan_entry_gate_thresholds(
                replay,
                horizon=effective_scan_horizon,
                grids={key: (value,) for key, value in live_thresholds.items()},
            )[0]
        total_observations += replay.observation_count
        for caveat in replay.caveats:
            if caveat not in caveats:
                caveats.append(caveat)
        replay_payloads.append(
            {
                "benchmark_label": key,
                "label_count": len(replay.labels),
                "labels": replay.labels,
                "decision_day_count": len(replay.decision_dates),
                "observation_count": replay.observation_count,
                "feature_coverage": replay.feature_coverage,
                "skipped_days": [asdict(item) for item in replay.skipped_days],
                "summary": summary,
                "factor_ic": factor_ic,
                "threshold_scan": threshold_scan,
                "production_threshold_row": production_threshold_row,
            }
        )

    if not total_observations:
        raise DirectionBacktestUnavailable(
            "重放完成但没有产生任何可评价观测（检查交易日历缓存与前瞻窗口长度）"
        )

    generated_at = datetime.now(timezone.utc)
    evaluated_policies = sorted({replay.entry_policy_version for replay in replays.values()})
    payload = {
        "schema_version": SECTOR_DIRECTION_BACKTEST_SCHEMA_VERSION,
        # 必须取重放实际使用的 policy version，不能写死常量：否则报告会把 v3 的结果
        # 标成 v2，读报告的人无法判断自己在看哪套口径。
        "policy_evaluated": evaluated_policies[0] if len(evaluated_policies) == 1 else evaluated_policies,
        "decision_policy": "shadow_record_only",
        "auto_tuning_eligible": False,
        "run_date": generated_at.date().isoformat(),
        "generated_at": generated_at.isoformat(),
        "params": {
            "trading_days": trading_days,
            "forward_horizons": horizons,
            "warmup_days": warmup_days,
            "step": step,
            "start_date": start_date,
            "end_date": end_date,
            "scan_thresholds": scan_thresholds,
            "scan_horizon": effective_scan_horizon if scan_thresholds else None,
        },
        "requested_label_count": len(sector_labels),
        "loaded_label_count": len(price_series_by_label),
        "observation_count": total_observations,
        "unavailable": loaded.get("unavailable") or {},
        "benchmark_by_label": loaded.get("benchmark_by_label") or {},
        "caveats": caveats,
        "replays": replay_payloads,
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "report.txt").write_text(_render_report(payload), encoding="utf-8")
    (out_path / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="板块方向模型前瞻收益评估（第0层标尺）")
    parser.add_argument("--trading-days", type=int, default=400, help="每个板块拉取的日线长度")
    parser.add_argument("--forward-horizons", type=str, default="5,10,20")
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        help="决策日采样步长；>= 最长持有期时前瞻窗口不重叠（显著性更可信，样本更少）",
    )
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--sectors", type=str, default=None, help="逗号分隔；默认全主题白名单")
    parser.add_argument(
        "--sqlite-cache",
        type=str,
        default=None,
        help="改从 sector_spot_cache 的 board-flow-hist:v2 缓存离线取数（零网络，价格与资金同源）",
    )
    parser.add_argument("--scan-thresholds", action="store_true", help="额外跑入场线阈值敏感性网格")
    parser.add_argument("--scan-horizon", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--entry-policy-version",
        choices=[ENTRY_POLICY_VERSION, ENTRY_POLICY_VERSION_V3],
        default=ENTRY_POLICY_VERSION_V3,
        help="回放哪一套方向成熟度口径；默认线上现行的 v3",
    )
    parser.add_argument("--out-dir", type=str, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    horizons = tuple(
        sorted({int(value) for value in args.forward_horizons.split(",") if value.strip()})
    )
    labels = (
        [value.strip() for value in args.sectors.split(",") if value.strip()]
        if args.sectors
        else None
    )

    cached_inputs = None
    if args.sqlite_cache:
        cached_inputs = load_direction_backtest_inputs_from_flow_cache(
            args.sqlite_cache,
            sector_labels=labels,
            min_history_days=args.warmup_days,
        )

    try:
        payload = build_direction_backtest_report(
            sector_labels=labels,
            trading_days=args.trading_days,
            forward_horizons=horizons,
            warmup_days=args.warmup_days,
            step=args.step,
            start_date=args.start_date,
            end_date=args.end_date,
            scan_thresholds=args.scan_thresholds,
            scan_horizon=args.scan_horizon,
            out_dir=args.out_dir,
            max_workers=args.max_workers,
            inputs=cached_inputs,
            entry_policy_version=args.entry_policy_version,
        )
    except DirectionBacktestUnavailable as exc:
        print(f"sector direction backtest unavailable: {exc}", file=sys.stderr)
        return 2

    console = {
        "run_date": payload["run_date"],
        "policy_evaluated": payload["policy_evaluated"],
        "loaded_label_count": payload["loaded_label_count"],
        "observation_count": payload["observation_count"],
        "verdicts": {
            group["benchmark_label"]: (group["summary"].get("verdict") or {}).get("by_horizon")
            for group in payload["replays"]
        },
    }
    print(json.dumps(console, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {Path(args.out_dir) / 'report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
