# 模块 1｜组合风险度量 — 技术设计文档（教学版）

> **文档定位：** 量化升级路线图的第 1 个可落地模块。本文偏**教学**：每个指标先讲直觉、再给公式、再用你项目里**已有的日快照数据**演示怎么算，最后给出后端/API/前端/测试的完整代码骨架。
>
> **读者：** 量化只懂皮毛、想边学边做的你。读完应能：① 看懂每个风险指标代表什么；② 知道这些数字从你现有的哪张表、哪个字段来；③ 照着骨架把 `compute_portfolio_metrics()` 写出来并接入「盈亏分析」Tab 与「好基灵 Pro」。
>
> **关联：** 这是和你第一次对话里定的「模块 1｜组合风险度量 ⭐ 最先做，直接变现」。后续模块（因子库、回测框架、AI 闭环）单独出文档。
>
> **版本：** 2026-06-24 初稿。

---

## 0. 怎么用这份文档

1. **先读第 1～2 章**（背景 + 预备知识），把"收益和风险的语言"搞懂。这部分没有代码，是地基。
2. **再读第 3 章**（七个指标详解），每个指标都能独立理解，看不懂可以跳过先看下一个。
3. **第 4 章起是工程落地**：数据映射 → 后端骨架 → API → 前端 → 测试。等你概念清楚了，照着一块块实现。
4. 你不需要一次写完。建议顺序：先实现**波动率 + 最大回撤 + 夏普**（最常用、用户最有感），再补 **Beta/Alpha + 相关性 + HHI**。

---

## 1. 背景：你现在的量化底子（诚实体检）

读过你的代码后，结论是：**底子比你以为的好，但有两个概念性 bug 必须先纠正。**

| 你已经有的 | 在哪个文件 | 现状评价 |
|------------|-----------|----------|
| 信号命中率回测（T→T+1） | `sector_signal_backtest.py` | 入门偏上，但判定基准用错了 |
| 大跌反弹统计 | `fund_dip_rebound_backtest.py` | 有统计意识，但收益累加方式有 bug |
| 组合 vs 指数超额收益（alpha） | `portfolio_profit_analysis.py` 的 `summarize_trend_footer` | **你其实已经在算 alpha 了** |
| 风控阈值（浮亏线、集中度） | `risk.py` | 是"规则告警"，不是"风险度量" |

### 1.1 缺口在哪

`risk.py` 现在做的是**阈值告警**：组合浮亏超过 8% 就报 `MAX_DRAWDOWN`，单只占比超 35% 就报 `CONCENTRATION`。这是"红绿灯"，不是"体检报告"。

它**完全没有**：

- **波动率** —— 你的组合每天上下波动有多剧烈？
- **夏普比率** —— 你承担的风险，换来了多少回报？（衡量"性价比"）
- **最大回撤** —— 历史上从最高点最多跌过多少？（用户最怕的数字）
- **Beta** —— 大盘涨跌 1%，你的组合跟着动多少？
- **持仓相关性** —— 你买的几只基金是不是其实都在赌同一个方向？

这些恰恰是 **toC 用户最想看、也最适合做成 Pro 付费功能**的东西。一句"你的组合夏普 0.8、最大回撤 -12%、和沪深 300 相关性 0.92（说明你没分散）"，比一堆文字建议更有说服力。

### 1.2 两个必须先纠正的 bug（这就是你要补的量化第一课）

**Bug A：百分比直接相加（简单收益不可加）**

`fund_dip_rebound_backtest.py` 里：

```python
cumulative = 0.0
for offset in range(1, window + 1):
    forward_change = _as_float(series[index + offset].get("change_percent"))
    cumulative += forward_change   # ← 把每日百分比直接相加
```

`portfolio_profit_analysis.py` 的 `build_daily_trend_series` 也是：

```python
cumulative_portfolio += float(daily_return)   # ← 同样直接相加
```

**为什么错：** 涨 3% 再跌 3%，真实结果不是 0%，而是 `1.03 × 0.97 - 1 = -0.09%`。简单百分比**不能直接相加**，因为复利是相乘关系。点数小时误差可忽略（所以你现在没爆雷），但这是量化第一课——**简单收益相乘、对数收益才相加**（第 2 章详解）。

**Bug B：命中率拿 50% 当基准**

`sector_signal_backtest.py` 里：

```python
"beats_random": hit_rate is not None and hit_rate > 50.0,
```

**为什么错：** A 股板块"上涨日"本身占比可能就 >50%（牛市里随便买都涨）。所以"打败随机"的基准**不是 50%**，而应该是"无脑买入持有的胜率"（即该板块上涨日占全部交易日的比例）。而且没有**统计显著性**：触发 10 次命中 6 次，很可能纯属运气，不能说明规则有效。

> 这两个 bug 我们在第 9 章给修正方案。**模块 1 本身不依赖修复它们也能做**，但建议顺手修 Bug A，因为风险度量也要算累计收益。

---

## 2. 预备知识（模块 0）：收益与风险的"语言"

这一章没有新功能，但它会让你看懂后面所有公式。**读懂这章，你就跨过了量化入门最大的坎。**

### 2.1 简单收益 vs 对数收益

**简单收益率**（你现在用的，也是支付宝/养基宝显示的）：

