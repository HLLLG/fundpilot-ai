# AI 决策"更准更果断"升级 — 设计方案

**状态：** 设计决策 1~4 已确认（见第 10 节），第 5 项（灰度周期细节）讨论中；整体仍处设计阶段，未实现
**范围：** 日报（生成日报 Tab）+ 推荐基金（荐基 Tab），共享底层信号与守卫基础设施
**关联文档：** `docs/PROJECT_CONTEXT.md`（2026-07-01「日报对齐荐基决策能力」一节是本次升级的直接前置基础）

---

## 0. 背景

用户实录：某日看到「半导体材料」板块主力资金净流出较多，但板块仍微涨 0.5%，日报给出的决策是「观察」，用户据此没有操作；次日该板块几乎跌停，造成较大损失。用户认为当前喂给 DeepSeek 的数据、提示词和 guard 守卫都不够支撑模型做出更聪明、更犀利（果断）的决策，希望能"根据已有资料推断下一交易日大致走势，给出更正确的决策"。

本方案在完整阅读现有日报/荐基决策链路代码后，定位到 5 个具体机制性根因（见第 2 节），并结合竞品与 2026 年学术研究（第 3 节）给出改进设计。

---

## 1. 现状架构（as-is）

```
build_analysis_facts()                     # app/services/analysis_facts.py
  ├─ sector_momentum / sector_intraday      # 板块动量、分时形态
  ├─ sector_fund_flow_context               # 板块资金流 + 量价模式分类（distribution/accumulation/…）
  ├─ sector_opportunity_scoring             # 板块方向机会分（momentum顺势/setup蓄势双轨）
  ├─ sector_signal_backtest                 # 4条规则的 T→T+1 历史回测（reversal_down等）
  ├─ market_flow_client                     # 南向资金摘要
  └─ factor_scores / risk_metrics / evidence(三路量化证据综合置信)
       ↓
trim_analysis_facts_for_llm()               # app/services/analysis_payload.py，按 fast/deep 裁剪
       ↓
DEFAULT_ROLE_PROMPT + OUTPUT_REQUIREMENTS_SYSTEM   # app/services/analysis_prompt.py
       ↓
DeepSeek 单次 chat.completions（JSON 输出）  # app/services/deepseek_client.py
       ↓
apply_recommendation_guards()               # app/services/recommendation_guard.py —— 仅单向降级
       ↓
judge_parsed_report()                       # app/services/report_judge.py —— 规则审校 + deep模式可选LLM审校（只对齐facts，不做多空校验）
```

荐基（`discovery_pipeline.py` / `discovery_guard.py`）复用同一套 `sector_opportunity_scoring.py` / `decision_guard_shared.py`，结构完全平行。

---

## 2. 根因定位（对照用户案例逐条验证过代码）

| # | 根因 | 代码位置 | 说明 |
|---|------|----------|------|
| 1 | **Guard 只会降级、不会升级** | `recommendation_guard.py::apply_recommendation_guards` | `_weak_evidence_reasons` 只把"分批加仓"降级为"观察"；如果 LLM 本来就给"观察"，即使命中 `distribution`（涨但资金流出）模式，系统不会把它升级为"暂停追涨/减仓评估"。守卫是单向的，只防乐观、不防迟钝。 |
| 2 | **板块方向置信度永远到不了"高"** | `sector_opportunity_scoring.py::_confidence()` | 该函数只有两个返回值分支：`"低"`（数据不可用/未对齐）和 `"中"`（其余全部情况，无论证据多强）。而 prompt 规则要求"中"只能措辞保留、不能作主理由——机制上就堵死了"果断"的可能性。 |
| 3 | **"量价背离"模式没有被历史回测验证过命中率** | `sector_fund_flow_context.py::_classify_flow_pattern` vs `sector_signal_backtest.py` | 系统已经有一套很好的"信号回测 + 显著性打分"基础设施（`sector_signal_backtest.py` 回测 4 条纯K线规则的 T+1 命中率），但唯独没把"当日资金流方向 vs 涨跌方向背离"（用户踩坑的这个模式）纳入回测。LLM 看到的只是定性提示"警惕高位出货"，没有"这个板块过去 N 次背离、次日下跌概率 X%、超基准 Y 个百分点"这种可以让它有底气果断行动的量化依据。 |
| 4 | **缺少大盘情绪这层自上而下信号** | 全链路 | 现有信号都是板块级/个股级（资金流、动量、回测），没有涨停/跌停家数、连板高度、炸板率、两融余额这类反映当日全市场风险偏好骤变的指标。用户这次"板块微涨但次日近乎跌停"，很典型的诱因之一是当日/隔夜大盘整体转冷，这不是单一板块资金流能提前完全捕捉的。 |
| 5 | **决策动作词表偏软、没有具体仓位** | `analysis_facts.py` 的 `allowed_actions` | 只有「观察/暂停追涨/分批加仓/减仓评估/风控复核」5个模糊动词，没有仓位百分比，"减仓评估"本身就是一个"再等等看"的词，不够犀利。 |

