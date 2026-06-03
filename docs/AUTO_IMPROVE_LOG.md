# 自主优化日志（养基宝板块 / 档案 OCR）

## 迭代 1 — 板块解析与档案合并（2026-06-03）

### 问题修复
- 养基宝详情 OCR：区分「场内指数」与「关联板块」，四只基金布局回归测试
- 拒绝将 `+` / `-`、纯涨跌幅行、`关联板块` Tab 标签误存为板块名
- 档案合并：部分 OCR 时保留已有 `sector_name` / `intraday_index_name`
- `resolve_holding`：已知基金代码时从档案修复错误板块（如 `+` → `半导体`）
- 板块涨跌：有场内指数时优先用指数口径（`sector_quote_lookup_label`）
- 读取档案时 `_sanitize_profile_sector_fields` 清理历史脏数据

### 测试
- `test_yangjibao_four_funds.py`（025856 / 015945 / 008586 / 519674）
- `test_resolve_holding_sectors.py`

## 迭代 2 — 持久化、修复接口与前端（2026-06-03）

### 问题修复
- 快照合并时同步 `sector_name` / `intraday_index_name`（有效值才覆盖）
- `save_fund_profile` / 读取档案时统一 sanitize
- `POST /api/fund-profiles/repair-sectors` 清理库内历史脏数据（如 `+`）
- 前端档案库增加「修复无效关联板块」按钮
- `resolve_holding` 对已识别基金代码同样从档案补全板块
- 合并档案时 `pick_index_name` 保留场内指数

### 测试
- `test_repair_fund_profiles.py`
- `test_portfolio_persistence_overlay.py`

## 迭代 3 — 校验、文档与 API 元数据（2026-06-03）

### 问题修复
- 持仓校验：`sector_name` 为 `+` 等无效值时给出 `invalid_sector_label` 警告
- 板块刷新 API 返回 `sector_quote_label` / `intraday_index_name` 便于排查
- `sector_display_label` 与前端 `profileSector.ts` 一致过滤无效名
- 更新 `docs/PROJECT_CONTEXT.md` 养基宝场内指数规则说明

### 测试
- `test_holding_validation.py::test_validate_invalid_sector_label`
