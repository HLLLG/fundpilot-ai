"""支付宝持有页 OCR 解析回归。

`ALIPAY_OVERVIEW_OCR` 与 `ALIPAY_GROUPED_OCR` 是把两种版式截图送进 qwen-vl-ocr 后
拿回的**真实**识别文本（不是手写的理想输入），所以行序、拆行位置、把多列读成一行的
习惯都保留了云端模型的真实行为：

* 「全部持有」总览页：4 列（金额 / 日收益 / 持有收益 / 累计收益），日收益等数字会被
  读成 `0.00 +17.03 +17.03` 这样的一整行；
* 「我的持有」财富号分组页：3 列 × 2 行，按财富号分组，长基金名被拆成两行，数字按列
  读出 [金额, 昨日收益, 持有收益, 率]。

两种版式必须解析出完全一致的四条持仓——这正是之前坏掉的地方：分组版式会把
`新华鑫科技3个月滚动持有灵活配置混合A` 截断成 `持有灵活配置混合A`。
"""
from __future__ import annotations

import pytest

from app.services.ocr_parser import detect_ocr_source, parse_holdings_from_text

ALIPAY_OVERVIEW_OCR = """11:15
全部持有 收益分析 配置分析 交易分析
本月变动(元)
昨日变动(元)
+45,204.14
+5.53
查看明细
查看变动趋势
清仓分析
收益地图
基金定投
专项计划
全部二
名称/金额
日收益 持有收益 累计收益
万家宏观择时多策略灵活配置混合C
基金 进阶理财
1,017.03
占比 0.40%
0.00 +17.03 +17.03
+1.70%
鹏扬中证数字经济主题ETF联接C
基金 进阶理财
509.09
占比 0.20%
0.00 +9.09 +9.09
+1.82%
博时黄金ETF联接A
基金 进阶理财
1,004.41
占比 0.39%
0.00 +4.41 +4.41
+0.44%
新华鑫科技3个月滚动持有灵活配置混合A
基金 进阶理财
2,229.22
占比 0.87%
0.00 -770.78 -770.78
-25.69%
以上按照持有收益排序
"""

ALIPAY_GROUPED_OCR = """11:18
基金
板块近一年涨幅+105.72%
半导体
我的持有
更新时间排序
全部 偏股 偏债 指数 黄金 全球
名称 金额/昨日收益 持有收益/率
新华基金财富号
新华鑫科技3个月滚动
持有灵活配置混合A
2,229.22
0.00
-770.78
-25.69%
博时基金财富号
博时黄金ETF联接A
1,004.41
0.00
+4.41
+0.44%
鹏扬基金财富号
鹏扬中证数字经济主题ETF联接C
509.09
0.00
+9.09
+1.82%
万家基金财富号
万家宏观择时多策略灵活配置混合C
1,017.03
0.00
+17.03
+1.70%
基金市场
排行
自选
持有
"""

# (持有金额, 持有收益, 持有收益率)
EXPECTED_HOLDINGS = {
    "新华鑫科技3个月滚动持有灵活配置混合A": (2229.22, -770.78, -25.69),
    "博时黄金ETF联接A": (1004.41, 4.41, 0.44),
    "鹏扬中证数字经济主题ETF联接C": (509.09, 9.09, 1.82),
    "万家宏观择时多策略灵活配置混合C": (1017.03, 17.03, 1.70),
}


@pytest.mark.parametrize(
    "ocr_text",
    [
        pytest.param(ALIPAY_OVERVIEW_OCR, id="overview_all_holdings"),
        pytest.param(ALIPAY_GROUPED_OCR, id="grouped_by_wealth_account"),
    ],
)
def test_both_alipay_layouts_yield_the_same_holdings(ocr_text: str) -> None:
    assert detect_ocr_source(ocr_text) == "alipay_holdings"

    holdings = parse_holdings_from_text(ocr_text)
    parsed = {
        holding.fund_name: (
            holding.holding_amount,
            holding.holding_profit,
            holding.holding_return_percent,
        )
        for holding in holdings
    }

    assert parsed == EXPECTED_HOLDINGS


def test_grouped_layout_keeps_yesterday_profit_out_of_holding_profit() -> None:
    """分组版式的 0.00 是昨日收益，绝不能顶替持有收益。

    回归点：曾经按「全部持有」的列序解读分组版式，把 -770.78 当成日收益，再用收益率
    反算出 -770.67 写进持有收益——金额对、收益差一毛，账本悄悄错。
    """
    holdings = {h.fund_name: h for h in parse_holdings_from_text(ALIPAY_GROUPED_OCR)}

    xinhua = holdings["新华鑫科技3个月滚动持有灵活配置混合A"]
    assert xinhua.holding_profit == -770.78
    assert xinhua.yesterday_profit == 0.0


