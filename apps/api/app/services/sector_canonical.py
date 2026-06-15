from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.services.eastmoney_spot_client import (
    fetch_eastmoney_quote_by_secid,
)
from app.services.eastmoney_trends_client import (
    fetch_eastmoney_kline_close_percent,
    is_plausible_daily_change,
)
from app.services.trading_session import build_trading_session
from app.services.sector_labels import normalize_sector_label

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]

"""养基宝常见「关联板块」→ 东财标准板块（名称 + secid）。

板块涨跌与分时图统一走东财 push2his K 线（收盘相对昨收），不再依赖 AkShare/批量板块表/天天基金估值。
"""


@dataclass(frozen=True)
class CanonicalSector:
    label: str
    source_type: str  # concept | industry | index
    source_name: str
    eastmoney_secid: str
    source_code: str | None = None


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
        eastmoney_secid="2.930713",
        source_code="930713",
    ),
    "中证半导体": CanonicalSector(
        label="中证半导体",
        source_type="index",
        source_name="中证半导体",
        eastmoney_secid="2.931865",
        source_code="931865",
    ),
    "中证半导": CanonicalSector(
        label="中证半导",
        source_type="index",
        source_name="中证半导体",
        eastmoney_secid="2.931865",
        source_code="931865",
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
        eastmoney_secid="2.930713",
        source_code="930713",
    ),
    "互联网": CanonicalSector(
        label="互联网",
        source_type="concept",
        source_name="互联网服务",
        eastmoney_secid="90.BK0962",
        source_code="BK0962",
    ),
    "有色金属": CanonicalSector(
        label="有色金属",
        source_type="industry",
        source_name="有色金属",
        eastmoney_secid="90.BK0478",
        source_code="BK0478",
    ),
    "新能源车": CanonicalSector(
        label="新能源车",
        source_type="concept",
        source_name="新能源车",
        eastmoney_secid="90.BK0900",
        source_code="BK0900",
    ),
    "医药": CanonicalSector(
        label="医药",
        source_type="industry",
        source_name="医药制造",
        eastmoney_secid="90.BK0465",
        source_code="BK0465",
    ),
    "证券": CanonicalSector(
        label="证券",
        source_type="industry",
        source_name="证券",
        eastmoney_secid="90.BK0473",
        source_code="BK0473",
    ),
    "银行": CanonicalSector(
        label="银行",
        source_type="industry",
        source_name="银行",
        eastmoney_secid="90.BK0475",
        source_code="BK0475",
    ),
    "白酒": CanonicalSector(
        label="白酒",
        source_type="concept",
        source_name="白酒",
        eastmoney_secid="90.BK0896",
        source_code="BK0896",
    ),
    "光伏": CanonicalSector(
        label="光伏",
        source_type="concept",
        source_name="光伏设备",
        eastmoney_secid="90.BK0588",
        source_code="BK0588",
    ),
    "锂电池": CanonicalSector(
        label="锂电池",
        source_type="concept",
        source_name="锂电池",
        eastmoney_secid="90.BK0576",
        source_code="BK0576",
    ),
    "消费电子": CanonicalSector(
        label="消费电子",
        source_type="concept",
        source_name="消费电子",
        eastmoney_secid="90.BK1037",
        source_code="BK1037",
    ),
    "机器人": CanonicalSector(
        label="机器人",
        source_type="concept",
        source_name="机器人概念",
        eastmoney_secid="90.BK1090",
        source_code="BK1090",
    ),
    "云计算": CanonicalSector(
        label="云计算",
        source_type="concept",
        source_name="云计算",
        eastmoney_secid="90.BK0968",
        source_code="BK0968",
    ),
    "5G": CanonicalSector(
        label="5G",
        source_type="concept",
        source_name="5G概念",
        eastmoney_secid="90.BK0448",
        source_code="BK0448",
    ),
    "医疗器械": CanonicalSector(
        label="医疗器械",
        source_type="concept",
        source_name="医疗器械",
        eastmoney_secid="90.BK1041",
        source_code="BK1041",
    ),
}


def list_canonical_sector_labels() -> list[str]:
    """返回已硬编码映射的板块/指数标签（去重、稳定排序）。"""
    seen: set[str] = set()
    labels: list[str] = []
    for label in sorted(_CANONICAL_BY_LABEL):
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


# 推荐基金「关注方向」chips：用户面向短名，按 secid 去重（不展示中证*别名）
_DISCOVERY_CHIP_LABELS: tuple[str, ...] = (
    "商业航天",
    "半导体",
    "国防军工",
    "人工智能",
    "电网设备",
    "互联网",
    "有色金属",
    "新能源车",
    "医药",
    "证券",
    "银行",
    "白酒",
    "光伏",
    "锂电池",
    "消费电子",
    "机器人",
    "云计算",
    "5G",
    "医疗器械",
)


