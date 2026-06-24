# 模块3-3A 因子有效性回测（IC）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development。每个任务先写失败测试→看它失败→最小实现→看它通过→提交。

**Goal:** 离线回测模块2 的因子（动量/风险调整/回撤/综合）有没有预测力——在基金池上算 walk-forward Rank IC + 显著性，产出人读报告 + 机读 summary.json。

**Architecture:** 共享 helper `fund_factor_nav.py`（NAV切片→因子原始值）← 纯引擎 `factor_ic_backtest.py`（walk-forward Rank IC）← CLI runner `scripts/run_factor_ic.py`（取数+落盘）。纯函数 + 依赖注入，算的部分零网络可单测。

**Tech Stack:** Python 标准库（无 numpy/scipy，手写 spearman）；pytest + hypothesis。

**Spec:** `docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md`（口径以 spec 为准）。

**命令：**
- 单文件：`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_factor_ic_backtest.py -q`
- 全量：`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q`
- git 用真实 git：`/mingw64/bin/git`（包装器在本机 git 2.30 注入 `--trailer` 会报错）

---

## Task 1: 共享 helper `fund_factor_nav.py`

**Files:**
- Create: `apps/api/app/services/fund_factor_nav.py`
- Test: `apps/api/tests/test_fund_factor_nav.py`

- [ ] **Step 1: 失败测试**

```python
from app.services.fund_factor_nav import window_return_percent, factor_input_from_navs


def test_window_return_percent_known():
    # 1.0→1.1，window 覆盖全段 → +10%
    assert round(window_return_percent([1.0, 1.05, 1.1], 60), 2) == 10.0


def test_window_return_percent_too_short():
    assert window_return_percent([1.0], 60) is None


def test_factor_input_rising_series_positive_momentum():
    navs = [1.0 + 0.01 * i for i in range(120)]
    fi = factor_input_from_navs("000001", "测试", navs)
    assert fi.return_3m_percent is not None and fi.return_3m_percent > 0
    assert fi.max_drawdown_1y_percent is not None
    assert fi.fund_scale_yi is None


def test_factor_input_empty_no_crash():
    fi = factor_input_from_navs("000001", "测试", [])
    assert fi.return_3m_percent is None
```

- [ ] **Step 2: 看它失败**（ModuleNotFoundError）
- [ ] **Step 3: 实现**（见 spec 第 5 章逐字）

```python
from __future__ import annotations


def window_return_percent(navs: list[float], window: int) -> float | None:
    if len(navs) < 2:
        return None
    base = navs[max(0, len(navs) - 1 - window)]
    if base <= 0:
        return None
    return (navs[-1] / base - 1.0) * 100.0


def factor_input_from_navs(code: str, name: str, navs: list[float]):
    from app.services.fund_factors import FundFactorInput
    from app.services.portfolio_risk_metrics import _max_drawdown

    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1] > 0]
    mdd = _max_drawdown(rets) * 100.0 if rets else None
    return FundFactorInput(
        fund_code=code,
        fund_name=name,
        return_3m_percent=window_return_percent(navs, 60),
        return_6m_percent=window_return_percent(navs, 120),
        return_1y_percent=window_return_percent(navs, 250),
        max_drawdown_1y_percent=mdd,
        fund_scale_yi=None,
    )
```

- [ ] **Step 4: 看它通过**
- [ ] **Step 5: 提交** `feat(api): 因子NAV共享helper fund_factor_nav + 单测`

---

## Task 2: 重构模块2 `_target_from_nav` 调共享 helper

**Files:**
- Modify: `apps/api/app/services/portfolio_snapshot.py`（`_target_from_nav` + `_window_return_percent`）

现有 `test_fund_factors.py::test_assembly_scores_holdings_offline` 是行为守卫（不在榜走净值兜底）。

- [ ] **Step 1: 重构**——删 `portfolio_snapshot._window_return_percent`，`_target_from_nav` 改为：取 points→排序升序 navs→`factor_input_from_navs(code, name, navs)`。保留它原有的「取 points、按日期排序、过滤 nav<=0」逻辑，只把窗口/回撤/构造换成 helper。

