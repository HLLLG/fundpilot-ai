#!/usr/bin/env python3
"""离线回测模块2 因子的有效性（walk-forward Rank IC），产出人读报告 + 机读 summary.json。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_factor_ic.py --universe-size 300 --nav-days 750

现行契约：docs/PROJECT_CONTEXT.md「现行权威契约 / Factor IC、PIT 与量化证据」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.factor_ic_backtest import (  # noqa: E402
    DEFAULT_FACTOR_LOOKBACK,
    DEFAULT_FORWARD_DAYS,
    DEFAULT_REBALANCE_STEP,
    FACTOR_ORDER,
    NavPoint,
    compute_factor_ic,
)
from app.services.factor_ic_snapshot import (  # noqa: E402
    FACTOR_IC_SCHEMA_VERSION,
    POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION,
    V2_FACTOR_IC_SCHEMA_VERSION,
)
from app.services.factor_ic_research import (  # noqa: E402
    DEFAULT_FORWARD_HORIZONS,
    build_research_model,
    build_v3_research_model,
    is_v3_research_model_publishable,
)

_DEFAULT_OUT_DIR = str(API_ROOT / "var" / "factor_ic")

_FACTOR_LABEL = {
    "momentum": "动量",
    "risk_adjusted": "风险调整",
    "drawdown": "回撤控制",
    "composite": "综合",
}

_CAVEATS = [
    "基金池为「当前在榜、业绩偏强」样本，存在幸存者/选择偏差，IC 偏乐观。",
    "结论仅用于因子之间的相对比较，不代表全市场真实预测力。",
    "单因子单期 Rank IC 在 0.03~0.05 即属可用；过高通常意味着前视偏差。",
]
_V2_CAVEATS = [
    "基金池来自当前仍存续目录，历史时点基金池尚在逐期积累，仍存在幸存者偏差。",
    "A/C 等份额已按名称保守合并，并按股票/混合/债券/指数/QDII/FOF 分组；分类不明时不借用全局 IC。",
    "收益优先用日增长率重建总收益指数；缺失时才回落单位净值比值，覆盖率低于门槛拒绝发布。",
    "规模因子缺少历史规模序列，继续标记为未回测，不进入 IC 结论。",
    "结论包含 5/20/60 日、HAC 区间与末段留出稳定性；当前幸存者池阶段最高只授予中等置信。",
]
_V3_CAVEATS = [
    "历史每个锚点仅使用当时已发布且不超过 7 日的基金池快照，清盘/退出基金不会被当前目录抹去。",
    "验证使用 5 折 expanding walk-forward、20 个交易日 embargo，并以 Benjamini-Hochberg q 值控制多重检验。",
    "A/C 等份额按底层组合保守合并；总收益、分类和净值覆盖未达硬门槛时自动退回 v2，不伪装 PIT 证据。",
    "规模因子缺少历史规模序列，继续标记为未回测，不进入 IC 结论。",
    "合格因子还须通过同类相对总收益分位组合、HAC 区间、样本外价差和 0.5% 成本门槛；只有统计显著不再算可用。",
    "股票/混合、债券、指数、QDII、FOF 使用同源 NAV 类型因子；指数 tracking 缺精确时点基准时明确标记不足。",
    "当前 PIT 仅冻结历史基金池 membership；NAV 修订时点不可得，普通基金因子统一滞后 1 个交易日、QDII 滞后 2 日，并以下一交易日首个可执行 NAV 计收益，因此置信最高仍为中。",
]
_V3_NAV_OBSERVATION_CAVEATS = [
    *_V3_CAVEATS[:-1],
    "NAV features use only values whose immutable collector first_observed_at was no later than each historical anchor; corrections remain append-only and the earliest observed value wins.",
]


class FactorIcRankUnavailable(RuntimeError):
    """The external fund catalogue produced no usable universe."""


def _default_fetch_rank(limit: int) -> list[dict]:
    from app.services.akshare_subprocess import fetch_open_fund_universe

    return fetch_open_fund_universe(limit=limit) or []


def _default_fetch_nav(code: str, name: str, trading_days: int) -> list[NavPoint]:
    from app.services.akshare_subprocess import fetch_fund_nav_history
    from app.services.fund_factor_nav import build_total_return_index

    payload = fetch_fund_nav_history(code, trading_days=trading_days)
    if not payload or not payload.get("data"):
        return []
    series = build_total_return_index(payload["data"])
    if series.return_coverage >= 0.95:
        return_source = "daily_growth"
    elif series.return_coverage >= 0.80:
        return_source = "mixed_total_return"
    else:
        return_source = "nav_ratio_fallback"
    return [NavPoint(day, value, return_source) for day, value in series.points]


def _load_pit_snapshot_file(path: str | None) -> list[dict]:
    if not path:
        return []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []
    return _coerce_snapshot_rows(payload)


def _canonical_hash(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_nav_observation_file(
    path: str,
) -> tuple[dict[str, list[NavPoint]], dict]:
    from app.services.fund_factor_nav import build_total_return_index

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("observations")
    if (
        payload.get("schema_version") != "factor_ic_nav_observation_history.v1"
        or payload.get("point_in_time_scope") != "nav_observation_pit"
        or payload.get("nav_revision_pit") is not True
        or payload.get("availability_basis") != "collector_first_observed_at"
        or payload.get("revision_policy") != "first_observed_value"
        or not isinstance(rows, list)
        or payload.get("content_hash") != _canonical_hash(rows)
    ):
        raise ValueError("NAV first-observation artifact contract is invalid")
    try:
        artifact_as_of = datetime.fromisoformat(
            str(payload.get("as_of") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("NAV first-observation artifact as_of is invalid") from exc
    if artifact_as_of.tzinfo is None:
        raise ValueError("NAV first-observation artifact as_of lacks timezone")
    artifact_as_of = artifact_as_of.astimezone(timezone.utc)

    grouped: dict[str, list[dict]] = {}
    observed_by_code_day: dict[tuple[str, str], str] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("NAV first-observation row is not an object")
        code = str(row.get("fund_code") or "")
        nav_date = str(row.get("nav_date") or "")[:10]
        observed_at = str(row.get("first_observed_at") or "")
        available_at = str(row.get("available_at") or "")
        if len(code) != 6 or not code.isdigit() or len(nav_date) != 10:
            raise ValueError("NAV first-observation row identity is invalid")
        if available_at != observed_at:
            raise ValueError("NAV availability must equal first_observed_at")
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("NAV first_observed_at is invalid") from exc
        if observed.tzinfo is None or observed.astimezone(timezone.utc) > artifact_as_of:
            raise ValueError("NAV first_observed_at exceeds artifact as_of")
        key = (code, nav_date)
        if key in seen:
            raise ValueError("NAV first-observation artifact contains a revision duplicate")
        seen.add(key)
        grouped.setdefault(code, []).append(
            {
                "date": nav_date,
                "nav": row.get("unit_nav"),
                "daily_growth": row.get("daily_growth_percent"),
            }
        )
        observed_by_code_day[key] = observed.astimezone(timezone.utc).isoformat()

    panel: dict[str, list[NavPoint]] = {}
    for code, raw_points in grouped.items():
        series = build_total_return_index(raw_points)
        if series.return_coverage >= 0.95:
            return_source = "first_observed_daily_growth"
        elif series.return_coverage >= 0.80:
            return_source = "first_observed_mixed_total_return"
        else:
            return_source = "first_observed_nav_ratio"
        points = [
            NavPoint(
                day,
                value,
                return_source,
                observed_by_code_day[(code, day)],
            )
            for day, value in series.points
        ]
        if points:
            panel[code] = points
    metadata = {
        "fund_code_count": int(payload.get("fund_code_count") or 0),
        "observation_count": int(payload.get("observation_count") or 0),
        "loaded_fund_count": len(panel),
        "as_of": artifact_as_of.isoformat(),
        "availability_basis": payload["availability_basis"],
        "revision_policy": payload["revision_policy"],
    }
    return panel, metadata


def _coerce_snapshot_rows(payload) -> list[dict]:
    rows = payload.get("snapshots") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _verdict(stats) -> str:
    if stats.mean_ic is None or stats.n_periods == 0:
        return "样本不足"
    if not stats.significant:
        return "不显著"
    if abs(stats.mean_ic) < 0.02:
        return "极弱"
    direction = "正向" if stats.mean_ic > 0 else "反向"
    return f"{direction}有效 ✓"


def _render_report(
    result,
    *,
    run_date: str,
    universe_effective: int,
    rebalance_step: int,
    caveats: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"因子有效性回测 (Rank IC)  运行: {run_date}")
    lines.append(
        f"池: 排行榜 {universe_effective} 只 (有效)  "
        f"再平衡: 每{rebalance_step}个净值日  前瞻: {result.forward_days}日  "
        f"期数: {result.rebalance_count}"
    )
    for c in caveats:
        lines.append(f"⚠ {c}")
    lines.append("-" * 64)
    lines.append(
        f"{'因子':<8}{'mean IC':>10}{'ICIR':>8}{'t':>8}{'%>0':>8}{'n':>6}  结论"
    )
    for stats in result.factors:
        label = _FACTOR_LABEL.get(stats.factor, stats.factor)
        mean_ic = "—" if stats.mean_ic is None else f"{stats.mean_ic:+.4f}"
        icir = "—" if stats.icir is None else f"{stats.icir:+.2f}"
        t = "—" if stats.t_stat is None else f"{stats.t_stat:+.2f}"
        pos = "—" if stats.positive_ratio is None else f"{stats.positive_ratio:.2f}"
        lines.append(
            f"{label:<8}{mean_ic:>10}{icir:>8}{t:>8}{pos:>8}{stats.n_periods:>6}  {_verdict(stats)}"
        )
    return "\n".join(lines) + "\n"


def build_ic_report(
    *,
    fetch_rank=_default_fetch_rank,
    fetch_nav=_default_fetch_nav,
    fetch_pit_snapshots=None,
    out_dir: str = _DEFAULT_OUT_DIR,
    universe_size: int = 300,
    nav_days: int = 750,
    rebalance_step: int = DEFAULT_REBALANCE_STEP,
    forward_days: int = DEFAULT_FORWARD_DAYS,
    factor_lookback: int = DEFAULT_FACTOR_LOOKBACK,
    max_workers: int = 8,
    limit_funds: int | None = None,
    universe_mode: str = "top",
    sample_pool_size: int = 500,
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS,
    pit_mode: str = "auto",
    pit_history_days: int = 1600,
    pit_max_snapshot_age_days: int = 7,
    pit_walk_forward_folds: int = 5,
    pit_embargo_trading_days: int = 20,
    universe_snapshots: list[dict] | None = None,
    pit_snapshot_file: str | None = None,
    nav_observation_history_file: str | None = None,
    nav_observation_panel: dict[str, list[NavPoint]] | None = None,
) -> dict:
    """取数 → 组面板 → 跑引擎 → 落盘 report.txt + summary.json，返回结果 dict。

    universe_mode:
      - "top"（默认）：取排行榜前 universe_size 名（偏强样本，行为不变）。
      - "sampled"：取前 sample_pool_size 名作大池，再跨业绩段等距抽样。
      - "stratified"：拉全目录、份额去重后按基金类别和业绩分位抽样。
    """
    rank_limit = (
        sample_pool_size
        if universe_mode in {"sampled", "stratified"}
        else universe_size
    )
    rank_candidates = fetch_rank(rank_limit) or []
    if not rank_candidates:
        raise FactorIcRankUnavailable(
            f"开放式基金全量目录获取失败（请求 {rank_limit} 条）"
        )
    all_rank_rows = rank_candidates
    if universe_mode == "stratified":
        from app.services.fund_universe_sampler import stratified_sample_universe

        rank_rows = stratified_sample_universe(rank_candidates, universe_size)
    elif universe_mode == "sampled":
        from app.services.fund_universe_sampler import sample_universe

        rank_rows = sample_universe(rank_candidates, universe_size)
    else:
        rank_rows = rank_candidates
    base_codes = [
        (row["fund_code"], row.get("fund_name", ""))
        for row in rank_rows
        if row.get("fund_code")
    ]
    snapshots: list[dict] = []
    if universe_mode == "stratified" and pit_mode != "off":
        snapshot_payload = (
            universe_snapshots
            if universe_snapshots is not None
            else _load_pit_snapshot_file(pit_snapshot_file)
            or (
                fetch_pit_snapshots(pit_history_days)
                if fetch_pit_snapshots
                else []
            )
        )
        snapshots = _coerce_snapshot_rows(snapshot_payload)
    observation_panel = nav_observation_panel or {}
    observation_metadata: dict = {
        "loaded_fund_count": len(observation_panel),
        "observation_count": sum(len(points) for points in observation_panel.values()),
        "availability_basis": "collector_first_observed_at",
        "revision_policy": "first_observed_value",
    }
    observation_failure_reason = None
    if nav_observation_panel is None and nav_observation_history_file:
        try:
            observation_panel, observation_metadata = _load_nav_observation_file(
                nav_observation_history_file
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            observation_panel = {}
            observation_failure_reason = "nav_observation_history_invalid"
    fetch_items: dict[str, tuple[str, str]] = {
        str(code): (str(code), str(name or "")) for code, name in base_codes
    }
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        members = snapshot.get("members") or []
        if isinstance(members, dict):
            members = [
                {"fund_code": code, **(row if isinstance(row, dict) else {})}
                for code, row in members.items()
            ]
        for member in members:
            if not isinstance(member, dict):
                continue
            code = str(member.get("fund_code") or "").strip()
            if code and code not in fetch_items:
                fetch_items[code] = (code, str(member.get("fund_name") or ""))
    codes = list(fetch_items.values())
    if limit_funds is not None:
        codes = codes[:limit_funds]

    def _one(item):
        code, name = item
        try:
            return code, fetch_nav(code, name, nav_days)
        except Exception:
            return code, []

    fetched_nav_panel: dict[str, list[NavPoint]] = {}
    if codes:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(codes)))) as pool:
            for code, points in pool.map(_one, codes):
                if points and len(points) >= 2:
                    fetched_nav_panel[code] = sorted(points, key=lambda p: p.date)

    base_code_set = {str(code) for code, _ in base_codes}
    nav_panel = {
        code: points
        for code, points in fetched_nav_panel.items()
        if code in base_code_set
    }

    calendar = sorted({p.date for pts in nav_panel.values() for p in pts})

    result = compute_factor_ic(
        nav_panel=nav_panel,
        calendar=calendar,
        rebalance_step=rebalance_step,
        forward_days=forward_days,
        factor_lookback=factor_lookback,
    )
    membership_v3_candidate = None
    observation_v3_candidate = None
    pit_failure_reason = None
    if snapshots:
        try:
            membership_v3_candidate = build_v3_research_model(
                nav_panel=fetched_nav_panel,
                universe_snapshots=snapshots,
                current_all_rows=all_rank_rows,
                factor_lookback=factor_lookback,
                rebalance_step=rebalance_step,
                forward_horizons=forward_horizons,
                max_snapshot_age_days=pit_max_snapshot_age_days,
                walk_forward_folds=pit_walk_forward_folds,
                embargo_trading_days=pit_embargo_trading_days,
            )
        except Exception:
            pit_failure_reason = "pit_research_failed"
        if observation_panel:
            try:
                observation_v3_candidate = build_v3_research_model(
                    nav_panel=observation_panel,
                    universe_snapshots=snapshots,
                    current_all_rows=all_rank_rows,
                    factor_lookback=factor_lookback,
                    rebalance_step=rebalance_step,
                    forward_horizons=forward_horizons,
                    max_snapshot_age_days=pit_max_snapshot_age_days,
                    walk_forward_folds=pit_walk_forward_folds,
                    embargo_trading_days=pit_embargo_trading_days,
                    nav_observation_pit=True,
                )
            except Exception:
                observation_failure_reason = "nav_observation_research_failed"
    observation_publishable = bool(
        observation_v3_candidate
        and is_v3_research_model_publishable(observation_v3_candidate)
    )
    membership_publishable = bool(
        membership_v3_candidate
        and is_v3_research_model_publishable(membership_v3_candidate)
    )
    v3_candidate = (
        observation_v3_candidate
        if observation_publishable
        else membership_v3_candidate
    )
    use_v3 = observation_publishable or membership_publishable
    use_nav_observation_v3 = observation_publishable
    research_model = (
        v3_candidate
        if use_v3
        else build_research_model(
            nav_panel=nav_panel,
            sampled_rows=rank_rows,
            all_rows=all_rank_rows,
            factor_lookback=factor_lookback,
            rebalance_step=rebalance_step,
            forward_horizons=forward_horizons,
        )
    )
    from app.services.fund_universe_sampler import universe_coverage

    coverage = universe_coverage(all_rank_rows, rank_rows)
    coverage_panel = observation_panel if use_nav_observation_v3 else nav_panel
    coverage["effective_nav_portfolios"] = len(coverage_panel)
    coverage["effective_nav_rate"] = (
        round(len(coverage_panel) / len(rank_rows), 4) if rank_rows else 0.0
    )
    nav_source_counts: dict[str, int] = {}
    for points in coverage_panel.values():
        source = str(points[0].return_source or "injected_or_unknown") if points else "empty"
        nav_source_counts[source] = nav_source_counts.get(source, 0) + 1
    coverage["nav_return_source_counts"] = nav_source_counts
    preferred_count = sum(
        nav_source_counts.get(key, 0)
        for key in (
            "daily_growth",
            "mixed_total_return",
            "injected_or_unknown",
            "first_observed_daily_growth",
            "first_observed_mixed_total_return",
        )
    )
    coverage["total_return_preferred_portfolios"] = preferred_count
    coverage["total_return_preferred_rate"] = (
        round(preferred_count / len(coverage_panel), 4) if coverage_panel else 0.0
    )

    generated_at = datetime.now(timezone.utc)
    run_date = generated_at.date().isoformat()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    caveats = (
        _V3_NAV_OBSERVATION_CAVEATS
        if use_nav_observation_v3
        else _V3_CAVEATS
        if use_v3
        else _V2_CAVEATS if universe_mode == "stratified" else _CAVEATS
    )
    report = _render_report(
        result,
        run_date=run_date,
        universe_effective=len(nav_panel),
        rebalance_step=rebalance_step,
        caveats=caveats,
    )
    (out_path / "report.txt").write_text(report, encoding="utf-8")

    params = {
        "universe_size": universe_size,
        "universe_mode": universe_mode,
        "sample_pool_size": sample_pool_size,
        "nav_days": nav_days,
        "rebalance_step": rebalance_step,
        "forward_days": forward_days,
        "factor_lookback": factor_lookback,
    }
    if universe_mode == "stratified":
        params["forward_horizons"] = list(forward_horizons)
    if use_v3:
        params.update(
            {
                "pit_history_days": pit_history_days,
                "pit_max_snapshot_age_days": pit_max_snapshot_age_days,
                "pit_walk_forward_folds": pit_walk_forward_folds,
                "pit_embargo_trading_days": pit_embargo_trading_days,
                "point_in_time_scope": (
                    "nav_observation_pit"
                    if use_nav_observation_v3
                    else "membership_only"
                ),
            }
        )

    summary = {
        "schema_version": (
            POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION
            if use_v3
            else V2_FACTOR_IC_SCHEMA_VERSION
            if universe_mode == "stratified"
            else FACTOR_IC_SCHEMA_VERSION
        ),
        "run_date": run_date,
        "generated_at": generated_at.isoformat(),
        "params": params,
        "available": result.available,
        "message": result.message,
        "universe_size": result.universe_size,
        "rebalance_count": result.rebalance_count,
        "forward_days": result.forward_days,
        "caveats": caveats,
        "factors": [
            {k: v for k, v in asdict(stats).items() if k != "ic_series"}
            for stats in result.factors
        ],
        "coverage": coverage,
        "research_model": research_model,
    }
    if universe_mode == "stratified" and not use_v3:
        pit = (membership_v3_candidate or {}).get("point_in_time") or {}
        summary["pit_upgrade"] = {
            "state": "collecting" if snapshots else "unavailable",
            "snapshot_count": len(snapshots),
            "effective_anchor_count": pit.get("effective_anchor_count", 0),
            "anchor_coverage_rate": pit.get("anchor_coverage_rate", 0.0),
            "cohort_nav_coverage_rate": pit.get("cohort_nav_coverage_rate", 0.0),
            "reason": pit_failure_reason
            or (
                "v3_quality_gate_not_met"
                if snapshots
                else "pit_snapshot_history_unavailable"
            ),
        }
    if universe_mode == "stratified":
        observation_pit = (
            (observation_v3_candidate or {}).get("point_in_time") or {}
        )
        if use_nav_observation_v3:
            observation_state = "active"
            observation_reason = "nav_observation_pit_publishable"
        elif observation_failure_reason:
            observation_state = "unavailable"
            observation_reason = observation_failure_reason
        elif observation_panel:
            observation_state = "collecting"
            observation_reason = "nav_observation_quality_gate_not_met"
        else:
            observation_state = "not_started"
            observation_reason = "nav_observation_history_empty"
        summary["nav_observation_upgrade"] = {
            "state": observation_state,
            "reason": observation_reason,
            "publishable": use_nav_observation_v3,
            "loaded_fund_count": int(
                observation_metadata.get("loaded_fund_count") or 0
            ),
            "observation_count": int(
                observation_metadata.get("observation_count") or 0
            ),
            "as_of": observation_metadata.get("as_of"),
            "availability_basis": "collector_first_observed_at",
            "revision_policy": "first_observed_value",
            "effective_anchor_count": int(
                observation_pit.get("effective_anchor_count") or 0
            ),
            "cohort_nav_coverage_rate": float(
                observation_pit.get("cohort_nav_coverage_rate") or 0
            ),
            "observation_timestamp_coverage_rate": float(
                observation_pit.get("observation_timestamp_coverage_rate") or 0
            ),
            "automatic_promotion_allowed": False,
        }
    (out_path / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="因子有效性回测 (Rank IC)")
    parser.add_argument("--universe-size", type=int, default=300)
    parser.add_argument(
        "--universe-mode", choices=["top", "sampled", "stratified"], default="top",
        help="top=榜单前N; sampled=大池等距抽样; stratified=全目录去重分类抽样",
    )
    parser.add_argument("--sample-pool-size", type=int, default=500)
    parser.add_argument("--nav-days", type=int, default=750)
    parser.add_argument("--rebalance-step", type=int, default=DEFAULT_REBALANCE_STEP)
    parser.add_argument("--forward-days", type=int, default=DEFAULT_FORWARD_DAYS)
    parser.add_argument("--factor-lookback", type=int, default=DEFAULT_FACTOR_LOOKBACK)
    parser.add_argument("--forward-horizons", type=str, default="5,20,60")
    parser.add_argument(
        "--pit-mode",
        choices=["auto", "off"],
        default="auto",
        help="auto=有足够PIT快照时发布v3，不足自动降级v2；off=禁用PIT研究",
    )
    parser.add_argument("--pit-history-days", type=int, default=1600)
    parser.add_argument("--pit-max-snapshot-age-days", type=int, default=7)
    parser.add_argument("--pit-walk-forward-folds", type=int, default=5)
    parser.add_argument("--pit-embargo-trading-days", type=int, default=20)
    parser.add_argument(
        "--pit-history",
        "--pit-snapshot-file",
        dest="pit_snapshot_file",
        type=str,
        default=None,
        help="可选的PIT基金池快照JSON；缺失或不足时自动降级v2",
    )
    parser.add_argument(
        "--nav-observation-history",
        dest="nav_observation_history_file",
        type=str,
        default=None,
        help="optional append-only NAV first-observation history JSON",
    )
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--limit-funds", type=int, default=None, help="调试用，限制只数")
    parser.add_argument("--out-dir", type=str, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()
    forward_horizons = tuple(
        sorted({int(value) for value in args.forward_horizons.split(",") if value.strip()})
    )

    try:
        summary = build_ic_report(
            out_dir=args.out_dir,
            universe_size=args.universe_size,
            universe_mode=args.universe_mode,
            sample_pool_size=args.sample_pool_size,
            nav_days=args.nav_days,
            rebalance_step=args.rebalance_step,
            forward_days=args.forward_days,
            factor_lookback=args.factor_lookback,
            forward_horizons=forward_horizons,
            pit_mode=args.pit_mode,
            pit_history_days=args.pit_history_days,
            pit_max_snapshot_age_days=args.pit_max_snapshot_age_days,
            pit_walk_forward_folds=args.pit_walk_forward_folds,
            pit_embargo_trading_days=args.pit_embargo_trading_days,
            pit_snapshot_file=args.pit_snapshot_file,
            nav_observation_history_file=args.nav_observation_history_file,
            max_workers=args.max_workers,
            limit_funds=args.limit_funds,
        )
    except FactorIcRankUnavailable as exc:
        print(f"factor IC generation failed: {exc}", file=sys.stderr)
        return 2
    console_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"research_model"}
    }
    model = summary.get("research_model") or {}
    console_summary["segments"] = {
        key: {
            "label": segment.get("label"),
            "sampled_portfolios": segment.get("sampled_portfolios"),
            "primary": (segment.get("horizons") or {}).get("20"),
        }
        for key, segment in (model.get("segments") or {}).items()
    }
    print(json.dumps(console_summary, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {Path(args.out_dir) / 'report.txt'}")
    return 0 if summary["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
