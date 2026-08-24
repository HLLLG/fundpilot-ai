"""支付宝交易记录 / 交易分析页 OCR 解析回归。

两种真实版式必须都能用：

* 「交易记录」：每条「买入/卖出」独占一行，基金名带「基金 |」前缀；
* 「交易分析」：顶栏仍有「全部持有」Tab，明细是「买入 基金」+ 名称 + 金额元 + 时间。
  以前会被 ``detect_ocr_source`` 当成持仓总览，且解析器要求「买入」整行精确匹配，
  两种原因叠在一起就会弹出「这张是持仓总览截图」。
"""
from __future__ import annotations

from app.services.alipay_transactions_parser import (
    is_alipay_transaction_page,
    parse_alipay_transactions,
)
from app.services.ocr_parser import detect_ocr_source, parse_holdings_from_text
from tests.test_alipay_holdings_parser import ALIPAY_OVERVIEW_OCR

ALIPAY_TRANSACTION_RECORDS_OCR = """交易记录
全部类型 全部基金 近三个月
全部交易汇总
3次 买入
2次 卖出
共7,030.00元
买入
基金 | 万家宏观择时多策略灵活配置混合C
2026-08-07 14:32:15
1,500.00元
交易成功
卖出
基金 | 博时黄金ETF联接A
2026-08-06 10:05:41
800.00元
交易成功
买入
基金 | 鹏扬中证数字经济主题ETF联接C
2026-08-05 11:18:03
2,000.00元
交易成功
卖出
基金 | 新华鑫科技3个月滚动持有灵活配置混合A
2026-08-04 15:02:57
1,200.00元
交易成功
买入
基金 | 华夏中证电网设备主题ETF联接A
2026-08-03 09:41:22
1,530.00元
交易进行中
仅展示近三个月交易记录
"""

# 用户 2026-08-13 上传的「交易分析」页：顶栏与持仓总览共用 Tab，汇总用「笔」而不是「次」。
ALIPAY_TRANSACTION_ANALYSIS_OCR = """交易分析
全部持有 收益分析 配置分析 交易分析
近一年
全部交易汇总
买入
65笔
124,500.00元
卖出
61笔
109,286.55元
定投/发车
3笔
30.00元
分红
0笔
现金分红 0.00元
红利再投 0份
预约
0笔
0.00元
明细
基金
买入 基金
招商医疗保健股票A
2,000.00元
2026-08-13 14:55:30
买入 基金
万家宏观择时多策略灵活配置混合C
200.00元
2026-08-13 14:43:38
"""

ALIPAY_TRANSACTION_ANALYSIS_COMPACT_OCR = """全部持有 收益分析 配置分析 交易分析
近一年
全部交易汇总
买入 65笔 124,500.00元
卖出 61笔 109,286.55元
定投/发车 3笔 30.00元
明细 基金
买入 基金 招商医疗保健股票A 2,000.00元
2026-08-13 14:55:30
卖出 基金 博时黄金ETF联接A 800.00元
2026-08-06 10:05:41
"""


def _compact(parsed) -> list[tuple[str, str, float, str, bool]]:
    return [
        (
            item.direction,
            item.fund_name,
            item.amount_yuan,
            item.trade_time,
            item.in_progress,
        )
        for item in parsed
    ]


def test_transaction_records_layout_keeps_five_trades() -> None:
    parsed = parse_alipay_transactions(ALIPAY_TRANSACTION_RECORDS_OCR)
    assert detect_ocr_source(ALIPAY_TRANSACTION_RECORDS_OCR) == "alipay_transactions"
    assert _compact(parsed) == [
        ("buy", "万家宏观择时多策略灵活配置混合C", 1500.0, "2026-08-07 14:32:15", False),
        ("sell", "博时黄金ETF联接A", 800.0, "2026-08-06 10:05:41", False),
        ("buy", "鹏扬中证数字经济主题ETF联接C", 2000.0, "2026-08-05 11:18:03", False),
        ("sell", "新华鑫科技3个月滚动持有灵活配置混合A", 1200.0, "2026-08-04 15:02:57", False),
        ("buy", "华夏中证电网设备主题ETF联接A", 1530.0, "2026-08-03 09:41:22", True),
    ]


