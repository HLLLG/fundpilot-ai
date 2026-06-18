# Requirements Document

## Introduction

本特性在现有「市场」Tab 下新增「美股」子 Tab，对标小倍养鸡「美股基金涨跌助手」，为用户提供：(a) 顶部美股指数期货行情卡（纳指期货 / 标普期货 / 道指期货）与人民币汇率（USD/CNY）；(b) 一份 QDII「盘前参考涨跌」基金列表，基于美股/全球指数盘前涨跌为每只 QDII 基金给出参考涨跌幅估算；并展示「更新时间」时间戳与美股交易时段状态标签（如「盘前交易中」）。

数据口径要求行情卡使用**真实指数期货**（纳指 / 标普 / 道指期货）与 USD/CNY 外汇，**不得回退到指数收盘价**。刷新策略需感知美股交易时段（盘前 / 盘中 / 盘后 / 休市，含夏令时切换），盘前与盘中高频刷新，休市时延长缓存有效期。数据来源在本环境的可行性尚未验证，因此本文档将「数据源不可用时的优雅降级（展示陈旧/不可用状态、禁止编造数值）」与「设计阶段验证数据源可行性」作为强制需求纳入。

本特性需复用现有 Market Tab 子 Tab 结构（全市场 | 主题板块）、`TradingSessionBar` 约定、服务端 snapshot + 缓存模式、前端 `useCachedFetch` / `clientCache` session 缓存，并补充后端 pytest 与前端 lint/typecheck/build 测试覆盖。

## Glossary

- **US_Market_Service**：后端美股概览服务，负责聚合期货、外汇与 QDII 列表数据并对外暴露 API。
- **US_Futures_Quote**：单条美股指数期货行情，包含品种标识（纳指期货 / 标普期货 / 道指期货）、最新价、涨跌幅、数据时间戳。
- **USD_CNY_Quote**：USD/CNY 人民币汇率行情，包含最新价、涨跌幅、数据时间戳。
- **QDII_Premarket_Item**：QDII「盘前参考涨跌」列表的单条记录，包含基金代码、基金名称、跟踪标的、盘前参考涨跌幅、估算依据标识。
- **US_Session_Detector**：美股交易时段检测器，依据美东时区（含夏令时）将当前时刻判定为盘前、盘中、盘后或休市之一。
- **US_Session_Kind**：美股交易时段状态枚举，取值为 `pre_market`（盘前）、`regular`（盘中）、`after_hours`（盘后）、`closed`（休市）。
- **US_Market_Snapshot**：US_Market_Service 在某一时刻产出的聚合快照，包含期货卡、汇率、QDII 列表、US_Session_Kind、更新时间戳与各数据源可用状态。
- **US_Market_Snapshot_Cache**：US_Market_Service 的服务端快照缓存，按 US_Session_Kind 应用不同有效期（TTL）。
- **US_Market_SubTab**：前端「市场」Tab 下的「美股」子 Tab 视图。
- **Data_Source_Status**：单个数据来源（期货 / 外汇 / QDII）的可用状态，取值为 `ok`、`stale`（陈旧）、`unavailable`（不可用）之一。
- **Reference_Change_Percent**：QDII 基金的盘前参考涨跌幅，由其跟踪标的的指数盘前涨跌经估算方法计算得出的非承诺性预估值。

## Requirements

### Requirement 1：美股指数期货行情卡

**User Story:** 作为关注美股 QDII 的投资者，我希望在「美股」子 Tab 顶部看到纳指 / 标普 / 道指期货与人民币汇率的实时卡片，以便快速判断隔夜美股方向。

#### Acceptance Criteria

1. THE US_Market_Service SHALL 提供纳指期货、标普期货、道指期货三条 US_Futures_Quote，每条包含品种标识、最新价、涨跌幅与数据时间戳。
2. THE US_Market_Service SHALL 提供一条 USD_CNY_Quote，包含最新价、涨跌幅与数据时间戳。
3. THE US_Futures_Quote SHALL 取自真实指数期货行情数据源，而非美股指数收盘价。
4. WHEN US_Market_SubTab 渲染顶部行情区域，THE US_Market_SubTab SHALL 以指标卡形式展示三条 US_Futures_Quote 与一条 USD_CNY_Quote。
5. IF 某条 US_Futures_Quote 或 USD_CNY_Quote 的来源数据无法获取真实期货或外汇报价，THEN THE US_Market_Service SHALL 将该条目的 Data_Source_Status 标记为 `unavailable` 并省略数值字段，而不填入收盘价或其他替代数值。

