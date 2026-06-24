# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 新对话或接手开发时先读本文，再按需打开具体文件。避免从零扫描仓库。
>
> **维护：** 功能或架构有实质变化时，同步更新「能力清单」「数据流」「API」「目录」「环境变量」。

**文档版本：** 2026-06-24（模块4 信号可信度+因子IC置信喂LLM · 模块3 因子IC回测/风格回归/分层抽样/信号基线修正 · 因子体检 · 组合风险度量）

**更新记录：**
- **因子分 + IC 置信 → LLM（模块4 竖切3，2026-06-24）：** 把模块2 因子分喂进 LLM，并用模块3A 的 IC 显著性给每个因子挂可回测背书——LLM 看到「动量分 A」时同时看到「动量因子回测显著正向 IC+0.04（置信高）」或「不显著（仅描述性）」。**纯函数** `factor_confidence.py`：`load_ic_summary()` best-effort 读缓存 `var/factor_ic/summary.json`（缺失→空，TTL 1800s）；`factor_confidence(ic_factors,key)→{level,basis}`（显著且 mean_ic≥0.03→高、显著弱正→中、显著反向/不显著→低、size 或无数据→不足）；`factor_reliability()` 对模块2 四因子各算一次。**装配** `portfolio_snapshot.build_factor_scores_for_facts`：调 `build_factor_scores_payload`（重）+ 挂 `factor_reliability` + 压成紧凑结构（每持仓 composite_grade/score + factor_percentiles），**TTL 1h 缓存（按持仓代码）+ best-effort**（异常→available=false 不阻塞日报；注入 fetcher/ic_factors 时绕过缓存便于测试）。**注入**：`build_analysis_facts` 加可选入参 `factor_scores`→`facts["factor_scores"]`；`analysis_payload.build_user_payload` best-effort 计算并传入（仅 for_llm 路径）；`analysis_facts.instruction` + `analysis_prompt.DEFAULT_ROLE_PROMPT` 各加护栏「按 factor_reliability 用因子分：高可作论据/中保留/低·不足仅描述、不得作买卖主理由；size 未回测仅参考」。**API+前端**：`/api/portfolio/factor-scores` 响应挂 `factor_reliability`；`PortfolioFactorScoresPanel` 每因子加「IC·X」置信小标签（`fundFactors.factorReliabilityTone`）。单测 `tests/test_factor_confidence.py` + `test_portfolio_snapshot.py`（紧凑/best-effort）+ `test_analysis_facts.py`（facts 注入）+ 前端 vitest。设计见 `docs/superpowers/specs/2026-06-24-factor-confidence-llm-design.md`。
- **板块信号可信度打分器 + 注入 LLM（模块4 竖切，4A+4B，2026-06-24）：** 路线图模块4「量化结论喂 LLM / 可信度打分器」第一个端到端竖切——让每条板块信号挂一个**可回测的置信分**，DeepSeek 按置信分级表述。**4A 纯函数** `signal_confidence.py::score_signal(bucket)→ConfidenceScore{level,score,basis}`：消费 3B 的 `trigger_count/hit_rate/baseline/edge/significant`，分级 **高(显著且edge≥10)/中(显著且5≤edge<10)/低(n≥30但不显著)/不足(n<30)**；`score=round(50+clamp(edge*2,-50,50)*min(1,n/50))` 落 0~100（仅可视化，分级以 level 为准）；edge 缺失用 `h-b` 兜底、桶空→不足；常量 `MIN_TRIGGERS=30/EDGE_MEDIUM=5/EDGE_HIGH=10` 对齐 3B。**4B 注入**：`sector_signal_context._compact_rules` 每规则加 `confidence` 字段；`analysis_facts.instruction` + `analysis_prompt.DEFAULT_ROLE_PROMPT` 各加一条护栏「高可作主理由/中措辞保留/低不足仅提示，不得主导追涨减仓」（双保险）；前端 `SectorSignalBacktestPanel` 用 `StatusPill` 展示「置信X」标签（`confidenceTone` 映射色，hover 显 basis）。**不改 3B 回测算法**，只做打分表述+喂 LLM。单测 `tests/test_signal_confidence.py`（等级/边界/score范围/兜底）+ `tests/test_sector_signal_context.py`（compact 带 confidence）+ 前端 `SectorSignalBacktestPanel.test.tsx`。设计见 `docs/superpowers/specs/2026-06-24-signal-confidence-design.md`、计划见 `docs/superpowers/plans/2026-06-24-signal-confidence.md`。后续竖切按同骨架接入模块1风险度量/模块2因子分/3A IC 显著性。
- **价值/成长风格因子（模块3-3C，2026-06-24，离线工具）：** 给因子库补「风格暴露」——**收益型风格分析**（非持仓穿透）。**纯引擎** `fund_style_regression.py`：把基金日收益对价值/成长指数日收益做**二元 OLS（中心化闭式解 2×2 正规方程）**，输出 `beta_value/beta_growth/style_tilt(=bV-bG)/r_squared/label(偏价值/偏成长/中性)`；样本<60 天或两风格共线(det≈0)→`available=false`；`align_returns` 按公共日期升序对齐三序列。**CLI runner** `scripts/run_style_factor.py`（排行榜池 + 线程池拉 NAV、取价值/成长指数日线→日收益→逐只回归→落盘 `apps/api/var/style_factor/{report.txt,summary.json}`；默认 **国证价值 399371 / 国证成长 399370**，可 `--value-index/--growth-index`）。诚实划界：这是「长得像价值/成长」的**风格暴露**，不是基本面便宜/质量；真·基本面因子需持仓穿透，后续项目。单测 `tests/test_fund_style_regression.py`（植入价值/成长基金→tilt 方向正确 r²≈1、样本不足/共线→unavailable、runner 离线注入）。
- **分层抽样基金池（模块3-3D，2026-06-24）：** 把回测/打分的池从「取榜单前 N 名（偏强样本）」换成「跨业绩段等距分层抽样」。**纯函数** `fund_universe_sampler.py::sample_universe(rows, size)`（step=n/size 等距取样、保序、横跨赢家→输家；n≤size 或 size≤0 原样返回）。接入 3A runner `run_factor_ic.py`：`build_ic_report` 增参 `universe_mode("top"|"sampled")`/`sample_pool_size`，`sampled` 取大池再抽样；`--universe-mode/--sample-pool-size` CLI 选项，summary.params 记录 mode。诚实划界：`fetch_open_fund_rank` 子进程**上限 500 条**且清盘基金不在榜，只削弱**选择**偏差，幸存者偏差仍在；彻底去偏需 point-in-time 基金库。单测 `tests/test_fund_universe_sampler.py` + `test_factor_ic_backtest.py::test_runner_sampled_mode_stratifies_pool`。
- **板块信号回测基线修正（模块3-3B，修 Bug B，2026-06-24）：** `sector_signal_backtest.py` 原把命中率和**固定 50%** 比，对「预测上涨」类信号天然偏乐观。改为**方向感知自然基线**：按桶内实际「涨/跌/平」自然概率算基线（`_direction_fractions/_baseline_prob`），命中率需超基线 `EDGE_MIN_PERCENT(5%)` 且触发数≥`MIN_TRIGGERS_FOR_SIGNIFICANCE(30)` 才算显著（`_finalize_bucket` 产出 `baseline_rate_percent/edge_percent/significant`，`beats_baseline` 兼容别名旧 `beats_random`）；规则方向取自 `sector_signal_rules`。`sector_signal_context` 透传新字段、前端 `SectorSignalBacktestPanel` 展示自然基线+edge+显著性。单测 `tests/test_sector_signal_backtest.py`。
- **因子有效性回测 IC（模块3-3A，2026-06-24，离线工具）：** 回测模块2 的因子到底有没有预测力——在基金池上做 walk-forward Rank IC（信息系数）。**纯引擎** `factor_ic_backtest.py`：手写斯皮尔曼秩相关 `_spearman`（并列均值秩、零方差→None、抹浮点尘）、单期 `_rank_ic_for_period`（横截面<10 只→None）、主函数 `compute_factor_ic`（每 21 交易日取一横截面、前瞻 20 日，**前视偏差铁律**：t 日因子值只用 ≤t 的 NAV、未来收益只用 >t 的 NAV）；每因子输出 mean IC / ICIR / t 统计量 / %>0 / 显著性（n≥12 且 |t|>2）；检验**动量/风险调整/回撤 + 综合**（规模因子排除：历史规模拿不到）；composite 复用模块2 `_factor_stats/_zscore/_composite_z`。**共享 helper** `fund_factor_nav.py`（NAV 切片→因子原始值），模块2 `_target_from_nav` 重构为复用它（消重）。**CLI runner** `scripts/run_factor_ic.py`（排行榜池 + 线程池拉 NAV → 跑引擎 → 落盘 `apps/api/var/factor_ic/{report.txt,summary.json}`，summary.json 给模块4 喂 LLM 用）。**离线工具，无 API、无前端**（IC 偏专业、计算重）。诚实划界：池为排行榜偏强样本，有幸存者/选择偏差，IC 偏乐观，报告显著标注。单测 `tests/test_factor_ic_backtest.py`（**植入真信号→IC≈1**、噪声→不显著、**前视偏差守卫**、hypothesis IC∈[-1,1]）+ `tests/test_fund_factor_nav.py`。价值/质量因子(3C)、Bug B(3B)、全市场池(3D)后续独立成文。设计见 `docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md`、计划见 `docs/superpowers/plans/2026-06-24-factor-ic-backtest.md`。
- **持仓因子体检（模块2 第一期，2026-06-24）：** 给每只持仓在「开放式基金排行榜横截面」里打净值系因子分。① **纯函数引擎** `fund_factors.py`：四因子——动量（0.5×6月+0.3×3月+0.2×1年，权重0.40）、风险调整Calmar（1年收益/|1年回撤|，0.35）、回撤控制（近1年最大回撤，0.15）、规模（log10规模，0.10）；横截面流水线 **去极值(5/95)→z-score(裁剪±3)→按剩余权重归一合成→百分位→等级A/B/C/D**；缺因子不当0、零方差退化为0、池<30→`available=false`。② **装配层** `portfolio_snapshot.build_factor_scores_payload`：`fetch_open_fund_rank(limit=300)` 做横截面池，持仓在榜直接用榜单行、不在榜用净值兜底 `_target_from_nav`（近60/120/250交易日收益 + 复用 `portfolio_risk_metrics._max_drawdown`，规模置None）；`fetch_rank`/`fetch_nav` 可注入便于离线测试。③ **独立懒加载接口** `GET /api/portfolio/factor-scores`（较重，前端展开才请求，不进 dashboard）。④ **前端** `PortfolioFactorScoresPanel`（盈亏分析 Tab，风险体检下方，展开懒加载）+ `lib/fundFactors.ts` 解读话术（vitest）；Pro 门控：免费看综合分+等级+动量，Pro 解锁其余三因子。⑤ 基准池为排行榜偏强样本，话术统一写「可比池」不写「全市场」。**价值因子 / IC 信息系数明确归入模块3**（需额外数据 + 回测框架）。单测 `tests/test_fund_factors.py`（已知答案 + 装配层离线注入 + hypothesis 不变量）。设计见 `docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md`、计划见 `docs/superpowers/plans/2026-06-24-fund-factor-scores.md`。
- **组合风险度量（模块1，2026-06-24）：** 新增独立服务 `portfolio_risk_metrics.py`（纯 Python 标准库纯函数：波动率、最大回撤、夏普、索提诺、Beta/Alpha、HHI/有效持仓数），与 `risk.py`（阈值告警）职责分离。① **累计收益走复利累乘**（`_equity_curve`，纠正"简单百分比直接相加"的概念 bug；仅风险计算内部用复利，收益走势图/日历展示口径不变）。② **数据零新增**：组合日收益取自 `list_portfolio_daily_snapshots` 的 `daily_return_percent`，基准取已缓存沪深300日线（`fetch_index_daily_history("000300")`），Beta/Alpha 按 `snapshot_date` 逐日对齐取交集。③ **装配层** `portfolio_snapshot.build_risk_metrics_payload` 挂进 `GET /api/portfolio/dashboard` 响应的 `risk_metrics` 字段（前端零新增请求）；样本不足 20 交易日返回 `available=false` + 友好文案。④ **前端** `PortfolioRiskMetricsPanel`（盈亏分析 Tab，收益走势下方）+ `lib/riskMetrics.ts` 解读话术（含 vitest）；「好基灵 Pro」门控：免费显最大回撤+有效持仓数，Pro 解锁其余（`localStorage` 开关，私有部署仅前端门控）。⑤ **配置** `FUND_AI_RISK_FREE_RATE`（默认 0.02，>1 自动归一）。单测 `tests/test_portfolio_risk_metrics.py`（已知答案 + hypothesis 不变量）。设计见 `docs/superpowers/specs/2026-06-24-portfolio-risk-metrics-design.md`。
- **组合风险度量第二批：相关性矩阵（2026-06-24）：** 持仓两两日收益皮尔逊相关性。纯函数 `portfolio_risk_metrics.compute_correlation_matrix`（`_pearson` + 全体公共交易日对齐，量纲无关），装配层 `portfolio_snapshot.build_risk_correlation_payload`（按持仓金额降序取前 15 只，`ThreadPoolExecutor` 并行拉各基金 nav-history 算逐日净值收益，注入 `fetch_nav` 便于离线测试）。**独立懒加载接口** `GET /api/portfolio/risk-correlation?lookback_days=120`（较重，前端展开才请求，不进 dashboard）；对齐后 <20 交易日或 <2 持仓返回 `available=false`。前端 `PortfolioCorrelationHeatmap`（N×N 热力图，红=同向/绿=反向，`max_pair` 给「假分散」话术）接进 `PortfolioRiskMetricsPanel`，Pro 解锁后展开懒加载。单测覆盖完全正/负相关、零方差、样本不足、日期对齐 + 装配层注入。
- **修正 Bug A：百分比收益走复利（2026-06-24）：** 纠正"简单百分比直接相加"的概念 bug——涨 3% 跌 3% 真实是 -0.09% 而非 0%（金额仍可相加，仅百分比改复利）。新增共享 helper `portfolio_profit_analysis._compound_return_percent`，统一应用到：① 收益走势图 `build_daily_trend_series`（组合与指数累计曲线）；② 盈亏日历 `build_calendar_month` 的 `month_cumulative_return_percent` / `month_index_return_percent`（`month_cumulative_profit` 金额仍为求和）；③ 近一周走势 `portfolio_snapshot.build_portfolio_trend_context`；④ 大跌反弹回测 `fund_dip_rebound_backtest`（未来 N 日累计反弹）。展示与计算口径自此统一为复利。
- **持有天数 + 盈亏日历 + 当日收益递延（2026-06-23）：** ① **持有天数**：修复详情页 `ensure_first_seen_anchor` 在读取时把锚点写成「今天」导致天数恒为 0；改由 `save_profile` 的 `reconcile_first_seen_date` 在持久化时一次性写入（购入日 > OCR 天数回推 > `shares_baseline_date` > 今天）；`shares_baseline_date` 早于错误 `first_seen_date` 时自动回退；读取层用 `_first_seen_anchor_date` 即时纠偏，不再写库。② **盈亏日历**：非交易日（周末 + 法定假日，新浪交易日历）收益固定 **0.00**，不沿用上一交易日快照；**今日**格在组合全部持仓切到 `official_nav` 前显示 **「未更新」**（`is_pending_update`），月累计不计入估算；已公布后用实时持仓重算官方收益。③ **当日收益递延**：支付宝 OCR 当日新购（日收益/持有收益/持有收益率均 ≈0）整行 `profit_accrual_deferred`，板块估算跳过直至下一交易日；`profit_accrual_defer.py`。④ **结算金额**：官方净值公布后 `settled_holding_amount` 滚入 `份额×最新净值`；持有金额展示仍为 settled-only（与支付宝列表口径分离）。设计见 `docs/superpowers/specs/2026-06-20-holding-days-anchor-design.md`、`2026-06-23-profit-accrual-defer-design.md`。
- **主题板块资金流历史 + 日报板块资金流（2026-06-23）：** ① **历史走势**：市场 → 主题板块展开行，四档明细下方懒加载「主力净流入走势」柱状图（近一周 5 日 / 近一月 20 日）；`GET /api/market/board-flow-history`；`board_fund_flow_history.py` 拉东财 `push2his` `fflow/daykline/get`（`secid=90.{BK}`）；host 优先 `80/82.push2his` + `_COMMON_PARAMS` + 4 轮重试/退避；按 BK 缓存（盘中 15min / 收盘 1h），失败回落 stale cache。② **BK 映射**：指数主题涨跌幅与资金流解耦；`theme_board_snapshot._THEME_BOARD_FLOW` 显式覆盖医药/贵金属/化工/交通运输等；`theme-boards` 每项带 `flow_source_code` 供前端/API 复用。③ **预热**：`refresh_theme_board_snapshot()` 写榜后后台线程限流预热历史资金流缓存。④ **日报 AI**：`sector_fund_flow_context.py` 按持仓关联板块注入 `analysis_facts.holdings[].sector_fund_flow`（当日/5d/20d 累计、四档、pattern 标签）；`trim_analysis_facts_for_llm` 保留摘要。前端 `BoardFlowHistoryChart.tsx` + `ThemeSectorOverview` 展开懒加载。设计见 `docs/superpowers/specs/2026-06-23-board-flow-history-design.md`、`2026-06-23-analysis-sector-fund-flow-design.md`。
- **移除简报 Tab + 持仓删除 + Bug 修复（2026-06-22）：** ① **信息架构**：删除冗余「简报」Tab 及 `TodayBriefing` 等组件；登录默认落地 **「持仓」**；顶/底导航 5 Tab（持仓/分析/市场/发现/日报）。② **线上 504**：`apply-holdings` 改「快速写入」（仅查码+档案+快照，不做同步板块/净值拉取）；前端 apply 成功后显式 `refresh-sector-quotes`（fast）。③ **无板块新基**：`refresh_holdings_sector_quotes` 在 boards/kline 全空时仍对有 `fund_code` 持仓拉天天基金估值兜底，避免硬失败红条与当日收益 0。④ **支付宝总览 OCR**：持有页判定优先于交易页 marker；扩展 `股票[A-CEH]` 后缀；总览 partial 早退回退切块；fixture `alipay_overview_holdings_5_ocr.txt`。⑤ **删除持仓**：`DELETE /api/portfolio/holdings/{fund_code}`（可选 `fund_name`）；详情页底部「删除该基金」+ 二次确认；仅从快照移除，保留 `fund_profiles`。设计见 `docs/superpowers/specs/2026-06-22-holdings-delete-and-fixes-design.md`。
- **小程序微信登录关联邮箱账号（2026-06-22）：** 修复小程序「微信一键登录」后看不到 Web 端持仓的问题。根因是身份隔离——业务数据按 `userId` 隔离，微信 callContainer 登录用 **openid** 为 key 新建空占位账号（`wx_*@wechat.fundpilot`），而 Web 持仓挂在邮箱账号下；且 Web `bind-wechat` 存的是 CloudBase **uid**，与小程序 openid 不同命名空间，原「Web 填 UID 绑定」路径实际打不通。方案：小程序侧账号打通。后端新增 `POST /api/auth/link-email`（需微信登录 JWT）→ `auth.service.link_email_account`：校验当前为微信占位账号 + 邮箱密码 → `database.merge_wechat_account_into_email_user` 事务内把占位账号 `cloudbaseUid` 迁移到邮箱账号并软删占位账号 → 返回邮箱账号新 JWT；之后每次微信登录稳定命中邮箱账号。小程序新增 `pages/link-email/`，持仓空态加「关联已有邮箱账号」入口，`utils/api.js` 加 `linkEmail()`。后端单测 +3（`tests/test_auth.py`）。
- **AI 简报首页 + 移动端导航（2026-06-21）：** 方案 B「蚂小财式」简报首页落地。① **信息架构**：登录默认 Tab 改为 **「简报」**（`TodayBriefing`）；原养基宝持有看板独立为 **「持仓」** Tab（`YangjibaoHoldingsBoard`）；主 Tab 顺序：简报 / 持仓 / 分析 / 市场 / 发现 / 日报。② **简报页**：`todayBriefing.ts` 汇总组合 KPI、板块脉搏、嵌入最新日报决策卡（`BriefingDecisionCards`）、内联 AI 追问（`BriefingChatPanel` / `ReportChatPanel inline`）。③ **导航**：`DashboardNav.tsx` — 桌面 `lg`（≥1024px）顶部 6 Tab；手机/平板仅 **底部固定导航**（简报/持仓/分析/市场/更多→发现/日报/历史）；修复 `.dashboard-bottom-nav { display:flex }` 覆盖 Tailwind `hidden` 导致顶底双栏并存；`.dashboard-shell` 底部留白 `5.75rem + safe-area` 避免市场页「数据日期」脚注被底栏遮挡。④ **大跌雷达 UI**：`DipReboundRadar` 改卡片列表（避免表格列宽截断「深度扫描」）。⑤ **落地页/注册**：转化优化（步骤、人群、sticky CTA）。设计/计划见 `docs/superpowers/specs/2026-06-21-ai-briefing-home-design.md`、`docs/superpowers/plans/2026-06-21-ai-briefing-home.md`。
- **本地开发 DB 回落（2026-06-21）：** 云 MySQL（如腾讯 CynosDB 30min 自动暂停）冷启动超时时，API 不再裸 500 触发浏览器 `Failed to fetch`。`db_connect.connect_with_fallback()` — MySQL 连接失败且 `FUND_AI_DB_FALLBACK_SQLITE=true`（默认，`scripts/dev.sh` 导出）时回落 SQLite；`main.py` 全局 `Exception` → JSON 500；CORS 仍最外层。`.env.example` 已文档化。
- **M3 大跌雷达历史命中率 + 主题联动（2026-06-21）：** `fund_dip_rebound_backtest.py` 板块指数代理回测（dip 日 → 未来 3 日累计反弹 ≥ `fee_break_even`）；`dip_radar_snapshot` 每项 `historical_hint`（`sample_count`、`rebound_rate_3d_percent`、`note`）。主题板块行操作「看大跌基金」→ `sessionStorage` `fundpilot-dip-radar-sector` + 子 Tab 切大跌雷达；「加入关注方向」→ `fundpilot-discovery-focus-sectors`（≤3）；`FundDiscoveryPanel` 挂载时读取关注方向预填。
- **好基灵 UI 全面升级（2026-06-21）：** 「静谧蓝海·高级克制」设计语言三轮落地，覆盖全产品前端。① **设计系统**：字体从 Plus Jakarta Sans 换成 **Sora**（`next/font/google` 自托管，构建期拉取，零操作），中文用 PingFang / HarmonyOS / 雅黑 / Noto 系统栈，彻底去除 AI 味；品牌色升级为深海蓝 `#2356e0` + 暖金强调 `#cf9b3e`（仅用于钱/收益/高光），背景 `#f3f6fc`，阴影偏冷蓝更通透；新增 `--brand-deep` / `--muted-soft` / `--shadow-brand` / `--font-display`；`globals.css` 新增 `.eyebrow`、`.trust-strip`、`.stat-value`、`.device-shell`、`.float-badge`、`.plan-card.is-pro`、`.ribbon`、`.reveal`（分级延迟入场，支持 `prefers-reduced-motion`）等工具类；Tab 分段控件选中态改品牌蓝文字+细描边；`.kpi-value` 统一 display 字体；`--background`/`--line`/`.btn-primary/secondary` 阴影/描边全面对齐新 token，清除全局残留旧蓝 `rgba(37,99,235)`。② **落地页重做**（`LandingPage.tsx`）：Hero 改为「左文案 + 右产品预览」两列布局，右侧新增**手机产品预览**（仿真持有首屏：收益大数字 + 上涨火花曲线 + 板块 Mini 卡 + 悬浮徽标），吸引力 > 原纯文字版；文案换成「搭子」人味；新增能力指标条（30s/0手动/每日）和信任条；功能卡片加编号+编号 hover 光晕；新增**「会员方案」展示区**（免费版 vs 好基灵 Pro ¥19/月，列出盘中提醒/多账户/回测/导出等付费价值，标「即将上线」，纯展示，为盈利目标铺钩子）；CTA 区加暖金光晕；整体换用分级 `.reveal` 入场。③ **App 壳**：顶部导航改为**毛玻璃悬浮 App Bar**（`sticky top-0 backdrop-blur`）；用户头像阴影和 ring 对齐新品牌色。④ **「持有」首屏**：总资产英雄区加柔光底框（`.holdings-hero`），数字放大至 `2.15rem` + Sora 等宽字；当日收益数字换 display 字体并加大。⑤ **「盈亏分析」页**：`.pl-hero` 加品牌蓝径向柔光框，大数字换 Sora `2.6rem`。⑥ **日报/推荐/设置页**：日报标题、空态标题、推荐报告标题、推荐基金标题均换 `font-display extrabold`；设置页卡片换设计系统 `section-card` + `section-eyebrow`，输入框/按钮/链接接入新 token。纯前端表现层，不动后端/数据流，`lint`（0 warning）、`typecheck`、`build`（静态导出）全部通过；Playwright 截图脚本验证全流程无报错。截图见 `apps/web/verify-shots/`。
- **好基灵 toC 视觉改造（2026-06-20）：** 面向普通基民的视觉与体验升级，定位 toC 订阅产品。中文品牌名「好基灵」（英文 FundPilot 辅助），Slogan「好基灵，截个图就懂你的基金」。① 设计地基：`globals.css` 扩展信任蓝 `#2563EB` + 暖橙 `#FB8C3B` 点缀的 token 体系（圆角 20px、分层柔和阴影、`.btn-primary/secondary/accent/ghost`、`.badge`、`.input-field`、`.empty-state`、`.card-hover`、`.landing-*`），涨跌红绿不变。② 新增登录前**品牌落地页**（`LandingPage.tsx`）+ 复用品牌标识（`BrandMark.tsx`）；路由：未登录 `/` 看落地页、已登录看 Dashboard（`AuthProvider` 放行 `/`、`page.tsx` 按登录态分支，静态导出安全）。③ 登录/注册/设置页、Dashboard 顶部品牌头、持有页空状态、基金详情页、上传截图弹窗（新增三步引导）全部统一到品牌视觉。④ **全站色彩统一**：推荐基金线（原 indigo）、日报/复盘/要闻面板（原 violet）、市场 `指数` 标签（原 violet）等全部归一到品牌蓝；语义色（warning 琥珀、danger 玫红、conservative 翠绿）保留。纯前端表现层，不动后端/数据流，单测 38 项前端用例通过、`build` 静态导出正常。
- **持有天数锚点修复（2026-06-20，2026-06-23 补强）：** `FundProfile.first_seen_date`（JSON payload）；`save_profile` / `reconcile_first_seen_date` 在**持久化**时写入锚点（购入日 > OCR 天数 > `shares_baseline_date` > 今天），**禁止**详情页读取路径回填 `today`；`_resolve_holding_days` 优先级 `first_purchase_date` → `first_seen_date`（含 baseline 纠偏）→ OCR aging → 快照。设计见 `docs/superpowers/specs/2026-06-20-holding-days-anchor-design.md`。
- **脚本清理（2026-06-19）：** 删除一次性研发脚本 `compare_analysis_payload.py`、`ab_compare_reports.py`、`analysis_payload_legacy.py`、`verify_qdii_seeds.py`、`diagnose_qdii_vs_xiaobei.py`；保留 `dev.sh`、迁库、板块/美股诊断与浏览器兜底脚本。精简 `test_us_market_*` 冗余属性测试、`test_api` 中 sector-boards 重复冒烟，删除未引用 OCR fixture。
- **文档精简（2026-06-18）：** 删除 `docs/superpowers/` 全部历史 spec/plan（Superpowers 过程稿）；架构与 API 仅以本文 + `docs/design/` 运维/契约为准。
- **持仓口径统一（2026-06-18）：** 后端 `holding_client.serialize_holding_for_client` 为 API 持仓附加 `estimated_*` 展示字段；前端 `holdingDisplay.ts` 优先读 API、fallback `holdingMetrics.ts`；共享用例 `tests/fixtures/holding_metrics_cases.json`；契约见 `docs/design/holding-metrics-contract.md`。删除已 superseded 的市场 Tab 设计 spec/plan（以本文为准）。
- **代码清理与体验（2026-06-18）：** 移除已下线「全市场」子 Tab 对应前端组件（`SectorPerformanceCard` / `HotSectorList` / `marketSectorBoard.ts`）及未使用的 API client（`fetchMarketSectorBoard*`、日报/荐基 diff 等）；删除一次性校准脚本 `compare_theme_boards_xiaobei.py` / `probe_xiaobei_secids.py`。**Dashboard 主 Tab** 用 `sessionStorage`（`fundpilot-dashboard-tab`）刷新后保持当前页。**持有**页：`localStorage` 缓存优先展示 + `GET /api/portfolio/holdings` 内存响应缓存（120s）+ `refreshed_at` 更新时间；主题板块列头排序、BK 双映射主力净流入见上文「市场板块」。
- **板块代码修正 + 补全（2026-06-18）：** 修复 canonical **「云计算」错码**（`90.BK0968` 实为「固态电池」→ 正确 `90.BK0579`）；主题白名单补 医疗(BK0727)、家电(BK0456)、AI医疗(BK1170)、固态电池(BK0968)，实得 **~66 个**粗板块。仅港股系列/韩国综合/红利低波/证券保险（东财 `m:90` 无对应板块）未纳入。
- **主题板块改小倍式精选白名单（2026-06-18）：** 东财 `m:90 t:2/t:3` 含 ~500 细分行业/概念（钨、磨具磨料…）过碎；改为对标小倍养基「今日板块涨幅榜」的**固定粗粒度白名单**（`theme_board_snapshot._THEME_BOARD_WHITELIST`：人工智能/消费电子/半导体/稀土/创新药/软件/银行/保险…），每个名经 canonical → 别名 `_THEME_BOARD_ALIAS`（软件→软件开发、基建→基础建设、化工→基础化工、农业→农林牧渔、保险→保险Ⅱ、医疗→医疗服务、家电→家用电器、AI医疗→AI制药 等）→ 东财概念/行业**精确名**解析到 secid，解析不到（港股/指数类）跳过。其余口径（push2delay 日 K 同源算 change+streak、后台 15min 刷新、board_kind 标签、持仓高亮）不变。
- **板块列表 clist 改 push2delay 优先（2026-06-18）：** `eastmoney_spot_client._CLIST_HOSTS`/`_STOCK_HOSTS` = push2delay + push2 子域回落（push2 数字子域偶发 Server disconnected）；`_request_board_page`/`_fetch_board_records_via_requests`/`fetch_eastmoney_quote_by_secid` 三处切换；同时修复全市场子 Tab。批量日 K `fetch_canonical_daily_kline_series(allow_akshare=False)` 跳过 per-board AkShare 子进程兜底（~60 板块刷新提速）。
- **主题板块扩展 + 后台刷新（2026-06-18）：** `GET /api/market/theme-boards` 板块从 21 扩展（见上「精选白名单」），每行带 `board_kind`（industry/concept/index）；**涨跌幅与连涨天数统一从同一段 push2delay 日 K 计算**（`refresh_theme_board_snapshot` 并行、120s 预算），日 K 缺失用东财 spot f3（`change_hint`）兜底涨跌幅、连涨显示 `—`；移除 `linked_fund_count`；持仓按 canonical secid 精确高亮。**后台 daemon 线程时段感知刷新**（lifespan 启动、盘中 15min / 收盘 1h，env `FUND_AI_THEME_BOARD_REFRESH_*`，CI 关闭），接口与前端只读缓存（`theme:boards:v3`、前端 staleTime 15min）；响应新增 `refreshed_at`。前端 `ThemeSectorOverview` 加 行业/概念/指数 标签、更新时间用 `refreshed_at`
- **主题板块口径与 UI（2026-06-18）：** `GET /api/market/theme-boards` **日涨幅**改 canonical secid + `fetch_eastmoney_kline_close_percent`（**push2delay** trends2，与持有页一致）；不再用全市场现货榜模糊匹配。连涨天数仍走 `sector_daily_kline_provider`（push2delay 日 K → relay → AkShare）；历史日 K 不可达时列显示 `—` 并提示配置 relay。前端 `ThemeSectorOverview` 对齐小倍「今日板块涨幅榜」：排名/板块/连涨天数/涨跌幅；固定按涨幅排序（移除连涨榜 Tab）；持仓行「持仓」标签；`marketThemeBoard.ts` 格式化 helper + vitest。
- **东财 K 线去 push2his（2026-06-18）：** `eastmoney_trends_client` / 浏览器分时脚本 / 美股指数 clist 仅保留 **push2delay** + 少量 **push2** 子域；历史日 K 失败后走 sector-relay / AkShare。运维文档同步。
- **市场 Tab — 美股概览（2026-06-18）：** 第三子 Tab「美股」+ `GET /api/market/us-overview`（指数期货 + USD/CNY + QDII 盘前参考涨跌 + 美东时段）；需求/设计/任务见 `.kiro/specs/us-market-overview/`；诊断脚本 `apps/api/scripts/diagnose_us_market.py`。
- **市场 Tab — 主题板块（2026-06-17）：** 市场 Tab 子 Tab「全市场 | 主题板块 | 美股」；`GET /api/market/theme-boards`（canonical 21 主题：日涨幅、连涨天数、我的持仓）；缓存 `theme:boards:v2`- **市场 Tab — 板块表现（2026-06-17）：** 底部导航新增「市场」Tab；`GET /api/market/sector-boards`（`view=widget|list`）拉取东财行业/概念涨跌幅与主力净流入（`f3`/`f62`），服务端 `sector_board_snapshot` + `sector_quote_cache`（盘中 60s / 收盘 1h）；前端 `MarketTab` / `SectorPerformanceCard` / `HotSectorList` + `useCachedFetch` session 缓存- **持仓恢复与荐基体验修复（2026-06-16）：** `GET /api/portfolio/holdings` 默认 `enrich_loaded_holdings(with_network=False)` 快速返回快照/档案，移除 `main.py` 重复 enrich；份额×净值与官方净值覆盖仍由 `POST /api/holdings/refresh-sector-quotes` 完成。`GET /api/fund-discovery/sectors` 改 `build_sector_heat_ranking_for_ui()`（轻量当日涨跌、12s 总预算、超时仍返回 19 个板块标签）；`FundDiscoveryPanel` 关注方向仅挂载时拉取、本地缓存 + 20s 超时，持仓刷新不再反复请求；历史列表请求失败保留已有数据。`DiscoveryHistoryRail` 对齐 `HistoryRail` 支持**批量删除**；单测新增 `test_discovery_sector_heat.py`（**203** 项总量）。
- **CI / 单元测试加速（2026-06-16）：** GitHub Actions `api` job 使用 `pytest-xdist` 并行（`-n auto --dist loadscope`）、pip 缓存、CI 环境关闭 OCR 预加载/新闻/回测/战术调优；`tests/conftest.py` 统一 stub 交易日历、东财行情、板块热度，强制 SQLite（`FUND_AI_DATABASE_URL=""`）；移除重复/集成慢测，保留核心 API/OCR/持仓/荐基守卫等 **199** 项；单测超时 30s；本地串行约 40s。详见 README「验证」与 `.github/workflows/ci.yml`。
- **激进波段投资风格（2026-06-16）：** `InvestorProfile.decision_style` 新增 `aggressive`；顶部 **投资预设**（`conservative_hold` | `aggressive_swing`）一键切换浮亏/集中度/持有天数/手续费/净赚目标；日报离线规则 `aggressive_swing_recommendations.py`（跌深加仓 + 扣费后止盈减仓）；荐基选基策略 `dip_rebound`；`discovery_guard` 激进时放宽追高；**盘中盯盘** `POST /api/swing-alerts/evaluate` + `GET /api/swing-alerts/today`；持有 Tab `SwingAlertsPanel` + `useSwingAlerts`（15min 评估 + 浏览器通知）- **荐基候选池名称修复（2026-06-16）：** `discovery_candidate_pool._resolve_fund_name()` — 全局种子/主关联板块映射不再使用 `种子基金 {code}` 占位，改东财名称表 `lookup_fund_name_by_code` → 档案 → 代码回退。
- **后台任务浮层（2026-06-16）：** `BackgroundJobsStack` 于 `Dashboard` 层堆叠 `JobStatusFloat` + `DiscoveryJobStatusFloat`；荐基 `discoveryJobId` 提升为 Dashboard 状态，切 Tab 不丢进度、不与日报浮层互相遮挡；扫描中按钮显示「扫描进行中…」。
- **持仓金额同步说明（2026-06-16）：** OCR/板块刷新路径下 `enrich_loaded_holdings(with_network=True)` 会按档案份额×净值重算 `holding_amount`；**启动恢复**默认 `with_network=False` 用快照展示，避免 AkShare 子进程拖慢首页。重新上传总览截图可 `force_reset_shares` 对齐养基宝。
- **板块信号回测修复（2026-06-16）：** 概念板块日 K 走 **push2delay**（AkShare 同款 `smplmt`/`lmt`/日期范围）；拉取链：**东财 push2delay → sector-relay `/kline/daily` → AkShare 子进程**；**仅 `has_data=true` 时缓存 24h**（避免空结果被锁一天）；`SectorSignalBacktestPanel` 仍在「生成日报」诊断区，日报正文已移除快照面板。
- **日报 UI 精简（2026-06-16）：** 移除日报内「今日三行结论」「分析上下文」「板块信号回测快照」「与上一份日报对比」「系统计算事实 + 风险提醒」；`建议复盘` 移至调仓示意下方且默认折叠；主题要闻标题可点原文、去掉底部「新闻原文出处」；移除前端 `DatabaseBackupPanel`（后端 export/import API 仍保留）。
- **追问侧栏体验（2026-06-16）：** `useChatAutoScroll` — 用户上滑时不强制贴底，右下角「回到底部」；侧栏加高/加宽- **登录持久化（2026-06-16）：** `AuthProvider` 启动 bootstrap 失败重试 5 次；仅 HTTP **401** 清 token（网络错误不清）；登录/注册 401 不 wipe 已有 token。
- **历史日报（2026-06-16）：** `HistoryRail` 支持批量选择与删除。
- **`.env.example`（2026-06-16）：** 重组为「Secrets & paths」+「App defaults」；显式列出 relay / JWT 30 天 / `NEXT_PUBLIC_API_BASE_URL`。
- **调仓示意模拟修复（2026-06-15）：** `rebalance_simulator.py` 在报告未填 `amount_yuan` 时自动补算示意金额；超集中度「观察」也应用负变动；非集中度「减仓评估」按持仓 15% 给 fallback；`GET /api/reports/{id}/rebalance-simulation` 从 `analysis_facts.portfolio` 恢复集中度上限；前端 `RebalanceSimulationPanel` 展示 `amount_note`。
- **日报持有收益口径对齐（2026-06-15）：** `analysis_facts` 新增 `estimated_holding_return_percent` / `estimated_holding_profit` / `over_drawdown_limit`，与前端「持有」列一致；组合/单只浮亏风控改用有效持有收益率（盘中含板块估算），不再误用昨日结算 `holding_return_percent`。
- **数据缓存优化（2026-06-15）：** 前端 `clientCache.ts` + `useCachedFetch.ts`（SWR：盈亏分析 dashboard、业绩走势、持仓详情）；板块后台轮询改 `fast` 预算、手动刷新 `accurate`；分时图去掉无条件 forceRefresh；服务端指数日线 1h TTL、组合分时 fingerprint 并行、新闻盘中 15min 过期、信号回测 24h 缓存- **推荐基金全市场扫描（2026-06-15）：** 板块库扩展至 **19 个**；`DiscoveryRequest.scan_mode`：`full_market`（默认）| `portfolio_gap`- **JWT 登录有效期（2026-06-15）：** `FUND_AI_JWT_ACCESS_EXPIRE_MINUTES` 默认 **43200**（30 天）；前端 token 仍存 `localStorage`。
- **推荐基金 V3 选基策略（2026-06-14）：** 扫描区新增 **选基策略**：`均衡潜力`（默认，综合近3/6月强弱、惩罚极端近1年涨幅）与 `含新发观察`（每板块约2只近6月新发 + 均衡老基）；`DiscoveryRequest.selection_strategy`；`discovery_selection_strategy.py` + `fetch_new_fund_offerings`；守卫在 `avoid_chasing` 时对近1年≥100%或贴近区间高点降档；候选池面板展示近3/6月与「新发」标记- **推荐基金 V2（2026-06-14）：** Tab 扩展：右侧 **历史推荐**（`DiscoveryHistoryRail`）；可编辑 **荐基 AI 角色设定**（`discovery_prompt_state`，schema v6，`GET/PUT /api/discovery-prompt`）；**基金类型偏好**（`any` / `etf_link` / `no_c_class`）；报告内 **候选池面板**、**7 日推荐复盘**（`DiscoveryOutcomesPanel`）；推荐卡片可打开 **基金详情预览**；`GET .../diff`、`GET .../outcomes`、`GET /api/fund-discovery/recommendation-accuracy`- **推荐基金稳定性（2026-06-14）：** 修复关注方向空白（`discovery_sector_heat` 改用 `fetch_eastmoney_kline_close_percent` + 并行拉取）；修复扫描 `'str' object has no attribute 'topic'`（`summarize_all_topics(market_news)` 参数）；`GET /api/jobs/{id}` 单连接先查 `discovery_jobs`（`job_status_service.py`），DB 超时返回 503；CORS 中间件置于最外层；`DiscoveryJobStatusFloat` 轮询失败自动重试。本地开发无云库时可不设 `FUND_AI_DATABASE_URL` 用 SQLite。
- **推荐基金 Tab（2026-06-13）：** 新增「推荐基金」主 Tab：窄池候选（板块热度 + 种子/排行/映射，15~25 只）+ 可选 `focus_sectors`（最多 3 个）；`POST /api/fund-discovery/async` 异步生成 `FundDiscoveryReport`；`discovery_guard` 白名单/追高/预算守卫；报告 SSE 追问；独立表 `fund_discovery_reports` / `discovery_jobs` / `discovery_chat_messages`（schema v5）。与「生成日报」职责分离。
- **LLM 数据包质量对齐（2026-06-13）：** 在瘦身基础上经 A/B 验证（4 只持仓、fast 模式、真实 DeepSeek）：slim user JSON **约 -50%** 体积且 rubric 评分与 legacy 持平或略优。改进点：`news_titles` 当日不足 12 条时回填近几日标题，并合并 `topic_briefs.points.source_titles`；恢复 `holding_return_semantics`；稳健模式保留精简版 `sector_intraday`（`pattern_label`/`pattern_hint` 等 4 字段）；`news_bullish`/`news_bearish` 强制 JSON 数组；`requirements` 6 条。完整报告仍存全量 `analysis_facts`（不经 slim）；`news_citation` 守卫仍用完整 `market_news`。
- **AI 角色设定（2026-06-13）：** 「生成日报」Tab `RiskControls` 新增 **AI 角色设定** 多行输入（`data-testid=analysis-role-prompt`）；`analysis_prompt.py` 定义 `DEFAULT_ROLE_PROMPT`（仅分析 **已有持仓**，不荐新基；`fund_code`/`fund_name` 须与 `holdings` 一致）；`AnalysisRequest.system_role_prompt` 随异步分析传入；`deepseek_client._system_prompt()` 在角色层后拼接时间戳、新闻规则、稳健/战术、`prompt_tuning`、JSON 约束；SQLite/MySQL `analysis_prompt_state`（schema v4）+ `GET/PUT /api/analysis-prompt`；前端 `localStorage` 缓存 + API 双写（模式同风控画像）。**荐新基**规划为独立 Tab，不在日报角色 Prompt 内实现。
- **LLM 数据包瘦身（2026-06-13）：** `analysis_payload.py` `build_user_payload()` — user JSON 去重（移除顶层 `holdings`/`risk`/`fund_snapshots`/`ocr_text`/`analysis_session`）；`prefetched_news` → `news_titles`（仅标题）；完整输出约束迁入 system `OUTPUT_REQUIREMENTS_SYSTEM`；稳健模式裁剪 `market_flow`/`signal_backtest`/`prompt_tuning`；快速模式再裁 `portfolio_trend` 与精简 `topic_briefs`；`analysis_facts` 新增 `sector_fund_gap_percent`、`nav_trend.recent_5d_daily_change_percent`，`recent_nav_series` 喂模型时 cap 5 点；对比初版约 **-65%** JSON 体积。
- **fund_code → 主关联板块（2026-06-12）：** SQLite/MySQL 表 `fund_primary_sectors`（schema v3）；`fund_primary_sector_service.py` — 详情 OCR / 养基宝总览沉淀、全局种子（519674→半导体、015945→商业航天 等）、AkShare 季报重仓关键词投票推荐；支付宝导入确认后按 **code 查表**补全 `sector_name`，禁用「国防军工」等名称推断覆盖混合基；`GET /api/funds/{code}/primary-sector`、`POST .../refresh-holdings`、`POST /api/fund-primary-sectors/sync-from-profiles`；`GET /api/funds/search` 东财名称表模糊查码。
- **养基宝详情 OCR + 代码纠错（2026-06-12）：** 识别养基宝详情页（含 6 位代码、关联板块）；`ApplyHoldingsRequest.detail_profiles`；`PATCH /api/fund-profiles/{code}` 支持改 `fund_code`/`fund_name`；前端 `FundCodeEditModal`、OCR 确认弹窗可编辑 code/名称/金额并东财搜索；`AddHoldingModal` 三分栏（支付宝 / 养基宝总览 / 养基宝详情）。
- **支付宝 OCR 查码增强（2026-06-12）：** `fund_code_resolver` — 「发起式」归一、C 类优先、provisional 9xxxxx 清理、`reconcile_holding_fund_codes`；总览无 6 位码时走 AkShare `fund_name_em` 名称表。
- **板块涨跌口径说明（2026-06-12）：** 持仓列表「关联板块」列 **始终**东财板块/指数（`sector_return_percent`）；「当日」列官方净值优先、否则板块估算。混合基 015945/519674 的板块曲线 **不应相同**（商业航天 `BK0963` vs 半导体概念 `BK1036`）；519674 涨跌走概念半导体、分时图走中证半导体 `931865`。养基宝详情偶见「曲线相同但收盘数字不同」——多为 **基金估值/净值** 与 **板块涨跌** 口径混用，对比时请以总览「关联板块」列为准。见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md#养基宝关联板块曲线-vs-收盘数字)。
- **用户认证（2026-06-11）：** 邮箱注册/登录 + JWT；`users` 表（驼峰字段）；业务数据按 `userId` 隔离；Web `/login` `/register` `/settings`（绑定微信）；`GET /api/auth/me` 返回 `wechatBound`；`POST /api/auth/wechat-login`、`/api/auth/bind-wechat`；微信小程序 `apps/miniprogram`；MySQL/`FUND_AI_DATABASE_URL` + Docker 云托管；部署见 `docs/deploy/cloudbase.md`；pytest **302** 项。
- **盈亏分析 Tab（2026-06-11，2026-06-23 日历口径）：** 主 Tab「持有 | 盈亏分析 | 生成日报」；`PortfolioDashboard` 含收益走势、**盈亏日历**（非交易日 0.00；今日净值未公布显示「未更新」）、当日 TOP5、持仓甜甜圈；`GET /api/portfolio/dashboard`；`portfolio_profit_analysis.build_calendar_month` + `portfolio_official_nav_settled`；`DELETE /api/portfolio/snapshots?on_or_before=` 清理历史日快照。
- **UI 精简（2026-06-11）：** 用户菜单仅保留「历史日报」；生成日报页诊断折叠为 `DiagnosticsAccordion`；风控收进「高级设置」；移除废弃组件 `HoldingTable` / `PortfolioSummaryCard` / `TodayWorkflowSteps` / `NavLineChart` / `holdingReview.ts`。
- **风控偏好语义（2026-06-11）：** `prefer_dca` / `avoid_chasing` 随 `profile` 传入模型；`avoid_chasing` 在 `recommendation_guard` 中板块大涨时限制加仓档；`prefer_dca` 在离线规则中弱持有收益时倾向分批加仓。
- **板块信号闭环（2026-06-10）：** `sector_signal_rules` + `sector_signal_backtest` + `sector_signal_context` + `signal_guard_policy` — 回测写入 `analysis_facts.signal_backtest` / `guard_policy`；`prompt_tuning` 与守卫按历史命中率自动收紧/放松；前端 `SectorSignalBacktestPanel`（生成日报诊断区）；环境变量 `FUND_AI_SECTOR_SIGNAL_BACKTEST_*`。
- **账户汇总与日报流程简化（2026-06-10）：** 隐藏 `000000`/待录入占位行；官方净值「已更新」浅蓝标签（含右上角当日收益）；移除「详细校对」与「生成日报」页 `HoldingTable`；AI 分析直接使用账户汇总 `displayableHoldings`；移除「快捷操作」侧栏。
- **期望投入总额（2026-06-10）：** `InvestorProfile.expected_investment_amount` 滑条默认 3 万、1–10 万步进 5 千；持仓占比/集中度/减仓建议以期望投入为分母（`risk.resolve_weight_denominator`），避免减仓后占比误判偏高。
- **风控画像持久化（2026-06-10）：** SQLite `investor_profile_state` + `GET/PUT /api/investor-profile`；前端 `localStorage` 作缓存，启动时 API 优先、修改后双写。
- **开盘前交易日语义（2026-06-10）：** 对齐养基宝：`trading_day_pre_open`（当日 9:30 前）`effective_trade_date` 回溯上一交易日；账户汇总/板块涨跌/分时图日期统一；修复开盘前东财无当日 K 线导致「板块拉取失败」「暂无分时数据」；`GET /api/trading-session` 新增 `market_open_time`、`session_kind: trading_day_pre_open`。
- **持有金额自动同步（2026-06-10）：** `holding_amount_sync.py` — OCR 确认时 `bootstrap_holding_baselines` 锁定份额/成本；盘中/恢复持仓时 `sync_holding_amounts_from_shares` 按档案份额 × 估值或官方净值更新 `holding_amount` 并重算持有收益；`amount_includes_today` 语义与 `holding_estimates.compute_daily_profit_from_rate` 联动。
- **支付宝持仓 OCR（2026-06-08）：** 支持上传支付宝「我的基金」列表截图；`alipay_holdings_parser.py` 解析三列交错 OCR 文本并自动匹配基金代码；`POST /api/ocr?preview=true` 预览、`POST /api/portfolio/apply-holdings` 确认写入；OCR 预热与 mobile 模型加速（`.env` `FUND_AI_OCR_*`）。
- **业绩走势（2026-06-08）：** 基金详情「业绩走势」Tab：近1月/3月/6月/1年/3年区间；本基金 vs 沪深300 区间涨跌对比折线图；成本价图例；历史净值表默认近1月预览，「查看历史净值」弹窗滚动分页加载（`GET /api/fund-profiles/{code}/nav-history/page`）；沪深300 日线优先新浪接口（`index_daily_client.py`），AkShare 备用。
- **持有天数（2026-06-08）：** 详情页点击「持有天数」弹出滚轮日期选择器设置首次购入日（`PATCH /api/fund-profiles/{code}` `first_purchase_date`）；OCR 详情天数随日历递增；持仓明细网格默认收起。
- **图表体验（2026-06-08）：** 分时图边框/虚线基准/十字辅助线；业绩走势图细线、Y 轴留白；日涨幅 `0%` 正确展示（修复 AkShare 日增长率为 0 时被丢弃）。
- **官方净值当日收益（2026-06-07）：** NAV 发布后 `daily_return_percent` 用官方日增长率，`daily_profit = 现金额 × r / (100 + r)`（结算前金额 × 涨幅，对齐支付宝）；`sector_return_percent` 仍仅展示东财板块涨跌；前端刷新不再用板块覆盖官方净值；账户汇总展示「昨日收益」。
- **持有收益展示（2026-06-07）：** 盘中 `持有收益 ≈ 昨日结算 + 板块涨跌`；官方净值公布后直接使用 OCR/档案中的含当日总值，不再叠加当日收益。
- **文档整理（2026-06-06）：** 合并历史迭代要点；`docs/design/` 仅保留分时 push2 运维 runbook，其余设计稿删除，以本文为准。
- **官方净值收益：** 收盘后以官方 T-day NAV 收益率替换板块估算；三层源标签（板块实时 / 收盘估算 / 官方净值）；修复周末日期回溯。
- **板块 canonical：** 养基宝常见板块名 → 东财 `secid` 硬编码映射（`sector_canonical.py`）；涨跌与分时统一走 **push2delay** K 线 / trends2。
- **分时 / push2：** 见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md)（931994 电网设备、push2delay、骨架点与小数形式防御）。

