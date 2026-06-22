# Requirements Document

## Introduction

《好基灵》FundPilot AI 是面向 ≤5 人私有部署的基金投研助手，目前同时拥有一个能力完整的 Web 端（Next.js/React）和一个仅含 MVP 的微信小程序（`apps/miniprogram`：微信登录 + 关联邮箱、持仓只读列表、基金详情只读 4 字段、刷新板块）。后端 FastAPI 为同一套，按 `userId` 共享数据。

本特性的目标是让微信小程序在**功能层面对标** Web 端，拥有与 Web 一样的全部能力面。这里采用**功能等价（functional parity）**而非**逐像素复刻（pixel parity）**：同样的能力都要能用，但交互（UX）用小程序原生方式实现，允许在图表渲染、AI 追问流式、文件上传等处与 Web 存在合理且已被接受的平台差异。

交付按 7 个阶段递进，本需求文档以 Requirement 分组对应阶段，每个阶段可独立验收。后端 API 原则上已存在且共享（见 `apps/web/src/lib/api.ts` 导出的全部函数即为需对标的能力面）；个别需后端适配的能力（文件上传透传、非流式追问入口）在相应需求中显式标注为后端约束。

本文档使用 EARS 模式编写验收标准，并使用 INCOSE 质量规则确保可测试性。

## Glossary

- **Miniprogram（小程序）**: 运行于微信宿主的 `apps/miniprogram` 客户端，本特性的实现载体。
- **Backend（后端）**: 共享的 FastAPI 服务，端点与 `apps/web/src/lib/api.ts` 对标。
- **API_Layer**: 小程序内的请求层（`apps/miniprogram/utils/api.js`），封装 `wx.cloud.callContainer`/`wx.request`、鉴权头、401 处理与重试。
- **Design_System**: 小程序的 WXSS 设计系统，包含设计 token（颜色/间距/圆角/阴影）、字体栈与通用组件（卡片、按钮、徽标、空态、加载态、错误态）。
- **TabBar**: 小程序底部主导航栏。
- **OCR_Uploader**: 基于 `wx.chooseMedia` + `wx.uploadFile` 的截图选择与上传组件。
- **Chart_Renderer**: 小程序图表渲染组件（基于 ec-canvas/echarts 或 F2 等 canvas 图表库）。
- **Markdown_Renderer**: 小程序 Markdown 渲染组件（基于 towxml 等库）。
- **Briefing_Page（简报首页）**: 对标 Web `TodayBriefing` 的小程序页面。
- **Holdings_Board（持仓看板）**: 对标 Web `YangjibaoHoldingsBoard` 的小程序页面。
- **Fund_Detail_Page（基金详情）**: 对标 Web `YangjibaoFundDetail` 的小程序页面。
- **Profit_Analysis_Page（盈亏分析）**: 对标 Web `PortfolioDashboard` 的小程序页面。
- **Market_Page（市场）**: 对标 Web `MarketTab` 的小程序页面，含主题板块/大跌雷达/美股三个子视图。
- **Discovery_Page（推荐基金）**: 对标 Web `FundDiscoveryPanel` 的小程序页面。
- **Daily_Report_Page（生成日报）**: 对标 Web「生成日报」Tab 的小程序页面。
- **History_Page（历史日报）**: 对标 Web `HistoryRail` 的小程序页面。
- **Settings_Page（账号设置）**: 对标 Web `/settings` 的小程序页面。
- **Investor_Profile（风控画像）**: 用户风控偏好与投资预设（`InvestorProfile`）。
- **Swing_Alerts（波段盯盘）**: 盘中盯盘提醒能力（`swing-alerts`）。
- **Trading_Session_Bar（交易日语义条）**: 展示交易时段语义（`trading_session`）的通用条带。
- **Async_Job（异步任务）**: 后端异步分析/荐基任务（`/api/analyze/async`、`/api/fund-discovery/async`、`GET /api/jobs/{id}`）。
- **Cache_Layer**: 小程序本地缓存层（`wx.setStorageSync`/内存），用于「缓存优先展示」。
- **Functional_Parity（功能对标）**: 能力等价但允许平台差异的对标标准（区别于逐像素复刻）。

## Assumptions and Constraints（已确认，贯穿全文）

这些是已接受的平台现实，不是缺陷。每条均在相关 Requirement 的验收标准中以「WHERE / 平台差异」形式落地。

