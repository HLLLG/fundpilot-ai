# 模块 2｜因子思维：持仓因子体检 — 技术设计文档（教学版）

> **文档定位：** 量化升级路线图的第 2 个可落地模块（第一期）。延续模块 1 的「教学 + 工程骨架」风格：每个概念先讲直觉、再给公式、再用你项目里**已有的数据**演示怎么算，最后给出后端/API/前端/测试的完整骨架。
>
> **读者：** 量化只懂皮毛、想边学边做的你。读完应能：① 看懂「因子」「横截面」「z-score」「百分位」是什么，跟模块 1 的「时间序列指标」有什么本质区别；② 知道这些分数从你现有的哪个接口、哪个字段来；③ 照着骨架把 `compute_factor_scores()` 写出来并接入「盈亏分析」Tab 与「好基灵 Pro」。
>
> **关联：** 路线图「模块 2｜因子思维（动量/价值因子、z-score、IC 信息系数）」。模块 1（组合风险度量）已完成，见 `docs/superpowers/specs/2026-06-24-portfolio-risk-metrics-design.md`。
>
> **范围（第一期，本文）：** 用**只靠净值/排行榜就能算**的因子（动量、风险调整收益、回撤控制、规模）给**你的每只持仓**打横截面因子分。价值因子、IC 信息系数因依赖额外数据 / 回测框架，**明确归入模块 3**，本文只解释为什么。
>
> **版本：** 2026-06-24 初稿。

---

## 0. 怎么用这份文档

1. **先读第 1～2 章**：搞懂「因子思维」和模块 1 的「风险度量」差在哪——一个是**横截面**（横着比一群基金），一个是**时间序列**（竖着看一只基金的历史）。这是模块 2 最大的概念坎。
2. **第 3 章**讲四个因子的定义和直觉。
3. **第 4 章**是模块 2 的数学核心：去极值 → z-score → 合成 → 百分位。看懂这一章，你就懂了所有「多因子打分」产品（韭圈儿、晨星）底层在干嘛。
4. **第 5 章起是工程落地**：数据映射 → 纯函数骨架 → 装配层 → API → 前端 → 测试。
5. 不需要一次写完。建议顺序：**纯函数引擎 + 单测**（第 5、9 章）先跑通，再做装配层 + API（第 6、7 章），最后前端 + Pro 门控（第 8 章）。

---

## 1. 背景：你为什么需要「因子」

### 1.1 模块 1 vs 模块 2：一个竖着看，一个横着比

模块 1 回答的是**「这一只（或这一个组合）历史表现怎么样」**——夏普、回撤、波动率，全是把**一条收益序列**从头扫到尾算出来的统计量。这叫**时间序列（time-series）**视角。

但用户真正的问题往往是**比较级**的：

> "我这只半导体基金，**在同类里**算强还是弱？"
> "我买的 3 只基金，哪只是**拖后腿**的那个？"

要回答这个，你必须把**一群基金摆在一起横着比**。这叫**横截面（cross-section）**视角。「因子」就是横截面比较的**标尺**：

- **动量因子**：把所有基金按「近期涨幅」排队，你的基金排在哪。
- **风险调整因子**：把所有基金按「每单位回撤换来的收益」排队，你的基金排在哪。

> **一句话区别：** 模块 1 是「**你**这只基金跌过 -15%」；模块 2 是「你这只基金的回撤控制能力**打败了同类里 78% 的基金**」。后者才有比较的锚，普通用户更有体感。

### 1.2 竞品在怎么做（调研结论）

| 竞品 | 做法 | 我们借鉴什么 |
|------|------|------------|
| **韭圈儿**（toC 龙头） | 基金评分、大佬评分、组合回测、估值情绪 | 评分必须「**一个综合分 + 几个可拆解维度**」，普通基民才看得懂 |
| **晨星投资风格箱** | 用重仓股「市值 + 价值/成长」把基金钉到 3×3 风格箱 | 经典「风格」分类，但**需要重仓股估值数据**（我们暂时没有） |
| **华泰 / 东财 基金评价框架** | **用净值对风格指数做时序回归**反推风格暴露，再叠收益/风控因子库 | 关键启发：**只用净值就能近似算价值/成长暴露**——但工程较重，归模块 3 |
| **标普 / Smart Beta** | A 股因子分三类：成长（进取）、行为（中性）、价值（防御） | 因子分类与解读话术可借鉴 |

**结论：** 第一期我们做「**净值系**」因子——纯靠净值序列和排行榜横截面就能算、能讲清楚、不烧网络的那批。价值/质量/经理类因子需要额外数据源或回归框架，归模块 3。

### 1.3 第一期不做什么（诚实划界）

| 不做 | 原因 | 去向 |
|------|------|------|
| **价值因子（重仓股 PE/PB、股息）** | 现有代码完全没有基金层面或重仓加权估值字段 | 模块 3（新增 AkShare 持仓明细 + 个股估值，或风格回归） |
| **质量因子（ROE、盈利稳定性）** | 同上，无数据 | 模块 3 |
| **基金经理因子（任期、从业年限）** | 无数据 | 模块 3 |
| **IC 信息系数** | IC 要在**大横截面**上算「因子值 vs 未来收益」的相关性，本质是**回测**；你的持仓只有几只，算 IC 无统计意义 | 模块 3（回测框架，在基金池上算因子 IC） |

