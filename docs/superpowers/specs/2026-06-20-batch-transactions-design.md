# 批量加减仓 + 交易记录 + 买卖点标记 — 设计（Phase 2）

**日期：** 2026-06-20
**状态：** 已与用户确认设计决策，待 review
**范围：** 多阶段扩展第二阶段。三件事：(A) 移除养基宝导入通道，只保留支付宝；(B) 支付宝「交易记录」截图 → 批量加减仓（交易事件流 + 份额调整）；(C) 业绩走势图买卖点标记。

---

## 1. 背景与目标

用户从支付宝「交易分析 / 交易记录」截图上传，系统 OCR 解析出加仓/减仓清单（方向、基金名、金额、成交时间），确认后：

1. **调整持仓**：按确认日净值把金额换算成份额增减，叠加到该基金持仓（保证后续当日收益计算正确）。
2. **同步买卖点**：在该基金「业绩走势」图上对应确认日打红/绿点。

同时移除养基宝总览/详情两个导入通道及其底层解析代码，导入入口只保留支付宝（+ 手动输入）。

---

## 2. 已确认的产品决策

| # | 决策 |
|---|------|
| 份额模型 | 交易金额按**确认日单位净值**换算成**份额增减**，调整 `holding_shares`；金额/当日收益由现有 `份额 × 净值` 管线自动重算 |
| 确认日规则 | 由**成交时间**（非上传时间）+ 15:00 切点推导：成交 `<15:00` 且当天为交易日 → 确认净值日 = 当天；`≥15:00` 或非交易日 → 顺延至下一交易日 |
| 协调模型 | **基线 + 事件流**：支付宝总览 OCR = 份额基线（带基线日）；持仓有效份额 = 基线份额 + 基线日**之后**所有已确认交易的份额增减；早于基线日的交易视为已含在基线内，不重复叠加 |
| 未确认交易 | 方案 A：确认日净值未发布（含「交易进行中」）→ 标 `pending`，**不调整份额**；持仓刷新/再次打开时自动重试拉净值，可得即转 `confirmed` 并补算份额 |
| 养基宝导入 | **彻底删除**养基宝总览/详情导入入口 + 底层解析 + fixtures + 测试；保证支付宝流程正常，允许重构 |
| 基金匹配 | 交易记录只有名称 → 确认清单内可手动搜索选码/编辑；未匹配且未手动指定 → 确认时跳过该条 |
| 非持仓基金 | 买入未持有基金 → 自动建仓；减仓导致份额 ≤ 0 → 标记已清仓并移出持有列表（保留交易历史与买卖点） |
| 清单交互 | 每条可删除(×) + 可编辑方向/金额/时间/基金；「继续上传」追加多张合并；「完成(N)」一次性提交 |
| 买卖点展示 | 按确认日定位；加仓实心红点、减仓实心绿点、pending 空心灰点；同日多笔聚合一个点，点按展开当天明细 |

> **颜色约定**：沿用 App 既有「红涨绿跌 / 加仓红、减仓绿」语义——**加仓=红、减仓=绿**（与 image3 的加仓红字、减仓蓝字不同；image3 蓝色仅为养基宝的减仓标识色，本项目统一用红/绿圆点）。

---

## 3. Part A — 移除养基宝导入

### 3.1 删除清单

**前端**
- `AddHoldingModal.tsx`：移除 `yangjibao_overview`、`yangjibao_detail` 两个 `UploadChannel` 及其文案/引导；只保留 `alipay` + 「手动输入」。（批量加减仓入口见 Part E，不混进本弹窗的 Tab。）
- `AlipayOcrConfirmModal.tsx`：移除 `isDetail` / `yangjibao_detail` 分支。
- `YangjibaoFundDetail.tsx`：移除"上传养基宝详情截图"按钮、`onUploadDetailScreenshot`、`isDetailOcrUploading` 等 props 与逻辑。（组件本身是基金详情页，**保留**。）
- `Dashboard.tsx`：移除 `pendingDetailProfile`、`pendingOcrSource === "yangjibao_detail"` 等分支；OCR 入口文案改为仅支付宝。
- `api.ts`：`applyHoldings` 移除 `detail_profiles` 入参。
- `workflowBlockers.ts`：文案「请先上传养基宝总览截图…」→ 改为支付宝。