---

## 一句话

**FundPilot AI** 是面向 ≤5 人私有部署的基金投研助手：邮箱登录（Web）+ 可选微信小程序；支付宝/养基宝截图 → OCR → **账户汇总**（板块涨跌估算当日收益）→ 个人风控画像 + **可编辑 AI 角色设定** → 东方财富新闻（AkShare）+ DeepSeek V4 生成**逐持仓基金**操作建议日报；**推荐基金** Tab 从窄池候选中精选新基机会（持有期/金额/风险）；首页自动恢复持仓并刷新板块。本地默认 SQLite；云端可迁 CloudBase MySQL（见 `docs/deploy/cloudbase.md`）。

---

## 能力清单（当前已实现）

| 类别 | 能力 |
|------|------|
| 鉴权 | 邮箱注册/登录（JWT，默认 **30 天**有效）；Web `/login` `/register`；`/settings` 绑定微信（`cloudbaseUid`）；`UserMenu` 显示「未绑微信」；小程序 `POST /api/auth/wechat-login`；开发模式 `FUND_AI_CLOUDBASE_AUTH_DEV_MODE` |
| 输入 | 养基宝**总览 / 详情** OCR（详情含 6 位代码与关联板块）；**支付宝持有列表 OCR**（预览确认后写入）；确认弹窗可编辑 code/名称/金额并东财搜索；当日列为 `-` 时不填当日收益；**OCR 漏负号**时规则补符号 |
| 主关联板块 | `fund_primary_sectors` 表 + 全局种子 + 季报重仓推荐；支付宝导入后 **按 fund_code 查表**补板块名（非名称推断）；详情 OCR 自动沉淀 |
| 当日收益 | 盘中/净值未公布：**板块涨跌估算**（`holding_amount × sector_return%`）；NAV 发布后：**官方日增长率** + `daily_profit = amount × r / (100 + r)`；关联板块列始终东财涨跌；账户汇总附「昨日收益」；**份额×净值**自动更新持有金额（`holding_amount_sync`） |
| OCR 校验 | OCR 返回 `holding_warnings`；账户汇总为唯一持仓展示与日报输入源（`displayableHoldings` 过滤占位行） |
| 持仓元数据 | SQLite `fund_profiles` + `fund_primary_sectors` 由 OCR **自动维护**（份额、成本、板块、购入日）；拒绝 `+`/`-`/Tab 标签误存为板块名；`POST /api/fund-profiles/repair-sectors` 清理历史脏数据；查码走东财名称表 + 档案兜底；详情页铅笔改代码 |
| 简报首页 | **简报** Tab（默认）：`TodayBriefing` — 组合摘要 KPI、板块脉搏、最新日报决策卡、内联 AI 追问；`todayBriefing.ts` 纯前端汇总逻辑 + vitest |
| 首页看板 | **持仓** Tab：`YangjibaoHoldingsBoard` 养基宝式卡片（`AddHoldingModal` 上传支付宝/养基宝截图）；**localStorage 缓存优先** instant 展示 → `GET /api/portfolio/holdings`（服务端 120s 内存缓存 + `refreshed_at`）→ 后台自动 refresh-sector-quotes；点击行打开 `YangjibaoFundDetail` |
| 导航 | `DashboardNav`：桌面 `lg+` 顶栏 6 Tab；手机/平板底栏 5 项 +「更多」 sheet（发现/日报/历史）；`dashboard-shell` 底栏安全区留白 |
| 导航记忆 | Dashboard 主 Tab（简报/持仓/盈亏分析/市场/推荐基金/生成日报）与 **市场** 子 Tab（主题板块/大跌雷达/美股）均用 `sessionStorage` 刷新后恢复 |
| 基金详情 | 关联板块分时图（边框/十字线）；**业绩走势**（区间涨跌 vs 沪深300、历史净值分页）；**我的收益**；持有天数滚轮选购入日；持仓明细默认收起 |
| 盈亏分析 | **盈亏分析** Tab：`PortfolioDashboard` — 收益走势（当日/周/月/年/全部）、盈亏日历（周末/假日 **0.00**；今日官方净值未出 **未更新**）、当日 TOP5、持仓甜甜圈；`GET /api/portfolio/dashboard` |
| 组合风险体检 | `PortfolioRiskMetricsPanel`（盈亏分析 Tab）：波动率/最大回撤/夏普/索提诺/Beta/Alpha/HHI；纯函数 `portfolio_risk_metrics.py`；复利累乘净值曲线；挂在 dashboard 响应 `risk_metrics` 字段；Pro 门控（免费 2 项）。相关性矩阵 `PortfolioCorrelationHeatmap` 经独立懒加载接口 `GET /api/portfolio/risk-correlation` |
| 持仓因子体检 | `PortfolioFactorScoresPanel`（盈亏分析 Tab，风险体检下方，展开懒加载）：给每只持仓在排行榜横截面里打**动量/风险调整(Calmar)/回撤控制/规模**因子分（z-score→百分位→等级A/B/C/D）；纯函数 `fund_factors.py` + 装配层 `build_factor_scores_payload`（`fetch_open_fund_rank` 做池，不在榜走净值兜底）；独立接口 `GET /api/portfolio/factor-scores`；Pro 门控（免费仅动量）；价值因子/IC 归模块3 |
| 市场板块 | **市场** Tab：`MarketTab` — 子 Tab「**主题板块 \| 大跌雷达 \| 美股**」；主题：`ThemeSectorOverview`（`GET /api/market/theme-boards`，**小倍式精选白名单 ~66**、`board_kind` 标签、涨跌幅+连涨 **push2delay 日 K**、**主力净流入+四档展开+历史走势柱状图（近一周/近一月，`GET /api/market/board-flow-history` 懒加载）**、列头排序、行操作「看大跌基金」「加入关注方向」；后台 15min 刷新、前台只读缓存）；大跌雷达：`DipReboundRadar`（`GET /api/market/dip-radar`，跨板块 NAV 大跌 Top N、`rebound_score`/反弹信号、**`historical_hint` 板块反弹命中率**、「深度扫描」跳转荐基 `dip_swing`） |
| 板块注册表 | `sector_registry` + `sector_registry_data` — 统一主题榜/荐基 chips 的 `market_quote`/`discovery_quote`、别名与 `discovery_eligible`/`theme_board_eligible`；`theme_board_snapshot` 优先读注册表 |
| 美股概览 | **市场** Tab 子 Tab「美股」：`UsMarketOverview`（`GET /api/market/us-overview`）— 纳指/标普/道指**指数期货**（真实期货，禁回退收盘价）+ USD/CNY 汇率指标卡 + QDII「盘前参考涨跌」列表（基于期货盘前涨跌估算，标注非承诺性预估）；美东时段标签（盘前/盘中/盘后/休市，含夏令时）+ 更新时间；服务端 snapshot + 时段感知 TTL（盘前/盘中 ≤60s、休市更长）+ 优雅降级（`*_status` 标 `ok`/`stale`/`unavailable`，绝不编造数值）；前端时段感知刷新（盘前/盘中 45s、其余 5min、不可见暂停） |
| 风控 | 浮亏线、单只集中度、**期望投入总额**（滑条 1–10 万）、**投资预设**（稳健持有 / 激进波段）、`decision_style`（`conservative` / `tactical` / **`aggressive`**）、扣费止盈参数、**偏定投** / **拒绝追高**；`InvestorProfile` 持久化 + localStorage |
| 波段盯盘 | `swing_alert_engine` + `swing_alert_service`；`POST /api/swing-alerts/evaluate`、`GET /api/swing-alerts/today`；持有 Tab `SwingAlertsPanel`；`useSwingAlerts` 15min 自动评估 + 桌面通知；高级设置：手续费%/净赚%/盯盘范围 |
| 报告 | 组合摘要 + `fund_recommendations` + `topic_briefs` + `market_news`；`analysis_facts`；守卫 + 深度 `report_judge` |
| 喂模型数据包 | `analysis_payload.build_user_payload()` 瘦身 user JSON（约 -50%）；落库仍全量 `analysis_facts` |
| 生成日报 | 「生成日报」Tab：`RiskControls`（**AI 角色设定**可编辑 + 高级设置折叠风控）+ `NewsPreviewPanel` / `SectorSignalBacktestPanel` / `RecommendationAccuracyPanel`；诊断项收进 `DiagnosticsAccordion`；日报 **仅分析已有持仓**，荐新基见独立 Tab |
| 推荐基金 | 「推荐基金」Tab：`FundDiscoveryPanel` — **扫描模式**（`full_market` \| `portfolio_gap` \| **`dip_swing`**）、**19 个**关注方向、`dip_drop_scanner` 大跌预筛、**选基策略**（含 `dip_rebound`）、荐基角色、基金类型偏好、预算、快速/深度；`DiscoveryReportPanel` + `DiscoveryHistoryRail`（含批量删除）+ `DiscoveryOutcomesPanel` |
| AI 角色 Prompt（日报） | `analysis_prompt.py` `DEFAULT_ROLE_PROMPT`；用户自定义 `role_prompt`（≤4000 字）持久化 `analysis_prompt_state`；`GET/PUT /api/analysis-prompt`；生成时 `system_role_prompt` 传入 `POST /api/analyze/async` |
| AI 角色 Prompt（荐基） | `discovery_prompt.py` `DEFAULT_DISCOVERY_ROLE_PROMPT`；持久化 `discovery_prompt_state`（schema v6）；`GET/PUT /api/discovery-prompt`；扫描时 `DiscoveryRequest.system_role_prompt` 传入 `discovery_client` |
| 复盘/模拟 | outcomes / outcomes-weekly / rebalance-simulation / recommendation-accuracy |
| 信号诊断 | `GET /api/diagnostics/sector-signal-backtest` — 板块短线规则历史命中率（东财日 K；失败时 relay/AkShare 兜底） |
| 交易日语义 | `trading_session.py` + `trade_calendar_cache`；**9:30 前** `trading_day_pre_open` 展示上一交易日（对齐养基宝，周末/节假日同理）；`TradingSessionBar` |
| 穿透估算 | 未收盘时按板块权重分配账户当日收益 |
| 板块实时 | **canonical 映射优先**（`sector_canonical` → 东财 `secid` K 线）；未知板块再走 spot 批量表 + `sector_quote_resolver` + `sector_on_demand`；可选中继/浏览器命令；300s 自动 + 手动；低置信度 `SectorMappingModal`；有场内指数时优先指数口径（`sector_quote_lookup_label`） |
| 分时图 | `GET /api/sector-quotes/intraday`；push2delay 首选；相对**昨收**对齐养基宝；骨架点 &lt;30 不写缓存；可选 `sector_intraday_browser_command` 浏览器兜底 |
| 官方净值 | AkShare `fund_open_fund_info_em` 覆盖**当日收益**（非板块列）；源标签：板块实时 / 收盘估算 / 官方净值；昨日收益取再上一交易日官方净值或 OCR |
| 工作流阻塞 | `workflowBlockers`（生成日报前校验，无独立阻塞清单组件） |
| 数据备份 | SQLite export/import API（`GET/POST /api/database/*`）；Web 面板已移除 |
| 小程序 | `apps/miniprogram`：登录、持有列表、基金详情（只读）；与 Web 经 `bind-wechat` 共享 `userId` |
| 云部署 | `apps/api/Dockerfile`、`docker-compose.cloud.yml`；`scripts/migrate_sqlite_to_mysql.py`；见 `docs/deploy/cloudbase.md` |
| CI / E2E | GitHub Actions：`api` 并行 pytest（**304** 项，~1min 量级）+ `web` lint/typecheck/build + Playwright 冒烟 |
| 基金诊断 | AkShare 概况/累计收益；详情页可 AkShare **按名称查码**并持久化 |
| 分析模式 | 快速 / 深度 |
| 体验 | Markdown 导出、桌面通知、**Sora 字体 + 中文系统字体栈**（PingFang / HarmonyOS / 雅黑 / Noto）UI；**「静谧蓝海·高级克制」设计语言**（深海蓝 `#2356e0` + 暖金 `#cf9b3e`、毛玻璃 App Bar、会员方案展示区）；**客户端 SWR 缓存**（盈亏分析/详情/业绩走势）；板块刷新 fast 轮询 + accurate 手动；追问侧栏智能滚动 |
| 报告追问 | SSE + ChatMarkdown；`useChatAutoScroll` 贴底/回到底部 |
| 异步任务 | `/api/analyze/async` + `/api/fund-discovery/async`；`Dashboard` 层 `BackgroundJobsStack` 堆叠 `JobStatusFloat` + `DiscoveryJobStatusFloat`（切 Tab 持续轮询）；`GET /api/jobs/{id}`（`job_kind` 区分日报/荐基） |
| 前端偏好 | localStorage：风控、**日报/荐基 AI 角色 Prompt**、分析模式、板块自动刷新 |