---

## 3. 竞品与学术调研结论

### 3.1 竞品/行业实践

| 产品/项目 | 关键设计 | 对本方案的启示 |
|---|---|---|
| **TradingAgents**（开源，AAAI 2025 workshop，GitHub Tauric Research） | 模拟真实机构：基本面/技术面/情绪面分析师 → 多头/空头研究员**辩论** → 研究经理综合 → 交易员出方案 → **风控经理/组合经理复核放行**。原生支持 DeepSeek。 | "先辩论、风控角色能一票升级/否决"的架构值得借鉴，但完整多智能体多轮辩论成本高，本方案先落地"单角色反思版"（见 M3）。 |
| 国内"寒鸦四维决策框架"类自媒体量化框架、韭圈儿等 | 「宏观定调 / 微观验证（资金+龙虎榜）/ **情绪水位**（涨跌停家数、连板高度、炸板率）/ 风控矩阵（仓位建议：空仓/轻仓/半仓/重仓）」四维结构。 | 情绪水位是判断"次日是否转弱"的核心先行指标，正是我们目前缺的一环（根因4）；仓位建议要具体档位，不能只给动词（根因5）。 |
| 蚂蚁支小宝 2.0 | 持仓诊断+行情分析，结合用户画像、近期资金申赎动态做个性化建议。 | 印证"结合持仓上下文做个性化解读"的方向正确，我们已经在做（`analysis_facts` 逐持仓证据），本方案重点补的是"信号强度"和"决策骨气"，不是重新做个性化。 |

### 3.2 学术研究（2026）：为什么"调 prompt 让 AI 更聪明"这条路走不通

- 《On the Limits of Prompt Repetition》（2026）：系统评测发现 prompt 层面的技巧（如重复 prompt 提升自信度）对金融时序预测**没有统计显著提升**——噪声主导的领域里，prompt 工程不能凭空产生信息增量，LLM 原生给出的概率估计校准度也很差。
- 《Reasoning through Verifiable Forecast Actions》(Stock-R1, 2026)：有效路径是把"预测"这件事**量化、可验证、可回测**，再让模型的推理去消费这些数字，而不是让模型自己"想出"一个前瞻性判断。
- TrustTrade / PolySwarm（2026）：多智能体架构里，通过**交叉一致性**（多个独立视角/采样是否收敛）来抑制单次输出的过度自信或分歧噪声，而不是让单个模型"更自信地说话"。

**结论与本方案的立场完全一致**：不靠"把 prompt 写得更凶"，而是（a）把"量价背离"这类模式纳入历史回测拿到统计显著性、（b）补充大盘情绪数据、（c）把 guard 从"只拦乐观"改成双向、（d）让动作和仓位更具体——这恰好延续了本项目自己已经验证有效的"`signal_confidence.py` 量化打分喂 LLM"哲学，只是要**扩大覆盖面**，而不是发明新范式。

---

## 4. 设计目标 / 非目标

### 目标

