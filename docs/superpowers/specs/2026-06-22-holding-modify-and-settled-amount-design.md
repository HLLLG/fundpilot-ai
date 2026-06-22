# 持有天数修复 + 结算金额分离 + 修改持仓 — 设计

**日期：** 2026-06-22  
**状态：** 已与用户确认  
**确认项：** 同步加减仓用手动表单（A）；持有收益可手动改；结算金额分离按方案 A

---

## 1. 持有天数（Bug）

**根因：** `sync_profiles_from_holdings` 直接 `save_fund_profile`，绕过 `save_profile()` 的 `first_seen_date` 锚点。

**修复：**
- 新建/更新档案统一走 `FundProfileService.save_profile()`
- 删除持仓时清除 `first_seen_date`（保留其余档案）；再次加入时写新锚点
- 已在持有但无锚点的旧档案：读详情时惰性回填（最早日快照 → 今天）

**天数优先级不变：** `first_purchase_date` → `first_seen_date` → OCR aging → 快照

---

## 2. 结算金额分离（养基宝口径）

**新增字段：** `FundProfile.settled_holding_amount`（JSON payload，无迁移）

| 时段 | 持有金额展示 | 当日收益 | `amount_includes_today` |
|------|-------------|---------|-------------------------|
| 盘中（净值未公布） | `settled_holding_amount` | settled × 涨跌幅 / 100 | `false` |
| 官方净值公布后 | 滚入 `shares × 官方净值` 写入 settled | 官方 r 用 settled×r/100 或滚入后展示 | `false` |

**改动：**
- `sync_holding_amounts_from_shares`：盘中不抬金额；仅官方净值时更新 settled
- `_amount_includes_today_return`：以显式 `amount_includes_today` 为准，不再因「有份额」推断为 true
- OCR/bootstrap：写入 `settled_holding_amount` + `amount_includes_today=false`
- 总资产：`Σ(settled + daily_profit)`

---

## 3. 修改持仓 UI

**入口：** 基金详情页底部「修改持仓」

**弹层 `HoldingModifyModal`：**
- 基金名称、持有金额（可编辑）、持有收益（可编辑）、持有天数（只读，可点进日期选择器）
- **同步加仓** / **同步减仓** → `SingleFundTransactionModal`（份额、快捷比例、成交时间）

**API：**
- `PATCH /api/portfolio/holdings/{fund_code}/adjust` — 手动改结算金额/持有收益
- 单基交易复用 `POST /api/transactions/apply`（一条 `ParsedTransaction`）

---

## 4. 非目标

同步定投、同步转换；费率精确建模。