```
r_simple = (今日净值 - 昨日净值) / 昨日净值
         = 今日净值 / 昨日净值 - 1
```

例：净值从 1.00 涨到 1.03，简单收益 = +3%。

**对数收益率**（量化里更常用）：

```
r_log = ln(今日净值 / 昨日净值)
```

例：同样从 1.00 到 1.03，对数收益 = ln(1.03) ≈ +2.956%。

### 2.2 为什么对数收益可加，简单收益不可加

假设连续两天：第一天涨 3%，第二天跌 3%。

**用简单收益相加（错误做法）：** `+3% + (-3%) = 0%` ❌

**真实结果（复利相乘）：** `1.03 × 0.97 = 0.9991`，即 **-0.09%**。

**用对数收益相加（正确）：** `ln(1.03) + ln(0.97) = 0.02956 + (-0.03046) = -0.0009`，即 **-0.09%** ✓

关键性质：

```
ln(A/B) + ln(B/C) = ln(A/C)
```

也就是说，**对数收益可以直接相加得到区间累计收益**，这就是为什么做多日累计、做回测时量化界都用对数收益。

> **实务约定：** 单日展示给用户看的用**简单收益**（符合直觉、和支付宝一致）；多日累计、算波动率/夏普这些统计量时，用**对数收益**或正确的复利相乘。你现在的 `cumulative += daily_return` 属于"简单收益硬相加"，是近似，点数大时会失真。

### 2.3 年化（把"日"换算成"年"）

A 股一年约 **252 个交易日**（这是行业惯例常数）。

**年化收益率**（几何年化，正确）：

```
年化收益 = (1 + 区间总收益) ^ (252 / 区间交易日数) - 1
```

**年化波动率**：日波动率乘以 √252：

```
年化波动率 = 日波动率 × sqrt(252)
```

> 为什么是 √252 而不是 252？因为波动率是标准差，方差才线性可加（方差 × 252），标准差是方差开根号，所以乘 √252。这是一个你记住就好的换算规则。

### 2.4 波动率 = 收益率的标准差

**波动率**就是"收益率围绕平均值的离散程度"，用标准差衡量。波动越大，组合越"刺激"，风险越高。

样本标准差公式：

```
mean = (Σ rᵢ) / n
variance = Σ (rᵢ - mean)² / (n - 1)    ← 注意分母是 n-1（样本标准差）
std = sqrt(variance)
```

> 分母用 `n-1` 而不是 `n`，是统计学里的"贝塞尔校正"，用样本估计总体时更无偏。金融实务里两者都有人用，我们统一用 `n-1`（和大多数库默认一致）。

---

## 3. 七个核心风险指标详解

每个指标的结构：**① 一句话直觉 → ② 公式 → ③ 数值例子 → ④ 用你的数据怎么算**。

你的数据来源统一是：`list_portfolio_daily_snapshots()` 返回的每日快照，关键字段 `daily_return_percent`（组合当日简单收益率，%）。基准指数日收益来自 `fetch_index_daily_history("000300")`（沪深 300）。

### 3.1 波动率 Volatility

**① 直觉：** 你的组合每天波动有多剧烈。两个组合都赚了 10%，但一个一路平稳、一个坐过山车，后者波动率高、风险大。

**② 公式：**

```
日波动率   = std(每日收益率序列)
年化波动率 = 日波动率 × sqrt(252)
```

**③ 例子：** 近 5 日收益率 `[+1%, -0.5%, +0.8%, -1.2%, +0.6%]`，均值 = 0.14%，样本标准差 ≈ 0.93%，年化 ≈ 0.93% × 15.87 ≈ **14.8%**。

**④ 用你的数据：** 取近 N 日快照的 `daily_return_percent`，算标准差再 ×√252。

> **解读话术（给用户看）：** "你的组合年化波动率 18%，介于纯债（<5%）和单只股票（>30%）之间，属于中等波动。"

---

### 3.2 最大回撤 Max Drawdown（MDD）⭐ 用户最在意

**① 直觉：** 从历史任意一个高点，最多跌过多少。这是用户最怕、最有体感的数字——"我最惨的时候亏了多少"。

**② 公式：** 先把日收益累计成"净值曲线"，然后对每一天，算它距离"此前出现过的最高点"跌了多少，取最大值。

```
净值曲线 equity[i] = ∏(1 + rₖ)  for k in 0..i      （复利累乘，不是相加！）
峰值 peak[i] = max(equity[0..i])
回撤 drawdown[i] = (equity[i] - peak[i]) / peak[i]   ← 负数
最大回撤 MDD = min(drawdown[i])                       ← 最负的那个
```

**③ 例子：** 净值走 `1.00 → 1.10 → 1.05 → 0.95 → 1.02`。峰值在 1.10。最低点 0.95 相对峰值 1.10 = `(0.95-1.10)/1.10 = -13.6%`，所以 MDD = **-13.6%**。注意最后回到 1.02 不影响 MDD（MDD 记录的是历史最痛点）。

**④ 用你的数据：** 用快照的 `daily_return_percent` 复利累乘成净值曲线（**这里必须用复利，不能用你现在的百分比相加**），再扫描求最大回撤。

> **解读话术：** "你的组合最大回撤 -15%，意味着最坏情况下你要扛得住账户从高点缩水 15% 不慌。"

---

