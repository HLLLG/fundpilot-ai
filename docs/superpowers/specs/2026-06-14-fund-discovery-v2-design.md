# 推荐基金 V2 — 竞品调研与扩展设计

> **版本：** 2026-06-14  
> **状态：** 已实现（V2.0 + V2.1 已合并交付；V2.2 定时扫描待做）  
> **前置：** MVP 已实现（`docs/superpowers/specs/2026-06-13-fund-discovery-design.md`）  
> **范围：** 竞品对标、差异化定位、V2 功能扩展（不含宽池 Tool 模式）

---

## 1. 竞品调研摘要

### 1.1 对标产品矩阵

| 产品 | 目标用户 | 核心能力 | 「荐新基 / 选基」方式 | 与 FundPilot 关系 |
|------|----------|----------|----------------------|-------------------|
| **养基宝** | 年轻基民、多平台持仓 | OCR 汇总、盘中估值、关联板块、加减仓榜、周报/盈亏分析 | **不提供** AI 荐新基；靠榜单与板块热度辅助人工决策 | **输入源对齐**（OCR/板块口径）；我们是其「投研大脑」延伸 |
| **支付宝 / 蚂蚁财富** | 大众理财 | 持仓管理、加仓榜算法、蚂小财 AI 盯盘/诊基/配置 | 榜单 + AI 对话（行情解读、诊基）；偏平台导流与爆款运营 | 我们有**私有部署**与**组合缺口**上下文，不依赖平台榜单 |
| **天天基金 / 东财** | 主动研究者 | 全市场排行、筛选器、自选、资讯 | 规则筛选 + 人工比较；AI 能力在平台侧逐步补齐 | 我们复用其**数据面**（AkShare/东财），但输出是**解释型报告**而非排行榜 |
| **且慢 / 盈米** | 中产、买方投顾 | 策略组合、主理人、账户诊断、资产配置 MCP（~60 项能力） | **组合级**推荐（二八轮动、稳健八心八箭等），非单基自由荐股 | 我们聚焦**单基窄池** + 用户已有持仓缺口，更轻、可私有 |
| **小倍养基** | 养基宝同类 | 估值、持仓汇总 | 同养基宝，无独立 AI 荐基 | 同类输入源 |
| **investool（开源）** | 技术型投资者 | 4433 法则、规则筛基、持仓检测 | **纯规则**，无 LLM 解释与新闻语境 | 可借鉴**规则层**（规模、经理、回撤阈值），守卫已部分覆盖 |

### 1.2 竞品能力拆解（荐新基相关）

```text
                    规则筛选    榜单/热度    组合策略    AI 解释    持仓上下文    私有部署
养基宝                 ○           ●           ○           ○           ●           ○
蚂蚁财富               ○           ●           ○           ●           ●           ○
天天基金               ●           ●           ○           ○           ○           ○
且慢                   ●           ○           ●           ○           ●           ○
FundPilot MVP          ●           ●           ○           ●           ●           ●
```

● = 强 / 已有　○ = 弱 / 无

### 1.3 用户旅程对比

| 步骤 | 养基宝 / 支付宝 | 且慢 | FundPilot（V2） |
|------|-----------------|------|-----------------|
| 录入持仓 | OCR / 平台原生 | 平台内购买 | OCR（养基宝/支付宝）✓ |
| 看当日涨跌 | 板块估值 / 官方净值 | 组合净值 | 板块估算 + 官方净值 ✓ |
| 发现新机会 | 加减仓榜、热门板块 | 跟投组合策略 | **扫描今日机会** → AI 窄池报告 ✓ |
| 理解「为何这只」 | 自行查排行/新闻 | 主理人文章 | 报告 points + risks + **候选池面板** ✓ |
| 细化需求 | 无 / 通用客服 | 投顾咨询 | **SSE 追问** ✓ |
| 回顾历史推荐 | 无专门能力 | 策略历史 | **DiscoveryHistoryRail** + diff ✓ |
| 验证推荐质量 | 无 | 组合业绩 | **DiscoveryOutcomesPanel** + 命中率 API ✓ |
| 自定义 AI 角色 | 无 | 无 | 日报 + **荐基独立角色 Prompt** ✓ |

### 1.4 差异化定位（FundPilot 应坚守）

