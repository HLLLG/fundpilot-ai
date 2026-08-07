"""业绩基准 → 关联板块 的身份门槛。

`关联板块` 的涨跌幅会被用户拿去解释自己基金当天的走势，所以"展示的板块"必须真的
是基金的驱动因素。门槛只放行两种情况：

1. 跟踪指数代码就是该板块自身的行情码（身份完全一致）；
2. 代码在 `_APPROVED_PROXY_INDEX_CODE_TO_SECTOR_LABEL` —— 人工核过的同族代理
   （同市场同主题，口径宽窄不同，日偏差通常 0.3~0.8pp）。

其余一律 fail-closed。历史上缺这道门槛时，跟踪「中证数字经济主题指数」的基金拿到
了板块「信创」，页面显示的是中证信创(931247)的涨跌：实测日均偏差 1.80pp，
2026-08-07 甚至方向相反（信创 -0.52% vs 中证数字经济 +1.08%）。
"""

from __future__ import annotations

import pytest

from app.services.fund_benchmark_sector import (
    _index_code_to_sector_label,
    resolve_sector_from_benchmark,
)


def _benchmark(index_name: str) -> str:
    return f"{index_name}收益率×95%+银行活期存款利率（税后）×5%"


@pytest.mark.parametrize(
    ("index_name", "expected_sector", "expected_code"),
    [
        # 报告的两个 case
        ("中证数字经济主题指数", "数字经济", "931582"),
        ("上海黄金交易所AU99.99", "黄金", "AU9999"),
        # 关键词最长优先修复后：新能源汽车不再退化成"新能源"
        ("中证新能源汽车指数", "新能源车", "399976"),
        # 注册表行情码修正后，这些从"同族代理"升级为"身份完全一致"
        ("中证军工指数", "军工", "399967"),
        ("中证医疗指数", "医疗", "399989"),
        ("中证大农业指数", "农业", "399814"),
        ("中证国有企业改革指数", "国企改革", "399974"),
        ("中证互联网指数", "互联网", "H30535"),
        # 人工核过的同族代理
        ("中证芯片产业指数", "半导体", "H30007"),
        ("中证煤炭指数", "煤炭", "399998"),
        ("中证煤炭等权指数", "煤炭", "399990"),
        ("中证全指软件指数", "软件", "932094"),
        ("中证全指信息技术指数", "计算机", "000993"),
        ("中证细分化工产业主题指数", "化工", "000813"),
        ("中证全指家用电器指数", "家电", "930697"),
        ("中证智能汽车主题指数", "汽车", "399432"),
        ("中证高端装备制造指数", "机械设备", "000097"),
        ("中证工业4.0指数", "机械设备", "399803"),
        ("中证新能源指数", "新能源", "000941"),
        ("中证绿色电力指数", "电力", "931897"),
        ("中证沪深港黄金产业股票指数", "黄金股", "931238"),
        ("国证信息技术创新主题指数", "信创", "CN5075"),
        ("中证800制药与生物科技指数", "医药", "000841"),
        ("中证生物医药指数", "医药", "399441"),
        # 跨市场但同主题、且无 A 股替代标签
        ("恒生沪深港创新药精选50指数", "创新药", "HSSSHID"),
        # 字母前缀码与"宽基前缀不遮蔽细分行业"
        ("中证银行指数", "银行", "H30022"),
        ("中证全指房地产指数", "房地产", "931775"),
    ],
)
def test_verified_identity_resolves_to_its_sector(
    index_name: str,
    expected_sector: str,
    expected_code: str,
) -> None:
    resolved = resolve_sector_from_benchmark(_benchmark(index_name))

    assert resolved is not None, index_name
    sector_name, _intraday_name, match = resolved
    assert sector_name == expected_sector, index_name
    assert match.index_code == expected_code, index_name


@pytest.mark.parametrize(
    ("index_name", "reason"),
    [
        # 跨市场：成分、交易时段、汇率暴露都不同，不能套 A 股板块
        ("中证沪港深数字经济主题指数", "沪港深口径不同于 A 股数字经济"),
        ("中证港股通科技指数", "曾因 987008 错指而被标成新能源"),
        ("恒生消费指数", "港股消费不是 A 股食品饮料"),
        ("中证港股通高股息投资指数", "港股高息不是中证红利"),
        ("中证沪港深互联网指数", "曾错解析为深互联网 399675"),
        ("中证香港内地国有企业指数", "市场级口径不是行业板块"),
        # 宽口径科技/宽基不能冒充细分板块
        ("中国战略新兴产业成份指数", "宽口径战略新兴不是电子"),
        ("中证TMT产业主题指数", "TMT 宽口径不是电子"),
        ("中证科技100指数", "科技宽口径不是电子"),
        ("中证科技传媒通信150指数", "TMT150 不是通信技术"),
        ("中证龙头企业指数", "宽基龙头不是机械设备"),
        ("中证全指工业指数", "宽口径工业不是机械设备"),
        # 上位概念不能冒充细分板块
        ("中证全指可选消费指数", "可选消费不是食品饮料"),
        ("中证内地消费主题指数", "宽口径消费不是食品饮料"),
        ("中证全指金融地产指数", "宽基金融不是金融科技"),
        # 曾经解析到完全无关的错误代码
        ("中证信息技术指数", "曾错解析为深港通信息技术R 483028"),
        ("中证主要消费指数", "曾错解析为深港通主要消费R 483022"),
        ("中证可选消费指数", "曾错解析为深港通可选消费R 483020"),
    ],
)
def test_unverifiable_identity_fails_closed(index_name: str, reason: str) -> None:
    assert resolve_sector_from_benchmark(_benchmark(index_name)) is None, (
        f"{index_name} 不应产出板块：{reason}"
    )


