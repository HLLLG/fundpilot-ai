# 短线抄底（跌深反弹）+ 板块注册表统一 — 设计

**日期：** 2026-06-21  
**状态：** 已与用户确认设计（2026-06-21）  
**范围：** 两阶段交付；偏**新基发现**（强化 discovery + 大跌预筛），复用激进波段止盈语义；附带方案 A 板块注册表统一（为扩展关注方向与大跌榜按板块筛选打底）。

**用户确认：**
- 板块统一：方案 A（底层注册表，UI/API 分场景）
- 短线抄底：Phase 1 + Phase 2 都要
- 场景侧重：偏新基 → discovery 管线，而非持仓加仓

---

## 1. 背景与问题

### 1.1 用户反馈（原文意图）

> 走抄底、快进快出路线：AI 推荐近几个交易日**大跌**的基金，且**较大概率反弹**；低点大资金买入，涨 2～3 天、**扣手续费后盈利**即卖出。

### 1.2 现状（已有碎片能力）

| 能力 | 位置 | 缺口 |
|------|------|------|
| 激进波段预设 | `investment_presets.aggressive_swing` | 有 3～7 天周期与扣费止盈线，但未与「荐新基大跌榜」打通 |
| 选基策略 `dip_rebound` | `discovery_selection_strategy` | 仅在 **15～25 只窄池**内按近 5 日回调排序，非全市场大跌预筛 |
| 今日波段信号 | `swing_alert_engine` + 持有 Tab | 全市场仅 **板块级**跌深提醒，无基金级大跌榜 |
| 板块信号回测 | `sector_signal_context` | 板块 T→T+1，非基金大跌后 3 日反弹率 |
| 7 日推荐复盘 | `discovery_outcomes` | 通用方向一致，无「3 日内达止盈线」指标 |

### 1.3 板块双轨问题（方案 A 前置）

- 市场主题板块：`_THEME_BOARD_WHITELIST`（67）+ `_THEME_BOARD_INDEX`（小倍指数口径）
- 荐基关注方向：`_DISCOVERY_CHIP_LABELS`（21）+ `sector_canonical._CANONICAL_BY_LABEL`
- 同名板块涨跌 secid 可能不一致（如人工智能 931071 vs 930713），**不可强行合并 API**，但应统一注册表。

---

## 2. 目标

### 2.1 产品目标

1. **Phase 1（MVP，可独立发布）：** 在「推荐基金」内提供明确的 **「短线抄底」** 扫描路径：全市场/板块内 **基金 NAV 大跌预筛** → 窄池 → AI 报告，输出含扣费止盈线与 2～5 天持有窗口。
2. **Phase 2：** 独立 **「大跌反弹雷达」** 面板：基金级大跌榜 + 反弹信号标签 + 历史参考 + 一键带入 discovery 深度扫描。
3. **板块注册表（方案 A）：** 单一 `sector_registry` 供主题板块、关注方向扩展、大跌榜按板块筛选共用；UI/API 保持分场景 secid 选择。

### 2.2 非目标（YAGNI）

- 不承诺「高概率稳赚」；不做自动下单/券商对接。
- Phase 1 不做基金级 120 日全量回测（Phase 2 用板块代理 + 抽样基金回测）。
- 不改造日报主流程（激进持仓规则保持现状）。
- 不把 67 个板块全部做成关注方向 chips（扩展为子集 + Top N + 从市场榜带入）。

### 2.3 合规与预期管理

- 文案统一：**「历史同类情景统计 / 非投资建议 / 非抄底承诺」**（与现有 `aggressive_swing_recommendations` 一致）。
- 明示场外基金 **T+1 确认、净值滞后、大额冲击**。
- 大跌过滤排除：规模过小、清盘风险、持续阴跌（近 20 日趋势）、可选新闻负面降权（Phase 2）。

---

## 3. 总体架构

```text
                    ┌─────────────────────────────────────┐
                    │         sector_registry.py          │
                    │  label / market_index / discovery   │
                    │  / holding canonical / aliases      │
                    └──────────────┬──────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
 theme_board_snapshot    discovery_sector_heat      dip_drop_scanner (新)
 sector_canonical        fund-discovery/sectors     GET /api/market/dip-radar
                                   │                         │
                                   └──────────┬──────────────┘
                                              ▼
                              discovery_pipeline (scan_mode=dip_swing)
                              selection_strategy=dip_rebound (默认)
                                              │
                                              ▼
                              DiscoveryReport + outcomes (3d 止盈命中)
```

