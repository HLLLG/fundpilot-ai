# 数据缓存优化设计

**日期：** 2026-06-15  
**状态：** 已批准（选项 3：Phase 1+2+3 全量）

## 目标

改善慢接口/页面的首屏与二次访问体验，通过本地/浏览器缓存与服务端优化减少重复外部请求（东财、AkShare）。

## Phase 1 — 快速修复

1. 板块后台轮询：`useSectorQuoteRefresh` 180s 自动刷新改用 `budget: "fast"`；用户手动刷新用 `accurate`
2. 基金详情分时：去掉无条件 `forceRefresh`；缓存 ≥2 点时跳过后台强刷；点击刷新图标可 force
3. 板块信号回测：服务端 24h 内存响应缓存（**2026-06-16 起仅 `has_data=true` 时写入**，避免空结果被缓存一天）

## Phase 2 — 前端 SWR

新增 `clientCache.ts` + `useCachedFetch.ts`（对齐 `loadDiscoverySectorHeatCache` 模式，不引入 TanStack Query）：

| 数据 | 存储 | staleTime |
|------|------|-----------|
| 盈亏分析 dashboard | session | 60s today / 5min 其他 |
| 基金 NAV 历史 | memory | 24h |
| 沪深300 指数 | memory | 1h |
| 持仓详情 | memory | 5min |

## Phase 3 — 服务端补强

1. `index_daily_client`：1h TTL 响应缓存（进程内）
2. `portfolio_profit_analysis`：持仓 fingerprint 跳过曲线重建；并行拉取分时；指数分时 60s 内存缓存
3. `news_cache`：盘中 15min `updated_at` 过期

## 验收

- pytest 全绿
- web lint / typecheck / build 通过
