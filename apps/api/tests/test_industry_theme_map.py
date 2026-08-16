"""数据源行业名 → 板块展示名 的映射约束。

数据源给的是申万二级行业（粒度比"板块"细一档，带"Ⅱ"后缀的即二级行业）。这层归并
必须满足两条，否则「关联板块」这一栏会退化：
  1. 任何返回值都必须能取到行情标的 —— 否则页面只有板块名、没有涨跌幅；
  2. 归并不到可交易板块时必须返回 None（fail-closed），而不是把原始行业名当主题透传。

历史实现走的是"透传"，结果 3292 行主板块落在取不到行情的 label 上（"通信设备" 1281 只、
"元件" 380 只、"化学制药" 237 只…），其中 140 行还是决策级 verified。
"""

from __future__ import annotations

import pytest

from app.services.fund_industry_theme_map import (
    _INDUSTRY_TO_THEME,
    map_industry_to_theme_label,
)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_registry_data import THEME_BOARD_WHITELIST


def test_every_mapping_target_is_a_quotable_whitelisted_board() -> None:
    broken: list[str] = []
    for industry, theme in sorted(_INDUSTRY_TO_THEME.items()):
        if theme not in THEME_BOARD_WHITELIST:
            broken.append(f"{industry} -> {theme}（不在白名单）")
        elif get_canonical_sector(theme) is None:
            broken.append(f"{industry} -> {theme}（取不到行情标的）")
    assert broken == [], broken


@pytest.mark.parametrize(
    ("industry", "expected"),
    [
        # 曾经直接透传、导致无行情的高频行业
        ("通信设备", "通信技术"),
        ("元件", "电子"),
        ("化学制药", "医药"),
        ("工业金属", "有色金属"),
        ("小金属", "有色金属"),
        ("化学制品", "化工"),
        ("通用设备", "机械设备"),
        ("专用设备", "机械设备"),
        ("炼化及贸易", "化工"),
        ("油气开采Ⅱ", "油气"),
        ("油服工程", "油气"),
        ("医疗研发外包", "CXO"),
        ("医疗服务", "医疗"),
        ("玻璃玻纤", "建材"),
        ("航空机场", "交通运输"),
        ("铁路公路", "交通运输"),
        ("航空装备Ⅱ", "军工"),
        ("养殖业", "畜牧养殖"),
        ("农化制品", "化工"),
        ("能源金属", "锂矿"),
        ("游戏Ⅱ", "动漫游戏"),
        ("IT服务Ⅱ", "计算机"),
        # 本来就是板块名，原样返回
        ("半导体", "半导体"),
        ("煤炭", "煤炭"),
        ("白酒", "白酒"),
    ],
)
def test_second_level_industry_is_folded_into_its_board(
    industry: str,
    expected: str,
) -> None:
    assert map_industry_to_theme_label(industry) == expected


@pytest.mark.parametrize(
    "industry",
    ["纺织制造", "服装家纺", "造纸", "酒店餐饮", "教育", "一般零售", "旅游及景区"],
)
def test_industry_without_a_tradable_board_fails_closed(industry: str) -> None:
    """没有对应板块的消费/服务细分不得透传成伪板块名。"""
    assert map_industry_to_theme_label(industry) is None


def test_blank_industry_is_ignored() -> None:
    assert map_industry_to_theme_label(None) is None
    assert map_industry_to_theme_label("") is None
    assert map_industry_to_theme_label("   ") is None


def test_returned_label_is_always_quotable() -> None:
    """对一批真实行业名做全量校验：只要有返回值，就必须能定价。"""
    samples = [
        *_INDUSTRY_TO_THEME.keys(),
        *THEME_BOARD_WHITELIST,
        "纺织制造",
        "服装家纺",
        "造纸",
        "饰品",
        "家居用品",
        "专业服务",
        "一般零售",
        "个护用品",
        "包装印刷",
        "旅游及景区",
        "化妆品",
        "教育",
        "文娱用品",
        "酒店餐饮",
    ]
    unpriced = [
        industry
        for industry in samples
        if (label := map_industry_to_theme_label(industry)) is not None
        and get_canonical_sector(label) is None
    ]
    assert unpriced == [], unpriced
