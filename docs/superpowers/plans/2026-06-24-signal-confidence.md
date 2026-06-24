# 板块信号可信度打分器 + 注入 LLM（模块4 竖切）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给板块信号回测结果挂一个可回测的置信分（高/中/低/不足），注入 analysis_facts + prompt，让 DeepSeek 按置信分级表述，前端展示置信标签。

**Architecture:** 4A 纯函数打分器 `signal_confidence.py`（桶→ConfidenceScore）；4B 在 `sector_signal_context._compact_rules` 注入 confidence、`analysis_facts` 与 `analysis_prompt` 加分级护栏、前端 `SectorSignalBacktestPanel` 展示标签。不改 3B 回测算法。

**Tech Stack:** Python 3.12 / dataclass / pytest / Next.js + TS / vitest。

设计文档：`docs/superpowers/specs/2026-06-24-signal-confidence-design.md`。

---

## Task 1: 4A 可信度打分器（纯函数）

**Files:**
- Create: `apps/api/app/services/signal_confidence.py`
- Test: `apps/api/tests/test_signal_confidence.py`

- [ ] **Step 1: 写失败测试** `test_signal_confidence.py`

```python
from app.services.signal_confidence import ConfidenceScore, score_signal


def _bucket(n, h, b, significant=None):
    e = round(h - b, 2)
    return {
        "trigger_count": n, "hit_rate_percent": h,
        "baseline_rate_percent": b, "edge_percent": e,
        "significant": (e >= 5 and n >= 30) if significant is None else significant,
    }


def test_high_confidence():
    r = score_signal(_bucket(60, 72.0, 55.0))  # edge 17, n 60
    assert r.level == "高"
    assert 0 <= r.score <= 100 and r.score > 60
    assert "置信高" in r.basis


def test_medium_confidence():
    r = score_signal(_bucket(40, 62.0, 55.0))  # edge 7, n 40, significant
    assert r.level == "中"


def test_low_when_not_significant():
    r = score_signal(_bucket(40, 57.0, 55.0))  # edge 2 (<5) → 不显著
    assert r.level == "低"
    assert r.score < 60


def test_insufficient_sample():
    r = score_signal(_bucket(10, 80.0, 50.0))  # n<30
    assert r.level == "不足"


def test_none_bucket():
    assert score_signal(None).level == "不足"
    assert score_signal({"trigger_count": 0}).level == "不足"


def test_zero_edge_is_50():
    r = score_signal(_bucket(50, 55.0, 55.0))  # edge 0
    assert r.score == 50


def test_negative_edge_below_50():
    r = score_signal(_bucket(50, 45.0, 55.0))  # edge -10
    assert r.score < 50
    assert r.level == "低"


def test_edge_missing_falls_back_to_h_minus_b():
    b = {"trigger_count": 60, "hit_rate_percent": 70.0,
         "baseline_rate_percent": 55.0, "significant": True}
    r = score_signal(b)  # edge 缺失，用 70-55=15 兜底
    assert r.level == "高"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_confidence.py -q`（在 apps/api 下）
Expected: FAIL（`ModuleNotFoundError: signal_confidence`）

- [ ] **Step 3: 写实现** `signal_confidence.py`

