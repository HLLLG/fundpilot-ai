"""因子 IC 快照的版本化发布契约与质量门槛。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FACTOR_IC_SCHEMA_VERSION = 1
V2_FACTOR_IC_SCHEMA_VERSION = 2
POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION = 3
CURRENT_FACTOR_IC_SCHEMA_VERSION = POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION
FACTOR_NAMES = frozenset({"momentum", "risk_adjusted", "drawdown", "composite"})
EXPECTED_PARAMS = {
    "universe_size": 300,
    "universe_mode": "sampled",
    "sample_pool_size": 500,
    "nav_days": 750,
    "rebalance_step": 21,
    "forward_days": 20,
    "factor_lookback": 250,
}
V2_EXPECTED_PARAMS = {
    "universe_size": 1500,
    "universe_mode": "stratified",
    "sample_pool_size": 25000,
    "nav_days": 1500,
    "rebalance_step": 10,
    "forward_days": 20,
    "factor_lookback": 250,
    "forward_horizons": [5, 20, 60],
}
V3_EXPECTED_PARAMS = {
    **V2_EXPECTED_PARAMS,
    "pit_history_days": 1600,
    "pit_max_snapshot_age_days": 7,
    "pit_walk_forward_folds": 5,
    "pit_embargo_trading_days": 20,
}
MIN_EFFECTIVE_UNIVERSE = 240
V2_MIN_EFFECTIVE_UNIVERSE = 1200
MIN_VALID_PERIODS = 12
API_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY_PATH = API_ROOT / "var" / "factor_ic" / "summary.json"

FactorIcEvidenceState = Literal["unavailable", "stale", "available"]


class FactorIcNewerSnapshotExists(RuntimeError):
    pass


class FactorIcStorageUnavailable(RuntimeError):
    pass


class FactorIcParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_size: int
    universe_mode: Literal["top", "sampled", "stratified"]
    sample_pool_size: int
    nav_days: int
    rebalance_step: int
    forward_days: int
    factor_lookback: int
    forward_horizons: list[int] | None = None
    pit_history_days: int | None = None
    pit_max_snapshot_age_days: int | None = None
    pit_walk_forward_folds: int | None = None
    pit_embargo_trading_days: int | None = None


class FactorIcFactorStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    factor: Literal["momentum", "risk_adjusted", "drawdown", "composite"]
    n_periods: int
    mean_ic: float | None
    ic_std: float | None = None
    icir: float | None = None
    t_stat: float | None = None
    positive_ratio: float | None = None
    significant: bool
    standard_error: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    oos_mean_ic: float | None = None
    oos_positive_ratio: float | None = None
    direction_stable: bool = False

    @model_validator(mode="after")
    def validate_statistics(self) -> "FactorIcFactorStats":
        if self.n_periods < MIN_VALID_PERIODS:
            raise ValueError(f"{self.factor} 有效期数不足 {MIN_VALID_PERIODS}")
        if (
            self.mean_ic is None
            or not math.isfinite(self.mean_ic)
            or not -1 <= self.mean_ic <= 1
        ):
            raise ValueError(f"{self.factor} mean_ic 非法")
        for name in (
            "ic_std", "icir", "t_stat", "positive_ratio", "standard_error",
            "ci_low", "ci_high", "oos_mean_ic", "oos_positive_ratio",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{self.factor} {name} 必须是有限数字")
        if self.positive_ratio is not None and not 0 <= self.positive_ratio <= 1:
            raise ValueError(f"{self.factor} positive_ratio 非法")
        if self.oos_positive_ratio is not None and not 0 <= self.oos_positive_ratio <= 1:
            raise ValueError(f"{self.factor} oos_positive_ratio 非法")
        if self.oos_mean_ic is not None and not -1 <= self.oos_mean_ic <= 1:
            raise ValueError(f"{self.factor} oos_mean_ic 非法")
        return self


class FactorIcSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int
    run_date: date
    generated_at: datetime
    params: FactorIcParams
    available: bool
    universe_size: int
    rebalance_count: int
    forward_days: int
    factors: list[FactorIcFactorStats]
    coverage: dict[str, Any] | None = None
    research_model: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_quality(self) -> "FactorIcSummary":
        if self.schema_version not in {
            FACTOR_IC_SCHEMA_VERSION,
            V2_FACTOR_IC_SCHEMA_VERSION,
            CURRENT_FACTOR_IC_SCHEMA_VERSION,
        }:
            raise ValueError("不支持的 factor IC schema_version")
        params = self.params.model_dump(exclude_none=True)
        if self.schema_version == POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION:
            expected_params = V3_EXPECTED_PARAMS
        elif self.schema_version == V2_FACTOR_IC_SCHEMA_VERSION:
            expected_params = V2_EXPECTED_PARAMS
        else:
            expected_params = EXPECTED_PARAMS
        if params != expected_params:
            raise ValueError("回测参数不是固定生产口径")
        if not self.available:
            raise ValueError("回测结果不可用")
        minimum_universe = (
            V2_MIN_EFFECTIVE_UNIVERSE
            if self.schema_version in {
                V2_FACTOR_IC_SCHEMA_VERSION,
                POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION,
            }
            else MIN_EFFECTIVE_UNIVERSE
        )
        if self.universe_size < minimum_universe:
            raise ValueError(f"有效基金数不足 {minimum_universe}")
        if self.rebalance_count < MIN_VALID_PERIODS:
            raise ValueError(f"回测期数不足 {MIN_VALID_PERIODS}")
        names = [row.factor for row in self.factors]
        if len(names) != len(FACTOR_NAMES) or set(names) != FACTOR_NAMES:
            raise ValueError("四个因子必须齐全且不可重复")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at 必须包含时区")
        if self.run_date != self.generated_at.astimezone(timezone.utc).date():
            raise ValueError("run_date 必须等于 generated_at 的 UTC 日期")
        if self.schema_version == V2_FACTOR_IC_SCHEMA_VERSION:
            self._validate_v2_research_model()
        elif self.schema_version == POINT_IN_TIME_FACTOR_IC_SCHEMA_VERSION:
            self._validate_v3_research_model()
        return self

    def _validate_v2_research_model(self) -> None:
        coverage = self.coverage or {}
        if int(coverage.get("source_share_classes") or 0) < 5_000:
            raise ValueError("v2 全量基金目录覆盖不足")
        if int(coverage.get("unique_portfolios") or 0) < self.universe_size:
            raise ValueError("v2 去重基金组合数不足")
        if (
            int(coverage.get("effective_nav_portfolios") or 0)
            < V2_MIN_EFFECTIVE_UNIVERSE
        ):
            raise ValueError("v2 有效总收益序列不足")
        if float(coverage.get("total_return_preferred_rate") or 0) < 0.8:
            raise ValueError("v2 总收益口径覆盖率不足 80%")

        model = self.research_model or {}
        if model.get("version") != "factor_ic.v2":
            raise ValueError("v2 research_model 版本非法")
        if model.get("cohort_mode") != "current_survivors":
            raise ValueError("v2 cohort_mode 非法")
        primary_horizon = str(model.get("primary_horizon") or 20)
        segments = model.get("segments")
        peers = model.get("peer_distributions")
        classifications = model.get("fund_classifications")
        if not isinstance(segments, dict) or len(segments) < 4:
            raise ValueError("v2 分类 IC 覆盖不足")
        if not isinstance(peers, dict) or len(peers) < 4:
            raise ValueError("v2 同类分布覆盖不足")
        if not isinstance(classifications, dict) or len(classifications) < 5_000:
            raise ValueError("v2 基金分类映射覆盖不足")
        primary_horizon = str(model.get("primary_horizon") or 20)
        qualified_segments = 0
        for key, segment in segments.items():
            if not isinstance(segment, dict):
                continue
            horizon = (segment.get("horizons") or {}).get(primary_horizon) or {}
            qualified = horizon.get("qualified") or {}
            peer = peers.get(key) if isinstance(peers.get(key), dict) else {}
            if any(bool(value) for value in qualified.values()) and int(
                peer.get("eligible_count") or 0
            ) >= 20:
                qualified_segments += 1
        if qualified_segments < 4:
            raise ValueError("v2 主周期合格同类组不足 4 类")

    def _validate_v3_research_model(self) -> None:
        coverage = self.coverage or {}
        if int(coverage.get("source_share_classes") or 0) < 5_000:
            raise ValueError("v3 全量基金目录覆盖不足")
        if (
            int(coverage.get("effective_nav_portfolios") or 0)
            < V2_MIN_EFFECTIVE_UNIVERSE
        ):
            raise ValueError("v3 有效总收益序列不足")
        if float(coverage.get("total_return_preferred_rate") or 0) < 0.8:
            raise ValueError("v3 总收益口径覆盖率不足 80%")

        model = self.research_model or {}
        if model.get("version") != "factor_ic.v3":
            raise ValueError("v3 research_model 版本非法")
        if model.get("cohort_mode") != "point_in_time":
            raise ValueError("v3 cohort_mode 必须是 point_in_time")
        point_in_time = model.get("point_in_time")
        if not isinstance(point_in_time, dict):
            raise ValueError("v3 point_in_time 元数据缺失")
        if (
            point_in_time.get("ready") is not True
            or point_in_time.get("publishable") is not True
        ):
            raise ValueError("v3 point-in-time 尚未达到可发布状态")
        if int(point_in_time.get("effective_anchor_count") or 0) < 24:
            raise ValueError("v3 point-in-time 有效锚点不足 24")
        if float(point_in_time.get("anchor_coverage_rate") or 0) < 0.90:
            raise ValueError("v3 point-in-time 锚点覆盖率不足 90%")
        if float(point_in_time.get("cohort_nav_coverage_rate") or 0) < 0.90:
            raise ValueError("v3 point-in-time cohort 净值覆盖率不足 90%")
        if int(point_in_time.get("future_snapshot_violations") or 0) != 0:
            raise ValueError("v3 检测到未来快照穿越")
        if int(point_in_time.get("max_snapshot_age_days") or 99) > 7:
            raise ValueError("v3 快照最大陈旧度超过 7 日")
        if int(point_in_time.get("walk_forward_folds") or 0) != 5:
            raise ValueError("v3 必须使用 5 折 expanding walk-forward")
        if int(point_in_time.get("embargo_trading_days") or 0) != 20:
            raise ValueError("v3 必须使用 20 交易日 embargo")
        if point_in_time.get("multiple_testing") != "benjamini_hochberg":
            raise ValueError("v3 必须使用 Benjamini-Hochberg 多重检验校正")
        if float(point_in_time.get("fdr_q_threshold") or 99) != 0.10:
            raise ValueError("v3 FDR q 阈值必须为 0.10")
        if point_in_time.get("point_in_time_scope") != "membership_only":
            raise ValueError("v3 当前 PIT 范围必须明确为 membership_only")
        if point_in_time.get("nav_revision_pit") is not False:
            raise ValueError("v3 当前不得声称 NAV 修订时点已 PIT 化")
        if point_in_time.get("nav_publication_lag_trading_days") != {
            "default": 1,
            "qdii": 2,
        }:
            raise ValueError("v3 NAV 发布滞后口径非法")
        if int(point_in_time.get("execution_entry_offset_trading_days") or 0) != 1:
            raise ValueError("v3 必须使用下一交易日可执行 NAV 入场")

        primary_horizon = str(model.get("primary_horizon") or 20)
        mature_counts = point_in_time.get("mature_anchor_count_by_horizon")
        mature_rates = point_in_time.get("mature_anchor_coverage_rate_by_horizon")
        horizon_ready = point_in_time.get("horizon_ready")
        if (
            not isinstance(mature_counts, dict)
            or not isinstance(mature_rates, dict)
            or not isinstance(horizon_ready, dict)
        ):
            raise ValueError("v3 各周期成熟度元数据缺失")
        mature_count = int(mature_counts.get(primary_horizon) or 0)
        effective_anchor_count = int(
            point_in_time.get("effective_anchor_count") or 0
        )
        expected_mature_rate = (
            round(mature_count / effective_anchor_count, 4)
            if effective_anchor_count
            else 0.0
        )
        if mature_count < 24 or horizon_ready.get(primary_horizon) is not True:
            raise ValueError("v3 主周期成熟锚点不足 24")
        if float(mature_rates.get(primary_horizon) or 0) != expected_mature_rate:
            raise ValueError("v3 主周期成熟锚点覆盖率非法")
        if int(point_in_time.get("primary_maturity_horizon") or 0) != int(
            primary_horizon
        ):
            raise ValueError("v3 主周期与成熟度门槛不一致")

        segments = model.get("segments")
        peers = model.get("peer_distributions")
        classifications = model.get("fund_classifications")
        if not isinstance(segments, dict) or len(segments) < 4:
            raise ValueError("v3 分类 PIT IC 覆盖不足")
        if not isinstance(peers, dict) or len(peers) < 4:
            raise ValueError("v3 PIT 同类分布覆盖不足")
        if not isinstance(classifications, dict) or len(classifications) < 5_000:
            raise ValueError("v3 当前 PIT 基金分类映射覆盖不足")
        pit_coverage = model.get("pit_coverage")
        validation = model.get("validation")
        economic_contract = model.get("economic_significance")
        if not isinstance(pit_coverage, dict):
            raise ValueError("v3 pit_coverage 缺失")
        for key in (
            "effective_anchor_count",
            "anchor_coverage_rate",
            "cohort_nav_coverage_rate",
            "future_snapshot_violations",
            "point_in_time_scope",
            "nav_revision_pit",
            "nav_publication_lag_trading_days",
            "execution_entry_offset_trading_days",
            "mature_anchor_count_by_horizon",
            "mature_anchor_coverage_rate_by_horizon",
            "horizon_ready",
            "primary_maturity_horizon",
        ):
            if pit_coverage.get(key) != point_in_time.get(key):
                raise ValueError(f"v3 pit_coverage.{key} 与 point_in_time 不一致")
        if not isinstance(validation, dict) or validation != {
            "method": "expanding_walk_forward",
            "folds": 5,
            "embargo_trading_days": 20,
            "multiple_test": "benjamini_hochberg",
            "fdr_q_threshold": 0.10,
        }:
            raise ValueError("v3 validation 口径非法")
        if not isinstance(economic_contract, dict) or economic_contract != {
            "schema_version": "factor_economic_significance.v1",
            "label_type": "peer_group_relative_total_return",
            "benchmark": "same_segment_cross_section_median",
            "point_in_time_scope": "membership_only",
            "nav_revision_pit": False,
            "entry_rule": "next_trading_day_first_available_nav",
            "entry_offset_trading_days": 1,
            "quantiles": 5,
            "cost_rates": [0.0, 0.005, 0.01],
            "qualification_cost_rate": 0.005,
            "minimum_periods": 36,
            "minimum_coverage_rate": 0.80,
            "minimum_top_net_positive_ratio": 0.55,
        }:
            raise ValueError("v3 经济显著性口径非法")

        qualified_segments = 0
        for key, segment in segments.items():
            if not isinstance(segment, dict):
                continue
            horizon = (segment.get("horizons") or {}).get(primary_horizon) or {}
            maturity = horizon.get("maturity") or {}
            if (
                int(maturity.get("mature_anchor_count") or 0) < 24
                or maturity.get("ready") is not True
            ):
                raise ValueError(f"v3 {key} 主周期回测尚未成熟")
            factors = {
                str(row.get("factor")): row
                for row in (horizon.get("factors") or [])
                if isinstance(row, dict) and row.get("factor")
            }
            qualified = horizon.get("qualified") or {}
            type_factor_model = segment.get("type_factor_model") or {}
            if type_factor_model.get("schema_version") != "fund_type_factors.v1":
                raise ValueError(f"v3 {key} 类型因子口径缺失")
            if type_factor_model.get("orientation") != "higher_is_better":
                raise ValueError(f"v3 {key} 类型因子方向口径非法")
            if type_factor_model.get("size_role") != "capacity_risk_guard_only":
                raise ValueError(f"v3 {key} 规模不得作为收益因子")
            expected_lag = 2 if key == "qdii" else 1
            if int(type_factor_model.get("nav_information_lag_trading_days") or 0) != expected_lag:
                raise ValueError(f"v3 {key} NAV 信息滞后口径非法")
            if type_factor_model.get("nav_revision_pit") is not False:
                raise ValueError(f"v3 {key} 不得声称 NAV 修订已 PIT 化")
            if key == "zs" and (
                (type_factor_model.get("tracking_evidence") or {}).get("status")
                != "insufficient"
            ):
                raise ValueError("v3 指数基金缺精确基准时 tracking 必须标记不足")
            segment_has_qualified = False
            for factor, is_qualified in qualified.items():
                if not is_qualified:
                    continue
                row = factors.get(str(factor)) or {}
                walk = row.get("walk_forward") or {}
                q_value = row.get("q_value")
                economic = row.get("economic_significance") or {}
                economic_walk = economic.get("walk_forward") or {}
                cost_scenarios = {
                    float(item.get("fee_rate")): item
                    for item in economic.get("cost_scenarios") or []
                    if isinstance(item, dict) and item.get("fee_rate") is not None
                }
                quintile_returns = economic.get("quintile_mean_relative_returns")
                costs_are_finite = bool(
                    set(cost_scenarios) == {0.0, 0.005, 0.01}
                    and all(
                        item.get("top_net_relative_return") is not None
                        and item.get("spread_net_return") is not None
                        and math.isfinite(float(item["top_net_relative_return"]))
                        and math.isfinite(float(item["spread_net_return"]))
                        for item in cost_scenarios.values()
                    )
                )
                if not (
                    int(row.get("n_periods") or 0) >= 30
                    and float(walk.get("oos_mean_ic") or -99) >= 0.02
                    and float(row.get("icir") or -99) >= 0.20
                    and int(walk.get("fold_count") or 0) == 5
                    and int(walk.get("valid_fold_count") or 0) == 5
                    and int(walk.get("embargo_trading_days") or 0) == 20
                    and int(walk.get("same_direction_folds") or 0) >= 4
                    and row.get("ci_low") is not None
                    and float(row["ci_low"]) > 0
                    and q_value is not None
                    and math.isfinite(float(q_value))
                    and float(q_value) <= 0.10
                    and row.get("qualified") is True
                    and economic.get("schema_version")
                    == "factor_economic_significance.v1"
                    and economic.get("label_type")
                    == "peer_group_relative_total_return"
                    and economic.get("benchmark")
                    == "same_segment_cross_section_median"
                    and economic.get("point_in_time_scope") == "membership_only"
                    and economic.get("nav_revision_pit") is False
                    and economic.get("entry_rule")
                    == "next_trading_day_first_available_nav"
                    and int(economic.get("entry_offset_trading_days") or 0) == 1
                    and int(economic.get("quantile_count") or 0) == 5
                    and int(economic.get("period_count") or 0) >= 36
                    and int(economic.get("valid_observation_count") or 0) > 0
                    and float(economic.get("peer_relative_coverage_rate") or 0)
                    >= 0.80
                    and economic.get("top_quantile_relative_return") is not None
                    and float(economic["top_quantile_relative_return"]) > 0
                    and economic.get("bottom_quantile_relative_return") is not None
                    and math.isfinite(
                        float(economic["bottom_quantile_relative_return"])
                    )
                    and economic.get("top_bottom_spread") is not None
                    and float(economic["top_bottom_spread"]) > 0
                    and economic.get("standard_error") is not None
                    and math.isfinite(float(economic["standard_error"]))
                    and economic.get("t_stat") is not None
                    and math.isfinite(float(economic["t_stat"]))
                    and economic.get("ci_low") is not None
                    and float(economic["ci_low"]) > 0
                    and float(economic.get("top_net_positive_ratio") or 0)
                    >= 0.55
                    and float(economic.get("top_net_positive_cost_rate") or -1)
                    == 0.005
                    and isinstance(quintile_returns, list)
                    and len(quintile_returns) == 5
                    and all(
                        value is not None and math.isfinite(float(value))
                        for value in quintile_returns
                    )
                    and float(economic.get("quintile_monotonicity") or 0)
                    >= 0.50
                    and economic.get("turnover") is not None
                    and 0 <= float(economic["turnover"]) <= 1
                    and economic.get("break_even_fee_rate") is not None
                    and float(economic["break_even_fee_rate"]) > 0
                    and costs_are_finite
                    and cost_scenarios[0.005].get("top_net_relative_return")
                    is not None
                    and float(
                        cost_scenarios[0.005]["top_net_relative_return"]
                    )
                    > 0
                    and economic.get("top_relative_return_p10") is not None
                    and economic.get("top_relative_return_worst") is not None
                    and math.isfinite(float(economic["top_relative_return_p10"]))
                    and math.isfinite(float(economic["top_relative_return_worst"]))
                    and float(economic["top_relative_return_worst"])
                    <= float(economic["top_relative_return_p10"])
                    and economic.get("downside_distribution_unit")
                    == "anchor_top_quantile_mean"
                    and int(economic_walk.get("fold_count") or 0) == 5
                    and int(economic_walk.get("valid_fold_count") or 0) == 5
                    and int(economic_walk.get("embargo_trading_days") or 0) == 20
                    and int(economic_walk.get("same_direction_folds") or 0) >= 4
                    and economic_walk.get("oos_mean_spread") is not None
                    and float(economic_walk["oos_mean_spread"]) > 0
                    and economic.get("qualified") is True
                ):
                    raise ValueError(f"v3 {key}/{factor} 合格标记未满足严格门槛")
                segment_has_qualified = True
            peer = peers.get(key) if isinstance(peers.get(key), dict) else {}
            if segment_has_qualified and int(peer.get("eligible_count") or 0) >= 20:
                qualified_segments += 1
        if qualified_segments < 4:
            raise ValueError("v3 主周期合格 PIT 同类组不足 4 类")


class FactorIcPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: FactorIcSummary
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    source_run_id: str = Field(min_length=1, max_length=64)


def validate_publish_request(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> FactorIcPublishRequest:
    request = FactorIcPublishRequest.model_validate(payload)
    current = now or datetime.now(timezone.utc)
    generated = request.summary.generated_at.astimezone(timezone.utc)
    if generated > current + timedelta(minutes=5):
        raise ValueError("generated_at 不能来自未来")
    if generated < current - timedelta(hours=24):
        raise ValueError("generated_at 已超过 24 小时")
    return request


def _canonical_summary(
    request: FactorIcPublishRequest,
) -> tuple[dict[str, Any], str, str]:
    summary = request.summary.model_dump(mode="json")
    summary["generated_at"] = request.summary.generated_at.astimezone(
        timezone.utc
    ).isoformat()
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot_id = hashlib.sha256(
        f"{request.source_commit}\n{encoded}".encode("utf-8")
    ).hexdigest()
    return summary, encoded, snapshot_id


def _row_dict(row: object) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)  # type: ignore[arg-type]


def read_latest_database_snapshot(
    connection_factory: Callable | None = None,
) -> dict[str, Any] | None:
    from app.database import _connect

    factory = connection_factory or _connect
    with factory() as connection:
        row = connection.execute(
            """
            SELECT snapshot_id, generated_at, published_at,
                   source_commit, source_run_id, payload
            FROM factor_ic_snapshots
            ORDER BY generated_at DESC, published_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    data = _row_dict(row)
    return {
        "snapshot_id": data["snapshot_id"],
        "generated_at": data["generated_at"],
        "published_at": data["published_at"],
        "source_commit": data["source_commit"],
        "source_run_id": data["source_run_id"],
        "summary": json.loads(data["payload"]),
    }


