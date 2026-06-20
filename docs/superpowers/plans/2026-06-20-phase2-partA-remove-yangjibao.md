# Phase 2 · Part A — 移除养基宝导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 彻底删除养基宝总览/详情导入（入口 + 底层解析 + fixtures + 测试），导入只保留支付宝（+手动输入），且支付宝全流程与全量测试保持通过。

**Architecture:** 删除「养基宝文本解析」专属代码，保留被支付宝路径共用的板块/指数推断 helper。`parse_holdings_from_text` 收敛为仅支付宝；`detect_ocr_source` 仅返回 `alipay_holdings`/`unknown`；`save_profile`/`migrate_fund_profile_code` 去除对 `merge_detail_profile` 的依赖。

**Tech Stack:** FastAPI/Pydantic/pytest 后端；Next.js/React/TS 前端。

---

## 关键：保留清单（绝不可删，支付宝路径共用）

`apps/api/app/services/fund_profile.py` 中以下**保留**：
- 类方法：`save_profile`、`list_profiles`、`resolve_holding`、`resolve_holdings`、`find_match`、`sync_profiles_from_holdings`、`_find_profile_for_holding`
- 函数：`resolve_first_seen_anchor`、`merge_holding_into_profile`、`_holding_to_provisional_profile`、`_aliases_for_name`、`provisional_code_for_name`、`migrate_fund_profile_code`
- 板块/指数推断 helper：`_is_valid_sector_label`、`_sanitize_profile_sector_fields`、`_looks_like_board_label`、`_looks_like_index_name`、`infer_intraday_index_from_sector`、`infer_intraday_index_from_fund_name`、`_normalize_index_and_board_fields`、`_infer_related_board_label`

`apps/api/app/services/ocr_parser.py` 中保留：
- `parse_holdings_from_text`（重构为仅支付宝）、`detect_ocr_source`（重构）
- 常量 `ALIPAY_HOLDINGS_MARKERS`（`detect_ocr_source` 用）
- 外部依赖 `alipay_holdings_parser.is_alipay_holdings_page` / `parse_alipay_holdings_page`（整文件保留）

`alipay_holdings_parser.py`、`overview_pipeline.py`、`holding_amount_sync.py`、`portfolio_parser.py`、`portfolio_snapshot.py` **整体保留**。

---

## 删除清单

`fund_profile.py` 删除（养基宝详情文本解析专属）：
- `parse_profile_from_text`、`_find_code`、`_find_name_before_code`、`_numbers_after_label`、`_find_detail_sector_fields`、`_finalize_sector_fields`、`_parse_name_percent_line`、`_parse_related_board_line`、`_related_board_after_heading`、`_name_percent_after`、`_normalize_ocr_line`
- `merge_detail_profile`（见 Task A2 的替代）
- 仅被上述函数使用的正则：`CODE_RE`、`NUMBER_RE`、`PERCENT_RE`、`SECTOR_RE`、`NAME_PERCENT_RE`、`_RELATED_BOARD_SUMMARY_RE`、`_DETAIL_TAB_LABELS`（**先确认** `_DETAIL_TAB_LABELS` 是否被保留函数引用：`merge_detail_profile`、`_parse_related_board_line` 用它；删除这些后若无其他引用则删）

`ocr_parser.py` 删除（养基宝总览解析）：
- `parse_holdings_from_text` 内的 FUND_CODE_RE 循环主体、`_holding_block`、`_guess_fund_name`、`_extract_float`、`_parse_alipay_drafts_without_codes`、`_extract_yangjibao_metrics`、`_extract_signed_numbers`、`_extract_signed_percents`、`_parse_amount_token`、`_is_negative_marker_line`、`_block_has_negative_markers`、`_first_meaningful_line`、`_align_profit_sign_with_return`、`_align_sector_sign`、`_parse_account_daily_profit`、`_reconcile_daily_profit_signs`、`_daily_data_missing`、`_is_daily_placeholder_line`、`_find_amount_index`、`_extract_sector_name`、`_looks_like_sector_name`、`_looks_like_alipay_fund_name`、`_looks_like_alipay_promo_text`、`_has_fund_share_class_suffix`、`_has_fund_product_name_shape`、`_round2`、`_trim_block_footer`、`is_yangjibao_detail_page`、`_looks_like_alipay_holdings_list`、`_parse_alipay_holdings_list`、`_trim_alipay_noise_lines`、`_extract_alipay_holdings_metrics` 及仅它们使用的正则/常量。
  > 注：`_parse_alipay_holdings_list` 等虽名带 alipay，但**未被** `parse_holdings_from_text` 调用（支付宝走外部 `alipay_holdings_parser`）。删前对每个待删函数 grep 全仓确认无外部引用。