```python
def _target_from_nav(holding: Holding, fetch_nav) -> "object":
    from app.services.fund_factor_nav import factor_input_from_navs
    from app.services.fund_factors import FundFactorInput

    code = holding.fund_code
    name = holding.fund_name or ""
    try:
        points = fetch_nav(code, name, 250)
    except Exception:
        points = []
    pairs: list[tuple[str, float]] = []
    for point in points or []:
        nav = getattr(point, "nav", None)
        day = str(getattr(point, "date", "") or "")[:10]
        if nav is None or float(nav) <= 0 or not day:
            continue
        pairs.append((day, float(nav)))
    pairs.sort(key=lambda x: x[0])
    navs = [nav for _, nav in pairs]
    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)
    return factor_input_from_navs(code, name, navs)
```

- [ ] **Step 2: 验证行为不变** `pytest tests/test_fund_factors.py -q` 全绿
- [ ] **Step 3: 提交** `refactor(api): 模块2 _target_from_nav 复用 fund_factor_nav（消重）`

---

## Task 3: 引擎统计工具 `_rankdata/_pearson/_spearman/_rank_ic_for_period`

**Files:**
- Create: `apps/api/app/services/factor_ic_backtest.py`
- Test: `apps/api/tests/test_factor_ic_backtest.py`

- [ ] **Step 1: 失败测试**

```python
from app.services.factor_ic_backtest import (
    _rankdata, _spearman, _rank_ic_for_period,
)


def test_rankdata_handles_ties():
    assert _rankdata([10, 10, 20]) == [1.5, 1.5, 3.0]


def test_spearman_perfect_positive():
    assert _spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0


def test_spearman_perfect_negative():
    assert _spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0


def test_spearman_monotonic_nonlinear_is_one():
    # 秩相关对单调变换不变
    assert _spearman([1, 2, 3, 4], [1, 4, 9, 16]) == 1.0


def test_spearman_zero_variance_none():
    assert _spearman([1, 1, 1], [1, 2, 3]) is None


def test_rank_ic_insufficient_cross_section():
    fv = {f"{i}": float(i) for i in range(5)}
    fwd = {f"{i}": float(i) for i in range(5)}
    assert _rank_ic_for_period(fv, fwd, min_cross_section=10) is None


def test_rank_ic_aligns_on_common_codes():
    fv = {"a": 1.0, "b": 2.0, "c": 3.0, "d": None}
    fwd = {"a": 1.0, "b": 2.0, "c": 3.0}
    assert _rank_ic_for_period(fv, fwd, min_cross_section=3) == 1.0
```

- [ ] **Step 2: 看它失败**
- [ ] **Step 3: 实现** `_rankdata/_pearson/_spearman/_rank_ic_for_period` + 常量（见 spec 第 4.1/4.2 逐字）+ 数据类 `NavPoint/FactorICStats/FactorICResult`（见 spec 第 4 章）。
- [ ] **Step 4: 看它通过**
- [ ] **Step 5: 提交** `feat(api): 因子IC回测统计工具 spearman/rank-ic + 单测`

---

## Task 4: 主函数 `compute_factor_ic` + 聚合

**Files:**
- Modify: `apps/api/app/services/factor_ic_backtest.py`
- Test: `apps/api/tests/test_factor_ic_backtest.py`

- [ ] **Step 1: 失败测试**（核心：植入真信号、噪声、前视守卫、边界）