1. 新增至少一类有历史统计显著性支撑的"量价背离"信号，并修复 confidence 机制，让证据极强时能真正给到"高"档位。
2. 新增"大盘情绪温度计"数据层（涨跌停家数/炸板率/连板高度/两融），同样纳入历史回测验证显著性。
3. Guard 从单向降级改为**双向**：证据强烈指向风险时，即使 LLM 给"观察"，系统也能强制升级为"暂停追涨"甚至新增的"大幅减仓评估/清仓评估"。
4. 决策动作附带**系统计算**的具体仓位调整百分比，而不是纯文字。
5. deep 模式引入"风控复核角色"二次校验（轻量版反方校验，不做重量级多智能体）；fast 模式用**零额外 LLM 调用**的双向规则守卫达到同等的"底线兜底"效果。
6. 荐基（discovery）同步获得同一套信号与置信修复（共享基础设施自动受益），guard 双向逻辑按荐基自身语义（推荐/回避新基金，而非清仓已持仓）落地。
7. 前端呈现新证据与更强警示，同时保持"不构成投资建议"的合规表述。

### 非目标（本轮不做）

- 不做实盘自动交易/下单对接。
- 不做个股级别选股引擎（龙虎榜等数据只做board级粗粒度信号，不做逐股票分析）。
- 不做真正意义上的多智能体多轮并发辩论（成本/延迟过高），先上线"单角色反思版"验证有效性。
- 不改动 OCR/账户同步等与本次决策链路无关的模块。

---

## 5. 总体架构（to-be）

```
┌─ 数据/信号层（M1）───────────────────────────────────────────┐
│ market_breadth_signal.py（新）        —— 大盘情绪温度计         │
│ board_fund_flow_history.py（扩展窗口）—— 板块历史资金流全序列   │
│ sector_flow_divergence_backtest.py（新）—— 量价背离 T+1 回测   │
│ sector_opportunity_scoring.py（修复confidence上限）            │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌─ 事实装配层 ───────────────────────────────────────────────┐
│ analysis_facts.py 新增 market_breadth / flow_divergence 字段  │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌─ Prompt 层 ────────────────────────────────────────────────┐
│ analysis_prompt.py / analysis_payload.py 更新护栏与新字段说明  │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌─ 生成层（M3）──────────────────────────────────────────────┐
│ fast：单次生成 + 双向规则 guard（零新增LLM调用）                │
│ deep：单次生成 →【风控复核角色】二次LLM校验 → 双向规则 guard     │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌─ 决策/守卫层（M2）─────────────────────────────────────────┐
│ decision_guard_shared.py 新增 resolve_escalation_floor()      │
│ recommendation_guard.py / discovery_guard.py 双向接入          │
│ 新增仓位百分比字段 suggested_position_change_percent           │
│ 动作词表扩展：+大幅减仓评估 +清仓评估                           │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌─ 展示层（M5）──────────────────────────────────────────────┐
│ 情绪温度计卡片 / 背离回测证据行 / 仓位建议可视化 / 复核前后对比   │
└──────────────────────────────────────────────────────────────┘

      贯穿：M6 验证与灰度（历史回放校准 + shadow 模式 + 命中率复盘）
```

---

## 6. 模块详细设计

### M1　数据与信号层

#### M1.1 大盘情绪温度计 `app/services/market_breadth_signal.py`（新文件）

数据源（均为 AkShare 免费接口，走 subprocess 隔离，参照 `market_flow_client.py` 现有子进程模式）：

| 指标 | AkShare 接口 | 说明 |
|---|---|---|
| 涨停家数/跌停家数/炸板率 | `stock_zt_pool_em` / `stock_zt_pool_dtgc_em` / `stock_zt_pool_zbgc_em` | 情绪水位核心指标 |
| 连板高度 | `stock_zt_pool_em` 里 `连板数` 列聚合最大值 | 反映资金敢不敢接力 |
| 两融余额环比 | `stock_margin_sse` + `stock_margin_szse` | 杠杆资金偏好，披露有 T-1 延迟，需标注 `as_of_date` |

输出契约（挂进 `analysis_facts["market_breadth"]`）：

```json
{
  "available": true,
  "trade_date": "2026-07-02",
  "limit_up_count": 32,
  "limit_down_count": 41,
  "limit_up_broken_ratio_percent": 38.5,
  "max_consecutive_boards": 3,
  "margin_balance_change_yi": -12.4,
  "sentiment_level": "冰点",
  "sentiment_level_change": -2,
  "interpretation": "跌停家数超过涨停家数且炸板率偏高，市场情绪偏冷，短线宜降低仓位敏感度。"
}
```