**数据流（Phase 1 扫描）：**

```text
POST /api/fund-discovery/async { scan_mode: "dip_swing", ... }
  → sector_heat（注册表标签）
  → select_target_sectors（大跌板块优先：近3/5日板块跌幅 Top + focus_sectors）
  → dip_drop_scanner：各板块内按 NAV 近3/5日跌幅预筛 Top M
  → build_candidate_pool（selection_strategy=dip_rebound，池 cap 30）
  → enrich_candidates（nav_trend、rebound_signals）
  → build_discovery_facts（含 fee_break_even、signal_backtest）
  → DeepSeek → discovery_guard → save
```

---

## 4. 板块注册表（方案 A，与抄底功能并行）

### 4.1 新模块 `sector_registry.py`

每条记录：

| 字段 | 说明 |
|------|------|
| `label` | 展示名（如「人工智能」） |
| `aliases` | 如 `军工` ↔ `国防军工` |
| `market_quote` | 主题板块榜用 secid（`_THEME_BOARD_INDEX` 口径） |
| `discovery_quote` | 荐基热度 / 大跌扫描用 secid（`get_quote_canonical_sector` 口径） |
| `holding_quote` | 持仓关联板块（通常同 discovery） |
| `board_kind` | industry / concept / index |
| `discovery_eligible` | 是否进入荐基 chips 候选池 |
| `theme_board_eligible` | 是否在主题涨幅榜 |

**迁移策略：**

1. 从 `theme_board_snapshot._THEME_BOARD_*` 与 `sector_canonical._DISCOVERY_CHIP_LABELS` 生成初始 JSON/Python 表。
2. `list_discovery_sector_labels()` → 读注册表 `discovery_eligible=true`（初始保留原 21，后续可扩）。
3. `_THEME_BOARD_WHITELIST` → 读注册表 `theme_board_eligible=true`。
4. 保留 thin wrapper 在旧模块，避免大范围 import 断裂。

### 4.2 关注方向扩展（Phase 1 范围）

- chips 数据源仍为 `GET /api/fund-discovery/sectors`，但标签来自注册表。
- **扩展规则：** 原 21 固定精选 **+** 用户 session 可选「从主题榜 Top N 动态补充」展示（最多再显示 10 个当日跌幅靠前板块，不与 67 全量铺开）。
- 市场 Tab 行操作（Phase 2 UI）：「加入关注方向」→ `sessionStorage` 写入 `fundpilot-discovery-focus-sectors`（最多 3）。

---

## 5. Phase 1 — Discovery「短线抄底」扫描

### 5.1 模型与 API

**`DiscoveryRequest` 扩展：**

```python
scan_mode: Literal["full_market", "portfolio_gap", "dip_swing"]  # 新增 dip_swing
dip_lookback_days: int = 5          # 3 | 5，默认 5
dip_min_drop_percent: float = 3.0   # 近 N 日累计跌幅门槛，默认 3%
```

- `scan_mode=dip_swing` 时：
  - 默认 `selection_strategy=dip_rebound`（前端锁定或自动切换）
  - 建议联动 `investment_preset=aggressive_swing`（提示但不强制）

**Pipeline 阶段标签新增：**

- `dip_prescreen` → 「预筛大跌基金…」

### 5.2 新服务 `dip_drop_scanner.py`

**职责：** 在给定板块集合内，按基金 NAV 跌幅产出预筛列表。

**输入：** `target_sectors: list[str]`、`lookback_days`、`min_drop_percent`、`exclude_codes`、`per_sector_top`、`budget_seconds`

**数据源：** `fetch_open_fund_rank`（已有）+ `FundDataService` / `nav_trend_summary` 批量拉近 N 日 NAV（与 `enrich_candidates` 共用）

**预筛规则（初版）：**

| 规则 | 阈值 |
|------|------|
| 近 N 日 NAV 累计跌幅 | ≥ `dip_min_drop_percent`（默认 3%） |
| 基金规模 | ≥ 1 亿（复用 `_MIN_SCALE_YI`） |
| 近 1 年涨幅 | ≤ 80%（避免追高后大跌的妖基） |
| 板块匹配 | 主关联板块 / 种子 / 名称关键词（复用 `discovery_candidate_pool`） |
| 排除 | 用户已持仓 code、货币型、清盘标记（若有） |