---

## 产品边界

| 会做 | 不会做 |
|------|--------|
| OCR、校对、风控、AI 日报（逐持仓）、可编辑角色 Prompt、**推荐基金 Tab**（窄池荐新基）、示意金额 | 自动下单、券商对接 |
| 邮箱登录、按用户隔离持仓（私有部署） | 公开大规模 SaaS |
| 本地 SQLite / 上传目录 | 默认把原始截图发往云端 |
| 公开新闻标题/摘要供模型参考 | 投资建议（报告须有 caveats） |

**隐私：** DeepSeek 收到**结构化持仓、风控、净值摘要、新闻标题/摘要**，不传原始截图。见 `README.md`「隐私和边界」。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js、React、TypeScript、Tailwind、Lucide；浏览器 `Notification` |
| 后端 | FastAPI、Pydantic v2、uvicorn；`lifespan` 可选 DB 自动导入 |
| 存储 | SQLite（本地）/ CloudBase MySQL（目标）：`users`、`reports`、`fund_profiles`、`portfolio_*` 等；业务表均含 `userId` |
| 鉴权 | JWT（邮箱密码 + 微信/CloudBase）；`FUND_AI_JWT_SECRET`；`app/auth/`（middleware、cloudbase_auth、routes） |
| AI | DeepSeek API；`fetch_market_news` Function Calling |
| OCR（可选） | PaddleOCR |
| 数据 | AkShare：净值 + `stock_news_em` / 基金公告 |

