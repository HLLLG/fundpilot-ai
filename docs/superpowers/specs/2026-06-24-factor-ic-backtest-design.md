# 模块3 第一期：因子有效性回测（IC / Rank IC）设计

> **教学版设计文档。** 面向「会写代码但量化是新手」的你，既给工程方案，也讲清每个概念为什么这么做。
>
> **关联：** 路线图「模块 3｜像样的回测（前视偏差、walk-forward、IC 信息系数、价值/质量因子、修 Bug B）」。模块 1（组合风险度量）、模块 2（因子思维：横截面打分）已完成，见 `2026-06-24-portfolio-risk-metrics-design.md`、`2026-06-24-fund-factor-scores-design.md`。
>
> **范围（本文 = 模块3 子项 3A）：** 用**只靠净值就能算**的方式，**回测模块2 那几个因子到底有没有预测力**——在一个基金池上算「某日的因子排序，能不能预测之后一段时间的收益排序」（即 IC / Rank IC），并配上前视偏差规避、walk-forward、统计显著性。
>
> **交付形态（已与人确认）：** **方案 C —— 离线工具**。一个纯函数回测引擎 + 一个 CLI runner，跑一次产出「人读报告 + 机读 summary.json」。**不做 API、不做前端面板**（IC 偏专业、计算重、是研究/验证动作，不是基民日常功能；YAGNI）。summary.json 为模块4「量化结论喂 LLM」预留接口。
>
> **明确不做（划界）：** 价值/质量因子（子项 3C，需新数据源）、全市场分层抽样池（子项 3D）、修 Bug B（子项 3B，`sector_signal_backtest.py`）。本文只做 3A。

---

## 0. 一段话总览

模块2 给每只基金打了「动量/风险调整/回撤/规模」的因子分，但有个诚实的问题没回答：**这些分到底有没有用？** 本期就来检验它——在一个基金池上，反复地问「今天因子分高的基金，接下来一个月是不是真的涨得多？」。把每个再平衡日的「因子排序 vs 未来收益排序」的相关系数（Rank IC）攒起来求平均、算显著性，就能给每个因子一个「可信度」的客观答案。整套东西离线跑、纯函数、可单测，并且**深度复用模块2 已经写好的因子引擎**。

---

## 1. 先讲清概念（这是模块3 该懂的东西）

### 1.1 IC / Rank IC 是什么

**IC（Information Coefficient，信息系数）** 衡量「某因子在 T 日给基金的排序，能不能预测 T+N 的收益排序」。

- 取一个**基金池**（横截面），在 T 日算每只基金的因子值；
- 再算每只基金从 T 到 T+N 的真实收益；
- 求这两列的相关系数。

用**普通皮尔逊相关**叫 IC；用**斯皮尔曼秩相关**（先把两列各自转成名次再求相关）叫 **Rank IC**。我们用 **Rank IC**，因为：① 它只看「排序对不对」，不被极端值带偏；② 对因子值做任何单调变换（比如 z-score）都不影响结果，稳健。

> 直觉：IC = +1 表示「因子排第1的基金，未来收益也排第1，完全说中」；IC = 0 表示「因子排序和未来收益毫无关系，等于瞎猜」；IC = -1 表示「完全说反」。A 股单因子单期 Rank IC 能稳定在 0.03~0.05 就已经是不错的因子了——**别期待 0.5 那种数字，那通常是代码有前视偏差**。

### 1.2 单期 IC 没意义，要看一串

一次横截面的 IC 受运气影响极大。所以要 **walk-forward**：每隔一段时间（本文每 21 个交易日≈1 月）取一个横截面，算一个 IC，攒成一条 **IC 时序**，再看它的统计性质：

| 指标 | 公式 | 含义 |
|------|------|------|
| **mean IC** | IC 序列均值 | 因子平均有多准、朝哪个方向 |
| **IC std** | IC 序列标准差 | 因子稳不稳定 |
| **ICIR** | mean / std | 信息比率，**性价比**——又准又稳才高 |
| **t 统计量** | mean / (std / √n) | 这个均值显著不为 0 吗 |
| **%>0** | IC>0 的期数占比 | 方向一致性 |

**显著性判定**：`n >= 12 期` 且 `|t| > 2`。期数太少、或 t 不够大，就**不下结论**（宁可说「样本不足」，也不把运气当本事）。

### 1.3 前视偏差（look-ahead bias）—— 回测头号杀手

**前视偏差 = 在 T 日的决策里，偷偷用了 T 日之后才知道的信息。** 一旦发生，回测结果会好得离谱、上线全亏。本文的铁律：

- T 日的**因子值**：只允许用 `date <= T` 的 NAV；
- 从 T 到 T+N 的**未来收益**：只允许用 `date > T` 的 NAV；
- 两段数据严格不重叠。