**输出字段（写入 candidate_pool 条目）：**

```python
{
  "dip_drop_percent": -5.2,           # 近 N 日累计
  "dip_lookback_days": 5,
  "distance_from_high_percent": -12.0,
  "rebound_signals": [                # 规则标签，供 AI/UI
    {"id": "two_day_reversal_up", "label": "近两日先跌后涨"},
    {"id": "sector_stabilizing", "label": "板块跌势放缓"},
  ],
  "rebound_score": 72.5,              # 0-100 启发式，非概率
}
```

**`rebound_score` 启发式（与 `dip_rebound_score` 对齐并扩展）：**

- 大跌深度（近 N 日负收益绝对值）
- 距区间高点空间
- `two_day_reversal_up` / 分时 `intraday_rebound`（有板块分时则加分）
- 板块 `signal_backtest` 命中率（有数据则加权）
- 持续阴跌惩罚（近 10 日全负）

**与现有 `build_candidate_pool` 关系：**

- `scan_mode=dip_swing`：`dip_drop_scanner` 先产出 **per-sector Top 8**，再 merge → cap **30** → 再走 `enrich_candidates`。
- 其他 scan_mode：行为不变。

### 5.3 `select_target_sectors` 扩展

`dip_swing` 模式板块选择：

1. 用户 `focus_sectors`（最多 3）优先；
2. 其余名额按 **板块近 5 日跌幅** 排序（`sector_heat.change_5d_percent` 升序），取跌最深者；
3. 默认 `max_sectors=8`（与 full_market 一致）。

### 5.4 Discovery Facts / Prompt / Guard

**`build_discovery_facts` 新增块：**

```python
"dip_swing": {
  "lookback_days": 5,
  "min_drop_percent": 3.0,
  "fee_break_even_percent": 2.5,      # take_profit_threshold_percent(profile)
  "target_exit_days": profile.hold_days_target or 5,
  "pool_prescreen_stats": { "candidates": 30, "avg_drop": -4.1 },
}
```

**`discovery_prompt.DEFAULT_DISCOVERY_ROLE_PROMPT` 增补：**

- `scan_mode=dip_swing`：必须说明持有 2～5 天、达扣费止盈线考虑卖出；强调大跌≠利好，需结合 `rebound_signals`。
- 推荐动作优先：`分批买入` / `建议关注` / `等待回调`；避免「重仓抄底」措辞。

**`discovery_guard`：**

- `dip_swing` 下：近 1 日 NAV 仍大跌且无企稳信号 → 降档为 `建议关注`。
- 保留激进模式对追高的放宽，但 `dip_swing` 仍拒绝「近 1 日涨幅 > 3%」的候选（避免追涨）。

### 5.5 推荐报告字段扩展

**`DiscoveryRecommendation` 可选新字段：**

| 字段 | 说明 |
|------|------|
| `target_exit_days` | 建议持有天数上限（2～5） |
| `fee_break_even_percent` | 扣费打平所需涨幅 |
| `dip_drop_percent` | 入选时近 N 日跌幅 |
| `rebound_signals` | 信号标签列表 |

前端 `DiscoveryReportPanel` 卡片展示上述字段；无数据则不展示。

### 5.6 复盘指标扩展

**`discovery_outcomes.py`：**

- 新增 `hit_take_profit_within_days`：推荐日后 **3 个交易日内** NAV 涨幅是否 ≥ `fee_break_even_percent`。
- 汇总文案：「3 日内达扣费止盈线 X/Y（历史统计，不代表未来）」。

**`build_discovery_recommendation_accuracy`：** 可选增加 30 日滚动 `dip_swing` 子样本命中率（样本不足时不展示）。

### 5.7 前端（Phase 1）

**`FundDiscoveryPanel`：**

1. 扫描模式新增第三项：**「短线抄底」**（`dip_swing`）
   - hint：「近几日大跌、有反弹信号的场外基金；默认 2～5 天波段」
2. 选中时：自动 `selectionStrategy=dip_rebound`；展示激进预设提示条（一键切换 `aggressive_swing`）。
3. 高级折叠（可选）：`回看天数` 3/5、`最小跌幅` 3%/5%。
4. 关注方向 chips：数据源改注册表；额外展示「今日跌深板块」动态标签（Top 5，来自 sector_heat 5d 升序）。

