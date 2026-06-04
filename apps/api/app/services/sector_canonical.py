from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        eastmoney_secid="2.931994",
        source_code="931994",
    ),
    "中证人工智能": CanonicalSector(
        label="中证人工智能",
        source_type="index",
        source_name="中证人工智能",
        eastmoney_secid="1.930713",
        source_code="930713",
    ),
    "电网设备": CanonicalSector(
        label="电网设备",
        source_type="index",
        source_name="中证电网设备",
        eastmoney_secid="2.931994",
        source_code="931994",
    ),
    "人工智能": CanonicalSector(
        label="人工智能",
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

    if canon.source_type == "index":
        change = _fetch_index_quote_via_akshare(canon.source_name)
        if change is not None:
            boards.setdefault("index", {})[canon.source_name] = change
            return CanonicalQuoteResult(
                change_percent=change,
                matched_name=canon.source_name,
                source_type="index",
                source_code=canon.source_code,
                message=f"AkShare 指数 {canon.source_name}",
            )

    logger.info("canonical sector %s (%s) quote miss", canon.label, canon.eastmoney_secid)
    return None


def _fetch_index_quote_via_akshare(index_name: str) -> float | None:
    """东财 secid/批量表缺失时，按指数名称从 AkShare 中证系列补拉。"""
    try:
        import akshare as ak  # type: ignore[import-not-found]

        for symbol in ("中证系列指数", "沪深重要指数", "上证系列指数"):
            frame = ak.stock_zh_index_spot_em(symbol=symbol)
            if frame is None or getattr(frame, "empty", True):
                continue
            if "名称" not in frame.columns or "涨跌幅" not in frame.columns:
                continue
            matched = frame.loc[frame["名称"] == index_name]
            if matched.empty:
                continue
            return round(float(matched.iloc[0]["涨跌幅"]), 4)
    except Exception as exc:
        logger.info("akshare index quote %s failed: %s", index_name, exc)
    return None


def prefetch_canonical_secid_quotes(
    labels: list[str | None],
    boards: dict[str, SpotBoard],
    *,
    timeout_seconds: float | None = None,
) -> int:
    """Concurrent secid fetch for canonical labels missing from spot boards."""
    unique_labels: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        label = normalize_sector_label(raw)
        if not label or label in seen or get_canonical_sector(label) is None:
            continue
        seen.add(label)
        unique_labels.append(label)
    if not unique_labels:
        return 0

    per_call_timeout = 8.0 if timeout_seconds is None else max(0.8, min(2.5, timeout_seconds * 0.3))
    max_workers = min(6, len(unique_labels))
    matched = 0

    def fetch_one(label: str) -> int:
        canon = get_canonical_sector(label)
        if canon is None:
            return 0
        board = boards.get(canon.source_type) or {}
        if canon.source_name in board:
            return 0
        _name, change = fetch_eastmoney_quote_by_secid(
            canon.eastmoney_secid,
            timeout=per_call_timeout,
            max_retries=1,
        )
        if change is not None:
            boards.setdefault(canon.source_type, {})[canon.source_name] = change
            return 1
        return 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_one, label) for label in unique_labels]
        for future in as_completed(futures):
            try:
                matched += int(future.result())
            except Exception as exc:
                logger.info("prefetch canonical secid worker failed: %s", exc)
    return matched