### 3.3 夏普比率 Sharpe Ratio ⭐ 衡量"性价比"

**① 直觉：** 你每承担 1 单位风险（波动），换来了多少超额回报。**夏普是量化里最重要的单一指标**——它回答"这个收益值不值得你担的风险"。

**② 公式：**

```
夏普 = (组合年化收益 - 无风险利率) / 组合年化波动率
```

- **无风险利率**：你不冒险也能拿到的收益，A 股语境用**一年期国债 / 银行定存**，通常取 **2%~3%**（可做成配置项，默认 2%）。
- 分子是"超额收益"，分母是"风险"。

**③ 例子：** 年化收益 12%，无风险 2%，年化波动 20% → 夏普 = (12%-2%)/20% = **0.5**。

**夏普参考刻度：**

| 夏普 | 评价 |
|------|------|
| < 0 | 还不如存银行 |
| 0 ~ 1 | 一般 |
| 1 ~ 2 | 良好 |
| 2 ~ 3 | 优秀 |
| > 3 | 极好（散户很难持续做到） |

**④ 用你的数据：** 年化收益和年化波动都从快照算（见 3.1 和 2.3），无风险利率从配置读。

> **解读话术：** "你的组合夏普 0.8，说明收益尚可但波动偏大，性价比一般。"

---

### 3.4 索提诺比率 Sortino Ratio

**① 直觉：** 夏普的改良版。夏普把"上涨波动"也当成风险来惩罚，但用户其实不怕涨、只怕跌。索提诺**只惩罚下行波动**，更贴合人的真实感受。

**② 公式：** 和夏普几乎一样，只是分母换成"下行波动率"（只统计收益为负，或低于目标值的那些天）。

```
下行波动率 = std(只取 rᵢ < 0 的收益率)   （更严谨是 rᵢ < 目标收益，目标常取 0）
索提诺 = (年化收益 - 无风险利率) / 年化下行波动率
```

**③ 例子：** 同样的组合，如果上涨贡献了大部分波动，索提诺会明显高于夏普——说明波动主要来自"赚钱方向"，是好事。

**④ 用你的数据：** 从快照收益序列里筛出负收益日算标准差。

> **解读话术：** "你的索提诺 1.3 高于夏普 0.8，说明波动主要来自上涨，下跌其实比较温和。"

---

### 3.5 Beta（β）与 Alpha（α）

**① 直觉：**
- **Beta** = 你的组合对大盘的"敏感度"。大盘涨 1%，β=1.2 的组合平均涨 1.2%（更激进），β=0.8 则涨 0.8%（更防守）。
- **Alpha** = 剔除大盘影响后，你**靠自己**多赚的部分。Alpha > 0 = 真本事；Alpha < 0 = 跑输大盘。

**② 公式（线性回归 / 协方差法）：**

```
Beta  = Cov(组合收益, 指数收益) / Var(指数收益)
Alpha = 组合年化收益 - [无风险 + Beta × (指数年化收益 - 无风险)]
```

- `Cov` 是协方差（两个序列一起变动的程度），`Var` 是方差。
- Alpha 这个公式来自 CAPM 模型，直觉是："按你的 Beta，理论上该赚这么多；实际多赚的就是 Alpha。"

**③ 例子：** 组合年化 12%，沪深 300 年化 8%，无风险 2%，Beta=1.0 → 理论收益 = 2% + 1.0×(8%-2%) = 8%，实际 12%，Alpha = **+4%**（不错的超额）。

**④ 用你的数据：** 你**已经在 `summarize_trend_footer` 里算 `alpha_percent = portfolio - index`** 了——但那是**简单的收益差**，不是 CAPM Alpha（没考虑 Beta）。本模块把它升级成正经的 Beta/Alpha。需要组合日收益序列 + 沪深 300 日收益序列**对齐日期**后做回归。

> **解读话术：** "你的组合 Beta 1.15、Alpha +3%，说明你比大盘更激进，且在承担更高风险的同时还跑赢了大盘 3%。"

---

### 3.6 持仓相关性矩阵 Correlation Matrix

**① 直觉：** 你买的几只基金，是不是其实在赌同一个方向？相关性接近 1 = 高度同涨同跌 = **假分散**（你以为买了 5 只很安全，其实等于重仓一个方向）。

**② 公式：** 两只基金收益序列的皮尔逊相关系数：

```
corr(A, B) = Cov(A, B) / (std(A) × std(B))     取值 [-1, 1]
```

- +1 完全同向，0 不相关，-1 完全反向（对冲）。

**③ 例子：** 半导体基金 A 和芯片 ETF B 相关性 0.95 → 几乎是一只；A 和黄金基金 C 相关性 -0.1 → 真分散。

**④ 用你的数据：** 这一项**需要每只持仓的净值历史**，你已经有 `GET /api/fund-profiles/{code}/nav-history`（AkShare）。取每只基金近 N 日净值算日收益序列，两两算相关系数，输出 N×N 矩阵。

> **解读话术：** "你的 3 只基金两两相关性都 >0.9，等于重仓一个方向，建议分散到低相关的板块。"
>
> **实现提示：** 相关性矩阵需要额外拉每只基金的净值历史，比其他指标重一些。可以作为模块 1 的**第二批**功能（先上波动率/回撤/夏普/Beta，相关性矩阵单独一个 PR）。

---