> **关于 IC，多说一句（这是模块 2 该懂的概念）：** IC（Information Coefficient，信息系数）衡量「某因子在 T 日的排序，能不能预测 T+1～T+N 的收益排序」。计算方式是：取一个**基金池**，算每只基金的因子值（横截面），再算每只基金之后一段时间的真实收益，求这两列的相关系数（常用 Rank IC = 斯皮尔曼相关）。IC 越高、越稳定，说明这个因子越「有用」。**它需要：① 足够大的横截面；② 未来收益（要等时间过去 / 用历史回放）。** 这两点都属于模块 3「像样的回测」的范畴，所以本期不做，但你要知道：模块 2 给因子打的分，到了模块 3 会用 IC 来检验「这些因子到底有没有用」。

---

## 2. 预备知识：横截面统计的"语言"

这一章没有新功能，但它会让你看懂第 4 章所有公式。

### 2.1 横截面（cross-section）

**横截面 = 同一时刻、一群对象的某个属性排成一列。**

例：今天这 5 只基金的「近 6 月收益」：

```
基金A: +12%   基金B: +5%   基金C: +30%   基金D: -3%   基金E: +8%
```

这一列 5 个数，就是「近 6 月收益」这个因子在今天的一个**横截面**。因子打分，就是对这种「一列数」做统计。

### 2.2 z-score（标准分）

**问题：** 基金 A 近 6 月 +12%，这到底算高还是低？孤零零一个 +12% 没法判断——得看**同类都涨了多少**。

**z-score** 把「一个原始值」换算成「它离群体均值有几个标准差」：

```
z = (x − 群体均值 mean) / 群体标准差 std
```

- `z = 0`：正好等于平均水平。
- `z = +1`：比平均高 1 个标准差（大约排在前 16%）。
- `z = +2`：比平均高 2 个标准差（大约前 2.5%，很强）。
- `z = −1`：比平均低 1 个标准差（落后）。

**例：** 上面 5 只基金近 6 月收益均值 = 10.4%，样本标准差 ≈ 12.0%。基金 A 的 z = (12 − 10.4) / 12.0 ≈ **+0.13**（也就比平均略好一点点，并没有 +12% 看起来那么猛）。

> **z-score 的意义：** 它把「不同量纲、不同量级」的因子拉到**同一把尺子**上，这样「动量 z」和「回撤 z」才能相加合成。这是多因子打分的地基。

### 2.3 去极值（winsorize）

**问题：** 横截面里偶尔有「妖基」——某只主题基近 6 月暴涨 +180%。它会把均值拉高、把标准差撑大，导致其他正常基金的 z-score 全部失真。

**去极值 = 把超过某分位（如 5% / 95%）的极端值"压"回分位线上。**

```
下界 = 第 5 百分位值, 上界 = 第 95 百分位值
x_winsorized = min(max(x, 下界), 上界)
```

这样 +180% 会被压到「第 95 百分位的那个值」（比如 +45%），不再带歪整列的均值和标准差。**先去极值，再算均值/标准差/z-score。**

### 2.4 百分位（percentile）

z-score 对你（开发者）直观，但对普通用户不直观——"z = 1.2" 是什么鬼？

**百分位**把分数翻译成人话：**"你超过了池中百分之多少的基金"。**

```
某基金的百分位 = (池中综合分 ≤ 它的基金数量 / 池子总数) × 100
```

- 百分位 = 78 → "超过了池中 78% 的基金"。
- 百分位 = 50 → "正好中游"。

> **实务约定：** 内部计算用 **z-score**（可加、可合成）；**展示给用户**用 **百分位 + 字母等级**（A/B/C/D）。这就是韭圈儿/晨星给你看「评分 85 分」背后的套路。

---

## 3. 四个核心因子（净值系 MVP）

每个因子的结构：**① 一句话直觉 → ② 原始值公式 → ③ 方向 → ④ 用你的数据怎么取**。

**统一数据口径：** 全部用「开放式基金排行榜」(`fetch_open_fund_rank`) 已有的字段，保证横截面里每只基金的因子值**同源可比**：`return_3m_percent`、`return_6m_percent`、`return_1y_percent`、`max_drawdown_1y_percent`、`fund_scale_yi`。

### 3.1 动量因子 Momentum

**① 直觉：** 近期赚钱的势头。强者恒强是 A 股最常被验证的因子之一。

**② 原始值（多窗口加权，近期权重高）：**

```
momentum_raw = 0.5 × 近6月收益 + 0.3 × 近3月收益 + 0.2 × 近1年收益
```

**③ 方向：** 越高越好。

**④ 用你的数据：** 排行榜的 `return_6m_percent / return_3m_percent / return_1y_percent`；缺某窗口时用其余窗口归一加权（见第 5 章 `_blend_momentum`）。

> **解读话术：** "动量百分位 82——近期赚钱势头排在同类前列，但注意强动量也可能意味着已经涨了一波。"

### 3.2 风险调整收益因子 Risk-Adjusted（Calmar 式）

