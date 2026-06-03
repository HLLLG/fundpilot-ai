from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.eastmoney_spot_client import (
    fetch_eastmoney_quote_by_secid,
    fetch_eastmoney_sector_quote,
)
from app.services.sector_labels import normalize_sector_label

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]

"""养基宝常见「关联板块」→ 东财标准板块（名称 + secid）。

东财概念板块行情页形如 https://quote.eastmoney.com/unify/r/90.BK0963
养基宝与开源养基宝类工具本质也是用东财/天天基金公开行情，差异在名称映射与刷新频率。
"""


@dataclass(frozen=True)
class CanonicalSector:
    label: str
    source_type: str  # concept | industry | index
    source_name: str
    eastmoney_secid: str
    source_code: str | None = None


# 高优先级：养基宝 OCR 样例与实测持仓中反复出现的板块
_CANONICAL_BY_LABEL: dict[str, CanonicalSector] = {
    "商业航天": CanonicalSector(
        label="商业航天",
        source_type="concept",
        source_name="商业航天",
        eastmoney_secid="90.BK0963",
        source_code="BK0963",
    ),
    "半导体": CanonicalSector(
        label="半导体",
        source_type="concept",
        source_name="半导体",
        eastmoney_secid="90.BK1036",
        source_code="BK1036",
    ),
    "国防军工": CanonicalSector(
        label="国防军工",
        source_type="concept",
        source_name="国防军工",
        eastmoney_secid="90.BK0490",
        source_code="BK0490",
    ),
    "中证电网设备": CanonicalSector(
        label="中证电网设备",
        source_type="index",
        source_name="中证电网设备",
        eastmoney_secid="0.931151",
        source_code="931151",
    ),
    "中证人工智能": CanonicalSector(
        label="中证人工智能",
        source_type="index",
        source_name="中证人工智能",
        eastmoney_secid="1.930713",
        source_code="930713",
    ),
}


def get_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None
    if label in _CANONICAL_BY_LABEL:
        return _CANONICAL_BY_LABEL[label]
    # 仅对概念类短名做包含匹配；「中证人工智能」等指数名走 index 全表匹配
    for key in ("商业航天", "国防军工", "半导体", "中证电网设备", "中证人工智能"):
        if key in label:
            return _CANONICAL_BY_LABEL[key]
    return None


@dataclass(frozen=True)
class CanonicalQuoteResult:
    change_percent: float
    matched_name: str
    source_type: str
    source_code: str | None = None
    message: str | None = None


def fetch_canonical_sector_quote(
    sector_name: str | None,
    boards: dict[str, SpotBoard],
) -> CanonicalQuoteResult | None:
    """优先用东财 secid 直连拉取养基宝常见板块。"""
    canon = get_canonical_sector(sector_name)
    if canon is None:
        return None

    board = boards.get(canon.source_type) or {}
    if canon.source_name in board:
        return CanonicalQuoteResult(
            change_percent=board[canon.source_name],
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
        )

    _name, change = fetch_eastmoney_quote_by_secid(canon.eastmoney_secid)
    if change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = change
        return CanonicalQuoteResult(
            change_percent=change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财 {canon.eastmoney_secid}",
        )

    change = fetch_eastmoney_sector_quote(canon.source_name, source_type=canon.source_type)
    if change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = change
        return CanonicalQuoteResult(
            change_percent=change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
        )

    logger.info("canonical sector %s (%s) quote miss", canon.label, canon.eastmoney_secid)
    return None