### 3.7 HHI 集中度指数 Herfindahl-Hirschman Index

**① 直觉：** 衡量持仓"鸡蛋是不是放一个篮子"。比你现在的"单只占比超 35% 告警"更科学——它用一个数字概括整个组合的集中程度。

**② 公式：** 各持仓权重的平方和。

```
HHI = Σ (wᵢ)²          wᵢ 是第 i 只的权重（小数，Σwᵢ = 1）
```

- 全压一只：HHI = 1²= **1.0**（最集中）。
- 平均分 5 只：HHI = 5 × 0.2² = **0.2**。
- 平均分 N 只：HHI = 1/N。
- **有效持仓数** = 1 / HHI（直觉化表达："你实际上相当于分散在几只"）。

**③ 例子：** 持仓权重 `[50%, 30%, 20%]` → HHI = 0.25+0.09+0.04 = **0.38**，有效持仓数 = 1/0.38 ≈ **2.6 只**（虽然买了 3 只，实际分散度只相当于 2.6 只）。

**④ 用你的数据：** 直接用 `holding_amount` 算权重，**无需任何外部数据**（最容易实现，建议第一个写）。

> **解读话术：** "你的 HHI 0.38、有效持仓数 2.6，集中度偏高，抗单一板块风险的能力较弱。"

---

### 3.8 指标实现难度与数据依赖速查

| 指标 | 数据来源 | 额外网络请求 | 实现难度 | 建议批次 |
|------|---------|------------|---------|---------|
| HHI 集中度 | `holding_amount`（现成） | 无 | ★ | 第一批 |
| 波动率 | 快照 `daily_return_percent` | 无 | ★ | 第一批 |
| 最大回撤 | 快照 `daily_return_percent` | 无 | ★★ | 第一批 |
| 夏普 | 快照 + 无风险利率配置 | 无 | ★★ | 第一批 |
| 索提诺 | 快照收益序列 | 无 | ★★ | 第一批 |
| Beta / Alpha | 快照 + 沪深300日线（已有接口） | 有（指数日线，已缓存） | ★★★ | 第一批 |
| 相关性矩阵 | 每只基金 nav-history（已有接口） | 有（每只一次） | ★★★ | 第二批 |

**结论：** 第一批六个指标**全部只依赖你已有的日快照 + 已缓存的沪深 300 日线**，不需要新数据源。相关性矩阵因为要逐只拉净值，放第二批。

---

## 4. 数据来源映射（用你现有的表，不新建数据）

这是本模块"成本低"的核心原因——所需数据你几乎都有。

| 需要的输入 | 来源函数 / 字段 | 文件 |
|-----------|----------------|------|
| 组合每日收益序列 | `list_portfolio_daily_snapshots(limit=N)` → 每行 `daily_return_percent` | `app/database.py` |
| 当前持仓权重 | `Holding.holding_amount` | `app/models.py` |
| 沪深 300 日线（基准） | `fetch_index_daily_history("000300", trading_days=N)` | `index_daily_client.py` |
| 沪深 300 日收益换算 | `_index_daily_change_lookup()`（已有，可复用） | `portfolio_profit_analysis.py` |
| 单只基金净值历史（相关性用） | `GET /api/fund-profiles/{code}/nav-history` 对应服务 | `fund_nav_service.py` |
| 无风险利率 | **新增配置** `FUND_AI_RISK_FREE_RATE`（默认 0.02） | `config.py` |

**快照行结构提醒**（来自 `portfolio_snapshot.py`）：每行有 `snapshot_date`、`total_assets`、`daily_profit`、`daily_return_percent`、`holdings`。其中 `daily_return_percent` 就是我们要的组合当日收益率（%）。

> **数据量要求：** 风险指标需要**足够样本**才有意义。建议最少 **20 个交易日**快照才计算（不足时返回 `available=false` + 友好提示），**60+ 日**结果才稳定。这也意味着新用户要用一阵子才能看到这些指标——这反而是"留存钩子"。

---

## 5. 后端实现骨架

新建独立服务 `app/services/portfolio_risk_metrics.py`，**不要塞进 `risk.py`**（`risk.py` 管"阈值告警"，本模块管"统计度量"，职责分离）。

骨架用**纯 Python 标准库**（`math` + `statistics`），每一步公式都看得见，便于你对照第 3 章学习。等你熟了想提速可换 numpy（akshare 已带）。

