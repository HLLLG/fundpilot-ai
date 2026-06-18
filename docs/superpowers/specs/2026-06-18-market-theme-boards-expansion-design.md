# 市场 Tab — 主题板块扩展（~100 板块 + 后台 15min 刷新）设计

**日期：** 2026-06-18
**状态：** 待实现
**父方案：** `2026-06-17-market-theme-boards-design.md`（已实现的 21 canonical 主题层）
**关联：** `2026-06-17-market-sector-performance-design.md`（全市场层）

## 背景

竞品「小倍养基」的「今日板块涨幅榜」覆盖约 **76** 个板块，而 FundPilot 当前主题板块仅 **21** 个 canonical 标签（`sector_canonical._DISCOVERY_CHIP_LABELS`）。用户希望：

1. 把主题板块扩展到 76+（与小倍相当）。
2. 确认涨跌数据源（已确认：东财 **push2delay** 日 K，`fetch_eastmoney_kline_close_percent` → `https://push2delay.eastmoney.com/api/qt/stock/kline/get`）。
3. 降低刷新频率到约 **15min**，**后台刷新、展示读缓存**，提高打开市场 Tab 的响应速度。

## 现状确认（调研结论）

- **板块全集**：当前主题列表 = `list_discovery_sector_labels()` 返回的 21 个硬编码 canonical 短名（与荐基关注方向同源）。
- **日涨幅来源**：`theme_board_snapshot._enrich_theme_board_daily_change` → `fetch_eastmoney_kline_close_percent`（canonical secid，push2delay 日 K，相对昨收）；失败回退现货榜模糊匹配 + AkShare。**确认即 push2delay 官方延迟行情。**
- **连涨天数来源**：`sector_daily_kline_provider.fetch_canonical_daily_kline_series`（push2delay 日 K → sector-relay → AkShare）。
- **全量板块能力已存在**：`eastmoney_spot_client.fetch_eastmoney_board_records("industry")`（`m:90 t:2`，约 86 个行业，含 name + BK 代码 f12 + 涨跌幅 f3 + 主力净流入 f62），全市场子 Tab 已在用。
- **刷新机制现状**：`get_theme_board_snapshot` 为**请求时惰性计算 + 缓存**（TTL：盘中 60s / 收盘 3600s），无后台定时刷新。
- **缓存后端**：`sector_quote_cache` = SQLite `sector_spot_cache` 表 + 进程内存双层，适合「后台写、前台读」。
- **lifespan 后台模式**：现有用 daemon 线程预热（`preload_fund_name_table`），无 APScheduler。

## 目标

1. 主题板块覆盖 **行业全量（~86）+ 21 canonical 概念/指数主题**，去重合并后约 **100 行**。
2. 涨跌幅与连涨天数**统一从同一段 push2delay 日 K 计算**，口径一致。
3. **后台 daemon 线程定时刷新**（时段感知：盘中 15min / 收盘 1h），接口与前端只读缓存。
4. 保留持仓高亮（概念主题精确命中），保持小倍式涨幅榜 UI。

## 非目标（本期）

- 拆成「行业榜 / 概念榜」两个子榜（用 `board_kind` 标签区分即可）。
- 概念板块全量（300+）纳入（噪音大、连涨天数拉取成本高）。
- 关联基金数 `linked_fund_count` 列（UI 已不展示，移除以减负）。
- sparkline、资金流入排序、点击跳荐基（后续 Phase）。
- 改动荐基关注方向 chips（仍保持 21 canonical，职责独立）。

---

## 口径决策（用户确认）

| 决策 | 选择 |
|------|------|
| 板块全集 | 行业全量 + 21 canonical 概念主题，合并去重（约 100） |
| 行业 vs 概念呈现 | 同一榜单，`board_kind` 小标签区分（行业 / 概念 / 指数） |
| 刷新机制 | 后台 daemon 定时线程 + 时段感知，前台读缓存 |
| 盘中涨跌幅刷新频率 | 接受 15min（与连涨天数同节奏，不再 60s） |

> **行业 vs 概念说明**：东财「行业板块」（`m:90 t:2`）是申万式一级行业（电子、计算机、医药生物、电力设备、银行…），**不含**「半导体、商业航天、CPO、PCB、消费电子」等概念主题（属 `m:90 t:3`）。用户持仓多为概念口径，故保留 21 canonical 概念主题单独成行，确保持仓高亮精确。

---

## 数据来源与字段口径

### 板块全集 `list_theme_board_universe()`（新）

合并两类来源，输出 `ThemeBoardUniverseItem`（`sector_label`、`secid`、`source_code`、`board_kind`）：

1. **行业全量**：`fetch_eastmoney_board_records("industry")` 的每行 → `secid = "90." + code`，`board_kind="industry"`，`sector_label = name`。
2. **canonical 概念/指数**：遍历 21 个 `list_discovery_sector_labels()`，取 `get_quote_canonical_sector(label)` → secid + `source_type`（concept/industry/index 映射到 board_kind）。

