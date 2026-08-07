"""THEME_BOARD_INDEX 的身份不变量对账。

这张表是手工维护的「板块名 → 东财行情标的」映射。写错代码不会报错，只会让板块
涨跌幅静默变成另一只指数的涨跌幅——而用户会拿这个数字解释自己基金当天的走势，
所以比缺数据更糟。2026-08 的一次全表对账发现 22 条错配（军工指向沪深300USD、
有色金属指向800汽车、机器人指向卫星产业……），这些测试用来锁住修复结果并挡住同类回归。

注意：这里只做**离线**不变量与回归钉子。标的名称是否真的对得上必须访问东财实时接口，
不适合放进单测（仓库里缓存的 var/amac/em_index_lookup.json 与实时数据已出现漂移，
例如 930601 缓存写「中证环保」而实时返回「中证软件」）。
"""

from __future__ import annotations

import re
from collections import defaultdict

import pytest

from app.services.sector_registry_data import (
    THEME_BOARD_INDEX,
    THEME_BOARD_PROVIDER_IDENTITIES,
    THEME_BOARD_WHITELIST,
)

# 行情码形态 → 东财 secid 必须使用的市场前缀。
# 用错前缀往往仍能返回一个"有效但无关"的标的，因此必须显式约束。
_SOURCE_CODE_SHAPE_TO_SECID_PREFIX: tuple[tuple[str, str], ...] = (
    (r"BK\d{4}", "90"),          # 东财概念/行业板块
    (r"AU\d{4}", "118"),         # 上金所现货（黄金 Au99.99）
    (r"HS[A-Z0-9]*", "124"),     # 恒生系列
    (r"9[35]\d{4}", "2"),        # 中证
    (r"H[A-Z0-9]+", "2"),        # 中证字母前缀系列
    (r"399\d{3}", "0"),          # 深证 / 国证
    (r"98\d{4}", "0"),           # 国证
    (r"000\d{3}", "1"),          # 上证挂牌
)

# 刻意留在 THEME_BOARD_INDEX 但不进白名单的消歧条目。
# 930713 中证人工智能主题 与 931071 中证人工智能产业 名称接近、成分不同，
# 需要两个 label 才能区分，但只有"人工智能"对外展示。
_NON_WHITELISTED_DISAMBIGUATION_LABELS = frozenset({"人工智能产业"})

# 2026-08 全表对账修正的错配：label → (被换掉的错误行情码, 修正后的行情码)。
# 左边这些代码在东财是完全无关的标的，任何一条重新出现都说明回归了。
_CORRECTED_QUOTES: dict[str, tuple[str, str]] = {
    "军工": ("930749", "399967"),        # 930749 实为 沪深300USD(CNH)
    "有色金属": ("H30015", "000819"),    # H30015 实为 800汽车
    "机器人": ("931594", "H30590"),      # 931594 实为 卫星产业
    "锂电池": ("932444", "BK1303"),      # 932444 实为 互联互通中国50HKD
    "可控核聚变": ("932000", "BK1163"),  # 932000 实为 中证2000（宽基）
    "机械设备": ("932078", "BK1205"),    # 932078 实为 全指材料行业
    "储能": ("H30057", "931746"),        # H30057 实为 AMAC矿物
    "氢能": ("H30198", "BK0864"),        # H30198 实为 油气产业
    "国企改革": ("931088", "399974"),    # 931088 实为 180 ESG
    "农业": ("931581", "399814"),        # 931581 实为 周期稳健成长50
    "智能家居": ("H50028", "399996"),    # H50028 实为 沪信息红
    "AI医疗": ("H30531", "BK1170"),      # H30531 实为 精工制造
    "CPO": ("932357", "BK1128"),         # 932357 实为 专精特新100
    "MLCC": ("930902", "BK0890"),        # 930902 实为 中证数据
    "PCB": ("931837", "BK0877"),         # 931837 实为 央企现代产业
    "体育": ("930790", "BK0708"),        # 930790 实为 CS娱乐TI
    "脑机接口": ("H11050", "BK0706"),    # H11050 实为 AMAC综企
    "新能源": ("931151", "000941"),      # 931151 是光伏产业，曾与"光伏"共用一码
    # 窄口径子主题冒充宽 label
    "医疗": ("930720", "399989"),        # 930720 是 CS互医疗（互联网医疗）
    "互联网": ("930604", "H30535"),      # 930604 是 中国互联网30（30 只成分）
    "红利": ("H30089", "000922"),        # H30089 是 红利潜力（另一套选样）
    "环保": ("930614", "000827"),        # 930614 是 环保50（50 只子集）
    # 现货金 vs 黄金股
    "黄金": ("BK1617", "AU9999"),        # BK1617 是黄金股票板（山东黄金等）
}


def test_no_two_sector_labels_share_a_market_quote() -> None:
    """一码两名会让两个板块永远显示同一个涨跌幅。

    历史上"光伏"与"新能源"共用 931151（光伏产业指数），于是新能源基金看到的
    始终是光伏的涨跌。
    """
    by_code: dict[str, list[str]] = defaultdict(list)
    for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items():
        by_code[source_code.upper()].append(label)

    shared = {code: sorted(labels) for code, labels in by_code.items() if len(labels) > 1}
    assert shared == {}, f"以下行情码被多个板块共用: {shared}"


