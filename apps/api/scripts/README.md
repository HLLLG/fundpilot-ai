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
[`docs/PROJECT_CONTEXT.md`](../../../docs/PROJECT_CONTEXT.md#5-factor-icpit-与量化证据)。

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

维护业绩比较基准要素库与全市场基金→板块映射。基金名称、新发标签与 LLM
只负责召回，只有精确跟踪指数或合格 PIT 持仓证据可以写入 `verified` 身份：

```bash
# 从中基协 API 同步 155 指数要素库 → app/data/amac_benchmark_index_library.json
python scripts/sync_amac_benchmark_index_library.py

# 连续完成全市场首轮档案解析；每只基金都会落为
# verified / queued / research_only / unmapped / unavailable 之一，可中断后续跑
python scripts/precompute_fund_primary_sectors.py --mode benchmark --limit 800 --until-covered

# 运维时也可只跑一个到期增量批次
python scripts/precompute_fund_primary_sectors.py --mode benchmark --limit 800

# 对首轮上游未返回资料的代码立即限次重试一轮
python scripts/precompute_fund_primary_sectors.py --mode benchmark --limit 800 --retry-status unavailable

# 指数目录规则修订后只重算受影响的原因码
python scripts/precompute_fund_primary_sectors.py --mode benchmark --reclassify-reason tracking_index_sector_catalog_pending,broad_or_non_sector_tracking_index

# 对少量代码补做严格持仓穿透（不会覆盖已有的新鲜 verified 身份）
python scripts/precompute_fund_primary_sectors.py --mode auto --limit 80

# 持续处理已排队的严格持仓核验；默认后台为 32 只/批，并发受 AkShare worker 池上限约束
python scripts/precompute_fund_primary_sectors.py --mode holdings --limit 32
```

### 中基协指数库离线重算

指数库需要中基协 API + 东财指数名表两个上游。规则改动后要确定性复算、或上游不可达时，
可以用仓库里已缓存的东财 `code → name` 表和现有库文件重建，不联网：

```bash
python scripts/sync_amac_benchmark_index_library.py \
  --em-lookup-cache var/amac/em_index_lookup.json \
  --amac-cache app/data/amac_benchmark_index_library.json
```

同名指数（东财对深证 `399262` 与中证 `931582` 都显示简称"数字经济"）按发布机构的原生
命名空间收敛，仍不唯一就记为 `unresolved`；模糊匹配只接受「东财简称是 AMAC 全称的前缀」
单方向，避免父子/跨市场指数族混作同一代码。

`_MANUAL_INDEX_CODES` 里手写的 `(代码, 东财简称)` 会与东财名表**双向对账**：简称与实况
不符即判 `manual_conflict` 并整条丢弃（不符往往说明代码本身就抄错了）。确知查不到的
条目登记为 `(None, None)` → `manual_unresolvable`，挡住自动匹配去抓一个错码。
2026-08-07 首次启用这道校验时查出 22 条：14 条代码指向完全无关的标的（`931248` 记着
"新基建"、实为"油气资源"；`931787` 记着"港股通医药"、实为"港股创新药"），6 条只是东财
改了简称，1 条是抄错（港股通医药卫生综合的正确代码是 `930965`）。

登记 `(None, None)` 前必须确认下游会不会**退化**而不是 fail-closed：`parse_benchmark_index`
拿不到库里代码时会退回按名字做子串匹配。`中证智能电动汽车指数` 因此从"新能源车"掉到更粗的
`931008 汽车`，两只 ETF 联接受影响。只在"下游确实 fail-closed"或"没有基金跟踪"时才登记查不到。

### 指数身份对账（与东财实时数据核对）

板块名 → 行情标的写错了不会报错，只会让页面上的涨跌幅静默变成另一只指数的涨跌幅。
离线单测只能锁住"表内自洽"，锁不住"表与市场一致"，所以这层必须联网对账：

```bash
python scripts/reconcile_em_index_lookup.py                  # 三层全量对账
python scripts/reconcile_em_index_lookup.py --check registry
python scripts/reconcile_em_index_lookup.py --refresh-cache  # 刷新离线重算的输入
python scripts/reconcile_em_index_lookup.py --write-baseline  # 人工复核后固化基线
python scripts/reconcile_em_index_lookup.py --json var/reconcile.json
```

三层的严格程度不同，刻意不一致：

| 层 | 对象 | 判定 |
| --- | --- | --- |
| `cache` | `var/amac/em_index_lookup.json` ↔ 实时指数全集 | 只告警。这张表只在离线重建时读，运行时不参与取数 |
| `library` | 库里每条记的 `eastmoney_name` ↔ 按 secid 取回的实时名称 | 不一致即失败 |
| `registry` | `THEME_BOARD_INDEX` 各 label 的实时名称 ↔ `app/data/sector_quote_identity_baseline.json` | 变动即失败，必须人工看过再 `--write-baseline` |

退出码 0 = 无失败项，1 = 有失败项（可直接当定时任务/CI 门槛），2 = 取数失败。

`library` 层长期会有一批 `no-quote`：中证 800 系列、港股通工业/资源/TMT、ESG 基准等
东财根本不挂行情，这些条目 `theme_label` 恒为 `None`，不影响任何板块，属于预期告警。

`--refresh-cache` 会原地重写缓存，而 `var/` 不在版本控制里——刷新前先自己备份一份，
否则离线重算的输入就回不去了。

### 失效已站不住的存量板块映射

解析规则修订后，存量派生行的 TTL 未到不会自动重算。该脚本按 (板块, 跟踪码) 重放每行存的
基准原文、或按新的行业→板块归并规则重放持仓 exposure，与存量比对后**只删派生缓存**让后台
用新规则重算，不手改标签；`manual` / `ocr_detail` 沉淀一律跳过，追加式 PIT 证据
`fund_sector_exposure_snapshots` 不删。默认 dry-run，`--apply` 才写且自动备份数据库。

```bash
# 只看报告（默认 dry-run，只查业绩基准链路）
python scripts/invalidate_stale_benchmark_sectors.py

# 基准链路实际执行
python scripts/invalidate_stale_benchmark_sectors.py --apply

# 持仓链路：只处理决策级 verified 行
# （pending 是研究线索、不参与展示，重算要为每只股票联网取行业分类，交给 TTL 自然刷新）
python scripts/invalidate_stale_benchmark_sectors.py --chain holdings --verified-only --apply

# 两条链路一起
python scripts/invalidate_stale_benchmark_sectors.py --chain all --verified-only --apply
```

持仓链路的重放走**股票级**证据（`detail.evidence[*].industry` → 生产函数
`assess_sector_from_portfolio_stocks`），不是把已归并的板块名再折叠一遍。后者看着等价其实
会漏判：`sector_name` 已经是归并结果，一旦归并规则在股票那一层改了（如 `军工电子Ⅱ` 从
"电子"改到"军工"），从"电子"这个名字再也还原不回去，该基金会被判成"主板块不变"而逃过失效。
报告里的「重放路径分布」就是用来确认这一点的：`stock_industry` 才可信，
`sector_name_refold` 说明该行证据缺失、只能退化重放。

失效方式是**删除** `fund_sector_resolution_status` 行让基金变成 `missing`——
`_bulk_resolution_candidates` 把 `queued` 排除在候选之外，所以不能用 `queued` 触发重算，
而 `missing` 在候选队列里优先级最高。

失效后补算大批基金时，**用 `--codes` 分片喂**，不要指望通用队列：被失效的基金证据都还在、
行业分类缓存（`stock-classification:industry:v1:`，TTL 30 天）是热的，`--mode holdings
--limit 250 --codes <250个代码>` 约 1.05 秒/只；而不带 `--codes` 的队列会把没见过的基金
也捞进来联网抓持仓快照，200 只能跑掉 25 分钟以上。注意 `--mode holdings` 不给 `--limit`
时默认每批只处理 32 只。计数里的 `miss` 不是失败，是"没产出决策级身份"——分散型基金过不了
60% 主导度门槛属正常，实测比例约 6~8%。

schema v22 的 `fund_sector_resolution_status` 保存每只基金的解析状态、失败原因与
下次重试时间；旧 `pending` 会在后台启动时迁移为明确状态。`queued` 表示等待持仓核验，
`research_only` 表示线索不具备执行资格，两者都不能进入金额分配；`fund_sector_current`
只保存可供决策使用的已核验投影。后台在首次档案覆盖完成前按 800 只连续跑批（内部
80 只一组、有限并发），完成后每 6 小时仅处理到期记录；持仓队列按 32 只/批持续排空，
配置并发仍受 AkShare worker 池容量约束。环境变量见 `.env.example`：
`FUND_AI_FUND_PRIMARY_SECTOR_GLOBAL_ENABLED`、`FUND_AI_FUND_PRIMARY_SECTOR_PRECOMPUTE_*`、
TTL 天数等。
