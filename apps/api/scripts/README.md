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

---

## 板块身份运维（持仓 / 全市场 / 细分预筛）

容器路径是 `/app/scripts/...`。生产用根目录 Dockerfile，漏拷贝会直接 `No such file`。

```bash
# 细分规则（CPO/CXO/算力租赁/PCB）预筛；run 会清 pending PCB 当前行并重算命中基金
python scripts/rescan_cpo_cxo_targets.py screen
python scripts/rescan_cpo_cxo_targets.py run

# 只重算当前用户持仓（主动走季报穿透，被动只复核合同指数）
python scripts/rerun_holdings_primary_sectors.py inspect
python scripts/rerun_holdings_primary_sectors.py run
python scripts/verify_holdings_primary_sectors.py

# 全市场强制重算。默认只强制主动持仓穿透，不要加全表 reclassify
python scripts/rerun_all_primary_sectors.py inspect
python scripts/rerun_all_primary_sectors.py run --workers 2
```

PCB 预筛与身份规则对齐：不把 BK0877 成分当身份，必须命中核心龙头且合计净值 ≥15%。
`rerun_all_primary_sectors.py` 的 `--mode holdings --force` 官方 CLI 会跳过已 verified
（医疗升不成 CXO）；本脚本才覆盖同优先级 holdings。**不要**默认跑
`reclassify_stored_profile_resolutions()`。

---

## 加仓梯形对比（仓位路径模拟）

`sector_direction_backtest` 评的是**信号质量**：每个观测都是「D+1 收盘买 1 单位、持有 h 天」，
因此它对「同一个信号下，资金该怎么分批投进去」完全不敏感——所有梯形在它的标尺上得分一模一样。
而 `_resolve_deterministic_position_change` 的四档（20/15/10/5，分母是**当前持仓市值**）与任何
金字塔式建仓法的差别恰恰只在资金路径上。本脚本补的就是这一层。

```bash
# 单组参数（零网络，价格与资金同源，读项目自己的 sector_spot_cache）
python scripts/run_position_sizing_backtest.py --sqlite-cache ../../data/app.db

# 参数敏感性：(最长持有期 x 止损幅度) 3x3 网格，看排序稳不稳
python scripts/run_position_sizing_backtest.py --sqlite-cache ../../data/app.db --sweep
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--sqlite-cache` | 必填 | 从 `sector_spot_cache` 的 `board-flow-hist:v2` 缓存离线取数 |
| `--max-days` | 40 | 单个 episode 最长持有交易日数 |
| `--stop-percent` | 10 | 从最高收盘起算的移动止损幅度 |
| `--no-trend-exit` | 关 | 只用移动止损，不叠加生产的趋势退出线 |
| `--base-fraction` | 0.20 | 现状类梯形的首仓占预算比例（取自 discovery 首仓上限） |
| `--sweep` | 关 | 在参数网格上重跑并附敏感性表 |
| `--sweep-tier-thresholds` | 关 | 固定「现状 + 浮亏封档」梯形，只扫加仓档位**分界线**（生产等分 vs 换锚/平移/不分档），逐变体与生产边界配对检验 |
| `--sweep-add-throttle` | 关 | 固定生产梯形，只叠加**加仓节流**候选：距上笔买入间隔 ≥{3,5,7} 自然日、或较上笔买入价涨 ≥{3,5}% 才可再加，逐变体与无节流配对检验 |
| `--out-dir` | `var/position_sizing` | 输出目录 |

档位百分比集合（20/15/10/5）是产品策略、sweep 不动它；扫的是 `_v3_add_tier_thresholds`
那条"等分 gate→85"分界线本身——它此前没有任何回测依据。加仓节流同理：方向持续 ready 时
线上每天都可能给同一只基金加仓、没有任何间隔抑制，节流是否值得上线由这份 sweep 先回答，
**当前没有任何线上节流规则**。

入场信号由 `replay_sector_direction` 逐日 PIT 重放**生产打分器**，档位调生产
`_resolve_sector_add_tier`、系数用 `V3_TREND_TRANCHE_SCALES`、退出线用 `EXIT_TREND_THRESHOLD`；
脚本只在上面加资金路径（T+1 成交、申购费逐笔、赎回费按先进先出且不足 7 天收惩罚性费率、
止损收盘触发次日执行）。任何一处改成副本，结论就不再描述线上行为。

读结果时必须一起读三件事：

* **配对检验才是结论**，不是两个均值。报告底部按「同一批 episode 逐个相减」给出均值差、中位差、
  占优比例与 t 值——梯形之间的差异普遍在 0.3 个百分点以内，只比均值会把噪声读成优劣。
* **样本期基调**会打印在表头（基准累计涨跌、最大回撤、全板块无条件持有 h 日的收益分布）。
  「加仓该不该更大」在涨市与跌市里答案本来不同，下行区间对「少加仓」类结论天然友好。
* 标的是**板块指数**，不是可买到的基金；过热缩放、基金证据降档、载体质量降档、集中度与新闻
  门禁均未建模。完整缺口见报告末尾的 caveat 列表与 `summary.json` 的 `caveats`。