**后端**
- `ocr_pipeline.py`：删除 `_run_yangjibao_detail_pipeline`、`yangjibao_detail` 分支；`_ocr_amount_semantics` 移除养基宝分支。
- `ocr_parser.py`：`detect_ocr_source` 不再返回 `yangjibao_detail` / `yangjibao_overview`；删除 `is_yangjibao_detail_page` 及养基宝总览的行解析主体；**重构** `parse_holdings_from_text` 为仅支付宝路径（`is_alipay_holdings_page` → `parse_alipay_holdings_page`），其余返回空并由调用方处理 `unknown`。
- `fund_profile.py`：删除 `merge_detail_profile`、`parse_profile_from_text`（养基宝详情解析）；`save_profile` 不再依赖 `merge_detail_profile`（新建直接落库 + 保留 Phase 1 的 `first_seen_date` 锚点逻辑）。
- `main.py`：`/api/portfolio/apply-holdings` 移除 `detail_profiles` 处理；OCR 路由移除养基宝详情 preview 分支。
- `fund_primary_sector_service.py`：移除"从养基宝详情 OCR 沉淀板块"的入口；保留种子 / 季报重仓 / 支付宝按 code 查表。

**测试 / fixtures**
- 删除：`tests/fixtures/yangjibao_*`、`tests/test_overview_pipeline.py` 中养基宝专属用例、`test_fund_profile.py` 的 `DETAIL_TEXT`/`YANGJIBAO_BOTTOM_LAYOUT`/`test_parse_yangjibao_detail_profile_text`、`test_ocr_parser.py`/`test_portfolio.py` 的养基宝总览用例。
- 改造：依赖养基宝 fixture 的用例改用支付宝 fixture（`tests/fixtures/alipay_holdings_*`）。

### 3.2 重构后支付宝主流程（必须保持正常）

```
POST /api/ocr (preview)  → detect_ocr_source → alipay_holdings → parse_alipay_holdings_page
  → enrich_holdings_from_profiles（查码、档案）→ AlipayOcrConfirmModal 预览
POST /api/portfolio/apply-holdings { holdings }
  → save_profiles + bootstrap_holding_baselines(force_reset_shares, 记基线日)
  → process_overview_holdings（板块刷新 + 份额×净值 + 官方净值）+ save_daily_snapshot
```

`process_overview_holdings`、`bootstrap_holding_baselines`、`sync_holding_amounts_from_shares`、`alipay_holdings_parser.py` **全部保留**。

### 3.3 副作用（已与用户确认接受）

- 关联板块映射来源减少为：全局种子 + 季报重仓投票 + 支付宝按 code 查表。
- 份额/成本只能由支付宝总览金额反推 + 交易事件流维护。
- 持有天数锚点来自 Phase 1 `first_seen_date`（或手动设 / 交易最早买入推导，见 Part D）。

---

## 4. Part B — 交易数据模型

### 4.1 新表 `fund_transactions`

| 列 | 类型 | 含义 |
|----|------|------|
| `id` | TEXT PK | UUID |
| `userId` | TEXT | 用户隔离 |
| `fund_code` | TEXT | 6 位代码（可空，未匹配时为 null，确认时必须有值） |
| `fund_name` | TEXT | OCR 原始名称 |
| `direction` | TEXT | `buy`（加仓）/ `sell`（减仓） |
| `amount_yuan` | REAL | 成交金额（元，支付宝原值） |
| `trade_time` | TEXT | 成交时间 ISO（含时分秒） |
| `confirm_date` | TEXT | 推导出的确认净值日（ISO date） |
| `status` | TEXT | `pending` / `confirmed` / `superseded` / `skipped` |
| `shares_delta` | REAL | 确认后填：`±amount / nav_on_confirm`（买正卖负） |
| `nav_on_confirm` | REAL | 确认日单位净值 |
| `dedup_key` | TEXT | `fund_code|direction|trade_time|amount_yuan` 哈希，去重 |
| `created_at` | TEXT | 写入时间 |