```python
from __future__ import annotations

from dataclasses import dataclass

MIN_TRIGGERS = 30
EDGE_MEDIUM = 5.0
EDGE_HIGH = 10.0
SCORE_SAMPLE_FULL = 50


@dataclass
class ConfidenceScore:
    level: str
    score: int
    basis: str


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_signal(bucket: dict | None) -> ConfidenceScore:
    if not bucket:
        return ConfidenceScore("不足", 0, "无触发样本")
    n = int(bucket.get("trigger_count") or 0)
    if n <= 0:
        return ConfidenceScore("不足", 0, "无触发样本")

    h = _num(bucket.get("hit_rate_percent"))
    b = _num(bucket.get("baseline_rate_percent"))
    e = _num(bucket.get("edge_percent"))
    if e is None and h is not None and b is not None:
        e = round(h - b, 2)
    if e is None:
        return ConfidenceScore("不足", 0, f"命中率数据缺失（{n} 次）")

    sig = bucket.get("significant")
    if sig is None:
        sig = e >= EDGE_MEDIUM and n >= MIN_TRIGGERS

    sample_factor = min(1.0, n / SCORE_SAMPLE_FULL)
    score = round(50 + _clamp(e * 2, -50, 50) * sample_factor)
    score = int(_clamp(score, 0, 100))

    if n < MIN_TRIGGERS:
        return ConfidenceScore("不足", score, f"样本仅 {n} 次（<{MIN_TRIGGERS}），不作数")
    if not sig or e < EDGE_MEDIUM:
        return ConfidenceScore("低", score, f"未稳定跑赢自然基线（edge {e:+.1f}%），置信低")
    if e < EDGE_HIGH:
        return ConfidenceScore("中", score, f"跑赢自然基线 {e:+.1f}%（{n} 次），置信中")
    return ConfidenceScore("高", score, f"显著跑赢自然基线 {e:+.1f}%（{n} 次），置信高")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_signal_confidence.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/signal_confidence.py apps/api/tests/test_signal_confidence.py
git commit -m "feat(模块4-4A): 板块信号可信度打分器(纯函数)"
```

---

## Task 2: 4B facts 注入 confidence + 指令护栏

**Files:**
- Modify: `apps/api/app/services/sector_signal_context.py`（`_compact_rules`）
- Modify: `apps/api/app/services/analysis_facts.py`（`instruction`）
- Test: `apps/api/tests/test_sector_signal_context.py`（新建）

- [ ] **Step 1: 写失败测试** `test_sector_signal_context.py`

```python
from app.services.sector_signal_context import _compact_rules


def test_compact_rules_attaches_confidence():
    raw = {
        "rule_x": {
            "label": "测试规则",
            "trigger_count": 60,
            "hit_count": 43,
            "hit_rate_percent": 72.0,
            "baseline_rate_percent": 55.0,
            "edge_percent": 17.0,
            "significant": True,
            "beats_baseline": True,
        }
    }
    out = _compact_rules(raw)
    conf = out["rule_x"]["confidence"]
    assert conf["level"] == "高"
    assert 0 <= conf["score"] <= 100
    assert isinstance(conf["basis"], str)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sector_signal_context.py -q`
Expected: FAIL（`KeyError: 'confidence'`）

- [ ] **Step 3: 实现注入**

`sector_signal_context.py` 顶部加 import：
```python
from dataclasses import asdict
from app.services.signal_confidence import score_signal
```
在 `_compact_rules` 的 `compact[rule_id] = {...}` 块末尾、`return compact` 前，给每条加：
```python
        compact[rule_id]["confidence"] = asdict(score_signal(compact[rule_id]))
```
（注意：传入已组装好的 compact 行，其字段名与 score_signal 读取的一致。）

- [ ] **Step 4: facts 指令护栏** `analysis_facts.py`

把 `facts` 的 `"instruction"` 字符串末尾追加：
```python
            "板块信号(signal_backtest)须按各规则 confidence.level 表述："
            "「高」可作主理由；「中」需措辞保留；「低/不足」只能作提示，"
            "不得据此主导追涨或减仓建议。"
```

- [ ] **Step 5: 跑测试 + facts 冒烟**

Run: `python -m pytest tests/test_sector_signal_context.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/services/sector_signal_context.py apps/api/app/services/analysis_facts.py apps/api/tests/test_sector_signal_context.py
git commit -m "feat(模块4-4B): facts 注入信号置信分 + 分级表述护栏"
```

---

## Task 3: 4B 角色 prompt 护栏

**Files:**
- Modify: `apps/api/app/services/analysis_prompt.py`（`DEFAULT_ROLE_PROMPT` 「约束」段）