**不新增独立 Tab**（Phase 1）。

---

## 6. Phase 2 — 「大跌反弹雷达」

### 6.1 入口与布局

- **位置：** 市场 Tab 新增子 Tab **「大跌雷达」**（与「主题板块 | 美股」并列），或推荐基金 Tab 顶部卡片链入（二选一实现时优先市场 Tab，曝光更自然）。
- **组件：** `DipReboundRadar.tsx`

### 6.2 API

**`GET /api/market/dip-radar`**

Query：

| 参数 | 默认 | 说明 |
|------|------|------|
| `lookback_days` | 5 | 3 或 5 |
| `sector` | — | 可选，注册表 label 过滤 |
| `limit` | 20 | 榜单长度 |
| `force_refresh` | false | 跳过缓存 |

Response：

```python
{
  "refreshed_at": "ISO",
  "trade_date": "YYYY-MM-DD",
  "lookback_days": 5,
  "fee_break_even_percent": 2.5,   # 若请求带 profile 则个性化；否则默认
  "items": [
    {
      "fund_code": "015945",
      "fund_name": "...",
      "sector_label": "商业航天",
      "dip_drop_percent": -6.8,
      "change_1d_percent": -1.2,
      "rebound_score": 74,
      "rebound_signals": [...],
      "historical_hint": {         # Phase 2b
        "sample_days": 120,
        "rebound_rate_3d_percent": 58.0,
        "note": "板块代理统计，非单基承诺"
      }
    }
  ],
  "sector_dip_leaders": [...]      # 板块跌幅 Top 5，链到筛选
}
```

**缓存：** `dip:radar:v1:{trade_date}:{lookback}`，盘中 60s / 收盘 3600s；后台不与 theme board 同线程（请求时按需构建，避免拖慢现有刷新）。

### 6.3 历史参考（Phase 2b，同 Phase 2 交付）

**新服务 `fund_dip_rebound_backtest.py`（轻量）：**

- 首期用 **板块指数** 代理：当板块近 N 日跌幅 ≥ 阈值时，统计未来 3 日指数反弹 ≥ `fee_break_even` 的比例（复用 `sector_daily_kline_provider`）。
- 榜单条目 `historical_hint` 展示板块级命中率；UI 脚注说明「基金走势可能与板块偏离」。
- 后续迭代：对 Top 基金抽样做 NAV 级回测（非 MVP 阻塞）。

### 6.4 交互

| 操作 | 行为 |
|------|------|
| 点击基金行 | 打开现有 `YangjibaoFundDetail` 预览 / 基金详情 |
| 「深度扫描」 | 跳转推荐基金 Tab，`scan_mode=dip_swing`，`focus_sectors` 预填该基金板块 |
| 主题板块行「看大跌基金」 | 带 `sector` 过滤打开雷达 |
| 关注方向「从榜加入」 | 写入 sessionStorage，最多 3 个 |

### 6.5 与波段信号关系

- Phase 2 **不替代** `SwingAlertsPanel`（持仓止盈仍走原链路）。
- 可选：雷达中标记「已在持仓」；不推荐对已持仓重复「新基」叙事。

---

## 7. 文件清单（实现时参考）

### 7.1 新增

| 文件 | 职责 |
|------|------|
| `apps/api/app/services/sector_registry.py` | 板块注册表 |
| `apps/api/app/services/dip_drop_scanner.py` | 大跌预筛 |
| `apps/api/app/services/fund_dip_rebound_backtest.py` | Phase 2 历史命中率 |
| `apps/api/app/services/dip_radar_snapshot.py` | 雷达缓存与组装 |
| `apps/web/src/components/DipReboundRadar.tsx` | Phase 2 UI |
| `apps/web/src/lib/dipRadar.ts` | 格式化 helper |
| `apps/api/tests/test_dip_drop_scanner.py` | 预筛单测 |
| `apps/api/tests/test_dip_radar.py` | 雷达 API 单测 |
| `apps/api/tests/test_sector_registry.py` | 注册表一致性 |

### 7.2 修改

