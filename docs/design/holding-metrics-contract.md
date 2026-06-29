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
| `daily_return_percent` | 基金**当日**收益率（板块估算或当日官方净值）；**不是**支付宝 OCR「日收益」 |
| `yesterday_profit` | 上一交易日官方净值收益；支付宝「全部持有」截图「日收益」列解析到此字段 |
| `estimated_holding_return_percent` | **持有列**展示：官方净值用结算值；盘中 = 昨日结算 + 板块涨跌 |
| `estimated_holding_profit` | **持有收益额**展示：官方净值用 OCR 总值；盘中 = 结算持有收益 + 当日收益 |
| `estimated_daily_return_percent` | 当日涨跌：优先 `daily_return_percent`，否则 **仅** `sector_return_percent`（不加 settled 收益率） |
| `daily_profit` | 当日收益额：`settled × r/(100+r)`（官方）或 `settled × sector%/100`（盘中估算） |
| `settled_holding_amount` | **持有金额展示**（盘中）：上一交易日结算额，不随板块刷新变动 |
| `display_holding_amount` | API 下发的展示用结算额（同 `settled_holding_amount`） |
| `amount_includes_today` | 盘中 OCR 确认后应为 `false`；金额已按上一交易日结算口径锁定 |

## 支付宝 OCR 语义

| 支付宝列 | 映射字段 | 说明 |
|----------|----------|------|
| 持有金额 | `holding_amount` / `settled_holding_amount` | 交易日盘中截图 = **上一交易日结算额**（静态展示） |
| 日收益 | `yesterday_profit` | **上一交易日**官方净值收益，不是今日盘中估算 |
| 持有收益 | `holding_profit` | 截至昨日结算的累计持有收益 |

确认写入路径：`POST /api/portfolio/apply-holdings`（`ocr_pipeline.apply_confirmed_holdings`）。

## OCR 确认写入流程（2026-06-26）

```text
用户点「完成」
  → apply-holdings（无网络，<1s）
      ① clear_client_daily_estimate_fields（去掉客户端脏 daily_*）
      ② 查码 + sync_profiles + bootstrap（skip_network，仅内存缓存净值算份额）
      ③ refresh_holdings_sector_quotes(cache_only=True) 读 sector_spot_cache
      ④ enrich_holdings_estimates → 写日快照 + portfolio_summary
  → 前端关闭确认弹窗，展示带板块估算的持仓
  → 后台 refresh-sector-quotes(budget=fast) 无感知刷新最新行情
```

**盘中持有金额：** 仅当 `get_official_nav_return` 已公布当日官方净值时才滚入 `shares × 最新净值`；否则 `holding_amount_sync` 锁定 `settled_holding_amount`，不因 `shares × 昨净值` 漂移。

**「已更新」标签：** 仅当 `daily_return_percent_source === "official_nav"`；板块估算不得标已更新。

## 公式（与养基宝对齐）

- 官方当日收益：`daily_profit = settled × r / (100 + r)`
- 盘中板块估算：`daily_profit ≈ settled × sector_return_percent / 100`
- 盘中累计持有收益率：`settled_return + sector_return_percent`（展示层）
- 总资产（盘中）：`Σ(settled_holding_amount + daily_profit)`
- 过滤规则：`holding_filters.py` ↔ `holdingMetrics.ts`（占位码 `000000`、测试名前缀）

## 修改流程

1. 改 `holding_estimates.py`
2. 更新 `holding_metrics_cases.json` 期望值
3. 跑 `pytest tests/test_holding_client.py tests/test_holding_metrics.py tests/test_apply_holdings_fast_path.py`

## 前端刷新合并（2026-06-26）

OCR 确认、`apply-holdings` 回写、`refresh-sector-quotes` 期间使用 `mergeHoldingsPreserveQuoteFields`（`holdingMetrics.ts`）：按 `fund_code` 合并新列表，**保留**上一屏的 `sector`、`estimated_*`、`daily_*` 等行情字段，直至 API 返回非空新值，避免列表闪「—」。

4. 跑 `vitest holdingDisplay.test.ts`

## 持仓成本与持有收益（2026-06-30）

| 字段 | 支付宝口径 | 说明 |
|------|------------|------|
| `holding_cost` | 持仓成本 | OCR 确认时写入；**不**用 `holding_amount` 或 `shares×净值` 替代 |
| `holding_profit` | 持有收益（累计） | OCR 含当日累计值时 `_ocr_holding_profit_is_cumulative` 为真，**禁止** `_repair_corrupted_settled_profit` 用档案污染值覆盖 |
| `holding_return_percent` | 持有收益率 | 由 `holding_profit / holding_cost` 推导；官方净值公布后随结算重算 |

**官方净值结算后：** `holding_amount_sync` 滚入 `settled_holding_amount` 时同步 `_profit_patch_from_rolled_settled`，持有收益/率与支付宝列表对齐；OCR 路径 `skip_roll=True` 锁定 OCR 金额直至下一结算窗口。

## OCR 确认提速（2026-06-30）

- `ocr_pipeline.apply_confirmed_holdings`：OCR 已带官方当日涨跌时 **跳过** `prime_official_nav_cache`（AkShare 全表预热），确认 <1s。
- 前端 OCR 确认后 **不** 立即 `refresh-sector-quotes`；`seedApplyDisplayFields` / `withApplyDisplayFields` 保留 OCR 持有收益，合并时丢弃 stale `estimated_holding_profit`。
- 详情预取 hydrate 用 `patchHoldingRecord`（按 `fund_code` 原位更新），**不在 hydrate 时 dedupe**，避免循环切换「下一只」时误删基金。

## 基金详情导航（2026-06-30）

- `navigableHoldings` = `dedupeHoldingsByCode(displayableHoldings)`，仅用于详情页上/下一只循环导航。
- `onNavigate(HoldingIdentity)` 按 code/name 定位，不用数组下标传递。
- 预取 effect 依赖 `holdingsPrefetchKey`（fund codes 指纹），避免 `holdings` 每次 hydrate 触发 detail 请求风暴。
