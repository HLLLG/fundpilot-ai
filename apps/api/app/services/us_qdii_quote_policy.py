"""小倍「美股基金涨跌助手」时段数据口径（调研结论 + 实现对齐）。

竞品公开文档未披露 QDII 穿透公式，以下由 image1 截图、HaoETF/同类工具与实跑
反推，供 ``us_stock_quote_client`` / ``us_market_service`` 统一引用。

小倍 UI 语义（image1，北京时间 06-18 04:14，状态「已收盘」）
----------------------------------------------------------------
- 顶部：纳斯达克 / 标普500 / 道琼斯 / 汇率 — 数值与**美股指数收盘涨跌**一致
  （如纳 -1.34%），而非当日盘前期货（实跑期货可能已转正）。
- 列表：15 只基金均标「夜盘」，参考涨跌**逐只不同** → 重仓穿透，非单一指数×系数。
- 指数普跌时仍有基金为正 → 必须用持仓加权，不能用统一期货乘子。

推断的时段矩阵
--------------

+------------------+---------------------------+-------------------------------+------------------+
| US_Session_Kind  | 顶部指标（我们方案 C）      | QDII 个股涨跌口径               | 竞品列表头       |
+==================+===========================+===============================+==================+
| pre_market       | 指数期货实时              | 成分股**盘前/实时**（昨收→现价）| 盘前（推断）     |
| regular          | 指数期货实时              | 成分股**盘中实时**              | 盘中（推断）     |
| after_hours      | 东财 push2delay 全球指数现货收盘 | 成分股**push2delay 夜盘涨跌**（105→106 回退） | 已收盘 + 夜盘    |
| closed           | 东财 push2delay 全球指数现货收盘 | 成分股**push2delay 夜盘涨跌** | 已收盘 + 夜盘    |
+------------------+---------------------------+-------------------------------+------------------+

估值合并（``us_qdii_valuation_service``）
----------------------------------------
- **指数型**（tracking_factor≥0.95 或名称含纳斯达克/标普）：天天基金 > 指数系数 > 穿透
- **主动型全球基金**（如易方达全球成长精选）：穿透 > 天天基金 > 指数系数

与 A 股「养基宝」类比：白天用重仓股**实时**估算；收盘后用**官方收盘**口径。

无法从公开信息复现的部分
------------------------
- 持仓是否比季报更新（模型持仓 / 上期年报融合）—— 012920 等全球基金偏差主因。
- 是否对单票涨跌做截断 / 行业替代 — 无证据，本期不做。
"""

from __future__ import annotations

from typing import Literal

UsQuoteMode = Literal["live", "rth_close"]

_LIVE_SESSIONS = frozenset({"pre_market", "regular"})


def quote_mode_for_session(session_kind: str) -> UsQuoteMode:
    """返回 QDII 穿透估值应使用的个股涨跌口径。"""
    return "live" if session_kind in _LIVE_SESSIONS else "rth_close"


def prefer_us_daily_for_mode(mode: UsQuoteMode) -> bool:
    """美股是否优先用 ``stock_us_daily`` 收盘口径。"""
    return mode == "rth_close"


def estimate_basis_suffix(mode: UsQuoteMode) -> str:
    if mode == "live":
        return "盘前/盘中实时行情"
    return "最近交易日收盘涨跌"