- **A1 功能等价而非像素一致**: 验收以「能力可用且数据等价」为准，不以视觉与 Web 逐像素一致为准。
- **A2 图表平台差异**: 分时图、收益走势、盈亏日历、持仓分布在 Miniprogram 由 Chart_Renderer（echarts/F2 类 canvas 组件）实现，视觉与交互允许与 Web 存在差异；数据口径必须等价。
- **A3 追问流式退化**: `wx.request` 不原生支持 SSE，Miniprogram 的报告/荐基 AI 追问允许退化为「整段返回」或轮询，不强求逐字流式。此能力可能需要 Backend 提供非流式追问入口（标注为后端约束 BC1）。
- **A4 上传方案**: 截图 OCR 在 Miniprogram 使用 `wx.chooseMedia` + `wx.uploadFile`，走 CloudBase `callContainer` 或已配置的合法/公网域名上传方案；后端 `/api/ocr` 与 `/api/transactions/ocr` 端点契约不变（标注为后端约束 BC2，仅需确认 multipart 经 callContainer 透传可用）。
- **A5 Markdown 渲染**: 日报/追问/荐基的 Markdown 正文由 Markdown_Renderer（towxml 类库）渲染，复杂排版允许简化。
- **A6 设计系统重建**: Web 的设计 token 在 WXSS 中重建；字体受限于小程序可用字体（默认系统字体栈），不强求与 Web 的 Sora/PingFang 完全一致。
- **A7 后端共享只读对标**: Backend 端点与数据按 `userId` 共享，原则上不改后端；如个别能力需后端适配，集中列于「后端约束」并在对应需求标注。
- **A8 账号打通已实现**: Miniprogram 登录后通过「关联邮箱账号」（`POST /api/auth/link-email`）与 Web 账号打通，数据按 `userId` 隔离，本特性沿用该机制。

### 后端约束（Backend Constraints）

- **BC1（非流式追问）**: WHERE Miniprogram 发起报告或荐基追问，THE Backend SHALL 提供可返回完整追问结果的非流式响应路径（整段返回或可轮询的任务结果），使 Miniprogram 无需消费 SSE 即可获得等价回答。
- **BC2（上传透传）**: WHERE Miniprogram 通过 `wx.uploadFile` 提交截图，THE Backend SHALL 在现有 `/api/ocr`（含 `preview`）与 `/api/transactions/ocr` 端点上接受该 multipart 上传且响应契约与 Web 等价。

## Requirements

---

## 阶段一 · 地基（设计系统 / API 层 / 导航 / 通用体验）

### Requirement 1: 小程序设计系统（WXSS）

**User Story:** 作为开发者，我想要在 WXSS 中重建好基灵的设计系统，以便所有小程序页面拥有一致且可复用的视觉与组件基础。

#### Acceptance Criteria

1. THE Design_System SHALL 定义品牌色（深海蓝主色、暖金强调色）、背景色、描边色、涨跌语义色（红涨绿跌）的 WXSS 变量。
2. THE Design_System SHALL 定义圆角、间距、阴影的 WXSS 变量。
3. WHERE 小程序可用字体受限，THE Design_System SHALL 使用小程序受支持的字体栈，并保持中文与数字展示清晰可读。
4. THE Design_System SHALL 提供可复用的通用组件：卡片、主/次按钮、徽标、分段切换控件。
5. THE Design_System SHALL 提供加载态、空态、错误态三种通用展示组件。
6. WHEN 任一页面展示金额或收益数值，THE Design_System SHALL 按红涨绿跌语义着色，并对正值显示「+」前缀。

### Requirement 2: API 层能力补全

**User Story:** 作为开发者，我想要小程序的 API_Layer 覆盖 Web 端的全部能力函数，以便各页面可直接调用后端共享端点。

#### Acceptance Criteria

1. THE API_Layer SHALL 为 `apps/web/src/lib/api.ts` 导出的每一个后端能力函数提供等价的小程序调用封装。
2. WHEN 任一请求附带已存储的访问令牌，THE API_Layer SHALL 在请求头注入 `Authorization: Bearer <token>`。
3. IF 任一请求返回 HTTP 401 且非登录/注册入口，THEN THE API_Layer SHALL 清除本地令牌并跳转登录页。
4. IF `callContainer` 网关返回基础设施层错误，THEN THE API_Layer SHALL 重试至多 3 次后回退公网 HTTP 请求。
5. IF 任一请求返回 HTTP 状态码 ≥ 400，THEN THE API_Layer SHALL 向调用方返回包含后端 `detail` 文案的错误。
6. WHERE 端点为 multipart 文件上传，THE API_Layer SHALL 提供基于 `wx.uploadFile` 的上传封装。