### Requirement 2：QDII 盘前参考涨跌列表

**User Story:** 作为 QDII 基金持有者，我希望看到一份跟踪美股/全球市场的 QDII 基金盘前参考涨跌列表，以便预估自己持有基金的盘前走势。

#### Acceptance Criteria

1. THE US_Market_Service SHALL 提供一组 QDII_Premarket_Item，每条包含基金代码、基金名称、跟踪标的与 Reference_Change_Percent。
2. THE US_Market_Service SHALL 依据 QDII_Premarket_Item 跟踪标的对应的指数期货盘前涨跌幅，按定义的估算方法计算该条目的 Reference_Change_Percent。
3. THE QDII_Premarket_Item SHALL 携带估算依据标识，用于在界面标注 Reference_Change_Percent 为基于盘前指数估算的非承诺性预估值。
4. WHEN US_Market_SubTab 渲染 QDII 列表区域，THE US_Market_SubTab SHALL 展示每条 QDII_Premarket_Item 的基金名称、跟踪标的与 Reference_Change_Percent。
5. IF QDII 列表数据源不可用，THEN THE US_Market_Service SHALL 将 QDII 列表对应的 Data_Source_Status 标记为 `unavailable` 并返回空列表，而不填入编造的基金条目或涨跌数值。

### Requirement 3：美股交易时段检测（含夏令时）

**User Story:** 作为用户，我希望系统能识别当前美股处于盘前、盘中、盘后还是休市，以便我理解数据时效并看到正确的时段标签。

#### Acceptance Criteria

1. THE US_Session_Detector SHALL 依据美国东部时区（含夏令时自动切换）将当前时刻判定为唯一的一个 US_Session_Kind。
2. WHEN 当前美东时刻处于常规交易时段（09:30–16:00 ET），THE US_Session_Detector SHALL 返回 US_Session_Kind 为 `regular`。
3. WHEN 当前美东时刻处于常规交易时段开始前的盘前时段（04:00–09:30 ET），THE US_Session_Detector SHALL 返回 US_Session_Kind 为 `pre_market`。
4. WHEN 当前美东时刻处于常规交易时段结束后的盘后时段（16:00–20:00 ET），THE US_Session_Detector SHALL 返回 US_Session_Kind 为 `after_hours`。
5. WHILE 当前处于美股非交易日或上述时段之外的时间，THE US_Session_Detector SHALL 返回 US_Session_Kind 为 `closed`。
6. WHEN US_Market_SubTab 渲染时段标签，THE US_Market_SubTab SHALL 展示与当前 US_Session_Kind 对应的中文状态标签（盘前交易中 / 盘中 / 盘后 / 休市）。

### Requirement 4：服务端快照与时段感知缓存

**User Story:** 作为系统维护者，我希望美股数据在服务端聚合为快照并按时段缓存，以便控制外部数据源调用频率并保证响应稳定。

#### Acceptance Criteria

1. THE US_Market_Service SHALL 将期货卡、USD_CNY_Quote、QDII 列表、US_Session_Kind、更新时间戳与各 Data_Source_Status 聚合为单个 US_Market_Snapshot。
2. THE US_Market_Service SHALL 通过 `GET /api/market/*` 命名空间下的接口对外暴露 US_Market_Snapshot。
3. WHILE US_Session_Kind 为 `pre_market` 或 `regular`，THE US_Market_Snapshot_Cache SHALL 使用不超过 60 秒的有效期（TTL）。
4. WHILE US_Session_Kind 为 `after_hours` 或 `closed`，THE US_Market_Snapshot_Cache SHALL 使用更长的有效期（TTL），用于降低数据源调用频率。
5. WHEN 接口收到带强制刷新参数的请求，THE US_Market_Service SHALL 绕过 US_Market_Snapshot_Cache 重新聚合一次 US_Market_Snapshot。
6. THE US_Market_Snapshot SHALL 携带反映其数据采集时刻的更新时间戳字段。

### Requirement 5：智能刷新策略

**User Story:** 作为用户，我希望「美股」子 Tab 在盘前和盘中高频刷新、在休市时段降低刷新频率，以便兼顾数据新鲜度与资源消耗。

#### Acceptance Criteria