- 情绪分级（冰点/低迷/中性/偏热/亢奋）初版用简单规则（如 `limit_up_count - limit_down_count` 分档 + 炸板率阈值），**具体阈值需要用近 1-2 年历史数据分布校准**，本设计只给结构，不锁死数值（"先测算再定阈值"的方式已获确认，见第 10 节）。
- 全程 best-effort：复用 `_run_budgeted_enhancement` 模式接入 `analysis_facts.py` 的并发预算池，新增 `MARKET_BREADTH_TIMEOUT_SECONDS`，超时/失败返回 `available=false`，不阻塞日报。
- 缓存：全市场共享（与用户无关），复用 `sector_quote_cache` 的 spot snapshot 模式，盘中/收盘 TTL 参照 `market_flow_client._flow_cache_ttl_seconds()`。

#### M1.2 板块历史资金流窗口确认

`board_fund_flow_history.fetch_board_flow_series` 当前用 `lmt=0`（东财接口返回其允许的全部历史，实测通常在 90~120 个交易日量级，需要在实现阶段实测确认真实天数）。`get_cached_board_flow_series` 已经返回完整序列（不只是 `week/month` 切片），可以直接复用做回测输入，**不需要新增抓取逻辑**，只需要新增一个"批量预热 + 与K线按日期对齐"的工具函数。

#### M1.3 量价背离信号回测 `app/services/sector_flow_divergence_backtest.py`（新文件）

现有 `sector_signal_backtest.py` 的回测引擎只吃单一K线序列（`_evaluate_rules` 签名只接收 `prev_change/cur_change/high_change`）。量价背离需要**两条对齐的时间序列**（K线涨跌 + 资金流方向），签名不兼容，因此新开一个文件，复用相同的统计口径（`_direction_fractions` / `_baseline_prob` / `_finalize_bucket` 这套"自然基准 + edge + 显著性"逻辑可以从 `sector_signal_backtest.py` 抽出公共函数复用，避免两套阈值/口径漂移）：

```python
def backtest_flow_price_divergence(
    board_code: str,
    kline_series: list[DailyKlineBar],
    flow_series: list[dict],
    *,
    lookback_days: int = 100,
) -> dict[str, Any]:
    """按日期对齐K线与资金流，对 `_classify_flow_pattern` 判定出的
    distribution / accumulation 模式做 T→T+1 回测，复用
    sector_signal_backtest 的统计显著性口径（自然基准+edge+触发数门槛）。
    """
```

输出结构与现有 `by_rule["reversal_down"]` 完全对齐（`trigger_count/hit_rate_percent/baseline_rate_percent/edge_percent/significant`），这样可以直接复用 `signal_confidence.py::score_signal` 与前端 `SectorSignalBacktestPanel` 的现有展示逻辑，只需要把新规则 id（如 `flow_price_divergence`）加入常量列表。

#### M1.4 修复 confidence 机制上限

`sector_opportunity_scoring.py::_confidence()` 增加"高"档位路径：

```python
def _confidence(flow, date_aligned, penalties, divergence_backtest=None) -> str:
    if not flow or not flow.get("available"):
        return "低"
    if not date_aligned:
        return "低"
    if divergence_backtest and divergence_backtest.get("significant") and (divergence_backtest.get("edge_percent") or 0) >= 10:
        return "低" if penalties and "资金背离或持续流出" not in penalties else "高"
    return "中" if not penalties else "中"
```

即：只有当"量价背离"这一新回测规则同时满足 `significant=True` 且 `edge_percent>=10` 时，置信度才能被打到"高"——**证据强度决定档位，而不是机制性封顶**。这一路径同时用于日报（`report_sector_opportunity.py`）和荐基共享。

---

### M2　决策与守卫层

#### M2.1 双向 guard：`decision_guard_shared.py` 新增共享升级判定

```python
def resolve_escalation_floor(
    *,
    sector_opportunity: dict | None,
    evidence: dict | None,
    market_breadth: dict | None,
    over_concentration: bool,
    has_unrealized_gain: bool,
    decision_style: str,
) -> dict[str, Any]:
    """返回 {min_bucket, reasons, suggested_position_change_percent, basis}。
    min_bucket 与 recommendation_guard/discovery_guard 现有的 _action_bucket
    同义（越低越保守）；调用方在 LLM 动作 bucket 高于 min_bucket 时强制下调。
    """
```

