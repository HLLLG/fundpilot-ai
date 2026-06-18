# 实现计划：美股概览（市场 Tab · 美股子 Tab）

## 概述

按「先验证数据源、再后端聚合、再前端集成、最后文档」的顺序增量实现。首个任务是**数据源可行性 spike（GATE）**，它是本特性最大风险点并决定后续是否需要切换备选源或将数据项标注为 `unavailable`（禁止回退收盘价）。后续每一步都建立在前一步之上，最终在 `MarketTab` 中接线为可见的「美股」子 Tab。

设计含「Correctness Properties」章节（Property 1–8），因此为对应实现安排属性测试子任务（标注属性号与校验需求号）。带 `*` 的子任务为测试类可选任务。

## 任务

- [ ] 1. 【GATE】数据源可行性 spike（最高风险，门禁后续全部任务）
  - [ ] 1.1 编写并运行 `apps/api/scripts/diagnose_us_market.py`，确认真实期货与外汇源在本环境可达
    - 仿 `diagnose_sector_quotes.py`，经 `akshare_subprocess.py` 子进程 + 清代理 + JSON stdout 约定分别实跑：`futures_global_em()`（美股指数期货：纳指/标普/道指期货）、`fx_quote_baidu(symbol="美元")` 与备选 `currency_boc_sina(symbol="美元")`（USD/CNY）
    - 对每个源断言：返回 DataFrame 非空、含目标品种行、数值列可转 `float`
    - 将每个源的真实返回落盘为离线 fixture（JSON）到 `apps/api/tests/fixtures/`（供后续 pytest stub 复用）
    - 在脚本输出与本任务备注中记录**可行性结论 / 降级矩阵**：任一主选源不可用即标注备选或对应数据项默认 `unavailable`，**严禁回退到指数收盘价或占位常量**
    - 仅当本任务给出明确可行性结论后，方可继续后续任务
    - _Requirements: 8.1, 8.2, 8.3, 1.3_

- [ ] 2. 后端：美股交易时段检测（含夏令时）
  - [x] 2.1 实现 `apps/api/app/services/us_market_session.py`
    - `detect_us_session(when=None)`：以 `ZoneInfo("America/New_York")` 自动处理 DST，按 04:00–09:30→`pre_market`、09:30–16:00→`regular`、16:00–20:00→`after_hours`、其余→`closed` 判定，并返回 `session_kind` / `session_label`（盘前交易中/盘中/盘后/休市）/ `et_date`
    - `_is_us_trading_day`：MVP 仅排除周末（按 ET 墙钟日期），节假日简化在代码注释中显式登记为已知限制
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ] 2.2 为时段划分编写属性测试（`apps/api/tests/test_us_market_session.py`）
    - **Property 1：时段划分完备且互斥**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

  - [ ] 2.3 为夏令时墙钟一致性编写属性测试
    - **Property 2：夏令时墙钟一致性**（相同 ET 墙钟时间、跨 DST/标准时返回相同 `session_kind`）
    - **Validates: Requirements 3.1**

  - [ ] 2.4 为非交易日恒为休市编写属性测试
    - **Property 3：非交易日（周末）恒为 `closed`**
    - **Validates: Requirements 3.5**

  - [ ] 2.5 为时段边界与 DST 切换日编写单元测试
    - 边界：09:29:59→pre_market、09:30:00→regular、15:59:59→regular、16:00:00→after_hours、19:59:59→after_hours、20:00:00→closed；「春进」3 月第二个周日、「秋退」11 月第一个周日
    - _Requirements: 9.1_

- [ ] 3. 后端：数据源 client（AkShare 子进程约定）
  - [x] 3.1 实现 `apps/api/app/services/us_futures_client.py`
    - 复用 `akshare_subprocess.py` 写法（`subprocess.run([sys.executable, "-c", script], timeout=60)`、子进程内清代理、`print(json.dumps(...))`、异常返回 `None`）
    - 调 `futures_global_em()`，按关键字匹配映射 `NASDAQ_FUT`/`SP500_FUT`/`DOW_FUT`，返回含 `symbol`/`display_name`/`last_price`/`change_percent`/`quote_time` 的列表；禁止使用指数/收盘接口作为数值来源
    - _Requirements: 1.1, 1.3_

  - [x] 3.2 实现 `apps/api/app/services/us_forex_client.py`
    - 同上子进程约定；主选 `fx_quote_baidu(symbol="美元")`，备选 `currency_boc_sina(symbol="美元")`（取最新一行，标注时效偏差），返回 `last_price`/`change_percent`/`quote_time`；禁止填占位常量
    - _Requirements: 1.2_