1. WHILE US_Session_Kind 为 `pre_market` 或 `regular`，THE US_Market_SubTab SHALL 以 30 至 60 秒的间隔自动刷新 US_Market_Snapshot。
2. WHILE US_Session_Kind 为 `after_hours` 或 `closed`，THE US_Market_SubTab SHALL 使用更长的刷新间隔。
3. WHILE US_Market_SubTab 未处于可见激活状态，THE US_Market_SubTab SHALL 暂停自动刷新。
4. THE US_Market_SubTab SHALL 复用现有 `useCachedFetch` 与 `clientCache` 的 session 缓存机制承载 US_Market_Snapshot 数据。

### Requirement 6：美股子 Tab 界面集成

**User Story:** 作为用户，我希望「美股」作为「市场」Tab 下与「全市场 | 主题板块」并列的子 Tab，以便在熟悉的位置访问美股概览。

#### Acceptance Criteria

1. THE US_Market_SubTab SHALL 作为「市场」Tab 下的子 Tab 与「全市场」「主题板块」并列展示。
2. THE US_Market_SubTab SHALL 复用现有 Market Tab 子 Tab 切换结构与 `TradingSessionBar` 展示约定。
3. WHEN 用户选择「美股」子 Tab，THE US_Market_SubTab SHALL 依次展示时段标签、期货与汇率指标卡、QDII 盘前参考列表与更新时间。
4. WHEN US_Market_Snapshot 正在加载且无可用缓存，THE US_Market_SubTab SHALL 展示加载状态指示。
5. THE US_Market_SubTab SHALL 在界面显著位置展示 US_Market_Snapshot 的更新时间。

### Requirement 7：数据源不可用时的优雅降级

**User Story:** 作为用户，我希望在某项美股数据暂时取不到时看到明确的陈旧或不可用提示，而不是被展示编造的数值，以便我据此判断数据可信度。

#### Acceptance Criteria

1. WHEN 某数据源本次采集失败但 US_Market_Snapshot_Cache 中存在该数据源的历史数据，THE US_Market_Service SHALL 返回历史数据并将其 Data_Source_Status 标记为 `stale`。
2. WHEN 某数据源本次采集失败且无任何历史数据可用，THE US_Market_Service SHALL 将该数据源的 Data_Source_Status 标记为 `unavailable`。
3. WHERE 某数据源的 Data_Source_Status 为 `stale`，THE US_Market_SubTab SHALL 展示数据陈旧提示并标注对应数据的采集时间。
4. WHERE 某数据源的 Data_Source_Status 为 `unavailable`，THE US_Market_SubTab SHALL 展示不可用提示而不渲染该数据源的数值。
5. THE US_Market_Service SHALL 在 Data_Source_Status 为 `stale` 或 `unavailable` 时省略对应数值字段或保留其最后一次真实采集值，而不填入由收盘价或占位常量推导的替代数值。

### Requirement 8：数据源可行性验证（设计阶段约束）

**User Story:** 作为系统维护者，我希望在设计阶段确认美股期货与外汇数据源在本环境可获取，以便避免上线后无真实数据可用。

#### Acceptance Criteria

1. THE 设计文档 SHALL 记录对美股指数期货与 USD/CNY 外汇数据源（含经由 AkShare subprocess 的获取方式）在本环境可行性的验证结论。
2. IF 验证表明真实指数期货或外汇数据源在本环境不可用，THEN THE 设计文档 SHALL 给出替代数据源方案或将该数据项标注为 `unavailable` 的处置方案，而不规定回退到收盘价。
3. THE 设计文档 SHALL 定义 QDII 列表的数据来源与 Reference_Change_Percent 的估算方法。

### Requirement 9：测试覆盖

**User Story:** 作为系统维护者，我希望新增功能具备后端与前端测试覆盖，以便回归时能及时发现问题。

#### Acceptance Criteria

1. THE 后端测试套件 SHALL 在 stub 外部网络的前提下覆盖 US_Session_Detector 在盘前、盘中、盘后、休市及夏令时切换边界下的 US_Session_Kind 判定。
2. THE 后端测试套件 SHALL 覆盖 US_Market_Service 在各数据源成功、`stale` 与 `unavailable` 情形下产出的 US_Market_Snapshot 结构与 Data_Source_Status。
3. THE 后端测试套件 SHALL 覆盖 US_Market_Snapshot_Cache 在不同 US_Session_Kind 下的有效期（TTL）选择行为。
4. THE 前端验证 SHALL 通过 lint、类型检查与构建，且覆盖 US_Market_SubTab 在加载、`stale` 与 `unavailable` 状态下的渲染。