### Requirement 3: 底部 TabBar 导航

**User Story:** 作为用户，我想要小程序底部有主导航，以便在简报、持仓、分析、市场、发现、日报等模块间切换。

#### Acceptance Criteria

1. THE TabBar SHALL 提供进入简报、持仓、市场、推荐基金、生成日报模块的主导航入口。
2. WHERE 主导航项数量超过底部可容纳数量，THE TabBar SHALL 通过「更多」入口提供盈亏分析、历史日报、账号设置的二级跳转。
3. WHEN 用户点击某个导航项，THE Miniprogram SHALL 切换到对应页面并高亮当前项。
4. WHILE 用户未登录，THE Miniprogram SHALL 将用户导向登录页而非主导航页面。
5. WHEN 用户从某页面返回，THE Miniprogram SHALL 保持该页面上次浏览的子视图或主 Tab 选择。

### Requirement 4: 通用加载/错误/缓存体验与交易日语义条

**User Story:** 作为用户，我想要小程序在加载、出错、离线时有一致的反馈，并能看到当前交易时段语义，以便理解数据时效。

#### Acceptance Criteria

1. WHILE 任一页面正在拉取数据，THE Miniprogram SHALL 显示加载态。
2. IF 数据拉取失败，THEN THE Miniprogram SHALL 显示错误态并提供「重试」操作。
3. WHERE 页面存在本地缓存数据，THE Miniprogram SHALL 优先即时展示缓存数据，再在后台刷新并替换为最新数据。
4. THE Trading_Session_Bar SHALL 通过 `GET /api/trading-session` 获取并展示当前交易时段语义（非交易日/盘前/盘中/盘尾/盘后）与生效交易日期。
5. WHEN 当前时间处于交易日 9:30 之前，THE Trading_Session_Bar SHALL 展示回溯至上一交易日的生效交易日期。

---

## 阶段二 · 持仓增强（OCR 录入 / 新增改码 / 详情补全）

### Requirement 5: 持仓看板（列表 + 缓存优先 + 刷新板块）

**User Story:** 作为用户，我想要在小程序看到与 Web 一致的持仓看板，以便随时查看我的基金持仓与当日表现。

#### Acceptance Criteria

1. WHEN 用户进入 Holdings_Board，THE Holdings_Board SHALL 通过 `GET /api/portfolio/holdings` 获取并展示持仓列表。
2. WHERE 本地存在持仓缓存，THE Holdings_Board SHALL 先即时展示缓存，再用后端响应替换。
3. THE Holdings_Board SHALL 为每条持仓展示基金名称、持有金额、持有收益率、当日收益与关联板块涨跌。
4. WHEN 用户触发刷新板块，THE Holdings_Board SHALL 调用 `POST /api/holdings/refresh-sector-quotes` 并更新各持仓的板块涨跌与当日收益。
5. WHEN 用户点击某条持仓，THE Holdings_Board SHALL 跳转到该基金的 Fund_Detail_Page。
6. IF 持仓列表为空，THEN THE Holdings_Board SHALL 展示空态并提供录入持仓与关联邮箱账号的入口。
7. THE Holdings_Board SHALL 展示数据更新时间（`refreshed_at`）。

### Requirement 6: 截图 OCR 录入

**User Story:** 作为用户，我想要在小程序上传支付宝/养基宝截图并自动识别持仓，以便无需手动录入。

#### Acceptance Criteria

1. WHEN 用户选择录入截图，THE OCR_Uploader SHALL 通过 `wx.chooseMedia` 让用户从相册或相机选择图片。
2. WHEN 用户确认上传图片，THE OCR_Uploader SHALL 通过 `wx.uploadFile` 以 `preview=true` 调用 `/api/ocr` 获取识别预览。
3. THE OCR_Uploader SHALL 支持支付宝持仓列表、养基宝总览、养基宝详情三类截图来源的选择。
4. WHEN 识别预览返回，THE Miniprogram SHALL 展示可编辑的识别结果，包含基金代码、名称、金额字段。
5. WHEN 用户确认识别结果，THE Miniprogram SHALL 调用 `POST /api/portfolio/apply-holdings` 写入持仓并刷新 Holdings_Board。
6. IF 识别结果包含校验告警（`holding_warnings`），THEN THE Miniprogram SHALL 展示该告警供用户确认或修正。
7. WHERE 后端约束 BC2 适用，THE Miniprogram SHALL 通过 `wx.uploadFile` 提交 multipart 截图而非 Web 的 `FormData`，且识别数据口径与 Web 等价。

