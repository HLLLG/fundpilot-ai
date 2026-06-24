# 模块4 竖切4：组合风险度量 + 置信 → LLM（设计）

> **教学版设计文档。** 模块4「量化结论喂 LLM」第四个竖切。把模块1 的组合风险度量（夏普/回撤/Beta/HHI）喂进 DeepSeek，并按**样本充足度**挂置信——区别于信号/因子的「跑赢基线/IC 显著」，风险度量的可信度本质是「历史够不够长」。
>
> **关联：** 消费模块1 `portfolio_risk_metrics`（已有 `available` + `sample_days`）与装配层 `build_risk_metrics_payload`。
>
> **已与人确认：** 置信阈值 **20/60/120 交易日**；本竖切不动前端（模块1 风险面板已展示指标，纯 LLM 侧增强）。
>
> **不改** 模块1 风险算法，只做「置信映射 + 喂 LLM」。

---

## 1. 概念与缺口

模块1 在 dashboard 上算了组合夏普/回撤/Beta/HHI，但这些数字**没进 LLM facts**——DeepSeek 写日报时看不到组合层面的风险画像。本竖切把它喂进去，并按样本长度标注可信度：20 交易日是「能算但很毛糙」，120+ 才「比较稳」。

---

## 2. 架构与数据流

```
list_portfolio_daily_snapshots(load 一次, ~400日)
   ├─► build_portfolio_trend_context(history_rows=rows)  (已有, 复用同一份)
   └─► build_risk_metrics_for_facts(rows, holdings)
            │ build_risk_metrics_payload (模块1, 内部取沪深300日线)  ← best-effort
            │ + risk_metrics_confidence(metrics) → {level, basis}     ← 新增纯函数
            ▼
   build_analysis_facts(risk_metrics=...) → facts["risk_metrics"]
            ▼
   instruction + 角色prompt 增「夏普/回撤/Beta 为系统计算事实, 按置信表述」→ DeepSeek
```

---

## 3. risk_confidence.py（新文件，纯函数）

```python
RISK_SAMPLE_HIGH = 120
RISK_SAMPLE_MEDIUM = 60
RISK_SAMPLE_MIN = 20   # 与模块1 MIN_SAMPLE_DAYS 对齐

def risk_metrics_confidence(metrics: dict | None) -> dict:  # {level, basis}
    ...
```

| 条件 | level | basis 示例 |
|------|-------|-----------|
| metrics 为空 / `available` 假 / `sample_days < 20` | **不足** | 「历史样本不足 20 交易日，风险指标暂不可用」 |
| `20 ≤ sample_days < 60` | **低** | 「仅 {n} 交易日样本，指标较毛糙，置信低」 |
| `60 ≤ sample_days < 120` | **中** | 「{n} 交易日样本，置信中」 |
| `sample_days ≥ 120` | **高** | 「{n} 交易日样本，置信高」 |

测试 `tests/test_risk_confidence.py`：四档 + 空/不可用 → 不足。

---

## 4. build_risk_metrics_for_facts（portfolio_snapshot.py 内）

```python
def build_risk_metrics_for_facts(history_rows, holdings_models) -> dict:
    from app.services.risk_confidence import risk_metrics_confidence
    try:
        payload = build_risk_metrics_payload(history_rows, holdings_models)
    except Exception:
        return {"available": False, "message": "风险指标暂不可用"}
    payload["confidence"] = risk_metrics_confidence(payload)
    return payload
```
best-effort：内部取沪深300日线失败也不抛、不阻塞日报。

---

## 5. facts 注入（analysis_facts.py + analysis_payload.py）

- `build_analysis_facts` 新增可选入参 `risk_metrics: dict | None = None`；非空则 `facts["risk_metrics"] = risk_metrics`。
- `facts["instruction"]` 追加：
  > 「组合风险指标(risk_metrics：夏普/回撤/Beta/HHI)为系统计算事实，按 confidence.level 表述：『高/中』可作风险论据；『低/不足』须声明样本有限、不得据此下强结论。」
- `build_user_payload`：load 历史一次 → `build_portfolio_trend_context(history_rows=rows)` + `build_risk_metrics_for_facts(rows, request.holdings)`（best-effort），传入 `build_analysis_facts(..., risk_metrics=...)`。仅 for_llm 路径。
- `analysis_prompt.DEFAULT_ROLE_PROMPT` 「约束」段加同义一条。

---

## 6. 测试与验收

- `test_risk_confidence.py`（纯函数四档 + 边界）。
- `test_portfolio_snapshot.py`：`build_risk_metrics_for_facts` monkeypatch `build_risk_metrics_payload` → 挂 confidence；payload 抛异常 → available=false。
- `test_analysis_facts.py`：facts 含 risk_metrics 且 instruction 含「risk_metrics」。
- 后端全量 `pytest tests -q` 全绿。
- best-effort：缺历史/指数失败 → 标 unavailable，不阻塞日报。

---

## 7. 在路线图里的位置

模块4 第四个竖切。至此板块信号（vs 自然基线）、因子分（vs IC 显著性）、组合风险度量（vs 样本充足度）三类量化结论都带置信进了 LLM。最后可做**信号合成**：把多信号聚合成单条带置信的可执行结论。
