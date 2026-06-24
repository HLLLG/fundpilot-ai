# 模块4 竖切5：信号合成（证据卡，不决策动作）（设计）

> **教学版设计文档。** 模块4 收尾竖切。前四块把三类量化结论各自带置信喂了 LLM（板块信号 / 因子分IC / 组合风险度量）。本块把**每只持仓**的这三路置信**聚合成一个综合置信 + 一句证据摘要**，挂到 facts 的持仓行，让 LLM/前端能一眼看到「这只票的建议有多少量化背书」。
>
> **已与人确认：** 走「证据卡·不决策动作」——**不**自动选买卖动作（避免与现有 `tactical_recommendations` / `signal_guard_policy` 重叠/冲突），动作仍由 LLM 决定。
>
> **不改** 现有任何信号/动作/守卫逻辑，只做「置信聚合 + 证据摘要」。

---

## 1. 概念与缺口

模块4 前四块产出的三路置信是**分散**的：因子 IC 置信在 `facts.factor_scores.factor_reliability`、板块信号置信在 `facts.holdings[].signal_backtest...confidence`、风险置信在 `facts.risk_metrics.confidence`。LLM 要自己拼。本块**替它拼好**：每只持仓一个 `evidence`，含综合置信等级 + 各分量来源，让「每条建议挂一个可回测的综合数字」真正落到持仓粒度。

---

## 2. 纯函数核心 `signal_synthesis.py`

```python
_LEVEL_SCORE = {"高": 3, "中": 2, "低": 1}   # 不足 = 无数据，不计入

def synthesize_confidence(component_levels: list[str]) -> dict:
    """把若干分量置信等级聚合成综合置信 {level, score}。
    只计入有数据的分量（高/中/低）；全为不足/空 → 综合不足。"""
    scores = [_LEVEL_SCORE[l] for l in component_levels if l in _LEVEL_SCORE]
    if not scores:
        return {"level": "不足", "score": 0}
    avg = sum(scores) / len(scores)
    if avg >= 2.5:
        level = "高"
    elif avg >= 1.5:
        level = "中"
    else:
        level = "低"
    return {"level": level, "score": round(avg, 2)}
```

### 持仓证据装配（纯函数，注入已组装好的 dict）
```python
_FACTOR_KEYS = ("momentum", "risk_adjusted", "drawdown")  # size 未回测，不参与

def build_holding_evidence(*, fund_code, signal_entry, factor_scores, risk_metrics) -> dict | None:
    components = []  # [{source, level, basis}]
    # 1) 因子：取该持仓百分位最高、且 IC 置信非「不足」的主因子
    # 2) 信号：取该持仓所属板块、score 最高的规则的 confidence
    # 3) 风险：组合层 risk_metrics.confidence（全组合共用）
    ...
    if not components:
        return None
    composite = synthesize_confidence([c["level"] for c in components])
    return {"composite": composite, "components": components,
            "summary": "；".join(c["basis"] for c in components)}
```

**三路分量取法（细节）：**
- **因子**：在 `factor_scores.holdings[code].factor_percentiles` 里，从 `_FACTOR_KEYS` 选**百分位最高**的因子，取其在 `factor_scores.factor_reliability` 的 `{level,basis}`；该因子 reliability 为「不足」则跳过因子分量。basis 形如「主因子 动量(百分位88)·IC置信高」。
- **信号**：`signal_entry`（= `signal_backtest_for_sector` 的返回）的 `by_rule` 各规则有 `confidence`（竖切1 产出），取 `confidence.score` 最高的规则，basis 形如「板块信号 {rule_label}·置信中」。`signal_entry` 为空 → 跳过。
- **风险**：`risk_metrics.confidence`（竖切4 产出），全组合共用；basis 形如「组合风险样本置信高」。`risk_metrics` 不可用 → 跳过。

只要至少一路有数据就产出 evidence；全无 → None（不挂）。

---

## 3. facts 注入（analysis_facts.py）

`build_analysis_facts` 在 per-fund 循环里（已能拿到该 holding 的 `signal_backtest` 条目），对每个 holding 计算并挂 `row["evidence"]`：
```python
from app.services.signal_synthesis import build_holding_evidence
...
evidence = build_holding_evidence(
    fund_code=holding.fund_code,
    signal_entry=row["signal_backtest"],
    factor_scores=factor_scores,
    risk_metrics=risk_metrics,
)
if evidence:
    row["evidence"] = evidence
```
（`factor_scores` / `risk_metrics` 为竖切3/4 已传入的入参；为 None 时各分量自然跳过。）

`facts["instruction"]` 追加：
> 「持仓的 evidence.composite 是该票三路量化证据（因子IC/板块信号/风险样本）的综合置信：『高』表示多路量化背书一致，可作主理由；『中』部分支持；『低/不足』量化背书弱，须以风险口径表述、不得据此追涨。」

`analysis_prompt.DEFAULT_ROLE_PROMPT` 「约束」段加同义一条。

---

## 4. 前端（轻量，可选）

本竖切以 LLM 侧为主。前端暂不强制；若要可在日报/持仓卡展示 `evidence.composite.level` 小标签（复用 `confidenceTone`），留作后续。**本期不做前端**，保持范围聚焦。

---

## 5. 测试与验收

- `tests/test_signal_synthesis.py`（纯函数）：
  - `synthesize_confidence`：[高,高]→高；[高,低]→中；[低,低]→低；[不足]/[]→不足；混入不足只计有数据。
  - `build_holding_evidence`：注入假 factor_scores/signal_entry/risk_metrics → 三路都在时 components 长度 3、composite 合理；只给一路 → 仍产出；全无 → None；因子主因子选百分位最高且跳过「不足」reliability。
- `tests/test_analysis_facts.py`：传入 factor_scores+risk_metrics+股票板块信号 → 对应 holding 行有 `evidence`，且 instruction 含「evidence」。
- 后端全量 `pytest tests -q` 全绿。
- best-effort：任一路缺失不报错；全缺则不挂 evidence。

---

## 6. 收尾：模块4 全景

至此模块4「信号合成 + AI 闭环」完成：
- 竖切1 板块信号置信（vs 自然基线）
- 竖切3 因子分置信（vs IC 显著性）
- 竖切4 组合风险度量置信（vs 样本充足度）
- 竖切5 **信号合成**：每只持仓把三路置信聚合成综合置信 + 证据摘要

护城河「让 LLM 从分析师退回沟通者，每条建议挂一个可回测的数字」落到持仓粒度。后续可选：把 evidence 展示到前端、或做组合层「证据总览」。