升级触发矩阵（方向已获确认，见第 10 节；初始建议值，**具体数字需在 M6 用历史回放校准，本设计不锁死**）：

| 触发条件组合 | 最低动作档位 | 建议仓位调整 |
|---|---|---|
| 量价背离显著（`significant=true`）+ `sector_opportunity.opportunity_available=false` | 暂停追涨 | — |
| 上一条 + `evidence.composite.level` 为低/不足 | 减仓评估 | 20%~30% |
| 上一条 + 该持仓当前浮盈>0（落袋压力更小） | 减仓评估（下限提高） | 30%~40% |
| 上一条 + `market_breadth.sentiment_level`=冰点 且较前一交易日下降≥2档 + 该持仓集中度超限 | **大幅减仓评估**（新） | 40%~60% |
| 多重强信号极端共振（背离 edge≥15 + 情绪冰点 + 已破位) | **清仓评估**（新） | 100%（全部赎回） |

- `decision_style` 只影响门槛松紧系数（tactical/aggressive 更容易触发强动作），**不是触发的必要条件**——conservative 风格下证据极强时同样可以触发"大幅减仓评估/清仓评估"（对应你确认的"如果判断准确，也可以激进"）。
- 该函数只产出"下限"（下限=更保守的方向），不产出"上限"——上限（防止过度乐观追涨）沿用现有 `_max_allowed_bucket` 逻辑，两者共同构成真正的双向约束。

#### M2.2 决策动作词表扩展

`allowed_actions` 由 5 个扩展为 7 个：观察 / 暂停追涨 / 分批加仓 / 减仓评估 / **大幅减仓评估** / **清仓评估** / 风控复核。

- 新增两档均保留"评估"后缀（不做实盘指令，符合产品边界"不做实盘交易指令"）。
- `_ACTION_BUCKET` 扩展映射（减仓评估=0、大幅减仓评估=-1、清仓评估=-2，或等价地用一个 0~4 的连续 bucket 表示强度而不是离散名字），`recommendation_guard.py` / `discovery_guard.py` 的 bucket 判定逻辑同步扩展。
- 这两档新动作**必须**由 M2.1 的触发矩阵门槛控制是否出现在 `allowed_actions` 里传给 LLM（即：没有强证据共振时，prompt 里根本不出现"清仓评估"这个选项，避免被滥用/误用吓退用户），只有当天确实命中高门槛时才向 LLM 开放这两个选项并同时启用 guard 强制下限。

#### M2.3 仓位建议结构化

`FundRecommendation` / `DiscoveryRecommendation` 新增：

```python
suggested_position_change_percent: float | None = None   # 正=建议加仓比例，负=建议减仓比例（相对当前持仓金额）
suggested_position_change_basis: str = ""                 # 引用具体证据（复用 humanize_evidence_text）
```

数值由 **guard 按 M2.1 的规则表计算并回填**，LLM 即使给出自己的数字也会被规则表覆盖（沿用项目"LLM 负责解释、系统负责算数"的既有哲学，与现有 `amount_yuan`/`weight_denominator` 的处理方式一致）。

---

### M3　生成与复核流程

#### M3.1 fast 模式：零新增 LLM 调用

fast 模式直接复用 M2 的双向 guard 作为唯一"复核"手段，不新增 LLM 调用，保持现有速度基本不变（新增的只是本地计算，成本≈0）。这满足"fast 模式保持轻快"的产品定位，同时通过双向 guard 兜底解决用户遇到的核心问题（即使模型给"观察"，系统兜底也会强制升级）。

#### M3.2 deep 模式：新增"风控复核角色"二次校验

改造现有 `report_judge._llm_judge`（当前只做"对齐 facts、修正矛盾"），新 prompt 明确要求扮演"风控经理"角色：