**① 直觉：** 模块 1 的夏普是「收益 / 波动」；这里用「收益 / 最大回撤」，叫 **Calmar 比率**。它回答："为了这点收益，你最惨要扛多大的跌？"

**② 原始值：**

```
calmar_raw = 近1年收益 / |近1年最大回撤|     （回撤取绝对值，避免除负数）
```

**③ 方向：** 越高越好。

**④ 用你的数据：** 排行榜 `return_1y_percent / max_drawdown_1y_percent`。

> **为什么这里用 Calmar 不用夏普？** 算全池 ~300 只的夏普要逐只拉净值算日波动（~300 次网络调用，太重）；而「收益 / 最大回撤」排行榜**直接有**，全池都能算，横截面同源。持仓自己的夏普可以在卡片里作为补充信息单独展示（复用模块 1 的 `_sharpe`），但**不进 z-score 合成**（口径不一致）。

> **解读话术：** "风险调整百分位 65——每承担 1 单位回撤换来的收益中等偏上。"

### 3.3 回撤控制因子 Drawdown

**① 直觉：** 跌起来扛不扛得住。和模块 1 的最大回撤同源，但这里是**横截面比较**——"你的抗跌能力在同类里排第几"。

**② 原始值：**

```
drawdown_raw = 近1年最大回撤（负数，如 -22 表示 -22%）
```

**③ 方向：** 越高（越接近 0）越好。`-8%` 比 `-30%` 好，数值上 `-8 > -30`，所以直接用原始负数算 z-score，方向天然正确。

**④ 用你的数据：** 排行榜 `max_drawdown_1y_percent`。

> **解读话术：** "回撤控制百分位 40——抗跌能力一般，近一年最深跌过 -22%。"

### 3.4 规模因子 Size

**① 直觉：** 基金规模。**方向是双刃**——太小（<1 亿）有清盘风险、流动性差；太大（>200 亿）调仓笨重、难做超额。所以规模不是「越大越好」，而是「适中为佳」，权重也最低。

**② 原始值（取对数压缩量级）：**

```
size_raw = log10(基金规模亿元)      （规模从 0.5 亿到 500 亿跨 3 个数量级，取 log 才好比）
```

**③ 方向：** 第一期简化为「适度正向」（偏大略稳健），但**权重只给 0.1**，避免它主导评分。规模的「适中惩罚」（太大反而扣分）留作后续优化。

**④ 用你的数据：** 排行榜 `fund_scale_yi`；持仓不在榜时从档案/基金概况 `fund_scale_yi` 取。

> **解读话术：** "规模 58 亿，适中——既无清盘风险，也不至于太笨重。"

### 3.5 因子速查

| 因子 | 原始值 | 方向 | 合成权重 | 数据字段 |
|------|--------|------|---------|---------|
| 动量 Momentum | 0.5×6月 + 0.3×3月 + 0.2×1年 | 越高越好 | **0.40** | `return_3m/6m/1y_percent` |
| 风险调整 Calmar | 1年收益 / \|1年回撤\| | 越高越好 | **0.35** | `return_1y_percent` ÷ `max_drawdown_1y_percent` |
| 回撤控制 Drawdown | 近1年最大回撤（负数） | 越高越好 | **0.15** | `max_drawdown_1y_percent` |
| 规模 Size | log10(规模亿元) | 适度正向 | **0.10** | `fund_scale_yi` |

> 权重是**可调常量**（`FACTOR_WEIGHTS`），不做成环境变量（避免过度配置）。等模块 3 用 IC 检验出各因子真实有效性后，再回来调权重。

---

## 4. 打分数学（模块 2 的核心算法）

这一章把第 2、3 章串成一条可执行的流水线。**输入**：一个基金池（横截面）+ 每只基金的 4 个因子原始值；**输出**：每只目标基金（你的持仓）的综合分、等级、各因子百分位。

### 4.1 五步流水线

```
第1步 收集横截面     ：池中每只基金的 [动量raw, calmar raw, 回撤raw, 规模raw]
第2步 逐因子去极值    ：每一列按 5%/95% 分位 winsorize
第3步 逐因子 z-score  ：z = (x − 列均值) / 列标准差，裁剪到 [-3, 3]
第4步 合成综合 z      ：综合z = Σ 权重ᵢ × 因子zᵢ
第5步 转百分位+等级    ：把目标基金的「综合z / 各因子z」放回池子分布求百分位 → 0-100 分 → A/B/C/D
```

### 4.2 数值小例子（手算一遍）

假设池子里 5 只基金的「动量 raw」（已去极值）：

```
[12, 5, 30, -3, 8]   均值 = 10.4   样本std ≈ 12.0
```

- 基金 C（raw=30）的动量 z = (30 − 10.4) / 12.0 ≈ **+1.63**
- 基金 D（raw=-3）的动量 z = (-3 − 10.4) / 12.0 ≈ **-1.12**

C 的动量百分位：池中 5 只里它的 z 最高，排第 1 → 百分位 = 5/5 × 100 = **100**（"动量打败池中所有基金"）。

### 4.3 边界与退化处理（必须写对，否则线上崩）