- [ ] 4. 后端：QDII 种子表
  - [ ] 4.1 实现 `apps/api/app/services/us_qdii_seeds.py`
    - 维护对标竞品的 QDII 种子清单，每条含 `fund_code`/`fund_name`/`tracking_target`/`tracking_symbol`（映射到 §2.1 期货 symbol）/`tracking_factor`（默认 1.0）/`estimate_basis`，覆盖纳指、标普500、道指及全球科技等方向
    - _Requirements: 2.1, 8.3_

- [ ] 5. 后端：Pydantic 模型
  - [ ] 5.1 在 `apps/api/app/models.py` 新增模型
    - `DataSourceStatus` / `UsSessionKind` 字面量；`UsFuturesQuote` / `UsdCnyQuote` / `QdiiPremarketItem` / `UsMarketSnapshot`
    - 数值字段（`last_price`/`change_percent`/`reference_change_percent`）允许为 `None`，注释标明 `unavailable` 时必须为 `None`
    - _Requirements: 4.1, 4.6, 1.1, 1.2, 2.1_

- [ ] 6. 后端：`us_market_service` 快照聚合 + 时段缓存 + 降级
  - [ ] 6.1 实现 `apps/api/app/services/us_market_service.py`
    - `get_us_market_snapshot(force_refresh=False)`：`detect_us_session` → 计算 TTL bucket 与 `cache_key=market:us_overview:v1:{bucket}:{et_date}` → 复用 `sector_quote_cache` 的 `get_spot_snapshot/any_age/save_spot_snapshot`
    - 时段感知 TTL：`pre_market`/`regular` ≤60s；`after_hours`/`closed` 用更长 TTL（1800s）
    - 并行拉取期货 + 汇率（ThreadPoolExecutor，预算 ~10s）；按「本次成功→ok / 失败有缓存→stale 沿用最后真实值 / 失败无缓存→unavailable 置 None」降级
    - 用期货盘前涨跌按 `round(c × k, 2)` 估算 QDII `reference_change_percent`，跟踪期货不可用/无映射置 `None`；QDII 全不可用且无缓存 → `qdii_status=unavailable` 且 `qdii=[]`
    - 组装 `UsMarketSnapshot`（含各 `*_status`/`available`/`from_cache`/`stale`/`updated_at`/`message`）；`available` 才写缓存；`force_refresh=True` 绕过缓存重聚合
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 1.5, 2.2, 2.5, 7.1, 7.2, 7.5_

  - [ ] 6.2 为时段感知 TTL 编写属性测试（`apps/api/tests/test_us_market_service.py`）
    - **Property 4：TTL 单调性** `ttl(pre_market)==ttl(regular)≤60s<ttl(after_hours)≤ttl(closed)`
    - **Validates: Requirements 4.3, 4.4**

  - [ ] 6.3 为「禁止编造数值」核心不变量编写属性测试
    - **Property 5：禁止编造数值**（ok 来自本次采集 / stale 等于最后真实缓存值 / unavailable 为 None；任何情形不等于收盘价或占位常量推导值）
    - **Validates: Requirements 1.5, 2.5, 7.5**

  - [ ] 6.4 为「采集失败且有历史→陈旧回退」编写属性测试
    - **Property 6：陈旧回退** 失败但有历史真实值时 `status=stale` 且数值等于最后一次真实采集值
    - **Validates: Requirements 7.1**

  - [ ] 6.5 为「采集失败且无历史→不可用」编写属性测试
    - **Property 7：不可用** 失败且无缓存时 `status=unavailable` 且数值省略（QDII 返回空列表）
    - **Validates: Requirements 1.5, 2.5, 7.2**

  - [ ] 6.6 为 QDII 参考涨跌估算编写属性测试
    - **Property 8：QDII 估算** `reference_change_percent==round(c×k,2)` 且 `estimate_basis` 非空；跟踪期货不可用/无映射时为 `None`
    - **Validates: Requirements 2.2, 2.3**

  - [ ] 6.7 为 snapshot 结构与强制刷新编写单元测试
    - 各源成功/stale/unavailable 的 snapshot 结构与 `*_status`；`force_refresh=True` 重聚合；`updated_at` 等字段完整
    - _Requirements: 9.2, 9.3, 4.5, 4.1, 4.6_

