# 个人持仓档案与总览同步 — 方案说明

**日期：** 2026-06-01  
**状态：** 已实现；P0/P1/P2 已于 2026-06-01 完成

### P1/P2 补充（2026-06-01）

- **P1：** `analysis_facts` 注入模型与报告；`news_citation` 校验利好/利空标题；`GET /api/reports/{id}/outcomes` 建议复盘；黄金路径测试 `test_golden_pipeline.py`
- **P2：** AkShare 基金概况/累计收益诊断字段；`rebalance_simulator` + API；深度模式 `report_judge`（规则审校 + Flash 二审）

## 竞品调研（简要）

| 产品 | 持仓展示 | 数据更新方式 | 与 FundPilot 关系 |
|------|----------|--------------|-------------------|
| **养基宝** | 账户汇总 + 列表（金额/板块/持有收益）；详情页 3×3 指标 | 截图/账户导入，非 API 开放 | 本项目 OCR 数据源 |
| **支付宝基金** | 总资产、昨日/当日收益、单基卡片 | 账户直连 | 用户真实持仓在支付宝，养基宝为跟踪视图 |
| **且慢/雪球** | 组合仪表盘、成本线、收益曲线 | 绑卡或手动录入 | 可参考「登录即见总资产」体验 |
| **FundPilot（当前）** | 截图校对 → 日报；档案仅详情建档 | 总览不反哺档案 | **本次补齐** |

**结论：** 用户心智是「支付宝持有列表 + 养基宝详情指标」。FundPilot 应用 `fund_profiles` 作本地「我的基金库」，总览 OCR 作增量同步，详情 OCR 作深度建档。

## 已确认的产品决策

1. 总览出现、档案没有的基金 → **自动简略档案**（`is_provisional` + 临时代码 `9xxxxx`）。
2. 总览消失的基金 → **保留档案与上次数据**，不删除、不清零。
3. UI → **强化「基金档案」Tab**：顶部账户汇总 + 基金卡片；顶栏指标从持久化数据读取。

## 技术方案

### 数据流

```text
养基宝总览截图 → POST /api/ocr
  → parse_holdings + parse_portfolio_summary
  → resolve_holdings（名称匹配档案补代码）
  → sync_profiles_from_holdings（合并/新建）
  → save_portfolio_summary → SQLite portfolio_state

养基宝详情截图 → POST /api/fund-profiles/ocr
  → 完整字段建档；若存在 provisional 同名档案则替换为正式代码

GET /api/portfolio/summary → 账户汇总 + 全部 profiles
```

### 合并规则（总览 → 档案）

**会更新：** 持有金额、持有/当日收益、板块、仓位占比（按本次总览金额之和重算）。  
**不覆盖：** 份额、成本、昨日收益、持有天数（仅详情页有）。

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ocr` | 响应增加 `profile_sync`、`portfolio_summary` |
| GET | `/api/portfolio/summary` | 账户资产、当日收益、基金列表 |

## 养基宝 OCR：负号恢复（2026-06-01）

**现象：** 绿色亏损行中，OCR 常识别出 `176.88`、板块 `2.52%`，但截图实为 `-176.88`、`-2.52%`；有时仅「当日收益率」带负号。

**处理（`ocr_parser.py`，解析后规则层，不依赖二次 OCR）：**

| 步骤 | 说明 |
|------|------|
| 独立行 `-` | 减号单独成行时与下一行数字/百分比合并 |
| 收益率对齐 | 当日/持有收益额与对应收益率同号；板块涨跌与当日收益率同号 |
| 账户总收益 | 顶部账户「当日收益」为负时，与各行金额加总交叉校验 |
| 版式分支 | ￥ 前双金额（当日+持有）vs 单金额（仅当日），避免误填 `daily_profit` |

**测试：**

- `apps/api/tests/fixtures/yangjibao_overview_signed_daily_ocr.txt` — 金额无符号、百分比有负号、账户 `-482`
- `test_parse_overview_restores_negative_daily_profit_and_sector_when_ocr_drops_signs`
- `test_parse_negative_marker_on_separate_line`
- 保留：`yangjibao_holdings_no_daily_ocr.txt` — 当日列为 `-` 时 `daily_profit` 仍为 `None`

人工校对仍建议扫一眼绿色亏损行；规则无法覆盖的版式可再补 fixture。

## 验收建议

1. 打开 http://127.0.0.1:3000 →「基金档案」应显示汇总（若曾上传总览）。
2. 上传 `tests/fixtures/yangjibao_holdings_no_daily_ocr.txt` 或总览截图 → 提示「档案已同步」。
3. 先详情建档一只基，再总览 → 该基份额/成本保留，金额更新。
4. 未建档的基金会显示「待补全详情」。
5. 上传绿色亏损总览（如账户当日收益约 `-482`）→ 校对表「当日收益额」「板块涨跌」应为负，与「当日收益率」同号。
