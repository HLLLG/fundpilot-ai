# 基金业绩基准 → 关联板块自动解析 — 实施计划

> **Status:** 2026-06-26 已落地

**Goal:** 指数型基金从官方业绩比较基准解析跟踪指数，映射到 `THEME_BOARD_INDEX` 展示板块，替代 per-fund seed 与易错的名称推断。

**Spec:** `docs/superpowers/specs/2026-06-26-fund-benchmark-sector-design.md`

---

## 任务总览

| 任务 | 状态 | 说明 |
|------|------|------|
| `fund_benchmark_sector.py` 拉取/解析/映射 | ✅ | AkShare 子进程 + `parse_benchmark_index` + `THEME_BOARD_INDEX` |
| `fund_primary_sector_service` 接入 priority 65 | ✅ | source=`benchmark_index`，可覆盖 name_infer/seed |
| `sector_canonical` 最长匹配 + 半导体材料 | ✅ | 修复「半导体材料」→「半导体」子串误命中 |
| Windows 子进程编码修复 | ✅ | `ensure_ascii=True` JSON 输出 |
| 当日收益 defer bypass 修复 | ✅ | sector_quote / amount_sync / estimates + 前端防御 |
| 单测 | ✅ | `test_fund_benchmark_sector.py` + `test_profit_accrual_defer.py` |

---

## 验收清单

- [x] 021533 → 板块「半导体材料」，指数 931743
- [x] `get_canonical_sector("半导体材料")` → 931743
- [x] 当日新购 defer 在官方 NAV 公布后仍日收益为 0
- [x] API 全量 147 passed