def test_index_code_without_registered_or_approved_identity_yields_no_sector() -> None:
    """曾经的第三条兜底会直接采信中基协库的 theme_label，不校验行情码身份。

    399262 是深证系列「数字经济」，中基协库把它标成「信创」，而信创自己的行情码
    是 931247 —— 展示的涨跌幅与基金无关。现在这类冲突一律返回 None。
    """
    assert _index_code_to_sector_label("399262") is None
    assert _index_code_to_sector_label("987008") is None
    assert _index_code_to_sector_label("483028") is None
    # 身份一致 / 已核过的代理仍然放行
    assert _index_code_to_sector_label("931582") == "数字经济"
    assert _index_code_to_sector_label("931247") == "信创"
    assert _index_code_to_sector_label("AU9999") == "黄金"
    assert _index_code_to_sector_label("H30007") == "半导体"


def test_negated_theme_is_not_matched_by_its_base_theme() -> None:
    """「非银行金融」是券商保险，与「银行」相反，不能被别名"银行"命中。"""
    assert (
        resolve_sector_from_benchmark(
            "沪深300非银行金融指数收益率×95%+活期存款利率(税后)×5%"
        )
        is None
    )


def test_market_level_label_requires_the_whole_index_name() -> None:
    """「港股通」是市场级标签，只有整只指数就是该宽口径时才成立。"""
    assert resolve_sector_from_benchmark(_benchmark("中证港股通科技指数")) is None

    resolved = resolve_sector_from_benchmark(_benchmark("恒生港股通指数"))
    assert resolved is not None
    assert resolved[0] == "港股通"


def test_same_market_scope_qualifier_still_matches_the_sector() -> None:
    """「全指」「细分」这类同市场口径限定词不影响主题归属，必须继续放行。"""
    for index_name, expected in (
        ("中证全指房地产指数", "房地产"),
        ("中证细分化工产业主题指数", "化工"),
        ("中证全指家用电器指数", "家电"),
    ):
        resolved = resolve_sector_from_benchmark(_benchmark(index_name))
        assert resolved is not None, index_name
        assert resolved[0] == expected, index_name


@pytest.mark.parametrize(
    ("benchmark_text", "reason"),
    [
        (
            "95%×中证银行50金融债指数收益率+5%×银行活期存款利率（税后）",
            "金融债指数，别名'银行'命中的是债券腿",
        ),
        (
            "彭博政策性银行债券1-5年指数（Bloomberg China Policy Bank 1-5 Year Index）收益率",
            "'政策性银行债'不是银行板块",
        ),
        (
            "CFETS银行间绿色债券指数（全价）收益率×95%"
            "+中国人民银行人民币活期存款利率（税后）×5%",
            "'银行间'是市场名，'中国人民银行'是机构名",
        ),
        (
            "上海清算所银行间1-3年中高等级信用债指数收益率×95%"
            "+银行活期存款利率（税后）×5%",
            "信用债指数",
        ),
        (
            "中债-高等级信用债财富(0-5年)指数收益率*60%"
            "+中债-国债及政策性银行债财富(总值)指数收益率*25%"
            "+中证可转换债券指数收益率*15%",
            "三条腿全是债券",
        ),
        (
            "同期中国人民银行公布的七天通知存款利率（税后）",
            "纯存款利率基准，历史实现的 or text 兜底会把它拿回来匹配'银行'",
        ),
    ],
)
def test_fixed_income_benchmark_never_yields_an_equity_sector(
    benchmark_text: str,
    reason: str,
) -> None:
    """债券/理财产品不能挂股票板块的涨跌幅。"""
    assert resolve_sector_from_benchmark(benchmark_text) is None, reason


def test_dominant_benchmark_leg_decides_the_sector() -> None:
    """复合基准只认权重最大的那条腿，主导腿解析不出就 fail-closed。

    景顺长城全球半导体芯片(QDII) 的基准是 费城半导体×70% + 中证芯片产业×20%。
    取 20% 那条腿会拿 A 股芯片涨跌解释一只 70% 由美股半导体驱动的基金。
    """
    assert (
        resolve_sector_from_benchmark(
            "费城半导体指数（PHLXSemiconductorSectorIndex）收益率*70%"
            "+中证芯片产业指数收益率*20%+人民币活期存款基准利率*10%"
        )
        is None
    )
    # 同一只指数单独作主导腿时仍然放行
    resolved = resolve_sector_from_benchmark(
        "中证芯片产业指数收益率×95%+银行活期存款利率（税后）×5%"
    )
    assert resolved is not None
    assert resolved[0] == "半导体"

    # 主导腿可解析、次要腿是港股/债券时，按主导腿给板块
    resolved = resolve_sector_from_benchmark(
        "中证大农业指数收益率×60%+恒生指数收益率×10%+中证全债指数收益率×30%"
    )
    assert resolved is not None
    assert resolved[0] == "农业"


def test_exchange_rate_wording_does_not_break_hang_seng_tech_identity() -> None:
    """QDII 基准常写成「经汇率调整后的<指数>」，前缀不能影响身份判定。"""
    resolved = resolve_sector_from_benchmark(
        "经汇率调整后的恒生科技指数收益率×95%+银行人民币活期存款利率（税后）×5%"
    )

    assert resolved is not None
    sector_name, _intraday_name, match = resolved
    assert sector_name == "恒生科技"
    assert match.index_code == "HSTECH"
