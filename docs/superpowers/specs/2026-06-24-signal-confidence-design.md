# 模块4 竖切：板块信号可信度打分器 + 注入 LLM（4A + 4B）

> **教学版设计文档。** 路线图模块4「信号合成 + AI 闭环（可信度打分器、量化结论喂 LLM）」的第一个端到端竖切。
>
> **愿景（来自路线图）：** 让 LLM 从「分析师」退回「沟通者」——每条建议挂一个**可回测的数字**。本竖切先把**板块信号**这一条结论挂上置信分，端到端跑通「量化证据 → 置信分 → DeepSeek 按置信分级表述」。
>
> **关联：** 模块3-3B（板块信号回测基线修正）已产出 `hit_rate / baseline_rate / edge / significant`，本文直接消费它。模块1/2/3A 的量化产物接入 LLM 留作模块4 后续竖切。
>
> **已与人确认：** 范围 = 4A+4B 薄竖切；首条结论 = 板块信号置信升级（改动最小、复用 3B 数据）；阈值对齐 3B（n≥30、edge 5%/10% 分界）。
>
> **交付形态：** 后端纯函数打分器 + facts/prompt 注入 + 前端置信标签。**不改回测算法本身。**

---

## 1. 为什么是这个竖切

现状：`analysis_facts.build_analysis_facts` 已经把一份结构化「事实字典」喂给 DeepSeek，并带「数字由系统计算、不得改写、只能解释」的护栏。板块信号回测 `signal_backtest` 也已在 facts 里，3B 还补齐了 `baseline_rate_percent / edge_percent / significant`。

**缺口：** prompt 没告诉 LLM「按可信度区别对待」——一个只触发 8 次的信号和一个显著跑赢基线 12% 的信号，在话术里被一视同仁。这正是护城河要补的：**每条信号挂一个置信分，LLM 据此分级表述**。

因为数据已就位，这是改动最小、价值最直接的起点。

---

## 2. 架构与数据流

```
sector_signal_backtest (3B: hit_rate / baseline_rate / edge / significant)
        │
        ▼
4A signal_confidence.score_signal(bucket) → ConfidenceScore{level, score, basis}   纯函数
        │
        ▼
4B sector_signal_context._compact_rules  每条规则注入 confidence 字段
        │
        ▼
analysis_facts (已有) ── instruction 补「按置信分级表述」 ──► DeepSeek
        │
        ▼
前端 SectorSignalBacktestPanel 展示置信标签（高/中/低/不足 + 色块）
```

每个单元职责单一、可独立测试：
- **4A** 只做「桶 → 置信结论」的纯映射，无 I/O。
- **4B** 只做「把 4A 结果挂到 facts，并告诉 LLM 怎么用」。

---

## 3. 4A 可信度打分器（纯函数 `apps/api/app/services/signal_confidence.py`）

### 输入
一个规则桶（来自 3B 的 compact 结构），关心四个字段：
- `trigger_count`（n，信号触发次数）
- `hit_rate_percent`（h，命中率）
- `baseline_rate_percent`（b，方向感知自然基线）
- `edge_percent`（e = h − b，超额命中）
- `significant`（3B 判定：e ≥ 5 且 n ≥ 30）

> 注：`edge_percent` 以 3B 输出为准；若缺失则由 `h − b` 兜底计算。`significant` 同理可由 `e ≥ EDGE_MIN 且 n ≥ MIN_TRIGGERS` 兜底。

### 常量（与 3B 对齐，集中可调）
```python
MIN_TRIGGERS = 30        # < 此值 → 样本不足
EDGE_MEDIUM = 5.0        # edge ≥ 此值且显著 → 中
EDGE_HIGH = 10.0         # edge ≥ 此值且显著 → 高
SCORE_SAMPLE_FULL = 50   # n ≥ 此值时样本因子封顶为 1
```

### 输出
```python
@dataclass
class ConfidenceScore:
    level: str       # "高" / "中" / "低" / "不足"
    score: int       # 0–100，给前端做条/色
    basis: str       # 一句话依据（中文）
```