```python
# app/services/portfolio_risk_metrics.py
"""组合风险度量：波动率、夏普、索提诺、最大回撤、Beta/Alpha、HHI。

数据来源：portfolio_daily_snapshots（组合日收益）+ 沪深300日线（基准）。
设计文档：docs/superpowers/specs/2026-06-24-portfolio-risk-metrics-design.md
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, asdict

TRADING_DAYS_PER_YEAR = 252
MIN_SAMPLE_DAYS = 20            # 少于此样本不计算
DEFAULT_RISK_FREE_RATE = 0.02  # 年化无风险利率，可被 config 覆盖


# ---------- 基础工具：收益序列处理 ----------

def _to_decimal_returns(daily_return_percents: list[float]) -> list[float]:
    """把百分比收益（如 1.5 表示 +1.5%）转成小数（0.015）。"""
    return [float(p) / 100.0 for p in daily_return_percents if p is not None]


def _equity_curve(returns: list[float]) -> list[float]:
    """复利累乘成净值曲线，起点 1.0。注意：用相乘，不是相加（见文档 Bug A）。"""
    equity = []
    value = 1.0
    for r in returns:
        value *= (1.0 + r)
        equity.append(value)
    return equity


def _cumulative_return(returns: list[float]) -> float:
    """区间总收益（小数）。"""
    if not returns:
        return 0.0
    return _equity_curve(returns)[-1] - 1.0


def _annualized_return(returns: list[float]) -> float:
    """几何年化收益。"""
    n = len(returns)
    if n == 0:
        return 0.0
    total = _cumulative_return(returns)
    return (1.0 + total) ** (TRADING_DAYS_PER_YEAR / n) - 1.0


def _daily_volatility(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns)  # 样本标准差，分母 n-1


def _annualized_volatility(returns: list[float]) -> float:
    return _daily_volatility(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _downside_volatility(returns: list[float], target: float = 0.0) -> float:
    """下行波动率：只统计低于 target 的收益。"""
    downside = [r for r in returns if r < target]
    if len(downside) < 2:
        return 0.0
    # 注意：分母用全样本天数还是下行天数，业界有分歧；这里用下行样本的样本std。
    return statistics.stdev(downside) * math.sqrt(TRADING_DAYS_PER_YEAR)


# ---------- 核心指标 ----------

def _sharpe(returns: list[float], risk_free_rate: float) -> float | None:
    vol = _annualized_volatility(returns)
    if vol == 0:
        return None
    return (_annualized_return(returns) - risk_free_rate) / vol


def _sortino(returns: list[float], risk_free_rate: float) -> float | None:
    dvol = _downside_volatility(returns)
    if dvol == 0:
        return None
    return (_annualized_return(returns) - risk_free_rate) / dvol


def _max_drawdown(returns: list[float]) -> float:
    """返回最大回撤（负数小数，如 -0.15 表示 -15%）。"""
    equity = _equity_curve(returns)
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = (value - peak) / peak
        mdd = min(mdd, drawdown)
    return mdd


def _beta_alpha(
    portfolio_returns: list[float],
    index_returns: list[float],
    risk_free_rate: float,
) -> tuple[float | None, float | None]:
    """对齐后的两条日收益序列求 Beta 和 CAPM Alpha。"""
    n = min(len(portfolio_returns), len(index_returns))
    if n < 2:
        return None, None
    p = portfolio_returns[-n:]
    m = index_returns[-n:]
    var_m = statistics.pvariance(m)  # 用总体方差与协方差口径一致
    if var_m == 0:
        return None, None
    mean_p, mean_m = statistics.mean(p), statistics.mean(m)
    cov = sum((p[i] - mean_p) * (m[i] - mean_m) for i in range(n)) / n
    beta = cov / var_m
    # Alpha（年化）：实际年化 - CAPM 理论年化
    ann_p = _annualized_return(p)
    ann_m = _annualized_return(m)
    alpha = ann_p - (risk_free_rate + beta * (ann_m - risk_free_rate))
    return beta, alpha


def _hhi(weights: list[float]) -> float:
    """权重平方和（weights 为小数，和为 1）。"""
    return sum(w * w for w in weights)


# ---------- 对外主函数 ----------

@dataclass
class PortfolioRiskMetrics:
    available: bool
    sample_days: int
    message: str | None = None
    annualized_return_percent: float | None = None
    annualized_volatility_percent: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown_percent: float | None = None
    beta: float | None = None
    alpha_percent: float | None = None
    hhi: float | None = None
    effective_holdings: float | None = None


def compute_portfolio_metrics(
    *,
    portfolio_daily_returns: list[float],   # 单位：百分比，如 [1.2, -0.5, ...]
    index_daily_returns: list[float],       # 同上，沪深300，已按日期对齐
    holding_amounts: list[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> PortfolioRiskMetrics:
    returns = _to_decimal_returns(portfolio_daily_returns)
    n = len(returns)
    if n < MIN_SAMPLE_DAYS:
        return PortfolioRiskMetrics(
            available=False,
            sample_days=n,
            message=f"历史快照不足 {MIN_SAMPLE_DAYS} 个交易日，暂无法计算风险指标。",
        )

    index_returns = _to_decimal_returns(index_daily_returns)
    beta, alpha = _beta_alpha(returns, index_returns, risk_free_rate)

    total_amount = sum(a for a in holding_amounts if a and a > 0)
    weights = [a / total_amount for a in holding_amounts if a and a > 0] if total_amount > 0 else []
    hhi = _hhi(weights) if weights else None

    def pct(x: float | None) -> float | None:
        return round(x * 100, 2) if x is not None else None

    return PortfolioRiskMetrics(
        available=True,
        sample_days=n,
        annualized_return_percent=pct(_annualized_return(returns)),
        annualized_volatility_percent=pct(_annualized_volatility(returns)),
        sharpe_ratio=round(_sharpe(returns, risk_free_rate), 2) if _sharpe(returns, risk_free_rate) is not None else None,
        sortino_ratio=round(_sortino(returns, risk_free_rate), 2) if _sortino(returns, risk_free_rate) is not None else None,
        max_drawdown_percent=pct(_max_drawdown(returns)),
        beta=round(beta, 2) if beta is not None else None,
        alpha_percent=pct(alpha),
        hhi=round(hhi, 3) if hhi is not None else None,
        effective_holdings=round(1.0 / hhi, 1) if hhi else None,
    )
```