`ocr_pipeline.py` 删除：`_run_yangjibao_detail_pipeline`、`yangjibao_detail` 分支、`_ocr_amount_semantics` 养基宝分支。

`main.py`：`/api/portfolio/apply-holdings` 移除 `detail_profiles` 入参处理；OCR 路由移除养基宝详情 preview 分支（保留 alipay 预览/确认）。

`fund_primary_sector_service.py`：移除"从养基宝详情 OCR 沉淀板块"的入口（保留种子/季报/按 code 查表）。

前端：
- `AddHoldingModal.tsx`：移除 `yangjibao_overview`/`yangjibao_detail` channel 与文案，保留 `alipay` + 手动输入。
- `AlipayOcrConfirmModal.tsx`：移除 `isDetail`/`yangjibao_detail` 分支。
- `YangjibaoFundDetail.tsx`：移除上传详情截图按钮、`onUploadDetailScreenshot`、`isDetailOcrUploading`。
- `Dashboard.tsx`：移除 `pendingDetailProfile`、`yangjibao_detail` 分支与相关 state/handler。
- `api.ts`：`applyHoldings` 移除 `detailProfiles`/`detail_profiles`。
- `workflowBlockers.ts`：文案改为支付宝。

测试/fixtures：
- 删除文件：`tests/test_yangjibao_four_funds.py`；`tests/fixtures/yangjibao_*`。
- 改造：`test_ocr_parser.py`（删养基宝用例，保留 `alipay_holdings_list` 等支付宝用例及 `parser_returns_empty`）；`test_fund_profile.py`（删 `parse_profile_from_text`/`merge_detail_profile` 用例与 `DETAIL_TEXT`/`YANGJIBAO_BOTTOM_LAYOUT`，保留 Phase 1 `first_seen`/anchor 用例并改用直接构造 `FundProfile`）；`test_portfolio.py`、`test_holding_detail_service.py`、`test_api.py` 中用 `parse_profile_from_text` 造数据的，改为直接构造 `FundProfile(...)` 并 `save_profile`。

---

### Task A1: 后端删除 + 重构（一个内聚单元）

**Files:** `ocr_parser.py`、`fund_profile.py`、`ocr_pipeline.py`、`main.py`、`fund_primary_sector_service.py` + 上述后端测试/fixtures。

- [ ] **Step 1:** 重构 `ocr_parser.parse_holdings_from_text`：

```python
def parse_holdings_from_text(text: str) -> list[Holding]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if is_alipay_holdings_page(lines):
        return parse_alipay_holdings_page(text)
    return []
```

- [ ] **Step 2:** 重构 `detect_ocr_source`：

```python
def detect_ocr_source(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(marker in line for line in lines for marker in ALIPAY_HOLDINGS_MARKERS):
        return "alipay_holdings"
    if is_alipay_holdings_page(lines):
        return "alipay_holdings"
    return "unknown"
```

- [ ] **Step 3:** 删除「删除清单」中 `ocr_parser.py` 列出的养基宝/未用函数与常量。删前对每个函数名 `grep_search` 全仓确认无外部引用（保留 `ALIPAY_HOLDINGS_MARKERS`）。

- [ ] **Step 4:** `fund_profile.py`：删 `parse_profile_from_text` 及其专属 helper 与正则（见删除清单）。

