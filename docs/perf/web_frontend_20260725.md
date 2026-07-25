# FundPilot 前端性能优化前后对比（2026-07-25）

范围：`apps/web` 前端 + `deploy/nginx` 静态层。没有改任何业务语义、后端契约、存储键或 fail-closed 边界。

## 1. 结论先说

- **首屏 CSS 从 148.9 KiB 降到 125.0 KiB（−23.9 KiB，−16.1%），6 条路由全部受益。** 手段是把只被懒加载的 `Dashboard` 及其面板使用的 939 行规则从 `globals.css` 移到独立的 `dashboard.css`，随 Dashboard chunk 按需到达。
- **首屏 JS 增加 1.3 KiB（480.9 → 482.2 KiB）。** 来自 memo 包装、`useDeferredValue`、模块级 formatter 常量，以及多出一个 CSS chunk 让 webpack runtime 变大 1.2 KiB。
- **`/` 首屏关键资源 629.8 → 607.2 KiB（−22.6 KiB），gzip 170.8 → 167.3 KiB。**
- **`≤ 400 KiB` 这个目标做不到，原因已用 bundle analyzer 定位清楚：首屏 JS 里 412.6 KiB 是框架地板**（`react-dom-client` 195.2 KiB + Next 16 App Router 客户端运行时 217.4 KiB），应用代码只有约 56 KiB。详见第 4 节。
- **交互延迟（INP 同口径的最大 `event` duration）从 20～24 ms 降到 16 ms**，3 次独立采样一致。代价是"新面板 DOM 出现"的墙钟时间增加约 27 ms —— 这是 `useDeferredValue` 的显式取舍，见第 6 节。
- **`tsconfig.target` 提到 ES2020 只省 35 字节（−0.007%），因此不改。** 110 KiB 的 polyfills chunk 带 `noModule`，现代浏览器根本不下载它，也从来不在 629.8 KiB 这个数里。
- **Turbopack 本轮不切换**，理由见第 5 节。
- **brotli 本轮不做**（需要换 nginx 镜像，属生产基建变更，已由你确认走 a 方案）。改为构建期 `gzip -9` 预压缩 + `gzip_static on`。

## 2. 测量口径

体积：`apps/web/scripts/perf/bundle-budget.mjs`（零第三方依赖）。只统计导出 HTML 真正会让浏览器在首屏下载的资源：`<script src>`（**排除带 `noModule` 的 legacy polyfill**）与 `<link rel="stylesheet">`；RSC flight payload 里被转义的路径字符串不计入。

交互与 Web Vitals：`apps/web/scripts/perf/ui-interaction-benchmark.mjs`（复用仓库已有的 Playwright 与 `serve-static.mjs`，零新依赖）。1440×900 headless，API 全部 stub 成固定响应（12 只持仓），每个 tab 切换重复 4～5 轮取中位数。**单机样本，只用于同一台机器上的前后对比，不能外推生产。**

before 基线的取法：`git stash` 掉本轮全部改动 → 在**同一份 `node_modules`** 上构建 → 测量 → `git stash pop`。这一步很重要：仓库文档里记录的旧基线 614.1 KiB 是更早依赖状态下的产物，在当前 `next@16.2.10` 上重建 HEAD 得到的是 **629.8 KiB**。本文所有 before/after 都用后者，前后可比。

## 3. 首屏体积前后对比

单位 KiB，格式 `before → after`。before = HEAD 在当前依赖上重建的产物。