| 情况 | 处理 |
|------|------|
| 池子有效样本 < `MIN_UNIVERSE_SIZE`（默认 30） | 整体 `available=false` + 友好文案（横截面太小，z-score 无意义） |
| 某因子全池零方差（std=0） | 该因子所有 z 退化为 0（不能除零），其余因子照常 |
| 某只基金某因子缺失（None） | 该因子 z 记为 None，**合成时按"剩余权重归一"**（不要把缺失当 0，会误判为平均） |
| 持仓基金不在排行榜池 | 用本来就会拉的**持仓净值**算同样 4 个原始值（近 60/120/250 交易日收益、净值最大回撤、规模从档案），再丢进池子分布求 z 和百分位；标 `in_universe=false` |
| 持仓为空 | `available=false` |

> **"剩余权重归一"是什么意思：** 比如某基金缺规模因子（权重 0.1），那就用动量/风险调整/回撤三个因子，把它们的权重 0.40/0.35/0.15（和为 0.9）重新归一成 0.444/0.389/0.167（和为 1），再合成。这样缺一个因子不会无故拉低综合分。

### 4.4 一个重要的诚实声明：池子是有偏的

`fetch_open_fund_rank` 默认取排行榜**靠前**的 ~300 只（偏强样本），不是全市场随机抽样。所以：

- z-score 和百分位的基准是「**排行榜可比池**」，不是「全市场」。
- 解读话术要写「在排行榜可比池（约 N 只）中超过 X%」，**不要**写成「打败全市场 X%」（会夸大）。
- 这是 MVP 的已知取舍。模块 3 做回测时会用更严谨的全市场分层抽样池，届时再升级。

---

## 5. 后端实现骨架：纯函数引擎

新建独立服务 `app/services/fund_factors.py`，**纯 Python 标准库**（`math` + `statistics`），不碰数据库/网络——和模块 1 的 `portfolio_risk_metrics.py` 一样是「纯函数」，极易单测。