```python
import random
from app.services.factor_ic_backtest import compute_factor_ic, NavPoint, MIN_PERIODS


def _panel_calendar(series_by_code):
    dates = sorted({d for s in series_by_code.values() for d, _ in s})
    panel = {c: [NavPoint(d, v) for d, v in s] for c, s in series_by_code.items()}
    return panel, dates


def test_planted_momentum_signal_detected():
    # 构造 20 只基金：过去动量越强、未来涨得越多 → 动量 IC≈1 且显著
    days = [f"2024-{m:02d}-01" for m in range(1, 13)] + [f"2025-{m:02d}-01" for m in range(1, 13)]
    # 用足够长的日序 + 每基金恒定日斜率，斜率由 rank 决定
    n_days = 600
    cal = [f"D{i:04d}" for i in range(n_days)]
    series = {}
    for k in range(20):
        slope = 0.0005 * (k + 1)  # k 越大涨得越快（过去与未来一致）
        series[f"{k:06d}"] = [(cal[i], 1.0 * (1.0 + slope) ** i) for i in range(n_days)]
    panel = {c: [NavPoint(d, v) for d, v in s] for c, s in series.items()}
    res = compute_factor_ic(nav_panel=panel, calendar=cal,
                            rebalance_step=21, forward_days=20,
                            factor_lookback=250, min_cross_section=10)
    assert res.available is True
    mom = next(f for f in res.factors if f.factor == "momentum")
    assert mom.mean_ic is not None and mom.mean_ic > 0.9
    assert mom.significant is True


def test_noise_panel_not_significant():
    random.seed(1)
    n_days = 600
    cal = [f"D{i:04d}" for i in range(n_days)]
    panel = {}
    for k in range(20):
        nav = 1.0
        pts = []
        for i in range(n_days):
            nav *= 1.0 + random.uniform(-0.01, 0.01)
            pts.append(NavPoint(cal[i], nav))
        panel[f"{k:06d}"] = pts
    res = compute_factor_ic(nav_panel=panel, calendar=cal, min_cross_section=10)
    mom = next(f for f in res.factors if f.factor == "momentum")
    assert mom.significant is False


def test_lookahead_guard_ignores_future():
    # 在两个面板上跑：B = A 砍掉最后 forward 段之后的「未来突变」，
    # 因子 IC 序列前缀应一致（因子值不偷看未来）。
    n_days = 400
    cal = [f"D{i:04d}" for i in range(n_days)]
    base = {}
    for k in range(15):
        slope = 0.0004 * (k + 1)
        base[f"{k:06d}"] = [(cal[i], (1.0 + slope) ** i) for i in range(n_days)]
    panelA = {c: [NavPoint(d, v) for d, v in s] for c, s in base.items()}
    # 面板B：把每只基金最后 30 天 nav 抬高 50%（未来突变），其余相同
    panelB = {}
    for c, s in base.items():
        pts = [NavPoint(d, v) for d, v in s]
        for j in range(len(pts) - 30, len(pts)):
            pts[j] = NavPoint(pts[j].date, pts[j].nav * 1.5)
        panelB[c] = pts
    resA = compute_factor_ic(nav_panel=panelA, calendar=cal, min_cross_section=10)
    resB = compute_factor_ic(nav_panel=panelB, calendar=cal, min_cross_section=10)
    momA = next(f for f in resA.factors if f.factor == "momentum")
    momB = next(f for f in resB.factors if f.factor == "momentum")
    # 不含「末段被改动会进入因子窗口」的早期再平衡期，IC 应一致
    k = min(len(momA.ic_series), len(momB.ic_series)) - 2
    assert momA.ic_series[:k] == momB.ic_series[:k]


def test_small_universe_unavailable():
    cal = [f"D{i:04d}" for i in range(300)]
    panel = {f"{k:06d}": [NavPoint(cal[i], 1.0 + 0.001 * i) for i in range(300)] for k in range(5)}
    res = compute_factor_ic(nav_panel=panel, calendar=cal, min_cross_section=10)
    assert res.available is False


def test_few_periods_not_significant():
    # 日历短，只够 < MIN_PERIODS 个再平衡期
    cal = [f"D{i:04d}" for i in range(120)]
    panel = {f"{k:06d}": [NavPoint(cal[i], (1.0 + 0.0003 * (k + 1)) ** i) for i in range(120)] for k in range(15)}
    res = compute_factor_ic(nav_panel=panel, calendar=cal, rebalance_step=21, forward_days=20, min_cross_section=10)
    for f in res.factors:
        assert f.significant is False
```

- [ ] **Step 2: 看它失败**
- [ ] **Step 3: 实现** `compute_factor_ic` + `_aggregate` + 内部 `_nav_asof(points, date)`（二分/线性取 ≤date 最后一点）+ `_navs_upto(points, date, lookback)`（≤date 尾部切片的 nav 列表）。单因子用 `factor_input_from_navs` 的 raw（momentum=blend、risk_adjusted=calmar、drawdown=mdd，复用 `fund_factors._blend_momentum/_calmar`）；composite 复用 `fund_factors._factor_stats/_zscore/_composite_z`。聚合见 spec 第 4.3。
- [ ] **Step 4: 看它通过**（全部，包括植入信号 IC>0.9）
- [ ] **Step 5: hypothesis 不变量测试**：随机面板下 `ic_series` 每值∈[-1,1]、`positive_ratio`∈[0,1]。跑通后提交。
- [ ] **Step 6: 提交** `feat(api): compute_factor_ic walk-forward 引擎 + 植入信号/噪声/前视守卫测试`

