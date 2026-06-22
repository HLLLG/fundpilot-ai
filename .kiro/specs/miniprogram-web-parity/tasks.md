# Implementation Plan: 小程序对标 Web 功能（miniprogram-web-parity）

## Overview

按 requirements.md 的 7 阶段递进交付。实现策略遵循 design.md：演进式扩展现有 MVP（不重写），把可测逻辑抽到无 `wx.*` 依赖的纯函数模块（`utils/format.js`、`utils/derive.js`、`utils/cache.js`、`utils/nav-state.js`、`utils/request.js` 决策 helper），页面/组件层只做渲染与事件绑定。小程序端逻辑用 JavaScript + fast-check 做属性测试；后端 BC1 聚合 helper 用 Python + Hypothesis。

后端仅两处最小适配且对 Web 零影响：BC1 新增非流式追问聚合端点（`/chat/sync`），BC2 仅以测试验证现有 multipart 上传透传（无需改后端代码）。

约定：
- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP；非 `*` 子任务必须实现。
- 每个属性测试以注释标注来源：`Feature: miniprogram-web-parity, Property {number}: {property_text}`，迭代 ≥ 100 次。
- 测试用一次性执行（`--run` / 非 watch 模式）。

## Tasks

- [x] 1. 阶段一·地基：设计系统 / 纯函数层 / 请求层 / 导航 / 通用组件
  - [x] 1.1 创建 WXSS 设计系统 token 与通用样式
    - 新建 `apps/miniprogram/styles/tokens.wxss`：品牌色（深海蓝 #2356e0、暖金 #cf9b3e）、背景/描边色、涨跌语义色（红涨 #e5484d / 绿跌 #2bae66）、圆角、间距、阴影 WXSS 变量
    - 新建 `apps/miniprogram/styles/shared.wxss`：卡片、主/次按钮、徽标、分段切换控件通用类，使用受支持字体栈
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 实现 `utils/format.js` 数字/金额/百分比格式化纯函数
    - 实现红涨绿跌着色判定、正值「+」前缀、千分位、百分号格式化（不依赖 `wx.*`）
    - _Requirements: 1.6, 5.3, 10.2_

  - [x] 1.3 为 `format.js` 编写属性测试
    - **Property 1: 数字格式化的符号与配色一致性**
    - **Validates: Requirements 1.6, 5.3, 10.2**
    - 使用 fast-check，≥100 次迭代

  - [x] 1.4 抽离 `utils/request.js` 底层请求层与决策 helper
    - 从现有 `api.js` 抽出 `request/requestViaCallContainer*/requestViaHttp/handleResponse`（行为不变），暴露纯函数 helper：`buildAuthHeader(token)`、`shouldClearToken(statusCode, isAuthEntrypoint, allowUnauthorized)`、`extractError(status, body)`、`buildQuery(params)`
    - 保留 callContainer 重试 ≤3（退避 600ms×n）后回退公网 HTTP、401 清 token 跳登录、≥400 抛含 `detail` 的错误
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 9.2, 9.5, 10.3, 11.4_

  - [x] 1.5 为鉴权头注入编写属性测试
    - **Property 2: 鉴权头注入**
    - **Validates: Requirements 2.2**

  - [x] 1.6 为 401 清登态决策编写属性测试
    - **Property 3: 401 清登态决策**
    - **Validates: Requirements 2.3**

  - [x] 1.7 为错误文案提取编写属性测试
    - **Property 4: 错误文案提取**
    - **Validates: Requirements 2.5**

  - [x] 1.8 为枚举请求参数构造编写属性测试
    - **Property 7: 枚举请求参数构造**
    - **Validates: Requirements 9.2, 9.5, 10.3, 11.4**

  - [x] 1.9 实现 `utils/cache.js` 缓存优先读写封装
    - 封装 `wx.setStorageSync/getStorageSync`（注入存储后端以便测试），未写入键读取返回空值不抛错
    - _Requirements: 4.3, 5.2_

  - [x] 1.10 为缓存往返编写属性测试
    - **Property 6: 缓存往返**
    - **Validates: Requirements 4.3, 5.2**

  - [x] 1.11 实现 `utils/nav-state.js` 跨页预填与子视图保持
    - 基于会话存储键实现 round-trip 写读（注入存储后端），支持 `dipRadarSector`/`discoveryFocusSectors`/`discoveryScanMode`/各页 `subView`
    - _Requirements: 3.5, 10.6, 11.5_

  - [x] 1.12 为导航状态往返编写属性测试
    - **Property 5: 导航状态往返**
    - **Validates: Requirements 3.5, 10.6, 11.5**

  - [x] 1.13 扩展 `utils/api.js` 按域封装能力函数
    - 在 `request.js` 之上按域（auth/holdings/ocr/funds/profit/market/report/discovery/risk/session）逐一封装 `api.ts` 导出的全部能力函数，命名对齐 Web；保留并并入现有 `fetchHoldings/refreshSectorQuotes/fetchHoldingDetail/wechatLogin/linkEmail`
    - 新增 `uploadFile(path, filePath, formData, name)` 封装 `wx.uploadFile`（注入 Bearer、解析 JSON、复用 401/错误处理）
    - _Requirements: 2.1, 2.6_

  - [x] 1.14 实现 `state-view` 与 `num-text` 通用组件
    - `state-view`：props `{ loading, error, empty, onRetry }`，封装加载/错误（带重试）/空态三态
    - `num-text`：props `{ value, kind, signed }`，复用 `format.js` 输出红涨绿跌 + 千分位 + 正号
    - _Requirements: 1.5, 1.6, 4.1, 4.2_

  - [x] 1.15 实现 `trading-session-bar` 交易日语义条组件
    - 挂 `GET /api/trading-session`，展示时段语义与生效交易日，9:30 前回溯上一交易日
    - _Requirements: 4.4, 4.5_

  - [x] 1.16 配置 TabBar 与全局登录守卫
    - 在 `app.json` 配置 5 项 TabBar（简报/持仓/市场/推荐基金/生成日报）；`app.js` 全局 token 守卫未登录 `reLaunch` 登录页，TabBar 切换高亮，提供「更多」二级入口跳转占位
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 2. 阶段二·持仓增强：持仓看板 / OCR 录入 / 新增改码搜索 / 基金详情
  - [x] 2.1 搭建 `ec-canvas` 桥接组件与 echarts 精简自定义构建
    - 放入官方 `components/ec-canvas/` 桥接组件与仅含 `line`/`pie` + `grid`/`tooltip` 的自定义构建产物，必要时配置分包加载
    - _Requirements: 8.2, 9.3, 9.7_

  - [x] 2.2 升级 Holdings_Board 持仓看板页
    - 在 `utils/derive.js` 实现持仓排序/状态推导纯函数；`pages/holdings` 缓存优先即时渲染 + 后端替换、展示名称/持有金额/持有收益率/当日收益/板块涨跌、刷新板块、`refreshed_at`、空态录入与关联邮箱入口、点击跳详情
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 2.3 实现 `ocr-uploader` 截图 OCR 录入组件
    - `wx.chooseMedia` 选图 + 来源选择（支付宝/养基宝总览/养基宝详情）、`wx.uploadFile` 以 `preview=true` 调 `/api/ocr`、展示可编辑识别结果（代码/名称/金额）、`holding_warnings` 展示、确认调 `/api/portfolio/apply-holdings` 刷新看板缓存（BC2 直传）
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 2.4 实现手动新增/改代码/改金额与东财搜索查码
    - `GET /api/funds/search` 候选展示与回填、`POST /api/portfolio/apply-holdings` 持久化金额、`PATCH /api/fund-profiles/{code}` 改码/名、代码不可解析时报错并保留输入
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 2.5 实现 Fund_Detail 详情核心与分时图
    - `pages/fund-detail` 升级：`POST /api/holdings/detail` 展示持有金额/收益/份额/成本/持有天数/最新净值与日期；`chart-line` + `ec-canvas` 渲染板块分时图（`GET /api/sector-quotes/intraday`）
    - _Requirements: 8.1, 8.2_

  - [x] 2.6 实现业绩对比与历史净值分页
    - 业绩走势：本基金区间涨跌 vs 沪深300（`GET /api/market/index-daily`，近1/3/6月/1年/3年）；`GET /api/fund-profiles/{code}/nav-history/page` 游标分页滚动加载并在 `derive.js` 合并去重排序
    - _Requirements: 8.3, 8.4_

  - [x] 2.7 为净值分页合并编写属性测试
    - **Property 9: 净值分页合并**
    - **Validates: Requirements 8.4**

  - [x] 2.8 实现首次购入日选择与持有天数重算
    - 滚轮日期选择器选 `first_purchase_date`，`PATCH /api/fund-profiles/{code}` 写入，成功后展示重算持有天数
    - _Requirements: 8.5, 8.6_