```python
# app/services/fund_factors.py
"""基金横截面因子打分：动量、风险调整(Calmar)、回撤控制、规模。

数据来源：开放式基金排行榜横截面（fetch_open_fund_rank）+ 持仓净值（不在榜时）。
设计文档：docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md

设计要点：
- 只接收纯数据（横截面行 + 目标行），不碰 DB/网络，便于单元测试。
- 横截面统计：去极值 → z-score → 合成 → 百分位（见文档第 4 章）。
- 缺失因子按"剩余权重归一"合成，不把 None 当 0。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

MIN_UNIVERSE_SIZE = 30          # 横截面有效样本少于此，不打分
WINSOR_LOWER_PCT = 5.0          # 去极值下分位
WINSOR_UPPER_PCT = 95.0         # 去极值上分位
Z_CLIP = 3.0                    # z-score 裁剪边界

FACTOR_KEYS = ("momentum", "risk_adjusted", "drawdown", "size")
FACTOR_WEIGHTS = {
    "momentum": 0.40,
    "risk_adjusted": 0.35,
    "drawdown": 0.15,
    "size": 0.10,
}
FACTOR_LABELS = {
    "momentum": "动量",
    "risk_adjusted": "风险调整收益",
    "drawdown": "回撤控制",
    "size": "规模",
}


# ---------- 输入/输出数据结构 ----------

@dataclass
class FundFactorInput:
    """一只基金的因子原始输入（横截面行 / 目标行通用）。"""
    fund_code: str
    fund_name: str = ""
    return_3m_percent: float | None = None
    return_6m_percent: float | None = None
    return_1y_percent: float | None = None
    max_drawdown_1y_percent: float | None = None  # 负数，如 -22.0
    fund_scale_yi: float | None = None


@dataclass
class FactorDetail:
    raw: float | None
    z: float | None
    percentile: float | None       # 0-100
    hint: str | None = None


@dataclass
class FundFactorScore:
    fund_code: str
    fund_name: str
    in_universe: bool
    composite_score: float | None  # 0-100
    composite_grade: str | None    # A/B/C/D
    factors: dict[str, FactorDetail] = field(default_factory=dict)


@dataclass
class FactorScoreResult:
    available: bool
    universe_size: int
    message: str | None = None
    funds: list[FundFactorScore] = field(default_factory=list)


# ---------- 基础工具：原始值提取 ----------

def _blend_momentum(row: FundFactorInput) -> float | None:
    """多窗口动量加权；缺某窗口时按剩余窗口权重归一。"""
    parts = [
        (0.5, row.return_6m_percent),
        (0.3, row.return_3m_percent),
        (0.2, row.return_1y_percent),
    ]
    avail = [(w, v) for w, v in parts if v is not None]
    if not avail:
        return None
    total_w = sum(w for w, _ in avail)
    return sum(w * v for w, v in avail) / total_w


def _calmar(row: FundFactorInput) -> float | None:
    ret = row.return_1y_percent
    mdd = row.max_drawdown_1y_percent
    if ret is None or mdd is None:
        return None
    denom = abs(mdd)
    if denom < 1e-9:
        return None
    return ret / denom


def _size_raw(row: FundFactorInput) -> float | None:
    scale = row.fund_scale_yi
    if scale is None or scale <= 0:
        return None
    return math.log10(scale)


def _raw_factor(row: FundFactorInput, key: str) -> float | None:
    if key == "momentum":
        return _blend_momentum(row)
    if key == "risk_adjusted":
        return _calmar(row)
    if key == "drawdown":
        return row.max_drawdown_1y_percent
    if key == "size":
        return _size_raw(row)
    return None


# ---------- 横截面统计 ----------

def _percentile_value(sorted_vals: list[float], pct: float) -> float:
    """线性插值求分位值（pct 为 0-100）。sorted_vals 已升序、非空。"""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[int(rank)]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _winsorize(values: list[float]) -> list[float]:
    if len(values) < 2:
        return values
    sorted_vals = sorted(values)
    lo = _percentile_value(sorted_vals, WINSOR_LOWER_PCT)
    hi = _percentile_value(sorted_vals, WINSOR_UPPER_PCT)
    return [min(max(v, lo), hi) for v in values]


@dataclass
class _FactorStats:
    mean: float
    std: float


def _factor_stats(universe_raw: list[float]) -> _FactorStats | None:
    """对一列因子原始值去极值后求均值/标准差。"""
    clean = [v for v in universe_raw if v is not None]
    if len(clean) < 2:
        return None
    wins = _winsorize(clean)
    mean = statistics.mean(wins)
    std = statistics.stdev(wins)  # 样本标准差 n-1
    return _FactorStats(mean=mean, std=std)


def _zscore(raw: float | None, stats: _FactorStats | None) -> float | None:
    if raw is None or stats is None or stats.std < 1e-9:
        return 0.0 if (raw is not None and stats is not None) else None
    z = (raw - stats.mean) / stats.std
    return max(-Z_CLIP, min(Z_CLIP, z))


def _percentile_rank(value: float | None, population: list[float]) -> float | None:
    """value 在 population 中的百分位（≤ 计数法），0-100。"""
    if value is None or not population:
        return None
    count = sum(1 for v in population if v <= value)
    return round(count / len(population) * 100, 1)


def _composite_z(factor_z: dict[str, float | None]) -> float | None:
    """按剩余权重归一合成综合 z。"""
    avail = [(FACTOR_WEIGHTS[k], z) for k, z in factor_z.items() if z is not None]
    if not avail:
        return None
    total_w = sum(w for w, _ in avail)
    if total_w < 1e-9:
        return None
    return sum(w * z for w, z in avail) / total_w


def _grade(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile >= 75:
        return "A"
    if percentile >= 50:
        return "B"
    if percentile >= 25:
        return "C"
    return "D"


# ---------- 对外主函数 ----------

def compute_factor_scores(
    *,
    universe: list[FundFactorInput],
    targets: list[FundFactorInput],
    min_universe_size: int = MIN_UNIVERSE_SIZE,
) -> FactorScoreResult:
    """对 targets（你的持仓）在 universe（排行榜横截面）里做因子打分。

    universe 与 targets 可以重叠（持仓在榜时直接用榜单行也行）；
    本函数只做统计，不去重、不取数。
    """
    # 1. 每个因子的横截面统计（去极值后的 mean/std）
    stats_by_factor: dict[str, _FactorStats | None] = {}
    universe_raw_by_factor: dict[str, list[float]] = {}
    valid_universe = 0
    for key in FACTOR_KEYS:
        raws = [_raw_factor(row, key) for row in universe]
        universe_raw_by_factor[key] = [r for r in raws if r is not None]
        stats_by_factor[key] = _factor_stats(raws)

    # 有效样本以「至少有动量值」的池内基金数衡量
    valid_universe = len(universe_raw_by_factor["momentum"])
    if valid_universe < min_universe_size:
        return FactorScoreResult(
            available=False,
            universe_size=valid_universe,
            message=f"可比基金池不足 {min_universe_size} 只，暂无法计算因子评分。",
        )

    # 2. 先算出池中每只基金的"综合 z"，作为综合百分位的分布底座
    universe_composite_pop: list[float] = []
    for row in universe:
        fz = {k: _zscore(_raw_factor(row, k), stats_by_factor[k]) for k in FACTOR_KEYS}
        cz = _composite_z(fz)
        if cz is not None:
            universe_composite_pop.append(cz)

    # 各因子 z 的分布底座（用于单因子百分位）
    factor_z_pop: dict[str, list[float]] = {k: [] for k in FACTOR_KEYS}
    for row in universe:
        for k in FACTOR_KEYS:
            z = _zscore(_raw_factor(row, k), stats_by_factor[k])
            if z is not None:
                factor_z_pop[k].append(z)

    # 3. 给每个 target 打分
    funds: list[FundFactorScore] = []
    universe_codes = {row.fund_code for row in universe}
    for tgt in targets:
        factor_z: dict[str, float | None] = {}
        details: dict[str, FactorDetail] = {}
        for k in FACTOR_KEYS:
            raw = _raw_factor(tgt, k)
            z = _zscore(raw, stats_by_factor[k])
            factor_z[k] = z
            pct = _percentile_rank(z, factor_z_pop[k])
            details[k] = FactorDetail(raw=raw, z=z, percentile=pct, hint=None)
        cz = _composite_z(factor_z)
        comp_pct = _percentile_rank(cz, universe_composite_pop)
        funds.append(
            FundFactorScore(
                fund_code=tgt.fund_code,
                fund_name=tgt.fund_name,
                in_universe=tgt.fund_code in universe_codes,
                composite_score=comp_pct,
                composite_grade=_grade(comp_pct),
                factors=details,
            )
        )

    return FactorScoreResult(
        available=True,
        universe_size=valid_universe,
        funds=funds,
    )
```

