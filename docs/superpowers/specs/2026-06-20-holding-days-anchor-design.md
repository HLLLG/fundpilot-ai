# 持有天数锚点修复 — 设计（Phase 1）

**日期：** 2026-06-20
**状态：** 已与用户确认设计
**范围：** 修复「持有天数不随日历递增」的 bug。本文为多阶段扩展（持有天数 / 批量加减仓 / 搜索+自选）的第一阶段，独立可发布。

---

## 1. 背景与问题

用户反馈：**持有中的基金，持有天数不会随时间增长。**

现状（`holding_detail_service._resolve_holding_days`）：

```
1. profile.first_purchase_date 存在 → today - first_purchase_date（source "user"）
2. 否则用 OCR holding_days + holding_days_as_of 做 aging（source "ocr_detail" / "snapshot"）
3. 都没有 → None（显示 —）
```

**根因：** 经支付宝「我的基金」列表 OCR 录入的持仓，既没有 `first_purchase_date`，也没有养基宝详情的 `holding_days` / `holding_days_as_of`。因此持有天数始终是 `—` 或静态值，**没有可随日历递增的日期锚点**。

用户期望：**用户首次录入持有时记录一个初始锚点，之后随时间自然递增。**（已确认）

---

## 2. 目标

- 任何首次录入持有的基金都获得一个稳定的「首次记录」日期锚点。
- 持有天数 = `today − 锚点`，每次请求按当天重算，自动递增。
- 已有数据（长期持有）不被错误重置为 0 天。
- 用户仍可通过现有滚轮日期选择器更正首次购入日（最高优先级）。

---

## 3. 数据模型

`FundProfile`（`apps/api/app/models.py`）新增字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| `first_seen_date` | `str \| None` | 应用侧「首次记录到持有」的锚点日（ISO `YYYY-MM-DD`） |

`fund_profiles` 表以整段 JSON `payload` 存储 `FundProfile`，**无需 schema 迁移**，新增字段默认 `None` 即可向后兼容。

---

## 4. 锚点写入（`FundProfileService.save_profile`）

`save_profile` 是所有持仓持久化的唯一收口（养基宝总览 OCR、养基宝详情 OCR、支付宝列表 OCR、`apply-holdings` 均经此）。

**仅当 profile 为全新（`existing is None`）且 `first_seen_date` 为空时**写入锚点：

```
if first_purchase_date 存在:
    first_seen_date = first_purchase_date
elif OCR holding_days 存在:
    first_seen_date = today - holding_days 天
else:
    first_seen_date = today
```

- 已存在的 profile 保留原锚点（重复上传不重置）。
- `merge_detail_profile` 像现有 `first_purchase_date` 一样把 `first_seen_date` 透传（取已有值优先，避免被覆盖成空）。

---

## 5. 持有天数解析（`_resolve_holding_days`）

新优先级：

| 优先级 | 来源 | 计算 | source 标签 |
|--------|------|------|-------------|
| 1 | 用户设定 `first_purchase_date` | `today − date` | `"user"` |
| 2 | `first_seen_date` | `today − date` | `"first_seen"` |
| 3 | 旧数据 OCR `holding_days` + `holding_days_as_of` aging | `holding_days + (today − as_of)` | `"ocr_detail"` |
| 4 | 快照回退 | — | `"snapshot"` |

天数每次按 `today` 重算 → 自动递增。

---

## 6. 向后兼容 / 回填

读取时若一个**持有中**的 profile 没有 `first_seen_date`：

- 有 `first_purchase_date` → 由它推导（不写库，解析层即得）。
- 有 OCR `holding_days` → 走优先级 3 的 aging（保持原有行为）。
- 两者都没有 → **保持 `None`（显示 `—`）**，不要回填成 `today`。

原因：对一个早已持有多日的旧基金回填 `today` 会把天数错误重置为 0。新锚点只对「从现在起首次录入」的持仓生效。

> 可选：首次录入新持仓时若顺手写库锚点即可；旧持仓不主动批量回填，交由用户用日期选择器更正或等 Phase 2 交易记录推导。

---

## 7. 前端

- `YangjibaoFundDetail` 已渲染 `holding_days` 及 source hint，无结构改动。
- `sourceHint` 映射新增 `"first_seen"` → 「按首次记录日」之类的提示文案。

---

## 8. 测试（`tests/test_holding_detail_service.py`）

| 用例 | 期望 |
|------|------|
| 全新 profile 无任何天数信息 | 锚点=today，持有天数=0，且对 today+N 递增 |
| 全新 profile 携带 OCR holding_days | 锚点回退 N 天，day-one 即为 N |
| 用户设定 `first_purchase_date` | 优先级最高，覆盖 first_seen |
| 旧 profile 无锚点但有 OCR holding_days | 走 aging 回退，行为不变 |
| 旧 profile 持有多日且无任何锚点 | 持有天数 `—`（None），不被重置为 0 |

---

## 9. 影响面与非目标

- **改动文件：** `models.py`、`fund_profile.py`（save_profile / merge_detail_profile）、`holding_detail_service.py`、前端 `sourceHint` 文案、测试。
- **非目标：** 交易记录推导真实建仓日（Phase 2）；批量加减仓；搜索/自选。