```
你是风控经理，正在复核基金经理草拟的日报建议。
输入：草案 fund_recommendations + facts（含新增的量价背离回测、大盘情绪信号）
     + 系统已计算的最低动作档位（M2.1 的 resolve_escalation_floor 结果）。
任务：
1. 对草案中「观察/分批加仓」的建议，检查是否忽视了强空头证据
   （量价背离significant、板块方向不构成机会、情绪骤冷）；若忽视须调整并说明理由。
2. 对草案中「减仓评估」及以上的建议，检查是否有被忽视的强多头证据，
   避免反向过度悲观（双向校验，不是只能变得更悲观）。
3. 最终动作不得比系统计算的最低动作档位更宽松（硬约束优先，不能绕过风控红线）。
仅输出完整 JSON，结构同 draft_report。
```

- 复用现有 `LLM_JUDGE_TIMEOUT_SECONDS` 预算机制，超时/失败自动降级为规则 guard 的结果，不阻塞日报（延续现有鲁棒性设计）。
- 成本增量：与当前 deep 模式已有的 `_llm_judge` 调用次数**相同**（1次生成 + 1次复核），只是把复核 prompt 从"单纯对齐 facts"升级为"真正的反方校验角色"，**不增加 deep 模式当前的延迟量级**。
- 是否要升级为真正的多智能体多轮辩论（Bull/Bear 分开两次独立调用再合并），**已确认先不做**（见第 10 节），先上线"单角色反思版"验证有效性，成本/收益比更明确后再评估。

---

### M4　荐基（Discovery）同步

- M1 全部信号是共享模块（`sector_opportunity_scoring.py`），荐基自动受益，无需重复开发。
- `discovery_guard.py` 接入 `decision_guard_shared.resolve_escalation_floor`，但**荐基的双向语义与日报不同**：荐基推荐的是"要不要买入新基金"，不涉及"清仓已持仓"，因此荐基侧的升级方向是——
  - 强负面证据共振时：从"建议关注"直接降级为**从候选池剔除**（而不是给一个"清仓"类动作，荐基本来就没有这个语义）；
  - 强正面证据共振时（如 accumulation 模式 + 背离回测显著 + 情绪回暖）：允许"分批买入"给到比现在更高的建议金额（当前 `discovery_guard` 对 `suggested_amount_yuan` 只有"不超预算"的上限约束，没有"证据强时可以更积极"的下限/建议逻辑）。
- `discovery_prompt.DEFAULT_DISCOVERY_ROLE_PROMPT` 与其 `OUTPUT_REQUIREMENTS` 同步新增证据引用要求（对齐日报 `analysis_prompt.py` 的写法）。
- deep 模式荐基同步接入 M3.2 的风控复核角色（复用同一个 prompt 模板，替换"清仓/减仓"相关措辞为"剔除/降低建议金额"）。

---

### M5　前端展示

| 组件 | 变更 |
|---|---|
| `SectorOpportunityCard.tsx` | 新增"历史回测证据"行：如"过去 98 个交易日量价背离出现 18 次，次日下跌概率 72%，超基准 22pp" |
| 新增 `MarketBreadthGauge.tsx` | 情绪温度计卡片（涨跌停家数/炸板率/连板高度 + 情绪等级），挂在市场 Tab 和日报诊断区 `DiagnosticsAccordion` |
| `ReportPanel.tsx` FundRecommendationCard | 新增"建议仓位变化"数字展示（百分比+方向图标，比纯文字 `amount_note` 更醒目） |
| 复核前后对比 | 新增 caveat 展示模式："风控复核已将该建议由『观察』上调为『暂停追涨』，理由：…"（复用现有 note/caveats 展示风格，不额外展示完整"辩论过程"，避免 UI 噪音） |
| 动作徽标 | "大幅减仓评估"「清仓评估」使用比现有"减仓评估"更强调的配色（如深红/警示条纹），并在前端加一次二次确认展开（已确认，见第 10 节） |
| `DiscoveryCandidatePoolPanel.tsx` | 展示"证据强度剔除"的板块（说明为何某板块的基金没有进入候选） |
| 新增 `ShadowEscalationDigestCard.tsx` | 灰度期间的"本周复盘摘要"卡片（触发次数/涉及板块/建议动作/次日实际走势对照），挂在诊断区，仅 `DECISION_ESCALATION_MODE=shadow` 时展示（见 M6.3） |

---