环境变量：`FUND_AI_*`、 `NEXT_PUBLIC_API_BASE_URL`。模板：`.env.example`。

---

## 仓库结构

```text
fundpilot-ai/
├── apps/api/app/
│   ├── main.py              # 路由
│   ├── lifespan.py          # 启动时可选 DB 自动导入
│   ├── config.py / models.py / database.py / db_connect.py（MySQL→SQLite 回落）/ db_migrations.py
│   ├── auth/                # JWT 中间件、邮箱/微信登录、bind-wechat
│   ├── mysql_bootstrap.py   # MySQL 建表（可选）
│   └── services/
│       ├── ocr_engine.py / ocr_parser.py / ocr_pipeline.py / alipay_holdings_parser.py / overview_pipeline.py
│       ├── index_daily_client.py   # 沪深300等指数日线（新浪优先）
│       ├── portfolio_parser.py / portfolio_snapshot.py / portfolio_holdings_service.py
│       ├── holding_validation.py / holding_metrics.py / holding_estimates.py / holding_amount_sync.py / holding_detail_service.py
│       ├── sector_quote_service.py / sector_quote_provider.py / sector_quote_resolver.py / sector_canonical.py
│       ├── sector_board_snapshot.py / theme_board_snapshot.py / dip_radar_snapshot.py / fund_dip_rebound_backtest.py / sector_daily_kline_provider.py  # 市场 Tab
│       ├── sector_registry.py / sector_registry_data.py / dip_drop_scanner.py  # 板块注册表 + 大跌预筛
│       ├── us_market_service.py / us_*_client.py  # 市场 Tab 美股概览
│       ├── fund_nav_service.py / eastmoney_spot_client.py / eastmoney_trends_client.py
│       ├── akshare_spot_client.py / sector_on_demand.py / sector_intraday_provider.py
│       ├── sector_intraday_browser_provider.py / sector_quote_browser_provider.py / sector_quote_relay_provider.py
│       ├── trade_calendar_cache.py / sector_labels.py / sector_quote_cache.py
│       ├── fund_code_resolver.py / fund_name_utils.py
│       ├── deepseek_http.py / fund_profile.py / risk.py / fund_data.py
│       ├── recommendation_guard.py / analysis_facts.py / news_citation.py
│       ├── recommendation_outcomes.py / rebalance_simulator.py / report_judge.py
│       ├── news_service.py / news_summarizer.py / news_cache.py
│       ├── penetration_daily_allocator.py / market_signal.py / trading_session.py
│       ├── portfolio_profit_analysis.py   # 盈亏走势、日历、TOP5
│       ├── portfolio_risk_metrics.py / fund_factors.py  # 组合风险度量 + 持仓因子打分（纯函数）
│       ├── fund_factor_nav.py / factor_ic_backtest.py   # 因子NAV共享helper + 因子IC回测引擎（模块3-3A，离线）
│       ├── fund_style_regression.py / fund_universe_sampler.py  # 价值/成长风格回归(3C) + 分层抽样池(3D)
│       ├── signal_confidence.py   # 板块信号可信度打分器（模块4-4A，纯函数，喂LLM）
│       ├── factor_confidence.py   # 因子 IC 置信映射（模块4 竖切3，读3A summary.json 喂LLM）
│       ├── cls_news_client.py / market_flow_client.py / news_freshness.py
│       ├── sector_momentum.py / sector_intraday_summary.py / sector_signal_*.py
│       ├── tactical_recommendations.py / prompt_tuning.py / recommendation_accuracy.py
│       ├── analysis_prompt.py     # 日报角色 Prompt 默认模板与持久化配置
│       ├── discovery_prompt.py    # 荐基角色 Prompt 默认模板与持久化配置
│       ├── analysis_payload.py    # 喂模型 user JSON 瘦身与按模式裁剪
│       ├── db_backup.py
│       ├── job_store.py           # 异步分析任务（含 stage）
│       ├── report_diff.py / report_export.py
│       ├── report_chat.py         # 追问 SSE + Tool 轮次
│       ├── report_chat_runtime.py # 追问 fast/deep
│       ├── report_chat_export.py  # 对话 Markdown
│       ├── deepseek_client.py / analysis_runtime.py / analyze_pipeline.py
│       ├── discovery_*.py           # 推荐基金：窄池、守卫、pipeline、chat、job_store、diff、outcomes、selection_strategy
│       ├── job_status_service.py    # GET /api/jobs/{id} 单连接查询 discovery/analysis
│       └── recommendations.py
├── apps/web/src/
│   ├── app/login/ register/ settings/   # 认证与账号设置
│   ├── lib/api.ts / holdingDisplay.ts / holdingMetrics.ts / portfolioHoldingsCache.ts / marketThemeBoard.ts / dipRadar.ts / todayBriefing.ts
│   └── components/
│       ├── AuthProvider.tsx       # JWT 与 /api/auth/me
│       ├── Dashboard.tsx          # 简报 / 持仓 / 盈亏分析 / 市场 / 推荐基金 / 生成日报 / 历史（Tab sessionStorage）
│       ├── DashboardNav.tsx       # 桌面顶栏 + 移动端底栏
│       ├── TodayBriefing / BriefingDecisionCards / BriefingChatPanel
│       ├── MarketTab / ThemeSectorOverview / DipReboundRadar / UsMarketOverview
│       ├── FundDiscoveryPanel / DiscoveryReportPanel / DiscoveryChatPanel / DiscoveryJobStatusFloat
│       ├── DiscoveryHistoryRail / DiscoveryCandidatePoolPanel / DiscoveryOutcomesPanel
│       ├── YangjibaoHoldingsBoard / YangjibaoFundDetail / AddHoldingModal / AlipayOcrConfirmModal
│       ├── PortfolioDashboard / ProfitAnalysisTrendChart / ProfitLossCalendar / DailyProfitTop5 / HoldingDonutChart
│       ├── PerformanceTrendPanel / PerformanceReturnChart / NavHistoryListModal / WheelDatePicker
│       ├── SectorMappingModal / IntradayPercentChart
│       ├── TradingSessionBar / useChatAutoScroll
│       ├── RiskControls / DiagnosticsAccordion / NewsPreviewPanel / SectorSignalBacktestPanel
│       ├── ReportPanel / JobStatusFloat / HistoryRail / UserMenu
├── apps/miniprogram/          # 微信小程序（登录、持有、详情）
├── apps/api/Dockerfile
├── docker-compose.cloud.yml
├── uploads/
├── data/app.db
├── scripts/dev.sh / dev.ps1 / migrate_sqlite_to_mysql.py / diagnose-sector-quotes.sh
├── docs/PROJECT_CONTEXT.md   # 本文
├── docs/design/holding-metrics-contract.md
├── docs/design/2026-06-04-eastmoney-intraday-troubleshooting.md
├── docs/deploy/cloudbase.md  # CloudBase 部署
└── README.md
```