### Requirement 7: 新增持仓 / 改代码 / 改金额 / 东财搜索查码

**User Story:** 作为用户，我想要在小程序手动新增基金、修改代码或金额并按名称搜索基金代码，以便修正与补全持仓数据。

#### Acceptance Criteria

1. WHEN 用户输入基金名称关键字，THE Miniprogram SHALL 调用 `GET /api/funds/search` 并展示候选基金代码与名称。
2. WHEN 用户选择搜索结果中的某只基金，THE Miniprogram SHALL 将其代码与名称填入录入表单。
3. WHEN 用户提交新增或修改的持仓金额，THE Miniprogram SHALL 通过 `POST /api/portfolio/apply-holdings` 持久化。
4. WHEN 用户修改某基金档案的代码或名称，THE Miniprogram SHALL 调用 `PATCH /api/fund-profiles/{code}` 持久化变更。
5. IF 修改后的基金代码无法被后端解析，THEN THE Miniprogram SHALL 展示错误并保留用户输入待修正。

### Requirement 8: 基金详情补全

**User Story:** 作为用户，我想要小程序的基金详情拥有与 Web 一致的持有数据、图表与历史净值，以便深入了解单只基金。

#### Acceptance Criteria

1. WHEN 用户进入 Fund_Detail_Page，THE Fund_Detail_Page SHALL 通过 `POST /api/holdings/detail` 获取并展示持有金额、持有收益、持有份额、成本、持有天数、最新净值与净值日期。
2. WHERE 平台差异 A2 适用，THE Fund_Detail_Page SHALL 使用 Chart_Renderer 展示关联板块分时图（数据来自 `GET /api/sector-quotes/intraday`），视觉允许与 Web 差异。
3. THE Fund_Detail_Page SHALL 展示业绩走势：本基金区间涨跌与沪深300（`GET /api/market/index-daily`）的对比，区间含近1月/3月/6月/1年/3年。
4. WHEN 用户查看历史净值，THE Fund_Detail_Page SHALL 通过 `GET /api/fund-profiles/{code}/nav-history/page` 分页加载净值记录并支持滚动加载更多。
5. WHEN 用户设置首次购入日，THE Fund_Detail_Page SHALL 通过滚轮日期选择器选择日期并调用 `PATCH /api/fund-profiles/{code}` 写入 `first_purchase_date`。
6. WHEN `first_purchase_date` 更新成功，THE Fund_Detail_Page SHALL 展示按该日期重算后的持有天数。

---

## 阶段三 · 盈亏分析

### Requirement 9: 盈亏分析

**User Story:** 作为用户，我想要在小程序查看组合盈亏分析，以便了解收益走势、每日盈亏与持仓分布。

#### Acceptance Criteria

1. WHEN 用户进入 Profit_Analysis_Page，THE Profit_Analysis_Page SHALL 通过 `GET /api/portfolio/dashboard` 获取盈亏分析数据。
2. WHEN 用户切换收益走势区间，THE Profit_Analysis_Page SHALL 以 `today`、`week`、`month`、`year`、`all` 之一为 `range` 重新请求并刷新走势。
3. WHERE 平台差异 A2 适用，THE Profit_Analysis_Page SHALL 使用 Chart_Renderer 展示「我的收益 vs 沪深300」收益走势曲线，视觉允许与 Web 差异。
4. WHERE 平台差异 A2 适用，THE Profit_Analysis_Page SHALL 使用 Chart_Renderer 展示盈亏日历，并对每个交易日按盈亏正负着色。
5. WHEN 用户选择某年月，THE Profit_Analysis_Page SHALL 以 `calendar_year` 与 `calendar_month` 请求并刷新盈亏日历。
6. THE Profit_Analysis_Page SHALL 展示当日盈亏 TOP5 的盈利与亏损基金列表。
7. WHERE 平台差异 A2 适用，THE Profit_Analysis_Page SHALL 使用 Chart_Renderer 展示持仓分布甜甜圈图，视觉允许与 Web 差异。

