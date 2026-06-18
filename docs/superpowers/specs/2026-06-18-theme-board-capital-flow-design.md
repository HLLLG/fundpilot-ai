# 主题板块 — 主力资金与四档净流入设计

**日期：** 2026-06-18  
**状态：** 已批准（方案 B）  
**关联：** `2026-06-17-market-theme-boards-design.md`、`2026-06-17-market-sector-performance-design.md`

## 目标

在「市场 → 主题板块」涨幅榜中，为每个东财 BK 板块展示 **主力净流入**，点击行展开 **四档订单净流入**（超大/大/中/小单，UI 副标机构/大户/散户）。

## 数据来源

复用东财 `push2delay` `clist/get`，扩展 `fields`：

| 字段 | 含义 | API 输出 |
|------|------|----------|
| `f62` | 主力净流入（超大+大单） | `main_force_net_yi` |
| `f66` | 超大单 | `flow_tiers.super_large_net_yi` |
| `f72` | 大单 | `flow_tiers.large_net_yi` |
| `f78` | 中单 | `flow_tiers.medium_net_yi` |
| `f84` | 小单 | `flow_tiers.small_net_yi` |

主题层 **不重复拉 clist**：从已有 `get_sector_board_snapshot()` 缓存按 `source_code` 合并；与全市场 Tab 同源、同 TTL。

## 覆盖与降级

| 板块类型 | 主力/四档 |
|----------|-----------|
| 东财行业/概念（`90.BKxxxx`） | ✅ 有数据（~59/66） |
| 中证指数主题（`2.93xxxx` 等） | ❌ 显示 `—`（东财无板块级资金流） |
| 未匹配 code | ❌ `—` |

## API

`GET /api/market/theme-boards?sort=change|streak|inflow`

响应 item 新增：

```json
{
  "main_force_net_yi": 58.29,
  "flow_tiers": {
    "super_large_net_yi": 108.1,
    "large_net_yi": -49.81,
    "medium_net_yi": -61.84,
    "small_net_yi": 2.51
  }
}
```

无数据时 `main_force_net_yi: null`、`flow_tiers: null`。

## 前端

- 表头新增「主力净流入」列；排序 pill 增加「资金流入」（`sort=inflow`）。
- 有资金数据的行可点击展开，展示四档 2×2 网格；脚注：主力 = 超大单 + 大单。
- 指数主题行不可展开。

## 非目标

- 指数主题资金流估算（成分股聚合）。
- 分时资金曲线、历史 5 日资金（Phase 2）。