1. **私有 + 可审计**：结构化 `discovery_facts`、候选池白名单、守卫 caveat，适合 ≤5 人小团队自用。
2. **组合缺口驱动**：`portfolio_gap` + `sector_heat` + `signal_backtest` 联合决策，不是全市场盲推。
3. **与日报分工清晰**：日报管存量持仓；推荐基金管增量机会——同一 `InvestorProfile`，不同 Prompt 与报告表。
4. **不做的边界（延续 MVP）**：自动下单、全市场 Tool 自由搜基、公募 SaaS 化运营。

### 1.5 可借鉴的竞品做法

| 来源 | 可借鉴点 | FundPilot 落地方式 |
|------|----------|-------------------|
| 养基宝 | 加减仓榜、板块标签一眼扫 | 关注方向 chips 已做；可加「热度变化箭头」与默认选中 Top3 缺口板块 |
| 蚂蚁蚂小财 | 诊基、盯盘、追问式交互 | 扩展 `DiscoveryChatPanel` 建议 prompt；报告卡片加「同类候选对比」 |
| 且慢 | 策略透明度、持仓可追溯 | 报告内展示 **候选池摘要**（15~25 只列表，标明入选/未入选原因） |
| 天天基金 | 多维度筛选（类型、规模、费率） | 窄池构建增加 **基金类型过滤**（联接/C 类优先）；守卫层硬过滤 |
| investool | 4433 等规则 | 候选池 `enrich` 阶段增加可选规则标签，喂模型作参考（非硬筛） |

---

## 2. MVP 现状与缺口（代码核对）

### 2.1 已实现（MVP ✓）

- Tab + 关注方向 + 预算 + 快速/深度 + 异步扫描
- 窄池 15~25、`discovery_guard`、离线兜底、SSE 追问
- 独立表 `fund_discovery_reports` / `discovery_jobs` / `discovery_chat_messages`
- `GET /api/fund-discovery/reports` 列表 API

### 2.2 V2 已交付（2026-06-14 ✓）

| 能力 | 状态 |
|------|------|
| 推荐报告历史 UI（`DiscoveryHistoryRail`）+ diff | ✓ |
| 荐基 AI 角色 Prompt（`discovery_prompt_state`，schema v6） | ✓ |
| 候选池面板（`DiscoveryCandidatePoolPanel`） | ✓ |
| 推荐卡片 → 基金详情预览 | ✓ |
| sector 加载失败错误态 + 重试 | ✓ |
| 推荐准确率复盘（`DiscoveryOutcomesPanel`） | ✓ |
| 基金类型偏好（`etf_link` / `no_c_class`） | ✓ |
| 扫描稳定性（sector 并行、job 单连接轮询、CORS） | ✓ |

### 2.3 待做（V2.2+）

| 缺口 | 优先级 |
|------|--------|
| 定时扫描 / 桌面通知 | **P2**（V2.2） |
| 宽池 Tool 搜基 | **P3**（V3） |
| 小程序只读推荐 Tab | **P3**（V3） |
| 默认勾选缺口 Top2 板块、涨跌色配置 | **P2**（体验增强） |

---

## 3. V2 目标

在 **不引入宽池 Tool** 的前提下，把推荐基金从「能跑通」提升到「可日常使用、可回顾、可微调、可解释」。

### 3.1 产品目标

| 目标 | 说明 |
|------|------|
| 可回顾 | 历史推荐报告列表 + 与上一份 diff |
| 可解释 | 展示候选池与守卫剔除记录 |
| 可微调 | 独立「推荐基金 AI 角色设定」持久化 |
| 可验证 | 7/30 日推荐结果简单复盘（净值方向） |
| 可深入 | 从推荐卡片进入基金详情（未持有也可预览） |

### 3.2 非目标（V2 仍不做）

- 全市场 `search_funds` Tool 宽池（V3）
- 自动下单、券商对接
- 复杂量化打分（多因子模型）
- 小程序推荐 Tab

---

## 4. 方案对比（三选一）

### 方案 A：体验补齐型（推荐）

**做法：** 历史列表 + 角色 Prompt + 候选池面板 + 详情跳转 + 前端错误态；后端改动小，主要前端与 `discovery_prompt` 持久化。