- [ ] 7. 后端：API 端点
  - [ ] 7.1 在 `apps/api/app/main.py` 新增 `GET /api/market/us-overview` 并增补 `conftest.py` 网络 stub
    - 端点接受 `force_refresh` 参数，调用 `get_us_market_snapshot`，返回 `model_dump(mode="json")`，任何数据源失败均返回 200 经 `*_status`/`available`/`stale`/`message` 表达；无需 JWT
    - 在 `tests/conftest.py` 的 `_stub_market_data_fetches` 增补：monkeypatch `us_futures_client.fetch_us_index_futures`、`us_forex_client.fetch_usd_cny`（返回 fixture/None），并为 `app.main.get_us_market_snapshot` 提供确定性 stub
    - _Requirements: 4.2, 4.5, 7.1, 7.2, 7.4_

  - [ ] 7.2 在 `apps/api/tests/test_api.py` 增补 smoke 测试
    - `GET /api/market/us-overview` 返回 200 + 关键字段；覆盖 `force_refresh=true` 路径
    - _Requirements: 9.2_

- [ ] 8. 检查点 —— 确保后端测试全部通过
  - 运行 `pytest` 确保时段/降级/TTL/估算/smoke 全绿，如有疑问询问用户。

- [ ] 9. 前端：API 类型与请求
  - [ ] 9.1 在 `apps/web/src/lib/api.ts` 新增类型与 `fetchUsMarketOverview`
    - `UsDataSourceStatus`/`UsSessionKind`/`UsFuturesQuote`/`UsdCnyQuote`/`QdiiPremarketItem`/`UsMarketSnapshot` 类型；`fetchUsMarketOverview(forceRefresh?)` 请求 `GET /api/market/us-overview`
    - _Requirements: 1.4, 2.4_

- [ ] 10. 前端：辅助逻辑
  - [ ] 10.1 实现 `apps/web/src/lib/usMarketOverview.ts`
    - `usRefreshIntervalMs(kind)`（pre_market/regular→45s；其余→300s）、`US_SESSION_LABEL` 映射、`acceptUsMarketFresh`（仅 `available` 时替换，保留 stale-while-revalidate）、`MarketSubTab` 扩展 `"us"`
    - _Requirements: 5.1, 5.2, 3.6, 6.1_

  - [ ] 10.2 为辅助逻辑编写单元测试
    - `usRefreshIntervalMs`：pre_market/regular→短间隔；after_hours/closed→长间隔；`acceptUsMarketFresh` 行为
    - _Requirements: 5.1, 5.2_

- [ ] 11. 前端：美股概览组件
  - [ ] 11.1 实现 `apps/web/src/components/UsMarketOverview.tsx`
    - 渲染顺序：时段标签 → 4 张指标卡（3 期货 + USD/CNY，复用 `SectorPerformanceCard` 风格）→ QDII 列表（名称/跟踪标的/盘前参考涨跌 + 免责文案）→ 更新时间
    - 三态：`ok` 展示数值；`stale` 展示数值 + 「上次 {quote_time}」角标；`unavailable` 展示「暂不可用」且不渲染数值；无缓存 loading 时展示加载指示
    - _Requirements: 1.4, 2.3, 2.4, 6.3, 6.4, 6.5, 7.3, 7.4_

  - [ ] 11.2 为组件编写渲染测试
    - 断言 `loading`（无缓存）/`ok`/`stale`/`unavailable` 四态渲染、QDII 免责文案存在、时段标签按 `session_kind` 正确
    - _Requirements: 9.4_

- [ ] 12. 前端：接入 MarketTab
  - [ ] 12.1 改动 `apps/web/src/components/MarketTab.tsx` 新增「美股」第三子 Tab 并接线时段感知刷新
    - segment 增加「美股」与「全市场 | 主题板块」并列，刷新后记住上次子 Tab；`useCachedFetch`（`cacheKey=market-us-overview`、`storage="session"`、`staleTimeMs=usRefreshIntervalMs(...)`、`enabled: subTab==="us"`、`keepPreviousUnless: acceptUsMarketFresh`）
    - `setInterval(refresh, usRefreshIntervalMs(data?.session_kind))`，依赖 `[subTab, data?.session_kind]`，`subTab!=="us"` 或 `document.hidden` 时清除定时器
    - _Requirements: 6.1, 6.2, 6.3, 5.1, 5.2, 5.3, 5.4_

- [ ] 13. 前端：验证
  - [ ] 13.1 运行前端校验
    - `npm run lint`、`tsc --noEmit`、`npm run build` 通过
    - _Requirements: 9.4_

- [ ] 14. 检查点 —— 确保前后端测试与构建全部通过
  - 确保 `pytest` 全绿、`web` lint/typecheck/build 通过，如有疑问询问用户。