def test_transaction_analysis_page_is_not_classified_as_holdings() -> None:
    lines = [line.strip() for line in ALIPAY_TRANSACTION_ANALYSIS_OCR.splitlines() if line.strip()]
    assert is_alipay_transaction_page(lines)
    assert detect_ocr_source(ALIPAY_TRANSACTION_ANALYSIS_OCR) == "alipay_transactions"
    assert detect_ocr_source(ALIPAY_OVERVIEW_OCR) == "alipay_holdings"


def test_transaction_analysis_layout_parses_detail_rows_not_summary() -> None:
    parsed = parse_alipay_transactions(ALIPAY_TRANSACTION_ANALYSIS_OCR)
    assert _compact(parsed) == [
        ("buy", "招商医疗保健股票A", 2000.0, "2026-08-13 14:55:30", False),
        ("buy", "万家宏观择时多策略灵活配置混合C", 200.0, "2026-08-13 14:43:38", False),
    ]


def test_parsed_trades_carry_confirm_and_first_return_dates() -> None:
    text = """交易记录
全部交易汇总
买入
1次
卖出
1次
买入
基金 | 测试基金A
2026-06-10 14:55:30
2,000.00元
交易成功
卖出
基金 | 测试基金B
2026-06-10 15:01:00
800.00元
交易成功
"""
    parsed = parse_alipay_transactions(text)
    assert parsed[0].direction == "buy"
    assert parsed[0].confirm_date == "2026-06-10"
    assert parsed[0].first_return_date == "2026-06-11"
    assert parsed[1].direction == "sell"
    assert parsed[1].confirm_date == "2026-06-11"
    assert parsed[1].first_return_date == "2026-06-12"


def test_transaction_analysis_compact_ocr_does_not_eat_summary_totals() -> None:
    parsed = parse_alipay_transactions(ALIPAY_TRANSACTION_ANALYSIS_COMPACT_OCR)
    assert detect_ocr_source(ALIPAY_TRANSACTION_ANALYSIS_COMPACT_OCR) == "alipay_transactions"
    assert _compact(parsed) == [
        ("buy", "招商医疗保健股票A", 2000.0, "2026-08-13 14:55:30", False),
        ("sell", "博时黄金ETF联接A", 800.0, "2026-08-06 10:05:41", False),
    ]


def test_transaction_analysis_amount_on_direction_row() -> None:
    """交易分析常见两列：左「买入 基金」、右金额，OCR 常读成同一行。"""
    text = """全部持有 收益分析 配置分析 交易分析
全部交易汇总
买入 65笔 124,500.00元
明细
买入 基金 2,000.00元
招商医疗保健股票A
2026-08-13 14:55:30
买入 基金 200.00元
万家宏观择时多策略灵活配置混合C
2026-08-13 14:43:38
"""
    parsed = parse_alipay_transactions(text)
    assert detect_ocr_source(text) == "alipay_transactions"
    assert _compact(parsed) == [
        ("buy", "招商医疗保健股票A", 2000.0, "2026-08-13 14:55:30", False),
        ("buy", "万家宏观择时多策略灵活配置混合C", 200.0, "2026-08-13 14:43:38", False),
    ]


def test_column_major_ocr_keeps_amount_above_direction() -> None:
    """两列排版时 VLM 可能先读右侧金额，再读左侧「买入 基金」。"""
    text = """全部持有 收益分析 配置分析 交易分析
全部交易汇总
2,000.00元
买入 基金
招商医疗保健股票A
2026-08-13 14:55:30
800.00元
卖出 基金
博时黄金ETF联接A
2026-08-06 10:05:41
"""
    parsed = parse_alipay_transactions(text)
    assert _compact(parsed) == [
        ("buy", "招商医疗保健股票A", 2000.0, "2026-08-13 14:55:30", False),
        ("sell", "博时黄金ETF联接A", 800.0, "2026-08-06 10:05:41", False),
    ]


def test_same_line_timestamp_and_amount_still_parse() -> None:
    text = """交易记录
全部交易汇总
买入
基金 | 万家宏观择时多策略灵活配置混合C
2026-08-07 14:32:15 1,500.00元
交易成功
"""
    parsed = parse_alipay_transactions(text)
    assert _compact(parsed) == [
        ("buy", "万家宏观择时多策略灵活配置混合C", 1500.0, "2026-08-07 14:32:15", False),
    ]


