# 市场 Tab — 主题板块（养基宝口径）设计

**日期：** 2026-06-17  
**状态：** 已实现  
**父方案：** 混合 C（全市场蚂蚁口径 + 主题养基宝口径）  
**关联：** `2026-06-17-market-sector-performance-design.md`（已实现的全市场层）

## 背景

竞品对比结论：

- **蚂蚁财富「热门板块」**：东财经典行业/概念全市场榜 + 多维热度 + 转化（买入）。
- **养基宝「板块总览」**：基金主题标签 + 日涨幅 + 连涨天数 + 关联基金数，与持仓「关联板块」同源。

FundPilot 已实现蚂蚁口径（`GET /api/market/sector-boards` + `MarketTab`）。用户确认采用 **混合 C**，并在市场 Tab 内用 **子 Tab「全市场 | 主题板块」**（选项 A）组织两层内容。

## 目标

1. 在市场 Tab 增加 **主题板块** 子视图，展示 canonical 19 个主题标签的养基宝式表格。
2. 新增 `GET /api/market/theme-boards`：日涨幅、连涨天数、关联基金数、用户持仓重合。
3. 与 OCR / 荐基 / 持有页的 `sector_canonical` 标签体系保持一致。
4. 服务端 + 前端双层缓存，控制东财 K 线请求量。

## 非目标（本期）

- 养基宝式「持有排名」（无社区持仓数据，用「我的持仓」替代）。
- 全市场 sparkline、实时异动 ticker、搜索飙升、估值排序（Phase 2/3）。
- 主题行挂基金推荐与「买入」按钮。
- 扩展超过 19 个主题标签（后续可独立迭代）。
- 点击主题行跳转荐基 Tab（可选增强，本期不做）。

---

## 信息架构

```text
市场 Tab
├── 子 Tab：全市场（默认）
│   ├── TradingSessionBar
│   ├── SectorPerformanceCard（涨跌幅 / 主力净流入 Top3+Bottom3）
│   └── HotSectorList（行业 | 概念；涨幅 | 资金流入）
└── 子 Tab：主题板块
    ├── TradingSessionBar
    └── ThemeSectorOverview（养基宝式表格）
```

两层数据**不合并名单**：全市场用东财 `m:90 t:2` 经典行业 + `m:90 t:3` 概念；主题层用 `list_discovery_sector_labels()` 返回的 19 个标签。

---

## 数据来源与字段口径

### 主题列表

`sector_canonical.list_discovery_sector_labels()` — 与荐基关注方向、波段盯盘、OCR canonical 映射同源。

### 日涨幅 `change_1d_percent`

复用 `discovery_sector_heat._sector_heat_row` 逻辑：`get_quote_canonical_sector` → `fetch_eastmoney_kline_close_percent`（有效交易日 `effective_trade_date`）。

### 连涨天数 `consecutive_up_days`

对每个 canonical 板块：

1. 拉取日 K 序列 `fetch_eastmoney_daily_kline_series(secid, max_days=20)`。
2. 取 `effective_trade_date` 对应 bar（若无则取最后一根）。
3. 从该 bar 向前遍历：若 `change_percent > 0` 则 `streak += 1`，否则停止。
4. 若当日 bar 缺失或 `change_percent` 为 null，`consecutive_up_days = null`（前端展示 `—`）。

> 口径对齐养基宝「连涨天数」：按**板块指数/概念日涨跌**计，非基金净值连涨。

### 关联基金数 `linked_fund_count`

去重计数以下来源的 `fund_code`（按 `sector_label` 聚合）：

1. `GLOBAL_FUND_SECTOR_SEEDS` 中 `sector_name == sector_label` 的条目。
2. `list_fund_primary_sectors()` 中 `sector_name == sector_label` 的条目。

首期**不**扫描东财全市场基金排行做关键词匹配（避免慢请求；数字可能小于养基宝，随用户 OCR / 映射增长）。

### 我的持仓 `held_fund_count` / `in_portfolio`

