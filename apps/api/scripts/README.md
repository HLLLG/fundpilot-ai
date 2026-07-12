# 离线分析工具（模块3）

模块3 的几个量化分析工具，**离线运行、无 API/前端**，结果落盘到 `apps/api/var/`（已 gitignore）：
`report.txt`（人读报告）+ `summary.json`（机读，供模块4 喂 LLM）。

> 均在 `apps/api/` 目录下运行。下文用 `python` 代指本机解释器（或 `./.venv/Scripts/python.exe`）。
> 联网拉数（AkShare 子进程），首跑较慢；纯函数引擎与 runner 的逻辑均已被离线单测覆盖。

---

## 3A 因子有效性回测（Rank IC）

回测模块2 的因子（动量/风险调整/回撤/综合）到底有没有预测力。

```bash
python scripts/run_factor_ic.py \
  --universe-mode stratified --sample-pool-size 25000 --universe-size 1500 \
  --nav-days 1500 --rebalance-step 10 --forward-horizons 5,20,60 --max-workers 16
```

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--universe-size` | 300 | 目标研究池只数；生产固定 1500 |
| `--nav-days` | 750 | 每只基金拉多少净值观测；生产固定 1500 |
| `--universe-mode` | `top` | `stratified`=全目录、份额去重、分类分层；生产使用此模式 |
| `--sample-pool-size` | 500 | 元数据大池；`stratified` 生产固定 25000，覆盖当前全目录并留增长空间 |
| `--forward-horizons` | `5,20,60` | 分类 IC 前瞻周期 |
| `--limit-funds` | 无 | 调试用，限制只数 |
| `--out-dir` | `var/factor_ic` | 输出目录 |

生产 v2 按同类基金读取 `mean_ic`、HAC 区间、`oos_mean_ic` 与
`direction_stable`；不能只看普通 t 值。完整口径见
[`docs/design/FACTOR_IC_V2.md`](../../../docs/design/FACTOR_IC_V2.md)。

### 发布已校验快照

`run_factor_ic.py` 始终只生成本地文件，不会写入生产数据库。生产发布通常由
GitHub Actions 执行；确需本地显式发布时，在 `apps/api/` 下配置环境变量后运行：

```bash
export FACTOR_IC_PUBLISH_URL="https://<api-domain>/api/internal/factor-ic-snapshots"
export FACTOR_IC_PUBLISH_TOKEN="<publication-only-token>"
export GITHUB_SHA="<40-character-commit-sha>"
export GITHUB_RUN_ID="<traceable-run-id>"
python scripts/publish_factor_ic.py var/factor_ic/summary.json
```

Token 只通过环境变量进入请求头，不得作为命令行参数、日志或 Actions Summary 内容。
发布器会在本地先执行版本化固定参数与覆盖质量门槛；v2 要求有效总收益序列至少
1200、总收益优先覆盖率至少 80%、分类研究模型至少覆盖四类。v2 快照体积较大，
单次发布超时为 90 秒；网络错误或
服务端 `5xx` 最多按 5、15、45 秒退避重试，`409` 视为已有更新快照并安全跳过。

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

## 3D 分层抽样基金池（旧 v1 调试口径）

不是独立脚本，而是 3A 的一个开关：把池从「取前 N 名」换成「跨业绩段分层抽样」以降偏差。

```bash
python scripts/run_factor_ic.py --universe-mode sampled --sample-pool-size 500 --universe-size 100
```

即先取榜单前 500 作大池，再等距抽样出 100 只（横跨赢家→输家）。
生产不再使用此口径；v2 使用 `stratified` 拉全目录、份额去重并按类别分层。旧入口仅为
回归和小样本调试保留。

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