我们专门写一个**单测守卫**它（见第 7 章）：构造一个「如果误用了未来数据，因子值就会变」的面板，断言引擎算出来的因子值不变。

### 1.4 幸存者 / 选择偏差（诚实划界）

本文基金池复用模块2 的 `fetch_open_fund_rank`（排行榜 ~300 只），它是**「当前还活着、且业绩偏强」**的样本：

- **幸存者偏差**：清盘/合并的差基金不在池里；
- **选择偏差**：排行榜本身偏强。

后果：**IC 会偏乐观**。本文不解决它（解决方案是子项 3D 全市场分层抽样池），但**报告里必须显著标注这条**，避免把偏乐观的数字当真。

---

## 2. 架构与文件

| 文件 | 职责 | 网络 |
|------|------|------|
| `apps/api/app/services/fund_factor_nav.py`（新） | **共享 helper**：从一段 NAV 切片算因子原始值（动量/Calmar/回撤）。模块2 `portfolio_snapshot._target_from_nav` 重构为调它（消重 DRY） | 否 |
| `apps/api/app/services/factor_ic_backtest.py`（新） | **纯引擎**：吃「NAV 面板 + 交易日轴」，walk-forward 算每因子 Rank IC 时序与汇总 | 否 |
| `apps/api/scripts/run_factor_ic.py`（新） | **CLI runner**：取数（排行榜池 + 线程池拉 NAV）→ 拼面板 → 调引擎 → 落盘 | 是 |
| `apps/api/tests/test_factor_ic_backtest.py`（新） | 引擎单测 | 否 |
| `apps/api/tests/test_fund_factor_nav.py`（新） | 共享 helper 单测 | 否 |

设计原则延续模块1/2：**纯函数 + 依赖注入**，把「算」和「取数」彻底分开，算的部分零网络、可单测。

## 3. 数据流

```
排行榜池(~300码) ──┐
                  ├─ runner 线程池拉 NAV(~750交易日) → nav_panel: {code: [(date, nav)...] asc}
交易日轴(union)  ──┘                                    + calendar: [date...] asc
        ↓ (注入纯引擎)
compute_factor_ic(nav_panel, calendar, rebalance_step=21, forward_days=20, ...)
        ↓
FactorICResult( 每因子: mean_ic / icir / t / 显著性 + composite )
        ↓ runner
apps/api/var/factor_ic/report.txt (人读) + summary.json (机读, 给模块4)
```

**交易日轴 calendar 怎么来：** runner 取所有基金 NAV 日期的并集、升序去重作为锚定日轴。再平衡日 = 在 calendar 上每隔 `rebalance_step` 取一个、且 `i + forward_days < len(calendar)` 的那些点。各基金在锚定日上「取 ≤ 该日的最后一个 NAV」对齐（容忍个别基金某天没净值）。

## 4. 引擎接口（纯函数，逐字落地）

```python
from __future__ import annotations
from dataclasses import dataclass, field

MIN_CROSS_SECTION = 10   # 单期横截面有效基金数下限
MIN_PERIODS = 12         # 有效期数下限（少于此不下显著性结论）
T_SIGNIF = 2.0           # |t| 显著阈值
DEFAULT_REBALANCE_STEP = 21
DEFAULT_FORWARD_DAYS = 20
DEFAULT_FACTOR_LOOKBACK = 250


@dataclass
class NavPoint:
    date: str
    nav: float


@dataclass
class FactorICStats:
    factor: str
    n_periods: int
    mean_ic: float | None
    ic_std: float | None
    icir: float | None
    t_stat: float | None
    positive_ratio: float | None
    significant: bool
    ic_series: list[float] = field(default_factory=list)


@dataclass
class FactorICResult:
    available: bool
    universe_size: int
    rebalance_count: int
    forward_days: int
    message: str | None = None
    factors: list[FactorICStats] = field(default_factory=list)
```

**被检因子（顺序固定）：** `momentum` / `risk_adjusted` / `drawdown` / `composite`。**规模因子排除**——历史规模拿不到，硬编为「不可回测」，不进结果。

### 4.1 斯皮尔曼相关 `_spearman`

```python
def _rankdata(values: list[float]) -> list[float]:
    """平均秩：并列取名次均值（如两个并列第2、3名都记 2.5）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 名次从 1 开始
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None  # 任一列零方差，相关无定义
    return cov / (vx ** 0.5 * vy ** 0.5)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return _pearson(_rankdata(xs), _rankdata(ys))
```

### 4.2 单期 Rank IC `_rank_ic_for_period`