`shadow_record_only`：研究输出，不自动改动任何线上权重、阈值、Prompt、Guard 或仓位。当前唯一
据它落地的线上规则是「该仓未转正时加仓档位封到最低档」，口径与样本限制见
[`docs/PROJECT_CONTEXT.md`](../../../docs/PROJECT_CONTEXT.md#2-决策事实仓位与-dataevidence)。

---

## 方向退出参数回测入口

`sector_direction_exit` 的两个新设参数（`PERSISTENT_BREAKDOWN_DAYS=3`、
`RELATIVE_TREND_DECAY_POINTS=12`）契约标注**未经回测**（`thresholds_validated=false`）。
本脚本是给它们补回测的入口：同一批 PIT 重放入场信号上，模拟生产的**分档减仓路径**
（跌破首日 −25%（浮盈 −1/3）→ 连续 ≥N 日 −50% → invalid+持续则清仓，按状态升级每档执行一次），
只换 (连续天数 × 回落分数) 参数，另设「不减仓·持有到期」与「首破即全退」两组对照。

```bash
python scripts/run_direction_exit_backtest.py --sqlite-cache ../../data/app.db
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--sqlite-cache` | 必填 | 与加仓梯形脚本同一数据源 |
| `--max-days` | 40 | 单个 episode 最长持有交易日数 |
| `--min-episodes` | 30 | **数据充分性门槛**：episode 少于它不产出任何结论 |
| `--min-decision-days` | 60 | 同上：重放决策日门槛 |
| `--out-dir` | `var/direction_exit_backtest` | 输出目录 |

样本不足时输出 `status=insufficient_data` 并以退出码 2 结束——这不是失败，是"数据还
不够、不配下结论"。移动止损刻意未启用（单独度量方向退出档位的贡献）；重放信号是生产
打分器的 PIT 重算，与线上逐日账本可能有差异，`sector_direction_states` 账本攒够真实
历史后应以账本重跑、以账本为准。`shadow_record_only`：占优取值也只取得人工评审资格。

---

## 板块方向状态每日捕获 / 历史回填

退出侧（`sector_direction_exit`）要回答「趋势连续跌破退出线几天了」，而
`sector_direction_states` 原来**只在用户手动跑一次发现基金时才写**。实测线下库里整张表只有
一天数据，于是「连续 N 个交易日才升级为大幅减仓」永远攒不出序列，
`consecutive_days_below_exit_line` 长期卡在 1，−50% 那一档实际不可达。

```bash
# 每交易日捕获（生产由 .github/workflows/sector-direction-capture.yml 定时调用）
python scripts/capture_sector_direction_states.py
python scripts/capture_sector_direction_states.py --trade-date 2026-08-10
python scripts/capture_sector_direction_states.py --json var/sector_direction_capture.json

# 回填最近 6 个历史交易日的趋势轴（本地补数用）
python scripts/capture_sector_direction_states.py --backfill-days 6
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--trade-date` | 按交易时段推导 | 目标交易日；回填模式下作为回溯起点 |
| `--backfill-days` | 0 | 大于 0 即切换为回填模式，重算最近 N 个历史交易日 |
| `--with-divergence` | 关 | 同时跑量价背离回测，默认关（见下） |
| `--json` | 无 | 把摘要写到该路径（机读） |

退出码 0 = 成功且**确实产出了趋势证据**；1 = 失败，或落库了但趋势证据为 0。后者要单独判
是因为「行数满」不等于「有用」：证据不足时趋势分会被写成 ≤45 的占位值，行数照样是 78，而
退出侧一行都用不上。

**口径要点**（完整论证见 `app/services/sector_direction_capture.py` 的模块 docstring）：

* **前台集合是全白名单（约 78 个板块）**，与发现基金那约 24 个预筛板块刻意不同。这张表没有
  `userId`，一次捕获服务所有用户，而「谁持有哪个板块」是逐用户的——不在前台集合里的板块拿不到
  mainline，趋势分会退化成 ≤45 的占位值。
* **打分、滞回与落库复用 `discovery_pipeline._score_select_and_persist_directions`**。另写一份
  会让同一板块同一天出现两个 `trend_strength_score`，而退出侧要把「今天实算的分」与「历史落库
  的分」放在一条序列上比较。
* **默认跳过量价背离回测**：它只流向 `confidence`，而 `confidence` 不在落库列里、也不是
  `entry_state` 的输入。实测它占全流程 103.5 s 里的 90 s（跑满预算仍整段超时），关掉后
  6.2 s（热缓存）/ 约 13.5 s（冷跑），落库结果逐项相同。
* **回填只重算趋势轴**。趋势轴的输入全是日线纯函数（20 日收益、距 MA20/MA60、20 日上涨天数
  占比、相对强度横截面分位），实测历史日期 6/6 命中、覆盖度 1.0、数值逐日真实变化。但历史资金流
  拿不回来，所以回填行的 `participation_score` 只是中性填充、`entry_state` 由它派生因而**不可
  当作历史入场判断**；这些行标 `source='backfilled'`，发现基金的滞回读取会过滤掉它们，只有退出
  侧的趋势历史才收。
* 回填**不会覆盖已有可用趋势证据的行**（判据是 `trend_evidence_coverage > 0`，不是「这天有没有
  行」）。迁移之前写入的行该列为 NULL 且趋势分可能正是占位值，这种行必须允许被替换——否则因为
  读取侧遇无证据日会停止回溯，它们会像路障一样把更早的回填整段挡住（实测踩过：回填了 5 天
  390 行，历史序列仍读成空）。
* 回填仍有 point-in-time 偏差：横截面分位的分母用的是**今天的**白名单集合。几天可以忽略，长历史
  会有幸存者偏差——它是补数手段，**不是回测数据源**。

诚实前提：本仓库沙箱到东财 `push2his` 的出站被阻断（与 `run_sector_direction_backtest.py` 同一
处划界）。受限环境下捕获会以趋势证据 0 收场并返回非零退出码，而不是假装成功。