- [x] 3. 检查点 - 确保阶段一/二测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. 阶段三·盈亏分析
  - [x] 4.1 在 `derive.js` 实现盈亏分析图表数据映射纯函数
    - 收益走势折线 option（保点、x 轴时间有序）、持仓分布甜甜圈 option（扇区值=weight_percent、总和≈100%）、盈亏日历着色映射、当日 TOP5 推导（盈利降序≤5/亏损升序≤5、互斥）
    - _Requirements: 9.3, 9.4, 9.6, 9.7_

  - [x] 4.2 为图表数据映射保真编写属性测试
    - **Property 8: 图表数据映射保真**
    - **Validates: Requirements 8.2, 9.3, 9.7**

  - [x] 4.3 为当日 TOP5 推导编写属性测试
    - **Property 11: 当日 TOP5 推导**
    - **Validates: Requirements 9.6**

  - [x] 4.4 实现 Profit_Analysis 页主体
    - `pages/profit`：`GET /api/portfolio/dashboard` 拉取、区间切换（today/week/month/year/all 重请求）、`chart-line` 收益走势 vs 沪深300、`chart-donut` 持仓分布、当日盈亏 TOP5 列表
    - _Requirements: 9.1, 9.2, 9.3, 9.6, 9.7_

  - [x] 4.5 实现 `chart-calendar` 盈亏日历组件
    - WXML 网格 + `num-text` 自绘日历，按 `profit_calendar.days[]` 的 `daily_profit` 符号着色、非交易日占位，`calendar_year`/`calendar_month` 选择重请求
    - _Requirements: 9.4, 9.5_

  - [x] 4.6 为盈亏日历配色编写属性测试
    - **Property 10: 盈亏日历配色**
    - **Validates: Requirements 9.4**