---

## Task 5: CLI runner `scripts/run_factor_ic.py`

**Files:**
- Create: `apps/api/scripts/run_factor_ic.py`
- Test: `apps/api/tests/test_factor_ic_backtest.py`（新增 runner 离线用例）

- [ ] **Step 1: 失败测试**——把取数与组装抽成可注入的 `build_ic_report(*, fetch_rank, fetch_nav, out_dir, ...) -> dict`，离线注入假数据，断言返回 dict 有 `available/factors` 且 `summary.json` 写出。

```python
import json
from app.services.factor_ic_backtest import NavPoint


def test_runner_offline_writes_summary(tmp_path):
    from scripts.run_factor_ic import build_ic_report  # noqa

    cal = [f"D{i:04d}" for i in range(400)]
    def fetch_rank(limit):
        return [{"fund_code": f"{k:06d}", "fund_name": f"基金{k}"} for k in range(15)]
    def fetch_nav(code, name, trading_days):
        k = int(code)
        return [NavPoint(cal[i], (1.0 + 0.0003 * (k + 1)) ** i) for i in range(400)]
    out = build_ic_report(fetch_rank=fetch_rank, fetch_nav=fetch_nav,
                          out_dir=str(tmp_path), universe_size=15, nav_days=400)
    assert out["available"] is True
    assert (tmp_path / "summary.json").exists()
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert "factors" in data and isinstance(data["factors"], list)
```

- [ ] **Step 2: 看它失败**
- [ ] **Step 3: 实现** `scripts/run_factor_ic.py`：
  - `build_ic_report(*, fetch_rank, fetch_nav, out_dir, universe_size=300, nav_days=750, rebalance_step=21, forward_days=20, max_workers=8, limit_funds=None) -> dict`：取码→线程池拉 NAV→组 `nav_panel`(过滤 nav<=0、按日期升序)+`calendar`(日期并集升序)→`compute_factor_ic`→写 `report.txt`+`summary.json`→返回 `asdict(result)`。
  - `main()`：argparse（参数见 spec 第 6 章）→默认 `fetch_rank=fetch_open_fund_rank`、`fetch_nav` 包 `fetch_fund_nav_history`→`build_ic_report`→打印报告路径。
  - `report.txt` 表格 + ⚠ 偏差免责（见 spec 第 6 章样例）；`summary.json` 含 `run_date/params/universe_size/rebalance_count/forward_days/caveats/factors`。
  - sys.path 注入同 `scripts/diagnose_sector_quotes.py`。
- [ ] **Step 4: 看它通过**
- [ ] **Step 5: 提交** `feat(api): 因子IC回测 CLI runner + 离线测试`

---

## Task 6: 收尾——.gitignore + 文档

**Files:**
- Modify: `.gitignore`（加 `apps/api/var/`）
- Modify: `docs/PROJECT_CONTEXT.md`

- [ ] `.gitignore` 追加 `apps/api/var/`。
- [ ] `PROJECT_CONTEXT.md`：更新记录加模块3-3A 段；目录树 services 加 `factor_ic_backtest.py / fund_factor_nav.py`；文档索引加 spec+plan；说明这是离线 CLI 工具（无 API）。
- [ ] 全量验收 `pytest tests -q` 全绿。
- [ ] **提交** `docs: 模块3-3A 因子IC回测接入 PROJECT_CONTEXT + gitignore`

---

## Self-Review

- **Spec 覆盖：** helper(T1)+重构(T2)+统计工具(T3)+主引擎(T4)+runner(T5)+文档(T6) 对齐 spec 第 8 章 6 任务。✓
- **类型一致：** `NavPoint/FactorICStats/FactorICResult`、`compute_factor_ic` 参数名（nav_panel/calendar/rebalance_step/forward_days/factor_lookback/min_cross_section）spec 与 plan 一致。✓
- **无占位：** 关键代码均给出；主循环 `_nav_asof/_navs_upto` 在 T4 Step3 说明输入输出契约，实现时逐字补全。✓
- **前视守卫可证伪：** T4 用两个面板（改/不改未来段）断言 IC 前缀一致，真能抓住偷看未来的 bug。✓