> **教学注记：**
> - `statistics.stdev` 是样本标准差（n-1），`statistics.pvariance` 是总体方差（n）。Beta 计算里协方差和方差都用总体口径（除以 n），保证一致。
> - 函数刻意写得碎，是为了让你对照第 3 章一个个看懂。生产里可以合并、可以换 numpy 向量化。
> - 这里 `compute_portfolio_metrics` **只接收纯数据**（收益数组、金额数组），不直接碰数据库——这叫"纯函数"，**极易写单元测试**（第 8 章）。取数的脏活由调用方（dashboard 装配层）做。

### 5.1 装配层：从快照取数喂给纯函数

在 `portfolio_snapshot.py` 的 `build_dashboard_payload` 里新增一段（或单独 helper）：

```python
def build_risk_metrics_payload(history_rows: list[dict], holdings_models: list[Holding]) -> dict:
    from app.services.portfolio_risk_metrics import compute_portfolio_metrics
    from app.services.portfolio_profit_analysis import _index_daily_change_lookup
    from app.services.index_daily_client import fetch_index_daily_history
    from app.config import get_risk_free_rate  # 新增

    # 1. 组合日收益序列（按日期升序）
    rows = list(reversed(history_rows))  # history_rows 是最新在前
    portfolio_returns = [r.get("daily_return_percent") for r in rows if r.get("daily_return_percent") is not None]

    # 2. 沪深300 对齐日收益
    index_lookup = _index_daily_change_lookup(fetch_index_daily_history("000300", trading_days=400))
    index_returns = [index_lookup.get(str(r.get("snapshot_date"))) for r in rows]
    index_returns = [v for v in index_returns if v is not None]

    # 3. 当前持仓金额
    holding_amounts = [h.holding_amount for h in holdings_models]

    metrics = compute_portfolio_metrics(
        portfolio_daily_returns=portfolio_returns,
        index_daily_returns=index_returns,
        holding_amounts=holding_amounts,
        risk_free_rate=get_risk_free_rate(),
    )
    return asdict(metrics)
```

> **对齐坑提醒：** 组合收益和指数收益必须**按相同日期对齐**再做 Beta/相关性。上面骨架做了简化（各自过滤 None 后按尾部 n 对齐），**严谨做法是按 `snapshot_date` 逐日 zip**，只保留两边都有的交易日。实现时建议先按日期配对，我在第 8 章测试里会强调这个点。

### 5.2 配置项：无风险利率

在 `app/config.py` 新增：

```python
def get_risk_free_rate() -> float:
    """年化无风险利率（小数）。默认 2%，可经环境变量覆盖。"""
    raw = os.getenv("FUND_AI_RISK_FREE_RATE", "0.02")
    try:
        value = float(raw)
    except ValueError:
        return 0.02
    # 容错：用户填 2 表示 2% 时归一到 0.02
    return value / 100 if value > 1 else value
```

`.env.example` 补一行：

```
# 风险指标无风险利率（年化，小数；默认 0.02 = 2%）
FUND_AI_RISK_FREE_RATE=0.02
```

---

## 6. API 设计

有两个方案，**推荐方案 A**（复用现有 dashboard 接口，前端少改）。

### 方案 A（推荐）：挂到现有 dashboard 响应里

`GET /api/portfolio/dashboard` 的返回里新增一个 `risk_metrics` 字段：

```jsonc
{
  "summary": { ... },
  "profit_trend": { ... },
  "profit_calendar": { ... },
  "daily_top5": { ... },
  "allocation": [ ... ],
  "risk_metrics": {                       // ← 新增
    "available": true,
    "sample_days": 63,
    "annualized_return_percent": 12.4,
    "annualized_volatility_percent": 18.2,
    "sharpe_ratio": 0.78,
    "sortino_ratio": 1.12,
    "max_drawdown_percent": -14.6,
    "beta": 1.15,
    "alpha_percent": 3.1,
    "hhi": 0.38,
    "effective_holdings": 2.6
  }
}
```

改动点：`build_dashboard_payload` 末尾加 `"risk_metrics": build_risk_metrics_payload(history_rows, holdings_models)`。

**优点：** 前端 `PortfolioDashboard` 已经在调这个接口，直接读新字段即可，0 新增请求。
**缺点：** dashboard 接口会稍慢（多算一遍 + 可能拉指数日线）。但指数日线已有 1h 缓存，且计算是纯 CPU，影响很小。

### 方案 B（可选）：独立接口

```
GET /api/portfolio/risk-metrics?lookback_days=120
```

适合后续相关性矩阵（较重）单独按需加载。**建议第一批用 A，相关性矩阵第二批用 B 懒加载。**

### api.ts 封装

```typescript
// apps/web/src/lib/api.ts —— dashboard 返回类型补字段
export interface PortfolioRiskMetrics {
  available: boolean;
  sample_days: number;
  message?: string | null;
  annualized_return_percent: number | null;
  annualized_volatility_percent: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown_percent: number | null;
  beta: number | null;
  alpha_percent: number | null;
  hhi: number | null;
  effective_holdings: number | null;
}
// PortfolioDashboardResponse 里加 risk_metrics?: PortfolioRiskMetrics;
```

