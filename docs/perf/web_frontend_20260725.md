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

## 9. 回滚方式

每一类改动都可以独立回退：

- CSS 拆分：删 `src/app/dashboard.css`、去掉 `Dashboard.tsx` 里那行 import、`git checkout -- apps/web/src/app/globals.css`。
- `useDeferredValue`：`Dashboard.tsx` 里把 5 处 `deferredActiveTab` 换回 `activeTab`，删掉一行声明。
- 图表 `memo`：每个文件末尾的 `export const X = memo(XView)` 换回 `export function X(...)` 即可。
- formatter 上提：纯局部替换，不影响调用方。
- nginx 预压缩：删掉两处 `gzip_static on;` 即回落运行时 gzip；`.gz` 是附加文件，删除不影响服务。
- 度量脚本与 workflow：纯新增文件。