---

## 阶段四 · 市场（主题板块 / 大跌雷达 / 美股）

### Requirement 10: 主题板块

**User Story:** 作为用户，我想要在小程序查看主题板块行情，以便了解板块涨跌、主力净流入与连涨情况。

#### Acceptance Criteria

1. WHEN 用户进入 Market_Page 的主题板块子视图，THE Market_Page SHALL 通过 `GET /api/market/theme-boards` 获取并展示板块列表。
2. THE Market_Page SHALL 为每个板块展示名称、板块类型标签（行业/概念/指数）、日涨跌幅、连涨天数与主力净流入。
3. WHEN 用户选择排序方式，THE Market_Page SHALL 以 `change`、`streak`、`inflow` 之一为 `sort` 重新请求并按该字段排序展示。
4. WHEN 用户展开某板块的资金流明细，THE Market_Page SHALL 展示超大单/大单/中单/小单四档净流入。
5. WHERE 某板块为用户持仓所属板块，THE Market_Page SHALL 标记「持仓」标签。
6. WHEN 用户对某板块选择「看大跌基金」，THE Market_Page SHALL 跳转到大跌雷达子视图并按该板块筛选。
7. WHEN 用户对某板块选择「加入关注方向」，THE Miniprogram SHALL 将该板块加入推荐基金的关注方向（最多 3 个）。
8. THE Market_Page SHALL 展示数据更新时间（`refreshed_at`）。

### Requirement 11: 大跌反弹雷达

**User Story:** 作为用户，我想要在小程序查看大跌反弹雷达，以便发现跨板块的大跌基金及其历史反弹命中率。

#### Acceptance Criteria

1. WHEN 用户进入大跌雷达子视图，THE Market_Page SHALL 通过 `GET /api/market/dip-radar` 获取并展示大跌基金列表。
2. THE Market_Page SHALL 为每条记录展示基金名称、所属板块、区间跌幅、反弹评分与反弹信号。
3. WHERE 某条记录含历史命中率（`historical_hint`），THE Market_Page SHALL 展示样本数与 3 日反弹概率。
4. WHEN 用户切换回看天数，THE Market_Page SHALL 以 `lookback_days` 为 3 或 5 重新请求并刷新列表。
5. WHEN 用户对某条记录选择「深度扫描」，THE Miniprogram SHALL 跳转到 Discovery_Page 并以 `dip_swing` 扫描模式预填。
6. IF 大跌雷达数据不可用，THEN THE Market_Page SHALL 展示后端返回的不可用提示文案。

### Requirement 12: 美股概览

**User Story:** 作为用户，我想要在小程序查看美股概览，以便了解美股期货、汇率、QDII 盘前参考与美东时段。

#### Acceptance Criteria

1. WHEN 用户进入美股子视图，THE Market_Page SHALL 通过 `GET /api/market/us-overview` 获取并展示美股概览数据。
2. THE Market_Page SHALL 展示纳指、标普、道指三条指数期货的最新价与涨跌幅。
3. THE Market_Page SHALL 展示 USD/CNY 汇率的最新价与涨跌幅。
4. THE Market_Page SHALL 展示 QDII 盘前参考涨跌列表，并标注为非承诺性预估。
5. THE Market_Page SHALL 展示美东交易时段标签（盘前/盘中/盘后/休市）与更新时间。
6. IF 某数据源状态为 `unavailable`，THEN THE Market_Page SHALL 对该数据源显示占位提示而非编造数值。

---

## 阶段五 · 生成日报（含非流式追问）

### Requirement 13: 生成日报

**User Story:** 作为用户，我想要在小程序生成基于我持仓的 AI 日报，以便获得逐持仓的操作建议。

#### Acceptance Criteria