---

## 7. 前端展示 + Pro 付费点

### 7.1 新组件 `PortfolioRiskMetricsPanel.tsx`

放在「盈亏分析」Tab（`PortfolioDashboard.tsx`）里，收益走势图下方。卡片网格展示六个核心指标，每个带：数值 + 一句话解读 + 颜色（好=品牌蓝/绿，差=琥珀/玫红）。

```tsx
// 结构示意（非完整代码）
<section className="section-card">
  <h3 className="section-eyebrow">组合风险体检</h3>
  {!metrics.available ? (
    <div className="empty-state">{metrics.message ?? "数据积累中…"}</div>
  ) : (
    <div className="risk-metrics-grid">
      <MetricCard label="夏普比率" value={metrics.sharpe_ratio} hint={sharpeHint(metrics.sharpe_ratio)} />
      <MetricCard label="最大回撤" value={fmtPct(metrics.max_drawdown_percent)} tone="danger" />
      <MetricCard label="年化波动率" value={fmtPct(metrics.annualized_volatility_percent)} />
      <MetricCard label="Beta（对沪深300）" value={metrics.beta} hint={betaHint(metrics.beta)} />
      <MetricCard label="Alpha（超额）" value={fmtPct(metrics.alpha_percent)} tone={metrics.alpha_percent! >= 0 ? "good" : "danger"} />
      <MetricCard label="有效持仓数" value={metrics.effective_holdings} hint={`HHI ${metrics.hhi}`} />
    </div>
  )}
</section>
```

`sharpeHint` / `betaHint` 等解读 helper 放 `apps/web/src/lib/riskMetrics.ts`，配 vitest（把第 3 章的"参考刻度"写成函数）。

### 7.2 接入「好基灵 Pro」付费点

你 `LandingPage.tsx` 已经埋了 Pro 钩子。这里是**第一个真实可交付的 Pro 功能**：

- **免费版：** 只显示「最大回撤」+「有效持仓数」（让用户尝到甜头）。
- **Pro 版：** 解锁夏普 / 索提诺 / Beta / Alpha / 波动率 + 相关性矩阵 + 解读话术。
- 前端用一个 `isPro` 开关 + 蒙层（`.plan-card.is-pro` 你已有样式），未订阅时核心指标打码显示 + "升级解锁"。

> **注意：** 后端**照常返回全部指标**，前端做展示门控即可（私有部署 ≤5 人，不需要做严格的服务端鉴权门控；等真正对外 SaaS 再加）。这符合你"先验证、再加严"的节奏。

---

## 8. 测试设计

`compute_portfolio_metrics` 是纯函数 → 单元测试极好写。新建 `apps/api/tests/test_portfolio_risk_metrics.py`。

### 8.1 用"已知答案"的构造数据测公式正确性

```python
from app.services.portfolio_risk_metrics import (
    _equity_curve, _max_drawdown, _hhi, compute_portfolio_metrics,
)

def test_equity_curve_uses_compounding_not_addition():
    # 涨3%再跌3% → 不是0，而是 -0.09%
    curve = _equity_curve([0.03, -0.03])
    assert round(curve[-1] - 1.0, 4) == -0.0009

def test_max_drawdown_known_case():
    # 净值 1.00→1.10→1.05→0.95→1.02，峰值1.10，谷0.95
    returns = [0.10, -0.0454545, -0.0952381, 0.0736842]
    mdd = _max_drawdown(returns)
    assert round(mdd, 3) == -0.136   # -13.6%

def test_hhi_and_effective_holdings():
    assert round(_hhi([0.5, 0.3, 0.2]), 2) == 0.38

def test_insufficient_sample_returns_unavailable():
    m = compute_portfolio_metrics(
        portfolio_daily_returns=[1.0] * 5,   # 只有5天 < MIN_SAMPLE_DAYS
        index_daily_returns=[0.5] * 5,
        holding_amounts=[100.0],
    )
    assert m.available is False
    assert m.sample_days == 5
```

### 8.2 测试要点清单

- ✅ **复利 vs 相加**：涨跌互抵后是负数（验证没掉回 Bug A）。
- ✅ **最大回撤**：用手算的已知净值序列。
- ✅ **HHI**：手算权重平方和。
- ✅ **样本不足**：< 20 天返回 `available=false`。
- ✅ **零波动**：所有收益相同 → 波动率 0 → 夏普返回 `None`（不能除零）。
- ✅ **Beta 日期对齐**：组合和指数序列长度不同时不报错、取交集。
- ✅ **空持仓**：`holding_amounts=[]` → HHI 为 None，不崩。

> **建议用 hypothesis**（你项目已装）：对随机收益序列断言"波动率 ≥ 0""HHI ∈ (0,1]""MDD ≤ 0"等不变量，比手写用例更能抓边界 bug。

### 8.3 CI 注意

这是纯计算、无网络，天然适配你 CI 的离线策略。`conftest.py` 已 stub 指数日线，装配层测试也能离线跑。

---

## 9. 顺带修正两个量化 bug

模块 1 不强依赖这两个修复，但建议**同一批顺手修 Bug A**（风险度量也要正确累计收益）。

### 9.1 Bug A：累计收益用复利而非相加