---

## 推荐使用流程

```text
0. 首次使用 → http://127.0.0.1:3001/register 注册；已有账号 → /login
1. bash scripts/dev.sh → 打开 http://127.0.0.1:3001（默认「简报」Tab；本地 MySQL 不可达时自动回落 SQLite）
2. 「简报」看组合摘要与最新决策；「持仓」Tab 管理持有列表；启动自动恢复上次持仓；点刷新更新板块涨跌
3. 需更新金额时 →「持仓」页「新增持有」上传支付宝/养基宝总览截图
4. 「盈亏分析」Tab 查看收益走势、盈亏日历、当日 TOP5、持仓分布
5. 「推荐基金」Tab 可选关注板块、投资预设/选基策略、预算 → **扫描今日机会**（可与日报并行，右下角双浮层进度）
6. 「生成日报」Tab 确认投资预设与 **AI 角色设定** → 选快速/深度 → 生成日报
7. 点击持仓行 → 基金详情（板块分时、业绩走势、我的收益）；低置信度板块 → 映射弹窗
8. 可上传**支付宝持有列表**截图 → 预览确认 → 写入持仓
9. 需与小程序共用持仓 → 用户菜单「账号设置」→ 绑定 CloudBase UID（或 API `bind-wechat`）
```

### 账户汇总与持仓元数据

```text
今日页 → 支付宝/养基宝总览截图 → POST /api/ocr?preview=true → POST /api/portfolio/apply-holdings
       → 自动 sync_profiles（fund_profiles 表）+ bootstrap 份额 + sector_refresh
打开应用 → localStorage 恢复持仓（若有）→ GET /api/portfolio/holdings（内存缓存 + refreshed_at）→ 可选自动 refresh-sector-quotes
点击持仓行 → 基金详情（业绩走势、持有天数、板块分时）
```

### 基金详情：业绩走势与持有天数

```text
业绩走势 Tab → 默认近3月；切换近1月/6月/1年/3年；蓝线本基金、橙线沪深300
下方近1月净值预览 →「查看历史净值」→ 滚动加载更早记录（每页 30 条）
点击「持有天数」→ 滚轮选择首次购入日 → PATCH /api/fund-profiles/{code} → 天数按日历递增
```

**档案合并规则：** 总览有、档案无 → 自动简略档案（`is_provisional`）；总览消失 → 保留档案不删；总览更新金额/收益/板块，不覆盖详情才有的份额/成本/持有天数。

### 养基宝 OCR：负号与符号一致性

养基宝亏损为绿色减号；PaddleOCR 常漏负号。`ocr_parser.py` 规则层补符号（独立行 `-`、收益额与收益率对齐、账户总收益交叉校验、双/单金额版式）。回归：`tests/fixtures/yangjibao_overview_signed_daily_ocr.txt`。

---

## 核心业务流

### 同步分析（兜底，前端不主动调用）

```text
POST /api/analyze
  → FundProfileService.resolve_holdings
  → evaluate_portfolio_risk
  → FundDataService.get_snapshots_with_nav_trends
  → DeepSeekClient.generate_report（analysis_mode: fast | deep）
  → save_report
```

### 异步分析（主流程）

```text
POST /api/analyze/async { holdings, profile, analysis_mode, system_role_prompt? } → job_id
  → 线程池 run_analysis()
  → DeepSeekClient._system_prompt(role + OUTPUT_REQUIREMENTS_SYSTEM) + build_user_payload()
  → GET /api/jobs/{id} 轮询（JobStatusFloat，1.5s；含 stage_label）
  → status=completed 时含 report → onComplete 回调 → 切换报告 Tab
```

### 推荐基金（异步）