- 需 JWT；从当前用户 `holdings` 解析 `sector_name`，经 `normalize_sector_label` + canonical 匹配到 `sector_label`。
- `held_fund_count`：该主题下用户持有基金只数。
- `in_portfolio`：`held_fund_count > 0`。
- 未登录：两项分别为 `0` / `false`（API 仍 200，不强制鉴权）。

---

## API 设计

### `GET /api/market/theme-boards`

**Query：**

| 参数 | 默认 | 说明 |
|------|------|------|
| `sort` | `change` | `change`（日涨幅降序）\| `streak`（连涨天数降序） |
| `force_refresh` | false | 跳过服务端缓存 |

**响应：**

```json
{
  "trade_date": "2026-06-17",
  "session_kind": "trading_day_intraday",
  "available": true,
  "from_cache": false,
  "stale": false,
  "message": null,
  "sort": "change",
  "items": [
    {
      "sector_label": "商业航天",
      "change_1d_percent": 2.78,
      "consecutive_up_days": 5,
      "linked_fund_count": 4,
      "held_fund_count": 1,
      "in_portfolio": true,
      "rank": 1
    }
  ]
}
```

**错误 / 降级：**

- 东财全失败：200 + `available: false` + 空 `items` + `message`；若有陈旧缓存则 `stale: true` 并返回上次数据。
- 部分板块 K 线失败：该板块对应数值为 `null`，其余正常；`available: true`（与 discovery sector heat 一致）。

**鉴权：** 可选 JWT；有则填充 `held_fund_count` / `in_portfolio`，无则默认 0/false。

---

## 后端架构

```text
list_discovery_sector_labels()
    ↓
theme_board_snapshot.py（新建）
    - compute_consecutive_up_days(series, trade_date)
    - count_linked_funds(sector_label)
    - build_theme_board_rows(holdings?)
    - get_theme_board_snapshot(force_refresh, holdings?)
    ↓
sector_quote_cache
    cache_key: theme:boards:v1:{trade_date}
    payload: { items, trade_date, session_kind }
    TTL: 60s（盘中 intraday/pre_close）/ 3600s（收盘后）
    ↓
GET /api/market/theme-boards  (main.py)
```

### 与 `discovery_sector_heat` 的关系

- 主题板块 API 独立缓存 key，避免与荐基 UI 轻量接口互相污染。
- 可复用 `_sector_heat_row` 的 secid 解析与 K 线拉取；连涨天数在完整拉取路径计算（非 `lightweight`）。
- 并行：`ThreadPoolExecutor(max_workers=6)`，总预算 **15s**（19 板块 × 日 K，与 discovery 全量热度同级）。

### 新文件

| 文件 | 职责 |
|------|------|
| `apps/api/app/services/theme_board_snapshot.py` | 行构建、连涨计算、基金计数、缓存读写 |
| `apps/api/tests/test_theme_board_snapshot.py` | 连涨边界、基金计数、排序、缓存 |
| `apps/api/tests/test_api.py` | smoke：`GET /api/market/theme-boards` |

---

## 前端设计

### `MarketTab.tsx` 改造

- 顶部增加 segment：`全市场` | `主题板块`（`tab-segment` 样式，与 `HotSectorList` 一致）。
- `全市场`：现有 `SectorPerformanceCard` + `HotSectorList` 不变。
- `主题板块`：仅 `TradingSessionBar` + `ThemeSectorOverview`。
- 子 Tab 选择存入 `sessionStorage`（key `market-sub-tab`），默认 `market`。

### 新组件 `ThemeSectorOverview.tsx`

| 列 | 说明 |
|----|------|
| 板块名称 | `sector_label`；副文案 `{linked_fund_count}只基金` |
| 日涨幅 | 红涨绿跌，`+x.xx%` |
| 连涨天数 | 红色数字；null 显示 `—` |
| 我的持仓 | `{held_fund_count}只` 或 `—`；`in_portfolio` 行浅蓝背景 |