### M6　验证、灰度与度量

1. **先测基线**：在改动前，用现有 `recommendation_accuracy.py` / `recommendation_outcomes.py` 数据跑一次"如果当时用新规则会如何"的离线回放，建立"该谨慎未谨慎"（观察类建议后次日板块跌幅超过阈值的比例）与"该乐观未乐观"（减仓类建议后次日反弹超过阈值的比例）两个基线指标。
2. **历史回放校准信号显著性**：M1.3/M1.1 的新信号必须先用近 60~120 个交易日历史数据跑通回测引擎，确认 `edge_percent≥5` 且 `trigger_count≥30`（对齐现有 `MIN_TRIGGERS_FOR_SIGNIFICANCE`/`EDGE_MIN_PERCENT`）；若某信号验证后不显著，**仍上线但标注"不足"置信、不参与 M2.1 的强制升级判定**（诚实划界，与项目现有因子 IC/风格回归模块的处理方式一致）。
3. **灰度（shadow 模式）**：新增配置 `FUND_AI_DECISION_ESCALATION_MODE=shadow|enforced`（默认 `shadow`）。shadow 模式下，M2.1 触发的升级只写入 `validation_notes`/caveats（"若启用新版守卫，此条建议会被系统升级为 XX"），不真正改变最终 action；观察 1 个月（约 20 个交易日）后由用户本人决定是否切换 `enforced`（见第 10 节）。
4. **M6.3 灰度复盘摘要**（新文件 `shadow_escalation_digest.py`）：扫描近 7 天报告记录中带 shadow 升级标记的 `validation_notes`/caveats，按板块/触发规则聚合出"触发次数、建议升级动作分布、次日实际涨跌对照"（复用 `recommendation_outcomes.py` 的次日结果数据），通过只读接口 `GET /api/admin/shadow-digest` 暴露，前端新增 `ShadowEscalationDigestCard.tsx` 展示最近一次摘要，供用户每周查看后判断是否提前/按期切换 `enforced`（见第 10 节）。
5. **单测**：新回测规则（已知答案+历史注入）、confidence 修复分支、guard 双向升级路径、荐基剔除逻辑、灰度复盘聚合逻辑、前端新组件，均按项目现有测试模式（如 `test_sector_signal_backtest.py`、`test_recommendation_guard_evidence.py`）补充用例。

---

## 7. 数据契约变更一览

| 位置 | 新增字段 |
|---|---|
| `analysis_facts` | `market_breadth`（大盘情绪）、`holdings[].flow_divergence_backtest`（该持仓板块的量价背离回测结果） |
| `FundRecommendation` / `DiscoveryRecommendation` | `suggested_position_change_percent`、`suggested_position_change_basis` |
| `allowed_actions` | 新增 `大幅减仓评估`、`清仓评估`（按门槛条件动态出现） |
| 配置项（`app/config.py`，前缀 `FUND_AI_`） | `MARKET_BREADTH_ENABLED`、`MARKET_BREADTH_TIMEOUT_SECONDS`、`FLOW_DIVERGENCE_BACKTEST_ENABLED`、`DECISION_ESCALATION_MODE` |
| 新增只读接口 | `GET /api/admin/shadow-digest`（M6.3 灰度复盘摘要，仅 shadow 模式下有意义） |

全部新增字段均带默认值/`available=false` 兜底，历史报告解析、离线兜底路径（`_offline_report`）不受影响。

---

## 8. 兼容性与合规

- 新增字段均为可选/带默认值，旧报告渲染、`report_export.py` markdown 导出无需强制迁移。
- "评估"类动作严格保留"评估"后缀，不生成"卖出/买入"这类实盘指令措辞，符合产品边界"不做实盘交易指令"。
- caveats 中明确新增信号"为历史统计规律，不构成投资建议承诺"，与现有 `caveats` 免责声明风格一致。
- "清仓评估"这类最强动作建议在前端增加一次展开确认交互（已确认，见第 10 节），避免用户被动作词吓到做出非理性操作。

---

## 9. 分期路线图与依赖