`portfolio_profit_analysis.py` 的 `build_daily_trend_series`：

```python
# 现在（近似，点数大时失真）
cumulative_portfolio += float(daily_return)

# 建议改为复利累乘
portfolio_equity *= (1 + float(daily_return) / 100)
cumulative_portfolio = (portfolio_equity - 1) * 100
```

> **权衡：** 这会让收益走势图的累计数字更准，但**会和盈亏日历的"日度相加"口径产生细微差异**。你文档里多处写了"日度相加近似"，说明你是有意为之。**建议：** 展示层（日历、走势图）可保留简单相加以维持口径统一；但**风险指标内部必须用复利**（骨架里 `_equity_curve` 已经是复利）。即"展示可近似，计算要精确"。

### 9.2 Bug B：命中率基准不应是固定 50%

`sector_signal_backtest.py` 的 `_finalize_aggregate`：

```python
# 现在
"beats_random": hit_rate is not None and hit_rate > 50.0,
```

**正确做法：** 基准应是该板块"无脑买入持有"的上涨日占比（base rate），并加**统计显著性**判断：

```python
# 伪代码思路
base_rate = 上涨日数 / 总交易日数 * 100        # 这个板块本身的"自然上涨率"
edge = hit_rate - base_rate                      # 真正的超额（信号有没有用）
# 显著性：触发次数太少（如 < 30）时不下结论
significant = trigger_count >= 30 and edge > 5   # 阈值可调
"beats_baseline": significant
```

> **这属于模块 3（回测框架）的范畴**，模块 1 可以先不动 `sector_signal_backtest`，只在文档里标记"已知问题，模块 3 修"。这里写出来是让你理解"50% 基准"为什么是错的。

---

## 10. 实施清单与验收标准

### 10.1 第一批（建议 1～2 周，边学边做）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 新增无风险利率配置 | `config.py`、`.env.example` | — |
| 2 | 写纯函数 `portfolio_risk_metrics.py`（HHI→波动率→回撤→夏普→索提诺→Beta/Alpha） | 新建 | — |
| 3 | 写单元测试（含已知答案 + hypothesis 不变量） | `tests/test_portfolio_risk_metrics.py` | 任务 2 |
| 4 | 装配层 `build_risk_metrics_payload` + 接进 dashboard 响应 | `portfolio_snapshot.py` | 任务 2 |
| 5 | `api.ts` 加类型；`PortfolioRiskMetricsPanel.tsx` + `riskMetrics.ts` 解读 helper | `apps/web` | 任务 4 |
| 6 | Pro 门控（免费显 2 项、Pro 全解锁） | `PortfolioRiskMetricsPanel.tsx` | 任务 5 |
| 7 | 顺手修 Bug A（仅风险计算内部用复利） | `portfolio_risk_metrics.py` 已含 | — |
| 8 | 更新 `PROJECT_CONTEXT.md`（能力清单 + API + 环境变量） | `docs/PROJECT_CONTEXT.md` | 全部 |

### 10.2 第二批（相关性矩阵）

| # | 任务 | 说明 |
|---|------|------|
| 9 | 独立接口 `GET /api/portfolio/risk-correlation` | 逐只拉 nav-history，较重，懒加载 |
| 10 | 相关性矩阵计算 + 热力图组件 | 前端 N×N 热力图 |

### 10.3 验收标准

- [ ] 至少 20 日快照时，dashboard 返回完整 `risk_metrics`；不足时 `available=false` 且有友好文案。
- [ ] 所有指标单测通过（含复利、回撤、HHI 的已知答案用例）。
- [ ] `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q` 全绿。
- [ ] `cd apps/web && npm run lint && npm run typecheck && npm run build` 通过。
- [ ] 「盈亏分析」Tab 能看到风险体检卡片；免费/Pro 门控生效。
- [ ] `PROJECT_CONTEXT.md` 已同步。

---

## 11. 学习路径回顾（你接下来的量化主线）

本模块是路线图第 1 站。完整路线（来自规划）：

| 模块 | 内容 | 状态 |
|------|------|------|
| 0 | 收益与风险的语言（对数收益、波动率、年化） | 本文第 2 章已覆盖 |
| **1** | **组合风险度量（夏普/回撤/Beta/相关性/HHI）** | **本文** |
| 2 | 因子思维（动量/价值因子、z-score、IC 信息系数） | 待出文档 |
| 3 | 像样的回测（前视偏差、walk-forward、显著性、修 Bug B） | 待出文档 |
| 4 | 信号合成 + AI 闭环（可信度打分器、量化结论喂 LLM） | 待出文档 |

**核心架构愿景**（模块 4 终点）：

```
现状：  持仓 + 新闻 ──► DeepSeek ──► 文字建议（不可回测、不可问责）

目标：  持仓 + 行情时序 ──► 量化引擎（因子/信号/风险度量）
                              ├─► 结构化结论（胜率、敞口、夏普）
                              └─► DeepSeek（把量化结论翻译成人话 + 新闻佐证）
```

让 LLM 从"分析师"退回"沟通者"，每条建议挂一个可回测的数字——这是和"接个 GPT 写两句"的产品拉开差距的护城河。

---

*文档结束。实现时如对任一指标或骨架有疑问，回到第 2、3 章对照概念，或让我针对某个指标展开讲 + 写完整实现。*
