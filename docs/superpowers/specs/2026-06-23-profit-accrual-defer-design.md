# 当日买入延迟计收益（对齐支付宝截图）

**日期：** 2026-06-23  
**状态：** 已落地（2026-06-26 补强 bypass 修复）

## 问题

用户在交易日收盘前买入基金并上传支付宝「全部持有」截图后，好基灵立即用板块涨跌估算当日/持有收益；支付宝同一只基金在份额未确认前显示 `日收益=0`、`持有收益=0`。

## 竞品结论

- **支付宝**：截图四列中 `日收益/持有收益/持有收益率` 全为 0 表示份额待确认，当日不计盈亏。
- **养基宝**：新买入建议次日再录入、收益填 0；已有持仓走截图识别，盘中估算。
- **行业规则**：T 日 15:00 前买入 → T+1 确认份额 → 自确认日起计盈亏。

## 方案 A（已选）

当 OCR 同时满足：

1. `日收益 ≈ 0`（overview 版式存在 `yesterday_profit` 字段）
2. `持有收益 ≈ 0`
3. `持有收益率 ≈ 0%`

则整只基金当日 **deferred**（含当日加仓场景），与支付宝整行展示一致。

### 持久化

`FundProfile.profit_accrual_deferred_until` = 导入当日 `effective_trade_date`（含当日 defer）。

### 收益计算

- `apply_sector_daily_estimates`：deferred 时 `daily_profit=0`，`daily_return_percent_source=pending_accrual`；**板块列仍展示** `sector_return_percent`。
- `effective_trade_date > profit_accrual_deferred_until` 自动恢复板块估算。
- 重新上传截图且收益非零时清除 defer。

### API / 前端

- `serialize_holding_for_client` 增加 `profit_accrual_deferred: bool`。
- 持有列表「估算」列 title 提示「份额待确认，次交易日起计收益」。

## 落地补强（2026-06-26）

初版 `profit_accrual_defer` 已在 `apply_sector_daily_estimates` 生效，但三条路径在 **官方净值已公布** 时绕过 defer，导致当日新购仍出现日收益：

| 路径 | 文件 | 修复 |
|------|------|------|
| 板块 quote 写日收益 | `sector_quote_service.py` | 应用 `official_nav` 前检查 `is_profit_accrual_deferred` |
| 结算金额滚动 | `holding_amount_sync.py` | `shares × NAV` 滚 settled 前检查 defer |
| 估算优先级 | `holding_estimates.py` | defer 检查置于 `official_nav` 分支之前 |

**前端防御：** `holdingMetrics.ts` / `holdingDisplay.ts` 在 `profit_accrual_deferred` 时强制日收益为 0。

**单测：** `apps/api/tests/test_profit_accrual_defer.py`（4 项）、`apps/web/src/lib/holdingMetrics.test.ts`（1 项）。

## 二次补强（2026-06-26）

| 问题 | 修复 |
|------|------|
| `return_percent=0` 未触发 defer | `ocr_holding_return_percent` 用 `is not None` 判断，`0%` 视为有效 |
| defer 仍滚份额×净值 | `apply_defer_to_profile` / bootstrap 清空 `holding_shares`，锁定 OCR `holding_amount` |
| 日收益列未解析时无法 defer | 持有收益≈0 且收益率≈0 时，即使缺日收益字段也视为待确认 |