```text
POST /api/fund-discovery/async {
  holdings, profile, focus_sectors?, budget_yuan?, analysis_mode,
  fund_type_preference?, scan_mode?, selection_strategy?,
  dip_lookback_days?, dip_min_drop_percent?, system_role_prompt?
} → job_id
  → discovery_pipeline: 板块热度 → 窄池(15~25)；**scan_mode=dip_swing** 时 `dip_drop_scanner` 预筛大跌候选并强制 `dip_rebound` → 新闻摘要 → DeepSeek → discovery_guard
  → GET /api/jobs/{id} 轮询（job_kind=discovery；单连接查询；完成时含 discovery_report）
  → DiscoveryReportPanel + DiscoveryHistoryRail；POST .../chat SSE 追问
```

---

## 分析模式：快速 vs 深度

| | 快速 `fast` | 深度 `deep` |
|---|-------------|-------------|
| 模型 | `deepseek-v4-flash` | `.env` 中 `FUND_AI_DEEPSEEK_MODEL`（默认 pro） |
| 新闻预取 | 有，主题数 ≤3 | 有，按 `NEWS_MAX_TOPICS` |
| `fetch_market_news` Tool | **关闭**（`news_tool_max_rounds=0`） | 可开启（按 `NEWS_TOOL_MAX_ROUNDS`） |
| 适用 | 交易日赶时间 | 需要模型主动补新闻 |

实现：`analysis_runtime.resolve_analysis_runtime()`，请求字段 `AnalysisRequest.analysis_mode`。

---

## 报告追问：快速 vs 深度

| | 快速 `fast` | 深度 `deep` |
|---|-------------|-------------|
| 模型 | `deepseek-v4-flash` | `.env` 中 `FUND_AI_DEEPSEEK_MODEL` |
| 上下文 | 已生成日报 Markdown + 历史对话 | 同上 |
| `fetch_market_news` | **关闭** | 按需调用（受 `NEWS_TOOL_MAX_ROUNDS` 限制） |
| 传输 | SSE：`user_message` → `status`（深度）→ `token` → `done` | 同上 |
| 存储 | SQLite `report_chat_messages`，按 `report_id` | 同上 |

实现：`report_chat_runtime.resolve_report_chat_runtime()`；`POST /api/reports/{id}/chat` body 含 `chat_mode`。

```text
POST /api/reports/{id}/chat  { message, chat_mode }
  → save user message
  → [deep] 非流式 Tool 轮次（fetch_market_news）
  → 流式 chat/completions
  → save assistant message
```

---