> **教学注记：**
> - `compute_factor_scores` 只接收两组 `FundFactorInput`（池 + 目标），**不取数**——脏活（拉排行榜、拉持仓净值）由装配层做。
> - 解读话术 `hint` 留空由谁填？后端可填，也可前端填。本设计**前端填**（`lib/fundFactors.ts`），后端只给数字，前后端解耦、话术可独立改。
> - 函数刻意写碎（`_winsorize` / `_zscore` / `_percentile_rank` 分开），方便对照第 4 章逐个单测。

---

## 6. 装配层：从排行榜 + 持仓取数喂给纯函数

并入 `app/services/portfolio_snapshot.py`，与模块 1 的 `build_risk_metrics_payload` / `build_risk_correlation_payload` 并列（保持装配层集中、与现有结构一致）。

```python
def build_factor_scores_payload(
    holdings_models: list["Holding"],
    *,
    fetch_rank=None,       # 注入排行榜拉取，便于离线测试
    fetch_nav=None,        # 注入净值拉取（持仓不在榜时），便于离线测试
) -> dict:
    from dataclasses import asdict
    from app.services.fund_factors import (
        FundFactorInput, compute_factor_scores,
    )
    from app.services.akshare_subprocess import fetch_open_fund_rank
    from app.services.fund_data import FundDataService

    fetch_rank = fetch_rank or (lambda: fetch_open_fund_rank(limit=300))

    # 1. 排行榜横截面 → universe
    rank_rows = fetch_rank() or []
    universe = [
        FundFactorInput(
            fund_code=r["fund_code"],
            fund_name=r.get("fund_name", ""),
            return_3m_percent=r.get("return_3m_percent"),
            return_6m_percent=r.get("return_6m_percent"),
            return_1y_percent=r.get("return_1y_percent"),
            max_drawdown_1y_percent=r.get("max_drawdown_1y_percent"),
            fund_scale_yi=r.get("fund_scale_yi"),
        )
        for r in rank_rows
        if r.get("fund_code")
    ]
    rank_by_code = {row.fund_code: row for row in universe}

    # 2. 持仓 → targets（在榜直接用榜单行；不在榜用净值兜底算）
    targets: list[FundFactorInput] = []
    for h in holdings_models:
        code = (h.fund_code or "").strip()
        if not code or len(code) != 6:
            continue
        if code in rank_by_code:
            row = rank_by_code[code]
            targets.append(FundFactorInput(
                fund_code=code, fund_name=h.fund_name or row.fund_name,
                return_3m_percent=row.return_3m_percent,
                return_6m_percent=row.return_6m_percent,
                return_1y_percent=row.return_1y_percent,
                max_drawdown_1y_percent=row.max_drawdown_1y_percent,
                fund_scale_yi=row.fund_scale_yi,
            ))
        else:
            targets.append(_target_from_nav(h, fetch_nav))

    result = compute_factor_scores(universe=universe, targets=targets)
    return asdict(result)
```

`_target_from_nav(holding, fetch_nav)`：拉该基金近 ~250 交易日净值，用净值序列算近 3/6/12 月收益（按 ~60/120/250 个交易日切片复利）、净值最大回撤（复用模块 1 的 `_max_drawdown` 思路），规模从基金概况取。拉不到净值时返回一个全 None 的 `FundFactorInput`（该基金最终各因子为 None，综合分 None，前端显示「数据不足」）。

> **复用与一致性：** 净值最大回撤直接 `from app.services.portfolio_risk_metrics import _max_drawdown` 复用，保证和模块 1 口径一致——**不要重新实现一遍回撤**。

> **缓存：** 排行榜池可按 `trade_date` 加一层进程内缓存（盘中 / 收盘不同 TTL），避免每次展开都拉。第一期可先不加（`fetch_open_fund_rank` 子进程本身有，且这是懒加载接口），按需再补。

---

## 7. API 设计

第一期采用**独立懒加载接口**（和模块 1 的相关性矩阵 `risk-correlation` 同样的理由：拉排行榜较重，不该拖慢 dashboard 首屏）。

```
GET /api/portfolio/factor-scores
```

响应（`asdict(FactorScoreResult)`）：

```jsonc
{
  "available": true,
  "universe_size": 287,
  "message": null,
  "funds": [
    {
      "fund_code": "519674",
      "fund_name": "银河创新成长",
      "in_universe": true,
      "composite_score": 78.0,
      "composite_grade": "A",
      "factors": {
        "momentum":      { "raw": 18.4, "z": 1.21, "percentile": 85.0, "hint": null },
        "risk_adjusted": { "raw": 0.62, "z": 0.43, "percentile": 66.0, "hint": null },
        "drawdown":      { "raw": -22.1, "z": -0.35, "percentile": 41.0, "hint": null },
        "size":          { "raw": 1.76, "z": 0.10, "percentile": 54.0, "hint": null }
      }
    }
  ]
}
```