```
M1（数据/回测/confidence修复）── 可独立先行，产出新信号
   │
   ├─→ M2（双向guard + 仓位建议）── 依赖 M1 的信号输出
   │       │
   │       ├─→ M3（风控复核角色，仅deep模式）── 依赖 M2 的最低档位计算
   │       │
   │       └─→ M4（荐基同步）── 依赖 M2，可与 M3 并行开发
   │
   └─→ M5（前端展示）── 跟随 M2/M3 的数据契约逐步接入

M6（验证/灰度）── 贯穿全程；M1完成即开始历史回放校准，M2上线即进入 shadow 灰度（观察1个月）
   └─→ M6.3（灰度复盘摘要）── 依赖 M2 产出的 validation_notes 升级标记，与 M5 前端卡片同步接入
```

---

## 10. 设计决策确认记录

| # | 问题 | 结论 | 状态 |
|---|---|---|---|
| 1 | 情绪分级阈值是否采用"先测算再定阈值"的方式（不在设计阶段锁死数字） | **确认**：M1 实现阶段用近 1~2 年历史数据分布校准后再定 | ✅ 已确认 |
| 2 | M2.1 触发矩阵方向是否符合风控直觉 | **确认**：方向合理，具体数值仍按 M6 历史回放流程校准，不在设计阶段锁死 | ✅ 已确认 |
| 3 | "清仓评估"等最强动作是否需要二次确认交互 | **确认**：需要，前端加"点击展开才能看到"的二次确认（M5 已按此设计） | ✅ 已确认 |
| 4 | M3 风控复核角色是否先做"单角色反思版" | **确认**：先上线单角色反思版，验证有效性后再评估是否升级为多智能体辩论 | ✅ 已确认 |
| 5 | 灰度周期：shadow 模式观察多久、谁拍板切换 `enforced` | **确认**：观察 1 个月（约 20 个交易日）；期间由系统每周生成一次"灰度复盘摘要"，你本人看摘要后拍板是否切换 `enforced` | ✅ 已确认 |

### 关于第 5 项："灰度周期"（shadow 模式）是什么，为什么需要它

**为什么不能一上线就直接生效：** M2.1 新增的"双向 guard 强制升级"是一个从未在真实场景验证过的新机制——如果规则本身的门槛设得不合适（比如把"量价背离"判断得过于敏感），可能会把一个本来合理的"观察"错误地升级成"减仓评估"甚至"清仓评估"。如果这套逻辑一上线就直接改变日报最终显示给你的动作，一旦判断有偏差，你可能会被一个还不成熟的规则误导，做出不必要的操作——这和这次事件本身想解决的问题（决策不可靠）是同一类风险，只是方向反过来了。

**shadow 模式怎么运作：** 新逻辑正常在后台跑，正常计算出"如果启用新守卫，这条建议会被升级为 XX"，但**不改变日报里实际显示的最终动作**，只是在旁边多加一行提示（如："若启用新版风控，本条建议将由『观察』上调为『暂停追涨』，理由：量价背离显著+情绪转冷"）。你可以正常按原有动作做决策，同时观察这个新机制说得有没有道理、触发得多不多。等观察确认靠谱后，再把配置从 `shadow` 切到 `enforced`，让它真正生效去改变最终动作。

**已确认的结论：**

- **观察时长**：1 个月（约 20 个交易日），覆盖更多不同市场状况，样本更充分。
- **复盘方式**：系统每周自动生成一次"灰度复盘摘要"，而不是让你逐条翻日报里的提示。摘要内容包括：本周 shadow 触发了几次、涉及哪些板块/规则、系统建议升级成什么动作、（如果次日数据已出）实际走势是否验证了升级判断。你看摘要后决定是否提前/按期切换 `enforced`。
- **落地方式（新增 M6.3）**：新增只读聚合服务 `shadow_escalation_digest.py`，扫描近 7 天报告记录中带 shadow 升级标记的 `validation_notes`/caveats，按板块与触发规则聚合出上述摘要字段，复用 `recommendation_outcomes.py` 已有的"次日实际涨跌"数据做命中率对照；新增只读接口（如 `GET /api/admin/shadow-digest`）与前端诊断区小卡片 `ShadowEscalationDigestCard.tsx` 展示最近一次摘要（不做邮件/消息推送，私有部署下打开日报页面即可看到，避免过度工程化）。