def test_grouped_layout_does_not_read_digits_inside_fund_names_as_metrics() -> None:
    """`新华鑫科技3个月滚动` 里的 3 是名字的一部分，不是金额/收益列。"""
    holdings = {h.fund_name: h for h in parse_holdings_from_text(ALIPAY_GROUPED_OCR)}

    xinhua = holdings["新华鑫科技3个月滚动持有灵活配置混合A"]
    assert xinhua.yesterday_profit == 0.0
    assert xinhua.holding_amount == 2229.22


def test_one_wealth_account_can_hold_several_funds() -> None:
    """同一个财富号下的多只基金要各自成条，靠行尾的持有收益率切分。"""
    text = """我的持有
更新时间排序
名称
金额/昨日收益
持有收益/率
华夏基金财富号
华夏中证电网设备主
题ETF联接A
9,618.51
0.00
+335.68
+3.62%
华夏人工智能ETF联接C
8,152.78
0.00
-158.19
-1.90%
博时基金财富号
博时黄金ETF联接A
1,004.41
0.00
+4.41
+0.44%
基金市场
"""

    parsed = {
        holding.fund_name: (holding.holding_amount, holding.holding_profit)
        for holding in parse_holdings_from_text(text)
    }

    assert parsed == {
        "华夏中证电网设备主题ETF联接A": (9618.51, 335.68),
        "华夏人工智能ETF联接C": (8152.78, -158.19),
        "博时黄金ETF联接A": (1004.41, 4.41),
    }


def test_grouped_layout_survives_row_major_number_order() -> None:
    """OCR 有时按视觉行读出 [金额, 持有收益, 昨日收益, 率]。

    列序变了结果不能变——持有收益是靠收益率反算认领的，不是靠下标。
    """
    text = """我的持有
名称
金额/昨日收益
持有收益/率
新华基金财富号
新华鑫科技3个月滚动
持有灵活配置混合A
2,229.22
-770.78
0.00
-25.69%
万家基金财富号
万家宏观择时多策略
灵活配置混合C
1,017.03
+17.03
0.00
+1.70%
"""

    parsed = {
        holding.fund_name: (holding.holding_profit, holding.yesterday_profit)
        for holding in parse_holdings_from_text(text)
    }

    assert parsed == {
        "新华鑫科技3个月滚动持有灵活配置混合A": (-770.78, 0.0),
        "万家宏观择时多策略灵活配置混合C": (17.03, 0.0),
    }


def test_grouped_layout_survives_two_columns_merged_into_one_line() -> None:
    text = """我的持有
名称 金额/昨日收益 持有收益/率
新华基金财富号
新华鑫科技3个月滚动
持有灵活配置混合A
2,229.22 -770.78
0.00 -25.69%
博时基金财富号
博时黄金ETF联接A
1,004.41 +4.41
0.00 +0.44%
"""

    parsed = {
        holding.fund_name: (holding.holding_amount, holding.holding_profit)
        for holding in parse_holdings_from_text(text)
    }

    assert parsed == {
        "新华鑫科技3个月滚动持有灵活配置混合A": (2229.22, -770.78),
        "博时黄金ETF联接A": (1004.41, 4.41),
    }


ALIPAY_GROUPED_NAME_GLUED_TO_METRICS = """10:07
基金
我的持有
更新时间排序
全部 偏股 偏债 指数 黄金 全球
名称 金额/昨日收益 持有收益/率
鹏扬基金财富号
鹏扬中证数字经济主题ETF联接C517.74+17.74+15.38+3.55%
国泰基金财富号
国泰国证房地产行业指数(LOF)A972.54-27.46+2.68-2.75%
博时基金财富号
博时黄金ETF联接A2,039.76+39.76+24.33+1.99%
嘉实基金财富号
嘉实中证稀土产业ETF联接C1,816.10+16.10+33.93+0.89%
基金市场
排行
自选
持有
"""


def test_grouped_layout_strips_metrics_glued_to_fund_name() -> None:
    """qwen-vl 常把名称和右侧三列读成一行，名称不能带着 51774+1774 这种数字尾巴。"""
    holdings = parse_holdings_from_text(ALIPAY_GROUPED_NAME_GLUED_TO_METRICS)
    parsed = {
        holding.fund_name: (
            holding.holding_amount,
            holding.holding_profit,
            holding.holding_return_percent,
        )
        for holding in holdings
    }
    assert parsed == {
        "鹏扬中证数字经济主题ETF联接C": (517.74, 17.74, 3.55),
        "国泰国证房地产行业指数(LOF)A": (972.54, -27.46, -2.75),
        "博时黄金ETF联接A": (2039.76, 39.76, 1.99),
        "嘉实中证稀土产业ETF联接C": (1816.10, 16.10, 0.89),
    }