```python
def _rank_ic_for_period(
    factor_vals: dict[str, float | None],
    forward_rets: dict[str, float | None],
    *,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> float | None:
    """对齐到两者都有值的基金，算 Rank IC；有效基金数不足返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    for code, fv in factor_vals.items():
        rv = forward_rets.get(code)
        if fv is None or rv is None:
            continue
        xs.append(fv)
        ys.append(rv)
    if len(xs) < min_cross_section:
        return None
    return _spearman(xs, ys)
```

### 4.3 主函数 `compute_factor_ic`

伪代码（完整实现见 plan）：

```
1. 校验：universe(=len(nav_panel)) < MIN_CROSS_SECTION → available=False
2. 选再平衡锚点：i = 0, step, 2*step, ... 且 i+forward_days < len(calendar)
3. for 每个锚点 i:
     t_date      = calendar[i]
     fwd_date    = calendar[i + forward_days]
     # 各基金 t 日因子原始值（只用 <= t_date 的 NAV，截 factor_lookback 尾巴）
     raws[factor][code] = factor_raws_from_nav_slice(code, navs<=t_date)
     # 各基金未来收益（nav@fwd / nav@t - 1），都用 on-or-before 对齐
     fwd[code]  = nav_asof(code, fwd_date)/nav_asof(code, t_date) - 1
     # 单因子：raw vs fwd 的 Rank IC（spearman 对单调变换不变，用 raw 即可）
     for f in (momentum, risk_adjusted, drawdown):
         ic = _rank_ic_for_period(raws[f], fwd); 收进 ic_series[f]
     # composite：对各 raw 做横截面 z（复用模块2 _factor_stats/_zscore/_composite_z）
     #           → 每基金 composite_z → 与 fwd 求 Rank IC
     ic_series[composite].append(...)
4. 每个因子把 ic_series 聚合成 FactorICStats（均值/std/icir/t/%>0/显著性）
5. 返回 FactorICResult
```

聚合：

```python
def _aggregate(factor: str, ics: list[float]) -> FactorICStats:
    ics = [v for v in ics if v is not None]
    n = len(ics)
    if n == 0:
        return FactorICStats(factor, 0, None, None, None, None, None, False, [])
    mean = sum(ics) / n
    if n >= 2:
        var = sum((v - mean) ** 2 for v in ics) / (n - 1)
        std = var ** 0.5
    else:
        std = 0.0
    icir = mean / std if std > 1e-12 else None
    t_stat = mean / (std / n ** 0.5) if std > 1e-12 else None
    pos = sum(1 for v in ics if v > 0) / n
    significant = n >= MIN_PERIODS and t_stat is not None and abs(t_stat) > T_SIGNIF
    return FactorICStats(factor, n, round(mean, 4), round(std, 4),
                         round(icir, 3) if icir is not None else None,
                         round(t_stat, 2) if t_stat is not None else None,
                         round(pos, 3), significant, [round(v, 4) for v in ics])
```

## 5. 共享 helper `fund_factor_nav.py`（DRY）

把「从 NAV 切片算因子原始值」从 `portfolio_snapshot` 抽出来，模块2 与模块3 共用：

```python
def window_return_percent(navs: list[float], window: int) -> float | None:
    """升序净值序列近 window 交易日区间收益(%)；不足则尽力从最早点算。"""
    if len(navs) < 2:
        return None
    base = navs[max(0, len(navs) - 1 - window)]
    if base <= 0:
        return None
    return (navs[-1] / base - 1.0) * 100.0


def factor_input_from_navs(code: str, name: str, navs: list[float]):
    """从一段升序净值算 FundFactorInput（return_3m/6m/1y + 1年最大回撤；规模 None）。
    窗口口径与排行榜一致：3月≈60、6月≈120、1年≈250 交易日；
    最大回撤复用 portfolio_risk_metrics._max_drawdown 保口径一致。"""
    from app.services.fund_factors import FundFactorInput
    from app.services.portfolio_risk_metrics import _max_drawdown
    if len(navs) < 2:
        return FundFactorInput(fund_code=code, fund_name=name)
    rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1] > 0]
    mdd = _max_drawdown(rets) * 100.0 if rets else None
    return FundFactorInput(
        fund_code=code, fund_name=name,
        return_3m_percent=window_return_percent(navs, 60),
        return_6m_percent=window_return_percent(navs, 120),
        return_1y_percent=window_return_percent(navs, 250),
        max_drawdown_1y_percent=mdd, fund_scale_yi=None,
    )
```

`portfolio_snapshot._target_from_nav` 重构为：取 points → 排序成升序 navs → `factor_input_from_navs(...)`。**模块2 现有单测保证行为不变**（重构守卫）。

## 6. CLI runner `scripts/run_factor_ic.py`