| 优点 | 缺点 |
|------|------|
| 2~3 天可交付；API 大半已存在 | 不提升选基算法本身 |
| 用户感知价值最大 | 准确率复盘仅能做轻量版 |

### 方案 B：算法增强型

**做法：** 在 A 基础上加估值分位、4433 规则标签、候选池多因子重排、推荐准确率全量复盘。

| 优点 | 缺点 |
|------|------|
| 推荐质量可量化提升 | 开发 1~2 周；依赖更多 AkShare 字段稳定性 |
| 与 investool 差异化 | UI 复杂度高 |

### 方案 C：运营闭环型

**做法：** 在 A 基础上加定时扫描、桌面通知、与日报联动（「今日日报 + 推荐摘要」合并推送）。

| 优点 | 缺点 |
|------|------|
| 提高打开率 | 需要调度与通知权限；私有场景价值有限 |

**推荐：方案 A 为 V2.0 首发，B 中「准确率复盘」拆为 V2.1，C 为 V2.2。**

---

## 5. V2 详细设计（方案 A + 轻量 B）

### 5.1 历史推荐报告（P0）

**UX**

```text
推荐基金 Tab
├── 扫描区（现有 FundDiscoveryPanel）
├── 当前报告（DiscoveryReportPanel）
└── 右侧/底部 DiscoveryHistoryRail（新）
    ├── 最近 30 条标题 + 日期 + target_sectors 摘要
    ├── 点击加载 report 详情
    └── 「与上一份对比」→ diff markdown（复用 report_diff 思路）
```

**API**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fund-discovery/reports` | 已有 |
| GET | `/api/fund-discovery/reports/{id}/diff` | **新增**：与同一用户上一份 discovery report 对比 |

**实现要点**

- 前端 `listDiscoveryReports()` 已有，新增 `DiscoveryHistoryRail.tsx`（参考 `HistoryRail`）
- 后端 `discovery_diff.py` mirror `report_diff.py`，对比 `recommendations` 字段（code/action/amount 变化）

### 5.2 推荐基金 AI 角色设定（P0）

**UX：** `FundDiscoveryPanel` 顶部可折叠「AI 角色设定」多行输入，`data-testid=discovery-role-prompt`。

**API（mirror 日报）**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/discovery-prompt` | `role_prompt`, `is_custom`, `default_role_prompt` |
| PUT | `/api/discovery-prompt` | body `{ role_prompt }` |

**存储：** `discovery_prompt_state` 表（schema v6）或复用 key-value 模式同 `analysis_prompt_state`。

**后端：** `DiscoveryRequest` 增加可选 `system_role_prompt`；`discovery_client` 优先用户自定义，否则 `DEFAULT_DISCOVERY_ROLE_PROMPT`。

### 5.3 候选池透明度面板（P0）

**UX：** 报告区新增可折叠「本次候选池（N 只）」：

- 表格：代码、名称、板块、近1年收益、规模、入选原因（`selection_reason`）
- 推荐卡片高亮池内被选中的 3~5 只
- `caveats` 中守卫剔除项同步展示在面板底部

**数据：** 已有 `report.candidate_pool`，纯前端组件 `DiscoveryCandidatePoolPanel.tsx`。

### 5.4 推荐卡片 → 基金详情（P1）

**UX：** 推荐卡片点击打开 `YangjibaoFundDetail` 预览模式（`holding_amount=0`，只读业绩/板块分时）。

**API：** 复用 `POST /api/holdings/detail`；前端构造临时 `Holding` 对象。

### 5.5 基金类型偏好（P1）

**UX：** 扫描区增加可选 chips：`不限` | `ETF联接优先` | `排除C类`（单选，默认不限）。

**后端：**

- `DiscoveryRequest` 新增 `fund_type_preference: "any" | "etf_link" | "no_c_class"`
- `discovery_candidate_pool._passes_quality` / 排序：联接基金加权；C 类后缀过滤
- `discovery_guard`：若偏好后池不足，caveat 说明

### 5.6 推荐准确率复盘（P1，轻量）

**目标：** 回答「7 天前推荐的基金，后来涨了吗？」