1. WHEN 用户配置生成日报，THE Daily_Report_Page SHALL 允许选择风控画像/投资预设、AI 角色设定、分析模式（快速/深度）。
2. WHEN 用户请求新闻预览，THE Daily_Report_Page SHALL 通过 `POST /api/news/preview` 获取并展示主题要闻与新闻时效。
3. WHEN 用户提交生成日报，THE Daily_Report_Page SHALL 通过 `POST /api/analyze/async` 创建 Async_Job 并获得任务标识。
4. WHILE Async_Job 处于进行中状态，THE Daily_Report_Page SHALL 轮询 `GET /api/jobs/{id}` 并展示当前阶段进度。
5. WHEN Async_Job 完成，THE Daily_Report_Page SHALL 展示日报正文，包含组合摘要、逐持仓建议、主题要闻与风险提示。
6. WHERE 平台差异 A5 适用，THE Daily_Report_Page SHALL 使用 Markdown_Renderer 渲染日报 Markdown 正文。
7. WHEN 用户查看调仓示意，THE Daily_Report_Page SHALL 通过 `GET /api/reports/{id}/rebalance-simulation` 获取并展示调仓模拟。
8. WHEN 用户查看建议复盘，THE Daily_Report_Page SHALL 通过 `GET /api/reports/{id}/outcomes` 获取并展示复盘结果。
9. WHEN 用户导出日报，THE Daily_Report_Page SHALL 通过 `GET /api/reports/{id}/markdown` 获取 Markdown 文本供用户复制或保存。
10. IF Async_Job 失败，THEN THE Daily_Report_Page SHALL 展示错误信息并允许重新生成。

### Requirement 14: 日报 AI 追问（非流式）

**User Story:** 作为用户，我想要对生成的日报进行追问，以便获得针对性的解释与建议。

#### Acceptance Criteria

1. WHEN 用户进入某份日报的追问，THE Daily_Report_Page SHALL 通过 `GET /api/reports/{id}/chat` 获取并展示历史追问消息。
2. WHEN 用户提交追问，THE Daily_Report_Page SHALL 提交追问内容与追问模式（快速/深度）至后端。
3. WHERE 平台差异 A3 与后端约束 BC1 适用，THE Daily_Report_Page SHALL 以非流式方式获取完整追问回答，不要求逐字流式展示。
4. WHEN 追问回答返回，THE Daily_Report_Page SHALL 使用 Markdown_Renderer 渲染回答正文并追加到对话列表。
5. IF 追问请求失败，THEN THE Daily_Report_Page SHALL 展示错误并允许重试。

---

## 阶段六 · 推荐基金

### Requirement 15: 推荐基金扫描与报告

**User Story:** 作为用户，我想要在小程序运行推荐基金扫描并查看荐基报告，以便发现新的投资机会。

#### Acceptance Criteria

1. WHEN 用户配置推荐扫描，THE Discovery_Page SHALL 允许选择扫描模式（`full_market`/`portfolio_gap`/`dip_swing`）、关注方向（最多 3 个）、选基策略、基金类型偏好、预算与分析模式（快速/深度）。
2. WHEN 用户加载关注方向候选，THE Discovery_Page SHALL 通过 `GET /api/fund-discovery/sectors` 获取板块热度列表。
3. WHEN 用户允许编辑荐基 AI 角色设定，THE Discovery_Page SHALL 通过 `GET /api/discovery-prompt` 与 `PUT /api/discovery-prompt` 读取并保存角色设定。
4. WHEN 用户提交扫描，THE Discovery_Page SHALL 通过 `POST /api/fund-discovery/async` 创建 Async_Job 并获得任务标识。
5. WHILE Async_Job 进行中，THE Discovery_Page SHALL 轮询 `GET /api/jobs/{id}` 并展示进度。
6. WHEN Async_Job 完成，THE Discovery_Page SHALL 展示荐基报告，包含市场观点、候选池、推荐基金列表与风险提示。
7. WHEN 用户点击某推荐基金，THE Discovery_Page SHALL 展示该基金的详情预览。

### Requirement 16: 推荐历史、复盘与追问

**User Story:** 作为用户，我想要管理历史荐基报告并对其追问与复盘，以便回顾推荐效果。

#### Acceptance Criteria

1. WHEN 用户查看推荐历史，THE Discovery_Page SHALL 通过 `GET /api/fund-discovery/reports` 获取并展示历史报告列表。
2. WHEN 用户选择多份历史报告并确认删除，THE Discovery_Page SHALL 通过 `DELETE /api/fund-discovery/reports/{id}` 批量删除所选报告。
3. WHEN 用户查看某报告复盘，THE Discovery_Page SHALL 通过 `GET /api/fund-discovery/reports/{id}/outcomes` 获取并展示复盘结果。
4. WHEN 用户进入某报告追问，THE Discovery_Page SHALL 通过 `GET /api/fund-discovery/reports/{id}/chat` 获取历史追问消息。
5. WHERE 平台差异 A3 与后端约束 BC1 适用，THE Discovery_Page SHALL 以非流式方式获取完整荐基追问回答。
6. WHEN 荐基追问回答返回，THE Discovery_Page SHALL 使用 Markdown_Renderer 渲染并追加到对话列表。

