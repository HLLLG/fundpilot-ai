# 推荐基金 V3 — 选基策略（均衡潜力 + 含新发观察）

> **版本：** 2026-06-14  
> **状态：** 已实现  
> **前置：** V2 已交付（`docs/superpowers/specs/2026-06-14-fund-discovery-v2-design.md`）  
> **用户确认：** 采用策略 1（均衡潜力）与 2（含新发观察），覆盖老基潜力 + 新发机会

---

## 1. 问题

MVP/V2 候选池按 **近 1 年收益降序** 取 Top，导致推荐偏向「已大涨」基金，与小白用户「不想追高」诉求冲突。

## 2. 目标

| 策略 | 说明 | 默认 |
|------|------|------|
| **均衡潜力** `balanced` | 综合近 3/6 月相对强弱，惩罚近 1 年极端涨幅（>70%），优先「近期走强但年度涨幅适中」 | ✓ |
| **含新发观察** `with_new_issue` | 每板块约 2 只近 6 月内成立、名称匹配板块的新发基金 + 3 只均衡潜力老基 | |

## 3. 非目标

- 强势动量（纯 1 年排行）保留为内部回退，不在 UI 暴露
- 复杂多因子量化模型（V3.1+）
- 自动下单

## 4. 技术方案

### 4.1 候选池

- 新增 `discovery_selection_strategy.py`：`balanced_score()`、`pick_sector_candidates()`
- `build_candidate_pool(..., selection_strategy)` 按策略排序/配额
- `akshare_subprocess.fetch_new_fund_offerings()` — `fund_new_found_em`，成立 ≤180 天

### 4.2 守卫增强

`avoid_chasing=true` 时额外规则：

- 近 1 年涨幅 ≥100% → `分批买入` 降为 `等待回调`
- `nav_trend.distance_from_high_percent` > -5%（贴近区间高点）→ 同上

### 4.3 API / 模型

- `DiscoveryRequest.selection_strategy: "balanced" | "with_new_issue"`，默认 `balanced`
- `discovery_facts.selection_strategy` 写入只读事实

### 4.4 前端

- 扫描区「选基策略」chips：均衡潜力 | 含新发观察
- 候选池表增加近 3 月 / 近 6 月列；新发标记「新发」

## 5. 验收标准

1. 默认「均衡潜力」扫描后，候选池近 1 年涨幅分布明显低于纯排行
2. 「含新发观察」候选池含 `selection_reason=新发观察` 条目（数据源可用时）
3. `avoid_chasing` 对高 1 年涨幅基金生效
4. pytest 新增项通过；`npm run typecheck` 通过
