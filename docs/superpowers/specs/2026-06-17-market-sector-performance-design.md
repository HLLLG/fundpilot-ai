# 市场 Tab — 板块表现设计

**日期：** 2026-06-17  
**状态：** 已实现  
**关联需求：** 对标蚂蚁财富「板块表现 / 热门板块」

## 背景

用户希望在底部导航新增独立 **「市场」** Tab，展示全市场板块涨跌与主力净流入概况，点击进入详情列表。首期采用 **核心版 MVP**，不做搜索飙升、估值、买入按钮、全量 sparkline。

## 目标

1. 新 Tab「市场」与持有/盈亏分析/推荐基金/生成日报同级
2. 板块表现卡片：涨跌幅 / 主力净流入切换，各 Top3 + Bottom3（红涨绿跌）
3. 热门板块详情：行业 / 概念分类；按涨幅或资金流入排序
4. 服务端 + 前端双层缓存，避免每次打开拉取东财全量列表（~11s）

## 非目标（首期）

- 搜索飙升、估值排序
- 板块下挂基金推荐与「买入」
- 全列表 sparkline / 实时异动 ticker
- 60日新高、连涨 N 日等历史标签
- 宽指主题 / 全球主题 Tab（仅行业 + 概念）

---

## 数据来源

东财 `push2` `clist/get`（复用 `eastmoney_spot_client` host 池与分页逻辑）：

| 字段 | 含义 |
|------|------|
| `f14` | 板块名称 |
| `f12` | 板块代码（如 BKxxxx） |
| `f3` | 当日涨跌幅 % |
| `f62` | 主力净流入（元，展示时 ÷1e8 为「亿」） |

| 分类 | `fs` 参数 | 约数量 |
|------|-----------|--------|
| 行业 | `m:90 t:2`（**勿加** `f:!50`） | ~86 经典行业 |
| 概念 | `m:90 t:3` | ~400+ |

> `f:!50` 会返回东财**细分**行业（如「防水材料」），与蚂蚁财富卡片上的「建筑材料」等经典口径不一致。

拉取策略：**一次请求同时取 f3 + f62 + f12 + f14**，行业与概念各分页拉全量，合并写入服务端缓存。

---

## 架构

```text
东财 push2 clist
    ↓
sector_board_snapshot.py（新建）
    - fetch_sector_board_snapshot()
    - build_widget_summary()  → Top3/Bottom3
    - build_sector_list()     → 详情排序列表
    ↓
sector_quote_cache（已有 spot 快照表）
    cache_key: market:sector_boards:v1:{trade_date}
    TTL: 60s（盘中 intraday/pre_close）/ 3600s（收盘后）
    ↓
GET /api/market/sector-boards
    ?view=widget|list
    &board_type=industry|concept
    &sort=change|inflow
    ↓
MarketTab（新建）
    SectorPerformanceCard → 点击展开/路由内详情 HotSectorList
    useCachedFetch + sessionStorage 60s
```

---

## API 设计

### `GET /api/market/sector-boards`

**Query 参数：**

| 参数 | 默认 | 说明 |
|------|------|------|
| `view` | `widget` | `widget` 仅返回摘要；`list` 返回完整排序列表 |
| `board_type` | `industry` | `industry` \| `concept`（`view=list` 时有效） |
| `sort` | `change` | `change`（涨幅）\| `inflow`（主力净流入） |
| `force_refresh` | false | 跳过缓存强制拉取（详情页手动刷新用） |

**`view=widget` 响应：**

```json
{
  "trade_date": "2026-06-17",
  "session_kind": "trading_day_intraday",
  "from_cache": true,
  "metric": "change",
  "top_gainers": [
    { "name": "建筑材料", "code": "BK0425", "change_percent": 4.02, "main_force_net_yi": 12.5 }
  ],
  "top_losers": [ "..." ],
  "top_inflow": [ "..." ],
  "top_outflow": [ "..." ]
}
```

说明：widget 一次返回涨跌幅与净流入两套 Top3/Bottom3，前端切换 Tab 不再请求。

**`view=list` 响应：**

```json
{
  "trade_date": "2026-06-17",
  "board_type": "industry",
  "sort": "change",
  "from_cache": true,
  "items": [
    {
      "name": "建筑材料",
      "code": "BK0425",
      "change_percent": 4.02,
      "main_force_net_yi": 12.5,
      "rank": 1
    }
  ]
}
```

列表默认返回该分类下全部板块（~494），按 `sort` 降序；跌幅最大在 `sort=change` 时通过升序查看或前端提供「跌幅榜」切换（复用同一数据，本地反转）。

