"""因子 IC 快照的版本化发布契约与质量门槛。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FACTOR_IC_SCHEMA_VERSION = 1
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
MIN_EFFECTIVE_UNIVERSE = 240
MIN_VALID_PERIODS = 12


class FactorIcParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_size: int
    universe_mode: Literal["top", "sampled"]
    sample_pool_size: int
    nav_days: int
    rebalance_step: int
    forward_days: int
    factor_lookback: int


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
        for name in ("ic_std", "icir", "t_stat", "positive_ratio"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{self.factor} {name} 必须是有限数字")
        if self.positive_ratio is not None and not 0 <= self.positive_ratio <= 1:
            raise ValueError(f"{self.factor} positive_ratio 非法")
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

    @model_validator(mode="after")
    def validate_quality(self) -> "FactorIcSummary":
        if self.schema_version != FACTOR_IC_SCHEMA_VERSION:
            raise ValueError("不支持的 factor IC schema_version")
        if self.params.model_dump() != EXPECTED_PARAMS:
            raise ValueError("回测参数不是固定生产口径")
        if not self.available:
            raise ValueError("回测结果不可用")
        if self.universe_size < MIN_EFFECTIVE_UNIVERSE:
            raise ValueError(f"有效基金数不足 {MIN_EFFECTIVE_UNIVERSE}")
        if self.rebalance_count < MIN_VALID_PERIODS:
            raise ValueError(f"回测期数不足 {MIN_VALID_PERIODS}")
        names = [row.factor for row in self.factors]
        if len(names) != len(FACTOR_NAMES) or set(names) != FACTOR_NAMES:
            raise ValueError("四个因子必须齐全且不可重复")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at 必须包含时区")
        if self.run_date != self.generated_at.astimezone(timezone.utc).date():
            raise ValueError("run_date 必须等于 generated_at 的 UTC 日期")
        return self


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
