# 日报 AI 注入板块资金流 — 设计说明

**日期：** 2026-06-23  
**状态：** 已确认（按持仓关联板块注入，含当日+近5日摘要与 pattern 提示）

## 目标

生成日报时，在 `analysis_facts.holdings[]` 为每只基金附加其**关联板块**的主力净流入数据，供模型判断高位出货、低位洗盘、量价背离等。

## 数据源

复用 `board_fund_flow_history`（东财 `fflow/daykline`，BK 码与主题榜/ canonical 一致）。

## 注入字段（`sector_fund_flow`）

| 字段 | 说明 |
|------|------|
| `available` | 是否拉到数据 |
| `board_code` | BK 码 |
| `today_main_force_net_yi` | 当日主力净流入（亿） |
| `cumulative_5d_net_yi` | 近 5 交易日累计 |
| `cumulative_20d_net_yi` | 近 20 交易日累计 |
| `recent_5d_main_force_yi` | 近 5 日逐日主力净流入（最多 5 点） |
| `flow_tiers` | 当日四档（超大/大/中/小） |
| `pattern_label` | 量价资金 heuristic 标签 |
| `pattern_hint` | 中文解读（供模型引用） |

## 方案

- **按鋪全量 20 日序列**到 LLM（控 token）；UI 图表仍走独立 API。
- **按板块去重拉取**（同板块多只基共享一次 HTTP/缓存）。
- 解析 BK：`theme 白名单` → `canonical.source_code`。
- `trim_analysis_facts_for_llm` 保留 `sector_fund_flow`（fast 模式仅保留摘要字段）。

## 竞品

东财/同花顺用「价涨量跌=出货」规则；养基宝/小倍未把板块资金流喂给 AI。本方案对齐东财 heuristic + 项目已有 `sector_momentum` 风格。
