# 模块3 子项 3C（风格回归因子）+ 3D（分层抽样池）设计

> **教学版设计文档。** 延续模块3「像样的回测」，补两块：3C 给因子库加「价值/成长风格暴露」（净值回归法），3D 把回测/打分的基金池从「头部偏强」换成「跨业绩分层抽样」以降偏差。
>
> **关联：** 模块3-3A（因子 IC 回测）、3B（修 Bug B）已完成。本文 = 3C + 3D。
>
> **交付形态：** 与 3A 一致的**离线工具**（纯函数引擎 + CLI runner + 离线测试），不做 API/前端。
>
> **已与人确认的方向：** 3C 走**风格回归法**（非持仓穿透）；3D 走**分层抽样**复用 3A 引擎。

---

## 3C 价值/成长风格因子（净值回归法）

### 概念
基本面价值/质量（PE/PB/ROE）需要持仓穿透 + 个股财务数据，本期不做。改用**收益型风格分析**：把一只基金的日收益对「价值指数」「成长指数」的日收益做回归，回归系数（beta）就是这只基金对两种风格的暴露。

- `beta_value` 高 → 跟价值股齐涨跌 → 偏价值；
- `beta_growth` 高 → 偏成长；
- `style_tilt = beta_value - beta_growth`：>0 偏价值、<0 偏成长。

**诚实边界：** 这是**风格暴露**（基金长得像价值/成长），不是基本面「便宜/赚钱能力」。真·基本面因子留作后续持仓穿透项目。

### 引擎 `apps/api/app/services/fund_style_regression.py`（纯函数）

```python
MIN_STYLE_SAMPLE_DAYS = 60
TILT_LABEL_THRESHOLD = 0.15

@dataclass
class StyleExposure:
    available: bool
    beta_value: float | None
    beta_growth: float | None
    style_tilt: float | None      # beta_value - beta_growth
    r_squared: float | None
    sample_days: int
    label: str | None             # 偏价值 / 偏成长 / 中性
    message: str | None = None
```

- `align_returns(fund_by_date, value_by_date, growth_by_date) -> (f, v, g)`：按公共日期升序对齐成三条等长列表（取交集）。
- `compute_style_exposure(fund_returns, value_returns, growth_returns) -> StyleExposure`：
  - 样本 < `MIN_STYLE_SAMPLE_DAYS` → unavailable；
  - **二元 OLS（中心化、闭式解）**：对 value、growth 两个回归元解 2×2 正规方程
    ```
    bv = (Sgg*Svy - Svg*Sgy) / det
    bg = (Svv*Sgy - Svg*Svy) / det
    det = Svv*Sgg - Svg^2        # |det| < eps（两风格共线/零方差）→ unavailable
    ```
  - `r_squared = 1 - SSres/SStot`（SStot=0 → None）；
  - `style_tilt = bv - bg`；`label`：tilt > 0.15 偏价值、< -0.15 偏成长、否则中性。

### CLI `apps/api/scripts/run_style_factor.py`
- 取排行榜池 + 各基金 NAV → 日收益；取价值/成长指数日线（默认 **国证价值 399371 / 国证成长 399370**，可 `--value-index/--growth-index`）→ 日收益。
- 逐只 `compute_style_exposure` → 落盘 `apps/api/var/style_factor/{report.txt, summary.json}`：每只基金 style_tilt/label/r²，以及全池偏价值/偏成长/中性的只数分布。
- 参数：`--universe-size`、`--nav-days`、`--value-index`、`--growth-index`、`--max-workers`、`--out-dir`。

### 测试 `tests/test_fund_style_regression.py`
- `align_returns`：按公共日期对齐、长度一致、缺日期被丢。
- 植入价值基金（fund = value 序列）→ bv≈1、bg≈0、tilt≈1、label「偏价值」、r²≈1。
- 植入成长基金 → 对称结论。
- 样本不足 → unavailable。
- 价值=成长（共线）→ det≈0 → unavailable。
- runner 离线注入假 fetcher → 写出 summary.json 且结构正确。

---

## 3D 分层抽样池（降低「头部偏强」偏差）

### 概念
3A/模块2 的池子用 `fetch_open_fund_rank` 的**前 N 名**，是偏强样本。3D 改为在排行榜池里**按排名位置等距分层抽样**，让样本横跨赢家→输家，z-score / IC 的横截面更中性。

**诚实边界：** `fetch_open_fund_rank` 子进程**上限 500 条**（`head(500)`），且清盘基金不在榜（幸存者偏差不可消）。本期只在「返回的池」内分层抽样以削弱**选择**偏差；彻底去偏需 point-in-time 基金库，超出本期。

### 引擎 `apps/api/app/services/fund_universe_sampler.py`（纯函数）

```python
def sample_universe(rank_rows: list[dict], sample_size: int) -> list[dict]:
    """在按业绩排序的榜单里等距分层抽样，横跨各业绩段。
    rows 数 <= sample_size 时原样返回。"""
    n = len(rank_rows)
    if n <= sample_size or sample_size <= 0:
        return list(rank_rows)
    step = n / sample_size
    return [rank_rows[int(i * step)] for i in range(sample_size)]
```

### 接入 3A runner `scripts/run_factor_ic.py`
- `build_ic_report` 增参 `universe_mode: "top" | "sampled" = "top"`、`sample_pool_size: int = 500`。
  - `top`（默认，行为不变）：`fetch_rank(universe_size)`。
  - `sampled`：`fetch_rank(sample_pool_size)` 取大池 → `sample_universe(rows, universe_size)`。
- `main()` 加 `--universe-mode`、`--sample-pool-size`。summary.json 的 `params` 记录 `universe_mode`。

### 测试（并入 `tests/test_factor_ic_backtest.py` 或新建 `tests/test_fund_universe_sampler.py`）
- `sample_universe`：返回恰好 sample_size 条；首/尾段都被覆盖（跨业绩段）；`n<=size` 原样返回；`size<=0` 原样返回。
- runner `universe_mode="sampled"` 离线注入 → 池被抽样、summary 记录 mode。

---

## 实施清单与验收

| # | 任务 | 文件 |
|---|------|------|
| 1 | 3C 引擎 + 单测（TDD） | `fund_style_regression.py`、`tests/test_fund_style_regression.py` |
| 2 | 3C CLI runner + 离线测试 | `scripts/run_style_factor.py` |
| 3 | 3D 抽样器 + 单测 | `fund_universe_sampler.py`、`tests/test_fund_universe_sampler.py` |
| 4 | 3D 接入 3A runner（universe_mode）+ 离线测试 | `scripts/run_factor_ic.py` |
| 5 | 文档同步 PROJECT_CONTEXT | `docs/PROJECT_CONTEXT.md` |

**验收：** 后端全量 `pytest tests -q` 全绿；风格回归在「植入价值/成长基金」上 tilt 方向正确、r²≈1；抽样器跨业绩段；两个 runner 离线测试产出结构正确的 summary.json。