MySQL 同步建表（`mysql_bootstrap.py`）。SQLite 走 `database.py` `CREATE TABLE IF NOT EXISTS` + 轻量迁移。

- `status=superseded`：确认日 ≤ 持仓份额基线日 → 视为已含在基线内，不叠加（但仍可画买卖点）。
- `dedup_key` 唯一约束（`userId + dedup_key`）：重复上传同一笔交易自动忽略。

### 4.2 `FundProfile` 基线字段

新增：

| 字段 | 含义 |
|------|------|
| `shares_baseline_date` | 最近一次支付宝总览 OCR 建立份额基线的日期（用于判断交易是否"晚于基线"） |

（仍在 JSON payload，无需迁移。）`bootstrap_holding_baselines` 写入 `holding_shares` 时一并写 `shares_baseline_date = effective_trade_date`。

---

## 5. Part C — 支付宝交易记录 OCR 解析

新增 `alipay_transactions_parser.py`：

- `is_alipay_transaction_page(lines)`：识别页面标志（「交易分析」「全部交易汇总」「买入/卖出」「定投/发车」「成交时间」等）。
- `parse_alipay_transactions(text) -> list[ParsedTransaction]`：逐条解析交错的 `买入/卖出` + 基金名(可跨行) + `金额元` + `YYYY-MM-DD HH:MM:SS` + 可选「交易进行中」。
  - `买入 → direction=buy（加仓）`，`卖出 → direction=sell（减仓）`。
  - 「交易进行中」→ 标记 `in_progress=True`（后续 → `pending`）。
- 确认日推导 `resolve_confirm_date(trade_time)`（放 `trading_session.py` 或新 helper）：
  - 解析 `trade_time` → 上海时区 datetime。
  - 当天为交易日且 `time < 15:00` → `confirm_date = 当天`。
  - 否则（`≥15:00` 或非交易日）→ `confirm_date = trade_time 当天之后的下一个交易日`。

`ParsedTransaction`（Pydantic）：`direction`、`fund_name`、`fund_code?`、`amount_yuan`、`trade_time`、`confirm_date`、`in_progress`。

`detect_ocr_source` 新增返回 `alipay_transactions`。

---

## 6. Part D — 基线 + 事件流份额重算（核心正确性）

新增 `transaction_ledger.py`：

### 6.1 确认与份额换算

`confirm_pending_transactions(user_transactions)`：对每条 `pending` 交易：
- 取 `confirm_date` 的官方单位净值（扩展 `fund_nav_service.get_unit_nav_on_date(code, date)`，从已拉取的净值 DataFrame 取该日 `单位净值`）。
- 净值可得 → `shares_delta = (+/−) amount_yuan / nav`，`nav_on_confirm = nav`，`status = confirmed`（若 `confirm_date ≤ profile.shares_baseline_date` 则 `superseded`）。
- 净值不可得（未发布/非交易日数据缺失）→ 保持 `pending`。

### 6.2 有效份额

`compute_effective_shares(fund_code)`：
```
baseline = profile.holding_shares (基线份额)
baseline_date = profile.shares_baseline_date
effective = baseline + Σ tx.shares_delta
            for tx in confirmed transactions of fund_code
            where tx.confirm_date > baseline_date
```
- 持仓恢复 / 刷新（`portfolio_holdings_service` / `refresh-sector-quotes`）时调用，得到 `effective_shares` 后由 `sync_holding_amounts_from_shares` 用 `effective_shares × 最新净值` 算金额与当日收益。
- `effective_shares ≤ 0` → 该基金标记已清仓，移出 `displayableHoldings`（交易/买卖点保留）。
- 买入未持有基金 → 建立简略 profile（建仓），`shares_baseline_date = confirm_date`，`holding_shares = shares_delta`。

### 6.3 自动补确认

`process_overview_holdings` 与持仓恢复路径中调用 `confirm_pending_transactions`，使 pending 交易在净值发布后自动转 confirmed（方案 A）。

### 6.4 与 Phase 1 持有天数