## HTTP API

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/auth/register` | 邮箱注册 |
| POST | `/api/auth/login` | 邮箱登录 |
| POST | `/api/auth/wechat-login` | 微信小程序 / CloudBase 登录 |
| POST | `/api/auth/bind-wechat` | 已登录用户绑定微信（需 JWT；Web 侧用 CloudBase uid/accessToken） |
| POST | `/api/auth/link-email` | **小程序**微信占位账号关联已有邮箱账号（需微信登录 JWT；校验邮箱密码后把本次 openid 迁移到邮箱账号并软删占位账号，返回邮箱账号新 JWT） |
| GET | `/api/auth/me` | 当前用户（需 JWT） |
| POST | `/api/ocr` | 截图/文本 → holdings；`preview=true` 仅解析不写入；支持支付宝列表 |
| POST | `/api/portfolio/apply-holdings` | 确认 OCR 预览结果写入持仓与快照；body 可选 `detail_profiles`（养基宝详情 OCR 档案） |
| GET | `/api/funds/search?q=` | 东财基金名称表模糊查码（OCR 确认 / 改码 picker） |
| GET | `/api/funds/{code}/primary-sector` | 查询 fund_code→主关联板块（DB / 档案 / 种子） |
| POST | `/api/funds/{code}/primary-sector/refresh-holdings` | AkShare 季报重仓推荐板块并写入表 |
| POST | `/api/fund-primary-sectors/sync-from-profiles` | 从已有 `fund_profiles` 批量同步板块映射 |
| POST | `/api/analyze` | 同步生成 Report（兜底） |
| POST | `/api/analyze/async` | `{ job_id, status }` |
| GET | `/api/trading-session` | 交易日/收盘窗口语义 |
| GET | `/api/investor-profile` | 读取持久化风控画像（未保存时返回默认） |
| PUT | `/api/investor-profile` | 保存风控画像（含 `decision_style`、`investment_preset`、激进波段参数、盯盘开关） |
| POST | `/api/swing-alerts/evaluate` | 评估持仓/全市场波段信号（服务端去重写入 `swing_alert_fired`） |
| GET | `/api/swing-alerts/today` | 当日已触发波段提醒列表 |
| GET | `/api/analysis-prompt` | 读取 AI 角色设定；含 `role_prompt`、`is_custom`、`default_role_prompt` |
| PUT | `/api/analysis-prompt` | 保存角色设定；body `{ role_prompt }`，`null`/空串恢复默认 |
| GET | `/api/reports/{id}/outcomes-weekly?days=7` | 7 日建议复盘 |
| GET | `/api/reports/recommendation-accuracy?days=30` | 相邻日报建议命中率（战术/稳健） |
| GET | `/api/diagnostics/sector-signal-backtest?days=120&sectors=半导体,商业航天` | 板块信号 T→T+1 回测；`sectors` 省略时用全部 canonical |
| GET | `/api/database/export` | 下载 SQLite |
| POST | `/api/database/import` | 上传替换 DB（自动备份 `.db.bak`） |
| GET | `/api/jobs/{id}` | 任务状态（日报或推荐基金）；`job_status_service` 单连接先查 `discovery_jobs`；含 `job_kind`、`stage`/`stage_label`；完成时含 `report` 或 `discovery_report`；DB 不可用 503 |
| GET | `/api/discovery-prompt` | 读取荐基 AI 角色设定；含 `role_prompt`、`is_custom`、`default_role_prompt` |
| PUT | `/api/discovery-prompt` | 保存荐基角色设定；body `{ role_prompt }`，`null`/空串恢复默认 |
| GET | `/api/market/sector-boards` | 全市场板块行情（**Web 已移除 UI**；`theme_board_snapshot` 合并主力净流入仍读此缓存；`view=widget|list`） |
| GET | `/api/market/theme-boards` | 主题板块（**小倍式粗粒度精选白名单 ~66**…；含 `main_force_net_yi` / `flow_tiers`；默认 `sort=change`，前端列头本地排序；`sort=inflow` 可选）；其余字段同前 |
| GET | `/api/market/board-flow-history` | 板块主力净流入历史：`sector_label` 或 `board_code`（推荐传 `theme-boards` 的 `flow_source_code`）；`range=week`（5 交易日）\| `month`（20 交易日）；响应 `available`、`points[]`（`date`、`main_force_net_yi`、`flow_tiers`）、`cumulative_net_yi`；东财 `80/82.push2his` `fflow/daykline`；BK 解析：主题白名单 → `_THEME_BOARD_FLOW` → canonical；缓存 `board-flow-hist:v1:{BK}`（盘中 15min / 收盘 1h）；拉取失败读 stale cache |
| GET | `/api/market/dip-radar` | 大跌反弹雷达：`lookback_days`（3\|5，默认 5）、可选 `sector`（注册表 label）、`limit`（默认 20）、`force_refresh`；跨 `discovery_eligible`+`theme_board_eligible` 板块并集，经 `dip_drop_scanner.build_dip_pool_for_sectors` 取 Top N；缓存 `dip:radar:v1:{trade_date}:{lookback}`（盘中 60s / 收盘 1h）；响应含 `items[]`（`dip_drop_percent`、`rebound_score`、`rebound_signals`、**`historical_hint`** 板块代理 3 日反弹率）、`sector_dip_leaders` |
| GET | `/api/market/us-overview` | 美股概览快照（`UsMarketSnapshot`）：纳指/标普/道指**指数期货** + USD/CNY 汇率 + QDII「盘前参考涨跌」列表 + 美东时段（`session_kind`/`session_label`，含夏令时）+ `updated_at`；`force_refresh` 跳过服务端时段感知缓存；无需 JWT；任一数据源失败仍返回 200，经 `futures_status`/`forex_status`/`qdii_status`/`available`/`stale`/`message` 表达陈旧或不可用，绝不回退收盘价或编造数值 |
| GET | `/api/fund-discovery/sectors` | 荐基关注方向 chips：`build_sector_heat_ranking_for_ui()`（当日涨跌轻量拉取、12s 预算；超时回退全部标签）；扫描 pipeline 仍用完整 `build_sector_heat_ranking()` |
| POST | `/api/fund-discovery/async` | 创建推荐基金异步任务；body `DiscoveryRequest`；**`scan_mode=dip_swing`** 时走大跌预筛 + `dip_rebound` 选基策略 |
| GET | `/api/fund-discovery/reports` | 最近 30 条推荐报告 |
| GET | `/api/fund-discovery/reports/{id}` | 推荐报告详情 |
| GET | `/api/fund-discovery/reports/{id}/diff` | 与上一份推荐报告对比 |
| GET | `/api/fund-discovery/reports/{id}/outcomes` | 推荐后 N 日净值复盘（`days` 默认 7） |
| GET | `/api/fund-discovery/recommendation-accuracy` | 近期推荐方向命中率（`days` 默认 30） |
| DELETE | `/api/fund-discovery/reports/{id}` | 删除推荐报告 |
| GET | `/api/fund-discovery/reports/{id}/markdown` | 导出 Markdown |
| GET | `/api/fund-discovery/reports/{id}/chat` | 推荐报告追问历史 |
| POST | `/api/fund-discovery/reports/{id}/chat` | SSE 流式追问（body: `{ message, chat_mode }`） |
| GET | `/api/reports` | 最近 50 条 |
| GET | `/api/reports/{id}` | 详情 |
| DELETE | `/api/reports/{id}` | 删除 |
| GET | `/api/reports/{id}/diff` | 与上一份对比 |
| GET | `/api/reports/{id}/markdown` | 导出 Markdown |
| GET | `/api/reports/{id}/chat` | 报告追问历史 |
| POST | `/api/reports/{id}/chat` | SSE 流式追问（body: `{ message, chat_mode }`） |
| GET | `/api/reports/{id}/chat/markdown` | 导出追问对话 Markdown |
| GET | `/api/fund-profiles` | 持仓元数据列表（内部/调试） |
| GET | `/api/fund-profiles/{code}/nav-history?days=` | 单位净值走势（AkShare，最长约 800 交易日，含日增长率） |
| GET | `/api/fund-profiles/{code}/nav-history/page` | 历史净值分页（`limit`、`before_date`，最新在前） |
| PATCH | `/api/fund-profiles/{code}` | 更新档案字段（`first_purchase_date`、`fund_code`、`fund_name`） |
| GET | `/api/market/index-daily?symbol=000300&days=` | 指数日线（沪深300 等，新浪优先） |
| POST | `/api/holdings/refresh-sector-quotes` | 刷新板块涨跌；返回 `sector_quote_meta`、映射候选 |
| POST | `/api/sector-mappings/apply` | 持久化板块映射选择 |
| GET | `/api/sector-quotes/status` | 自动刷新开关/间隔/交易时段 |
| GET | `/api/sector-quotes/intraday` | 板块分时涨跌 |
| POST | `/api/holdings/detail` | 单只持仓详情（含 AkShare 查码、净值） |
| GET | `/api/portfolio/holdings` | 恢复首页持仓（快照优先，否则档案）；响应含 `refreshed_at`；服务端 **120s 内存响应缓存**（`portfolio_holdings_cache.py`，快照写入时失效） |
| GET | `/api/portfolio/summary` | 账户汇总 + 全部档案 |
| GET | `/api/portfolio/dashboard` | 盈亏分析：`range` 为 today/week/month/year/all；可选 `calendar_year`、`calendar_month`；含 profit_trend、profit_calendar、daily_top5、持仓分布、**risk_metrics**（组合风险体检，样本不足时 `available=false`） |
| GET | `/api/portfolio/risk-correlation` | 持仓相关性矩阵（懒加载，逐只拉 nav-history）；`lookback_days` 默认 120（30~400）；<2 持仓或对齐 <20 日返回 `available=false` |
| GET | `/api/portfolio/factor-scores` | 持仓因子体检（懒加载）：排行榜横截面 z-score 多因子打分（动量/风险调整/回撤/规模）；响应 `available`、`universe_size`、`funds[]`（`composite_score`/`composite_grade`/`factors{percentile,z,raw}`）；排行榜池 <30 只返回 `available=false` |
| DELETE | `/api/portfolio/snapshots` | 清除 `on_or_before`（含）及更早的日快照（运维/重置盈亏历史） |
| GET | `/api/reports/{id}/outcomes` | 上一份日报建议复盘 |
| GET | `/api/reports/{id}/rebalance-simulation` | 按报告动作 + 示意金额模拟调仓（缺 `amount_yuan` 时自动补算；超集中度「观察」也会减） |

前端封装：`apps/web/src/lib/api.ts`。

---

## 领域模型（摘要）

| 模型 | 要点 |
|------|------|
| **Holding** | 6 位代码、金额、持有/当日/昨日收益、板块；`sector_return_percent_source`（realtime / closing_estimate）；`daily_return_percent_source`（sector_estimate / official_nav）；`yesterday_profit`；见 `holding_analysis_payload` |
| **InvestorProfile** | 稳健默认；浮亏 8%、集中度 35%、期望投入 3 万（可配置）；`prefer_dca`（弱收益时分批加仓规则）、`avoid_chasing`（板块大涨限加仓档）；随 `profile` 传入 DeepSeek；持久化 `investor_profile_state` |
| **FundRecommendation** | action、amount_*、news_bullish/bearish、points |
| **NewsItem** | topic、title、is_today |
| **Report** | 含 `fund_recommendations`、`market_news`、`topic_briefs`、`analysis_facts`；`market_context` 保留字段恒 `[]` |
| **AnalysisRequest** | holdings、profile、ocr_text、**analysis_mode**、**system_role_prompt**（可选，≤4000 字；缺省用 `DEFAULT_ROLE_PROMPT`） |
| **AnalysisPromptConfig** | `role_prompt`、`is_custom`、`default_role_prompt`；持久化 `analysis_prompt_state` 按 `userId` |
| **DiscoveryRequest** | `profile`、`analysis_mode`、`focus_sectors`（≤3）、`budget_yuan`、`holdings`、**`fund_type_preference`**、**`selection_strategy`**、**`scan_mode`**（`full_market` \| `portfolio_gap` \| **`dip_swing`**）、**`dip_lookback_days`**（3\|5）、**`dip_min_drop_percent`**、**`system_role_prompt`**（可选，≤4000 字） |
| **DiscoveryPromptConfig** | `role_prompt`、`is_custom`、`default_role_prompt`；持久化 `discovery_prompt_state`（schema v6）按 `userId` |
| **DiscoveryRecommendation** | `action`、`suggested_amount_yuan`、`hold_horizon`、`confidence`、`points`、`risks`；**`dip_drop_percent`**、**`rebound_signals`**、**`target_exit_days`**、**`fee_break_even_percent`**（`dip_swing` 波段止盈语义） |
| **FundDiscoveryReport** | 推荐报告；含 `candidate_pool`、`discovery_facts`、`recommendations`；表 `fund_discovery_reports` |
| **ChatMessage** | report_id、role、content |
| **ReportChatRequest** | message、**chat_mode**（fast \| deep） |

占位码 `000000`：总览 OCR 无代码时，**仅**通过已保存 `FundProfile` 按名称补全；未知代码分析时保留 `yangjibao-ocr` 快照。用户在详情页打开基金时可东财名称表 / AkShare 按名称查码。

### fund_code → 主关联板块

混合基（如 015945 国防军工→**商业航天**、519674 创新成长→**半导体**）不能从基金名推断板块。解析优先级：

1. **用户表** — `fund_primary_sectors`（OCR 沉淀 / 手动 refresh-holdings）
2. **档案** — `fund_profiles.sector_name`（养基宝详情 OCR）
3. **全局种子** — `GLOBAL_FUND_SECTOR_SEEDS`（常见四只基）
4. **重仓推荐** — `fund_portfolio_hold_em` + 关键词投票（`POST .../refresh-holdings`）
5. ~~名称推断~~ — 支付宝导入路径 **禁用**（避免「国防军工」≠「商业航天」）

实现：`fund_primary_sector_service.py`；接入 `overview_pipeline`、`ocr_pipeline`、`fund_profile.save_profile`。

### 板块实时行情

**解析优先级：**

1. **Canonical（首选）** — `sector_canonical.get_canonical_sector`：养基宝常见名（商业航天、半导体、中证电网设备等）→ 固定东财 `secid`；涨跌经 `prefetch_canonical_kline_quotes` 拉 K 线收盘涨跌幅。
2. **持久化映射** — SQLite `sector_mappings`（用户曾在 `SectorMappingModal` 点选）。
3. **Spot 批量表** — `eastmoney_spot_client` push2 全市场 concept/industry/index；`sector_quote_resolver` 模糊匹配。
4. **按需补拉** — `sector_on_demand` 单板块 AkShare 子进程（短预算刷新会跳过）。
5. **可选中继/浏览器** — `sector_quote_relay_provider`、`sector_quote_browser_provider`（板块 spot）；`sector_intraday_browser_provider`（分时 push2 全断时）。
6. **兜底** — `fund_estimate_provider` 天天基金估值；前端标记「估值兜底」，不当作真实板块行情。

| 项 | 说明 |
|----|------|
| 场内指数 | 有 `intraday_index_name` 时优先用指数口径（`sector_quote_lookup_label`） |
| 快刷预算 | `/api/holdings/refresh-sector-quotes` 前端同步 5s；短预算下东财单 host 0.5s，跳过 AkShare 慢路径 |
| 缓存 | `sector_quote_cache` 按日 TTL；`force_refresh` 跳过持久化映射 |
| 元数据 | 响应含 `provider_path`、`from_stale_cache`、`summary.secid_matched` / `board_matched` / `estimate_fallback` |
| 分时 | `eastmoney_trends_client` + `sector_intraday_provider`；换机排查见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md) |

### 养基宝收益率语义（传给 DeepSeek / 首页展示）

| 字段 | 含义 |
|------|------|
| `sector_return_percent` | 关联板块/场内指数**当日**东财涨跌（展示用，不用官方净值替换） |
| `sector_return_percent_source` | `"realtime"` 板块实时 / `"closing_estimate"` 收盘估算 |
| `daily_return_percent` | 当日基金收益率：官方净值或板块估算 |
| `daily_return_percent_source` | `"sector_estimate"` 板块估算 / `"official_nav"` 官方净值 |
| `daily_profit` | 当日收益额；官方净值时 `amount × r / (100 + r)`，盘中估算时 `amount × sector% / 100` |
| `yesterday_profit` | 再上一交易日官方净值收益（或 OCR）；账户汇总「昨」行 |
| `holding_return_percent` | 持有收益率；OCR 多为**昨日结算**（不含今日盘中） |
| `estimated_holding_return_percent` | **与界面「持有」列一致**；盘中=昨日结算+板块估算；浮亏/风控判断用此字段 |
| `estimated_daily_return_percent` | 当日基金涨跌：优先 `daily_return_percent`，否则 `sector_return_percent` 估算 |

实现：`holding_estimates.py`（展示层收益计算）、`holding_metrics.py`（报告语义）、`holdingMetrics.ts`（前端镜像）；喂模型 user JSON 经 `analysis_payload.build_user_payload()` 含 `holding_return_semantics`（`HOLDING_RETURN_SEMANTICS` 四字段时间义）。

### 净值走势摘要（传给 DeepSeek，非完整 K 线）

生成报告时 `FundDataService.get_snapshots_with_nav_trends` 与 AkShare 快照**同一次拉取**近 N 日净值，经 `nav_trend_summary.summarize_nav_history` 压缩后写入 `analysis_facts.holdings[].nav_trend`：

| 字段 | 含义 |
|------|------|
| `period_change_percent` | 区间内涨跌幅 |
| `recent_5d_change_percent` | 近 5 交易日涨跌 |
| `distance_from_high_percent` / `distance_from_low_percent` | 距区间高/低点 |
| `trend_label` | 区间 + 近 5 日综合标签（如「区间震荡，近5日走弱」） |
| `recent_nav_series` | 最近若干日 `date` + `nav` 采样（默认 8 点；**喂模型时 cap 5 点**） |
| `recent_5d_daily_change_percent` | 近 5 交易日逐日涨跌幅数组（仅 `for_llm`） |
| `sector_fund_gap_percent` | 基金当日涨跌 − 板块涨跌（背离度，仅 `for_llm`） |
| `sector_fund_flow` | 关联板块主力净流入：当日/近5日+近20日累计/四档 + `pattern_label`/`pattern_hint`（高位出货、低位洗盘等 heuristic） |

配置：`FUND_AI_NAV_TREND_DAYS`（默认 66）、`FUND_AI_NAV_TREND_RECENT_SAMPLE`（默认 8）。前端 `GET /api/fund-profiles/{code}/nav-history` 仍用于完整折线图，与 AI 摘要独立。

### 官方净值收益覆盖（2026-06-07）

**背景：** 养基宝收盘后仅显示板块涨幅估算；NAV 发布前（通常 ~21:00）用板块估算，发布后须切官方净值。养基宝周末界面用**结算后金额** × 涨幅会低估当日收益（如 -166.40），支付宝/正确口径为**结算前金额** × 涨幅（如 -169.04）。

**流程：**

| 时段 | 当日收益源 | 板块列 | 实现 |
|------|-----------|--------|------|
| 9:30 前（开盘前） | 上一交易日结算 | 上一交易日收盘 | `session_kind = trading_day_pre_open`；`effective_trade_date` = 上一交易日 |
| 09:30–15:00（盘中） | 板块实时估算 | 东财实时 | `sector_return_percent_source = "realtime"` |
| 15:00 后、NAV 前 | 板块收盘估算 | 东财收盘 | `"closing_estimate"` |
| NAV 发布后 | **官方净值** | 仍东财板块 | `daily_return_percent_source = "official_nav"` |

**当日收益公式：**

| 场景 | 公式 |
|------|------|
| 官方净值已公布 | `daily_profit = holding_amount × daily_return% / (100 + daily_return%)` |
| 盘中板块估算 | `daily_profit ≈ holding_amount × sector_return% / 100` |

**昨日收益：** 再上一交易日官方净值涨跌（`compute_yesterday_profit_from_official_nav`），账户汇总「估算当日」列下展示「昨 ±xx」；OCR 详情页 `yesterday_profit` 作兜底。

**持有收益展示：** 盘中 `≈ 昨日结算持有收益 + 当日板块估算`；官方净值公布后直接使用 OCR/档案 `holding_profit`（已含当日），不再叠加 `daily_profit`。

**关键实现：**

- **`fund_nav_service.py`：** `get_official_nav_return()` 取 AkShare 日增长率；`compute_yesterday_profit_from_official_nav()` 算上一交易日收益。
- **`sector_quote_service.refresh_holdings_sector_quotes()`：** 官方 NAV 写入 `daily_return_percent` / `daily_profit` / `daily_return_percent_source`；**不**覆盖 `sector_return_percent`。
- **`holding_estimates.py`：** `overlay_official_nav_returns`（恢复持仓时补官方净值）、`compute_official_daily_profit`、`enrich_holdings_yesterday_profits`。
- **`holdingMetrics.ts`：** `applySectorDailyEstimate` 保留 `official_nav`；`computeDailyProfit` / `computeHoldingProfit` 与后端一致。
- **`YangjibaoHoldingsBoard`：** 估算当日 + 昨日收益子行；关联板块列独立展示东财涨跌；日期取自 `GET /api/trading-session` 的 `effective_trade_date`。
- **`holding_amount_sync.py`：** OCR 后锁定 `holding_shares`；刷新/恢复时 `shares × unit_nav` 更新金额；有份额时当日收益用 `r/(100+r)` 公式。

**缓存策略：** `_NAV_CACHE[f"{fund_code}:{trade_date}"]` TTL 24h（命中）/ 5min（未发布重试）。

### 有效交易日与分时（2026-06-10）

`get_effective_trade_date()` 在 `trading_day_pre_open` 与非交易日回溯至**上一交易日**；板块 K 线、分时、`sector_quote_service._get_last_trade_date()` 与首页日期展示均依赖此字段。开盘前拉取当日东财数据会为空，故须用上一交易日（养基宝同款）。分时在 `trading_day_pre_open` / 非交易日展示上一交易日收盘曲线（`sector_intraday_provider` `closed_session`）。

## 新闻与 DeepSeek

### 日报 Prompt 分层

生成日报时 DeepSeek **system** 消息由多层拼接，避免用户角色设定与工程约束冲突：

| 层级 | 来源 | 内容 |
|------|------|------|
| **角色设定** | `AnalysisRequest.system_role_prompt` 或 `analysis_prompt.DEFAULT_ROLE_PROMPT`；Web「AI 角色设定」可编辑 | 投顾人设、任务边界（**仅已有持仓、不荐新基**）、数据口径（板块/持有收益/估算）、反幻觉（`fund_code`/`fund_name` 须来自 `holdings`） |
| **系统后缀** | `deepseek_client._system_prompt()` + `OUTPUT_REQUIREMENTS_SYSTEM` | 当前时间戳、新闻/Tool 规则、`decision_style` 稳健/战术、`prompt_tuning` 动态提示、完整 JSON 输出约束 |
| **用户消息** | `analysis_payload.build_user_payload()` | 见下表「喂模型 user JSON」 |

**分工约定：** 风险偏好、浮亏线、期望投入等数值走 `profile`（高级设置滑条），**不要**写进角色 Prompt；荐新基、宽基/行业配仓规划为**未来独立 Tab**，与日报角色 Prompt 分离。

默认角色模板见 `apps/api/app/services/analysis_prompt.py` 中 `DEFAULT_ROLE_PROMPT`。

- **数据源（`FUND_AI_NEWS_SOURCES`）：** `eastmoney`（东财 `stock_news_em`）、`announcement`（基金公告）、`macro`（宏观主题，默认「上证指数」）。
- **预取：** `NewsService.prefetch_for_holdings` → `market_news`（标题 + ≤200 字 snippet + 链接）。
- **按主题摘要：** `news_summarizer.summarize_all_topics`（Flash，每主题 1 次）→ `topic_briefs`；失败 → `rule-fallback`；关闭：`FUND_AI_NEWS_SUMMARIZE=false`。
- **喂模型（user JSON）：** `build_user_payload()` 结构如下；**落库报告**仍用 `_compose_analysis_facts()` 全量事实，不经 slim。

| 字段 | 说明 |
|------|------|
| `today` | 分析日期 ISO |
| `profile` | 精简风控（`decision_style`、`prefer_dca`、`avoid_chasing`、浮亏/集中度/期望投入） |
| `holding_return_semantics` | 板块/持有收益/当日/估算四字段时间义 |
| `analysis_facts` | 系统只读事实（`trim_analysis_facts_for_llm` 按模式裁剪） |
| `news_titles` | 可引用标题列表：优先当日，不足 12 条回填近几日；合并 `topic_briefs.source_titles`；含 `is_today` |
| `topic_briefs` | 按主题摘要；fast 模式省略 `source_urls` |
| `requirements` | 6 条精简提醒（完整规则在 system） |

**`analysis_facts` 裁剪规则（phase 3）：**

| 条件 | 裁剪 |
|------|------|
| 全部模式 | 持仓去掉 `management_fee`/`fund_scale_yi`/`fund_type`；`news.topics` 去掉；`nav_trend` 去 `source`、`recent_nav_series` cap 5 |
| `decision_style=conservative` | 去掉 `market_flow`、`signal_backtest`（持仓级与组合级）、`prompt_tuning`；**保留**精简 `sector_intraday`（4 字段） |
| `analysis_mode=fast` | 再去 `portfolio_trend`；`topic_briefs` 为 minimal |

- **引用校验：** `news_citation` 守卫用完整 `market_news` 列表校验 `news_bullish`/`news_bearish`，不依赖 slim 后的 `news_titles`。
- **Tool：** 仅深度模式且 `news_tool_max_rounds > 0` 时注册 `fetch_market_news`（默认最多 3 轮）；Tool 补拉后 `merge_topic_briefs` 增量摘要。
- **缓存：** `news_cache` 表按 `topic+date` 同日复用。
- **兜底：** JSON 解析失败 → `_offline_report` + `recommendations.enrich_*`。

---

## 前端要点

- **简报 Tab：** `TodayBriefing` 组合 KPI + 板块脉搏 + 决策卡 + 内联追问；可跳转持仓/市场/日报。
- **持仓 / 生成日报 Tab：** 持有看板 vs 交易日历 + **AI 角色设定** + 风控画像 + `ReportPanel`。
- **推荐基金 Tab：** `FundDiscoveryPanel`（**扫描模式**含 `dip_swing`、19 板块关注方向、localStorage 热度缓存、荐基角色、基金类型偏好）+ `DiscoveryReportPanel` + `DiscoveryHistoryRail`（批量删除）+ `DiscoveryChatPanel`；`DiscoveryJobStatusFloat` 轮询失败自动重试；大跌雷达「深度扫描」经 `fundpilot-discovery-prefill` + `fundpilot-dashboard-tab` 事件预填并跳转；主题板块「加入关注方向」经 `fundpilot-discovery-focus-sectors` 预填 chips。
- **市场 Tab：** 子 Tab 主题板块 / **大跌雷达** / 美股；`loadMarketSubTab` + `fundpilot-dip-radar-sector` sessionStorage；主题行「看大跌基金」带板块过滤切雷达子 Tab。
- **缓存：** `clientCache.ts` / `useCachedFetch.ts` — 盈亏分析 `sessionStorage`、详情/NAV `memory`；`portfolioHoldingsCache.ts` — 持有 **localStorage** 优先展示；`loadDashboardTab` / `saveDashboardTab` — 主 Tab **sessionStorage**；`loadMarketSubTab` — 市场子 Tab；`loadDiscoverySectorHeatCache` 板块热度 30min；板块 `useSectorQuoteRefresh` 后台 `fast`、手动 `accurate`。
- **认证：** `AuthProvider` 注入 JWT；未登录访问受保护页会跳转 `/login`；`apiFetch` 自动带 `Authorization: Bearer`；CORS 中间件置于最外层（含 401 响应）。
- **用户菜单：** 历史日报（`HistoryRail` 支持批量删除）、**账号设置**（`/settings` 绑定微信）；未绑微信时显示角标；持仓元数据由 OCR 自动维护，无独立档案页。
- **日报正文：** 仅保留决策建议、主题要闻、调仓示意、建议复盘（折叠）等核心块；诊断与回测在「生成日报」Tab `DiagnosticsAccordion`。
- **分析：** `ReportPanel` + `JobStatusFloat` 异步轮询；提交时携带 `system_role_prompt`。
- **偏好：** `lib/storage.ts`（profile、**analysisPrompt** / **discoveryPrompt** 缓存、analysisMode、sectorAutoRefresh、**dashboardTab**）；风控与角色 Prompt 主存 SQLite/MySQL（`investor_profile_state` / `analysis_prompt_state` / `discovery_prompt_state`）。

---

## 环境变量

### 鉴权与数据库

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_JWT_SECRET` | — | JWT 签名密钥（生产必填，≥32 字符） |
| `FUND_AI_JWT_ACCESS_EXPIRE_MINUTES` | 43200 | JWT 有效期（分钟）；默认 30 天 |
| `FUND_AI_DATABASE_URL` | — | 设则使用 MySQL（`mysql://user:pass@host:3306/db`）；否则 SQLite `data/app.db` |
| `FUND_AI_DB_FALLBACK_SQLITE` | true | MySQL 连接失败时回落 SQLite（本地开发推荐；云库自动暂停冷启动时避免 API 500） |
| `FUND_AI_CLOUDBASE_ENV_ID` | — | 云开发环境 ID（微信登录校验） |
| `FUND_AI_CLOUDBASE_CUSTOM_LOGIN_KEY` | — | 自定义登录私钥 JSON 路径 |
| `FUND_AI_CLOUDBASE_AUTH_DEV_MODE` | false | `true` 时小程序可用开发 UID（仅本地联调） |
| `FUND_AI_CORS_ORIGINS` | `http://localhost:3001,http://127.0.0.1:3001` | 允许的前端 Origin（逗号分隔）；生产设为 Web 静态托管域名 |

