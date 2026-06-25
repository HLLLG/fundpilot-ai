# 持仓展示口径契约

> **权威实现：** `apps/api/app/services/holding_estimates.py`  
> **API 边界：** `apps/api/app/services/holding_client.py` → `serialize_holding_for_client`  
> **前端展示：** `apps/web/src/lib/holdingDisplay.ts`（优先读 API 字段，无则 fallback 至 `holdingMetrics.ts`）  
> **共享用例：** `apps/api/tests/fixtures/holding_metrics_cases.json`

## 字段语义

| 字段 | 含义 |
|------|------|
| `holding_return_percent` | 昨日结算后的累计持有收益率（不含今日盘中） |
| `sector_return_percent` | 关联板块当日涨跌幅（东财口径） |
| `daily_return_percent` | 基金当日收益率（官方净值或 OCR） |
| `estimated_holding_return_percent` | **持有列**展示：官方净值用结算值；盘中 = 昨日结算 + 板块涨跌 |
| `estimated_holding_profit` | **持有收益额**展示：官方净值用 OCR 总值；盘中 = 结算持有收益 + 当日收益 |
| `estimated_daily_return_percent` | 当日涨跌：优先 `daily_return_percent`，否则 sector 估算 |
| `daily_profit` | 当日收益额：`amount×r/(100+r)`（金额已含当日）或 `amount×r/100` |

## 公式（与养基宝对齐）

- 官方当日收益：`daily_profit = amount × r / (100 + r)`
- 盘中累计持有收益率：`settled + sector_return_percent`
- 过滤规则：`holding_filters.py` ↔ `holdingMetrics.ts`（占位码 `000000`、测试名前缀）

## 修改流程

1. 改 `holding_estimates.py`
2. 更新 `holding_metrics_cases.json` 期望值
3. 跑 `pytest tests/test_holding_client.py tests/test_holding_metrics.py`

## 前端刷新合并（2026-06-26）

OCR 确认、`apply-holdings` 回写、`refresh-sector-quotes` 期间使用 `mergeHoldingsPreserveQuoteFields`（`holdingMetrics.ts`）：按 `fund_code` 合并新列表，**保留**上一屏的 `sector`、`estimated_*`、`daily_*` 等行情字段，直至 API 返回非空新值，避免列表闪「—」。
4. 跑 `vitest holdingDisplay.test.ts`