`first_seen_date` 优先级之上：若该基金有 `buy` 交易，最早一笔 `buy` 的 `confirm_date` 可作为更准确的建仓日（`_resolve_holding_days` 增加一档：用户设定 > 最早买入交易确认日 > first_seen_date > 旧 OCR > 快照）。

---

## 7. Part E — 入口与确认清单 UI

### 7.1 入口（image1）

- `YangjibaoHoldingsBoard` 顶部「新增持有」按钮旁新增「**批量加减仓**」按钮。
- 点击打开 `BatchTransactionModal`（仿 image1）：标题「支付宝-批量加减仓」、交易记录示意图、文案「上传"交易记录"截图即可加减仓、同步买卖点」、按钮「去相册选择」。

### 7.2 确认清单（image3）

- 上传 → `POST /api/transactions/ocr?preview=true` → 返回 `ParsedTransaction[]`。
- `BatchTransactionConfirmModal`（仿 image3）：每条卡片显示方向标签（加仓红/减仓绿）、基金名、金额、成交时间、`×` 删除；未匹配代码的条目显示「选择基金」入口（复用 `GET /api/funds/search`）；可编辑方向/金额/时间。
- 「继续上传」追加解析合并；「完成(N)」→ `POST /api/transactions/apply`。

### 7.3 apply 流程

```
POST /api/transactions/apply { transactions: ParsedTransaction[] }
  → 去重(dedup_key) → 写入 fund_transactions(status: pending)
  → confirm_pending_transactions（可得净值即 confirmed/superseded）
  → 对受影响基金 compute_effective_shares → 更新 holding_shares / 建仓 / 清仓
  → process_overview_holdings 刷新金额与当日收益
  → 返回更新后的 holdings + 交易状态汇总
```

---

## 8. Part F — 买卖点标记

- `GET /api/funds/{code}/transactions` → 该基金交易列表（含 `confirm_date`、`direction`、`amount_yuan`、`status`）。
- `PerformanceReturnChart`（业绩走势图）接收 `markers` prop：把交易按 `confirm_date` 映射到净值序列对应点。
  - 加仓实心红点、减仓实心绿点、pending 空心灰点。
  - 同一 `confirm_date` 多笔聚合一个点，点按弹出当天明细（方向 + 金额 + 时间）。
  - 确认日落在当前区间（近1月/3月/…）外则不显示。

---

## 9. API 变更

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/transactions/ocr?preview=true` | 支付宝交易记录截图 → `ParsedTransaction[]`（不写库） |
| POST | `/api/transactions/apply` | 写入交易、确认、重算份额、刷新持仓 |
| GET | `/api/funds/{code}/transactions` | 单基金交易列表（买卖点） |
| ~~detail_profiles~~ | `/api/portfolio/apply-holdings` | **移除** `detail_profiles` 入参 |

---

## 10. 测试策略

- `test_alipay_transactions_parser.py`：用 image2 文本造 fixture，校验方向映射、金额、成交时间、跨行基金名、「交易进行中」识别。
- `test_confirm_date.py`：15:00 切点、非交易日顺延、周末顺延。
- `test_transaction_ledger.py`：份额换算、基线之后叠加、基线之前 superseded 不重复、effective_shares、清仓、建仓、pending 自动确认、去重。
- `test_api.py`：`/api/transactions/*` 集成；apply 后 holdings 份额/金额正确。
- 养基宝删除后全量 pytest 通过；新增支付宝交易解析 fixture。
- 前端：`marketThemeBoard` 式 helper 单测（确认日格式化、marker 聚合）；`npm run lint && typecheck && build`。

---

## 11. 影响面与非目标

- **非目标**：搜索 + 自选页面（Phase 3）；交易费率精确建模（用净额近似，不单独扣赎回费）；分红/拆分事件。
- **风险**：养基宝解析删除涉及面广 → 实现时先单独完成 Part A 并跑通支付宝全流程与全量测试，再做 B/C。
- **实现顺序建议**：A（删养基宝 + 支付宝回归）→ B（数据模型 + 解析 + 确认日）→ D（账本/份额/确认）→ E（入口/清单/apply）→ F（买卖点）。