# 基金 Tab「我的持有」：不是收益明细「全部持有」。有的基金没有财富号、只有产品周报；
# 长名会拆行。qwen-vl-ocr 常见两种读法都要过。
ALIPAY_FUND_TAB_MIXED_WEALTH_OCR = """14:00
基金
我的持有
更新时间排序
全部 偏股 偏债 指数 黄金 全球
名称 金额/昨日收益 持有收益/率
南方黄金股指数C
1,592.98
+76.54
+92.98
+6.20%
产品周报
通胀粘性仍存，金价高位震荡
招商基金财富号
招商医疗保健股票A
4,637.97
+205.86
+137.97
+3.07%
国泰基金财富号
国泰国证房地产行业指数(LOF)A
731.92
+7.46
-18.08
-2.41%
更多产品，去市场看看
基金市场
排行
自选
持有
"""

ALIPAY_FUND_TAB_NO_WEALTH_WRAPPED_OCR = """14:00
基金
我的持有
更新时间排序
全部 偏股 偏债 指数 黄金 全球
名称 金额/昨日收益 持有收益/率
万家宏观择时多策略灵活
配置混合C
2,245.73
+6.95
+45.73
+2.08%
华夏半导体材料设备
ETF联接A
1,882.06
-11.41
-117.94
-5.90%
南方黄金股指数C
1,592.98
+76.54
+92.98
+6.20%
产品周报
通胀粘性仍存，金价高位震荡
更多产品，去市场看看
基金市场
排行
自选
持有
"""

ALIPAY_FUND_TAB_ROW_MAJOR_OCR = """我的持有
名称 金额/昨日收益 持有收益/率
南方黄金股指数C 1,592.98 +92.98
+76.54 +6.20%
产品周报 通胀粘性仍存，金价高位震荡
招商基金财富号
招商医疗保健股票A 4,637.97 +137.97
+205.86 +3.07%
国泰基金财富号
国泰国证房地产行业指数(LOF)A 731.92 -18.08
+7.46 -2.41%
基金市场
"""


def _holding_tuple(holding) -> tuple[float, float | None, float | None, float | None]:
    return (
        holding.holding_amount,
        holding.holding_profit,
        holding.yesterday_profit,
        holding.holding_return_percent,
    )


def test_fund_tab_keeps_preamble_fund_before_first_wealth_account() -> None:
    """没有财富号、只有产品周报的基金必须留下，不能从第一个财富号才起算。"""
    assert detect_ocr_source(ALIPAY_FUND_TAB_MIXED_WEALTH_OCR) == "alipay_holdings"
    holdings = {item.fund_name: item for item in parse_holdings_from_text(ALIPAY_FUND_TAB_MIXED_WEALTH_OCR)}
    assert set(holdings) == {
        "南方黄金股指数C",
        "招商医疗保健股票A",
        "国泰国证房地产行业指数(LOF)A",
    }
    assert _holding_tuple(holdings["南方黄金股指数C"]) == (1592.98, 92.98, 76.54, 6.20)
    assert _holding_tuple(holdings["招商医疗保健股票A"]) == (4637.97, 137.97, 205.86, 3.07)
    assert _holding_tuple(holdings["国泰国证房地产行业指数(LOF)A"]) == (
        731.92,
        -18.08,
        7.46,
        -2.41,
    )


def test_fund_tab_parses_without_any_wealth_account() -> None:
    """零财富号 + 长名拆行：万家 / 华夏半导体 不能被截成「配置混合C」「ETF联接A」。"""
    holdings = {item.fund_name: item for item in parse_holdings_from_text(ALIPAY_FUND_TAB_NO_WEALTH_WRAPPED_OCR)}
    assert _holding_tuple(holdings["万家宏观择时多策略灵活配置混合C"]) == (
        2245.73,
        45.73,
        6.95,
        2.08,
    )
    assert _holding_tuple(holdings["华夏半导体材料设备ETF联接A"]) == (
        1882.06,
        -117.94,
        -11.41,
        -5.90,
    )
    assert holdings["南方黄金股指数C"].holding_amount == 1592.98


def test_fund_tab_survives_visual_row_major_ocr() -> None:
    """OCR 按视觉行读出「金额+持有收益 / 昨日+收益率」两列时，收益列仍靠收益率认领。"""
    holdings = {item.fund_name: item for item in parse_holdings_from_text(ALIPAY_FUND_TAB_ROW_MAJOR_OCR)}
    assert holdings["南方黄金股指数C"].holding_profit == 92.98
    assert holdings["南方黄金股指数C"].yesterday_profit == 76.54
    assert holdings["招商医疗保健股票A"].holding_profit == 137.97
    assert holdings["国泰国证房地产行业指数(LOF)A"].holding_profit == -18.08
    assert "产品周报" not in "".join(holdings)
    assert "通胀粘性" not in "".join(holdings)
