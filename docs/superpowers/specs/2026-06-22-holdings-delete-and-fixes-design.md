# 好基灵 — 删除简报页 + 持仓删除 + 三个 Bug 修复（设计）

**日期：** 2026-06-22
**状态：** 已与用户确认，进入实现

## 背景与目标

核心功能已完成，本次为功能扩展 + Bug 修复，共 5 项：

1. **优化**：删除冗余的「简报」（TodayBriefing）展示页及相关代码。
2. **Bug 1**：线上（CloudBase）手动添加新基金 504 / CORS（本地连同库却成功）。
3. **Bug 2**：支付宝总览截图上传后提示「未识别为支付宝持有页」，且 5 只基金只识别 4 只（漏「广发电子信息传媒产业精选股票C」）。
4. **Bug 3**：手动添加「中航机遇领航混合发起C」后「板块行情拉取失败（网络/代理），且没有可用快照」+ 当日收益 0.00。
5. **新功能**：持仓页支持删除基金。

## 已确认决策

| 决策点 | 选择 |
|--------|------|
| 删除基金交互 | 基金详情页底部「删除该基金」按钮 + 二次确认 |
| 删除数据范围 | 仅从当前持仓/账户汇总移除；保留 `fund_profiles`、板块映射、历史日快照 |
| 504 修复策略 | apply-holdings 改「快速写入」：只写库，不做重网络拉取；板块/当日收益交前端 `refresh-sector-quotes` 异步刷新 |
| 删除简报后默认落地页 | 「持仓」Tab |

## 根因结论（代码级）

- **Bug 1**：`apply_confirmed_holdings`（`ocr_pipeline.py`）在单次 HTTP 请求内串行做 AkShare 查码（冷启动可达 120s）+ `process_overview_holdings` 全量板块刷新（`timeout_seconds=None` 无预算）+ per-fund 官方净值拉取，总耗时远超 CloudBase 网关 ~60s；504 不经 FastAPI CORS 中间件，浏览器二次报 CORS。
- **Bug 3**：`sector_quote_service.refresh_holdings_sector_quotes` 在所有持仓 `sector_quote_lookup_label` 均为空时，`need_spot_boards=False` → 跳过 spot 拉取**和**天天基金估值兜底 → `kline_prefetched==0 且 boards 全空 且 estimate 空` → 命中硬失败文案（`sector_quote_service.py:147`），当日收益 0。
- **Bug 2**：① `detect_ocr_source`（`ocr_parser.py`）缺少 `is_alipay_holdings_page` 的 `%` 启发式，页眉关键词漏读时返回 `unknown` → 警告文案；② 基金名锚点正则（`COMPLETE_FUND_NAME_RE`/`FUND_PRODUCT_SUFFIX_RE` 等）不含 `股票[A-CEH]` 后缀 → 「广发…精选股票C」不成锚点被漏；总览「部分成功即早退」不再回退切块兜底。

## 竞品调研（删除交互）

- 同花顺投资账本（基金记账，定位最接近）：持仓页右上角「…」菜单选删除，或资产页项目左滑删除。
- iOS 列表通用：左滑删除。有知有行账本：支持修改/删除渠道。
- 结论：主流为 列表左滑 / 「…」或长按菜单 / 编辑模式，均带二次确认。本项目采用**详情页删除按钮 + 二次确认**（桌面/移动一致、不与列表行「点击打开详情」手势冲突，改动最小最稳）。

## 方案

### 任务 A：删除简报页（纯前端）
- 删除文件：`TodayBriefing.tsx`、`BriefingDecisionCards.tsx`、`BriefingChatPanel.tsx`、`lib/todayBriefing.ts`、`lib/todayBriefing.test.ts`（已确认仅彼此 + Dashboard 引用，安全）。
- `Dashboard.tsx`：移除 `today` 分支与 import；`activeTab` 初始默认 `holdings`；`fundpilot-dashboard-tab` 事件白名单去掉 `today`。
- `DashboardNav.tsx`：桌面 `DESKTOP_TABS`、移动 `MOBILE_PRIMARY` 去掉「简报」；移动首项为「持仓」。
- `storage.ts`：`DashboardTabId`/`DASHBOARD_TAB_IDS` 去 `today`；`loadDashboardTab` 默认 `holdings`，旧值 `today` 兜底为 `holdings`。

### 任务 B（Bug 1）：apply-holdings 快速写入
- `apply_confirmed_holdings`：保留 finalize 查码 + `apply_primary_sector_to_holdings`(DB) + `sync_profiles_from_holdings`(DB)；用 `enrich_loaded_holdings(with_network=False)` 做展示估算；`save_portfolio_summary` + `save_daily_snapshot` 后立即返回。**不再调用** `process_overview_holdings`。
- 前端 `Dashboard.tsx`：`handleManualAddHoldings` 与 `handleConfirmOcrHoldings` 成功后显式 `await sectorRefresh.refresh(false, "fast")`，由 `refresh-sector-quotes`（8s 预算、已持久化）补板块/当日收益。
- 保留 OCR 总览份额基线：`refresh-sector-quotes` 成功后的 `persist_holdings_after_sector_refresh` 已做 `sync_holding_amounts_from_shares`；验证「重新上传总览金额对齐」不回归。

### 任务 C（Bug 3）：无板块基金估值兜底
- `sector_quote_service.py`：将天天基金估值兜底与 `need_spot_boards` 解耦——当 boards 全空且无 kline 命中时，仍对有真实 `fund_code` 的持仓拉一次批量估值；只要有任一估值/快照成功就不返回硬失败。无板块基金「板块」列显示「—」可接受，但当日收益由估值给出而非 0。

### 任务 D（Bug 2）：支付宝总览 OCR
- `ocr_parser.detect_ocr_source`：补 `%` 启发式（≥2 行含 `%` 且无 `￥` 视为 alipay_holdings），与 `is_alipay_holdings_page` 一致。
- `fund_name_utils.py` / `alipay_holdings_parser.py`：基金名/份额后缀正则加入 `股票[A-CEH]?`、`精选股票`。
- 总览/名称锚点解析数明显少于 `%` 行数时，回退 `_split_fund_blocks` 切块兜底。
- 新增 fixture `alipay_overview_holdings_5_ocr.txt`（5 只含股票C + 页眉缺失变体）+ 测试断言 5 只、来源 `alipay_holdings`。

### 任务 E（新功能）：删除基金
- 后端 `DELETE /api/portfolio/holdings/{fund_code}`：从最近快照移除该 `fund_code`（同名兜底）→ 重存快照/summary；保留档案/映射/历史快照。空仓后写空快照。
- 前端 `api.ts` 加 `deletePortfolioHolding(fundCode)`；`YangjibaoFundDetail` 底部「删除该基金」按钮 + 确认弹窗；`Dashboard` 删除成功后更新 `holdings` 与本地缓存、关闭详情。

## 验证
- 后端 pytest：OCR 解析 5 只 + 来源判定、apply 快速返回（不触网）、delete 端点、sector 估值兜底不硬失败。
- 前端：`npm run lint && npm run typecheck && npm run build`；vitest（删 todayBriefing.test 后无残留引用）。
- 本地起服手测：添加新基金无红条且当日收益非 0；删除基金；支付宝总览 5 只；默认落地持仓页。

## 不做（YAGNI）
- 列表左滑/编辑模式批量删除（本期仅详情页删除）。
- 彻底清除档案/历史（本期仅移除当前持仓）。
- 简报页相关后端无独立逻辑，无需改后端。
