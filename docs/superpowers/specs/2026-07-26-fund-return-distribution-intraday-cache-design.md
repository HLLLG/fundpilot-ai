# 基金涨跌分布：盘中实时口径 + 15 分钟缓存与空跑保护

- 日期：2026-07-26
- 状态：已确认设计，待写实现计划
- 影响范围：`apps/api`（`fund-return-distribution` 端点与服务）、`apps/web`（`FundReturnDistributionPanel` 与缓存层）

## 1. 背景与目标

市场页 `MarketBreadthGauge` 内挂载的 `FundReturnDistributionPanel`（标题"基金涨跌分布"）当前只消费 `fund_open_fund_daily_em` 的**官方已结算净值**（日终数据，`source_mode: "official_nav"`）。由此产生两个问题：

1. 交易日盘中看到的分布是**上一交易日**的官方净值，不是当日盘中的实时涨跌分布——用户需要的是交易日交易时段的实时分布。
2. 缓存只在内存（`useCachedFetch` 默认 `storage="memory"`），30 分钟 TTL、无 localStorage 冷启动、无自动刷新定时器。每次打开市场页都要重新拉取并闪 loading，也没有按交易日/收盘区分调度，存在空跑。

目标：交易日连续交易时段用东方财富实时估值（`fund_value_estimation_em`）按估算增长率分桶，给出**当日盘中实时**涨跌分布；其余时段仍用官方净值口径；并加 15 分钟刷新 + 交易日/收盘/非交易日闸门，杜绝空跑。

## 2. 数据语义与时段规则

时段判定一律走 `build_trading_session()`（`apps/api/app/services/trading_session.py`），不重复实现。

| `session_kind` / `market_phase` | 数据源 | `source_mode` | 是否实时刷新 |
|---|---|---|---|
| `trading_day_intraday`（`continuous`，非午休）/ `trading_day_pre_close` | `fund_value_estimation_em("全部")` 估算增长率分桶 | `intraday_estimate` | 是，15 分钟 |
| `trading_day_pre_open` / `lunch_break` / `trading_day_after_close` / `non_trading_day` | 现有 `fund_open_fund_daily_em` 官方净值 | `official_nav` | 否（空跑保护） |

- 连续交易时段 = `is_continuous_trading === true`（已含"交易日 + 09:30–15:00 + 非午休"判定）。
- 收盘后口径选官方净值而非陈旧盘中估值：当日结算净值比上一交易日盘中估值更可信，且收盘后估值不再更新。
- 午休（`lunch_break`）不发估算请求：午间估值不更新，按空跑处理，沿用缓存。

## 3. 后端

### 3.1 `apps/api/app/services/fund_return_distribution.py`

- `build_fund_return_distribution(force_refresh=False)` 在开头调 `build_trading_session()`；`session["is_continuous_trading"]` 为真 → 走估算分支 `_fetch_intraday_estimate_distribution()`，否则走现有官方净值分支（保持不变）。
- 新增 `_fetch_intraday_estimate_distribution(timeout)`：在 akshare 子进程内 `ak.fund_value_estimation_em("全部")`，取 `{gxrq}-估算数据-估算增长率` 列按 9 档分桶，输出与官方净值分支同构：
  - `bins`（9 档，沿用现有 key）、`advance_count`、`decline_count`、`flat_count`、`valid_count`、`missing_count`、`source_row_count`、`coverage_percent`、`as_of_date`（=`gzrq` 公布净值日）、`as_of_datetime`（**估算时间**：优先取 East Money 原始响应里的 `gztime` 字段；该字段不可得时退化为 `gxrq` 估算日。`fund_value_estimation_em` 的列映射未暴露 `gztime`，故在自定义子进程脚本里直接从 `json_data["Data"]` 取，不依赖 akshare 的列名）。
- 聚合在子进程内完成（沿用 `_fetch_official_distribution` 的模式：子进程内分桶，只回传小 JSON，主进程不接 2 万行大表）。
- 数值守卫沿用 `_as_non_negative_int` / `_as_float`；并保留 `sum(bins) == valid_count`、`advance+decline+flat == valid_count` 的合计校验，任一失败视为本次拉取失败。
- `payload` 外层字段：`available=true, stale=false, source_mode="intraday_estimate", source_name="东方财富实时估值", universe_scope="开放式基金份额代码（A/C/E 等分别计数，盘中估算口径）", fetched_at=now(CN_TZ).isoformat()` + `result`。

### 3.2 服务端缓存

- 复用 `app.services.sector_quote_cache` 的 `get_spot_snapshot` / `save_spot_snapshot` / `get_spot_snapshot_any_age`（现有官方净值分支已用）。
- 估算分支独立 key：`fund:return-distribution:intraday:v1`，TTL **10 分钟**（< 前端 15 分钟，保证每个前端 tick 命中过期、拿到新数据；多用户共享同一份服务端缓存）。
- 官方净值分支沿用现有 key `fund:return-distribution:v1` 与 30 分钟 TTL，不变。
- 三级回退（与官方净值分支一致）：服务端缓存命中 → 返回；未命中且拉取成功 → 写缓存返回；拉取失败 → 返回上一份快照 `stale=true` + 文案"实时估值源本次更新失败，正在展示上次成功统计" → 再失败返回 `available=false, stale=true, message="暂未取得可核验的盘中实时估值分布。"`。

### 3.3 端点

- `apps/api/app/routes/market_diagnostics.py` 的 `GET /fund-return-distribution` 签名与响应结构不变；响应只是新增 `source_mode` 取值 `intraday_estimate` 与可选 `as_of_datetime`。
- 响应头 `Cache-Control: private, max-age=0, must-revalidate`（实时数据禁止 CDN/中间代理缓存）；如已存在 ETag 机制保持不变。