def test_secid_market_prefix_matches_source_code_shape() -> None:
    mismatched: list[str] = []
    for label, (secid, source_code, _kind) in sorted(THEME_BOARD_INDEX.items()):
        prefix = secid.split(".", 1)[0]
        code = source_code.upper()
        expected = next(
            (
                want
                for pattern, want in _SOURCE_CODE_SHAPE_TO_SECID_PREFIX
                if re.fullmatch(pattern, code)
            ),
            None,
        )
        if expected is None:
            mismatched.append(f"{label}: 行情码 {code} 形态未登记")
        elif prefix != expected:
            mismatched.append(f"{label}: {secid} 前缀应为 {expected}. (码 {code})")
    assert mismatched == [], mismatched


# 只有东财概念/行业板、没有同名指数的板块。
#
# 这份清单必须显式维护：`fund_benchmark_sector._build_benchmark_name_to_code` 会把
# BK 代码排除在别名表之外（BK 板块不是基金跟踪指数身份），所以用 BK 码的板块**无法
# 再由业绩基准文案解析到**。有色金属与新能源都曾被误改成 BK 码，导致 57 只指数基金
# 突然识别不出板块；两者都有同名指数（000819 / 000941），已改回指数。
_BOARD_ONLY_SECTOR_LABELS: frozenset[str] = frozenset(
    {
        "AI医疗",
        "CPO",
        "MLCC",
        "PCB",
        "低空经济",
        "体育",
        "化工",
        "可控核聚变",
        "商业航天",
        "固态电池",
        "存储芯片",
        "机械设备",
        "氢能",
        "煤炭",
        "算力租赁",
        "脑机接口",
        "证券保险",
        "贵金属",
        "通信技术",
        "锂电池",
        "锂矿",
    }
)


def test_every_whitelisted_sector_has_a_market_quote() -> None:
    missing = [label for label in THEME_BOARD_WHITELIST if label not in THEME_BOARD_INDEX]
    assert missing == [], f"白名单板块缺少行情码: {missing}"


def test_board_backed_sectors_are_explicitly_documented() -> None:
    """用 BK 码的板块必须登记在案，否则会静默失去基准解析能力。"""
    board_backed = {
        label
        for label, (_secid, source_code, _kind) in THEME_BOARD_INDEX.items()
        if source_code.upper().startswith("BK")
    }
    undocumented = sorted(board_backed - _BOARD_ONLY_SECTOR_LABELS)
    assert undocumented == [], (
        "以下板块改用了东财 BK 板块代码，会导致业绩基准文案再也解析不到它们。"
        f"若确实没有同名指数可用，请补进 _BOARD_ONLY_SECTOR_LABELS: {undocumented}"
    )


def test_registry_has_no_undocumented_extra_labels() -> None:
    extra = set(THEME_BOARD_INDEX) - set(THEME_BOARD_WHITELIST)
    assert extra <= _NON_WHITELISTED_DISAMBIGUATION_LABELS, (
        f"未登记用途的额外 label: {sorted(extra - _NON_WHITELISTED_DISAMBIGUATION_LABELS)}"
    )


@pytest.mark.parametrize(
    ("label", "stale_code", "expected_code"),
    [(label, stale, fixed) for label, (stale, fixed) in sorted(_CORRECTED_QUOTES.items())],
)
def test_corrected_sector_quote_does_not_regress(
    label: str,
    stale_code: str,
    expected_code: str,
) -> None:
    entry = THEME_BOARD_INDEX.get(label)
    assert entry is not None, label
    _secid, source_code, _kind = entry
    assert source_code.upper() != stale_code.upper(), (
        f"{label} 又指回了错配的 {stale_code}"
    )
    assert source_code.upper() == expected_code.upper(), label


def test_gold_label_tracks_the_metal_and_gold_equities_stay_separate() -> None:
    """黄金 ETF 联接跟踪 AU99.99 现货，不能拿黄金股票板当涨跌代理。

    实测近 8 个交易日：基金净值 vs 黄金股票板 BK1617 日均偏差 1.82pp（单日最大
    5.38pp），vs 实物金代理仅 0.25pp。
    """
    assert THEME_BOARD_INDEX["黄金"] == ("118.AU9999", "AU9999", "index")
    assert THEME_BOARD_INDEX["黄金股"] == ("2.931238", "931238", "index")

    policy = THEME_BOARD_PROVIDER_IDENTITIES["黄金"]
    assert policy["source_codes"] == ("AU9999",)
    assert "黄金9999" in policy["security_names"]


def test_digital_economy_is_not_aliased_to_xinchuang() -> None:
    """中证数字经济主题(931582) 与 中证信创(931247) 是两套成分，不能互为代理。

    东财对深证 399262 与中证 931582 都显示简称"数字经济"，按简称反查必然歧义，
    所以两个 label 都上了 provider identity 锁。
    """
    assert THEME_BOARD_INDEX["数字经济"] == ("2.931582", "931582", "index")
    assert THEME_BOARD_INDEX["信创"] == ("2.931247", "931247", "index")

    assert THEME_BOARD_PROVIDER_IDENTITIES["数字经济"]["source_codes"] == ("931582",)
    assert THEME_BOARD_PROVIDER_IDENTITIES["信创"]["source_codes"] == ("931247",)
