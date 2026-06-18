"""QDII 种子表：对标小倍养基「美股基金涨跌助手」精选清单。

设计依据：竞品 image1（15 只全球成长/科技 QDII，短简称 + 夜盘参考涨跌）。

每条种子提供 ``fund_code`` / ``fund_name``（短简称）/ ``tracking_target`` /
``tracking_symbol``（映射期货/指数内部 symbol）/ ``tracking_factor`` /
``estimate_basis``。

``reference_change_percent`` 由 ``us_market_service`` 按对应标的涨跌幅 × 系数估算；
主动型全球基金系数 < 1.0，仅为方向性参考；若季报重仓与个股行情可用则优先穿透估值。
"""

from __future__ import annotations

from typing import Any

NASDAQ_FUT = "NASDAQ_FUT"
SP500_FUT = "SP500_FUT"
DOW_FUT = "DOW_FUT"

_BASIS_NASDAQ = "基于纳斯达克盘前/收盘涨跌估算，非实时净值/承诺收益"
_BASIS_SP500 = "基于标普500盘前/收盘涨跌估算，非实时净值/承诺收益"
_BASIS_BLEND = "基于全球指数涨跌综合估算，非实时净值/承诺收益"
_BASIS_EM = "基于新兴市场相关指数涨跌估算，非实时净值/承诺收益"


def _seed(
    fund_code: str,
    fund_name: str,
    tracking_target: str,
    tracking_symbol: str,
    estimate_basis: str,
    tracking_factor: float = 1.0,
) -> dict[str, Any]:
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "tracking_target": tracking_target,
        "tracking_symbol": tracking_symbol,
        "tracking_factor": tracking_factor,
        "estimate_basis": estimate_basis,
    }


# 顺序与竞品「美股基金涨跌助手」列表一致（image1）。
_QDII_SEEDS: tuple[dict[str, Any], ...] = (
    _seed("012920", "易方达全球成长精选", "全球成长", NASDAQ_FUT, _BASIS_BLEND, 0.75),
    _seed("270023", "广发全球精选股票", "全球精选", SP500_FUT, _BASIS_BLEND, 0.8),
    _seed("005698", "华夏全球科技先锋", "全球科技", NASDAQ_FUT, _BASIS_NASDAQ, 0.85),
    _seed("539002", "建信新兴市场优选", "新兴市场", SP500_FUT, _BASIS_EM, 0.65),
    _seed("017436", "华宝纳斯达克精选", "纳斯达克", NASDAQ_FUT, _BASIS_NASDAQ, 1.0),
    _seed("016701", "银华海外数字经济", "数字经济", NASDAQ_FUT, _BASIS_NASDAQ, 0.9),
    _seed("001668", "汇添富全球移动互联", "移动互联", NASDAQ_FUT, _BASIS_NASDAQ, 0.85),
    _seed("006555", "浦银安盛全球智能科技", "智能科技", NASDAQ_FUT, _BASIS_NASDAQ, 0.85),
    _seed("002891", "华夏移动互联", "移动互联", NASDAQ_FUT, _BASIS_NASDAQ, 0.8),
    _seed("006373", "国富全球科技互联", "科技互联", NASDAQ_FUT, _BASIS_NASDAQ, 0.85),
    _seed("000043", "嘉实美国成长", "美国成长", SP500_FUT, _BASIS_SP500, 0.95),
    _seed("017730", "嘉实全球产业升级", "产业升级", NASDAQ_FUT, _BASIS_BLEND, 0.7),
    _seed("016664", "天弘全球高端制造", "高端制造", SP500_FUT, _BASIS_BLEND, 0.75),
    _seed("501226", "长城全球新能源车", "新能源车", NASDAQ_FUT, _BASIS_BLEND, 0.8),
    _seed("017144", "华宝海外新能源汽车", "新能源汽车", NASDAQ_FUT, _BASIS_BLEND, 0.75),
)


def get_qdii_seeds() -> list[dict[str, Any]]:
    """返回 QDII 种子清单的拷贝。"""
    return [dict(seed) for seed in _QDII_SEEDS]