- 排序 pill：**涨幅领先** | **连涨天数**（映射 `sort=change|streak`）。
- 刷新按钮 → `force_refresh=true`。
- `useCachedFetch`：`cacheKey = market-theme-boards-{sort}`，`staleTimeMs = 60_000`，`storage = session`。

### `api.ts` 类型

```ts
export type MarketThemeBoardSort = "change" | "streak";
export interface MarketThemeBoardItem { ... }
export interface MarketThemeBoardResponse { ... }
export function fetchMarketThemeBoards(opts?: { sort?, forceRefresh? }): Promise<...>
```

---

## 性能预算

| 场景 | 目标 |
|------|------|
| 缓存命中 | API < 50ms |
| 冷启动（19 板块日 K 并行） | ≤ 15s；用户见 loading / 陈旧缓存 |
| 前端有 session 缓存 | 切换子 Tab < 100ms 展示 |

---

## 验收标准

1. 市场 Tab 出现子 Tab「全市场 | 主题板块」，切换正常，刷新后记住上次子 Tab。
2. 主题板块表展示 19 行，列：名称、日涨幅、连涨天数、关联基金数、我的持仓。
3. 排序「涨幅领先」「连涨天数」正确；有持仓行视觉高亮。
4. 60s 内重复请求 `from_cache: true`（测试或日志验证）。
5. `pytest` 全绿；`web` lint / typecheck / build 通过。
6. `docs/PROJECT_CONTEXT.md` 同步能力清单与 API 表。

---

## 后续 Phase（不在本期）

| Phase | 内容 |
|-------|------|
| 2 | 主题行迷你 sparkline；标签「连涨≥3天」 |
| 3 | 全市场实时异动 ticker；搜索飙升 / 估值 |
| 可选 | 点击主题行 → 荐基 Tab 预选 `focus_sector` |

---

## 文件清单（实现时）

| 层 | 文件 |
|----|------|
| API 服务 | `apps/api/app/services/theme_board_snapshot.py` |
| 路由 | `apps/api/app/main.py` |
| 测试 | `apps/api/tests/test_theme_board_snapshot.py`，`test_api.py` 增补 |
| 前端 | `ThemeSectorOverview.tsx`，`MarketTab.tsx`，`api.ts`，`marketThemeBoard.ts`（可选校验 helper） |
| 文档 | `docs/PROJECT_CONTEXT.md` |

---

## 实现备注（2026-06-17）

### 日 K 拉取链（共用 `sector_daily_kline_provider.py`）

```
东财板块日 K → sector-relay /kline/daily → AkShare 板块日 K → 新浪/AkShare 指数日 K
```

`discovery_sector_heat`、`sector_signal_backtest` 已复用该 provider。

### 主题板块数据策略（`theme_board_snapshot.py`）

1. **日涨幅（`change_1d_percent`）**：优先 AkShare/全市场现货榜 + **模糊名匹配**（如「医药」→「医药医疗」）；可复用 `sector_board_snapshot` 缓存。
2. **连涨天数**：后台并行拉日 K，12s 预算内 `wait()` + `shutdown(wait=False, cancel_futures=True)`，避免 ThreadPool 阻塞响应。
3. **缓存**：`theme:boards:v2:{trade_date}`；仅当 `has_live_quotes=true` 时写入；始终返回 19 行骨架。
4. **持仓叠加**：`resolve_holding_to_discovery_label` + secid 区分「半导体」与「中证半导体」等同名指数。

### 东财不可用时的表现

- 全市场层：AkShare 现货榜 → sector-relay 兜底。
- 主题层：现货榜模糊匹配仍可填充部分日涨幅；连涨天数依赖日 K fallback 链，可能为 `—`。

### 测试

- 新增 `test_sector_board_snapshot.py`、`test_theme_board_snapshot.py`、`test_sector_daily_kline_provider.py`。
- 全量 **228** 项 pytest 通过。