**错误处理：**

- 东财全失败：返回 200 + `available: false` + 空列表 + `message`（与 discovery sectors 超时回退一致）
- 部分 host 失败：沿用现有分页早停，有数据即缓存

---

## 前端设计

### 导航

- `Dashboard.tsx`：`TabId` 新增 `"market"`
- `primaryTabs` 插入：`{ id: "market", label: "市场" }`（建议放在「盈亏分析」与「推荐基金」之间）
- `TabNav` 类型与 `highlightedTab` 逻辑同步扩展

### 页面结构 `MarketTab.tsx`

1. **顶部**：`TradingSessionBar`（复用）+ 数据日期
2. **板块表现卡片** `SectorPerformanceCard`
   - 标题「板块表现」+ 右箭头 → 滚动/展开至详情区
   - Pill 切换：「涨跌幅」|「主力净流入」
   - 2×3 网格：上排红底 Top3 涨幅/流入，下排绿底 Top3 跌幅/流出
   - 数值格式：涨跌幅 `+x.xx%`；净流入 `x.xx亿` / `-x.xx亿`
3. **热门板块详情** `HotSectorList`（同页下方，或卡片点击 `scrollIntoView`）
   - Tab：行业 | 概念
   - 排序 Pill：涨幅领先 | 资金流入
   - 表头：板块名称 | 日涨幅 或 主力净流入
   - 行：名称 + 数值（红涨绿跌）
   - 下拉刷新 / 刷新按钮 → `force_refresh=true`

### 缓存

- `useCachedFetch`：`cacheKey = market-sector-boards-widget`，`staleTimeMs = 60_000`（盘中）
- 详情列表：`cacheKey` 含 `board_type` + `sort`，`staleTimeMs = 60_000`
- 与 `discovery sector heat` 独立 key，互不影响

### 样式

- 对齐现有 Tailwind + `profit-up` / `profit-down` + `tab-segment` 风格
- 卡片使用现有 `card` 圆角与暗色主题变量，不引入新设计系统

---

## 后端实现要点

### 新文件 `sector_board_snapshot.py`

- `fetch_sector_board_rows(board_type)` → `list[SectorBoardRow]`
- 扩展 `eastmoney_spot_client`：新增 `fetch_eastmoney_boards_extended(fields=f3,f14,f12,f62)` 或在本模块封装分页
- `get_sector_board_snapshot(force_refresh=False)` → 缓存读写
- `build_widget_payload(snapshot)` / `build_list_payload(snapshot, board_type, sort)`

### 路由 `main.py`

```python
@app.get("/api/market/sector-boards")
def market_sector_boards(...): ...
```

### 测试 `test_sector_board_snapshot.py`

- stub 东财返回，断言 Top3/Bottom3 切分正确
- 净流入单位换算（f62 → 亿，保留 2 位小数）
- 缓存命中不重复拉取
- API 集成测 `test_api.py` 增 1 条 smoke

---

## 性能预算

| 场景 | 目标 |
|------|------|
| 缓存命中 | API < 50ms |
| 冷启动全量拉取 | ≤ 15s（后台一次），用户仍先看到缓存或 loading |
| 前端首屏 | 有 session 缓存时 < 100ms 展示 |

可选优化（首期不做）：API `lifespan` 交易时段每 60s 预热缓存。

---

## 验收标准

1. 底部出现「市场」Tab，切换正常
2. 板块表现卡片展示涨跌幅 Top3+Bottom3，切换主力净流入展示对应柱状/网格数据
3. 详情列表可切换行业/概念、涨幅/资金流入，排序正确
4. 60s 内重复进入 Tab 不触发东财全量请求（日志或测试验证 `from_cache: true`）
5. `pytest` 全绿；`web` lint / typecheck / build 通过

---

## 文件清单（实现时）

| 层 | 文件 |
|----|------|
| API 服务 | `apps/api/app/services/sector_board_snapshot.py` |
| API 扩展 | `apps/api/app/services/eastmoney_spot_client.py`（可选 f62 字段） |
| 路由 | `apps/api/app/main.py` |
| 测试 | `apps/api/tests/test_sector_board_snapshot.py` |
| 前端页面 | `apps/web/src/components/MarketTab.tsx` |
| 子组件 | `SectorPerformanceCard.tsx`, `HotSectorList.tsx` |
| 集成 | `Dashboard.tsx`, `api.ts` |
| 文档 | `docs/PROJECT_CONTEXT.md`（能力清单 + API 表） |