- [x] 5. 阶段四·市场：主题板块 / 大跌雷达 / 美股
  - [x] 5.1 实现 Market_Page 主题板块子视图
    - `pages/market`：`GET /api/market/theme-boards` 列表、名称/类型标签/日涨跌/连涨/主力净流入、`sort`（change/streak/inflow）切换、资金流四档明细展开、持仓标签、`refreshed_at`、「看大跌基金」跳大跌雷达预填、「加入关注方向」写 nav-state，子视图保持
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 3.5_

  - [x] 5.2 在 `derive.js` 实现关注方向增减约束逻辑
    - 加入去重、长度 ≤ 3、已满且新方向不在其中时保持不变
    - _Requirements: 10.7_

  - [x] 5.3 为关注方向增减约束编写属性测试
    - **Property 12: 关注方向增减约束**
    - **Validates: Requirements 10.7**

  - [x] 5.4 实现大跌反弹雷达子视图
    - `GET /api/market/dip-radar` 列表、名称/板块/区间跌幅/反弹评分/反弹信号、`historical_hint` 样本数与3日反弹概率、`lookback_days`（3/5）切换、「深度扫描」跳 Discovery 以 `dip_swing` 预填、不可用展示后端 `message`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [x] 5.5 实现美股概览子视图
    - `GET /api/market/us-overview`：纳指/标普/道指期货、USD/CNY、QDII 盘前参考（标注非承诺）、美东时段标签与更新时间；`status==='unavailable'` 占位映射不渲染数值
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x] 5.6 为美股数据源不可用占位编写属性测试
    - **Property 13: 美股数据源不可用占位**
    - **Validates: Requirements 12.6**