def test_transaction_analysis_20260817_page_parses_six_buys() -> None:
    text = """交易分析
全部持有 收益分析 配置分析 交易分析
近一年
全部交易汇总
买入 74次 131,500.00元
卖出 61次 109,286.55元
定投/发车 3次 30.00元
分红 0次
预约 0次
买入 基金 1,000.00元
南方黄金股指数C
2026-08-17 14:59:52
买入 基金 1,000.00元
招商医疗保健股票A
2026-08-17 14:59:35
买入 基金 2,000.00元
华夏半导体材料设备ETF联接A
2026-08-17 14:58:45
买入 基金 500.00元
南方黄金股指数C
2026-08-14 14:59:57
买入 基金 300.00元
嘉实中证稀土产业ETF联接C
2026-08-14 14:57:23
买入 基金 1,000.00元
博时黄金ETF联接A
2026-08-14 14:56:15
"""
    parsed = parse_alipay_transactions(text)
    assert detect_ocr_source(text) == "alipay_transactions"
    assert _compact(parsed) == [
        ("buy", "南方黄金股指数C", 1000.0, "2026-08-17 14:59:52", False),
        ("buy", "招商医疗保健股票A", 1000.0, "2026-08-17 14:59:35", False),
        ("buy", "华夏半导体材料设备ETF联接A", 2000.0, "2026-08-17 14:58:45", False),
        ("buy", "南方黄金股指数C", 500.0, "2026-08-14 14:59:57", False),
        ("buy", "嘉实中证稀土产业ETF联接C", 300.0, "2026-08-14 14:57:23", False),
        ("buy", "博时黄金ETF联接A", 1000.0, "2026-08-14 14:56:15", False),
    ]


def test_transaction_analysis_time_then_amount_does_not_steal_previous_amount() -> None:
    """交易分析现网版式：方向+名称 → 时间 → 金额。上一笔金额不能滑到下一笔。"""
    text = """全部持有 收益分析 配置分析 交易分析
全部交易汇总
近一年
74次买入
共131,500.00元
62次卖出
共109,526.42元
明细 基金
卖出 基金 | 国泰国证房地产行业指数(LOF)A
2026-08-19 14:37:26
239.87元
买入 基金 | 南方黄金股指数C
2026-08-17 14:59:52
1,000.00元
买入 基金 | 招商医疗保健股票A
2026-08-17 14:59:35
1,000.00元
买入 基金 | 华夏半导体材料设备ETF联接A
2026-08-17 14:58:45
2,000.00元
买入 基金 | 南方黄金股指数C
2026-08-14 14:59:57
500.00元
买入 基金 | 嘉实中证稀土产业ETF联接C
2026-08-14 14:57:23
300.00元
"""
    parsed = parse_alipay_transactions(text)
    assert detect_ocr_source(text) == "alipay_transactions"
    assert _compact(parsed) == [
        ("sell", "国泰国证房地产行业指数(LOF)A", 239.87, "2026-08-19 14:37:26", False),
        ("buy", "南方黄金股指数C", 1000.0, "2026-08-17 14:59:52", False),
        ("buy", "招商医疗保健股票A", 1000.0, "2026-08-17 14:59:35", False),
        ("buy", "华夏半导体材料设备ETF联接A", 2000.0, "2026-08-17 14:58:45", False),
        ("buy", "南方黄金股指数C", 500.0, "2026-08-14 14:59:57", False),
        ("buy", "嘉实中证稀土产业ETF联接C", 300.0, "2026-08-14 14:57:23", False),
    ]