| 路由 | JS 原始 | CSS 原始 | 合计原始 | 合计 gzip -9 | 请求数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/` | 480.9 → 482.2 (+1.3) | 148.9 → 125.0 (−23.9) | 629.8 → 607.2 (−22.6) | 170.8 → 167.3 (−3.5) | 10 → 10 |
| `/login` | 472.8 → 474.1 (+1.3) | 148.9 → 125.0 (−23.9) | 621.8 → 599.1 (−22.6) | 168.2 → 164.7 (−3.5) | 10 → 10 |
| `/register` | 473.7 → 474.9 (+1.3) | 148.9 → 125.0 (−23.9) | 622.6 → 600.0 (−22.6) | 168.3 → 164.8 (−3.5) | 10 → 10 |
| `/reset-password` | 469.6 → 470.9 (+1.3) | 148.9 → 125.0 (−23.9) | 618.6 → 595.9 (−22.6) | 167.1 → 163.6 (−3.5) | 10 → 10 |
| `/settings` | 472.8 → 474.1 (+1.3) | 148.9 → 125.0 (−23.9) | 621.7 → 599.1 (−22.6) | 167.9 → 164.4 (−3.5) | 10 → 10 |
| `/admin/users` | 491.1 → 492.4 (+1.3) | 148.9 → 125.0 (−23.9) | 640.0 → 617.4 (−22.6) | 172.5 → 169.0 (−3.5) | 10 → 10 |

全量产物：JS 51 个 / 1761.7 → 1764.7 KiB（+3.0）；CSS 2 个 / 148.9 → 3 个 / 149.1 KiB（+0.2，多出的第三个是懒加载的 `dashboard.css` 24.1 KiB，它不在任何路由的首屏里）。

`/` 首屏明细（after）：

| 资源 | 原始 | gzip -9 | 说明 |
| --- | ---: | ---: | --- |
| `chunks/3794-*.js` | 217.4 | 59.4 | **100% Next App Router 客户端运行时**，无一行应用代码 |
| `chunks/4bd1b696-*.js` | 195.2 | 61.3 | `react-dom-client.production.js` 单模块 |
| `css/bff479403d6f41d4.css` | 121.6 | 21.5 | globals（Tailwind utilities 约 92.2 + 手写约 29.3） |
| `chunks/2209-*.js` | 22.5 | 5.9 | `lib/api` 摇树后剩余 + BrandMark + auth |
| `chunks/app/page-*.js` | 18.7 | 7.0 | LandingPage + HomeClient + AuthProvider + lucide 图标 |
| `chunks/app/layout-*.js` | 13.1 | 5.1 | AuthProvider + web-vitals |
| `chunks/9743-*.js` | 9.6 | 3.9 | next/link + lucide 核心 |
| `chunks/webpack-*.js` | 5.0 | 2.5 | runtime |
| `css/1810758657f1e979.css` | 3.5 | 0.5 | next/font 自托管 Sora 的 @font-face |
| `chunks/main-app-*.js` | 0.5 | 0.2 | — |

## 4. 为什么 400 KiB 达不到（analyzer 实证）

接入 `@next/bundle-analyzer`（devDependency，仅 `ANALYZE=true` 时启用，不进运行时产物）后对 217.4 KiB 那个 chunk 做模块归属统计，147 个模块、841.5 KiB statSize 的构成：

| 归属 | statSize |
| --- | ---: |
| `next/dist/client/components/segment-cache` | 257.4 KiB |
| `next/dist/client`（其余：layout-router / app-router / links / navigation …） | 253.4 KiB |
| `next/dist/client/components/router-reducer`（含 ppr-navigations） | 156.7 KiB |
| `react-server-dom-webpack` client | 63.5 KiB |
| `next/dist/shared` | 43.8 KiB |
| `next/dist` 其余 + compiled react / scheduler / react-dom 片段 | 65.3 KiB |
| **应用代码 `src/`** | **0 KiB** |

也就是说首屏 JS 的地板是 195.2（react-dom）+ 217.4（App Router runtime）= **412.6 KiB**，加上必须存在的 CSS 与外壳代码，`≤ 400 KiB` 在不动框架的前提下不可能达成。

顺带纠正一个此前的假设：我原本怀疑 `AuthProvider` 从 106 KB 的 `@/lib/api` barrel 只取两个符号会把整个 barrel 拽进首屏。analyzer 证伪了 —— 该 chunk 实际只有 22.5 KiB，`lib/api.ts` 摇树后只剩 32 KiB statSize，不是瓶颈，因此没有对 barrel 做任何拆分。

**如果确实要把 `/` 压到 400 KiB 以下，只剩这几条路，都需要你决策：**

1. 试 `experimental.clientSegmentCache: false`（未文档化的实验开关，理论上可去掉 257.4 KiB statSize 的 segment-cache）。风险：非公开契约、Next 升级即可能失效、静态导出下的路由行为需要完整回归。
2. 把 App Router 换回 Pages Router 或改用非 Next 的静态方案。远超本轮范围。
3. 接受"框架地板 412.6 KiB"，把预算目标改成**应用代码体积**（当前约 56 KiB）与**传输体积**（当前 gzip 167.3 KiB）。这是我的建议。

## 5. 评估结论（做了但没采纳的三项）

### 5.1 Tailwind v4 `@theme` 迁移 —— 不做

用一次隔离构建量出 CSS 的真实构成：把 `globals.css` 临时换成只有 `@import "tailwindcss";` 再构建，主 CSS 为 **94,445 B（92.2 KiB）**。对照当前主 CSS 124,478 B（121.6 KiB），得到：

| 组成 | 体积 |
| --- | ---: |
| Tailwind preflight + 实际用到的 utilities + Tailwind 默认 `@theme` 变量 | 92.2 KiB（首屏 CSS 的 **76%**） |
| 手写规则（留在 globals 的部分） | 29.3 KiB |
| 手写规则（已移出到 dashboard.css，不在首屏） | 24.1 KiB |

`@theme` 的作用是把 token 注册进 Tailwind 并**额外生成**对应 utilities，它不会把已有的 92.2 KiB 去重掉；相反可能变大。要压这 92.2 KiB 只能改写 159 个组件里的 utility 用法，属于重构而非性能优化。**因此不迁移**，`:root` 的 535 个变量保持原样。

### 5.2 `tsconfig.target` ES2017 → ES2020 —— 不改

实测（同一份源码，只改 target，各构建一次）：

| target | `/` 首屏 JS | `/` 首屏 CSS |
| --- | ---: | ---: |
| ES2017 | 493,772 B | 128,000 B |
| ES2020 | 493,737 B | 128,033 B |

差 **35 字节（−0.007%）**。Next 的客户端编译目标由它自己的 browserslist 决定，`tsconfig.target` 基本只影响 tsc 类型检查行为。既然收益为零，就没有理由用浏览器兼容面去换，保留 ES2017。

同时纠正原始需求里的一处前提：`polyfills-*.js`（110.0 KiB）在 `out/index.html` 里是 `<script src="…" noModule="">`，支持 ES module 的浏览器直接跳过；它也从来不在 629.8 / 607.2 KiB 这两个数里（本文的统计口径显式排除 `noModule`）。所以"提升 target 消掉 110 KB polyfills"这个收益不存在。

### 5.3 Turbopack —— 保留 webpack

不切换的理由：

1. `package.json` 的 `--webpack` 是显式选择，`next.config.ts` 里还有 dev/prod `distDir` 隔离的注释，说明当前构建路径是被有意固定的；
2. Turbopack 的 chunk 切分策略不同，会让本轮全部体积基线、`budget.config.json` 阈值和 nginx 预压缩清单同时作废，需要整套重做；
3. Next 16 的 `output: "export"` 与 Turbopack 组合仍有公开的静态导出相关 issue，而这是一个金融决策应用的唯一发布路径。

## 6. 交互与 Web Vitals 前后对比

同机、同脚本，各采 3 次。`before` 同样是 stash 掉改动后重建的 HEAD。

| 指标 | HEAD（3 次） | after（3 次） | 读法 |
| --- | --- | --- | --- |
| 最大交互 duration（INP 同口径）· 持仓 tab | 24 / 20 / 20 ms | **16 / 16 / 16 ms** | 一致下降到单帧下限 |
| 最大交互 duration · 日报 tab | 20 / 20 / 24 ms | **16 / 16 / 16 ms** | 同上 |
| 面板 DOM 出现墙钟 · 持仓 tab | 16.5 / 20 / 30 ms | 40.5 / 47 / 49 ms | **变慢约 27 ms，见下** |
| 面板 DOM 出现墙钟 · 日报 tab | 20.5 / 16 / 24 ms | 56 / 47 / 49 ms | 同上 |
| LCP | 516 / 452 / 452 ms | 484 / 448 / 456 ms | 无可测差异 |
| FCP | 96 / 32 / 36 ms | 96 / 32 / 36 ms | 无差异 |
| CLS | 0.033 / 0 / 0.033 | 0.033 / 0 / 0 | **两侧同样在 0 与 0.033 之间抖动，本轮没有引入新的布局跳动，也没有消除既有那一处** |
| 加载期长任务 | 0 个 | 0～1 个 / 0～52 ms | 单样本噪声，不足以判定 |

**必须交给你决策的取舍：** `useDeferredValue` 让 tab 切换的交互延迟稳定降到 16 ms（单帧），但"新面板 DOM 出现"的墙钟时间增加约 27 ms。这不是 bug 而是这个 API 的语义：切换期间**旧面板继续可见**（不是空白也不是 loading 占位），React 把这一帧优先让给输入响应。在这台机器上面板本身挂载只要 20 ms，所以延后的相对代价看起来偏大；在低端手机上重面板挂载远不止 20 ms，收益会放大而这 27 ms 占比会缩小。**如果你更看重"点了立刻换页面"的观感，我可以把 `deferredActiveTab` 改回 `activeTab`，一行还原。**

## 7. 具体改动清单

### P0 首屏与关键路径

| 改动 | 文件 | 效果 |
| --- | --- | --- |
| 拆出工作台专属样式（939 行）到独立 CSS，由懒加载的 `Dashboard` 导入 | 新增 `src/app/dashboard.css`；`src/app/globals.css` 2246 → 1307 行；`Dashboard.tsx` 加一行 import | 6 条路由首屏 CSS 各 −23.9 KiB |
| 原 `@media (max-width:767px)` 里混着的工作台与设置页规则按家族拆开（`.settings-danger-row,.workflow-completion` 这条共用声明拆成两条，声明逐字不变） | `globals.css` / `dashboard.css` | 保证 dashboard.css 后加载时移动端覆盖不被基础规则顶掉 |
| `FundSearchDialog` 改条件挂载 | `Dashboard.tsx` | 它是 `dynamic()` 组件但此前无条件渲染，chunk 在工作台挂载时必然下载；组件本来就 `!open → return null`，焦点捕获/恢复都发生在 open 生效的 effect 里，行为一致 |
| 构建期 `gzip -9` 预压缩 + nginx `gzip_static on` | 新增 `scripts/perf/precompress.mjs`（挂到 npm `postbuild`）；`deploy/nginx/fundpilot.conf` 的 `/_next/static/` 与 `/` 两处 | 78 个 `.gz`，2118.1 → 638.2 KiB（69.9%）。压缩级别从运行时 5 提到构建期 9，且每请求不再现压。缺 `.gz` 时自动回落运行时 gzip |
| 本地预览服务器同步支持预压缩 | `scripts/serve-static.mjs` | 避免"本地现压、线上预压缩"两套口径 |

**路由级 CSS 拆分（auth / settings / admin）试过但已回退。** 实测这三片确实各自产出了独立 CSS 文件（3.4 / 2.9 / 1.7 KiB），但 Next 只在部分路由的静态 HTML 里插入了对应 `<link>`：`register.html`、`reset-password.html` 有，`login.html`、`settings.html`、`admin/users.html` 没有——样式改由客户端 chunk 加载时注入。对登录页这种首屏就是表单的页面，这意味着可能出现一帧无样式闪烁。**8 KiB 的收益不值这个风险，因此回退，globals.css 用 `git checkout` 复原后只重做 dashboard 一片**，确保除 dashboard 区段外与 HEAD 逐字一致。

### P1 渲染性能与交互响应

| 改动 | 位置 |
| --- | --- |
| `todayKey` / `todayReport` / `currentReportIndex` / `previousReport` / `nextReport` 收进 `useMemo`（`todayKey` 仍逐次求值并留在依赖里，跨日判定语义不变） | `Dashboard.tsx` |
| `reportDateKey` 的 `Intl.DateTimeFormat` 提到模块作用域（它此前在 `orderedReports.find()` 里按报告条数逐条 new） | `Dashboard.tsx` |
| `popstate` effect 拆成两个：恢复逻辑仍按原依赖逐次补跑，监听器只在挂载/卸载时绑一次 | `Dashboard.tsx` |
| 8 个手写 SVG 图表加 `memo`（保持命名导出，内部实现改名 `*View`，末尾 `export const X = memo(XView)`） | `ProfitAnalysisTrendChart` / `PerformanceReturnChart` / `IntradayPercentChart` / `HoldingDonutChart` / `PortfolioCorrelationHeatmap` / `BoardFlowHistoryChart` / `MarketBreadthGauge` / `ProfitLossCalendar` |
| 父级 props 稳定化：`rows={currentData?.allocation ?? []}` → 模块级空数组常量；`points={intraday?.points ?? []}` 同理；`ProfitLossCalendar` 的两个内联箭头 props → `useCallback` | `PortfolioDashboard.tsx`、`FundResearchDetail.tsx` |
| tab 面板区改读 `useDeferredValue(activeTab)`；导航高亮、页头文案、后台任务浮层继续读 `activeTab` | `Dashboard.tsx` |
| 渲染路径上的 `Intl.*` / `toLocale*` 全部提到模块作用域（14 个文件、22 处调用点）。`Intl.NumberFormat(locale, opts).format(n)` 与 `n.toLocaleString(locale, opts)` 按规范输出一致；无选项的 `toLocaleString()` 用显式 `year/month/day/hour/minute/second` 组合等价替代，展示结果逐字不变 | `admin/users/page.tsx`、`Dashboard.tsx`、`FundResearchDetail.tsx`、`FundReturnDistributionPanel.tsx`、`FundTradeabilityEvidence.tsx`、`holdingMetrics.ts`、`YangjibaoHoldingsBoard.tsx`、`PortfolioDashboard.tsx`、`DiscoveryCandidatePoolPanel.tsx`、`RebalanceSimulationPanel.tsx`、`HistoryRail.tsx`、`DiscoveryHistoryRail.tsx`、`UsMarketOverview.tsx`、`EvidenceMaturityPanel.tsx`、`FundRecommendationCard.tsx`、`RiskControls.tsx`、`PerformanceReturnChart.tsx`、`DiscoveryReportPanel.tsx` |

**`Dashboard.tsx` 的 49 个 `useState` 没有合并。** 前 6 项改完后交互延迟已经压到 16 ms 单帧下限，本机测不出进一步收益；而状态合并会改变 batching 时序，是这批改动里唯一可能悄悄改变行为的一类。按"只加在能测到收益的地方"的约束，这一项**未做**，留在结论清单里。可下沉的候选簇是明确的：OCR 待确认流程 6 个、批量交易流程 5 个、手动新增持仓 3 个。

### P2 长列表与滚动

| 组件 | 处理 |
| --- | --- |
| `NavHistoryTable` | 加 `memo`；把每次渲染都跑的 `[...points].sort().slice()`（最多 260 个净值点）收进 `useMemo`；行高固定，加 `content-visibility: auto` + `contain-intrinsic-size: auto 38px` |
| `DiscoveryCandidatePoolPanel` | 面板默认收起，但两个 Map、四次 filter 和每个候选的 `qualityPresentation()` 原来每次渲染都全量重算 —— 收进 `useMemo`；空池提前返回移到 hooks 之后（hooks 必须无条件调用），返回值不变 |
| `YangjibaoHoldingsBoard` | `sortedHoldings` 已有 `useMemo`；行数等于用户实际持仓数（十几到几十），**不引入分页**，避免为不存在的规模问题改体验 |
| `app/admin/users/page.tsx` | 已经是服务端分页（`pageSize: 20`）+ 既有 `.admin-pagination` 移动端 sticky 控件，**无需改动** |

`ThemeSectorOverview` 的 `MOBILE_PAGE_SIZE = 10` 模式本轮**没有被复制到别处**：四个候选列表里两个本来就有界、一个已服务端分页、一个行数由用户持仓决定。虚拟滚动同样未引入（按计划只在"行高固定且行数可能过百"时才考虑，没有命中）。

### P3 布局与动画

- 审计 `globals.css` + `dashboard.css` 全部 `@keyframes`（`fade-up` / `float-soft` / `report-drawer-fade` / `report-drawer-in` / `drawer-enter` / `sheet-enter`）：**全部只动 `transform` 与 `opacity`**，无需改写。
- 全部 `transition:` 声明里只有一处动画 layout 属性：`dashboard.css` 的 `.factor-bar-fill { transition: width 0.3s ease; }`。**这一处保留，作为取舍记录**：改成 `transform: scaleX()` 需要去掉填充条自身的 `border-radius: 999px` 才能靠 track 的 `overflow:hidden` 裁切，部分填充时右端的圆头会变成直角 —— 那是视觉改动，与"不要重新设计视觉"冲突。它是一次性 300ms 动画、作用在被 `overflow:hidden` 裁切的小元素上，影响可控。
- `will-change` 仍为 0 处，**这是结论不是遗漏**：现有动画元素只动 transform/opacity，浏览器在动画期间本来就会提升合成层；常驻 `will-change` 只会白占显存。
- 10 处 `backdrop-filter` 未改。其中 `.app-masthead` 在 `@media (max-width:1023px)` 下已有 `backdrop-filter:none`（注释明确记录这是 2026-07-12 移动端底栏被 header 变成包含块那次事故的修复），本轮原样保留并随 dashboard.css 一起移动。320px 视口由 `mobile-320` 的溢出与触控区断言覆盖，本轮全部通过。

### P4 可观测与防回归

- `scripts/perf/bundle-budget.mjs`：首屏体积报告 + `--budget` 硬门禁 + `--baseline` 前后对比。零第三方依赖。
- `scripts/perf/budget.config.json`：按本轮实测值留约 3% 余量。注释里写明"JS 预算是拦应用代码回胖，不是要求总量继续下降"。
- `scripts/perf/ui-interaction-benchmark.mjs`：交互延迟 + 长任务 + LCP/FCP/CLS/TTFB 采集。零第三方依赖。
- `.github/workflows/frontend-perf.yml`：新建独立 workflow，**没有动 `deploy-lighthouse.yml`**。`bundle-budget` 是硬门禁；Web Vitals 先只采集并上传 artifact，等攒够方差数据再决定阈值（无头环境的 LCP/INP 抖动做硬门禁会变成随机红叉）。
- 新增 devDependency：`@next/bundle-analyzer@16.2.2`（`--save-exact`），仅 `ANALYZE=true` 时启用，不进运行时产物。`npm run build:analyze` 走 `scripts/perf/build-analyze.mjs`（跨平台设置环境变量，不引入 cross-env）。
- 接口侧沿用既有 `scripts/perf/local_api_benchmark.py`，本轮**没有新造接口基准**，也没有改任何后端代码。

## 8. 验证

| 项 | 结果 |
| --- | --- |
| `npm run typecheck` | 通过（exit 0） |
| `npm run lint`（`--max-warnings=0`） | 通过（exit 0） |
| `npm run test`（Vitest 全量） | **105 files / 471 passed**，与基线一致 |
| `npm run build`（production 静态导出） | 通过；`postbuild` 预压缩产出 78 个 `.gz` |
| `npm run perf:bundle:check` | 体积预算通过 |
| `npm run test:e2e:ui:smoke` | **27 passed / 6 failed / 3 skipped**，见下 |
| `git diff --check` | 通过（无输出） |

**smoke 的 6 个失败全部来自 `visual-regression.spec.ts`（`/`、`/login`、`/register` × desktop-1440、mobile-320），且与本轮改动无关。** 判定方法：`git stash` 掉全部改动、在同一台机器上重建 HEAD、跑同一条 spec —— HEAD 失败**同样的 9 项**（全量 7 视口口径），且失败像素数逐一完全相同：42464 / 12713 / 12617 / 143343 / 7262 / 3872 / 27423 / 2119 / 2071。像素数完全相同意味着本轮改动在这三条公共路由上的渲染结果与 HEAD **逐像素一致**，也就正面证明了 `globals.css` 拆分没有改变公共页面的视觉。

结论：这批快照基线相对本机的 Chromium/字体渲染已经陈旧（快照名带 `-win32`，平台一致，差异来自浏览器版本）。仓库记录的 `30 passed / 6 skipped` 在本机连 HEAD 都复现不了。**本轮没有更新这些基线图** —— 更新快照会掩盖"基线到底该以哪台机器为准"这个问题，应该由你决定是在 CI 里统一生成基线，还是在本机 `--update-snapshots` 后入库。

## 8.1 环境与工程卫生

- **`.gitattributes`（新增）**：仓库 `core.autocrlf=true` 且此前没有 `.gitattributes`，`git add` 会提示「LF will be replaced by CRLF」。`deploy/lighthouse/deploy.sh`、`scripts/dev.sh` 等 5 个 shell 脚本、nginx `.conf`、Dockerfile、CI YAML、`.mjs`、`.py` 都会被 Linux 侧解释器直接读取，多出的 `\r` 会产出 `$'\r': command not found` 这类难定位的错误，或让 shell 变量末尾带上不可见回车（本轮就被这个坑了两次）。现在对这些扩展名强制 `eol=lf`，Playwright 视觉基线 PNG 显式标 `binary` 防止被改写。`git add --renormalize .` 验证：**零改动**，纯安全网、无 diff 噪音。
- PowerShell 侧 `npm.ps1` 被执行策略拦的问题已不复现（`CurrentUser` 已是 `RemoteSigned`，`npm --version` 正常）。本轮采用的稳定通道是「用 `node` 直接驱动 `next` / `tsc` / `eslint` / `vitest` / `playwright` 的 bin，输出写日志文件再读」，不依赖 shell 的交互输出。

## 9. 回滚方式

每一类改动都可以独立回退：

- CSS 拆分：删 `src/app/dashboard.css`、去掉 `Dashboard.tsx` 里那行 import、`git checkout -- apps/web/src/app/globals.css`。
- `useDeferredValue`：`Dashboard.tsx` 里把 5 处 `deferredActiveTab` 换回 `activeTab`，删掉一行声明。
- 图表 `memo`：每个文件末尾的 `export const X = memo(XView)` 换回 `export function X(...)` 即可。
- formatter 上提：纯局部替换，不影响调用方。
- nginx 预压缩：删掉两处 `gzip_static on;` 即回落运行时 gzip；`.gz` 是附加文件，删除不影响服务。
- 度量脚本与 workflow：纯新增文件。

---

## 10. 第二轮：允许框架重构与视觉改动后的补充（同日）

前提变化：第一轮受「不改框架、不改视觉」约束。之后放开这两条，于是把第一轮标记为「需你决策」和「未做」的项逐条重新实测。**结论是三个框架级方案全部被数据否掉**，落地的是一项工具改进、一项动画改写和一项加载态重做。

### 10.1 Next App Router 运行时（217.4 KiB）—— 无配置出口

在 `next@16.2.10` 的 `config-shared.d.ts` 里逐项检索：**没有 `clientSegmentCache` 开关**。该特性在 16 已转为常开、不提供 opt-out。也就是说 257.4 KiB statSize 的 segment-cache 无法用配置裁掉。

现存的相关开关只有 `prefetchInlining` / `staleTimes` / `dynamicOnHover` / `optimisticRouting`，它们改的是预取行为，不改变运行时代码是否进包。

**结论：这 217.4 KiB 的唯一移除路径是离开 App Router（换 Pages Router 或换掉 Next）。** 那会重写 159 个组件的路由/布局假设并作废整套 e2e，属于独立立项，不适合夹在性能轮里做。

### 10.2 `experimental.inlineCss: true` —— 实测更差，不采纳

| 口径 | 当前 | inlineCss |
| --- | ---: | ---: |
| `/` HTML | 9.9 KiB | **262.2 KiB** |
| `/` 首屏 CSS 请求 | 121.6 + 3.5 KiB | 0（内联进 HTML） |
| `/` 首屏请求数 | 10 | 8 |
| `/` 首屏真实下载量（HTML + JS + CSS） | ~617 KiB | **~744 KiB** |

HTML 从 9.9 涨到 262.2 KiB，比 CSS 本身（125.0 KiB）还多一倍 —— CSS 被内联了约两份（`<style>` 一份、RSC flight payload 里再一份）。叠加第二个问题：CSS 原本走 `/_next/static/` 的 `immutable` 一年缓存，内联后跟着 `expires -1` 的 HTML 每次重新下载，回访用户净亏。

**这一轮顺手修了度量工具的一个真问题**：原来的 `bundle-budget.mjs` 只统计 JS + CSS 资源，不算 HTML。inlineCss 在旧口径下会显示成「CSS −125 KiB、总量 −125 KiB」的巨大改善，而浏览器实际多下载了 127 KiB。现在报告新增 `htmlBytes` 列与 `firstScreenBytes = HTML + JS + CSS`，预算也改按 `firstScreenBytes` 卡，这类假收益再也藏不住。

### 10.3 React Compiler（`reactCompiler: true`）—— 有成本无收益，不采纳

Next 16 已把它从 `experimental` 提到顶层（`experimental.reactCompiler` 会直接报 invalid key）。实测：

| 口径 | 当前 | reactCompiler |
| --- | ---: | ---: |
| `/` 首屏 JS | 482.2 KiB | **488.9 KiB（+6.7）** |
| 全量产物 JS | 1764.7 KiB / 51 个 | **1920.8 KiB / 53 个（+156.1）** |
| 最大交互 duration | 16 ms | 16 ms（无变化） |
| tab 切换墙钟 | 47～55 ms | 49～55 ms（无变化） |
| Vitest | 105 files / 471 passed | **105 files / 471 passed** |

构建通过、测试全绿、行为无变化 —— 但**交互指标一动不动**，因为热点已经手工 memo 过、指标本身停在 16 ms 单帧下限。为一个测不出收益的改动付 +156 KiB 产物不合理；它还会引入 `babel-plugin-react-compiler` 及其依赖树里 4 条 high 级公告。已卸载。

**保留作为选项**：如果目标是「以后新写的组件不用再手工 memo」这种长期工程保障，而不是本轮的指标，它是可用的，代价就是上表这些字节。

### 10.4 已落地：`.factor-bar-fill` 改 clip-path（去掉 layout 动画，圆头不变）

第一轮把它记成取舍保留，因为改 `transform: scaleX()` 会让部分填充时右端圆头变直角。第二轮找到了没有视觉代价的写法：

```css
/* 之前：transition: width 0.3s ease;  —— 每帧重排 */
width: 100%;
clip-path: inset(0 calc(100% - var(--factor-bar-fill, 0%)) 0 0 round 999px);
transition: clip-path 0.3s ease;
```

`inset(... round 999px)` 会把裁剪矩形本身圆角化，所以右端圆头与原来一致；同时彻底不再触发 layout（最差只重绘）。填充比例由组件通过 CSS 自定义属性传入（`PortfolioFactorScoresPanel`、`PortfolioEvidenceOverviewPanel`）。一张基金卡最多 5 条、多只基金同时进场时，原实现是每帧对所有条目重排。

### 10.5 已落地：工作台壳层骨架屏取代居中转圈

原来登录态恢复与 Dashboard chunk 加载两段等待，都显示屏幕正中一张几十像素高的小卡片（「正在恢复工作台…」/「正在加载工作台…」）。问题是首屏最大可见元素是这张小卡，真正的工作台挂载后整页结构突然出现。

改成 `WorkspaceSkeleton`：按真实壳层尺寸给出顶栏（4.25rem）+ 页头标题区 + 主内容卡片骨架，文案降为 `sr-only` 只给辅助技术。**关键约束**：骨架只用首屏 CSS 里已有的 Tailwind utilities，不用 `app-masthead` / `app-page-heading` / `dashboard-shell` —— 那些规则住在按需加载的 `dashboard.css` 里，骨架若依赖它们就会把工作台样式拉回首屏，让 CSS 拆分白做。

代价（实测）：`/` HTML +3.3 KiB、page chunk +1.9 KiB、CSS +0.7 KiB，首屏合计 617 → 622.8 KiB。本机 FCP 从 32～36 ms 变为 44～52 ms（多出的 DOM 解析），LCP 420～480 ms 与之前的 448～484 ms 无可测差异。**本机是 localhost 零延迟，测不出骨架屏真正的价值（真实网络下用户在等待期看到的是结构而不是一个点）；这一项的收益标记为"未在本机测得"，只有体积代价是确定的。**

### 10.6 CLS 0.033 定位清楚了：是测量环境造成的，不是产品缺陷

第一轮只知道「CLS 在 0 与 0.033 之间抖动，HEAD 也一样」。这一轮给基准脚本加了位移来源捕获（`layout-shift` entry 的 `sources` + 前后 rect），直接定位到：

```
位移 0.033 @456ms  section.app-page-heading      124,116 1192x153 -> 124,182 1192x153
位移 0.033 @456ms  main#main-content             124,301 1192x599 -> 124,367 1192x533
```

页头在挂载后约 450ms 被向下推 **66px**，把 `main` 一起带下去。66px 正是一条 `InlineNotice` 的高度 —— 它渲染在顶栏与页头之间。

再用一个健康 bootstrap 的探针复核：shell 只有 3 个子元素（`header` 68px / `app-page-heading` 153px / `main` 575px），**没有 notice、CLS = 0**。所以基准里的 0.033 来自我的 stub 不完整触发的错误提示条，是**测量污染**。基准脚本现在会显式披露 `noticePresent` 与提示文案，这个数字不会再被误读。

**但产品侧有一条真实的条件性 CLS**：任何在挂载后才出现的提示条（持仓字段告警、刷新失败、写入阻断等）都会把整页下推约 66px。两个候选修法，都涉及"重要告警放在哪"这个信息层级决策，因此**交给你定**：

1. 把提示条移到页头之后、`main` 内容之上 —— 页头（LCP 候选）不再位移，但 `main` 仍会下移，实测收益有限（估算 0.033 → 0.049，反而可能更差，因为 `main` 高度会被视口裁掉的部分变了）。
2. 把提示条改成吸附在 sticky 顶栏下方的固定条 —— 彻底零位移，而且告警滚动时不会消失（对金融告警是更好的行为）；代价是它会常驻遮住内容顶部约 66px。

我倾向 2，但它改变了重要告警的呈现方式，不适合我单方面定。

### 10.7 新工具顺手抓到的一个真实缺陷（非性能，待你决定是否单独立项）

给基准脚本加上 `noticePresent` 披露后，第一次运行就打出了提示条的实际文案：

```
[注意] 工作台出现了提示条，CLS 含它插入造成的位移：Cannot read properties of undefined (reading 'map')
```

也就是说：当某个接口返回的结构与预期不符时，工作台会把一条**原始 JS TypeError** 直接当作用户可见提示展示出来。对一个金融决策应用，用户看到 `Cannot read properties of undefined (reading 'map')` 是不可接受的 —— 它既不可行动，也泄漏实现细节。

根因是某处对响应字段直接 `.map()` 而没有做形状守卫，异常被 catch 后原文进了 `InlineNotice`。本轮**没有修**：定位具体端点需要逐一比对响应形状，属于健壮性/错误文案范畴而不是性能，且会碰到"错误信息该怎么说"的产品口径。建议单独立项，修法有两层：

1. 对 `.map()` 之类的数组访问补形状守卫（`Array.isArray(x) ? x : []`），让缺字段降级成空状态而不是抛错；
2. 用户可见的 notice 只允许来自白名单文案；未识别的异常统一显示成可行动的通用提示，原文只进遥测。

### 10.8 一处数字更正

上一轮报告里我把 smoke 结果写成「27 passed / 6 failed / 3 skipped」，那是从不完整的日志尾部推断的，**不准确**。本轮完整跑出的权威结果是：

```
24 passed / 6 failed / 6 skipped （合计 36）
```

6 个失败仍然全部是 `visual-regression` 陈旧基线（已用 HEAD 对照证明与本轮改动无关，失败像素数逐一相同）；6 个 skipped 是视口条件跳过（桌面跳过移动端专属断言、平板跳过 OCR 流程等）。`docs/PROJECT_CONTEXT.md` 的「本轮验证」已同步更正。

---

## 11. 第三轮：生产静态层实测、brotli 真正落地、以及收尾（同日）

这一轮的输入是你的三条指示：先自己去核实服务器连接、a～e 五项按建议来、剩下的一并做完不要做一半。

### 11.1 先核实"能不能直连服务器"——不能，改用只读探针

仓库里**没有任何服务器凭据**：`.env` / `.env.example` 都不含 host / user / key；`deploy-lighthouse.yml` 走 GitHub `production` Environment Secret（`LIGHTHOUSE_HOST` / `LIGHTHOUSE_USER` / `LIGHTHOUSE_SSH_PRIVATE_KEY` / `LIGHTHOUSE_KNOWN_HOSTS`），Secret 的值在 CI 之外读不到、也不该读。本机同样没有 docker。

所以改成对生产站点发**只读 HTTPS 探针**（纯 GET，不改任何东西）直接量行为：

| 探针 | 实测结果 |
| --- | --- |
| `server` 响应头 | `nginx/1.27.5` |
| `GET /`，`Accept-Encoding: gzip, deflate, br` | `content-encoding: gzip`、`transfer-encoding: chunked`、**无 `Content-Length`** |
| `GET /`，`Accept-Encoding: br` | 无 `content-encoding`，10,181 B 原文 |
| `GET /_next/static/chunks/app/layout-*.js`，`gzip, deflate, br` | `gzip`、chunked、13,453 → 5,186 B |
| 同上，`Accept-Encoding: br` | 无 `content-encoding`，13,453 B 原文 |
| `.js` 的 `Content-Type` | `application/javascript` |

四条可执行结论：

1. **生产没有 brotli。** 只声明 `br` 时退化成 identity，说明镜像里没有 brotli 模块。
2. **生产是运行时 gzip level 5。** chunked + 无 `Content-Length` 是运行时压缩的指纹；`gzip_static` 直发预压缩文件会带精确长度。也就是说第一轮加进 `fundpilot.conf` 的 `gzip_static on` **还没有部署上去**，那部分收益仍待兑现。
3. **JS 没有漏压。** `.js` 的 MIME 是 `application/javascript`，被现有 `gzip_types` 覆盖。本轮顺手把 `text/javascript` 也列进 `gzip_types`：nginx 上游有过改这个默认映射的先例，一旦改了而配置没跟上，469 KiB 的 JS 会静默地完全不压缩。
4. **生产落后于 main。** 线上 `/` 文档 10,181 B，当前构建是 13,281 B。

### 11.2 brotli：自建 nginx 镜像，默认关闭

被否掉的三条路：

| 方案 | 否掉的原因 |
| --- | --- |
| `apk add nginx-mod-http-brotli` | Alpine 仓库的模块是针对 Alpine 自编译的 nginx 构建的，与官方镜像（nginx.org 源码构建）动态模块签名不一致，`load_module` 会报 not binary compatible |
| 第三方带 brotli 的 nginx 镜像 | 供应链风险，一个金融应用的唯一入口不适合托给来源不明的镜像 |
| 换成 Alpine 自带的 nginx 包 | entrypoint、日志路径、配置目录全变，等于重做部署 |

采用的方案：`deploy/nginx/Dockerfile` 多阶段构建 —— 用**与运行镜像完全相同的 tag**做 builder，`nginx -V` 取出官方的 configure 参数原样复用，只追加 `--add-dynamic-module=ngx_brotli`，编译出 `.so` 拷到运行镜像。签名一致性由构造保证，不靠运气。

#### 过程中改掉了一个会让整件事白做的设计错误

最初生成的 `precompressed.conf` 同时写了 `gzip_static on; brotli_static on;`。查证后确认这是**错的**：`gzip_static` 是静态编入 nginx 的模块，`brotli_static` 是 `load_module` 动态加载的；nginx 的 content phase 处理器按模块注册顺序执行，而动态模块永远排在静态模块之后。于是只要客户端声明 `gzip, br`（所有现代浏览器都这样），`gzip_static` 先命中并返回 `.gz`，**`brotli_static` 永远轮不到，brotli 收益归零**。这是 ngx_brotli 的已知问题，且该模块没有提供调整优先级的配置项（[google/ngx_brotli#123](https://github.com/google/ngx_brotli/issues/123)）。

最终形态是"两个 context 分工"：

- **`fundpilot.conf` 的 server 段**：`gzip_static on;`。`gzip_static` 是官方 nginx 镜像固有编入的模块（`--with-http_gzip_static_module`），所以**不需要任何自建镜像**，官方镜像也能直发构建期的 `.gz`（gzip -9），比今天的运行时 level 5 更小且不耗 CPU。
- **自建镜像内生成的 `/etc/nginx/precompressed/precompressed.conf`**：`gzip_static off; brotli_static on; brotli on; …`，在 location 段覆盖 server 段的值。
- 必须分两个 context：同一 context 里写两次 `gzip_static` 会报 `"gzip_static" directive is duplicate`，**nginx 直接拒绝启动**。
- 站点配置用 `include /etc/nginx/precompressed/*.conf;`。官方镜像下 glob 匹配不到文件，而 nginx 对空 glob 不报错 ⇒ 空操作。如果直接写 `brotli_static on;`，官方镜像下就是 unknown directive → nginx 起不来 → 站点宕机。
- 不支持 br 的老客户端在这两个 location 里回落到运行时 gzip level 5，也就是**和今天的生产一致，不是退步**。

#### 因为本地没有 docker，一次都没能真跑，所以搭了三道防线

1. **镜像构建期断言**：`nginx -V | grep --with-http_gzip_static_module`，再用生成的 conf 跑一次 `nginx -t`。任一模块缺失或二进制不兼容都让**镜像构建失败**，而不是等生产 nginx 起不来。
2. **部署前预检**：`deploy.sh` 在**替换前端根目录之前**用真实站点配置 + 真实证书起一次性容器跑 `nginx -t`。这一步带 `--add-host api:127.0.0.1` —— nginx 在解析配置阶段就会解析 `upstream` 里的主机名，一次性容器不在 compose 网络里，少了这行会以 `host not found in upstream` 失败（这是第一版预检里的真 bug，已修）。失败时前端根目录还没被换，站点不受影响。
3. **CI 用真实请求验证**（新增）：`deploy/nginx/verify-precompressed.sh` + `frontend-perf.yml` 的 `nginx-static-layer` job。造一份带 `.gz` / `.br` 的最小 web 根，起容器发真实请求，断言"到底发的是哪种编码、是不是预压缩产物"。**判定是否预压缩的依据是 `Content-Length`**：静态模块直发预压缩文件带精确长度，运行时压缩是 chunked 没有长度。官方镜像与自建镜像各跑一遍，期望值不同：

| 客户端 `Accept-Encoding` | 官方镜像期望 | 自建 brotli 镜像期望 |
| --- | --- | --- |
| `gzip, deflate, br` | `gzip` + `.gz` 精确长度 | **`br` + `.br` 精确长度** |
| `gzip` | `gzip` + `.gz` 精确长度 | `gzip` + 无长度（运行时） |
| `identity` | 原文 + 源文件长度 | 原文 + 源文件长度 |

这个 job 同时验证了"官方镜像下站点配置照样能加载"，也就是把"没装 brotli 就宕机"这个风险钉死在 CI 里。

#### 启用方式（默认关）

`FUND_AI_NGINX_IMAGE` 留空 = 官方镜像（此时已经受益于 `gzip_static` 直发 `.gz`）。要开 brotli：

```bash
docker build -f deploy/nginx/Dockerfile -t fundpilot-nginx:brotli deploy/nginx
# 然后在服务器 .env.production 里
FUND_AI_NGINX_IMAGE=fundpilot-nginx:brotli
```

`deploy.sh` 只在 `FUND_AI_NGINX_IMAGE` 形如 `fundpilot-nginx:*` 时才构建镜像并做预检，其余情况完全走原路径。

### 11.3 体积口径升级：加上 brotli 列，预算改按传输量卡

`bundle-budget.mjs` 现在同时给出三个口径，`budget.config.json` 新增 `firstScreenGzipBytes` 门禁。

| 路由 | HTML | JS 原始 | CSS 原始 | 首屏原始 | 首屏 gzip -9 | 首屏 brotli q11 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `/` | 13.0 | 484.1 | 125.8 | 622.8 | 171.1 | **143.2** |
| `/login` | 12.0 | 474.4 | 125.8 | 612.1 | 168.3 | **140.8** |
| `/register` | 15.2 | 475.2 | 125.8 | 616.3 | 169.6 | **141.8** |
| `/reset-password` | 15.0 | 471.2 | 125.8 | 611.9 | 168.4 | **140.7** |
| `/settings` | 10.1 | 474.1 | 125.8 | 610.0 | 167.4 | **140.1** |
| `/admin/users` | 10.6 | 492.7 | 125.8 | 629.1 | 172.2 | **144.2** |

单位 KiB。全量产物：JS 51 个 / 1767.6 KiB，CSS 3 个 / 150.0 KiB。预压缩：78 个 `.gz`（原始 2125.2 → 640.3 KiB，−69.9%）+ 78 个 `.br`（→ 539.1 KiB，−74.6%），33 个 <1 KiB 的小文件跳过。

**为什么门禁卡 gzip 而不是 brotli：** gzip 是所有客户端都拿得到的保底口径，生产没启用自建镜像时发的也是 gzip。如果按 brotli 卡预算，就会出现"指标好看、但用户实际下载的是更大的 gzip"这种自欺。brotli 只作为观察列（`firstScreenBrotliBytes`），它只会让实际传输更小。

与 HEAD 基线（同一份 `node_modules` 上重建）的对比，只取同编解码器可比的部分：

| 口径（`/`） | HEAD | 本轮 | 差值 |
| --- | ---: | ---: | ---: |
| 首屏原始（HTML+JS+CSS） | 639.4 KiB | 622.8 KiB | **−16.6 KiB（−2.6%）** |
| 首屏 CSS 原始 | 148.9 KiB | 125.8 KiB | **−23.1 KiB（−15.5%）** |
| 首屏 JS+CSS gzip -9 | 170.8 KiB | 167.9 KiB | −2.9 KiB（−1.7%） |
| 首屏 JS+CSS brotli q11 | —（生产无 brotli） | 140.6 KiB | **−30.2 KiB（−17.7%，对比 HEAD 的 gzip）** |

HTML 那一栏没做同编解码器对比，因为 HEAD 基线是用第二轮之前的脚本生成的、没有记录 `htmlGzipBytes`。HTML 原始体积从 9.6 → 13.0 KiB 是第二轮骨架屏的已知代价，已在 10.5 节记账。

### 11.4 提示条改成吸附顶栏下方的固定条（第二轮 10.6 的 CLS，收口）

第二轮把 CLS 的产品侧成因定位清楚了：任何挂载后才出现的 `InlineNotice` 都会把 `.app-page-heading` 连同整个 `<main>` 向下推 **66px**。两个候选修法里第 1 个实测反而更差（估算 0.033 → 0.049），所以按第 2 个做：

```tsx
<div className="pointer-events-none fixed inset-x-0 top-[4.25rem] z-30 flex justify-center px-4 sm:px-6">
  <div className="pointer-events-auto w-full max-w-[1240px]">
    <InlineNotice … />
  </div>
</div>
```

- `top` 与 `.app-masthead` 的 `min-height`（4.25rem）对齐，宽度与外层 `max-w-[1240px]` 一致，所以视觉上仍然贴着内容列，不像"飘在页面上的浮窗"。
- 固定定位后**完全不参与文档流，位移归零**。
- 顺带得到一个对金融告警更合适的行为：滚动时提示条不会划走。
- **取舍要说清楚**：提示条存在期间会盖住页头约 66px。它是可关闭的（`onDismiss` 按钮 44px 触控区保留），而且只在真的有提示时才占位——比"任何页面永久预留 66px 空白"划算。`role` / `aria-live`（error 用 `alert`+`assertive`，其余 `status`+`polite`）逐字未改，`pointer-events-none` 只加在外层容器上，内层可交互，不挡下面的内容点击。

### 11.5 用户可见错误文案收敛：不再把 `TypeError` 原文贴给用户

第二轮 10.7 记的那个真实缺陷（基准脚本打出 `Cannot read properties of undefined (reading 'map')` 被当作用户提示展示）本轮修掉了。

新增 `src/lib/userFacingError.ts`，按**异常类型**而不是文本内容判断：

- 原生编程错误类型（`TypeError` / `ReferenceError` / `RangeError` / `SyntaxError` / `EvalError` / `URIError`）→ 一律不展示原文，只展示调用方给的兜底中文文案，原文进 `console.error`（可换成遥测回调）。
- 其余情况（`ApiError`、以及我们自己 `new Error("中文文案")` 抛出的业务错误）→ **保持原有行为**，照旧展示 `error.message`。
- 空白 message 视为无效，走兜底文案。

覆盖范围：**55 处调用点 / 36 个文件**，把原来遍布各处的 `error instanceof Error ? error.message : "兜底"` 全量替换。扫描确认仓库里已经没有"把原始异常直接送进用户可见字符串"的写法了（另外 16 处 `${error}` 模板插值经逐一核对，插的都是上游已经过滤过的 `error` 字符串状态，不是异常对象）。

**这一项不改任何正常业务错误的展示文案**，只堵住"内部错误外泄"这一条；因此不属于业务语义变更。

### 11.6 视觉基线：更新为本机 Chromium 基线，smoke 回到全绿

第一轮/第二轮报告里 6 个 `visual-regression` 失败已经用 HEAD 对照证明与改动无关（失败像素数逐一相同），根因是快照相对本机 Chromium 版本陈旧。本轮按你"剩下的一并搞好"的要求把它做完：

- `--update-snapshots` 重新生成 9 张基线（`/`、`/login`、`/register` × `desktop-1440`、`mobile-390`、`mobile-320`），清跑复核 **9 passed / 12 skipped**。
- `test:e2e:ui:smoke` 从 **24 passed / 6 failed / 6 skipped** 回到 **30 passed / 6 skipped**，与仓库文档里记录的历史基线一致。
- 视觉基线仍由 Windows Chromium 维护（spec 里 `test.skip(process.platform !== "win32")` 未改）。**在 Linux runner 上跑视觉回归这件事本轮没做**，因为生成 Linux 基线必须在 Linux 上执行，本机造不出来；先加一个 `continue-on-error` 的占位 job 只会得到一个永远不报警的假门禁，不如不加。要做的话正确顺序是：在 CI 上跑一次 `--update-snapshots` 并把产物作为 artifact 取回入库，然后才把 job 改成阻塞。

### 11.7 本轮明确没做的两项（都不是遗漏）

1. **`content-visibility: auto` 没有扩大到发现页候选卡片与日报列表。** 当前的基准脚本只能在工作台默认视图上采 CLS，这两个面板需要先进入对应 tab 并注入足量 stub 数据才有屏幕外内容。在测不到 CLS 的情况下给可滚动区域加 `content-visibility`，典型故障是滚动条长度跳变和锚点跳位——这类回归比它省下的那点渲染时间贵得多。要做就得先给基准脚本加"进入指定 tab 并采该视图 CLS"的能力，属于独立一小步。
2. **`Dashboard.tsx` 的 49 个 `useState` 依然没有合并。** 理由与第一轮相同且本轮复核仍成立：交互延迟已经停在 16 ms 单帧下限，合并状态测不出收益，而它是这批改动里唯一可能悄悄改变 batching 时序的一类。

### 11.8 本轮验证

| 项 | 结果 |
| --- | --- |
| `npm run typecheck` | 通过（exit 0） |
| `npm run lint`（`--max-warnings=0`） | 通过（exit 0） |
| `npm run test`（Vitest 全量） | **105 files / 471 passed**，与基线一致 |
| `npm run build`（production 静态导出） | 通过；`postbuild` 产出 78 个 `.gz` + 78 个 `.br` |
| `npm run perf:bundle:check` | 通过（含新增的 `firstScreenGzipBytes` 门禁） |
| `npm run test:e2e:ui:smoke` | **30 passed / 6 skipped**（6 个 skipped 是视口条件跳过） |
| `git diff --check` | 通过（exit 0，无内容告警） |
| `bash -n deploy/nginx/verify-precompressed.sh` | 通过（本机 Git Bash 5.2.37） |
| nginx 镜像构建 / `nginx -t` / 编码协商断言 | **未在本机执行**（无 docker），已交由 CI `nginx-static-layer` job 验证 |

两条需要如实记下的环境噪声：

- 连续跑了四轮 Playwright 之后，第一次全量 smoke 在 `mobile-320` 上失败一条（`history-workflows.spec.ts:221`），原因是浏览器加载 chunk 时报 `net::ERR_NO_BUFFER_SPACE` —— Windows 本地临时端口/套接字耗尽，不是产品问题。等 TCP `TIME_WAIT` 排空后重跑即 **30 passed / 6 skipped**，上表记的是这次干净结果。
- `git diff --check` 的 stderr 会打出若干条 `LF will be replaced by CRLF`。那是 `core.autocrlf=true` 对 `.ts` / `.tsx` / `.json`（`.gitattributes` 只对交给 Linux 解释器的文件强制 `eol=lf`）的正常提示；`git diff --stat` 逐一核对过，改动都是定向的几行，没有任何文件被整体重写。

### 11.9 推送、上线与生产实测（`ef22d71`）

四个提交推到 `origin/main` 后的流水线结果：

| workflow | 结果 |
| --- | --- |
| `CI`（run 181） | `api` / `web` / `e2e-smoke` 三个 job 全部 success |
| `Deploy to Lighthouse`（run 50） | **success**，生产已切到 `ef22d71` |
| `Frontend Perf`（run 1） | `bundle-budget` success、`web-vitals` success、`nginx-static-layer` **failure**（见 11.10） |

部署完成后用同一套只读探针复测生产，**预压缩直发已经生效**：

| 探针 | 上线前 | 上线后 |
| --- | --- | --- |
| `GET /`（`gzip, deflate, br`） | `gzip`、chunked、**无 Content-Length**（运行时现压） | `gzip`、**Content-Length: 3409**（预压缩直发） |
| `GET /_next/static/chunks/app/layout-*.js` | `gzip`、chunked、5,186 B | `gzip`、**Content-Length: 5197**（预压缩直发；文件本身变了，不同构建） |
| `GET /_next/static/css/1810758657f1e979.css` | `gzip`、chunked、496 B | `gzip`、**Content-Length: 497**（预压缩直发） |
| `Accept-Encoding: br` 单独 | identity | identity（brotli 仍未启用，符合预期：自建镜像默认关闭） |

判定依据仍是 `Content-Length`：静态模块直发预压缩文件带精确长度，运行时压缩是 chunked。

生产首屏实测（逐路由抓 HTML + 全部首屏 JS/CSS，`Accept-Encoding: gzip, deflate, br`）：

| 路由 | HTML | JS 原始 | CSS 原始 | 首屏原始 | 首屏实际传输 | 首屏请求数 | 其中预压缩直发 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `/` | 13.3 KiB | 483.9 | 125.8 | 623.0 | **171.4 KiB** | 11 | 10 |
| `/login` | 12.3 | 474.2 | 125.8 | 612.3 | 168.5 | 11 | 10 |
| `/register` | 15.6 | 475.1 | 125.8 | 616.4 | 169.9 | 11 | 10 |
| `/reset-password` | 15.3 | 471.0 | 125.8 | 612.1 | 168.7 | 11 | 10 |
| `/settings` | 10.4 | 473.9 | 125.8 | 610.1 | 167.7 | 11 | 10 |
| `/admin/users` | 10.9 | 492.5 | 125.8 | 629.2 | 172.4 | 11 | 10 |

与本机预算报告逐路由对得上（`/` 623.0 vs 622.8 KiB 原始、171.4 vs 171.1 KiB gzip），差值来自生产构建注入的环境变量。唯一一个不走预压缩直发的是 0.5 KiB 的 `main-app-*.js` —— 它同时低于预压缩脚本的 1 KiB 阈值和 nginx 的 `gzip_min_length 1024`，本来就不该压。

### 11.10 CI 抓到的第一个真问题：brotli 镜像构建失败

`nginx-static-layer` job 的前两步通过、第三步失败：

| 步骤 | 结果 |
| --- | --- |
| `Shell syntax`（`bash -n`） | PASS |
| `Official image keeps serving the site config` | **PASS** —— 官方镜像下 `fundpilot.conf` 正常加载，且真实请求断言到"gzip 客户端拿到的是带精确 Content-Length 的 `.gz`"。这一条正是生产实际走的路径 |
| `Build custom nginx image with brotli` | **FAIL**（16 秒即失败） |
| `Custom image prefers precompressed brotli` | skipped |

**影响面：只影响默认关闭的自建 brotli 镜像，不影响生产。** 生产用的是官方镜像，而官方镜像那条路径在同一个 job 里已被真实请求验证通过；`FUND_AI_NGINX_IMAGE` 留空时 `deploy.sh` 根本不会碰这个镜像。

修法（本轮已改）：**放弃"复刻官方 configure 参数"，改用 `--with-compat`**。

原来的做法是 `nginx -V` 取出官方镜像的完整 configure 参数原样 `eval`，为此 builder 必须装齐 `libxml2-dev` / `libxslt-dev` / `gd-dev` / `geoip-dev` / `pcre-dev` 等一堆只为满足 `--with-http_xslt_module` `--with-http_image_filter_module` 这类可选模块的开发包。这条路依赖面太大，任一包在新 Alpine 里改名或下架就会失败。

`--with-compat` 才是 nginx 官方给第三方动态模块的推荐做法：它会把 `NGX_MODULE_SIGNATURE` 里"启用了哪些可选模块"相关的位固定成常量，所以只要**同版本源码 + `--with-compat`** 编译出来的 `.so`，就能装进同样以 `--with-compat` 构建的 nginx（官方镜像正是如此）。于是：

- 依赖收敛为 `build-base git linux-headers ca-certificates pcre2-dev zlib-dev`（configure 的硬依赖只有 PCRE 与 zlib）；
- 新增构建期断言：基础镜像若不是 `--with-compat` 构建的，直接以明确文案失败，而不是产出一个装不上的 `.so`；
- ngx_brotli 从浮动 `master` 改为固定 commit（`a71f9312…`），用 `git init` + `git fetch --depth 1 <ref>` 单一代码路径，同时支持 sha 与分支名，并断言 `deps/brotli` 子模块确实拉全；
- 关键步骤前加 `echo` 分段标记，`wget` 去掉 `-q`，让日志能定位到具体阶段。

**顺带解决"看不到日志"这个更麻烦的问题。** 仓库只读权限拿不到 Actions 日志（`/actions/jobs/{id}/logs` 需要 admin），只能看到 `Process completed with exit code 1`，排查一个只在 CI 里能跑的镜像构建等于盲猜。现在构建步骤把输出 `tee` 到文件，失败时把尾部 40 行以 `::error::` workflow command 回吐成 annotation（annotation 走 check-runs API，只读权限可见），同时把完整日志上传成 `nginx-build-log` artifact。下一次失败可以直接读到真实原因。