- [ ] **Step 5:** `fund_profile.py`：去除 `merge_detail_profile`。`save_profile` 改为：

```python
        if existing is not None:
            profile = profile.model_copy(
                update={"aliases": sorted(set(existing.aliases) | set(profile.aliases))}
            )
        if existing is None and not profile.first_seen_date:
            profile = profile.model_copy(
                update={"first_seen_date": resolve_first_seen_anchor(profile)}
            )
```
（移除 `profile = merge_detail_profile(...)` 与 `source == "yangjibao-detail"` 分支。）
`migrate_fund_profile_code` 中 `merge merged = merge_detail_profile(existing, merged)` 改为保留新码档案为主、并入旧 aliases：
```python
    if existing is not None and existing.fund_code != old_code:
        merged = merged.model_copy(
            update={"aliases": sorted(set(existing.aliases) | set(merged.aliases))}
        )
```

- [ ] **Step 6:** `ocr_pipeline.py`：删 `_run_yangjibao_detail_pipeline` 及 `if ocr_source == "yangjibao_detail":` 分支；`_ocr_amount_semantics` 删养基宝分支（`source != "alipay_holdings"` 与 `yangjibao_detail` 部分），未知源回退保守语义。

- [ ] **Step 7:** `main.py`：apply-holdings 去 `detail_profiles`；OCR 路由去养基宝详情 preview 分支。`fund_primary_sector_service.py`：去养基宝详情沉淀入口。

- [ ] **Step 8:** 测试/fixtures：删 `test_yangjibao_four_funds.py`、`tests/fixtures/yangjibao_*`；改造 `test_ocr_parser.py`/`test_fund_profile.py`/`test_portfolio.py`/`test_holding_detail_service.py`/`test_api.py`（用 `parse_profile_from_text` 处改为直接构造 `FundProfile`）。

- [ ] **Step 9: 验证** Run（from `apps/api`）：`.venv/Scripts/python.exe -m pytest tests -q`
Expected: 全部通过（养基宝用例已删，支付宝用例保留）。

- [ ] **Step 10: Commit**

```bash
git add apps/api
git commit -m "refactor: remove yangjibao import parsing, keep alipay flow"
```

---

### Task A2: 前端删除养基宝导入

**Files:** `AddHoldingModal.tsx`、`AlipayOcrConfirmModal.tsx`、`YangjibaoFundDetail.tsx`、`Dashboard.tsx`、`api.ts`、`workflowBlockers.ts`

- [ ] **Step 1:** 按「删除清单」前端部分移除养基宝 channel/分支/props/handler 与文案。
- [ ] **Step 2:** 确保 `AddHoldingModal` 只剩「支付宝」+「手动输入」，删除 channel 切换 Tab（仅一个上传源时可去掉 Tab 行）。
- [ ] **Step 3: 验证** Run（from `apps/web`）：`npm run lint`、`npm run typecheck`、`npm run build`
Expected: 全部通过。
- [ ] **Step 4: Commit**

```bash
git add apps/web
git commit -m "refactor: drop yangjibao import channels in UI, keep alipay"
```

---

### Task A3: 全量回归

- [ ] **Step 1:** Run（from `apps/api`）：`.venv/Scripts/python.exe -m pytest tests -q` → 全绿。
- [ ] **Step 2:** Run（from `apps/web`）：`npm run lint && npm run typecheck && npm run build` → 全绿。
- [ ] **Step 3:** 手动核对支付宝主流程未受影响（OCR detect → alipay 解析 → apply → 板块刷新路径仍在）。

---

## 注意
- 删函数前务必 `grep_search` 确认无外部引用，尤其 `ocr_parser` 里名字带 `alipay` 但实际服务养基宝/未用的 helper。
- 不要动 `alipay_holdings_parser.py`、`overview_pipeline.py`、`holding_amount_sync.py`、板块/指数推断 helper。
- Phase 1 的 `first_seen_date`/`resolve_first_seen_anchor` 必须保留且测试通过。