- [x] 6. 阶段五·生成日报（含后端 BC1 非流式追问）
  - [x] 6.1 后端实现 `aggregate_chat_stream` 聚合 helper
    - 在 API 服务新增纯函数 helper：遍历追问生成器事件，对 `token` 累加 `content`、对 `done` 取最终消息、对 `error` 抛 `ValueError`；不触碰现有 SSE 路由（Web 零影响）
    - _Requirements: 14.3, 16.5, 17.5_

  - [x] 6.2 后端新增 `POST /api/reports/{report_id}/chat/sync` 非流式端点
    - 复用现有 `stream_report_chat` 生成器 + `aggregate_chat_stream` 返回 `{ user_message, message, chat_mode, model? }`，`ValueError`→400 携带 `detail`；与 SSE 端点并存
    - _Requirements: 14.3_

  - [x] 6.3 为追问流聚合编写属性测试（Hypothesis）
    - **Property 14: 追问流聚合（BC1）**
    - **Validates: Requirements 14.3, 16.5, 17.5**
    - 使用 pytest + Hypothesis，≥100 次迭代

  - [x] 6.4 后端回归与 BC2 验证测试
    - 集成测试断言 `/chat/sync` 落库与响应契约、原 SSE 端点行为不变；`TestClient` 以 `multipart/form-data`（`file` + `preview='true'`）提交 `/api/ocr` 与 `/api/transactions/ocr` 验证契约与 Web 等价（BC2）
    - _Requirements: 6.7, 14.3_

  - [x] 6.5 在 `derive.js` 实现任务轮询状态机与阶段标签映射
    - `pending/running`→继续轮询、`completed/failed`→停止，已知 stage→唯一标签
    - _Requirements: 13.4, 15.5_

  - [x] 6.6 为任务轮询状态机编写属性测试
    - **Property 16: 任务轮询状态机**
    - **Validates: Requirements 13.4, 15.5**

  - [x] 6.7 实现 `md-view` Markdown 渲染组件（towxml）
    - 放入 `components/towxml/`，封装 `md-view` props `{ markdown }`，主题与设计 token 对齐，随分包加载
    - _Requirements: 13.6_

  - [x] 6.8 实现 Daily_Report 生成日报页主体
    - `pages/report`：配置（风控画像/预设、AI 角色、快速/深度）、`POST /api/news/preview` 新闻预览、`POST /api/analyze/async` 创建任务、轮询 `GET /api/jobs/{id}` 展进度、`md-view` 渲染日报正文、`GET /api/reports/{id}/rebalance-simulation` 调仓示意、`GET /api/reports/{id}/outcomes` 复盘、`GET /api/reports/{id}/markdown` 导出、失败展示错误并允许重生
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10_

  - [x] 6.9 实现 `chat-panel` 非流式追问组件并接入日报追问
    - `GET /api/reports/{id}/chat` 历史、`POST .../chat/sync` 提交（快速/深度）、`md-view` 渲染并追加对话、失败可重试
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

  - [x] 6.10 为对话追加顺序编写属性测试
    - **Property 15: 对话追加顺序**
    - **Validates: Requirements 14.4, 16.6**

- [x] 7. 检查点 - 确保阶段三/四/五测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. 阶段六·推荐基金
  - [x] 8.1 后端新增 `POST /api/fund-discovery/reports/{report_id}/chat/sync`
    - 同构契约，复用荐基 SSE 生成器 + `aggregate_chat_stream` 聚合返回 JSON，`ValueError`→400；SSE 端点不动
    - _Requirements: 16.5_

  - [x] 8.2 后端荐基非流式聚合测试
    - 单测断言聚合等于 token 拼接、`done` 取最终消息、`error`→400；集成测试验证落库与契约、SSE 回归不变
    - _Requirements: 16.5_

  - [x] 8.3 实现 Discovery_Page 扫描与报告主体
    - `pages/discovery`：扫描配置（模式/关注方向≤3/选基策略/类型偏好/预算/快速深度）、`GET /api/fund-discovery/sectors` 候选、`GET|PUT /api/discovery-prompt` 角色设定、`POST /api/fund-discovery/async` 建任务、轮询 `GET /api/jobs/{id}`、`md-view` 渲染荐基报告（市场观点/候选池/推荐列表/风险）、点击推荐基金详情预览
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7_

  - [x] 8.4 实现荐基历史/复盘/批量删除/非流式追问
    - `GET /api/fund-discovery/reports` 历史、多选 `DELETE /api/fund-discovery/reports/{id}` 批量删、`GET .../outcomes` 复盘、`GET .../chat` 历史 + `POST .../chat/sync` 非流式追问、`md-view` 渲染追加（复用 `chat-panel`）
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