def list_discovery_sector_labels() -> list[str]:
    return list(_DISCOVERY_CHIP_LABELS)


def get_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    label = normalize_sector_label(sector_name)
    if not label:
        return None
    if label in _CANONICAL_BY_LABEL:
        return _CANONICAL_BY_LABEL[label]
    for key in (
        "商业航天",
        "国防军工",
        "半导体",
        "中证半导体",
        "中证半导",
        "中证电网设备",
        "中证人工智能",
        "互联网",
        "有色金属",
        "新能源车",
        "医药",
        "证券",
        "银行",
        "白酒",
        "光伏",
        "锂电池",
        "消费电子",
        "机器人",
        "云计算",
        "5G",
        "医疗器械",
    ):
        if key in label:
            return _CANONICAL_BY_LABEL[key]
    return None


# 关联板块短名 → 东财 zz 指数分时（概念板块 90.BK 无稳定分钟线）
_BOARD_TO_INTRADAY_INDEX: dict[str, str] = {
    "半导体": "中证半导体",
    "电网设备": "中证电网设备",
    "人工智能": "中证人工智能",
}


def get_intraday_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    """分时图优先走场内指数 K 线；无映射时回落概念/行业 canonical。"""
    label = normalize_sector_label(sector_name)
    if not label:
        return None
    index_label = _BOARD_TO_INTRADAY_INDEX.get(label)
    if index_label:
        return get_canonical_sector(index_label)
    return get_canonical_sector(label)


def labels_need_spot_boards(labels: list[str | None]) -> bool:
    """是否存在无法走 canonical K 线的板块名（才需要拉全量板块表）。"""
    for raw in labels:
        label = normalize_sector_label(raw)
        if label and get_canonical_sector(label) is None:
            return True
    return False


@dataclass(frozen=True)
class CanonicalQuoteResult:
    change_percent: float
    matched_name: str
    source_type: str
    source_code: str | None = None
    message: str | None = None


def get_quote_canonical_sector(sector_name: str | None) -> CanonicalSector | None:
    """板块涨跌与分时图同一标的（如 半导体→中证半导体），避免列表与曲线数字不一致。"""
    return get_intraday_canonical_sector(sector_name) or get_canonical_sector(sector_name)


def fetch_canonical_sector_quote(
    sector_name: str | None,
    boards: dict[str, SpotBoard],
) -> CanonicalQuoteResult | None:
    """养基宝常见板块：东财 K 线收盘涨跌幅为准（与分时 15:00 一致）。"""
    canon = get_quote_canonical_sector(sector_name)
    if canon is None:
        return None

    trade_date = build_trading_session().get("effective_trade_date")
    kline_change = fetch_eastmoney_kline_close_percent(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        trade_date=trade_date,
    )
    if kline_change is not None and not is_plausible_daily_change(kline_change):
        logger.info(
            "canonical sector %s (%s) kline change %.4f out of range, ignored",
            canon.label,
            canon.eastmoney_secid,
            kline_change,
        )
        kline_change = None
    if kline_change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = kline_change
        return CanonicalQuoteResult(
            change_percent=kline_change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财K线收盘 {canon.eastmoney_secid}",
        )

    board = boards.get(canon.source_type) or {}
    if canon.source_name in board:
        return CanonicalQuoteResult(
            change_percent=board[canon.source_name],
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财K线缓存 {canon.source_name}",
        )

    _name, change = fetch_eastmoney_quote_by_secid(canon.eastmoney_secid)
    if change is not None:
        boards.setdefault(canon.source_type, {})[canon.source_name] = change
        return CanonicalQuoteResult(
            change_percent=change,
            matched_name=canon.source_name,
            source_type=canon.source_type,
            source_code=canon.source_code,
            message=f"东财快照 {canon.eastmoney_secid}",
        )

    logger.info("canonical sector %s (%s) kline quote miss", canon.label, canon.eastmoney_secid)
    return None


def prefetch_canonical_kline_quotes(
    labels: list[str | None],
    boards: dict[str, SpotBoard],
    *,
    timeout_seconds: float | None = None,
) -> int:
    """并发拉取 canonical 板块东财 K 线收盘涨跌，写入 boards。"""
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

    per_call_timeout = 12.0 if timeout_seconds is None else max(1.0, min(8.0, timeout_seconds * 0.45))
    max_workers = min(6, len(unique_labels))
    matched = 0
    trade_date = build_trading_session().get("effective_trade_date")

    def fetch_one(label: str) -> int:
        canon = get_quote_canonical_sector(label)
        if canon is None:
            return 0
        change = fetch_eastmoney_kline_close_percent(
            canon.eastmoney_secid,
            source_code=canon.source_code,
            trade_date=trade_date,
            timeout=per_call_timeout,
            max_retries=1,
        )
        if change is not None and not is_plausible_daily_change(change):
            return 0
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
                logger.info("prefetch canonical kline worker failed: %s", exc)
    return matched