- argparse 参数：`--universe-size`(默认 300)、`--nav-days`(默认 750)、`--rebalance-step`(21)、`--forward-days`(20)、`--max-workers`(8)、`--out-dir`(默认 `apps/api/var/factor_ic`)、`--limit-funds`(调试用，限制只数)。
- 流程：`fetch_open_fund_rank(limit=universe_size)` 取码 → `ThreadPoolExecutor` 并行 `fetch_fund_nav_history(code, trading_days=nav_days)` → 组 `nav_panel` + `calendar`（日期并集升序）→ `compute_factor_ic(...)` → 写 `report.txt` + `summary.json`。
- 取数失败的基金跳过、计数；池有效数 < MIN_CROSS_SECTION 时友好报错退出。

**report.txt 示例：**

```
因子有效性回测 (Rank IC)  运行: 2026-06-24
池: 排行榜 287 只 (有效)  再平衡: 每21日  前瞻: 20日  期数: 28
⚠ 池为「当前在榜、偏强」样本，有幸存者/选择偏差，IC 偏乐观，仅供因子相对比较。
------------------------------------------------------------
因子          mean IC   ICIR   t      %>0    n    结论
动量          0.042     0.31   2.34   0.64   28   弱但显著 ✓
风险调整      0.018     0.12   0.91   0.54   28   不显著
回撤控制      -0.005    -0.03  -0.21  0.48   28   无效
综合          0.038     0.28   2.10   0.61   28   弱但显著 ✓
```

**summary.json：** `{run_date, params{...}, universe_size, rebalance_count, forward_days, caveats[], factors:[{factor, mean_ic, icir, t_stat, positive_ratio, significant, n_periods}]}`。

## 7. 测试策略（TDD）

**`test_fund_factor_nav.py`：**
- `window_return_percent` 已知答案（升序净值区间收益）；序列过短→None。
- `factor_input_from_navs`：上升序列动量>0、回撤接近 0；空/单点不崩。

**`test_factor_ic_backtest.py`：**
- `_rankdata`：并列取平均秩（`[10,10,20]→[1.5,1.5,3]`）。
- `_spearman`：完美正相关=1、完美负=-1、单调非线性仍=1（秩相关特性）、零方差→None。
- `_rank_ic_for_period`：对齐两者都有值的基金；有效数<10→None。
- **植入真信号（核心）**：构造 NAV 面板使「过去动量」与「未来收益」完全同序 → 动量 mean_ic≈1、significant=True。证明引擎能识别真信号。
- **噪声面板**：随机 NAV → |mean_ic| 小、significant=False。
- **前视偏差守卫（核心）**：构造面板，使 t 之后的 NAV 若被误用会改变 t 日因子值；断言因子 IC 与「砍掉 t 之后数据」算出来的一致。
- 边界：`len(nav_panel)<10`→available=False；有效期数<MIN_PERIODS→各因子 significant=False。
- hypothesis：任意面板下 `ic_series` 每个值∈[-1,1]、`positive_ratio`∈[0,1]。

**runner**：注入假 `fetch_rank`/`fetch_nav` 的轻量离线测试，断言 `summary.json` 结构与文件生成（不触网）。

## 8. 实施清单与验收

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 共享 helper + 单测（TDD） | `fund_factor_nav.py`、`test_fund_factor_nav.py` | — |
| 2 | 重构模块2 `_target_from_nav` 调共享 helper（现有测试守卫） | `portfolio_snapshot.py` | 1 |
| 3 | 纯引擎 `_rankdata/_spearman/_rank_ic_for_period` + 单测 | `factor_ic_backtest.py`、`test_factor_ic_backtest.py` | — |
| 4 | `compute_factor_ic` + 聚合 + 植入信号/噪声/前视守卫测试 | 同上 | 1,3 |
| 5 | CLI runner + 落盘 + 离线 runner 测试 | `scripts/run_factor_ic.py` | 4 |
| 6 | `.gitignore` 加 `apps/api/var/`；`PROJECT_CONTEXT.md` 同步 | — | 全部 |

**验收：** 后端全量 `pytest tests -q` 全绿；引擎在「植入真信号」测试上 IC≈1、在噪声上≈0、前视守卫通过；runner 离线测试产出结构正确的 summary.json。

## 9. 在路线图里的位置

模块1「竖着看一只基金」→ 模块2「横着比一群基金」→ **模块3-3A「检验横着比的因子到底准不准」**。本期产出的 summary.json，是模块4「量化结论喂 LLM / 可信度打分器」的直接输入：哪个因子可信（显著）、哪个该忽略（不显著），让 AI 的结论有量化背书。后续 3B（修 Bug B）、3C（价值/质量因子）、3D（全市场池）各自独立成文。