- [x] 9. 阶段七·收尾：简报首页 / 风控盯盘 / 历史 / 账号设置
  - [x] 9.1 在 `derive.js` 实现组合 KPI 汇总纯函数
    - 总资产=Σ`holding_amount`、当日收益=Σ`daily_profit`（浮点容差），空列表归零不异常
    - _Requirements: 17.2_

  - [x] 9.2 为组合 KPI 汇总编写属性测试
    - **Property 17: 组合 KPI 汇总**
    - **Validates: Requirements 17.2**

  - [x] 9.3 实现 Briefing_Page 简报首页
    - `pages/briefing`：登录后默认落地、组合 KPI 摘要、板块脉搏摘要、最新日报决策卡摘要、内联非流式追问（复用 `chat-panel`/BC1）
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_

  - [x] 9.4 在 `derive.js` 实现投资预设联动逻辑
    - `conservative_hold`/`aggressive_swing` 切换设定对应风控字段，`investment_preset` 同步
    - _Requirements: 18.3_

  - [x] 9.5 为投资预设联动编写属性测试
    - **Property 18: 投资预设联动**
    - **Validates: Requirements 18.3**

  - [x] 9.6 实现风控画像与日报角色设定页
    - `pages/risk`：`GET /api/investor-profile` 读取、设置浮亏线/集中度/期望投入/偏定投/拒绝追高、预设切换联动、`PUT /api/investor-profile` 保存、`GET|PUT /api/analysis-prompt` 角色设定
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

  - [x] 9.7 实现波段盯盘提醒视图
    - `POST /api/swing-alerts/evaluate` 评估、`GET /api/swing-alerts/today` 当日提醒、展示类型/标题/内容/优先级、新增提醒标记
    - _Requirements: 19.1, 19.2, 19.3, 19.4_

  - [x] 9.8 实现 History_Page 历史日报页
    - `pages/history`：`GET /api/reports` 列表、查看详情、多选 `DELETE /api/reports/{id}` 批量删除
    - _Requirements: 20.1, 20.2, 20.3_

  - [x] 9.9 实现 Settings_Page 账号设置页
    - `pages/settings`：`GET /api/auth/me` 展示账号与微信绑定、`POST /api/auth/link-email` 关联邮箱、成功用新令牌更新本地登录态、失败展示后端文案保留输入
    - _Requirements: 21.1, 21.2, 21.3, 21.4_

- [x] 10. 最终检查点 - 确保全部测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP；其余子任务必须实现。
- 每个任务引用具体子需求编号以保证可追溯；属性测试任务显式引用 design.md 的属性编号。
- 检查点用于增量验证；属性测试覆盖纯逻辑层（`format.js`/`derive.js`/请求决策/后端聚合 helper），单测与集成测试覆盖 UI 编排、`wx.*` 宿主交互、上传、轮询等不可 PBT 的部分。
- 微信开发者工具的真机/模拟器交互（TabBar、chooseMedia、uploadFile 域名、ec-canvas 渲染、towxml）需手动走查，不在自动化任务范围内。
- 后端改动仅 BC1 新增端点 + 聚合 helper（对 Web SSE 零影响，由回归测试保护）；BC2 不改后端代码，仅以测试验证 multipart 透传。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.4", "1.9", "1.11", "1.16", "2.1"] },
    { "id": 1, "tasks": ["1.3", "1.5", "1.6", "1.7", "1.8", "1.10", "1.12", "1.13", "6.1"] },
    { "id": 2, "tasks": ["1.14", "1.15", "6.7", "6.2", "6.3", "8.1"] },
    { "id": 3, "tasks": ["2.2", "6.4", "8.2"] },
    { "id": 4, "tasks": ["4.1", "2.3", "2.5"] },
    { "id": 5, "tasks": ["5.2", "2.4", "2.6", "4.4", "4.5"] },
    { "id": 6, "tasks": ["6.5", "5.1", "5.4", "5.5", "2.8", "6.9"] },
    { "id": 7, "tasks": ["9.1", "6.8", "8.3", "2.7", "4.2", "4.3", "4.6", "5.3", "5.6", "6.6"] },
    { "id": 8, "tasks": ["9.4", "8.4", "6.10", "9.3", "9.8", "9.9", "9.2", "9.7"] },
    { "id": 9, "tasks": ["9.5", "9.6"] }
  ]
}
```