### 分级规则
| 条件 | level | basis 示例 |
|------|-------|-----------|
| `n < MIN_TRIGGERS` | **不足** | 「样本仅 {n} 次（<30），不作数」 |
| `n ≥ MIN_TRIGGERS` 且（不显著或 `e < EDGE_MEDIUM`） | **低** | 「未稳定跑赢自然基线（edge {e}%），置信低」 |
| 显著 且 `EDGE_MEDIUM ≤ e < EDGE_HIGH` | **中** | 「跑赢自然基线 {e}%（{n} 次），置信中」 |
| 显著 且 `e ≥ EDGE_HIGH` | **高** | 「显著跑赢自然基线 {e}%（{n} 次），置信高」 |

### score 计算（0–100，给前端展示，非分级依据）
```python
sample_factor = min(1.0, n / SCORE_SAMPLE_FULL)
score = round(50 + clamp(e * 2, -50, 50) * sample_factor)
score = clamp(score, 0, 100)
```
- e=0（无超额）→ 50 中性；e=+12 且 n≥50 → 74；e=−10 → <50；样本越小越往 50 收。
- score 仅用于可视化强弱，**分级以 level 为准**（避免「数字精确感」误导）。

### 边界
- 桶为 None 或 `trigger_count` 缺失/≤0 → `level="不足", score=0, basis="无触发样本"`。
- `edge_percent` 缺失 → 用 `h − b` 兜底；`h`、`b` 也缺失 → 视为不足。

---

## 4. 4B 注入 LLM

### 4B-1 facts 注入（`sector_signal_context._compact_rules`）
每条规则字典追加：
```python
compact[rule_id]["confidence"] = asdict(score_signal(bucket))
```
保持其余字段不变（向后兼容）。

### 4B-2 facts 顶层指令（`analysis_facts.build_analysis_facts`）
现有 `facts["instruction"]` 追加一句：
> 「板块信号（`signal_backtest`）须按各规则 `confidence.level` 表述：**高**可作主理由；**中**需措辞保留（"偏向/可关注"）；**低/不足**只能作提示，**不得**据此主导追涨或减仓建议。」

### 4B-3 角色 prompt（`analysis_prompt.DEFAULT_ROLE_PROMPT` 「约束」段）
加同义一条，双保险（facts 指令 + 角色约束）。

### 4B-4 前端（`SectorSignalBacktestPanel.tsx`）
每条规则在现有 edge 展示旁加置信标签：`高/中/低/不足` + 按 level 上色的小色块（复用 `globals.css` 语义色：高=信任蓝/绿、中=琥珀、低/不足=灰）。`lib/api.ts` 的 `SectorSignalBacktestRule` 增 `confidence` 可选字段。

---

## 5. 测试

### 后端
- `tests/test_signal_confidence.py`（纯函数）：
  - 显著高 edge → 「高」；显著中 edge → 「中」；n≥30 但不显著 → 「低」；n<30 → 「不足」。
  - score 落 0–100；e=0→50；样本越小越收敛 50；负 edge → <50。
  - None / 缺字段 → 「不足」。
- `tests/test_sector_signal_context.py`（或并入现有）：注入桶 → compact 每规则带 `confidence`。
- facts 冒烟：`build_analysis_facts` 产物的 `signal_backtest` 规则含 `confidence`，且 `instruction` 含「置信」字样。

### 前端
- `SectorSignalBacktestPanel` vitest：给定带 confidence 的规则 → 渲染对应置信标签。

### 验收
- 后端全量 `pytest tests -q` 全绿；前端 `npm test` 全绿、`build` 通过。
- 不改 `sector_signal_backtest` 回测算法（3B 已定）。

---

## 6. 在路线图里的位置

模块4 终点是「量化引擎 → 结构化结论（胜率/敞口/夏普）→ DeepSeek 翻译成人话 + 新闻佐证，每条建议挂可回测数字」。本竖切先把**板块信号**这一条挂上置信分、端到端跑通闭环。后续竖切按同一骨架接入模块1（风险度量置信）、模块2（因子分）、模块3A（IC 显著性），各自独立成文。