**做法：** 复用 `recommendation_outcomes` 框架：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fund-discovery/reports/{id}/outcomes` | 推荐日 vs 7/30 日后净值方向 |
| GET | `/api/fund-discovery/recommendation-accuracy?days=30` | 聚合命中率 |

**前端：** `DiscoveryReportPanel` 底部 `DiscoveryOutcomesPanel`（参考 `RecommendationAccuracyPanel`）。

**指标（MVP 级）：** 推荐日收盘 → N 日后收盘涨跌幅；按 `建议关注/分批买入` 分档统计方向命中率（不做金额模拟）。

### 5.7 关注方向体验增强（P1）

- sector chips 加载失败时展示错误 + 重试按钮（不再静默 `[]`）
- 默认勾选：自动缺口 Top2 板块（`select_target_sectors` 结果预览，用户可取消）
- 涨跌色：涨红跌绿（与养基宝习惯一致，可配置）

### 5.8 数据流（V2 扫描全链路）

```text
用户打开 Tab
  → GET /fund-discovery/sectors（关注方向）
  → GET /discovery-prompt（角色设定，localStorage 缓存）
  → 可选 GET /fund-discovery/reports（历史）

点击扫描
  → POST /fund-discovery/async { holdings, profile, focus_sectors, budget, fund_type_preference, system_role_prompt }
  → pipeline（同 MVP）
  → 报告含 candidate_pool + recommendations + caveats

报告展示
  → DiscoveryReportPanel + CandidatePoolPanel + Outcomes（若有历史）
  → 追问 DiscoveryChatPanel
```

---

## 6. 后端文件规划（V2 增量）

| 文件 | 变更 |
|------|------|
| `discovery_prompt.py` | 增加 load/save 默认模板 |
| `discovery_diff.py` | **新建** |
| `discovery_outcomes.py` | **新建**（mirror recommendation_outcomes） |
| `discovery_candidate_pool.py` | `fund_type_preference` 过滤 |
| `models.py` | `DiscoveryRequest.fund_type_preference`, `system_role_prompt` |
| `database.py` | `discovery_prompt_state` CRUD |
| `db_migrations.py` | schema v6 |
| `main.py` | 新路由 |
| `FundDiscoveryPanel.tsx` | 角色设定、类型偏好、错误态 |
| `DiscoveryHistoryRail.tsx` | **新建** |
| `DiscoveryCandidatePoolPanel.tsx` | **新建** |
| `DiscoveryOutcomesPanel.tsx` | **新建** |

---

## 7. 测试策略

| 测试 | 覆盖 |
|------|------|
| `test_discovery_prompt.py` | 持久化、默认恢复 |
| `test_discovery_diff.py` | 两份报告 recommendations diff |
| `test_discovery_outcomes.py` | mock 净值序列 |
| `test_discovery_candidate_pool.py` | etf_link / no_c_class 过滤 |
| `test_api.py` | 新路由鉴权 |
| Playwright | 历史列表加载、扫描、候选池折叠 |

---

## 8. 分期交付建议

| 版本 | 内容 | 预估 |
|------|------|------|
| **V2.0** | 历史 Rail + 角色 Prompt + 候选池面板 + sector 错误态 | 2~3 天 |
| **V2.1** | 详情跳转 + 基金类型偏好 + outcomes 复盘 | 2~3 天 |
| **V2.2** | 定时扫描 + 桌面通知（可选） | 1~2 天 |
| **V3** | 宽池 Tool、小程序、估值分位 | 另立 spec |

---

## 9. 风险与合规

- 准确率复盘须标注「历史统计，不代表未来」
- 候选池展示须保留免责声明
- 基金类型过滤为偏好而非投资建议
- 角色 Prompt 编辑增加 4000 字上限与示例模板

---

## 10. 验收标准（V2.0）

1. 推荐基金 Tab 可查看最近 30 条历史报告并切换展示
2. 可编辑并持久化推荐基金 AI 角色设定，下次扫描生效
3. 报告内可展开候选池列表，推荐项与池内数据一致
4. sector 接口失败时 UI 有明确错误提示
5. pytest 新增项通过；`npm run typecheck` 通过

---

## 11. 交付记录

**已确认并交付：** 选项 2 — V2.0 + V2.1 合并一次交付（2026-06-14）。

实现计划：`docs/superpowers/plans/2026-06-14-fund-discovery-v2.md`。架构与 API 以 `docs/PROJECT_CONTEXT.md` 为准。
