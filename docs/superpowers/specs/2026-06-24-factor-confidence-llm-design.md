# 模块4 竖切3：因子分 + IC 置信 → LLM（设计）

> **教学版设计文档。** 模块4「量化结论喂 LLM / 可信度打分器」的第三个竖切（前两个：板块信号置信、已完成；组合风险度量置信、暂缓）。
>
> **愿景：** LLM 看到「某基金动量分 A」时，同时看到「动量因子在 3A 回测中**显著正向 IC=0.04（置信高）**」或「**不显著（置信低，仅描述性）**」——把模块2 的因子分从「描述」升级为「带可回测背书的论据」，落实护城河「每条建议挂一个可回测的数字」。
>
> **关联：** 消费模块2 `fund_factors`（因子分）+ 模块3A `factor_ic` 的离线 `summary.json`（IC 显著性）。
>
> **已与人确认：** IC 当因子置信 + TTL 缓存 best-effort + 顺带前端小标签；IC 置信阈值 显著且 `|mean_ic|≥0.03`→高、显著但更弱→中。
>
> **不改** 模块2 因子算法、模块3A 回测算法，只做「置信映射 + 喂 LLM + 前端标签」。

---

## 1. 概念与缺口

模块2 给每只持仓在排行榜横截面打了因子分（动量/风险调整/回撤/规模 → 综合等级 A/B/C/D），但 LLM facts 里**没有**这些分；而且分本身是「描述性」的——它没说「这个因子到底准不准」。模块3A 的 IC 回测正好回答了后者：哪个因子显著有预测力、哪个是噪声。

**本竖切 = 把因子分喂进 LLM，并用 3A 的 IC 显著性给每个因子挂置信**，让 LLM 知道「动量分可信、规模分仅供参考」。

---

## 2. 架构与数据流

```
模块3A 离线产物 var/factor_ic/summary.json  (mean_ic / significant / ...)
        │  load_ic_summary() best-effort 读+缓存
        ▼
factor_confidence(ic_factors, key) → {level, basis}   纯函数
        │
模块2 build_factor_scores_payload(holdings)  (排行榜+净值, 重)
        │  build_factor_scores_for_facts(): TTL缓存 + 挂 factor_reliability + 压缩
        ├──────────────► /api/portfolio/factor-scores 响应(挂 factor_reliability) → 前端 IC 置信标签
        ▼
build_user_payload: best-effort 算 → 传入 build_analysis_facts(factor_scores=...)
        ▼
facts["factor_scores"] + instruction/prompt 护栏 → DeepSeek
```

各单元职责单一、可独立测试：
- `factor_confidence` 纯映射，无 I/O（`load_ic_summary` 单独负责读文件）。
- `build_factor_scores_for_facts` 只做「取数(缓存) + 挂置信 + 压缩」。
- `build_analysis_facts` 只接收预算好的 payload，不在内部取数（保护其它调用方）。

---

## 3. factor_confidence.py（新文件）

### 常量
```python
IC_STRONG = 0.03          # |mean_ic| ≥ 此值且显著 → 高
SUMMARY_PATH = var/factor_ic/summary.json   # 模块3A 产物
SUMMARY_TTL_SECONDS = 1800
# 模块2 因子键 → 3A IC 因子键（size 未回测）
FACTOR_IC_KEY = {"momentum": "momentum", "risk_adjusted": "risk_adjusted",
                 "drawdown": "drawdown", "size": None}
```

### `load_ic_summary() -> dict[str, dict]`
best-effort 读 `summary.json` 的 `factors` 列表 → `{factor_key: stats}`；文件缺失/损坏/过期 → `{}`。带模块级 TTL 缓存（与文件 mtime 或时间戳）。

### `factor_confidence(ic_factors: dict, factor_key: str) -> dict`
返回 `{"level": str, "basis": str}`：

| 条件 | level | basis 示例 |
|------|-------|-----------|
| `factor_key=="size"` 或 IC 键为 None | **不足** | 「规模因子未回测，仅供参考」 |
| 无 IC 数据（summary 空/缺该因子） | **不足** | 「无回测数据」 |
| `significant` 且 `mean_ic ≥ IC_STRONG` | **高** | 「回测显著正向（IC {mean_ic:+.3f}），置信高」 |
| `significant` 且 `0 < mean_ic < IC_STRONG` | **中** | 「回测显著但偏弱（IC {mean_ic:+.3f}），置信中」 |
| `significant` 且 `mean_ic < 0` | **低** | 「回测显著反向（IC {mean_ic:+.3f}），慎用」 |
| 不显著 | **低** | 「回测不显著，仅描述性」 |

