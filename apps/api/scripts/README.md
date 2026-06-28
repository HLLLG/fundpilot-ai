# 离线分析工具（模块3）

模块3 的几个量化分析工具，**离线运行、无 API/前端**，结果落盘到 `apps/api/var/`（已 gitignore）：
`report.txt`（人读报告）+ `summary.json`（机读，供模块4 喂 LLM）。

> 均在 `apps/api/` 目录下运行。下文用 `python` 代指本机解释器（或 `./.venv/Scripts/python.exe`）。
> 联网拉数（AkShare 子进程），首跑较慢；纯函数引擎与 runner 的逻辑均已被离线单测覆盖。

---

## 3A 因子有效性回测（Rank IC）

回测模块2 的因子（动量/风险调整/回撤/综合）到底有没有预测力。

```bash
python scripts/run_factor_ic.py --universe-size 300 --nav-days 750
```

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--universe-size` | 300 | 基金池只数 |
| `--nav-days` | 750 | 每只基金拉多少交易日 NAV |
| `--universe-mode` | `top` | `top`=榜单前N(偏强样本)；`sampled`=大池跨业绩段分层抽样（见 3D） |
| `--sample-pool-size` | 500 | `sampled` 模式下的大池容量（上限 500） |
| `--limit-funds` | 无 | 调试用，限制只数 |
| `--out-dir` | `var/factor_ic` | 输出目录 |

读结果：`mean IC` 0.03~0.05 即属可用，`n≥12 且 |t|>2` 才算显著；过高通常是前视偏差。
**注意**：池为「当前在榜、业绩偏强」样本，有幸存者/选择偏差，IC 偏乐观。

---

## 3C 价值/成长风格暴露（收益型风格分析）

把基金日收益对价值/成长指数回归，得出每只基金偏价值还是偏成长。

```bash
python scripts/run_style_factor.py --universe-size 200 --nav-days 250
```

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--universe-size` | 200 | 基金池只数 |
| `--nav-days` | 250 | NAV / 指数取多少交易日 |
| `--value-index` | `399371` | 价值指数（默认国证价值） |
| `--growth-index` | `399370` | 成长指数（默认国证成长） |
| `--out-dir` | `var/style_factor` | 输出目录 |

读结果：`style_tilt = beta_value − beta_growth`，>0.15 偏价值、<−0.15 偏成长，否则中性；
`r_squared` 越高说明风格解释力越强。
**注意**：这是「风格暴露」（基金长得像价值/成长），**不是**基本面便宜/质量。

---

## 3D 分层抽样基金池

不是独立脚本，而是 3A 的一个开关：把池从「取前 N 名」换成「跨业绩段分层抽样」以降偏差。

```bash
python scripts/run_factor_ic.py --universe-mode sampled --sample-pool-size 500 --universe-size 100
```

即先取榜单前 500 作大池，再等距抽样出 100 只（横跨赢家→输家）。
**注意**：榜单子进程上限 500 条且清盘基金不在榜，只削弱选择偏差，幸存者偏差仍在。

---

## 关联板块 · 中基协指数库 · 全市场预计算

维护业绩比较基准要素库与全市场基金→板块映射（无 API，部署/运维时运行）：

```bash
# 从中基协 API 同步 155 指数要素库 → app/data/amac_benchmark_index_library.json
python scripts/sync_amac_benchmark_index_library.py

# 批量预计算全市场基金关联板块 → fund_primary_sectors_global
python scripts/precompute_fund_primary_sectors.py --limit 200 --mode benchmark
python scripts/precompute_fund_primary_sectors.py --mode auto --limit 150
```

环境变量（见 `.env.example`）：`FUND_AI_FUND_PRIMARY_SECTOR_GLOBAL_ENABLED`、`FUND_AI_FUND_PRIMARY_SECTOR_PRECOMPUTE_*`、TTL 天数等。