def publish_factor_ic_snapshot(
    request: FactorIcPublishRequest,
    *,
    connection_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.database import _connect

    factory = connection_factory or _connect
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary, encoded, snapshot_id = _canonical_summary(request)
    generated_at = request.summary.generated_at.astimezone(timezone.utc)

    try:
        with factory() as connection:
            dialect = getattr(connection, "dialect", None)
            if get_settings().uses_mysql and dialect != "mysql":
                raise FactorIcStorageUnavailable(
                    "MySQL 不可用，拒绝回落到本地 SQLite 发布"
                )
            if dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE")

            duplicate = connection.execute(
                """
                SELECT snapshot_id
                FROM factor_ic_snapshots
                WHERE snapshot_id = ?
                LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            if duplicate is not None:
                return {"created": False, "snapshot_id": snapshot_id}

            latest_query = """
                SELECT snapshot_id, generated_at
                FROM factor_ic_snapshots
                ORDER BY generated_at DESC, published_at DESC
                LIMIT 1
            """
            if dialect == "mysql":
                latest_query += " FOR UPDATE"
            existing = connection.execute(latest_query).fetchone()
            if existing is not None:
                latest = _row_dict(existing)
                latest_generated = datetime.fromisoformat(
                    str(latest["generated_at"])
                ).astimezone(timezone.utc)
                if generated_at <= latest_generated:
                    raise FactorIcNewerSnapshotExists(
                        "数据库已有更新的 factor IC 快照"
                    )

            connection.execute(
                """
                INSERT OR IGNORE INTO factor_ic_snapshots (
                    snapshot_id, schema_version, run_date, generated_at,
                    published_at, source_commit, source_run_id, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    request.summary.schema_version,
                    request.summary.run_date.isoformat(),
                    str(summary["generated_at"]),
                    current.isoformat(),
                    request.source_commit,
                    request.source_run_id,
                    encoded,
                ),
            )
    except (FactorIcNewerSnapshotExists, FactorIcStorageUnavailable):
        raise
    except Exception as exc:
        raise FactorIcStorageUnavailable("factor IC 快照数据库写入失败") from exc
    return {"created": True, "snapshot_id": snapshot_id}


def load_factor_ic_summary(
    *,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    try:
        database_row = read_latest_database_snapshot(connection_factory)
    except Exception:
        database_row = None
    if database_row is not None:
        metadata = {
            "snapshot_id": database_row["snapshot_id"],
            "published_at": database_row["published_at"],
            "source_commit": database_row["source_commit"],
            "source_run_id": database_row["source_run_id"],
        }
        return database_row["summary"], "database", metadata

    path = local_path or DEFAULT_SUMMARY_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None, "unavailable", {}
    if not isinstance(raw, dict):
        return None, "unavailable", {}
    # A local development snapshot has no database row, but consumers still need
    # a stable identity to freeze into a DecisionEvent.  Hash the canonical
    # payload instead of a file path/mtime so repeated reads are deterministic.
    encoded = json.dumps(
        raw,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return raw, "local_file", {
        "snapshot_id": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _unavailable_status(threshold: int) -> dict[str, Any]:
    return {
        "available": False,
        "stale_after_days": threshold,
        "source": "unavailable",
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_factor_ic_status_from_loaded(
    raw: dict[str, Any] | None,
    source: str,
    metadata: dict[str, Any],
    *,
    threshold: int,
    current: datetime,
) -> dict[str, Any]:
    """Build status from one already-loaded summary without touching storage."""
    if not raw or not raw.get("run_date") or raw.get("available") is False:
        return _unavailable_status(threshold)
    params = raw.get("params")
    if params is not None and not isinstance(params, dict):
        return _unavailable_status(threshold)
    factors = raw.get("factors")
    if factors is not None and not isinstance(factors, list):
        return _unavailable_status(threshold)

    generated_at = raw.get("generated_at") or f"{raw['run_date']}T00:00:00+00:00"
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return _unavailable_status(threshold)

    generated_utc = generated.astimezone(timezone.utc)
    age_days = max(0, (current.date() - generated_utc.date()).days)
    factors = factors or []
    factor_periods = {
        str(row.get("factor")): row.get("n_periods")
        for row in factors
        if isinstance(row, dict) and row.get("factor")
    }
    source_commit = str(metadata.get("source_commit") or "")[:7] or None
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    research_model = (
        raw.get("research_model")
        if isinstance(raw.get("research_model"), dict)
        else {}
    )
    primary_horizon = str(research_model.get("primary_horizon") or 20)
    point_in_time = (
        research_model.get("point_in_time")
        if isinstance(research_model.get("point_in_time"), dict)
        else {}
    )
    segment_status = {}
    for key, segment in (research_model.get("segments") or {}).items():
        if not isinstance(segment, dict):
            continue
        horizon = (segment.get("horizons") or {}).get(primary_horizon) or {}
        segment_status[str(key)] = {
            "label": segment.get("label"),
            "sampled_portfolios": segment.get("sampled_portfolios"),
            "universe_size": horizon.get("universe_size"),
            "rebalance_count": horizon.get("rebalance_count"),
            "maturity": horizon.get("maturity"),
            "qualified_factors": sorted(
                factor
                for factor, qualified in (horizon.get("qualified") or {}).items()
                if qualified
            ),
        }
    return {
        "available": True,
        "snapshot_id": metadata.get("snapshot_id"),
        "schema_version": raw.get("schema_version", 1),
        "run_date": str(raw["run_date"]),
        "generated_at": generated.isoformat(),
        "published_at": metadata.get("published_at"),
        "age_days": age_days,
        "stale": age_days >= threshold,
        "stale_after_days": threshold,
        "source": source,
        "target_universe_size": (params or {}).get("universe_size"),
        "universe_size": raw.get("universe_size"),
        "universe_mode": (params or {}).get("universe_mode"),
        "rebalance_count": raw.get("rebalance_count"),
        "factor_periods": factor_periods,
        "forward_horizons": (params or {}).get("forward_horizons") or [raw.get("forward_days")],
        "coverage": {
            key: coverage.get(key)
            for key in (
                "source_share_classes",
                "unique_portfolios",
                "sampled_portfolios",
                "effective_nav_portfolios",
                "effective_nav_rate",
                "total_return_preferred_portfolios",
                "total_return_preferred_rate",
                "nav_return_source_counts",
                "sampled_by_type",
            )
            if key in coverage
        },
        "segments": segment_status,
        "cohort_mode": research_model.get("cohort_mode"),
        "point_in_time": {
            key: point_in_time.get(key)
            for key in (
                "snapshot_id",
                "snapshot_date",
                "effective_anchor_count",
                "anchor_coverage_rate",
                "cohort_nav_coverage_rate",
                "publishable",
                "point_in_time_scope",
                "nav_revision_pit",
                "nav_publication_lag_trading_days",
                "execution_entry_offset_trading_days",
                "mature_anchor_count_by_horizon",
                "mature_anchor_coverage_rate_by_horizon",
                "horizon_ready",
                "primary_maturity_horizon",
            )
            if key in point_in_time
        },
        "pit_upgrade": (
            raw.get("pit_upgrade")
            if isinstance(raw.get("pit_upgrade"), dict)
            else None
        ),
        "pit_coverage": (
            research_model.get("pit_coverage")
            if isinstance(research_model.get("pit_coverage"), dict)
            else None
        ),
        "validation": research_model.get("validation"),
        "economic_significance": research_model.get("economic_significance"),
        "source_commit": source_commit,
    }


def load_factor_ic_context(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    from app.config import get_settings

    threshold = (
        stale_after_days
        if stale_after_days is not None
        else get_settings().factor_ic_stale_after_days
    )
    current = _as_utc(now or datetime.now(timezone.utc))
    raw, source, metadata = load_factor_ic_summary(
        local_path=local_path,
        connection_factory=connection_factory,
    )
    status = _build_factor_ic_status_from_loaded(
        raw,
        source,
        metadata,
        threshold=threshold,
        current=current,
    )
    state: FactorIcEvidenceState
    if not status.get("available"):
        state = "unavailable"
    elif status.get("stale"):
        state = "stale"
    else:
        state = "available"
    return {"state": state, "status": status, "summary": raw}


def build_factor_ic_status(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    return load_factor_ic_context(
        stale_after_days=stale_after_days,
        now=now,
        local_path=local_path,
        connection_factory=connection_factory,
    )["status"]