样本不足 / 无持仓时 `available=false` + `message`，前端显示友好空态。

### api.ts 类型

```typescript
// apps/web/src/lib/api.ts
export interface FactorDetail {
  raw: number | null;
  z: number | null;
  percentile: number | null;
  hint: string | null;
}
export interface FundFactorScore {
  fund_code: string;
  fund_name: string;
  in_universe: boolean;
  composite_score: number | null;
  composite_grade: "A" | "B" | "C" | "D" | null;
  factors: Record<"momentum" | "risk_adjusted" | "drawdown" | "size", FactorDetail>;
}
export interface FactorScoresResponse {
  available: boolean;
  universe_size: number;
  message?: string | null;
  funds: FundFactorScore[];
}
export function fetchPortfolioFactorScores(): Promise<FactorScoresResponse> { /* GET */ }
```

---

## 8. 前端展示 + Pro 付费点

### 8.1 新组件 `PortfolioFactorScoresPanel.tsx`

放在「盈亏分析」Tab（`PortfolioDashboard.tsx`），**风险体检面板下方**，懒加载（展开才请求，和相关性矩阵一致）。

```tsx
// 结构示意（非完整代码）
<section className="section-card">
  <h3 className="section-eyebrow">持仓因子体检</h3>
  {!data.available ? (
    <div className="empty-state">{data.message ?? "数据积累中…"}</div>
  ) : (
    data.funds.map((f) => (
      <div className="factor-fund-card" key={f.fund_code}>
        <div className="factor-grade">
          <span className="grade-badge">{f.composite_grade}</span>
          <span className="grade-score">{f.composite_score}</span>
          <span className="fund-name">{f.fund_name}</span>
        </div>
        <FactorBar label="动量" detail={f.factors.momentum} />          {/* 免费可见 */}
        {isPro ? (
          <>
            <FactorBar label="风险调整收益" detail={f.factors.risk_adjusted} />
            <FactorBar label="回撤控制" detail={f.factors.drawdown} />
            <FactorBar label="规模" detail={f.factors.size} />
          </>
        ) : (
          <ProLock hint="升级解锁全部因子拆解" />
        )}
      </div>
    ))
  )}
</section>
```

### 8.2 解读 helper `lib/fundFactors.ts`（配 vitest）

把第 3 章每个因子的「解读话术」和等级/颜色写成纯函数：

```typescript
export function gradeTone(grade: string | null): "good" | "neutral" | "danger" { ... }
export function momentumHint(pct: number | null): string { ... }
export function riskAdjustedHint(pct: number | null): string { ... }
export function drawdownHint(pct: number | null): string { ... }
export function sizeHint(rawScaleYi: number | null): string { ... }
export function compositeSummary(f: FundFactorScore): string { ... }  // 综合一句话
```

### 8.3 接入「好基灵 Pro」

延续模块 1 的门控约定（私有部署 ≤5 人，仅前端门控，后端照常返回全量）：

- **免费版：** 综合评分 + 等级 + **动量**因子（让用户尝到甜头）。
- **Pro 版：** 解锁风险调整 / 回撤控制 / 规模 + 全部解读话术。
- 复用模块 1 的 `isPro`（localStorage 开关）和 `.plan-card.is-pro` 蒙层样式。

---

## 9. 测试设计

`compute_factor_scores` 及其工具都是纯函数 → 单元测试极好写。新建 `apps/api/tests/test_fund_factors.py`。

### 9.1 已知答案用例

```python
from app.services.fund_factors import (
    _winsorize, _zscore, _factor_stats, _percentile_rank,
    _blend_momentum, _calmar, _composite_z,
    FundFactorInput, compute_factor_scores, FACTOR_WEIGHTS,
)

def test_zscore_basic():
    stats = _factor_stats([0, 10, 20])      # 去极值后均值10
    assert round(_zscore(20, stats), 2) == round((20 - 10) / 10.0, 2)

def test_winsorize_caps_outlier():
    vals = [1, 2, 3, 4, 1000]
    out = _winsorize(vals)
    assert max(out) < 1000                  # 极端值被压回

def test_percentile_rank_top():
    assert _percentile_rank(9.0, [1, 2, 3, 9]) == 100.0

def test_blend_momentum_handles_missing_window():
    row = FundFactorInput("x", return_6m_percent=10, return_3m_percent=None, return_1y_percent=None)
    assert _blend_momentum(row) == 10       # 仅 6 月时归一回该值

def test_calmar_uses_abs_drawdown():
    row = FundFactorInput("x", return_1y_percent=20, max_drawdown_1y_percent=-10)
    assert _calmar(row) == 2.0

def test_composite_renormalizes_missing_factor():
    # 缺规模因子时，剩余权重归一
    z = {"momentum": 1.0, "risk_adjusted": 1.0, "drawdown": 1.0, "size": None}
    assert _composite_z(z) is not None
```

### 9.2 测试要点清单