### 板块实时

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_SECTOR_QUOTES_ENABLED` | true | 关闭则不走 live 板块 |
| `FUND_AI_SECTOR_QUOTES_TTL_SECONDS` | 60 | spot 缓存 TTL |
| `FUND_AI_SECTOR_QUOTES_AUTO_INTERVAL_SECONDS` | 300 | 前端自动刷新间隔 |
| `FUND_AI_SECTOR_QUOTES_DISCREPANCY_WARN` | 0.5 | OCR vs 实时板块相差阈值（百分点） |
| `FUND_AI_SECTOR_QUOTES_RELAY_URL` | — | 可选板块行情中继（`apps/sector-relay` 默认 `:8787`）；填 `http://host:8787/boards`；除 spot 外还提供 `GET /kline/daily` 供板块信号回测拉历史日 K |
| `FUND_AI_SECTOR_QUOTES_RELAY_TIMEOUT_SECONDS` | 2.5 | 中继请求超时（日 K 兜底会自动放宽） |
| `FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED` | false | 是否启用浏览器命令链路 |
| `FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND` | — | 浏览器命令，例如 `node scripts/sector-quote-browser-command.mjs` |
| `FUND_AI_SECTOR_QUOTES_BROWSER_TIMEOUT_SECONDS` | 4 | 板块 spot 浏览器命令超时 |
| `FUND_AI_SECTOR_QUOTES_RELAY_TOKEN` | — | 中继可选鉴权 Bearer |
| `FUND_AI_SECTOR_INTRADAY_BROWSER_COMMAND` | — | 分时浏览器兜底，如 `node scripts/sector-intraday-browser-command.mjs` |
| `FUND_AI_THEME_BOARD_REFRESH_ENABLED` | true | 主题板块后台 daemon 刷新线程开关（CI/pytest 关闭） |
| `FUND_AI_THEME_BOARD_REFRESH_INTERVAL_SECONDS` | 900 | 盘中/盘前主题板块刷新间隔（15min） |
| `FUND_AI_THEME_BOARD_REFRESH_IDLE_INTERVAL_SECONDS` | 3600 | 收盘/非交易日主题板块刷新间隔（1h） |
| `FUND_AI_RISK_FREE_RATE` | 0.02 | 组合风险指标无风险利率（年化小数；夏普/索提诺/Alpha 用；填 >1 视作百分数自动归一） |

### DeepSeek / 新闻

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_DEEPSEEK_API_KEY` | — | 无/占位符则离线；校验见 `config.normalize_deepseek_api_key` |
| `FUND_AI_DEEPSEEK_MODEL` | deepseek-v4-pro | 深度模式模型 |
| `FUND_AI_DEEPSEEK_MODEL_FAST` | deepseek-v4-flash | 快速模式（日报/追问） |
| `FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` | 300 | 读超时 |
| `FUND_AI_NEWS_ENABLED` | true | 关闭则不注册 Tool |
| `FUND_AI_NEWS_TOOL_MAX_ROUNDS` | 3 | Tool 轮数上限 |
| `FUND_AI_NEWS_SOURCES` | eastmoney,announcement,macro | 新闻源 |
| `FUND_AI_NEWS_SUMMARIZE` | true | Flash 按主题摘要 |
| `FUND_AI_NEWS_MACRO_TOPIC` | 上证指数 | 宏观检索主题 |
| `FUND_AI_NAV_TREND_DAYS` | 66 | 报告生成时拉取净值交易日数 |
| `FUND_AI_NAV_TREND_RECENT_SAMPLE` | 8 | `nav_trend.recent_nav_series` 采样点数 |
| `FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED` | true | 日报生成时是否拉取板块信号回测 |
| `FUND_AI_SECTOR_SIGNAL_BACKTEST_DAYS` | 120 | 板块信号回测窗口（交易日） |
| `FUND_AI_SECTOR_SIGNAL_BACKTEST_MIN_TRIGGERS` | 10 | 守卫按回测收紧/放松的最少触发次数 |
| `FUND_AI_NEWS_REQUIRE_TODAY_FOR_ADD` | true | 无当日新闻时守卫压过加仓建议 |
| `FUND_AI_DB_AUTO_IMPORT_PATH` | — | 启动时若文件存在则自动导入 DB（会先备份当前库） |
| `FUND_AI_OCR_PRELOAD` | false | 启动时预热 PaddleOCR |
| `FUND_AI_OCR_USE_MOBILE_MODELS` | false | 使用 mobile 模型（更快，适合列表截图） |
| `FUND_AI_OCR_MAX_IMAGE_SIDE` | — | OCR 前缩放最长边（像素） |

修改 `.env` 后需重启 API。

---

## 本地开发

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh    # 或 scripts/dev.ps1
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q          # 301 项，串行 ~25s
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope  # 与 CI 一致
cd apps/web && npm run lint && npm run typecheck && npm run build
cd apps/web && npm run test:e2e   # Playwright 冒烟
```

### 测试与 CI

| 项 | 说明 |
|----|------|
| 规模 | **304** 项单元测试（自 ~400+ 精简；去掉重复集成测与纯网络拉取测） |
| 离线 | `conftest.py` autouse stub：交易日历、基金名称表、东财 spot/K 线、板块刷新、`build_sector_heat_ranking` 等 |
| 数据库 | 测试强制 `FUND_AI_DATABASE_URL=""` → SQLite 文件库；勿在 pytest 期间连生产 MySQL |
| 超时 | `pytest.ini`：`timeout = 30` |
| 并行 | CI：`python -m pytest tests -q -n auto --dist loadscope`（`pytest-xdist`） |
| CI 环境变量 | `FUND_AI_OCR_PRELOAD=false`、`FUND_AI_NEWS_ENABLED=false`、`FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED=false`、`FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED=false` |
| 保留覆盖 | 核心 API（OCR/分析/荐基）、持仓指标、OCR 解析、discovery 守卫与候选池、`test_api.py` 集成冒烟 |

Workflow：`.github/workflows/ci.yml`（`api` / `web` / `e2e-smoke` 三 job）。

---

## 给 AI 的修改建议

1. 改 API：`models.py` → `main.py` → `api.ts` → 组件 → `tests/`。
2. 改报告结构：同步 `deepseek_client` JSON、`recommendations`、`_offline_report`、`Report` 类型。
3. 改异步流程：`job_store.py`（后端）→ `JobStatusFloat.tsx`（前端轮询）→ `Dashboard.tsx`（回调）。
4. 改追问：`report_chat.py` / `report_chat_runtime.py` → `main.py` chat 路由 → `ReportChatPanel.tsx` / `ChatMarkdown.tsx` → `tests/test_report_chat.py`。
5. 改 OCR/估算收益：`ocr_parser.py` → `holding_metrics.py` → `YangjibaoHoldingsBoard.tsx` / `holdingMetrics.ts` → `tests/test_ocr_parser.py`、`tests/fixtures/`。
6. 改盈亏分析：`portfolio_profit_analysis.py` → `portfolio_snapshot.py` → `GET /api/portfolio/dashboard` → `PortfolioDashboard.tsx` / `ProfitAnalysisTrendChart.tsx` → `tests/test_portfolio_profit_analysis.py`。
7. 改板块/净值收益：`sector_canonical.py` → `sector_quote_service.py`（板块 + 官方 NAV 写入 daily）→ `fund_nav_service.py` → `holding_estimates.py` / `holdingMetrics.ts` → `YangjibaoHoldingsBoard.tsx` → 相关 tests。
8. 改分时：`eastmoney_trends_client.py` → `sector_intraday_provider.py` → `IntradayPercentChart.tsx`；换机排查见 design 分时文档。
9. 改交易日/开盘前日期：`trading_session.py` → `sector_quote_service.py` / `sector_intraday_provider.py` / `holding_amount_sync.py` → `YangjibaoHoldingsBoard.tsx` / `TradingSessionBar.tsx` → `tests/test_trading_session.py`。
10. 改持有金额同步：`holding_amount_sync.py` → `overview_pipeline.py` / `portfolio_holdings_service.py` → `holding_estimates.py` → `tests/test_holding_amount_sync.py`。
11. 改风控画像/期望投入：`models.py` `InvestorProfile` → `database.py` / `main.py` `/api/investor-profile` → `risk.py` / `analysis_facts.py` → `RiskControls.tsx` / `storage.ts` → `tests/test_api.py`（`test_investor_profile_persistence`）。
12. 改账户汇总展示：`holdingMetrics.ts` `displayableHoldings` → `YangjibaoHoldingsBoard.tsx` → `Dashboard.tsx`（日报直接喂 `displayableHoldings`）。
13. 改日报角色 Prompt：`analysis_prompt.py` `DEFAULT_ROLE_PROMPT` → `deepseek_client._system_prompt` → `models.py` `AnalysisRequest.system_role_prompt` → `database.py` `analysis_prompt_state` → `main.py` `/api/analysis-prompt` → `RiskControls.tsx` / `storage.ts` / `api.ts` → `tests/test_api.py` / `tests/test_fund_profile.py`。
14. 改喂模型数据包：`analysis_payload.py` `build_user_payload` / `compact_news_titles` / `trim_analysis_facts_for_llm` → `analysis_facts.py`（`for_llm`、`sector_fund_gap_percent`）→ `nav_trend_summary.py` → `deepseek_client._generate_with_tools`（`append_output_requirements_to_system`）→ `tests/test_analysis_payload.py`。
15. 改推荐基金：`discovery_pipeline.py` → `discovery_candidate_pool.py` / `discovery_selection_strategy.py` / `discovery_guard.py` / `discovery_client.py` → `main.py` `/api/fund-discovery/*` → `FundDiscoveryPanel.tsx` / `DiscoveryReportPanel.tsx` / `api.ts` → `tests/test_discovery_*.py`、`tests/test_api.py`；行为与 API 见本文「推荐基金」。
16. 改荐基角色 Prompt：`discovery_prompt.py` → `models.py` `DiscoveryRequest.system_role_prompt` → `database.py` `discovery_prompt_state` → `main.py` `/api/discovery-prompt` → `FundDiscoveryPanel.tsx` / `storage.ts` / `api.ts`。
17. 改任务轮询：`job_status_service.py` → `main.py` `GET /api/jobs/{id}` → `JobStatusFloat.tsx` / `DiscoveryJobStatusFloat.tsx`；`db_connect.py` 超时参数。
18. 改市场板块：`sector_board_snapshot.py` / `theme_board_snapshot.py` / `board_fund_flow_history.py` / `dip_radar_snapshot.py` / `sector_daily_kline_provider.py` → `main.py` `/api/market/*` → `MarketTab.tsx` / `ThemeSectorOverview.tsx` / `BoardFlowHistoryChart.tsx` / `DipReboundRadar.tsx` / `api.ts` / `marketThemeBoard.ts` / `dipRadar.ts` → `tests/test_sector_board_snapshot.py` / `test_theme_board_snapshot.py` / `test_board_fund_flow_history.py` / `test_dip_radar.py`；行为与 API 见本文「市场板块」。主题板块后台刷新线程在 `theme_board_snapshot.theme_board_refresh_loop`，由 `lifespan.py` 启动（env `FUND_AI_THEME_BOARD_REFRESH_*`）；刷新 `refresh_theme_board_snapshot()`（缓存 `theme:boards:v3`，并后台预热 `board-flow-hist`）。改日报板块资金流：`sector_fund_flow_context.py` → `analysis_facts.py` / `analysis_payload.py` → `tests/test_sector_fund_flow_context.py` / `test_analysis_facts.py`。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、用户流程 |
| `docs/PROJECT_CONTEXT.md` | **本文** — 架构、API、数据流、环境变量（维护主入口） |
| `docs/deploy/cloudbase.md` | CloudBase 云托管 + MySQL + 小程序上线 |
| `apps/miniprogram/README.md` | 小程序本地联调与合法域名 |
| `docs/SECURITY.md` | API Key 与 Secret Scanning |
| `docs/design/holding-metrics-contract.md` | 持有列展示口径（前后端契约 + 共享 fixture） |
| `docs/design/2026-06-04-eastmoney-intraday-troubleshooting.md` | 分时 push2 换机自测、指数映射、脏缓存清理（仅运维时查阅） |
| `docs/superpowers/specs/2026-06-21-ai-briefing-home-design.md` | AI 简报首页设计方案 B |
| `docs/superpowers/plans/2026-06-21-ai-briefing-home.md` | AI 简报首页实现计划 |
| `docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md` | 模块2 因子思维：持仓因子体检设计（教学版） |
| `docs/superpowers/plans/2026-06-24-fund-factor-scores.md` | 模块2 因子体检实现计划（TDD 分步） |
| `docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md` | 模块3-3A 因子有效性回测(IC) 设计（教学版，离线工具） |
| `docs/superpowers/plans/2026-06-24-factor-ic-backtest.md` | 模块3-3A 因子IC回测实现计划（TDD 分步） |
| `docs/superpowers/specs/2026-06-24-factor-style-and-universe-design.md` | 模块3-3C 风格回归因子 + 3D 分层抽样池 设计（离线工具） |
| `docs/superpowers/specs/2026-06-24-signal-confidence-design.md` | 模块4 竖切 板块信号可信度打分器+注入LLM(4A+4B) 设计 |
| `docs/superpowers/plans/2026-06-24-signal-confidence.md` | 模块4 竖切 信号可信度 实现计划（TDD 分步） |
| `docs/superpowers/specs/2026-06-24-factor-confidence-llm-design.md` | 模块4 竖切3 因子分+IC置信→LLM 设计 |
| `docs/superpowers/specs/2026-06-23-board-flow-history-design.md` | 主题板块主力净流入历史走势 |
| `docs/superpowers/specs/2026-06-23-analysis-sector-fund-flow-design.md` | 日报 AI 注入板块资金流 |
| `docs/superpowers/specs/2026-06-21-dip-swing-discovery-design.md` | 大跌波段荐基设计 |
| `docs/superpowers/plans/2026-06-21-dip-swing-discovery.md` | 大跌波段荐基实现计划 |
| `.kiro/specs/us-market-overview/requirements.md` | 市场 Tab — 美股概览需求 |
| `.kiro/specs/us-market-overview/design.md` | 市场 Tab — 美股概览设计 |
| `.kiro/specs/us-market-overview/tasks.md` | 美股概览实现计划 |
| `.env.example` | 环境变量模板 |

- **改功能先改 `PROJECT_CONTEXT.md`**：能力清单、API、环境变量、目录结构须与代码同步。
- **`docs/design/`** 保留运维 runbook 与前后端契约（分时 push2 排查、持有列口径）；产品决策与实现细节以本文为准。
- **不保留** 已完成的一次性实现计划、清理报告、迭代日志。