## 4. 前端

### 4.1 类型 `apps/web/src/lib/api/marketDiagnostics.ts`

- `FundReturnDistribution.source_mode` 扩为 `"official_nav" | "intraday_estimate"`。
- 新增 `as_of_datetime?: string | null`（盘中估算时间）。

### 4.2 持久化缓存 `apps/web/src/lib/storage.ts`

- 新增 `loadFundReturnDistributionCache()` / `saveFundReturnDistributionCache(data)`（localStorage，参考同文件 `loadDiscoverySectorHeatCache` 写法：含 `fetchedAt` 信封与读取容错）。
- `FundReturnDistributionPanel` 的 `useCachedFetch`：
  - `storage="session"`（sessionStorage 持久跨 unmount/remount，解决"每次打开市场页都重新拉"）。
  - `bootstrap: () => loadFundReturnDistributionCache()`（sessionStorage 为空时从 localStorage 冷启动，秒开）。
  - `staleTimeMs = 15 * 60_000`（15 分钟）。
  - 不设 `keepPreviousUnless`（沿用默认 `undefined`，即总是接受新数据）。新数据请求失败时由 `useCachedFetch` 的 catch 路径保留旧数据展示（`setData` 不被清空），即 stale-while-revalidate。

### 4.3 调度与闸门 `FundReturnDistributionPanel`

- 新增 `setInterval(15min)` + `visibilitychange` effect，**完全照抄** `MarketBreadthGauge` 的 effect 写法（页面隐藏 `clearInterval`、可见时刷一次再启）。
- **闸门只作用于 15 分钟定时 tick 与 visibility 触发的刷新**；初始挂载沿用 `useCachedFetch` 既有逻辑（缓存新鲜则不发，过期或无缓存则发一次），保证首屏有数据。
- 每 tick：
  1. 调 `fetchTradingSession()`（已存在，`apps/web/src/lib/api.ts`，服务端 `Cache-Control: max-age=30`，开销极小）。
  2. `session.is_continuous_trading === true` → `refresh()` 真发分布请求（拿盘中实时）。
  3. 否则跳过本次网络请求（空跑保护，保留缓存展示）。trading-session 拉取失败 → 保守发一次分布请求（宁可多一次请求也不空跑掉实时性）。
- 副标题口径区分：`source_mode === "intraday_estimate"` 显示"实时估值 · 截至 {as_of_datetime}"，否则沿用"官方净值 · 截至 {as_of_date}"；`stale` 标注与"更新中"提示不变。
- `revalidating` 期间保留旧数据展示（stale-while-revalidate），不闪空白。

## 5. 错误处理

- 后端：估算拉取失败 → 回退上一份盘中快照（`stale=true`）→ 再失败 `available=false`，与官方净值分支三级回退对齐。
- 后端：`fund_value_estimation_em` 返回空 / 缺 `估算增长率` 列 / 合计校验失败 → 视为本次失败，走回退。
- 前端：分布请求失败 → `useCachedFetch` 的 `error` 态展示"本次更新失败；如有历史结果仍会保留展示"（沿用现有文案），旧数据不丢。
- 前端：trading-session 请求失败 → 当作"可能需要刷新"，正常发一次分布请求，不因 session 拉取失败而停摆。

## 6. 测试

### 后端 `apps/api/tests`
- mock akshare（沿用现有测试对子进程/`run_akshare_json_script` 的 mock 方式）返回固定 `估算增长率` 列表，断言：
  - 9 档分桶计数与 `advance/decline/flat` 正确；
  - `session_kind` 为 `trading_day_intraday` 时 `source_mode=="intraday_estimate"`；为 `non_trading_day`/`trading_day_after_close` 时 `source_mode=="official_nav"`；
  - 服务端缓存 TTL 命中（10 分钟内）不重复调子进程；
  - 估算拉取失败时回退上一份快照 `stale=true`，再失败 `available=false`。

### 前端 `apps/web/src/components/FundReturnDistributionPanel.test.tsx`
- session 为 `is_continuous_trading` 时，15 分钟 tick 触发 `fetchTradingSession` + `fetchFundReturnDistribution`；
- session 为非交易日/收盘后时，tick 不发分布请求（空跑保护）；
- 首次渲染从 localStorage bootstrap 秒开（无 loading 闪烁）；
- `visibilitychange` 隐藏停定时器、可见刷一次再启；
- `source_mode="intraday_estimate"` 时副标题显示"实时估值 · 截至 …"。

## 7. 范围与不做的事

- 只动 `fund-return-distribution` 端点与 `FundReturnDistributionPanel`；不改 `MarketBreadthGauge`、不碰股票涨跌家数、不动 `MarketBreadthSignal`。
- 估算口径只用已安装的 `ak.fund_value_estimation_em`，不引入雪球/其他估值源。
- 不做历史盘中估值回放，只做当下盘中/盘后实时分布。
- 不改官方净值分支的 30 分钟服务端缓存与 `as_of_date` 语义。

## 8. 风险与缓解

- **盘中估值口径与官方净值口径不可混比**：UI 副标题明确标注"实时估值 / 官方净值"，`source_mode` 在响应中显式区分，避免用户把盘中估算当作结算净值。
- **`fund_value_estimation_em` 接口偶发超时/限频**：子进程超时 30s（沿用 `_FETCH_TIMEOUT_SECONDS`），失败三级回退，不阻断市场页其余部分。
- **TTL 边界**：服务端估算 TTL 10 分钟 < 前端 15 分钟，确保前端每个 tick 命中过期、拿到新数据；若未来调短前端 cadence，需同步收紧服务端 TTL。
