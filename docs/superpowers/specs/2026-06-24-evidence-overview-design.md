# 组合层「证据总览」（设计）

> **教学版设计文档。** 模块4 竖切5 给**每只持仓**挂了 `evidence`（三路量化置信聚合）。本块把全组合的 evidence **聚合成组合级背书分布**——「组合多少市值有中/高量化背书」——同时进 LLM（facts）+ 前端（懒加载端点 + 面板）。
>
> **不改** 任何信号/动作/置信算法，只做「持仓 evidence 的组合级汇总」。

---

## 1. 概念

单只持仓的 `evidence.composite.level`（高/中/低/不足）回答「这只票建议有多少量化背书」。组合层关心：
- **覆盖率**：几只持仓有证据（覆盖）。
- **背书分布**：按**持仓市值加权**，多少比例落在 高/中/低/不足。
- **一句话**：「组合 X% 市值有中/高量化背书」——给 LLM 一个开篇定调，给用户一个直观体检结论。

市值加权（而非简单计数）更贴近组合实际暴露：一只 60% 仓位的「低背书」票，比五只各 2% 的「高背书」票更该被点名。

---

## 2. 纯函数 `signal_synthesis.py::build_evidence_overview`

```python
_OVERVIEW_LEVELS = ("高", "中", "低", "不足")

def build_evidence_overview(rows: list[dict]) -> dict:
    """把每只持仓的 evidence 聚合成组合级背书分布。

    rows: build_analysis_facts 的 per_fund 行（含 holding_amount，可含 evidence）。
    """
    total_amount = sum(float(r.get("holding_amount") or 0) for r in rows)
    covered = [r for r in rows if r.get("evidence")]
    if not covered or total_amount <= 0:
        return {"available": False}

    count_by_level = {lv: 0 for lv in _OVERVIEW_LEVELS}
    weight_by_level = {lv: 0.0 for lv in _OVERVIEW_LEVELS}
    for r in covered:
        lv = r["evidence"]["composite"]["level"]
        if lv not in count_by_level:
            continue
        count_by_level[lv] += 1
        weight_by_level[lv] += float(r.get("holding_amount") or 0) / total_amount * 100

    weight_by_level = {k: round(v, 1) for k, v in weight_by_level.items()}
    backed = round(weight_by_level["高"] + weight_by_level["中"], 1)
    return {
        "available": True,
        "total_holdings": len(rows),
        "covered_holdings": len(covered),
        "count_by_level": count_by_level,
        "weight_by_level": weight_by_level,
        "backed_weight_percent": backed,   # 高+中 市值占比
        "summary": f"组合 {backed:.0f}% 市值有中/高量化背书，"
                   f"{len(covered)}/{len(rows)} 只持仓有证据覆盖。",
    }
```

**口径说明：** 分母为**全部持仓市值**（含未覆盖），所以 `weight_by_level` 各级之和 = 已覆盖市值占比，剩余即「未覆盖」。`backed_weight_percent` = 高+中，是组合「量化背书」核心体检数。

---

## 3. facts 注入（LLM）

`build_analysis_facts` per-fund 循环**之后**：
```python
overview = build_evidence_overview(per_fund)
if overview.get("available"):
    facts["evidence_overview"] = overview
```
`instruction` 追加：
> 「evidence_overview 是组合级量化背书体检：backed_weight_percent 为『中/高背书』市值占比。占比高→建议可更积极；占比低→须强调多数仓位量化背书不足、以风险口径表述。」
`analysis_prompt.DEFAULT_ROLE_PROMPT` 加同义一条。

---

## 4. 懒加载端点（前端）

新 `GET /api/portfolio/evidence-overview`（与 `/api/portfolio/factor-scores` 同款懒加载）：精简装配三路（factor_scores 走 TTL 缓存、risk_metrics 取日快照、signal 取板块上下文），逐持仓 `build_holding_evidence` → `build_evidence_overview`。返回：
```json
{ "overview": {...}, "holdings": [{"fund_code","fund_name","evidence"}], "available": true }
```
best-effort：任一路失败 → 该路分量缺失；全失败 → `available: false`。**不**触发 LLM、不阻塞。

---

## 5. 前端面板

`api.ts` 加类型 `PortfolioEvidenceOverview`；新组件 `PortfolioEvidenceOverviewPanel`（懒加载按钮触发 fetch），展示：
- 顶部一句 `summary` + `backed_weight_percent` 大字
- 各级 `weight_by_level` 横条（复用 `confidenceTone` 配色）
- 折叠：每只持仓 `composite.level` 小标签 + `summary` hover

挂到现有组合分析页（与因子体检面板同区）。

---

## 6. 测试与验收

- `tests/test_signal_synthesis.py` 增 `build_evidence_overview`：加权分布正确 / 无 evidence→available False / 未覆盖计入分母 / backed=高+中。
- `tests/test_analysis_facts.py`：facts 含 evidence_overview + instruction 提及。
- 端点：`tests/test_*` 加 `/api/portfolio/evidence-overview` 冒烟（best-effort 不 500）。
- 前端 vitest：`confidenceTone` 已覆盖；可加面板渲染冒烟。
- 后端全量 `pytest tests -q` 全绿。