- ✅ **z-score 基础**：手算已知均值/标准差。
- ✅ **去极值**：极端值被压回分位线。
- ✅ **百分位**：最高值 = 100，最低值的边界。
- ✅ **动量缺窗口**：仅有部分窗口时按剩余权重归一。
- ✅ **Calmar 取绝对值**：回撤为负不会算出负 Calmar。
- ✅ **合成缺因子归一**：缺一个因子不把它当 0。
- ✅ **池子不足**：universe < 30 → `available=false`。
- ✅ **零方差因子**：某因子全池相同 → z 退化为 0，不崩。
- ✅ **持仓不在池**：`in_universe=false`，用 target 自身原始值求 z。
- ✅ **空持仓 / 全 None target**：综合分 None，不崩。

### 9.3 hypothesis 不变量（项目已装）

对随机横截面断言：
- 任意 z-score ∈ `[-3, 3]`（裁剪生效）。
- 任意 percentile ∈ `[0, 100]`。
- universe ≥ 30 且 target 字段齐全时 `composite_score` 必为 `[0,100]` 或 None。

### 9.4 装配层测试（离线）

注入 `fetch_rank`（返回构造的 ~40 行假排行榜）和 `fetch_nav`（返回构造净值），断言 `build_factor_scores_payload` 在**无网络**下产出正确结构。和模块 1 `build_risk_correlation_payload` 的注入式测试一致。

### 9.5 前端测试

`apps/web` vitest 覆盖 `lib/fundFactors.ts` 的等级/解读/颜色 helper（边界：百分位 None、0、100）。

---

## 10. 实施清单与验收标准

### 10.1 任务分解

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 纯函数引擎 `fund_factors.py`（去极值→z-score→合成→百分位） | 新建 | — |
| 2 | 单元测试（已知答案 + hypothesis 不变量） | `tests/test_fund_factors.py` | 1 |
| 3 | 装配层 `build_factor_scores_payload`（排行榜池 + 持仓净值兜底，复用 `_max_drawdown`） | `portfolio_snapshot.py` | 1 |
| 4 | 装配层离线测试（注入 `fetch_rank` / `fetch_nav`） | `tests/` | 3 |
| 5 | API `GET /api/portfolio/factor-scores` | `main.py` | 3 |
| 6 | `api.ts` 类型 + `fetchPortfolioFactorScores` | `apps/web` | 5 |
| 7 | `PortfolioFactorScoresPanel.tsx` + `lib/fundFactors.ts`（含 vitest） | `apps/web` | 6 |
| 8 | Pro 门控（免费综合分+等级+动量，Pro 全解锁） | `PortfolioFactorScoresPanel.tsx` | 7 |
| 9 | 更新 `PROJECT_CONTEXT.md`（能力清单 + API + 目录） | `docs/PROJECT_CONTEXT.md` | 全部 |

### 10.2 验收标准

- [ ] 排行榜池 ≥ 30 只时，`GET /api/portfolio/factor-scores` 返回每只持仓的综合分 + 等级 + 4 因子百分位；不足时 `available=false` 且有友好文案。
- [ ] 所有因子单测通过（含 z-score / 去极值 / 百分位 / 合成归一的已知答案用例 + hypothesis 不变量）。
- [ ] `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q` 全绿。
- [ ] `cd apps/web && npm run lint && npm run typecheck && npm run build` 通过。
- [ ] 「盈亏分析」Tab 能看到「持仓因子体检」卡片；免费/Pro 门控生效；离线（无网络）时优雅降级为 `available=false`，不报错。
- [ ] `PROJECT_CONTEXT.md` 已同步。

### 10.3 第二期（接入选基，本文不实现）

第一期建好的 `fund_factors.py` 纯函数引擎，第二期直接复用到「推荐基金」：把候选池作为 universe + targets，用因子综合分替代/增强现在的 `balanced_score`，让选基逻辑透明化。届时 universe 足够大，z-score / 百分位统计意义更强，也为模块 3 的 IC 检验铺好横截面底座。

---

## 11. 与路线图的关系

| 模块 | 内容 | 状态 |
|------|------|------|
| 0 | 收益与风险的语言 | 模块 1 文档第 2 章已覆盖 |
| 1 | 组合风险度量（夏普/回撤/Beta/相关性/HHI） | ✅ 已完成 |
| **2** | **因子思维（横截面 z-score / 百分位 / 多因子打分）** | **本文（第一期：持仓因子体检）** |
| 3 | 像样的回测（前视偏差、walk-forward、IC 信息系数、价值/质量因子、修 Bug B） | 待出文档 |
| 4 | 信号合成 + AI 闭环（量化结论喂 LLM） | 待出文档 |

**本期在路线图里的位置：** 模块 1 教会你「竖着看一只基金的历史」，模块 2 教会你「横着比一群基金」。等模块 3 的回测框架就位，就能用 IC 检验「这些因子到底有没有用」，再把价值/质量因子补齐——那时模块 2 的打分才从「描述性」升级为「有预测力的」。

---

*文档结束。实现时如对横截面统计或某个因子有疑问，回到第 2、4 章对照概念，或让我针对某一步展开讲 + 写完整实现。*
