# 板块主力净流入历史走势 — 设计说明

**日期：** 2026-06-23  
**状态：** 已确认

## 目标

在「市场 → 主题板块」展开行中，于四档资金流明细下方展示近一周（5 交易日）/ 近一月（20 交易日）主力净流入柱状图，便于观察走势。

## 数据源

- 现有 `theme-boards` 仅含当日 `f62` 快照，**不含历史**。
- 东财历史接口：`/api/qt/stock/fflow/daykline/get`，`secid=90.{BK_CODE}`，约 120 交易日。
- **Host 与可靠性（2026-06-23 修复）：** 默认 `63/28.push2his` 易 `Server disconnected`；实现优先 `80.push2his`、`82.push2his`，请求带 `_COMMON_PARAMS`（`ut` 等），4 轮 host 轮询 + 退避重试；空 klines 亦重试。连续批量请求会被东财限流，主题榜刷新后后台单线程限流预热缓存。
- BK 码与当日口径一致，复用 `theme_board_snapshot` 的 `flow_source_code`；指数主题涨跌幅用中证/国证指数 secid，资金流走东财 BK。显式映射见 `_THEME_BOARD_FLOW`（如医药→BK0465、贵金属→BK0732、化工→BK1206、交通运输→BK1210）。

## API

`GET /api/market/board-flow-history`

| 参数 | 说明 |
|------|------|
| `sector_label` | 主题榜板块名（与 `theme-boards` 一致） |
| `board_code` | 可选，直接传 `BKxxxx` |
| `range` | `week`（5 日）或 `month`（20 日），默认 `week` |

响应：`available`、`points[]`（`date`、`main_force_net_yi`）、`cumulative_net_yi`、`board_code`、`sector_label`。

缓存：按 `board_code` 存全量序列（`board-flow-hist:v1:{BK}`），TTL 盘中 15min / 收盘 1h；拉取失败时读任意年龄 stale cache。

## 实现文件

| 层 | 文件 |
|----|------|
| 后端拉取/缓存 | `apps/api/app/services/board_fund_flow_history.py` |
| BK 解析/预热 | `apps/api/app/services/theme_board_snapshot.py`（`_THEME_BOARD_FLOW`、`flow_source_code`、`prefetch`） |
| API | `apps/api/app/main.py` `GET /api/market/board-flow-history` |
| 前端 | `BoardFlowHistoryChart.tsx`、`ThemeSectorOverview.tsx`、`api.ts` |
| 测试 | `tests/test_board_fund_flow_history.py` |

## 前端

- 展开板块时懒加载；`近一周 | 近一月` 切换。
- 新组件 `BoardFlowHistoryChart`（SVG 柱状图，红涨绿跌）。
- 不改动 `theme-boards` 批量 payload。

## 竞品

东方财富板块资金页以日柱状图为主；小倍养基当前仅当日四档，本功能为差异化增强。