**去重规则**：按 `secid`（优先）或归一化板块名去重。canonical 与行业重名/同码时（如 有色金属 BK0478、银行 BK0475、证券 BK0473、国防军工 BK0490、医药）以 canonical 口径为准（保留其精确 secid 与 board_kind）。

行业 spot 拉取失败时，universe 退化为仅 21 canonical（保证不空榜）。

### 涨跌幅 `change_1d_percent` + 连涨天数 `consecutive_up_days`（同源）

后台对 universe 每个板块：

1. `fetch_canonical_daily_kline_series(canon, max_days=20)`（push2delay 日 K → relay → AkShare）。
2. 取 `effective_trade_date` 对应 bar（无则取最后一根）：
   - `change_1d_percent` = 该 bar `change_percent`（`is_plausible_daily_change` 校验）。
   - `consecutive_up_days` = 从该 bar 向前数 `change_percent > 0` 的连续天数（复用现有 `compute_consecutive_up_days`）。
3. 日 K 全失败兜底：`change_1d_percent` 取行业 spot 该 BK 代码的 f3（精确按代码，不模糊匹配）；`consecutive_up_days = null`（前端 `—`）。

> 因 universe 已携带每个板块的精确 secid/BK 代码，无需再按名称模糊匹配，去掉旧 `_lookup_spot_change` 模糊路径的歧义。

### 持仓 `held_fund_count` / `in_portfolio`

- 需 JWT；从当前用户 holdings 解析 `sector_name` → `get_quote_canonical_sector` → secid。
- 与板块行 `secid` 匹配：命中则 `held_fund_count += 1`，`in_portfolio = held_fund_count > 0`。
- 行业行（无 canonical）按 secid 兜底名称匹配。
- 未登录：0 / false（API 仍 200）。

### 移除字段

`linked_fund_count`（关联基金数）：UI 不展示，且 100 板块逐一计数成本高，本期移除（payload 与计算均删除）。

---

## 后端架构

```
list_theme_board_universe()                    # 行业 spot + canonical，去重，带 board_kind
        ↓
refresh_theme_board_snapshot()                 # 后台：~100 板块并行日 K，算 change+streak，写缓存
        ↓
sector_quote_cache  key: theme:boards:v3:{trade_date}
        payload: { items[], trade_date, session_kind, refreshed_at }
        ↓
get_theme_board_snapshot(force_refresh, holdings, sort)   # 只读缓存 + 持仓叠加；缓存空才同步兜底
        ↓
GET /api/market/theme-boards  (main.py 不变)
        ↑
theme_board_refresh_loop()  (lifespan daemon 线程，时段感知间隔)
```

### `theme_board_snapshot.py` 改造

| 函数 | 变化 |
|------|------|
| `list_theme_board_universe()` | **新增**：构建合并去重后的 universe |
| `refresh_theme_board_snapshot(fetch_series=None)` | **新增**：后台刷新主体，拉日 K 算 change+streak，写缓存，返回快照；无持仓叠加 |
| `get_theme_board_snapshot(...)` | 改为优先读缓存（任意时段命中即用）；缓存空时调用一次 `refresh_theme_board_snapshot()` 兜底；保留持仓叠加与排序 |
| `build_theme_board_payload(...)` | 输出增加 `board_kind`、`refreshed_at`；移除 `linked_fund_count` |
| `compute_consecutive_up_days` | 复用 |
| `_enrich_theme_board_daily_change` / `_enrich_theme_board_streak` | 合并为单段：一次日 K 同时算 change + streak |
| `build_linked_fund_counts` / `_load_theme_spot_changes` 模糊匹配 | 行业兜底改按 BK 代码精确取；移除关联基金计数 |

并行预算：`ThreadPoolExecutor(max_workers=8)`，后台总预算放宽到 **90s**（~100 板块日 K）；后台线程允许慢，不阻塞请求。

### `lifespan.py` 改造

```python
if theme_board_refresh_enabled():
    threading.Thread(target=theme_board_refresh_loop, name="theme-board-refresh", daemon=True).start()
```

`theme_board_refresh_loop()`（放 `theme_board_snapshot.py` 或新 `theme_board_refresh.py`）：

```
启动即 refresh_theme_board_snapshot() 预热
while True:
    interval = 900 if session in {intraday, pre_close, pre_open} else 3600
    sleep(interval)
    try: refresh_theme_board_snapshot()
    except: log，继续
```