| 文件 | 变更 |
|------|------|
| `models.py` | `scan_mode` + `dip_*` 字段；`DiscoveryRecommendation` 扩展 |
| `discovery_pipeline.py` | 接入 dip_prescreen |
| `discovery_candidate_pool.py` | dip_swing 分支 |
| `discovery_target_sectors.py` | dip_swing 板块选择 |
| `discovery_facts.py` / `discovery_prompt.py` / `discovery_guard.py` | 短线抄底语义 |
| `discovery_outcomes.py` | 3 日止盈命中 |
| `theme_board_snapshot.py` / `sector_canonical.py` | 读注册表 |
| `main.py` | `GET /api/market/dip-radar` |
| `FundDiscoveryPanel.tsx` / `MarketTab.tsx` / `api.ts` | 前端 |
| `PROJECT_CONTEXT.md` | 能力清单与 API 表 |

---

## 8. 测试策略

| 层级 | 内容 |
|------|------|
| 单测 | `dip_drop_scanner` 跌幅排序、过滤、rebound_score；`dip_swing` target_sectors 选板块；registry 别名与 secid；outcomes 3 日止盈命中 |
| API 冒烟 | `POST fund-discovery/async` scan_mode=dip_swing；`GET dip-radar` |
| 回归 | 原 `full_market` / `portfolio_gap` / `balanced` 行为不变 |
| 前端 | vitest helper；Playwright 选短线抄底模式 → 扫描不报错 |
| 离线 | conftest stub `dip_drop_scanner` / `dip_radar` 网络 |

---

## 9. 发布顺序

| 里程碑 | 交付物 | 可独立上线 |
|--------|--------|------------|
| **M0** | `sector_registry` + 原行为回归 | ✅ |
| **M1 / Phase 1** | `dip_swing` 扫描 + outcomes 3 日指标 + 前端扫描模式 | ✅ |
| **M2 / Phase 2a** | `GET /api/market/dip-radar` + `DipReboundRadar` UI | ✅ |
| **M3 / Phase 2b** | 板块代理历史命中率 + 市场榜/雷达联动关注方向 | ✅ |
| **M4** | 关注方向动态 Top N + 从主题榜带入 | 可并入 M1/M3 |

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 全市场 NAV 拉取慢 | 先 rank 表筛板块内基金，并行有 budget；超时降级为板块级提示 |
| 大跌基金基本面暴雷 | 规模门槛 + 持续阴跌惩罚 + Phase 2 新闻降权 |
| 用户误解为承诺收益 | 文案 + `rebound_score` 称「信号强度」非「概率」；历史命中率标注样本量 |
| secid 不一致 | 注册表分 `market_quote` / `discovery_quote`，测试锁定 |
| AI 幻觉新基 | 仍强制 `candidate_pool` 白名单 + `discovery_guard` |

---

## 11. 验收标准（用户可测）

### Phase 1

- [ ] 推荐基金可选「短线抄底」，扫描生成报告；候选含 `dip_drop_percent` / `rebound_signals`
- [ ] 激进预设下报告展示扣费止盈线与建议持有天数
- [ ] 7 日复盘显示「3 日内达止盈线」统计
- [ ] 原「全市场机会」「持仓缺口」扫描不受影响

### Phase 2

- [ ] 市场 Tab「大跌雷达」展示基金大跌榜（默认近 5 日）
- [ ] 可按板块筛选；点击「深度扫描」跳转推荐基金并预填参数
- [ ] 展示板块代理历史反弹参考（含免责声明）
- [ ] 主题板块行可「看大跌基金」或「加入关注方向」

### 板块注册表

- [ ] 主题板块 67、关注方向 21 与迁移前 secid 行为一致（回归测试）
- [ ] 关注方向可展示额外「今日跌深板块」动态标签（≤5）

---

## 12. 竞品参考（简）

| 产品 | 相关能力 | 好基灵差异化 |
|------|----------|--------------|
| 小倍养基 | 板块涨幅榜、板块选基 | 基金级大跌榜 + 扣费止盈线 + AI 窄池报告 |
| 天天基金 | 跌幅榜、主题资金 | 与持仓/风控/波段预设打通，非纯榜单 |
| 支付宝 | 涨跌排行 | 强调 OCR 持仓上下文 + 新基窄池 + 复盘闭环 |

---

**请审阅本文档。** 确认后进入 `docs/superpowers/plans/2026-06-21-dip-swing-discovery.md` 实现计划，按 M0→M1→M2→M3 分步开发与自测。