def test_transaction_analysis_fund_pipe_layout_from_holdings_tab_neighbor() -> None:
    """收益明细「交易分析」页：明细是「卖出 / 基金 | 名称 / 时间 / 金额元」。

    同步持仓不该吃这张图；批量加减仓必须能解析。顶栏仍有「全部持有」Tab。
    """
    text = """14:00
全部持有 收益分析 配置分析 交易分析
全部交易汇总
近一年
74次 买入
共131,500.00元
62次 卖出
共109,526.42元
3次 定投/发车
共30.00元
0次 分红
现金分红0.00元
红利再投资0份
0次 预约
共0.00元
清仓分析
分析复盘历史持仓
明细
基金
全部
卖出
基金 | 国泰国证房地产行业指数(LOF)A
2026-08-19 14:37:26
239.87元
买入
基金 | 南方黄金股指数C
2026-08-17 14:59:52
1,000.00元
买入
基金 | 招商医疗保健股票A
2026-08-17 14:59:35
1,000.00元
买入
基金 | 华夏半导体材料设备ETF联接A
2026-08-17 14:58:45
2,000.00元
买入
基金 | 南方黄金股指数C
2026-08-14 14:59:57
500.00元
买入
基金 | 嘉实中证稀土产业ETF联接C
2026-08-14 14:57:23
300.00元
"""
    assert detect_ocr_source(text) == "alipay_transactions"
    assert parse_holdings_from_text(text) == []
    parsed = parse_alipay_transactions(text)
    assert _compact(parsed) == [
        ("sell", "国泰国证房地产行业指数(LOF)A", 239.87, "2026-08-19 14:37:26", False),
        ("buy", "南方黄金股指数C", 1000.0, "2026-08-17 14:59:52", False),
        ("buy", "招商医疗保健股票A", 1000.0, "2026-08-17 14:59:35", False),
        ("buy", "华夏半导体材料设备ETF联接A", 2000.0, "2026-08-17 14:58:45", False),
        ("buy", "南方黄金股指数C", 500.0, "2026-08-14 14:59:57", False),
        ("buy", "嘉实中证稀土产业ETF联接C", 300.0, "2026-08-14 14:57:23", False),
    ]


ALIPAY_PENDING_SHARE_SELLS_OCR = """交易分析
全部持有 收益分析 配置分析 交易分析
明细
基金
全部
买入
南方黄金股指数C
10,000.00元
2026-08-24 14:56:04
交易进行中
买入
万家宏观择时多策略灵活配置混合C
10,000.00元
2026-08-24 14:55:06
交易进行中
买入
博时黄金ETF联接A
10,000.00元
2026-08-24 14:54:28
交易进行中
卖出
华夏半导体材料设备ETF联接A
401.71份
2026-08-24 14:34:25
预计08-25 24点前到账
卖出
华夏半导体材料设备ETF联接A
133.91份
2026-08-24 14:26:16
预计08-25 24点前到账
卖出
嘉实中证稀土产业ETF联接C
1,431.92份
2026-08-24 13:15:17
预计08-25 24点前到账
卖出
鹏扬中证数字经济主题ETF联接C
278.13份
2026-08-24 13:14:45
预计08-25 24点前到账
"""


def test_compact_share_sell_row_is_full_exit() -> None:
    text = """全部持有 收益分析 配置分析 交易分析
明细
卖出 基金 华夏半导体材料设备ETF联接A 401.71份
2026-08-24 14:34:25
预计08-25 24点前到账
"""
    parsed = parse_alipay_transactions(text)
    assert len(parsed) == 1
    assert parsed[0].direction == "sell"
    assert parsed[0].fund_name == "华夏半导体材料设备ETF联接A"
    assert parsed[0].confirmed_shares == 401.71
    assert parsed[0].full_exit is True
    assert parsed[0].in_progress is True


def test_pending_share_sells_parse_as_full_exit() -> None:
    parsed = parse_alipay_transactions(ALIPAY_PENDING_SHARE_SELLS_OCR)
    assert detect_ocr_source(ALIPAY_PENDING_SHARE_SELLS_OCR) == "alipay_transactions"
    buys = [item for item in parsed if item.direction == "buy"]
    sells = [item for item in parsed if item.direction == "sell"]
    assert _compact(buys) == [
        ("buy", "南方黄金股指数C", 10000.0, "2026-08-24 14:56:04", True),
        ("buy", "万家宏观择时多策略灵活配置混合C", 10000.0, "2026-08-24 14:55:06", True),
        ("buy", "博时黄金ETF联接A", 10000.0, "2026-08-24 14:54:28", True),
    ]
    assert [(item.fund_name, item.confirmed_shares, item.full_exit, item.in_progress) for item in sells] == [
        ("华夏半导体材料设备ETF联接A", 401.71, True, True),
        ("华夏半导体材料设备ETF联接A", 133.91, True, True),
        ("嘉实中证稀土产业ETF联接C", 1431.92, True, True),
        ("鹏扬中证数字经济主题ETF联接C", 278.13, True, True),
    ]
    assert all(item.amount_yuan == 0.0 for item in sells)


def test_holdings_overview_tabs_alone_are_not_a_transaction_page() -> None:
    lines = [line.strip() for line in ALIPAY_OVERVIEW_OCR.splitlines() if line.strip()]
    assert not is_alipay_transaction_page(lines)
    assert parse_alipay_transactions(ALIPAY_OVERVIEW_OCR) == []
