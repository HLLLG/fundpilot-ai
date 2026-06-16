# 激进波段投资风格（日报 + 荐基）

> **版本：** 2026-06-16  
> **状态：** 已实现（含第二期盘中盯盘 + 浏览器推送）  
> **用户确认：** 选项 A — 新增 `decision_style=aggressive`，保留 `tactical`；日报 + 荐基均覆盖

---

## 1. 问题

激进型用户希望「跌买涨卖、一周内出手」，扣 1.5% 手续费后净赚 ≥1%（约涨 2.5%+ 止盈）。现有 `tactical` 偏追涨动量，荐基策略偏中长期不追高，无法满足。

## 2. 目标

| 预设 | `investment_preset` | `decision_style` | 持有周期 |
|------|---------------------|------------------|----------|
| 稳健持有 | `conservative_hold` | `conservative` | 半年～一年 |
| 激进波段 | `aggressive_swing` | `aggressive` | 3～7 天 |

激进波段新增参数：`round_trip_fee_percent`（默认 1.5）、`min_net_profit_percent`（默认 1.0）、`hold_days_target`（默认 7）。止盈线 = fee + net ≈ 2.5%。

## 3. 技术方案

### 3.1 日报（已有持仓）

- `aggressive_swing_recommendations.py`：跌深买入 + 达止盈线减仓
- `recommendation_guard.py`：激进模式同战术，放宽当日新闻限制；不取保守离线最小值
- `deepseek_client._system_prompt`：激进后缀（扣费止盈、持有天数）
- `analysis_facts.portfolio`：写入止盈线与费用参数

### 3.2 荐基

- `selection_strategy` 新增 `dip_rebound`（跌深反弹排序）
- `discovery_guard`：激进时放宽追高守卫（板块 ≥6% 才等待回调）
- `discovery_prompt`：补充 `dip_rebound` 与激进语义

### 3.3 前端

- `RiskControls` / `FundDiscoveryPanel` 顶部：稳健持有 | 激进波段预设
- 激进预设默认荐基策略 `dip_rebound`
- 展示扣费后止盈线示意

## 4. 非目标（第二期）

- 后台自动盯盘推送（机器人 Tab）
- 自动下单

## 5. 第二期（已实现）

- `POST /api/swing-alerts/evaluate` — 评估持仓 + 全市场板块信号，服务端去重
- `GET /api/swing-alerts/today` — 当日已触发记录
- 前端 `useSwingAlerts`：每 **15 分钟**自动评估（评估前刷新板块）+ 浏览器 `Notification`
- `SwingAlertsPanel`：持有 Tab「今日波段信号」
- 高级设置：手续费%/净赚% 滑条、盯盘开关、盯盘范围（仅持仓/全市场/两者）

## 6. 验收标准

1. 切激进预设后 `decision_style=aggressive`，参数一键更新
2. 持有收益 ≥2.5% 时离线规则建议减仓
3. 板块下跌日可建议分批加仓（与 tactical 追涨区分）
4. 荐基 `dip_rebound` 优先近 5 日回调深的候选
5. pytest 全绿