### 环境变量（新增）

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_THEME_BOARD_REFRESH_ENABLED` | true | 是否启动后台刷新线程（**CI/pytest 设 false**） |
| `FUND_AI_THEME_BOARD_REFRESH_INTERVAL_SECONDS` | 900 | 盘中/盘前刷新间隔（15min） |
| `FUND_AI_THEME_BOARD_REFRESH_IDLE_INTERVAL_SECONDS` | 3600 | 收盘/非交易日刷新间隔（1h） |

---

## API 设计

### `GET /api/market/theme-boards`（路由签名不变）

**响应**（新增 `board_kind`、`refreshed_at`；移除 `linked_fund_count`）：

```json
{
  "trade_date": "2026-06-18",
  "session_kind": "trading_day_intraday",
  "available": true,
  "from_cache": true,
  "stale": false,
  "refreshed_at": "2026-06-18T06:15:02+00:00",
  "message": null,
  "sort": "change",
  "items": [
    {
      "sector_label": "半导体",
      "board_kind": "concept",
      "change_1d_percent": 2.78,
      "consecutive_up_days": 5,
      "held_fund_count": 1,
      "in_portfolio": true,
      "rank": 1
    }
  ]
}
```

- `sort`：`change`（默认）| `streak`，沿用。非法值 → 400（现有测试保留）。
- 降级：行业 spot + 全部日 K 失败且无缓存 → 仅 21 canonical 兜底；仍 200。
- 鉴权：可选 JWT。

---

## 前端设计

### `ThemeSectorOverview.tsx`

- 板块名旁加 `board_kind` 小标签：`行业`（slate）/ `概念`（amber）/ `指数`（violet），轻量 pill。
- 列不变：排名 / 板块名称(+标签+持仓) / 连涨连跌天数 / 涨跌幅。
- ~100 行直接渲染（表格已可横向滚动；100 行性能可接受，暂不虚拟化）。
- 「更新于」改为展示后端 `refreshed_at`（后台快照时间），而非前端渲染时间，让用户理解是 15min 缓存。

### `api.ts`

```ts
export type MarketThemeBoardKind = "industry" | "concept" | "index";
export interface MarketThemeBoardItem {
  sector_label: string;
  board_kind: MarketThemeBoardKind;
  change_1d_percent: number | null;
  consecutive_up_days: number | null;
  held_fund_count: number;
  in_portfolio: boolean;
  rank: number;
}
// response 增 refreshed_at?: string | null; 移除 linked_fund_count
```

### `MarketTab.tsx` / `marketThemeBoard.ts`

- theme `useCachedFetch` 的 `staleTimeMs`：`60_000` → `900_000`（15min）。
- `marketThemeBoard.ts` 增 `formatBoardKindLabel(kind)`、`boardKindClass(kind)`；`formatThemeBoardUpdatedAt` 接受后端 `refreshed_at`。

---

## 性能预算

| 场景 | 目标 |
|------|------|
| 接口缓存命中（常态） | < 50ms |
| 后台一轮刷新（~100 板块日 K，8 并发） | ≤ 90s（后台，不阻塞请求） |
| 冷启动无缓存的首次请求 | 触发一次同步兜底（最坏数十秒）；后台启动即预热，正常用户不遇到 |
| 前端 session 缓存命中 | 切子 Tab < 100ms |

---

## 测试

| 文件 | 用例 |
|------|------|
| `test_theme_board_snapshot.py` | universe 合并去重（canonical 覆盖同码行业）；`board_kind` 标注；change+streak 同源；日 K 失败 spot 兜底；持仓 secid 匹配；`refresh_theme_board_snapshot` 写缓存 |
| `test_api.py` | `GET /api/market/theme-boards` smoke：200、含 `board_kind`、行数 > 21；非法 sort → 400 |
| conftest | 已 stub 东财 spot/K 线、board records；后台线程 env 默认关闭，测试不启动 |

验证命令：
```
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q
cd apps/web && npm run lint && npm run typecheck && npm run build
```

---

## 验收标准

1. 市场 Tab「主题板块」展示约 100 行（行业 + 概念），带 `行业/概念` 标签。
2. 涨跌幅与连涨天数同源于 push2delay 日 K；行业兜底按 BK 代码精确取。
3. 持仓主题（半导体/商业航天等）能精确高亮。
4. 后台 15min（盘中）/1h（收盘）定时刷新；接口与前端读缓存，打开秒出；`from_cache: true` 常态命中。
5. `pytest` 全绿；`web` lint/typecheck/build 通过。
6. `docs/PROJECT_CONTEXT.md` 同步能力清单、API 表（`board_kind`/`refreshed_at`）、环境变量、目录与修改指引第 18 条。

---

## 文件清单

| 层 | 文件 |
|----|------|
| API 服务 | `apps/api/app/services/theme_board_snapshot.py`（改造 + universe + refresh loop） |
| 启动 | `apps/api/app/lifespan.py`（后台线程） |
| 配置 | `apps/api/app/config.py` 或 env helper（3 个新环境变量） |
| 路由 | `apps/api/app/main.py`（签名不变，可能透传 refreshed_at） |
| 测试 | `apps/api/tests/test_theme_board_snapshot.py`、`test_api.py` |
| 前端 | `ThemeSectorOverview.tsx`、`MarketTab.tsx`、`api.ts`、`marketThemeBoard.ts` |
| 文档 | `docs/PROJECT_CONTEXT.md`、`.env.example` |

---

## 后续 Phase（不在本期）

| Phase | 内容 |
|-------|------|
| 2 | 行业/概念子榜切换；连涨≥3 天标签；迷你 sparkline |
| 3 | 点击主题行 → 荐基 Tab 预选 focus_sector |