- [ ] 15. 文档同步
  - [ ] 15.1 更新 `docs/PROJECT_CONTEXT.md`
    - 在能力清单新增「美股概览」，在 API 表新增 `GET /api/market/us-overview`
    - _Requirements: 8（验收标准 8）_

## 备注

- 带 `*` 的子任务为可选测试任务，可为更快 MVP 跳过；核心实现任务不可跳过。
- 任务 1.1 为 **GATE**：它验证真实期货/外汇数据源可行性并产出离线 fixture，是后续全部任务的前置；未通过则按降级矩阵将对应数据项标注 `unavailable`，**绝不回退收盘价**。

### 任务 1.1 GATE 实跑结论（akshare==1.18.64，本环境实测）

**可行性结论：`FEASIBLE`** —— 期货与 USD/CNY 真实数据源在本环境均可达，可继续后续任务。

**关键修正（影响任务 3.1）：** 设计文档拟用的 `futures_global_em()` 在 akshare 1.18.64 **不存在**（`hasattr(ak,"futures_global_em") is False`）。本环境真实期货实时源为 **`futures_global_spot_em()`**，美股指数期货以 CME E-mini 命名：`小型纳指当月连续` / `小型标普当月连续` / `小型道指当月连续`（按「当月连续」取主力合约）。`us_futures_client.py` 须改用此接口与命名匹配，**不得**使用指数/收盘接口。

**降级矩阵：**

| 数据项 | 主选 | 主选结果 | 备选 | 最终决策 / 状态 |
|--------|------|----------|------|------------------|
| 美股指数期货 | `futures_global_spot_em()`（小型纳指/标普/道指 当月连续） | ✅ 620 行，三品种齐全，`最新价`/`涨跌幅` 均可转 float | — | **ok**：使用真实期货 |
| USD/CNY 外汇 | `fx_quote_baidu(symbol="美元")` | ❌ 上游 403（`ResultCode 403`，本环境不可达） | `currency_boc_sina(symbol="美元")` | **ok（备选）**：改用中行牌价（真实汇率，**日频**，须标注时效偏差） |

**硬约束：** 任一主选源不可用即标注备选或将对应数据项默认 `unavailable`；**严禁回退指数收盘价或占位常量**。

**注意事项（供后续任务）：**
- `us_forex_client.py` 主选 `fx_quote_baidu` 在本环境返回 403，应**优先实现 `currency_boc_sina` 备选路径**（取最新一行，列：`日期`/`中行汇买价`/`中行钞买价`/`中行钞卖价/汇卖价`/`央行中间价`/`中行折算价`），数值单位为「分」（如 `689.51` → `6.8951` CNY/USD），并标注 `status` 为日频/陈旧时效偏差；保留 `fx_quote_baidu` 主选尝试以便其他环境可用。
- `currency_boc_sina` 为历史日频序列，`quote_time` 取最新一行日期；`change_percent` 可由相邻两行折算价计算。

**离线 fixture（已落盘，供 pytest stub 复用）：**
- `apps/api/tests/fixtures/us_futures_global_spot_em.json`（620 行 futures_global_spot_em 真实返回）
- `apps/api/tests/fixtures/us_currency_boc_sina.json`（180 行中行牌价真实返回）
- `fx_quote_baidu` 因 403 无真实返回，未落盘 fixture；后续测试该源不可达分支用 `None` stub 模拟。

**复跑命令：** `python apps/api/scripts/diagnose_us_market.py --pretty`（在 `apps/api` 目录下，使用 venv 解释器）。
- 每个任务标注所校验的需求号；含 Correctness Properties 的实现额外标注属性号（Property 1–8）。
- 属性测试 ≥100 次迭代，标注 `Feature: us-market-overview, Property N`；若仓库未引入 `hypothesis`，以参数化 + 多随机样本近似并在实现时标注。
- 检查点用于增量验证，确保每阶段测试通过后再推进。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "3.2", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "6.1"] },
    { "id": 3, "tasks": ["2.3", "6.2", "7.1", "9.1"] },
    { "id": 4, "tasks": ["2.4", "6.3", "7.2", "10.1"] },
    { "id": 5, "tasks": ["2.5", "6.4", "10.2", "11.1"] },
    { "id": 6, "tasks": ["6.5", "11.2", "12.1"] },
    { "id": 7, "tasks": ["6.6", "13.1"] },
    { "id": 8, "tasks": ["6.7", "15.1"] }
  ]
}
```