- [ ] **Step 1: 改 prompt**

在 `DEFAULT_ROLE_PROMPT` 的 `## 约束` 段最后一条 bullet 后追加：
```
- 板块信号回测（`signal_backtest`）须按各规则 `confidence.level` 区别对待：**高**可作主理由；**中**措辞保留；**低/不足**仅作提示，不得主导追涨/减仓
```

- [ ] **Step 2: 验证既有 prompt 测试不破**

Run: `python -m pytest tests/test_api.py -q`（若有 prompt 相关）
Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add apps/api/app/services/analysis_prompt.py
git commit -m "feat(模块4-4B): 角色prompt 增信号置信分级护栏"
```

---

## Task 4: 前端置信标签

**Files:**
- Modify: `apps/web/src/lib/api.ts`（`SectorSignalBacktestRule` 加 `confidence?`）
- Modify: `apps/web/src/components/SectorSignalBacktestPanel.tsx`
- Modify: `apps/web/src/app/globals.css`（置信标签样式）
- Test: `apps/web/src/components/SectorSignalBacktestPanel.test.tsx`（若已存在则加用例，否则跳过组件测，改在 lib 层测纯函数）

- [ ] **Step 1: api.ts 类型**

`SectorSignalBacktestRule` 接口加：
```ts
  confidence?: { level: string; score: number; basis: string } | null;
```

- [ ] **Step 2: 面板展示**

在 `SectorSignalBacktestPanel.tsx` 每条规则现有 edge/baseline 展示旁，渲染：
```tsx
{rule.confidence ? (
  <span className={`signal-confidence signal-confidence--${confLevelKey(rule.confidence.level)}`}>
    置信{rule.confidence.level}
  </span>
) : null}
```
并在文件内加纯函数（便于单测）：
```ts
export function confLevelKey(level: string): string {
  if (level === "高") return "high";
  if (level === "中") return "mid";
  if (level === "不足") return "none";
  return "low";
}
```

- [ ] **Step 3: globals.css 样式**

```css
.signal-confidence { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; margin-left: 6px; }
.signal-confidence--high { background: rgba(37, 99, 235, 0.12); color: #2563EB; }
.signal-confidence--mid  { background: rgba(251, 140, 59, 0.14); color: #C2610A; }
.signal-confidence--low  { background: rgba(100, 116, 139, 0.14); color: #475569; }
.signal-confidence--none { background: rgba(100, 116, 139, 0.10); color: #94A3B8; }
```

- [ ] **Step 4: 前端测试 + build**

Run: `npm test`、`npm run build`（在 apps/web 下）
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/lib/api.ts apps/web/src/components/SectorSignalBacktestPanel.tsx apps/web/src/app/globals.css
git commit -m "feat(模块4-4B): 前端板块信号置信标签"
```

---

## Task 5: 全量验收 + 文档同步

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`

- [ ] **Step 1: 后端全量**

Run: `python -m pytest tests -q`（apps/api）
Expected: 全绿（原 518 + 新增）。

- [ ] **Step 2: PROJECT_CONTEXT 同步**

更新记录加模块4 竖切条目；目录树加 `signal_confidence.py`；文档版本行加模块4。

- [ ] **Step 3: 提交**

```bash
git add docs/PROJECT_CONTEXT.md
git commit -m "docs: PROJECT_CONTEXT 同步模块4 信号置信竖切"
```

---

## 自检（Self-Review）
- **Spec 覆盖：** 4A 打分器(Task1)、facts 注入(Task2)、facts 指令(Task2)、角色 prompt(Task3)、前端标签(Task4)、测试与验收(各 Task + Task5)——全覆盖。
- **类型一致：** `ConfidenceScore{level,score,basis}` 在 4A 定义，Task2 用 `asdict` 落字典，Task4 前端类型字段名一致（level/score/basis）。
- **无占位符：** 每步含完整代码/命令。
- **不改回测算法：** 仅消费 3B 输出。