---

## 阶段七 · 简报首页 / 风控 / 盯盘 / 历史 / 账号设置（收尾）

### Requirement 17: 简报首页

**User Story:** 作为用户，我想要登录后默认看到简报首页，以便一屏掌握组合概况、板块脉搏与最新日报决策。

#### Acceptance Criteria

1. WHEN 用户登录后进入 Miniprogram，THE Miniprogram SHALL 默认展示 Briefing_Page。
2. THE Briefing_Page SHALL 展示组合 KPI 摘要（总资产、当日收益、持有收益）。
3. THE Briefing_Page SHALL 展示板块脉搏摘要。
4. WHERE 存在最新日报，THE Briefing_Page SHALL 展示该日报的决策卡摘要。
5. WHEN 用户在简报内发起内联追问，THE Briefing_Page SHALL 以非流式方式（平台差异 A3）返回并展示追问回答。

### Requirement 18: 风控画像与投资预设

**User Story:** 作为用户，我想要在小程序设置风控画像与投资预设，以便影响日报与荐基的建议口径。

#### Acceptance Criteria

1. WHEN 用户进入风控设置，THE Miniprogram SHALL 通过 `GET /api/investor-profile` 获取并展示当前 Investor_Profile。
2. THE Miniprogram SHALL 允许设置浮亏线、单只集中度、期望投入总额、偏定投、拒绝追高。
3. WHEN 用户切换投资预设，THE Miniprogram SHALL 在 `conservative_hold` 与 `aggressive_swing` 之间切换并联动相关风控参数。
4. WHEN 用户保存风控画像，THE Miniprogram SHALL 通过 `PUT /api/investor-profile` 持久化变更。
5. THE Miniprogram SHALL 通过 `GET /api/analysis-prompt` 与 `PUT /api/analysis-prompt` 读取并保存日报 AI 角色设定。

### Requirement 19: 波段盯盘提醒

**User Story:** 作为用户，我想要在小程序获得盘中波段盯盘提醒，以便及时关注止盈与加仓机会。

#### Acceptance Criteria

1. WHERE 用户启用波段盯盘，THE Miniprogram SHALL 通过 `POST /api/swing-alerts/evaluate` 评估并展示提醒项。
2. WHEN 用户进入盯盘视图，THE Miniprogram SHALL 通过 `GET /api/swing-alerts/today` 获取当日提醒。
3. THE Miniprogram SHALL 为每条提醒展示类型、标题、内容与优先级。
4. WHERE 某提醒为新增提醒，THE Miniprogram SHALL 标记其为新提醒。

### Requirement 20: 历史日报

**User Story:** 作为用户，我想要在小程序查看与管理历史日报，以便回顾并清理过往报告。

#### Acceptance Criteria

1. WHEN 用户进入 History_Page，THE History_Page SHALL 通过 `GET /api/reports` 获取并展示历史日报列表。
2. WHEN 用户选择某历史日报，THE History_Page SHALL 展示该日报详情。
3. WHEN 用户选择多份历史日报并确认删除，THE History_Page SHALL 通过 `DELETE /api/reports/{id}` 批量删除所选日报。

### Requirement 21: 账号设置

**User Story:** 作为用户，我想要在小程序管理账号绑定，以便打通微信与邮箱账号。

#### Acceptance Criteria

1. WHEN 用户进入 Settings_Page，THE Settings_Page SHALL 通过 `GET /api/auth/me` 获取并展示当前账号与微信绑定状态。
2. WHERE 用户当前为微信占位账号，THE Settings_Page SHALL 提供「关联已有邮箱账号」入口并通过 `POST /api/auth/link-email` 完成打通。
3. WHEN 关联邮箱成功，THE Settings_Page SHALL 使用返回的新令牌更新本地登录状态。
4. IF 关联邮箱失败，THEN THE Settings_Page SHALL 展示后端返回的错误文案并保留用户输入。
