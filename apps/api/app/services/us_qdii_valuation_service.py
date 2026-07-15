"""QDII 穿透估值：重仓股加权参考涨跌 + 指数系数回退。"""

from __future__ import annotations

import math
from typing import Any

from app.services.us_qdii_seeds import get_qdii_seeds
from app.services.us_stock_quote_client import quote_key

from app.services.us_qdii_quote_policy import UsQuoteMode, estimate_basis_suffix

_BASIS_HOLDINGS_TEMPLATE = "基于季报重仓穿透估算（{detail}），非实时净值/承诺收益"
_BASIS_FUNDGZ = "基于天天基金平台估值估算，非实时净值/承诺收益"
_MIN_QUOTED_WEIGHT_RATIO = 0.35


def is_index_like_qdii(seed: dict[str, Any]) -> bool:
    """指数型 / 高贝塔 QDII：优先天天基金估值或指数系数（对标小倍华宝纳斯达克精选等）。"""
    try:
        factor = float(seed.get("tracking_factor") or 1.0)
    except (TypeError, ValueError):
        factor = 1.0
    label = f"{seed.get('fund_name', '')}{seed.get('tracking_target', '')}"
    return factor >= 0.95 or "纳斯达克" in label or "标普" in label


def build_fundgz_meta_map(
    estimates: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """fund_code → 天天基金估值元数据（``gztime`` 等）。"""
    out: dict[str, dict[str, str]] = {}
    for fund_code, row in estimates.items():
        if not isinstance(row, dict):
            continue
        estimated_at = row.get("estimated_at")
        if not estimated_at:
            continue
        out[str(fund_code)] = {"estimated_at": str(estimated_at)}
    return out


def latest_fundgz_time(meta: dict[str, dict[str, str]] | None) -> str | None:
    if not meta:
        return None
    times = [row.get("estimated_at") for row in meta.values() if row.get("estimated_at")]
    return max(times) if times else None


def build_fundgz_reference_map(
    estimates: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """fund_code → 天天基金 ``gszzl`` 参考涨跌（%）。"""
    out: dict[str, float] = {}
    for fund_code, row in estimates.items():
        if not isinstance(row, dict):
            continue
        change = row.get("change_percent")
        if change is None:
            continue
        try:
            out[str(fund_code)] = round(float(change), 2)
        except (TypeError, ValueError):
            continue
    return out


def compute_holdings_reference(
    holdings: list[dict[str, Any]],
    quote_map: dict[str, float],
    *,
    min_quoted_ratio: float = _MIN_QUOTED_WEIGHT_RATIO,
) -> float | None:
    """Compute the disclosed sleeve's contribution to whole-fund return.

    ``weight`` is a percentage of fund NAV, so the contribution is
    ``sum(weight_percent * security_change_percent) / 100``.  It must not be
    divided by quoted weight: doing so turns a small disclosed/quoted sleeve
    into a fictitious 100%-of-fund nowcast.
    """
    if not holdings or not quote_map:
        return None

    weights = [_finite_positive(row.get("weight")) for row in holdings]
    total_weight = sum(weight for weight in weights if weight is not None)
    if total_weight <= 0 or total_weight > 100.01:
        return None
    if (
        isinstance(min_quoted_ratio, bool)
        or not isinstance(min_quoted_ratio, (int, float))
        or not math.isfinite(float(min_quoted_ratio))
        or not 0 <= float(min_quoted_ratio) <= 1
    ):
        return None

    quoted_weight = 0.0
    weighted_change = 0.0
    for row in holdings:
        market = str(row.get("market", ""))
        code = str(row.get("code", ""))
        weight = _finite_positive(row.get("weight"))
        if weight is None:
            continue
        change = quote_map.get(quote_key(market, code))
        change_value = _finite_number(change)
        if change_value is None:
            continue
        quoted_weight += weight
        weighted_change += weight * change_value

    if quoted_weight <= 0 or quoted_weight / total_weight < min_quoted_ratio:
        return None
    return round(weighted_change / 100.0, 4)


def build_holdings_reference_map(
    holdings_by_fund: dict[str, dict[str, Any]],
    quote_map: dict[str, float] | None,
) -> dict[str, float]:
    """Build full-fund references only from explicitly eligible snapshots."""
    if not quote_map:
        return {}
    out: dict[str, float] = {}
    for fund_code, payload in holdings_by_fund.items():
        qualification = payload.get("qualification") if isinstance(payload, dict) else None
        if (
            not isinstance(qualification, dict)
            or qualification.get("nowcast_eligible") is not True
            or _payload_coverage_percent(payload) is None
        ):
            continue
        rows = payload.get("holdings") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        ref = compute_holdings_reference(rows, quote_map)
        if ref is not None:
            out[fund_code] = ref
    return out


def build_disclosed_holdings_contribution_map(
    holdings_by_fund: dict[str, dict[str, Any]],
    quote_map: dict[str, float] | None,
) -> dict[str, float]:
    """Return research-only disclosed sleeve contributions.

    This output is intentionally separate from ``build_holdings_reference_map``
    and must not be passed to ``merge_qdii_references`` as a NAV reference.
    """

    if not quote_map:
        return {}
    out: dict[str, float] = {}
    for fund_code, payload in holdings_by_fund.items():
        if not isinstance(payload, dict):
            continue
        qualification = payload.get("qualification")
        if (
            not isinstance(qualification, dict)
            or qualification.get("disclosed_contribution_research_eligible") is not True
            or _payload_coverage_percent(payload) is None
        ):
            continue
        rows = payload.get("holdings")
        if not isinstance(rows, list):
            continue
        contribution = compute_holdings_reference(rows, quote_map)
        if contribution is not None:
            out[fund_code] = contribution
    return out


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_positive(value: object) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _payload_coverage_percent(payload: dict[str, Any]) -> float | None:
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        return None
    raw = coverage.get("portfolio_weight_coverage_percent")
    if raw is None:
        raw = coverage.get("weight_sum_percent")
    value = _finite_number(raw)
    return value if value is not None and 0 < value <= 100.01 else None


def index_factor_reference(
    *,
    tracking_symbol: str | None,
    tracking_factor: float | None,
    change_map: dict[str, float | None],
) -> float | None:
    if not tracking_symbol or tracking_factor is None:
        return None
    change = change_map.get(tracking_symbol)
    if change is None:
        return None
    return round(float(change) * float(tracking_factor), 2)


def merge_qdii_references(
    seeds: list[dict[str, Any]],
    fundgz_refs: dict[str, float],
    holdings_refs: dict[str, float],
    change_map: dict[str, float | None],
    *,
    fundgz_meta: dict[str, dict[str, str]] | None = None,
    quote_mode: UsQuoteMode = "live",
) -> list[dict[str, Any]]:
    """为每只种子基金选择估值（对标小倍「夜盘」口径）。

    - **指数型**（tracking_factor≥0.95 或名称含纳斯达克/标普）：
      天天基金 > 指数系数 > 穿透
    - **主动型全球基金**：穿透 > 天天基金 > 指数系数
    """
    holdings_basis = _BASIS_HOLDINGS_TEMPLATE.format(
        detail=estimate_basis_suffix(quote_mode)
    )
    items: list[dict[str, Any]] = []
    for seed in seeds:
        fund_code = seed["fund_code"]
        fundgz_ref = fundgz_refs.get(fund_code)
        holdings_ref = holdings_refs.get(fund_code)
        factor = seed.get("tracking_factor")
        try:
            factor_f = float(factor) if factor is not None else None
        except (TypeError, ValueError):
            factor_f = None

        index_ref = index_factor_reference(
            tracking_symbol=seed.get("tracking_symbol"),
            tracking_factor=factor_f,
            change_map=change_map,
        )

        if is_index_like_qdii(seed):
            reference, basis, estimated_at = _pick_index_like_reference(
                fund_code=fund_code,
                fundgz_ref=fundgz_ref,
                holdings_ref=holdings_ref,
                index_ref=index_ref,
                seed_basis=seed.get("estimate_basis"),
                holdings_basis=holdings_basis,
                fundgz_meta=fundgz_meta,
            )
        else:
            reference, basis, estimated_at = _pick_active_reference(
                fund_code=fund_code,
                fundgz_ref=fundgz_ref,
                holdings_ref=holdings_ref,
                index_ref=index_ref,
                seed_basis=seed.get("estimate_basis"),
                holdings_basis=holdings_basis,
                fundgz_meta=fundgz_meta,
            )

        items.append(
            {
                "fund_code": fund_code,
                "fund_name": seed["fund_name"],
                "tracking_target": seed["tracking_target"],
                "tracking_symbol": seed.get("tracking_symbol"),
                "reference_change_percent": reference,
                "estimate_basis": basis,
                "estimated_at": estimated_at,
            }
        )
    return items


def _pick_active_reference(
    *,
    fund_code: str,
    fundgz_ref: float | None,
    holdings_ref: float | None,
    index_ref: float | None,
    seed_basis: str | None,
    holdings_basis: str,
    fundgz_meta: dict[str, dict[str, str]] | None,
) -> tuple[float | None, str | None, str | None]:
    if holdings_ref is not None:
        return holdings_ref, holdings_basis, None
    if fundgz_ref is not None:
        return (
            fundgz_ref,
            _BASIS_FUNDGZ,
            (fundgz_meta or {}).get(fund_code, {}).get("estimated_at"),
        )
    return index_ref, seed_basis, None


def _pick_index_like_reference(
    *,
    fund_code: str,
    fundgz_ref: float | None,
    holdings_ref: float | None,
    index_ref: float | None,
    seed_basis: str | None,
    holdings_basis: str,
    fundgz_meta: dict[str, dict[str, str]] | None,
) -> tuple[float | None, str | None, str | None]:
    if fundgz_ref is not None:
        return (
            fundgz_ref,
            _BASIS_FUNDGZ,
            (fundgz_meta or {}).get(fund_code, {}).get("estimated_at"),
        )
    if index_ref is not None:
        return index_ref, seed_basis, None
    if holdings_ref is not None:
        return holdings_ref, holdings_basis, None
    return None, seed_basis, None