### `factor_reliability(ic_factors=None) -> dict[str, dict]`
对模块2 四因子各算一次 confidence，返回 `{factor_key: {level, basis}}`。便于一次挂到 payload。

### 测试 `tests/test_factor_confidence.py`
- 显著强正→高；显著弱正→中；显著负→低；不显著→低；size→不足；空 summary→全不足。
- `load_ic_summary` 读临时 JSON → 正确解析；文件缺失→{}。

---

## 4. build_factor_scores_for_facts（portfolio_snapshot.py 内）

```python
_FACTOR_FACTS_CACHE: dict[str, tuple[float, dict]] = {}
_FACTOR_FACTS_TTL = 3600
```
- 入参 `holdings_models`；缓存键 = 持仓代码排序拼接。
- 调 `build_factor_scores_payload(holdings_models)`（重，可注入 fetch_rank/fetch_nav 便于测试）。
- 读 `factor_reliability(load_ic_summary())`。
- 压缩为紧凑 facts 结构（见 §5），写缓存返回。
- 任意异常 → 返回 `{"available": False, "message": "因子分暂不可用"}`（best-effort，不抛）。

---

## 5. facts 注入（analysis_facts.py）

`build_analysis_facts` 新增可选入参 `factor_scores: dict | None = None`；非空则 `facts["factor_scores"] = factor_scores`。紧凑结构：
```python
{
  "available": True,
  "universe_size": 300,
  "factor_reliability": {
     "momentum": {"level": "高", "basis": "回测显著正向（IC +0.041），置信高"},
     "risk_adjusted": {...}, "drawdown": {...},
     "size": {"level": "不足", "basis": "规模因子未回测，仅供参考"}
  },
  "holdings": [
     {"fund_code": "000001", "fund_name": "...", "composite_grade": "A",
      "composite_score": 82.0,
      "factor_percentiles": {"momentum": 88, "risk_adjusted": 71, "drawdown": 64, "size": 40}}
  ]
}
```

`facts["instruction"]` 追加：
> 「因子分(factor_scores)：按 factor_reliability 各因子置信使用——『高』可作论据；『中』措辞保留；『低/不足』仅作描述，不得作买卖主理由；size 因子未回测仅供参考。」

`build_user_payload`（analysis_payload.py）：
```python
try:
    factor_scores = build_factor_scores_for_facts(request.holdings)
except Exception:
    factor_scores = None
```
传入 `build_analysis_facts(..., factor_scores=factor_scores)`。`for_llm=True` 时才算（避免 report_judge 等无谓开销）。

`analysis_prompt.DEFAULT_ROLE_PROMPT` 「约束」段加同义一条。

---

## 6. API + 前端（轻量）

- `/api/portfolio/factor-scores`（main.py 现有端点）响应挂 `factor_reliability`（复用 `factor_reliability(load_ic_summary())`）。
- `lib/api.ts`：`PortfolioFactorScores` 加 `factor_reliability?`。
- `PortfolioFactorScoresPanel.tsx`：每个因子行加 IC 置信小标签（复用 `confidenceTone`，hover 显 basis）。
- 前端 vitest：置信标签映射。

---

## 7. 测试与验收

- 后端：`test_factor_confidence.py`（纯函数 + 文件读）、`build_factor_scores_for_facts` 离线注入（假 rank/nav + 临时 summary.json）、facts 注入冒烟（facts 含 factor_scores 且 instruction 含「factor_reliability」）。
- 前端：vitest 标签映射；`npm run build` 通过。
- 后端全量 `pytest tests -q` 全绿。
- best-effort：缺 summary.json / 取数失败 → 因子分标 unavailable，不阻塞日报。

---

## 8. 在路线图里的位置

继板块信号置信之后，本竖切把**因子分**也挂上可回测背书（IC 显著性）。模块4 后续可再做组合风险度量置信（样本充足度）、以及把多信号聚合成单条带置信的可执行结论（信号合成）。
