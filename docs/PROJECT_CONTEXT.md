# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 新对话或接手开发时先读本文，再按需打开具体文件。避免从零扫描仓库。
>
> **维护：** 功能或架构有实质变化时，同步更新「能力清单」「数据流」「API」「目录」「环境变量」。

**文档版本：** 2026-07-12（日报 / 荐基决策准确性 V2 收尾、历史回填与全量验收）

**更新记录：**
- **日报 / 荐基决策准确性 V2 完整闭环（2026-07-12）：** SQLite v10 / MySQL 新增不可变 `decision_portfolio_snapshots`、`decision_events`、`fund_benchmark_mappings`、修订式 `outcome_observations` 与哈希链 `portfolio_ledger_events`。用户可一次性确认实际份额、可选成本和现金；交易优先使用实际份额，未知费用保持 unknown，删除持仓追加零份额关闭事件，pending/未来确认/冲突/账本截断会 fail-closed；生产 MySQL fallback 不允许写入仓位真值。报告生成在同一事务冻结仓位、费用假设、模型/Prompt/策略版本与点时基准：仅完整基金业绩比较基准合同进入正式超额，跟踪指数/类别代理只作参考。日报与荐基统一按基金自身估值日评价 QDII 等品种，结果拆为毛方向、假设费后正收益、合同基准毛超额、合同基准假设费后超额四项；正式统计仅纳入持久化、主存储、可审计 DecisionEvent v2，legacy 继续可见但排除。OutcomeObservation pending 可修订、成熟终态锁定并在冲突时返回 409。前端新增账本基线确认和四指标审计网格；回填/SQLite→MySQL 迁移默认 dry-run、不可变表 insert-only。**本地库回填实绩：** 扫描 8 份历史日报，写入 26 条 legacy 决策事件和 6 份快照；本库无可回填荐基记录，未伪造历史 outcome；二次执行新增 0，数据库完整性、外键、事件/快照哈希及原报告字节一致性全部通过。**最终验证：** API **922 passed**；Web **333 passed**，typecheck、lint、Next production build 全通过；七视口 production UI E2E **63 passed / 21 expected skips / 0 failed**。完整契约见 [DECISION_ACCURACY_V2.md](DECISION_ACCURACY_V2.md)。
- **核心工作流与历史信息架构第三次优化（2026-07-12）：** 发现基金的历史推荐从页面级无限长列表重构为桌面端有界粘性侧轨、平板/手机端焦点受控抽屉；默认渐进挂载 12/20 条，支持按标题、板块和日期检索任意历史，切换报告时保留扫描条件与阅读位置，删除当前项后连续选择相邻报告。日报历史不再占用独立 Tab、用户菜单或移动「更多」入口，而是并入日报阅读区的 `ReportNavigator` 与 `ReportHistoryDrawer`：提供上一份/下一份/回到今日/全部历史，当前报告同步到 `?report=`，浏览器前进后退可恢复；历史加载或解析失败时保留当前正文，删除当前报告后选择相邻项。两个抽屉共用 `HistoryDrawerShell`，具备 modal 语义、Escape、焦点循环/恢复、背景滚动锁和独立滚动；移动端取消 masthead 的 `backdrop-filter`，修复其形成包含块后底部导航被抬入页头的问题。业务 API、报告/OCR/行情语义与存储键未改动。**本地验证：** Web **82 files / 328 tests**，typecheck、lint（0 warning）、Next production build 全通过；production UI E2E 全套 **63 passed / 21 expected skips / 0 failed**，其中新增历史工作流在 1440×900、1280×800、1024×900、768×1024、430×932、390×844、320×844 七视口 **14/14 passed**，覆盖 100 条历史有界挂载、搜索、焦点恢复、URL/浏览器历史、失败保留与删除连续性，并产出桌面/平板/手机回归截图。
- **灵析前端“深海投研编辑部”系统升级（2026-07-12）：** `apps/web` 统一为深墨蓝结构层、象牙阅读面与暖金决策高光，标题使用跨平台中文宋体栈、正文保持无衬线高可读性，金融数字继续使用 tabular numbers；圆角、阴影、动效和焦点节奏收敛为明确 tokens。品牌标识替换为“析”字与数据刻度组成的专属印记；落地页改为非对称编辑构图和真实研究台界面切片，移动固定 CTA 只在首屏 CTA 离开后出现；登录/注册改为桌面研究氛围双栏、移动专注表单；设置页按身份、隐私/数据和危险操作分组。应用壳层新增编辑式页头，持仓与历史改用台账结构，盈亏/市场/发现扩大有效阅读宽度；OCR 导入加入“截图进入→校对数据→确认写入”进度轨道与完成反馈，发现基金和日报分别加入三阶段扫描轨道与六阶段决策轨道。UI E2E 改为 production export 静态预览，新增 1280×800、430×932 两个项目并覆盖完整七视口，增加移动 CTA 时机与落地/认证视觉回归截图；本地 Lighthouse 落地页/登录页均为 Performance 98、Accessibility 100、Best Practices 100，LCP 分别约 2.21s/2.34s，CLS 约 0。内部 `fundpilot-*` 存储键、API/认证/SSE/风控与收益口径未改动。
- **投研日报 action-first 阅读流与诊断修复（2026-07-11）：** 日报完成态重构为单列「结论摘要 → 需要处理 / 继续观察 → 更多内容与工具」，默认只展开存在明确仓位动作的基金；单卡再分「动作主因 / 为什么这样建议 / 专业依据」三层，空新闻占位和重复证据不再展示，历史字符串日报仍可解析。生成设置在已有日报时默认压缩为一行摘要，支持「调整设置 / 收起设置 / 重新生成」，高级设置里的「偏好定投」恢复明确可点击标签。原常驻追问列替换为桌面右侧、移动端底部的按需对话抽屉，具备 modal 语义、焦点循环/恢复、Escape/遮罩关闭和滚动锁；主题要闻、板块轮动、调仓模拟、建议复盘/投研诊断收进懒挂载工具中心，关闭时不触发诊断请求，旧 `DiagnosticsAccordion` 删除。后端 `_parse_return_frame` 改按累计收益百分比构造正 growth index 后计算区间收益/最大回撤，最终两项必须同时有限且分别位于 `[-100,1000]` / `[-100,0]`，否则整组丢弃；基金诊断缓存升级 `fund:diagnostics:v2:*`，并补齐 `opportunity absent/present`、`daily_return(_percent) pending`、`momentum` 的人话化。Chrome QA 另修复历史报告滚动锚点被 66px 粘性顶栏遮挡、摘要操作仅 40px、展开设置后无法主动收起三处体验问题；最终整包审查继续修复跨日报同基金复用卡片/极端动作确认状态，以及关闭追问抽屉未中止 SSE、重开后可能并发追问的问题，并统一抽屉关键触控区到 44px。**本地验证：** Web 42 files / **196 tests**，typecheck、lint（0 warning）、Next production build 全通过；API **675 passed**（仅 9 条既有 Starlette TestClient 弃用警告）；Playwright API E2E **3/3 passed**。七基金真实页面在 Chrome 默认桌面（2040×974）、768×1024、390×844 验收均无横向溢出，控制台 0 error/warning；默认页高桌面 **1740px**（对比 6330，减少 72.5%）、手机 **2212px**（对比 9154，减少 75.8%），移动端日报主操作均 ≥44px，聊天触发器不压底栏，抽屉关闭后滚动位置原值恢复。
- **因子 IC 周度自动刷新（2026-07-10）：** GitHub Actions `Factor IC Refresh` 每周日北京时间 03:23（也支持手动触发）在生产容器外运行固定口径 `sampled 500→300` 回测，不占用 CloudBase API 实例 CPU/线程；runner 输出 schema v1 与 UTC `generated_at`，发布 CLI 和 API 共用质量门槛（`available=true`、有效基金 ≥240、总期数及四因子有效期均 ≥12、统计量有限；不显著结论仍可发布）。`POST /api/internal/factor-ic-snapshots` 只豁免普通 JWT，另用独立 `X-Factor-IC-Publish-Token` 常量时间鉴权；生产配置 MySQL 时拒绝回落 SQLite 写入。SQLite v9/MySQL 新表 `factor_ic_snapshots` 追加保存源码提交、Actions run id 和完整 payload；SQLite `BEGIN IMMEDIATE`、MySQL `FOR UPDATE` 串行化最新快照判断，同 payload 幂等、旧快照 409。`factor_confidence` 改为数据库优先、本地文件兜底、300 秒缓存；损坏兜底文件诚实降级。登录诊断接口 `GET /api/diagnostics/factor-ic-status` 给出来源、样本数和 30 天过期状态，`FactorIcStatusBadge` 显示在“持仓因子体检”标题旁。**本地验证：** 后端 `pytest -n auto --dist loadscope`（本机 auto worker 上限 4）652 passed；前端 142 tests + typecheck + lint 零 warning + build；另用临时 SQLite 完成 500→300、750 日生成→首次/重复/旧版/低质量发布→JWT 状态→置信读取闭环，四因子各 34 个有效期。生产配置与验收见 `docs/deploy/cloudbase.md`，实现设计见 `docs/superpowers/specs/2026-07-04-factor-ic-refresh-automation-design.md`。
- **日报「量化证据缺失」三处根因修复（2026-07-04）：** 排查生产环境（腾讯云 CynosDB MySQL）真实日报发现每只持仓「量化证据」均显示缺失，直接连库读取最新报告 `analysis_facts` 定位到三个独立死因，逐一修复并验证。**① `daily_return_percent` 计算门槛 bug**：`portfolio_persistence.py::persist_holdings_after_sector_refresh` 与 `holding_adjust_service.py::adjust_holding_in_portfolio` 此前用 `total_assets > daily_profit > 0` 做门槛——要求 `daily_profit` 严格大于 0，导致平盘/亏损交易日的 `daily_return_percent` 被永久写成 `None` 而非正确的 0 或负数，拖慢组合日快照凑够 `risk_metrics` 所需 20 个交易日样本的进度；改为对齐 `official_nav_settlement.py::_persist_settlement_holdings` 的正确写法（只要求分母 `previous=total_assets-daily_profit > 0`，不限制 `daily_profit` 符号）。**② 因子分/板块信号回测串行请求超时**：`portfolio_snapshot.py::build_factor_scores_payload` 对不在开放式基金排行榜横截面里的持仓走 `_target_from_nav` 净值兜底（每次独立 AkShare 拉取），`sector_signal_backtest.py::_build_sector_signal_backtest_impl` 逐板块拉日 K 线，两处均用 `for` 循环**串行**执行，喂 LLM 的装配路径分别只给 4/5 秒预算（`analysis_payload.FACTOR_SCORE_TIMEOUT_SECONDS` / `analysis_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS`），持仓/关联板块数量哪怕只有 3~5 个，串行拉取就必然超时——均改为 `ThreadPoolExecutor` 并发（`max_workers=8`，同 `fund_data.py::_map_holdings_concurrently` 模式，单项时走直调避免线程池开销）。**③ Docker 镜像未打包因子 IC 回测数据**：`var/factor_ic/summary.json`（`scripts/run_factor_ic.py` 生成，供 `factor_confidence.py::load_ic_summary()` 读取）此前从未打进生产镜像——`apps/api/Dockerfile` 与根目录 `Dockerfile` 均只 `COPY app`，`.gitignore` 又把整个 `apps/api/var/` 排除，容器里该文件永远不存在，因子分置信度这一路在线上恒为「不足」。修复分两层：`.gitignore` 规则从「整个目录排除」改为「内容排除 + 显式放行 `var/factor_ic/` 及新增的 `.gitkeep` 占位文件」（git 语义：父目录被规则整体匹配排除后子路径否定模式不生效，必须先精确到目录层再放行子路径）；两个 Dockerfile 新增 `COPY .../var/factor_ic .../var/factor_ic`（不是裸 `COPY var /app/var`——`var/` 整体在一次干净 checkout 里连空目录都不存在，裸拷贝会让镜像构建直接失败；`.gitkeep` 保证这一层目录必然存在，`summary.json` 本身仍受忽略、缺失时因子分诚实降级为「不足」而不阻塞构建）。长期待办（该数据本身是 2026-06-24 的静态快照，需要定期重新生成机制）记录于新建 `docs/TODO_factor_ic_refresh.md`（GitHub issue 创建因 token 权限不足未能建立）。**验证方式**：每处修复均用 `git stash`/临时 commit + worktree 模拟「还原前 vs 修复后」对比运行，而非仅静态审查代码；新建 `test_daily_return_percent_gating.py`（5 项）、`test_portfolio_snapshot_factor_concurrency.py`（3 项）、`test_sector_signal_backtest_concurrency.py`（3 项，含并发结果按 label 正确映射防错位）、`test_dockerfile_factor_ic_packaging.py`（7 项，用 `git check-ignore`/`git ls-files` 验证、不依赖本机 Docker）。后端全量 pytest 572 passed（含新增 18 项）。
- **板块资金流"今日"四档结构喂给 LLM（2026-07-04）：** 此前主题板块的机构(超大单)/大单/中单(大户)/小单(散户)四档净流入数据（`flow_tiers`，来自东财 clist 与涨跌幅同一次实时快照拉取）只在市场 Tab 前端展示，未进入喂给 LLM 的 `sector_fund_flow` 上下文（日报 fast 模式与荐基裁剪逻辑均未保留该字段；deep 模式虽保留原始数字但 system prompt 没有解释字段语义，LLM 只能瞪着 `super_large_net_yi` 猜含义）；且机构 vs 散户资金背离的解读（`retail_buy_inst_sell`）此前只嵌在"涨但主力净流出"这一种 pattern 分支里，其余分支即使四档结构同样出现背离也不会提示。**按用户要求的取数原则**——只喂"今日"的资金结构，不喂逐日历史结构，5d/20d 仍只给主力净流入汇总数字：`sector_fund_flow_context.py::_classify_flow_pattern` 新增 `_flow_structure_hint()`，用当日 `super_large_net_yi+large_net_yi`（机构）与 `medium_net_yi+small_net_yi`（大户/散户）的净流入方向对比，生成"机构净流入而散户净流出"等结构化结论句子（`flow_structure_hint` 字段），覆盖全部 pattern 分支而非只有 distribution；同时移除此前存在但未被任何调用方使用的逐日明细数组 `recent_5d_main_force_yi`（保留 `flow_tiers` 只表示"当日"）。`analysis_payload.py`（日报）与 `discovery_sector_context.py`（荐基）的 fast 模式裁剪白名单新增 `flow_tiers`/`flow_structure_hint`；`OUTPUT_REQUIREMENTS_SYSTEM` 与 `discovery_prompt.py::DISCOVERY_FACTS_INSTRUCTION` 补充四档字段中英对照说明（机构/大单/大户/散户），并声明 LLM 应直接引用系统给出的 `flow_structure_hint` 结论、不得自行编造未给出的机构/散户资金动向。单测 `test_sector_fund_flow_context.py`（新增结构解读的正反例 + 无四档数据时返回 None）、`test_analysis_payload_sector_opportunity_trim.py`（新增 fast/deep 裁剪断言）、`test_discovery_sector_context.py`（新建，覆盖 `_slim_sector_fund_flow`）。后端全量 pytest 554 passed。
- **板块资金流"今日"数据滞后误标修复（2026-07-04）：** 修复日报/荐基的板块方向判断（`sector_opportunity_scoring.py`，2026-07-02 引入）在资金流与涨跌幅"日期不对齐"时，仍把滞后的旧资金流数字当作"今日主力净流入"写进 evidence 文案和返回字段的问题——表现为同一张卡片一边显示"资金日期需核验"，一边又言之凿凿给出具体的"今日主力净流入 XX 亿"（实际是几天前的数据）。**根因**：东财历史资金流接口 `fflow/daykline`（`board_fund_flow_history.py`）盘中常滞后一天才落定"今日"这一行，而主题板块榜的涨跌幅走的是另一路实时快照（`fetch_eastmoney_clist_theme_metrics_by_code`），两路数据源不同步产生日期错位；这不是新 bug，`sector_fund_flow_context.py` 早在 2026-06-25 就已能检测出这种错位（`date_aligned`/`flow_date_mismatch`），但 2026-07-02 新增的机会打分器没有遵守这条既有纪律，检测到错位后只追加警示文案、却让旧数字继续参与打分与展示。**修复（数据源修正，非仅报警）**：`sector_fund_flow_context.py` 新增 `_ensure_today_point`——历史资金流序列缺当日行时，从主题板块缓存（`theme_board_snapshot.get_theme_board_snapshot_cache_only`，只读缓存不触发刷新）拼接同源、同日对齐的实时主力净流入值，而不是放任滞后数据被误标为"今日"；`sector_opportunity_scoring.py::_compute_opportunity_row` 同时加固：即使拼接失败仍标记为未对齐，`today_main_force_net_yi`/`cumulative_5d_net_yi` 置空、不参与打分、不出现在 evidence 里（双层防御，即使前置数据修正万一失效也不会展示自相矛盾的数字）。单测 `test_sector_fund_flow_context.py`（新增拼接场景 + 无匹配板块不误拼场景）、`test_sector_opportunity_flow_date_alignment.py`（新增，覆盖打分/evidence/confidence 三方面）。同时排查了官方净值、持有收益、板块涨跌等其余数据流，未发现同类"旧数据贴今日标签"问题。后端全量 pytest 548 passed。
- **官方净值结算持有收益冻结修复（2026-07-03）：** 修复 OCR 上传后 `holding_profit` / `settled_holding_amount` 在后续交易日官方净值公布时不再更新、多日持仓收益无法逐日滚动的根因——同一设计缺陷的五处表现：把 holding 上「当前 profit/收益率」当作可信成本推断依据，或用 amount/profit/return% 数学自洽性做「污染检测」，但在结算重算路径里 profit 已是旧值、自洽判定对任何正常盈利持仓恒为真。**① 跳过信号**：`_should_skip_official_nav_roll` 不再认 `amount_includes_today`（快照原样带入、次日仍为真）与 `_ocr_holding_profit_is_cumulative`（恒等式）；改认 `FundProfile.profit_settled_trade_date == 本交易日` + shares×净值幂等；OCR 带官方日涨跌确认时 `ocr_pipeline` 同步写入该日期。**② 收益重算**：`_profit_patch_from_rolled_settled` 结算时优先 `profile.holding_cost`（保留 `_is_imputed_market_unit_cost` 防档案成本被市值污染）；删除 `profit_is_artifact` / `return_is_polluted` 恒等式分支；兜底反推成本时 `market_amount=None`（旧金额配旧 profit，禁止 new_settled 配旧 profit）。**③ 滚入基线**：`_pre_roll_settled` 删除「settled≈成本×(1+累计%)」与 `settled−profit` 两段猜测分支（对正常盈利数据恒为真，会把昨日市值替换成成本价、逐日复利失效）；档案污染改由 `holding_estimates._repair_corrupted_settled_profit`（`profile.holding_return_percent` 交叉校验）在展示层兜底。新增模型字段 `FundProfile.profit_settled_trade_date`。单测 `test_holding_amount_sync.py`（含多日结算、stale `amount_includes_today` 回归）；后端相关 pytest 全绿。
- **AI 决策"更准更果断"升级 M1~M6（2026-07-02）：** 完整实现 `docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md` 设计方案，日报+荐基共享底层信号与守卫基础设施。**M1 数据/信号层**：新增 `market_breadth_signal.py`（大盘情绪温度计：新高/新低家数近2年历史分布百分位自校准 sentiment_level，涨跌停/炸板当日快照，沪市两融环比，best-effort + 缓存 + stale 回退）；新增 `sector_flow_divergence_backtest.py`（量价背离信号 T→T+1 回测，拆 `flow_price_distribution`/`flow_price_accumulation` 两条规则，复用 `signal_backtest_stats.py` 统计口径）；修复 `sector_opportunity_scoring.py::_confidence()` 机制性封顶——只有量价背离显著（`significant=True` 且 `edge_percent>=10`）时才能真正给到"高"档位。**M2 决策/守卫层**：`decision_guard_shared.py` 新增 `resolve_escalation_floor()`（双向 guard 升级判定核心，5 档触发矩阵）+ `ACTION_BUCKET_*` 扩展为 6 档（新增大幅减仓评估=-1/清仓评估=-2）+ `classify_action_bucket()` 统一分类器（替换 `recommendation_guard.py`/`report_judge.py` 各自维护的重复实现）+ `escalation_severity_rank()`（专供升级比较，独立于封顶逻辑的原始 bucket 数值）；`analysis_facts.py` 的 `allowed_actions` 从静态 5 项改为按 escalation 门槛动态追加后两档；`FundRecommendation`/`DiscoveryRecommendation` 新增 `suggested_position_change_percent`/`suggested_position_change_basis`；`recommendation_guard.py` 接入双向 guard（不仅能降级，也能在证据强烈时把"观察"强制升级为"暂停追涨/减仓评估/大幅减仓评估/清仓评估"）。**M3 生成与复核**：fast 模式确认零新增 LLM 调用（`judge_parsed_report` 短路到纯规则 `_rule_judge`）；deep 模式 `report_judge._llm_judge` 升级为"风控经理二次复核"角色，喂入 `escalation_floors` 作为具体红线，硬约束"最终 action 不得比系统计算的最低档位更宽松"（即使复核失灵，`apply_recommendation_guards` 仍会兜底强制封顶，双层防御）。**M4 荐基同步**：`resolve_discovery_escalation()`（荐基语义：无清仓概念，负向共振剔除候选池 `action=exclude`、正向共振允许突破常规金额上限 `action=boost`，两个方向都要求板块+基金质量分双维度共振）；新增 `discovery_judge.py`（同构 `report_judge.py`，措辞替换为"剔除候选/提高建议金额"）；`discovery_guard.py` 接入。**M5 前端展示**：`SectorOpportunityCard.tsx` 新增"历史回测证据"行（复用 `decisionText.ts::divergenceBacktestLines()`）；新增 `MarketBreadthGauge.tsx`（自包含请求，挂载市场 Tab 主题板块子页 + 生成日报诊断区）；`ReportPanel.tsx` 新增仓位变化徽标 `PositionChangeBadge` + 极端动作二次确认 `ExtremeActionGate`（"大幅减仓评估/清仓评估"点击展开才显示完整依据）；`actionStyles.ts` 新增 `deep_reduce`/`clear_all` 玫红色系 tone + `isExtremeAction()`；`DiscoveryCandidatePoolPanel.tsx` 展示"证据强度剔除"（结构化 `EliminatedCandidate` 模型，非正则解析 caveats）；新增 `GET /api/diagnostics/market-breadth` 端点（复用现有 `/api/diagnostics/*` 前缀而非设计原文的 `/api/admin/*`）。**M6 灰度与复盘**：新增配置 `FUND_AI_DECISION_ESCALATION_MODE=shadow|enforced`（默认 `shadow`）——shadow 模式下三处均"只提示不生效"：① `recommendation_guard.py`/`discovery_guard.py` 规则层不真正改变 action/剔除候选/提额，改写入 `validation_notes`（"【灰度提示，未生效】若启用新版守卫会被系统升级为 XX"）；② `analysis_facts.py` 的 `allowed_actions` 不向 LLM 开放"大幅减仓评估/清仓评估"新词表（`_extra_allowed_actions_for_escalation()` 提取为独立函数）；③ `report_judge.py`/`discovery_judge.py` 的 LLM 复核角色 task prompt 在 shadow 下把"硬约束"措辞降级为"仅供参考"，避免模型自行遵照 escalation 提示把 action 改得比 shadow 允许的更保守（这是设计原文未明确提及、经用户确认后补充的关键点——只挡规则层不够）。新增 `shadow_escalation_digest.py`（M6.3：扫描近 7 天报告的结构化 `holdings[].escalation`/`discovery_facts.escalation_hints` 字段聚合触发次数/涉及板块/建议动作/当日走势对照，非正则解析文本）+ `GET /api/diagnostics/shadow-escalation-digest` + 前端 `ShadowEscalationDigestCard.tsx`（仅 shadow 模式下渲染）。测试：后端新增约 90 项单测覆盖 M1~M6 全部规则分支与灰度双模式（`test_market_breadth_signal.py`、`test_sector_flow_divergence_backtest.py`、`test_sector_opportunity_confidence_upgrade.py`、`test_decision_guard_shared.py`、`test_recommendation_guard_evidence.py`、`test_report_judge_facts_reuse.py`、`test_discovery_guard_escalation.py`、`test_discovery_judge.py`、`test_decision_escalation_mode.py`、`test_shadow_escalation_digest.py` 等），后端全量 pytest **539 passed**；前端新增/更新 `MarketBreadthGauge.tsx`/`ShadowEscalationDigestCard.tsx`/`ExtremeActionGate` 等组件，vitest 137 passed、`tsc --noEmit`/`eslint --max-warnings=0`/`next build` 均通过。
- **项目瘦身与文档归一化（2026-07-02）：** 删除临时 `debug_probe` 调试探针及热路径 info 日志；Web 侧移除未使用的 `isAuthenticated` / `fetchSectorLabels` helper。小程序功能暂时下线，删除 `apps/miniprogram/` 与 `.kiro/specs/miniprogram-web-parity/`，同步移除后端微信/CloudBase 登录兼容接口，Web 账号设置页改为只读账号信息，文档改为 Web/API 私有部署口径。文档侧再次清理 `docs/superpowers/` 历史过程稿，当前权威资料收敛为本文、`docs/design/` 的运维/契约文档、`docs/deploy/` 与 `docs/SECURITY.md`；旧 spec/plan 的已落地结论保留在本文更新记录与对应代码测试中。
- **日报（report）对齐荐基（discovery）决策能力（2026-07-01）：** 全面提升日报 LLM 决策的可追溯性与准确性，后端优先、Web 前端同步展示，移动端当时未同步。**共享基础设施**：把荐基 `discovery_sector_opportunity.py` 里双轨（momentum 顺势/setup 蓄势）板块打分核心逻辑抽成 `sector_opportunity_scoring.py`（`select_sector_opportunities` / `describe_sector_opportunity`），`discovery_sector_opportunity.py` 改为薄re-export层；`discovery_guard.py` 的人话化/置信度归一化/去重等公共逻辑抽成 `decision_guard_shared.py`，两处 guard 共用同一套标准。**数据增强**：新增 `report_sector_opportunity.py`，用 `describe_sector_opportunity` 给每个持仓板块一个方向判断（即使暂不构成机会也返回 `opportunity_available=False`），并给出全市场机会分最高、未持有的方向作为轮动参考；接入 `analysis_facts.py`（best-effort、超时降级、纳入 budgeted 并行 enhancement，`ThreadPoolExecutor` `max_workers` 5→6）与 `analysis_payload.py` 的 trim 规则；facts 新增每持仓 `sector_opportunity` 与顶层 `sector_rotation.market_top`。**结构化输出**：`FundRecommendation` 模型新增 `confidence` / `hold_horizon` / `risks` / `decision_path` / `sector_evidence` / `fund_evidence` / `validation_notes`（均带默认值，向后兼容旧报告/离线路径）；`DEFAULT_ROLE_PROMPT` 与 `OUTPUT_REQUIREMENTS_SYSTEM/USER` 要求 LLM 按「先判断板块方向 → 再看基金自身证据 → 最后给出动作」输出并附证据链；`parse_fund_recommendations_raw` / `merge_fund_recommendations` 同步解析与合并新字段。**Guard 升级**：`recommendation_guard.py` 新增弱证据降级（板块方向不构成机会 + 基金综合置信不足时，「分批加仓」自动降级为「观察」/「减仓评估」）、结构化字段自动回填（LLM 未给出时用板块/基金证据反推）、`decision_path` 与最终动作同步、全字段人话化输出；`deepseek_client._finalize_recommendations` 透传 `analysis_bundle.facts` 供 guard 使用。**导出/追问同步**：`report_export.py` markdown 新增置信度/持有窗口/决策路径/板块依据/基金依据/校验备注/风险渲染（有则显示、无则跳过，修复了同名变量遮蔽外层 `risk` 字典导致的 `AttributeError`）；`report_chat.py` 系统提示词补充新字段解读说明，避免追问时夸大置信度。**Web 前端**：`ReportPanel` 基金建议卡新增置信度徽标、持有/观察窗口、板块方向提示、决策路径说明框、板块/基金依据+校验备注三栏证据网格、结构化风险提示；新增可折叠「板块轮动参考」区块展示 `sector_rotation.market_top`；把荐基 `DiscoveryReportPanel` 里的证据网格、板块方向卡片、文案人话化函数抽成共享组件/工具（`DecisionEvidenceGrid.tsx`、`SectorOpportunityCard.tsx`、`lib/decisionText.ts`），两处报告的展示风格保持一致。同时修复 `db_migrations.py` 的 SQLite 迁移并发竞态（`threading.Lock` 序列化）。测试：新增 `test_decision_guard_shared.py` / `test_report_sector_opportunity.py` / `test_analysis_payload_sector_opportunity_trim.py` / `test_recommendations_structured_fields.py` / `test_recommendation_guard_evidence.py` / `test_report_export_structured_fields.py`，Web 端 `ReportPanel.test.tsx` / `DiscoveryReportPanel.test.tsx` 补充结构化字段渲染用例；后端全量 pytest 414 passed，前端相关 vitest 与 `tsc --noEmit` 通过；并做了端到端手工验证（模拟 LLM 给出「分批加仓」，因板块资金面弱被正确降级为「减仓评估」且证据链完整回填）。
- **支付宝口径对齐 + 详情导航修复（2026-06-30）：** 修复 OCR 确认后持仓成本/持有收益/当日收益与支付宝不一致、电网基金持有收益被档案污染覆盖（-607 vs +142）、以及基金详情「下一只」循环切换后某只基金从列表消失。**后端**：`holding_amount_sync` 支付宝成本/收益语义、`holding_cost` bootstrap、官方净值滚结算时 `_profit_patch_from_rolled_settled`；`holding_estimates._ocr_holding_profit_is_cumulative` + `_repair_corrupted_settled_profit` 跳过 OCR 累计持有收益；`ocr_pipeline` OCR 带官方日涨跌时跳过 `prime_official_nav_cache` 加速确认；`alipay_holdings_parser` / `portfolio_holdings_service` 补强。**前端**：`patchHoldingRecord` 按 code 原位 hydrate（**不在 hydrate 时 dedupe**）；`navigableHoldings` + 循环 `onNavigate(identity)`；预取 `holdingsPrefetchKey` 防抖；`withApplyDisplayFields` 保留 OCR 持有收益。单测 `test_holding_amount_sync.py` / `test_alipay_daily_semantics.py` / `test_apply_holdings_fast_path.py` / `holdingMetrics.test.ts` / `YangjibaoFundDetail.navigate.test.tsx`。契约见 `docs/design/holding-metrics-contract.md`。
- **荐基 20 日位置上下文暂停接入（2026-06-30）：** 实测板块日 K 链路在 77 个主题上覆盖不稳定且单次可能耗时 7-20s，容易拖垮荐基 AI 分析前的准备阶段；因此 `discovery_pipeline` / `discovery_streaming` 不再拉取 `build_sector_position_map_for_opportunities`，`sector_opportunities` 不再接收或输出 `position_context`，LLM payload 也不再要求引用 `position_label` / 20 日回撤 / 量比。`discovery_sector_position.py` 与日 K 解析能力暂保留为未来稳定接口或后台预热缓存的备用模块。另修复 `discovery_streaming` 候选池线程未透传 `request_context` 导致真实烟测报「未设置当前用户上下文」的问题。新增回归 `test_discovery_pipeline_opportunity_context.py`，并调整 `test_discovery_streaming.py` / `test_discovery_sector_opportunity.py` / `test_discovery_payload.py`。
- **荐基 LLM 决策输出结构化（2026-06-30）：** P0 后端改造完成，移动端当时未同步。`DiscoveryRecommendation` 新增 `decision_path` / `sector_evidence` / `fund_evidence` / `validation_notes`；荐基 prompt 要求 LLM 按「先判断板块方向 → 再比较方向内基金质量 → 最后决定动作」输出，优先引用 `sector_opportunities` 与候选池 `fund_quality_score` / `sector_fit_score` / `quality_reasons`。`discovery_guard` 会按候选池校正基金名称/板块，并在 LLM 未写结构化依据时用板块机会与候选基金质量字段补齐；动作被追高规则改写后，补齐的 `decision_path` 使用最终动作。Markdown 导出同步包含结构化依据，供荐基追问复用。单测 `test_discovery_decision_output.py`。
- **荐基 guard P0.5 强化（2026-06-30）：** 在 P1 展示前先把后端荐基结果审校补强。`discovery_guard` 新增 action/confidence 标准化（仅保留 `建议关注` / `分批买入` / `等待回调` 与 `高/中/低`）、低置信方向/资金弱信号/低 `fund_quality_score` / 低 `sector_fit_score` 的 `分批买入` 降级为 `建议关注`、总 `suggested_amount_yuan` 不超过本次预算、以及 LLM 已写 `decision_path` 与最终动作冲突时同步修正。校正/降级原因会写入 `points` / `caveats` / `validation_notes`，避免 P1 前端放大未经审校的 LLM 文案。单测继续覆盖在 `test_discovery_decision_output.py`。
- **荐基报告 P1 Web 展示（2026-06-30）：** Web 端荐基报告同步结构化解释字段，移动端当时未同步。`FundDiscoveryReport` 类型新增 `discovery_facts.sector_opportunities`，`DiscoveryRecommendation` / `DiscoveryCandidatePoolItem` 类型补 `decision_path`、`sector_evidence`、`fund_evidence`、`validation_notes`、`fund_quality_score`、`sector_fit_score`、`quality_reasons`、`quality_penalties`。`DiscoveryReportPanel` 顶部新增「本次主方向」模块（机会分、track、置信、1d/5d、今日/5日主力、pattern/entry_hint），推荐卡展示「决策路径 / 板块依据 / 基金依据 / 校验备注」，`DiscoveryCandidatePoolPanel` 展开表格展示质量分、匹配分、质量理由和短板。测试 `DiscoveryReportPanel.test.tsx`；前端 `typecheck` 与荐基相关 vitest 通过。
- **荐基深度模式流式卡住修复 + 报告人话化（2026-06-30）：** 根因是 `discovery_streaming` 在板块资金流、候选池、新闻预取结果等待，以及深度模式 `run_discovery_news_tool_rounds` 非流式 HTTP 期间没有持续 SSE 事件；前端 `discoveryStreamApi` 120s idle watchdog 会判定「long time without progress」并回退后台任务，后台其实仍会完成。新增 `PREP_HEARTBEAT_SECONDS` 与 `_await_future_with_progress()`，慢步骤等待期间每秒发送 stage heartbeat；荐基 deep P0 后不再让 LLM 触发新闻工具轮，统一使用系统预取的 `news_titles` / `topic_briefs`，并在 prompt 中约束过旧/为空新闻不能作为买入主依据。报告 P0/P1：prompt 要求面向用户使用中文标签；`discovery_guard` 在 backfill 与出口清洗 `fund_quality_score`、`sector_fit_score`、`quality_penalties`、`sector_opportunities`、`nav_trend`、`max_drawdown_1y_percent`、`estimated_daily_return_percent` 等内部字段；Web 报告展示兜底翻译旧报告残留字段，并给报告卡片/候选池长文本加 `overflow-wrap:anywhere` 断行约束。回归：`test_stream_discovery_emits_heartbeat_while_waiting_for_slow_candidates`、`test_stream_discovery_deep_uses_prefetched_news_without_tool_rounds`、`test_report_parser_preserves_structured_decision_fields`、`DiscoveryReportPanel.test.tsx`；真实 deep smoke 从 `deep-heartbeat-fix` 的约 245.7s 降到 `deep-prefetch-news` 的约 153.0s。
- **荐基方向内基金质量分（2026-06-30）：** `build_candidate_pool` 不再让板块反查基金按 DB 顺序直接占满每方向名额，而是将 `fund_primary_sectors_global` / 用户主关联结果与排行榜名称匹配结果合并，统一计算 `fund_quality_score`（板块匹配、3/6 月表现、1 年追高惩罚、回撤、规模、类型偏好、信息缺失惩罚）与 `sector_fit_score`，同基金家族保留最高分/最符合偏好的一只；`no_c_class` / `etf_link` 偏好也覆盖主关联板块结果。全市场候选池上限调整为 28，基础每方向 3 只，机会分 ≥70 且排名靠前的强方向可额外给第 4 只。LLM payload 透传 `fund_quality_score` / `quality_reasons` / `quality_penalties`。单测 `test_discovery_candidate_pool_opportunity.py` / `test_discovery_payload.py`。
- **荐基双轨候选池（2026-06-29）：** 荐基先用主题 1d/5d 与板块主力资金流合成 `sector_opportunities`，按「顺势机会 momentum」与「蓄势观察 setup」双轨均衡选 6~8 个方向；「回调承接」暂作为 `entry_hint` 而非独立取板块轨道。候选池优先按 `fund_primary_sectors_global` / 用户主关联板块反查基金，叠加家族去重、已持有过滤、类型偏好，再交给 LLM 精选。慢 `signal_backtest`、目标板块增强上下文与市场资金流预算化，超时降级继续，避免卡在 AI 分析前上下文阶段。单测 `test_discovery_sector_opportunity.py` / `test_discovery_candidate_pool_opportunity.py` / `test_discovery_payload.py` / `test_discovery_streaming.py`。
- **市场共享缓存跨进程 stale（2026-06-29）：** 修复 API 进程重启后主题板块/大跌雷达首请求同步打网超时，以及东财不可达时用空 snapshot 覆盖有效磁盘缓存。① **跨进程 stale**：`get_theme_board_snapshot` / `get_dip_radar_snapshot` 对本进程启动前刷新的缓存标 `stale=true` 直接返回，不再同步 `refresh_*` 阻塞请求。② **空写入保护**：`refresh_theme_board_snapshot` 仅当至少一个板块有 live 指标（1d/5d/主力/四档流）才 `save_spot_snapshot`。③ **后台刷新**：`market_shared_refresh_loop` 不再在循环入口预置 `_last_*_refresh_at=now`，首周期可立即尝试刷新。④ **theme-boards 读持仓**：`GET /api/market/theme-boards` 高亮持仓时 `load_persisted_holdings(fetch_benchmark=False)`，避免打开市场 Tab 触发 benchmark 子进程。单测 `test_market_shared_cache.py`。
- **OCR/板块刷新 holdings 缓存同步（2026-06-29）：** `POST /api/portfolio/apply-holdings` 与 `POST /api/holdings/refresh-sector-quotes` 成功后 `save_cached_holdings_response`；fast 持久化 `sync_holding_amounts_from_shares(estimate_quotes={}, allow_nav_fetch=False)`；OCR apply `skip_network=True`；Dashboard 确认后乐观写 localStorage、不再立即 `hydratePortfolio` 覆盖 stale 行情。单测 `test_apply_holdings_fast_path` / `test_portfolio_sector_refresh` / `Dashboard.applyRefresh.test.ts`。
- **大跌雷达关联板块（2026-06-28）：** 修复雷达榜单基金几乎全显示「综合」。`dip_drop_scanner.build_dip_radar_pool_fast` 改批量调用 `resolve_sector_labels_for_radar`：用户 `fund_primary_sectors` → TTL 内 `fund_primary_sectors_global` → `resolve_primary_sector`（默认不拉网基准）→ discovery 名称关键词；缓存 key 升 **`dip:radar:v2`**。单测 `test_dip_radar_sector.py`。
- **中基协 155 指数要素库（2026-06-28）：** 接入 AMAC 业绩比较基准要素库（154 条，API 当前计数）→ 静态 JSON `app/data/amac_benchmark_index_library.json`；`scripts/sync_amac_benchmark_index_library.py` 从中基协 API + 东财 clist + 手工映射解析指数代码；`amac_benchmark_index_data.py` 合并进 `THEME_BOARD_INDEX` 与 `fund_benchmark_sector` 名称→代码表。单测 `test_amac_benchmark_index.py`。
- **全市场关联板块离线预计算（2026-06-28）：** 新表 `fund_primary_sectors_global`（schema v8）；`fund_primary_sector_precompute.py` + 后台线程（12h/批 150）+ CLI `scripts/precompute_fund_primary_sectors.py`；`resolve_primary_sector` 读 global 缓存，benchmark/holdings 成功写回 promote。env `FUND_AI_FUND_PRIMARY_SECTOR_*`。单测 `test_fund_primary_sector_global.py`。
- **关联板块自动匹配重构（2026-06-28）：** 移除 `GLOBAL_FUND_SECTOR_SEEDS` 与名称子串主路径；新增 `fund_industry_theme_map` / `fund_holdings_sector_infer`；`resolve_primary_sector` 优先级：高信任 OCR/手动 → 业绩基准（含 AMAC 155 库）→ 重仓穿透 → global/档案 → 可选 `name_infer`（默认关）。快路径 apply/refresh 仍 `fetch_benchmark=False`。单测 `test_fund_sector_auto_match.py` 等。
- **CloudBase 线上稳定性 + 持仓读路径瘦身（2026-06-27）：** 修复 Web 部署后 CORS/504/「持仓加载超时」与荐基/日报 SSE 并发问题。① **CORS**：`FUND_AI_CORS_ORIGINS` + 设 `FUND_AI_CLOUDBASE_ENV_ID` 后自动放行 `*.webapps.tcloudbase.com`（`config.resolved_cors_origin_regex`）；504 无 CORS 头时浏览器误报跨域，见 `docs/deploy/cloudbase.md` §7。② **SSE 不阻塞 worker**：日报/荐基仍走 SSE；`async_sse.sse_from_sync_iterator` + async 流式端点，重计算在后台线程，避免长连接占满 uvicorn worker。Dockerfile **`--workers 2`**；单副本 2 核仍建议在荐基/日报并发时将 CloudBase **实例副本数 ≥2**。③ **GET /api/portfolio/holdings 快路径**：内存缓存命中直接返回；未命中走 **`build_fast_snapshot_holdings_response()`**（只读最近日快照 + 官方净值内存缓存 overlay，**不** triple `resolve_holdings` / 不 `apply_server_sector_cache` 打网）；25s `asyncio.wait_for` 超时返回 503「持仓加载超时」。④ **前端**：AI 日报/荐基/异步 job 进行中跳过 holdings 后台轮询；`settleOfficialNav` / 详情预取用 `mergeHoldingsPreserveQuoteFields`；`mergeSectorIntradayClose` 仅更新板块列、不覆盖官方净值当日收益。⑤ **口径**：`sector_return_percent` 仅 `realtime`/`closing_estimate` 可信；快照里仅有 `official_nav` 的 `sector_return_percent` 视为脏数据不展示；`refresh_holdings_sector_quotes` fast 路径官方 NAV 优先。⑥ **荐基轮询**：MySQL 短暂不可用时 `GET /api/jobs/{id}` 返回 `transient_unavailable` + `status=running`，前端自动重试。单测 `test_async_sse.py` / `test_portfolio_holdings_service.py::test_fast_snapshot_*` / `test_job_status_service.py` / `holdingMetrics.test.ts`。
- **持仓首次加载提速 + 官方净值结算补刷（2026-06-27）：** 修复持仓页首次启动/后台缓存刷新被基金业绩基准 AkShare 子进程拖慢，以及周末/次日官方净值已公布但持仓仍停留板块估算的问题。① **板块快路径**：`refresh_holdings_sector_quotes(cache_only=True | timeout_seconds=8.0)`、`GET /api/portfolio/holdings`、`load_persisted_holdings(fetch_benchmark=False)`、`apply_confirmed_holdings`、后台 `refresh_portfolio_sectors_for_user` 均禁止为缺失 benchmark 触发 `fetch_fund_benchmark_text`；已缓存 `benchmark_index` 仍应用，手动 accurate/`timeout_seconds=None` 仍可补全；失败 benchmark 有 24h miss cache。② **官方净值结算**：新增 `official_nav_settlement.py` 与 `POST /api/portfolio/settle-official-nav`，非盘中按 `build_trading_session().effective_trade_date` 结算上个有效交易日官方净值，写回 `daily_return_percent` / `daily_profit` / `daily_return_percent_source=official_nav`、summary 与快照；盘中/收盘前跳过，defer 持仓先跳过官方净值查询；持久化合并保留 official_nav 字段。性能关键：`fund_open_fund_daily_em` 全量净值表一次预热本次持仓的官方涨跌幅/单位净值缓存，结算 endpoint 走轻量快照写回，不再逐只基金拉净值历史或复用板块刷新重型持久化链路。③ **前端无感补刷**：`Dashboard.hydratePortfolio` 先快速展示缓存/快照，再后台调用 `settleOfficialNav()`，成功且未 skipped 时回写持仓、summary、refreshed_at 和 localStorage。单测覆盖 `test_holdings_fast_sector_resolution.py`、`test_official_nav_settlement.py`、前端 `api.settlement.test.ts`。
- **OCR 确认秒回 + 盘中结算额锁定（2026-06-26）：** 修复 OCR 确认后「正在更新…」久等、盘中持有金额漂移、板块估算与「已更新」标签错误。**① 确认写入提速**：`apply_confirmed_holdings` 改 `bootstrap_holding_baselines(skip_network=True)`（不拉天天基金估值/AkShare 净值子进程），同请求内 `refresh_holdings_sector_quotes(cache_only=True)` 读 `sector_spot_cache` 即时补全板块涨跌与当日估算；前端 `handleConfirmOcrHoldings` 立即关弹窗切持仓 Tab，后台 `apply-holdings` + `refresh-sector-quotes`。**② 盘中结算额**：`settled_holding_amount` 为持有金额展示源；仅官方净值公布后才滚入 `shares×净值`；`holding_client` 下发 `display_holding_amount`；禁止用 `profile.holding_amount` 作盘中 fallback。**③ 估算口径**：`estimated_daily_return_percent` 盘中仅用 `sector_return_percent`（不加 settled 收益率）；板块刷新清空 `daily_*` 时同步清 `official_nav` 残留；盘中 `overlay_official_nav_returns` 短路。**④ 支付宝语义**：「日收益」→ `yesterday_profit`（昨官方净值收益），非 `daily_profit`。单测 `test_apply_holdings_fast_path.py` / `test_holding_amount_sync.py` / `test_sector_refresh_daily_clear.py` / `test_alipay_daily_semantics.py`。契约见 `docs/design/holding-metrics-contract.md`。
- **支付宝 OCR 确认无感知刷新（2026-06-26）：** 修复确认截图后列表估算/持有/板块列闪「—」。**前端**：`mergeHoldingsPreserveQuoteFields`（`holdingMetrics.ts`）在 OCR 确认、apply 回写、板块刷新时保留上一屏行情字段，直至新值返回。**单测** `holdingMetrics.test.ts`。
- **业绩基准 / defer / 查码二次补强（2026-06-26）：** ① **查码**：`normalize_fund_name_for_lookup` 将「半导体材料设备」→「半导体设备」，修复支付宝名「天弘半导体材料设备指数C」自动匹配 021533。② **板块**：仅 `ocr_detail`/`manual` 可挡业绩基准；`resolve_holding` 优先 `benchmark_index` 并回写档案；`sector_quote_lookup_label` 走 canonical 指数（931743 非 BK1036）；AkShare 失败时 `021533` 业绩基准兜底文案。③ **defer 金额**：`return_percent=0` 不再被当作缺失；defer 时清空 `holding_shares`、锁定 OCR 金额不滚 `份额×净值`。单测 `test_sector_quote_label.py` / `test_fund_code_resolver_index.py` / `test_profit_accrual_defer.py`（154 API passed）。
- **基金业绩基准 → 关联板块（2026-06-26）：** 指数型基金不再靠 per-fund seed 或基金名子串推断板块。新增 `fund_benchmark_sector.py`：AkShare 雪球概况拉「业绩比较基准」→ 解析跟踪指数（如 931743）→ `THEME_BOARD_INDEX` 映射展示名（如「半导体材料」）。`fund_primary_sector_service.resolve_primary_sector` 新增 source=`benchmark_index`（优先级 65），可覆盖 `alipay_overview`/`name_infer`/`seed` 的错误板块；`sector_canonical.get_canonical_sector` 改最长子串匹配优先，新增「半导体材料」→ `2.931743`。Windows 子进程 stdout 用 `ensure_ascii=True` JSON 防乱码。案例：021533 天弘半导体设备指数 C → 半导体材料（非泛化「半导体」BK1036）。单测 `test_fund_benchmark_sector.py`。实现口径以本文为准。
- **当日收益 defer bypass 修复（2026-06-26）：** `profit_accrual_defer` 初版已在 `apply_sector_daily_estimates` 生效，但官方 NAV 公布后 `sector_quote_service`、`holding_amount_sync`、`holding_estimates` 三条路径绕过 defer，导致当日新购仍出现日收益（如 6/25 买入 3000 元、净值更新后显示 +79）。现三处均先检查 `is_profit_accrual_deferred`；前端 `holdingMetrics.ts`/`holdingDisplay.ts` 防御性强制日收益 0。单测 `test_profit_accrual_defer.py` + `holdingMetrics.test.ts`。实现口径以本文为准。
- **荐基 LLM 数据包对齐日报（2026-06-25，2026-07 更新资金口径）：** `discovery_payload.build_user_payload` 新增顶层 `news_titles` / `topic_briefs`（复用 `analysis_payload.compact_*`）；`discovery_facts` 透传 `session`、`target_sector_context`（板块热度+主力+分时+信号，见 `discovery_sector_context.py`）、`stock_connect_flow`（仅南向数值，北向为不可用审计状态）、`candidate_factor_scores`、`selection_strategy`、`dip_swing`、`instruction`；requirements 要求按证据时点引用南向与 `news.freshness_label`。单测 `test_discovery_payload.py`。
- **日报/推荐报告管线提速 · 阶段 4（2026-06-25）：** LLM **流式输出** + 前端骨架/浮层。**后端**：`analyze_streaming.py` / `discovery_streaming.py` SSE（`stage` / `token` / `recommendation` partial / `done`）；`deepseek_streaming.py` + `streaming_json_parser.py`；深度模式同步新闻 tool 轮后流式 JSON；`stream_session_store.py` + `POST /api/analyze/stream/{id}/followup`（生成前追加 `operator_notes`，仅日报）。**前端**：`streamApi.ts` / `discoveryStreamApi.ts` token 打字机；`ReportThinkingSidebar` / `DiscoverySkeleton`；`StreamingAnalysisFloat` / `DiscoveryStreamingFloat`（切 Tab 不丢进度 + 完成通知）；荐基 fast/deep 均走流式。烟测 `smoke_run_analysis.py --stream`、`smoke_run_discovery.py`。实现口径以本文为准。未做：生成中 follow-up、tool_calls delta 流、荐基 operator_notes。
- **日报/推荐报告管线提速 · 阶段 1（2026-06-25）：** 四项后端数据/缓存优化落地。**F2** `discovery_candidate_pool.build_candidate_pool` / `dip_drop_scanner.build_dip_pool_for_sectors` 默认 fetcher 改 lookup 模式接 `fund_rank_cache.fetch_open_fund_rank_cached`（与因子分模块共享 1h 缓存）；热路径省 1~3s。**F3** `NewsService.prefetch_topics` 由串行 for-loop 改 `ThreadPoolExecutor.map`（max=5），冷态 5 主题并发省 2~5s；日报+荐基双路径受益。**F1** `judge_parsed_report` 增加 `facts: dict` 必填 kwarg，`_rule_judge` / `_llm_judge` 不再各自重算 `build_analysis_facts`（深度 -5~10s、快速 -1~3s）；副效应：judge 看到的字段集合==prompt 看到的，事实一致性提升。**F4** `nav_cache_pull_days=252` / `nav_trend_window=66`，`summarize_nav_history` 加 `window_days` 参数——拉满 252 让日报/荐基与持仓详情弹窗预热共享 `fund_nav_cache`，摘要窗口仍 66 保留 LLM 决策口径（period_change / distance_from_high|low 在窗口内；recent_5d / recent_nav_series 始终基于真实尾部）；旧 `nav_trend_days` 转 property 兼容。实现口径以本文为准。阶段 2（LLM 流式）/ 阶段 3（前端骨架卡）独立 spec 排期。
- **DeepSeek 日报数据管线缓存优化（2026-06-25）：** 审计后落地 top3：**①** `fund_diagnostics_cache.py` — 基金概况/1年收益 AkShare 全用户共享缓存（盘中 1h / 收盘 24h）；**②** `fund_rank_cache.py` — 开放式基金排行榜缓存（因子横截面，1h）；**③** `prepare_analysis_bundle` + `finalize_analysis_facts` — `build_analysis_facts` **只算一次**（prompt trim + 存档 overlay pipeline/news），替代原 `build_user_payload` + `_compose_analysis_facts` 双遍。附加：北向资金 `@lru_cache` → `sector_quote_cache`（30min/1h）；板块 intraday 按 label 去重。单测 `test_fund_diagnostics_rank_cache.py` / `test_analysis_payload_bundle.py`。
- **板块资金流日期对齐修复（2026-06-25）：** 修复日报「半导体 +5% 却写主力净流出 216 亿」——根因非符号反了，而是 **6/24 涨跌幅配了 6/23 资金流**。`sector_fund_flow_context.py` 按 `effective_trade_date` 选 flow 点；新增 `trade_date`/`flow_date`/`date_aligned`/`main_force_direction`；`date_aligned=false` 时跳过背离 pattern（`flow_date_mismatch`）。单测 `test_sector_fund_flow_context.py`。
- **持仓详情三层预热 + 基金净值全局缓存（2026-06-25）：** `fund_nav_cache.py`（`fund:nav:v1:{code}:{days}`，15min/1h）；`holding_intraday_warmup` 扩展预热 NAV + 用户详情；前端 `holdingDetailPrefetch.ts` 错峰 prefetch。单测 `test_fund_nav_cache.py` / `holdingDetailPrefetch.test.ts`。
- **市场 Tab 共享快照强化（2026-06-25，2026-06-29 跨进程 stale）：** 主题板块榜、全市场板块资金流、**大跌雷达**、**美股概览**均为**全用户共享**服务端缓存（`sector_quote_cache`）；API 优先读缓存（TTL 过期仍返回 stale，避免每用户打源）。**进程重启后**主题榜/大跌雷达对磁盘上「本进程启动前」的缓存标 `stale=true` 秒回，由后台线程异步刷新，不在首请求同步打网；主题榜 refresh 无 live 指标时不覆盖旧缓存。后台 **`market_shared_refresh.py`** → `market_shared_refresh_loop`（`lifespan` 线程 `market-shared-refresh`）**每 30min** 唤醒检查（循环入口不预置刷新时间戳，首周期可立即刷新），**A 股 / 美股各自独立判定活跃时段**：活跃（A 股 `intraday`/`pre_close` 9:30–15:00；美股 `pre_market`/`regular`/`after_hours`）每 **20min** 刷新；非活跃每 **3h** 静默刷新。env `FUND_AI_THEME_BOARD_REFRESH_INTERVAL_SECONDS=1200`、`FUND_AI_MARKET_SHARED_IDLE_INTERVAL_SECONDS=10800`。前端 `MarketTab` 主题/大跌雷达 staleTime 20min、美股 `usRefreshIntervalMs` 活跃 20min / 休市 3h + SWR。单测 `test_market_shared_cache.py`。
- **持仓详情双层缓存 + 板块分时预热（2026-06-25）：** 修复基金详情弹窗每次打开都重复请求 `detail` / `intraday` / `trading-session`。**前端**：`holdingDetailCache.ts` — 按 `userId+fundCode` 内存缓存详情（5min）；**stale-while-revalidate**（先展示缓存、缓存期内静默后台刷新）；板块分时客户端缓存（盘中 60s / 收盘 15min，静默更新不打双请求）；`trading-session` 5min 复用+静默刷新。**后端**：`holding_detail_cache.py` 按用户+基金+金额指纹内存缓存 `/api/holdings/detail`（默认 300s，随持仓 generation 失效）；`holding_intraday_warmup.py` 在 `GET /api/portfolio/holdings` 与 `refresh-sector-quotes` 后防抖后台预热持仓关联板块分时（走全局 intraday 缓存，非 force_refresh）。env `FUND_AI_HOLDING_DETAIL_CACHE_TTL_SECONDS` / `FUND_AI_HOLDING_INTRADAY_WARMUP_ENABLED`。
- **QDII 币种后缀查码（2026-06-25）：** 修复支付宝 OCR「广发全球精选股票(QDII)C」无法自动匹配东财 **021277**（全称带「人民币」）——根因：查码归一化未剥离 `(QDII)人民币/美元/港币` 与份额字母间的币种词，且 QDII 份额字母正则不识别 `(QDII)C`。**现行为**：`normalize_fund_name_for_lookup` 去币种后缀 + 全角括号；`extract_share_class_letter` / 模糊 token 支持 QDII 括注；OCR 展示名不变。单测 `test_fund_name_utils.py` + `test_fund_code_resolver_index.py::test_lookup_fund_code_qdii_currency_suffix_resolves_rmb_c`。
- **删除持仓彻底清理 + 复活脏数据修复（2026-06-25）：** 修复「删除基金后刷新又回来 / 列表残留 0.00 持仓」——根因①删除只改日快照、`fund_profiles.holding_amount` 仍>0 → `load_persisted_holdings` 的 `profiles_recovered` 用档案把基金捞回；②快照残留行 + 档案停用不同步 → 列表显示 0.00。**现行为**：`DELETE /api/portfolio/holdings/{fund_code}` 从快照移除后 **`_purge_fund_profile`** 删除 `fund_profiles` + 用户级 `fund_primary_sectors`（`delete_fund_profile` / `delete_fund_primary_sector`）；历史**按日日快照**仍保留。加载侧：`without_inactive_holdings` 过滤金额≤0 行；`merge_holdings_with_profiles` 跳过档案已停用/快照零金额孤儿行；`load_persisted_holdings` 自愈写回干净快照。前端 `displayableHoldings` 不展示零金额持仓；确认文案改为「删除档案，重新添加将作为新持仓」。单测 `test_portfolio_holdings_delete.py`（删档案+刷新不复活）。实现口径以本文为准。
- **OCR 预览提速 + 东财查码/搜索索引（2026-06-25）：** 实测完整 `preview` 管线 ~30s 瓶颈在识别后「逐只串行查码 + 双遍档案 enrichment」，非 VLM（~4s）。**Preview 瘦路径**：`/api/ocr?preview=true` 仅 `extract_holdings` + `_resolve_fund_codes`，跳过 `resolve_holdings` / `enrich_holdings_from_profiles`（确认后 `apply-holdings` + `refresh-sector-quotes` 再补全）→ 实测 ~6s。 **东财名称表内存索引** `_FundNameIndex`（`by_code` O(1)、`by_normalized` 精确查码、`postings_by_*_bigram` 子串搜索）；`search_funds_by_keyword`（确认页搜基金）复用索引。`FundProfileService` 请求内 `list_profiles` 缓存。单测 `test_ocr_preview_fast_path.py` / `test_fund_code_resolver_index.py` / `test_fund_profile_cache.py`。
- **截图识别模型切换 qwen-vl-ocr（2026-06-25）：** 把云端识别默认模型从 `qwen3-vl-flash`（即将下线）切到文字识别专用 **`qwen-vl-ocr`** 稳定版（现已等同 `qwen-vl-ocr-2025-11-20`，基于 Qwen3-VL，输入 ¥0.30/输出 ¥0.52 每百万 token，单图<¥0.001）。沿用 **DashScope OpenAI 兼容模式**（调用链不变）。**关键架构决策（实测后定稿）**：qwen-vl-ocr 文字识别强、但**做不了支付宝多列+纵向错位的字段归属推理**——让它直接吐结构化 JSON 会把日收益/持有收益/累计收益串列、把数字当基金名、还把余额宝算进来（不论 prompt 怎么写；实测对比见下）。故 **provider 只让模型做纯文本 OCR（不传 text prompt 用模型默认识别；传「阅读顺序」自定义 prompt 反而触发文字定位/坐标输出），再交给久经测试的本地 `parse_holdings_from_text` 做结构化**——与本地 PaddleOCR 路径**同一解析器**，对称设计。实测：top6 6 只全中(含 3 QDII)、持有收益 +0.01 列对位正确(4.16s)；bottom2 2 只、余额宝/余额/footer 跳过、中航持有收益 -373.30 正确(2.47s)；对照「模型直出 JSON」则把持有收益错成日收益/累计收益、且含余额宝。其它点：① `min_pixels`/`max_pixels` 作为 `image_url` **同级字段**控制缩放与 token 上限（默认 3072 / 8388608）；② **上传前 best-effort 压缩** `compress_image_for_vlm`（Pillow 转 JPEG + 仅最长边>2000 才缩小，异常回退原图），仅减上传体积/延迟、不影响 token（token 只与像素数有关，与文件体积无关），data-URL MIME 随压缩改 `image/jpeg`；③ `extract_holdings_via_vlm` 返回 `(holdings, ocr_text)`，VLM 路径 raw_text 透传给 pipeline（优于旧 JSON 路径的空文本）。改 `vlm_holdings_provider.py`（OCR 文本→本地解析）+ `holdings_extractor.py`（VlmFn 返回 tuple+raw_text）+ `config.py`（默认模型 + 5 新配置）+ 文档；auto/vlm/local 软回退、本地 PaddleOCR、前端全不动。单测 `test_vlm_holdings_provider.py`（OCR 文本→解析 6 只/跳余额宝/列对位、压缩缩放/JPEG/非法图回退/messages 纯 OCR 无 text）+ `test_holdings_extractor.py`（tuple+raw_text）+ `test_config.py`。烟测脚本 `scripts/smoke_vlm_ocr.py`。实现口径以本文为准。
- **截图识别升级 VLM + 本地解析器修复（2026-06-24）：** 解决「新增持有」截图识别**慢/不准/截不全报错**三问题。根因：①本地 PaddleOCR CPU 推理 6~9s（冷加载再+3.8s）超 5s 目标；②QDII 基金名（`(QDII)`/`（QDII）` 在份额字母前）未被 `COMPLETE_FUND_NAME_RE`/`looks_like_fund_product_name` 匹配 → image1 仅识别 3/6 只；③`is_near_zero(None)` 抛 `TypeError` 使 `/api/ocr` 500、生产经 CloudBase 表现为 `failed to fetch`。方案：新增 `HoldingsExtractor` 抽象层（`vlm` 主 + `local` 回退，`auto` 软回退）插入 `run_ocr_upload_pipeline`，主路走云端 `qwen3-vl-flash`（阿里云百炼 DashScope，图片→结构化 JSON）<5s；本地解析器修两 bug 作可靠兜底（`is_near_zero` None 安全 + `parse_holdings_from_text` try/except 永不抛 + QDII 名正则 + 余额宝/法律声明噪声过滤）。新增 `vlm_holdings_provider.py`/`holdings_extractor.py`/`scripts/smoke_vlm_ocr.py` + 5 配置项（`FUND_AI_OCR_PROVIDER`/`FUND_AI_VLM_OCR_*`）。**隐私**：auto/vlm 模式截图发往阿里云百炼，`local` 强制本地不外传（见隐私段）。单测 `test_alipay_holdings_parser.py`/`test_vlm_holdings_provider.py`/`test_holdings_extractor.py`/`test_ocr_pipeline.py`/`test_config.py`。实现口径以本文为准。
- **量化依据进追问上下文（模块4 证据卡延伸，2026-06-24）：** 追问对话上下文用的是 `report_to_markdown(report)`（非 facts），此前不含 evidence。现 `report_export.report_to_markdown` 从 `report.analysis_facts` 取数：① 顶部加「## 组合量化背书」段（`evidence_overview.summary` + backed 占比）；② 「逐基金建议」每条 points 后追加「**量化依据**（综合置信X）：{summary}」（仅该 fund_code 有 evidence 时）。`report_chat._report_chat_system_prompt` 加护栏「量化依据/组合量化背书 是可回测证据，综合置信高可作主理由/中保留/低·不足仅风险提示、不得据此追涨；用户问『为什么这么建议/多大把握』应引用其量化依据」。markdown 导出/下载同步受益。单测 `tests/test_report_export.py`（每基金 evidence 行 / overview 段 / 无 facts 不渲染 / 无 evidence 的基金不出依据行）。后端全量 571 passed。
- **证据进日报正文（模块4 证据卡延伸，2026-06-24）：** 把每只持仓的 `evidence` 综合置信 + 证据摘要展示到日报每条基金建议**下方**（「量化依据」行），让用户直接看到「这条建议有多少可回测背书」。**后端**：日报存档 facts 由 `prepare_analysis_bundle` + `finalize_analysis_facts` 构建（2026-06-25 起替代 `_compose_analysis_facts`），best-effort 计算 `build_factor_scores_for_facts` + `build_risk_metrics_for_facts` 并传入 `build_analysis_facts`，使在线/离线两条报告路径的存档 facts 持仓行均带 `evidence`、顶层带 `evidence_overview`（任一路失败不阻塞）。**前端**：`ReportPanel.FundRecommendationCard` 从 `report.analysis_facts.holdings[].evidence` 取数（`evidenceForFund` 按 fund_code 查），渲染「量化依据」卡（复用 `confidenceTone` 的综合置信 StatusPill + summary）。单测 `test_deepseek_client.py::test_compose_analysis_facts_wires_evidence`（monkeypatch 两 builder→facts 带 evidence/overview）。后端全量 567 passed、前端 vitest 69 passed、tsc 通过。
- **组合证据总览（模块4 证据卡延伸，2026-06-24）：** 把竖切5 每只持仓的 `evidence` 聚合成**组合级背书分布**——「组合多少**市值**有中/高量化背书」——同时进 LLM（facts）+ 前端（懒加载端点+面板）。**纯函数** `signal_synthesis.build_evidence_overview(rows)`：按**持仓市值加权**统计各级（高/中/低/不足）占比 + 计数，`backed_weight_percent`=高+中市值占比，分母为全部持仓市值（含未覆盖→各级之和=覆盖率），无 evidence/零市值→available False。**LLM 注入**：`build_analysis_facts` per-fund 循环后挂 `facts["evidence_overview"]`；`instruction`+角色 prompt 各加护栏「backed_weight_percent 高→建议可更积极，低→强调多数仓位背书不足、风险口径」。**前端端点**：`portfolio_snapshot.build_evidence_overview_payload(holdings)` 精简装配三路（factor_scores 走 TTL 缓存 / risk_metrics 取日快照 / signal 取板块上下文，逐路 best-effort）→ 逐持仓 evidence → 汇总；`GET /api/portfolio/evidence-overview`（懒加载，异常不 500）。**前端面板** `PortfolioEvidenceOverviewPanel`（懒加载，复用 `confidenceTone`）展示 backed 大字+各级市值横条+逐持仓综合置信标签，接入 `PortfolioDashboard` 组合分析页「组合证据总览」可折叠区。单测 `tests/test_signal_synthesis.py`（加权分布/未覆盖计入分母/无evidence→False）+ `test_analysis_facts.py`（facts 含 overview）+ `test_api.py`（端点契约）。后端全量 566 passed、前端 vitest 69 passed。实现口径以本文为准。
- **信号合成 证据卡（模块4 竖切5，2026-06-24）：** 模块4 收尾——把每只持仓的**三路量化置信**（因子IC/板块信号/风险样本）聚合成**一个综合置信 + 一句证据摘要**挂到 facts 持仓行，让「每条建议挂可回测数字」落到持仓粒度。**已与人确认走「证据卡·不决策动作」**：动作仍由 LLM/`tactical_recommendations` 决定，本块只聚合置信，**不**与现有 tactical/`signal_guard_policy` 重叠。**纯函数** `signal_synthesis.py`：`synthesize_confidence(levels)→{level,score}`（高3/中2/低1，不足=无数据不计入；均值≥2.5高/≥1.5中/否则低/全无→不足）；`build_holding_evidence(fund_code,signal_entry,factor_scores,risk_metrics)→{composite,components,summary}|None`，三路分量取法：因子取该持仓百分位最高且 IC 置信非「不足」的主因子(momentum/risk_adjusted/drawdown，size 排除)、信号取该板块 confidence.score 最高的规则、风险用组合层 risk_metrics.confidence；任一路缺失自动跳过、全缺→None。**注入**：`build_analysis_facts` per-fund 循环对每持仓挂 `row["evidence"]`（复用已传入的 factor_scores/risk_metrics + 该行 signal_backtest）；`instruction` + `analysis_prompt.DEFAULT_ROLE_PROMPT` 各加护栏「evidence.composite：高多路背书一致可作主理由/中部分支持/低·不足量化背书弱须风险口径不得追涨」。**本期不做前端**（纯 LLM 侧）。单测 `tests/test_signal_synthesis.py`（合成等级/三路取法/主因子跳过不足/全无→None）+ `test_analysis_facts.py`（facts 挂 evidence）。后端全量 561 passed。实现口径以本文为准。
- **组合风险度量 + 置信 → LLM（模块4 竖切4，2026-06-24）：** 把模块1 组合风险度量（夏普/回撤/Beta/HHI）喂进 LLM，按**样本充足度**挂置信（区别于信号「跑赢基线」、因子「IC 显著」）。**纯函数** `risk_confidence.py::risk_metrics_confidence(metrics)→{level,basis}`：`sample_days≥120`→高、`60~120`→中、`20~60`→低、`<20`或 unavailable→不足（阈值 20/60/120，20 与模块1 `MIN_SAMPLE_DAYS` 对齐）。**装配** `portfolio_snapshot.build_risk_metrics_for_facts(history_rows,holdings)`：调模块1 `build_risk_metrics_payload`（内部取沪深300日线）+ 挂 confidence，best-effort（异常→available=false 不阻塞日报）。**注入**：`build_analysis_facts` 加可选入参 `risk_metrics`→`facts["risk_metrics"]`；`build_user_payload` 把日快照历史 **load 一次**复用给 `build_portfolio_trend_context` + `build_risk_metrics_for_facts`（best-effort，仅 for_llm）；`instruction` + 角色 prompt 各加护栏「风险指标按 confidence.level 表述：高/中可作风险论据，低/不足须声明样本有限不得下强结论」。**不动前端**（模块1 风险面板已展示指标，纯 LLM 侧增强）。单测 `tests/test_risk_confidence.py` + `test_portfolio_snapshot.py`（挂置信/best-effort）+ `test_analysis_facts.py`（facts 注入）。实现口径以本文为准。
- **因子分 + IC 置信 → LLM（模块4 竖切3，2026-06-24）：** 把模块2 因子分喂进 LLM，并用模块3A 的 IC 显著性给每个因子挂可回测背书——LLM 看到「动量分 A」时同时看到「动量因子回测显著正向 IC+0.04（置信高）」或「不显著（仅描述性）」。**纯函数** `factor_confidence.py`：`load_ic_summary()` best-effort 读缓存 `var/factor_ic/summary.json`（缺失→空，TTL 1800s）；`factor_confidence(ic_factors,key)→{level,basis}`（显著且 mean_ic≥0.03→高、显著弱正→中、显著反向/不显著→低、size 或无数据→不足）；`factor_reliability()` 对模块2 四因子各算一次。**装配** `portfolio_snapshot.build_factor_scores_for_facts`：调 `build_factor_scores_payload`（重）+ 挂 `factor_reliability` + 压成紧凑结构（每持仓 composite_grade/score + factor_percentiles），**TTL 1h 缓存（按持仓代码）+ best-effort**（异常→available=false 不阻塞日报；注入 fetcher/ic_factors 时绕过缓存便于测试）。**注入**：`build_analysis_facts` 加可选入参 `factor_scores`→`facts["factor_scores"]`；`analysis_payload.build_user_payload` best-effort 计算并传入（仅 for_llm 路径）；`analysis_facts.instruction` + `analysis_prompt.DEFAULT_ROLE_PROMPT` 各加护栏「按 factor_reliability 用因子分：高可作论据/中保留/低·不足仅描述、不得作买卖主理由；size 未回测仅参考」。**API+前端**：`/api/portfolio/factor-scores` 响应挂 `factor_reliability`；`PortfolioFactorScoresPanel` 每因子加「IC·X」置信小标签（`fundFactors.factorReliabilityTone`）。单测 `tests/test_factor_confidence.py` + `test_portfolio_snapshot.py`（紧凑/best-effort）+ `test_analysis_facts.py`（facts 注入）+ 前端 vitest。实现口径以本文为准。
- **板块信号可信度打分器 + 注入 LLM（模块4 竖切，4A+4B，2026-06-24）：** 路线图模块4「量化结论喂 LLM / 可信度打分器」第一个端到端竖切——让每条板块信号挂一个**可回测的置信分**，DeepSeek 按置信分级表述。**4A 纯函数** `signal_confidence.py::score_signal(bucket)→ConfidenceScore{level,score,basis}`：消费 3B 的 `trigger_count/hit_rate/baseline/edge/significant`，分级 **高(显著且edge≥10)/中(显著且5≤edge<10)/低(n≥30但不显著)/不足(n<30)**；`score=round(50+clamp(edge*2,-50,50)*min(1,n/50))` 落 0~100（仅可视化，分级以 level 为准）；edge 缺失用 `h-b` 兜底、桶空→不足；常量 `MIN_TRIGGERS=30/EDGE_MEDIUM=5/EDGE_HIGH=10` 对齐 3B。**4B 注入**：`sector_signal_context._compact_rules` 每规则加 `confidence` 字段；`analysis_facts.instruction` + `analysis_prompt.DEFAULT_ROLE_PROMPT` 各加一条护栏「高可作主理由/中措辞保留/低不足仅提示，不得主导追涨减仓」（双保险）；前端 `SectorSignalBacktestPanel` 用 `StatusPill` 展示「置信X」标签（`confidenceTone` 映射色，hover 显 basis）。**不改 3B 回测算法**，只做打分表述+喂 LLM。单测 `tests/test_signal_confidence.py`（等级/边界/score范围/兜底）+ `tests/test_sector_signal_context.py`（compact 带 confidence）+ 前端 `SectorSignalBacktestPanel.test.tsx`。实现口径以本文为准。后续竖切按同骨架接入模块1风险度量/模块2因子分/3A IC 显著性。
- **价值/成长风格因子（模块3-3C，2026-06-24，离线工具）：** 给因子库补「风格暴露」——**收益型风格分析**（非持仓穿透）。**纯引擎** `fund_style_regression.py`：把基金日收益对价值/成长指数日收益做**二元 OLS（中心化闭式解 2×2 正规方程）**，输出 `beta_value/beta_growth/style_tilt(=bV-bG)/r_squared/label(偏价值/偏成长/中性)`；样本<60 天或两风格共线(det≈0)→`available=false`；`align_returns` 按公共日期升序对齐三序列。**CLI runner** `scripts/run_style_factor.py`（排行榜池 + 线程池拉 NAV、取价值/成长指数日线→日收益→逐只回归→落盘 `apps/api/var/style_factor/{report.txt,summary.json}`；默认 **国证价值 399371 / 国证成长 399370**，可 `--value-index/--growth-index`）。诚实划界：这是「长得像价值/成长」的**风格暴露**，不是基本面便宜/质量；真·基本面因子需持仓穿透，后续项目。单测 `tests/test_fund_style_regression.py`（植入价值/成长基金→tilt 方向正确 r²≈1、样本不足/共线→unavailable、runner 离线注入）。
- **分层抽样基金池（模块3-3D，2026-06-24）：** 把回测/打分的池从「取榜单前 N 名（偏强样本）」换成「跨业绩段等距分层抽样」。**纯函数** `fund_universe_sampler.py::sample_universe(rows, size)`（step=n/size 等距取样、保序、横跨赢家→输家；n≤size 或 size≤0 原样返回）。接入 3A runner `run_factor_ic.py`：`build_ic_report` 增参 `universe_mode("top"|"sampled")`/`sample_pool_size`，`sampled` 取大池再抽样；`--universe-mode/--sample-pool-size` CLI 选项，summary.params 记录 mode。诚实划界：`fetch_open_fund_rank` 子进程**上限 500 条**且清盘基金不在榜，只削弱**选择**偏差，幸存者偏差仍在；彻底去偏需 point-in-time 基金库。单测 `tests/test_fund_universe_sampler.py` + `test_factor_ic_backtest.py::test_runner_sampled_mode_stratifies_pool`。
- **板块信号回测基线修正（模块3-3B，修 Bug B，2026-06-24）：** `sector_signal_backtest.py` 原把命中率和**固定 50%** 比，对「预测上涨」类信号天然偏乐观。改为**方向感知自然基线**：按桶内实际「涨/跌/平」自然概率算基线（`_direction_fractions/_baseline_prob`），命中率需超基线 `EDGE_MIN_PERCENT(5%)` 且触发数≥`MIN_TRIGGERS_FOR_SIGNIFICANCE(30)` 才算显著（`_finalize_bucket` 产出 `baseline_rate_percent/edge_percent/significant`，`beats_baseline` 兼容别名旧 `beats_random`）；规则方向取自 `sector_signal_rules`。`sector_signal_context` 透传新字段、前端 `SectorSignalBacktestPanel` 展示自然基线+edge+显著性。单测 `tests/test_sector_signal_backtest.py`。
- **因子有效性回测 IC（模块3-3A，2026-06-24，离线工具）：** 回测模块2 的因子到底有没有预测力——在基金池上做 walk-forward Rank IC（信息系数）。**纯引擎** `factor_ic_backtest.py`：手写斯皮尔曼秩相关 `_spearman`（并列均值秩、零方差→None、抹浮点尘）、单期 `_rank_ic_for_period`（横截面<10 只→None）、主函数 `compute_factor_ic`（每 21 交易日取一横截面、前瞻 20 日，**前视偏差铁律**：t 日因子值只用 ≤t 的 NAV、未来收益只用 >t 的 NAV）；每因子输出 mean IC / ICIR / t 统计量 / %>0 / 显著性（n≥12 且 |t|>2）；检验**动量/风险调整/回撤 + 综合**（规模因子排除：历史规模拿不到）；composite 复用模块2 `_factor_stats/_zscore/_composite_z`。**共享 helper** `fund_factor_nav.py`（NAV 切片→因子原始值），模块2 `_target_from_nav` 重构为复用它（消重）。**CLI runner** `scripts/run_factor_ic.py`（排行榜池 + 线程池拉 NAV → 跑引擎 → 落盘 `apps/api/var/factor_ic/{report.txt,summary.json}`，summary.json 给模块4 喂 LLM 用）。**离线工具，无 API、无前端**（IC 偏专业、计算重）。诚实划界：池为排行榜偏强样本，有幸存者/选择偏差，IC 偏乐观，报告显著标注。单测 `tests/test_factor_ic_backtest.py`（**植入真信号→IC≈1**、噪声→不显著、**前视偏差守卫**、hypothesis IC∈[-1,1]）+ `tests/test_fund_factor_nav.py`。价值/质量因子(3C)、Bug B(3B)、全市场池(3D)后续独立成文。实现口径以本文为准。
- **持仓因子体检（模块2 第一期，2026-06-24）：** 给每只持仓在「开放式基金排行榜横截面」里打净值系因子分。① **纯函数引擎** `fund_factors.py`：四因子——动量（0.5×6月+0.3×3月+0.2×1年，权重0.40）、风险调整Calmar（1年收益/|1年回撤|，0.35）、回撤控制（近1年最大回撤，0.15）、规模（log10规模，0.10）；横截面流水线 **去极值(5/95)→z-score(裁剪±3)→按剩余权重归一合成→百分位→等级A/B/C/D**；缺因子不当0、零方差退化为0、池<30→`available=false`。② **装配层** `portfolio_snapshot.build_factor_scores_payload`：`fetch_open_fund_rank(limit=300)` 做横截面池，持仓在榜直接用榜单行、不在榜用净值兜底 `_target_from_nav`（近60/120/250交易日收益 + 复用 `portfolio_risk_metrics._max_drawdown`，规模置None）；`fetch_rank`/`fetch_nav` 可注入便于离线测试。③ **独立懒加载接口** `GET /api/portfolio/factor-scores`（较重，前端展开才请求，不进 dashboard）。④ **前端** `PortfolioFactorScoresPanel`（盈亏分析 Tab，风险体检下方，展开懒加载）+ `lib/fundFactors.ts` 解读话术（vitest）；Pro 门控：免费看综合分+等级+动量，Pro 解锁其余三因子。⑤ 基准池为排行榜偏强样本，话术统一写「可比池」不写「全市场」。**价值因子 / IC 信息系数明确归入模块3**（需额外数据 + 回测框架）。单测 `tests/test_fund_factors.py`（已知答案 + 装配层离线注入 + hypothesis 不变量）。实现口径以本文为准。
- **组合风险度量（模块1，2026-06-24）：** 新增独立服务 `portfolio_risk_metrics.py`（纯 Python 标准库纯函数：波动率、最大回撤、夏普、索提诺、Beta/Alpha、HHI/有效持仓数），与 `risk.py`（阈值告警）职责分离。① **累计收益走复利累乘**（`_equity_curve`，纠正"简单百分比直接相加"的概念 bug；仅风险计算内部用复利，收益走势图/日历展示口径不变）。② **数据零新增**：组合日收益取自 `list_portfolio_daily_snapshots` 的 `daily_return_percent`，基准取已缓存沪深300日线（`fetch_index_daily_history("000300")`），Beta/Alpha 按 `snapshot_date` 逐日对齐取交集。③ **装配层** `portfolio_snapshot.build_risk_metrics_payload` 挂进 `GET /api/portfolio/dashboard` 响应的 `risk_metrics` 字段（前端零新增请求）；样本不足 20 交易日返回 `available=false` + 友好文案。④ **前端** `PortfolioRiskMetricsPanel`（盈亏分析 Tab，收益走势下方）+ `lib/riskMetrics.ts` 解读话术（含 vitest）；「好基灵 Pro」门控：免费显最大回撤+有效持仓数，Pro 解锁其余（`localStorage` 开关，私有部署仅前端门控）。⑤ **配置** `FUND_AI_RISK_FREE_RATE`（默认 0.02，>1 自动归一）。单测 `tests/test_portfolio_risk_metrics.py`（已知答案 + hypothesis 不变量）。实现口径以本文为准。
- **组合风险度量第二批：相关性矩阵（2026-06-24）：** 持仓两两日收益皮尔逊相关性。纯函数 `portfolio_risk_metrics.compute_correlation_matrix`（`_pearson` + 全体公共交易日对齐，量纲无关），装配层 `portfolio_snapshot.build_risk_correlation_payload`（按持仓金额降序取前 15 只，`ThreadPoolExecutor` 并行拉各基金 nav-history 算逐日净值收益，注入 `fetch_nav` 便于离线测试）。**独立懒加载接口** `GET /api/portfolio/risk-correlation?lookback_days=120`（较重，前端展开才请求，不进 dashboard）；对齐后 <20 交易日或 <2 持仓返回 `available=false`。前端 `PortfolioCorrelationHeatmap`（N×N 热力图，红=同向/绿=反向，`max_pair` 给「假分散」话术）接进 `PortfolioRiskMetricsPanel`，Pro 解锁后展开懒加载。单测覆盖完全正/负相关、零方差、样本不足、日期对齐 + 装配层注入。
- **修正 Bug A：百分比收益走复利（2026-06-24）：** 纠正"简单百分比直接相加"的概念 bug——涨 3% 跌 3% 真实是 -0.09% 而非 0%（金额仍可相加，仅百分比改复利）。新增共享 helper `portfolio_profit_analysis._compound_return_percent`，统一应用到：① 收益走势图 `build_daily_trend_series`（组合与指数累计曲线）；② 盈亏日历 `build_calendar_month` 的 `month_cumulative_return_percent` / `month_index_return_percent`（`month_cumulative_profit` 金额仍为求和）；③ 近一周走势 `portfolio_snapshot.build_portfolio_trend_context`；④ 大跌反弹回测 `fund_dip_rebound_backtest`（未来 N 日累计反弹）。展示与计算口径自此统一为复利。
- **持有天数 + 盈亏日历 + 当日收益递延（2026-06-23）：** ① **持有天数**：修复详情页 `ensure_first_seen_anchor` 在读取时把锚点写成「今天」导致天数恒为 0；改由 `save_profile` 的 `reconcile_first_seen_date` 在持久化时一次性写入（购入日 > OCR 天数回推 > `shares_baseline_date` > 今天）；`shares_baseline_date` 早于错误 `first_seen_date` 时自动回退；读取层用 `_first_seen_anchor_date` 即时纠偏，不再写库。② **盈亏日历**：非交易日（周末 + 法定假日，新浪交易日历）收益固定 **0.00**，不沿用上一交易日快照；**今日**格在组合全部持仓切到 `official_nav` 前显示 **「未更新」**（`is_pending_update`），月累计不计入估算；已公布后用实时持仓重算官方收益。③ **当日收益递延**：支付宝 OCR 当日新购（日收益/持有收益/持有收益率均 ≈0）整行 `profit_accrual_deferred`，板块估算跳过直至下一交易日；`profit_accrual_defer.py`。④ **结算金额**：官方净值公布后 `settled_holding_amount` 滚入 `份额×最新净值`；持有金额展示仍为 settled-only（与支付宝列表口径分离）。实现口径以本文为准。
- **主题板块资金流历史 + 日报板块资金流（2026-06-23）：** ① **历史走势**：市场 → 主题板块展开行，四档明细下方懒加载「主力净流入走势」柱状图（近一周 5 日 / 近一月 20 日）；`GET /api/market/board-flow-history`；`board_fund_flow_history.py` 拉东财 `push2his` `fflow/daykline/get`（`secid=90.{BK}`）；host 优先 `80/82.push2his` + `_COMMON_PARAMS` + 4 轮重试/退避；按 BK 缓存（盘中 15min / 收盘 1h），失败回落 stale cache。② **BK 映射**：指数主题涨跌幅与资金流解耦；`theme_board_snapshot._THEME_BOARD_FLOW` 显式覆盖医药/贵金属/化工/交通运输等；`theme-boards` 每项带 `flow_source_code` 供前端/API 复用。③ **预热**：`refresh_theme_board_snapshot()` 写榜后后台线程限流预热历史资金流缓存。④ **日报 AI**：`sector_fund_flow_context.py` 按持仓关联板块注入 `analysis_facts.holdings[].sector_fund_flow`（当日/5d/20d 累计、四档、pattern 标签）；`trim_analysis_facts_for_llm` 保留摘要。前端 `BoardFlowHistoryChart.tsx` + `ThemeSectorOverview` 展开懒加载。实现口径以本文为准。
- **移除简报 Tab + 持仓删除 + Bug 修复（2026-06-22，删除行为 2026-06-25 修订，apply 提速 2026-06-26）：** ① **信息架构**：删除冗余「简报」Tab 及 `TodayBriefing` 等组件；登录默认落地 **「持仓」**；顶/底导航 5 Tab（持仓/分析/市场/发现/日报）。② **线上 504 / 确认久等**：`apply-holdings` 改「快速写入」（仅查码+档案+快照，**2026-06-26** 起同请求 `cache_only` 板块缓存估算，不拉估值/净值子进程）；前端 apply 成功后显式 `refresh-sector-quotes`（fast）后台刷新。③ **无板块新基**：`refresh_holdings_sector_quotes` 在 boards/kline 全空时仍对有 `fund_code` 持仓拉天天基金估值兜底，避免硬失败红条与当日收益 0。④ **支付宝总览 OCR**：持有页判定优先于交易页 marker；扩展 `股票[A-CEH]` 后缀；总览 partial 早退回退切块；fixture `alipay_overview_holdings_5_ocr.txt`。⑤ **删除持仓**：`DELETE /api/portfolio/holdings/{fund_code}`（可选 `fund_name`）；详情页底部「删除该基金」+ 二次确认；**从快照移除并删除 `fund_profiles` + 用户级 `fund_primary_sectors`**（历史日快照保留）。实现口径以本文为准。
- **小程序微信登录关联邮箱账号（2026-06-22，已于 2026-07-02 下线）：** 当时用于修复小程序「微信一键登录」后看不到 Web 端持仓的问题。小程序代码及相关微信/CloudBase 登录接口已在项目瘦身时移除；当前认证主流程仅保留 Web 邮箱注册/登录 + JWT。
- **AI 简报首页 + 移动端导航（2026-06-21）：** 方案 B「蚂小财式」简报首页落地。① **信息架构**：登录默认 Tab 改为 **「简报」**（`TodayBriefing`）；原养基宝持有看板独立为 **「持仓」** Tab（`YangjibaoHoldingsBoard`）；主 Tab 顺序：简报 / 持仓 / 分析 / 市场 / 发现 / 日报。② **简报页**：`todayBriefing.ts` 汇总组合 KPI、板块脉搏、嵌入最新日报决策卡（`BriefingDecisionCards`）、内联 AI 追问（`BriefingChatPanel` / `ReportChatPanel inline`）。③ **导航**：`DashboardNav.tsx` — 桌面 `lg`（≥1024px）顶部 6 Tab；手机/平板仅 **底部固定导航**（简报/持仓/分析/市场/更多→发现/日报/历史）；修复 `.dashboard-bottom-nav { display:flex }` 覆盖 Tailwind `hidden` 导致顶底双栏并存；`.dashboard-shell` 底部留白 `5.75rem + safe-area` 避免市场页「数据日期」脚注被底栏遮挡。④ **大跌雷达 UI**：`DipReboundRadar` 改卡片列表（避免表格列宽截断「深度扫描」）。⑤ **落地页/注册**：转化优化（步骤、人群、sticky CTA）。实现口径以本文为准。
- **本地开发 DB 回落（2026-06-21）：** 云 MySQL（如腾讯 CynosDB 30min 自动暂停）冷启动超时时，API 不再裸 500 触发浏览器 `Failed to fetch`。`db_connect.connect_with_fallback()` — MySQL 连接失败且 `FUND_AI_DB_FALLBACK_SQLITE=true`（默认，`scripts/dev.sh` 导出）时回落 SQLite；`main.py` 全局 `Exception` → JSON 500；CORS 仍最外层。`.env.example` 已文档化。
- **M3 大跌雷达历史命中率 + 主题联动（2026-06-21）：** `fund_dip_rebound_backtest.py` 板块指数代理回测（dip 日 → 未来 3 日累计反弹 ≥ `fee_break_even`）；`dip_radar_snapshot` 每项 `historical_hint`（`sample_count`、`rebound_rate_3d_percent`、`note`）。主题板块行操作「看大跌基金」→ `sessionStorage` `fundpilot-dip-radar-sector` + 子 Tab 切大跌雷达；「加入关注方向」→ `fundpilot-discovery-focus-sectors`（≤3）；`FundDiscoveryPanel` 挂载时读取关注方向预填。
- **好基灵 UI 全面升级（2026-06-21）：** 「静谧蓝海·高级克制」设计语言三轮落地，覆盖全产品前端。① **设计系统**：字体从 Plus Jakarta Sans 换成 **Sora**（`next/font/google` 自托管，构建期拉取，零操作），中文用 PingFang / HarmonyOS / 雅黑 / Noto 系统栈，彻底去除 AI 味；品牌色升级为深海蓝 `#2356e0` + 暖金强调 `#cf9b3e`（仅用于钱/收益/高光），背景 `#f3f6fc`，阴影偏冷蓝更通透；新增 `--brand-deep` / `--muted-soft` / `--shadow-brand` / `--font-display`；`globals.css` 新增 `.eyebrow`、`.trust-strip`、`.stat-value`、`.device-shell`、`.float-badge`、`.plan-card.is-pro`、`.ribbon`、`.reveal`（分级延迟入场，支持 `prefers-reduced-motion`）等工具类；Tab 分段控件选中态改品牌蓝文字+细描边；`.kpi-value` 统一 display 字体；`--background`/`--line`/`.btn-primary/secondary` 阴影/描边全面对齐新 token，清除全局残留旧蓝 `rgba(37,99,235)`。② **落地页重做**（`LandingPage.tsx`）：Hero 改为「左文案 + 右产品预览」两列布局，右侧新增**手机产品预览**（仿真持有首屏：收益大数字 + 上涨火花曲线 + 板块 Mini 卡 + 悬浮徽标），吸引力 > 原纯文字版；文案换成「搭子」人味；新增能力指标条（30s/0手动/每日）和信任条；功能卡片加编号+编号 hover 光晕；新增**「会员方案」展示区**（免费版 vs 好基灵 Pro ¥19/月，列出盘中提醒/多账户/回测/导出等付费价值，标「即将上线」，纯展示，为盈利目标铺钩子）；CTA 区加暖金光晕；整体换用分级 `.reveal` 入场。③ **App 壳**：顶部导航改为**毛玻璃悬浮 App Bar**（`sticky top-0 backdrop-blur`）；用户头像阴影和 ring 对齐新品牌色。④ **「持有」首屏**：总资产英雄区加柔光底框（`.holdings-hero`），数字放大至 `2.15rem` + Sora 等宽字；当日收益数字换 display 字体并加大。⑤ **「盈亏分析」页**：`.pl-hero` 加品牌蓝径向柔光框，大数字换 Sora `2.6rem`。⑥ **日报/推荐/设置页**：日报标题、空态标题、推荐报告标题、推荐基金标题均换 `font-display extrabold`；设置页卡片换设计系统 `section-card` + `section-eyebrow`，输入框/按钮/链接接入新 token。纯前端表现层，不动后端/数据流，`lint`（0 warning）、`typecheck`、`build`（静态导出）全部通过；Playwright 截图脚本验证全流程无报错。截图见 `apps/web/verify-shots/`。
- **好基灵 toC 视觉改造（2026-06-20）：** 面向普通基民的视觉与体验升级，定位 toC 订阅产品。中文品牌名「好基灵」（英文 FundPilot 辅助），Slogan「好基灵，截个图就懂你的基金」。① 设计地基：`globals.css` 扩展信任蓝 `#2563EB` + 暖橙 `#FB8C3B` 点缀的 token 体系（圆角 20px、分层柔和阴影、`.btn-primary/secondary/accent/ghost`、`.badge`、`.input-field`、`.empty-state`、`.card-hover`、`.landing-*`），涨跌红绿不变。② 新增登录前**品牌落地页**（`LandingPage.tsx`）+ 复用品牌标识（`BrandMark.tsx`）；路由：未登录 `/` 看落地页、已登录看 Dashboard（`AuthProvider` 放行 `/`、`page.tsx` 按登录态分支，静态导出安全）。③ 登录/注册/设置页、Dashboard 顶部品牌头、持有页空状态、基金详情页、上传截图弹窗（新增三步引导）全部统一到品牌视觉。④ **全站色彩统一**：推荐基金线（原 indigo）、日报/复盘/要闻面板（原 violet）、市场 `指数` 标签（原 violet）等全部归一到品牌蓝；语义色（warning 琥珀、danger 玫红、conservative 翠绿）保留。纯前端表现层，不动后端/数据流，单测 38 项前端用例通过、`build` 静态导出正常。
- **持有天数锚点修复（2026-06-20，2026-06-23 补强）：** `FundProfile.first_seen_date`（JSON payload）；`save_profile` / `reconcile_first_seen_date` 在**持久化**时写入锚点（购入日 > OCR 天数 > `shares_baseline_date` > 今天），**禁止**详情页读取路径回填 `today`；`_resolve_holding_days` 优先级 `first_purchase_date` → `first_seen_date`（含 baseline 纠偏）→ OCR aging → 快照。实现口径以本文为准。
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
- **LLM 数据包瘦身（2026-06-13）：** `analysis_payload.py` `build_user_payload()` — user JSON 去重（移除顶层 `holdings`/`risk`/`fund_snapshots`/`ocr_text`/`analysis_session`）；`prefetched_news` → `news_titles`（仅标题）；完整输出约束迁入 system `OUTPUT_REQUIREMENTS_SYSTEM`；稳健模式裁剪 `stock_connect_flow`/`signal_backtest`/`prompt_tuning`；快速模式再裁 `portfolio_trend` 与精简 `topic_briefs`；`analysis_facts` 新增 `sector_fund_gap_percent`、`nav_trend.recent_5d_daily_change_percent`，`recent_nav_series` 喂模型时 cap 5 点；对比初版约 **-65%** JSON 体积。
- **fund_code → 主关联板块（2026-06-12）：** SQLite/MySQL 表 `fund_primary_sectors`（schema v3）；`fund_primary_sector_service.py` — 详情 OCR / 养基宝总览沉淀、全局种子（519674→半导体、015945→商业航天 等）、AkShare 季报重仓关键词投票推荐；支付宝导入确认后按 **code 查表**补全 `sector_name`，禁用「国防军工」等名称推断覆盖混合基；`GET /api/funds/{code}/primary-sector`、`POST .../refresh-holdings`、`POST /api/fund-primary-sectors/sync-from-profiles`；`GET /api/funds/search` 东财名称表模糊查码。
- **养基宝详情 OCR + 代码纠错（2026-06-12）：** 识别养基宝详情页（含 6 位代码、关联板块）；`ApplyHoldingsRequest.detail_profiles`；`PATCH /api/fund-profiles/{code}` 支持改 `fund_code`/`fund_name`；前端 `FundCodeEditModal`、OCR 确认弹窗可编辑 code/名称/金额并东财搜索；`AddHoldingModal` 三分栏（支付宝 / 养基宝总览 / 养基宝详情）。
- **支付宝 OCR 查码增强（2026-06-12）：** `fund_code_resolver` — 「发起式」归一、C 类优先、provisional 9xxxxx 清理、`reconcile_holding_fund_codes`；总览无 6 位码时走 AkShare `fund_name_em` 名称表。
- **板块涨跌口径说明（2026-06-12）：** 持仓列表「关联板块」列 **始终**东财板块/指数（`sector_return_percent`）；「当日」列官方净值优先、否则板块估算。混合基 015945/519674 的板块曲线 **不应相同**（商业航天 `BK0963` vs 半导体概念 `BK1036`）；519674 涨跌走概念半导体、分时图走中证半导体 `931865`。养基宝详情偶见「曲线相同但收盘数字不同」——多为 **基金估值/净值** 与 **板块涨跌** 口径混用，对比时请以总览「关联板块」列为准。见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md#养基宝关联板块曲线-vs-收盘数字)。
- **用户认证（2026-06-11，2026-07-02 Web 化）：** 邮箱注册/登录 + JWT；`users` 表（驼峰字段）；业务数据按 `userId` 隔离；Web `/login` `/register` `/settings`；小程序微信/CloudBase 登录端点已随小程序下线移除；MySQL/`FUND_AI_DATABASE_URL` + Docker 云托管；部署见 `docs/deploy/cloudbase.md`。
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

**FundPilot AI** 是面向 ≤5 人私有部署的 Web 基金投研助手：邮箱登录；支付宝/养基宝截图 → OCR → **账户汇总**（板块涨跌估算当日收益）→ 个人风控画像 + **可编辑 AI 角色设定** → 东方财富新闻（AkShare）+ DeepSeek V4 生成**逐持仓基金**操作建议日报；**推荐基金** Tab 从窄池候选中精选新基机会（持有期/金额/风险）；首页自动恢复持仓并刷新板块。本地默认 SQLite；云端可迁 CloudBase MySQL（见 `docs/deploy/cloudbase.md`）。

---

## 能力清单（当前已实现）

| 类别 | 能力 |
|------|------|
| 鉴权 | 邮箱注册/登录（JWT，默认 **30 天**有效）；Web `/login` `/register`；`/settings` 查看当前账号 |
| 输入 | 养基宝**总览 / 详情** OCR（详情含 6 位代码与关联板块）；**支付宝持有列表 OCR**（预览确认后写入）；确认弹窗可编辑 code/名称/金额并东财搜索；当日列为 `-` 时不填当日收益；**OCR 漏负号**时规则补符号；**截图识别引擎 auto**：有 key 走云端 `qwen-vl-ocr`（DashScope OpenAI 兼容；模型只做纯文本 OCR，再交本地 `parse_holdings_from_text` 结构化——与本地 PaddleOCR 同一解析器；min/max_pixels 控制 token、上传前 JPEG 压缩；QDII/截不全/余额宝过滤鲁棒、<5s），否则/失败回退本地 PaddleOCR |
| 主关联板块 | `fund_primary_sectors`（用户）+ **`fund_primary_sectors_global`（全市场预计算，TTL）** + **业绩基准解析**（`benchmark_index`，**中基协 155 指数库 + `THEME_BOARD_INDEX`**）+ **季报重仓行业穿透**（`holdings_infer`）；`resolve_primary_sector` 默认不用名称子串推断；支付宝/OCR 确认后写用户表，benchmark 命中 promote global |
| 当日收益 | 盘中/净值未公布：**板块涨跌估算**；NAV 发布后：**官方日增长率**；**当日新购 defer**（OCR 三列收益≈0 → 次交易日起计，含官方 NAV 公布后仍强制日收益 0）；关联板块列始终东财涨跌 |
| OCR 校验 | OCR 返回 `holding_warnings`；账户汇总为唯一持仓展示与日报输入源（`displayableHoldings` 过滤占位行） |
| 持仓元数据 | SQLite `fund_profiles` + `fund_primary_sectors` 由 OCR **自动维护**（份额、成本、板块、购入日）；拒绝 `+`/`-`/Tab 标签误存为板块名；`POST /api/fund-profiles/repair-sectors` 清理历史脏数据；查码走东财名称表 + 档案兜底；详情页铅笔改代码 |
| 简报首页 | **简报** Tab（默认）：`TodayBriefing` — 组合摘要 KPI、板块脉搏、最新日报决策卡、内联 AI 追问；`todayBriefing.ts` 纯前端汇总逻辑 + vitest |
| 首页看板 | **持仓** Tab：`YangjibaoHoldingsBoard` 养基宝式卡片（`AddHoldingModal` 上传支付宝/养基宝截图）；**localStorage 缓存优先** instant 展示 → `GET /api/portfolio/holdings`（服务端 120s 内存缓存 + `refreshed_at`）→ 后台 `refresh-sector-quotes`（**stale-while-revalidate**：`mergeHoldingsPreserveQuoteFields` 刷新完成前保留上一屏板块/收益）；OCR 确认后乐观写 localStorage + 服务端 `save_cached_holdings_response`，不再立即 `hydratePortfolio` 覆盖；点击行打开 `YangjibaoFundDetail` |
| 导航 | `DashboardNav`：桌面 `lg+` 顶栏 6 Tab；手机/平板底栏 5 项 +「更多」 sheet（发现/日报）；历史入口下沉到对应工作区的导航器/抽屉；`dashboard-shell` 底栏安全区留白 |
| 导航记忆 | Dashboard 主 Tab（简报/持仓/盈亏分析/市场/推荐基金/生成日报）与 **市场** 子 Tab（主题板块/大跌雷达/美股）均用 `sessionStorage` 刷新后恢复 |
| 基金详情 | 关联板块分时图（边框/十字线）；**业绩走势**（区间涨跌 vs 沪深300、历史净值分页）；**我的收益**；持有天数滚轮选购入日；持仓明细默认收起 |
| 盈亏分析 | **盈亏分析** Tab：`PortfolioDashboard` — 收益走势（当日/周/月/年/全部）、盈亏日历（周末/假日 **0.00**；今日官方净值未出 **未更新**）、当日 TOP5、持仓甜甜圈；`GET /api/portfolio/dashboard` |
| 组合风险体检 | `PortfolioRiskMetricsPanel`（盈亏分析 Tab）：波动率/最大回撤/夏普/索提诺/Beta/Alpha/HHI；纯函数 `portfolio_risk_metrics.py`；复利累乘净值曲线；挂在 dashboard 响应 `risk_metrics` 字段；Pro 门控（免费 2 项）。相关性矩阵 `PortfolioCorrelationHeatmap` 经独立懒加载接口 `GET /api/portfolio/risk-correlation` |
| 持仓因子体检 | `PortfolioFactorScoresPanel`（盈亏分析 Tab，风险体检下方，展开懒加载）：给每只持仓在排行榜横截面里打**动量/风险调整(Calmar)/回撤控制/规模**因子分（z-score→百分位→等级A/B/C/D）；纯函数 `fund_factors.py` + 装配层 `build_factor_scores_payload`（`fetch_open_fund_rank` 做池，不在榜走净值兜底）；独立接口 `GET /api/portfolio/factor-scores`；标题下 `FactorIcStatusBadge` 展示共享 IC 快照日期、有效基金数与 30 天过期状态；Pro 门控（免费仅动量） |
| 市场板块 | **市场** Tab：`MarketTab` — 子 Tab「**主题板块 \| 大跌雷达 \| 美股**」；三者均为**全用户共享**服务端缓存 + stale 回退（含**跨进程 stale**：重启后首请求秒回磁盘缓存并标 `stale`，后台异步刷新）。主题：`ThemeSectorOverview`（`GET /api/market/theme-boards`，读持仓高亮 `fetch_benchmark=False`；**小倍式精选白名单 ~76**（含 AMAC 主题补码）、`board_kind` 标签、涨跌幅+连涨 **push2delay 日 K**、**主力净流入+四档展开+历史走势柱状图（近一周/近一月，`GET /api/market/board-flow-history` 懒加载）**、列头排序、行操作「看大跌基金」「加入关注方向」）；大跌雷达：`DipReboundRadar`（`GET /api/market/dip-radar`，全市场近 1 周跌幅榜预筛 + NAV 精算、`sector_label` 经 **`resolve_sector_labels_for_radar`**、`rebound_score`/反弹信号、**`historical_hint`**、板块 chip 筛选、「深度扫描」跳转荐基 `dip_swing`）；后台 `market_shared_refresh_loop` 活跃 20min / 休市 3h（每 30min 唤醒检查）、前台 SWR |
| 板块注册表 | `sector_registry` + `sector_registry_data` — 统一主题榜/荐基 chips 的 `market_quote`/`discovery_quote`、别名与 `discovery_eligible`/`theme_board_eligible`；`theme_board_snapshot` 优先读注册表 |
| 美股概览 | **市场** Tab 子 Tab「美股」：`UsMarketOverview`（`GET /api/market/us-overview`）— 纳指/标普/道指**指数期货**（真实期货，禁回退收盘价）+ USD/CNY 汇率指标卡 + QDII「盘前参考涨跌」列表（基于期货盘前涨跌估算，标注非承诺性预估）；美东时段标签（盘前/盘中/盘后/休市，含夏令时）+ 更新时间；服务端 snapshot + stale 回退 + 优雅降级（`*_status` 标 `ok`/`stale`/`unavailable`，绝不编造数值）；后台与 A 股独立计时（活跃 20min / 休市 3h）；前端 SWR 活跃 20min / 休市 3h、不可见暂停 |
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
| 大盘情绪温度计 | `GET /api/diagnostics/market-breadth`；`MarketBreadthGauge.tsx` 挂市场 Tab + 生成日报诊断区；情绪等级基于全市场新高/新低家数近2年分布自校准，涨跌停/炸板/两融为当日快照 |
| 双向决策 guard | `decision_guard_shared.resolve_escalation_floor()`（日报）/ `resolve_discovery_escalation()`（荐基）——证据强烈时不仅能降级、也能把"观察"强制升级为"暂停追涨/减仓评估/大幅减仓评估/清仓评估"（日报）或剔除候选池/提高建议金额（荐基）；灰度开关 `FUND_AI_DECISION_ESCALATION_MODE=shadow\|enforced` |
| 灰度复盘摘要 | `GET /api/diagnostics/shadow-escalation-digest` — 近 N 天双向 guard 升级触发聚合（按板块/建议动作+当日走势对照）；`ShadowEscalationDigestCard.tsx` 仅 shadow 模式下展示 |
| 交易日语义 | `trading_session.py` + `trade_calendar_cache`；**9:30 前** `trading_day_pre_open` 展示上一交易日（对齐养基宝，周末/节假日同理）；`TradingSessionBar` |
| 穿透估算 | 未收盘时按板块权重分配账户当日收益 |
| 板块实时 | **canonical 映射优先**（`sector_canonical` → 东财 `secid` K 线）；未知板块再走 spot 批量表 + `sector_quote_resolver` + `sector_on_demand`；可选中继/浏览器命令；300s 自动 + 手动；低置信度 `SectorMappingModal`；有场内指数时优先指数口径（`sector_quote_lookup_label`） |
| 分时图 | `GET /api/sector-quotes/intraday`；push2delay 首选；相对**昨收**对齐养基宝；骨架点 &lt;30 不写缓存；可选 `sector_intraday_browser_command` 浏览器兜底 |
| 官方净值 | AkShare `fund_open_fund_info_em` 覆盖**当日收益**（非板块列）；源标签：板块实时 / 收盘估算 / 官方净值；昨日收益取再上一交易日官方净值或 OCR |
| 工作流阻塞 | `workflowBlockers`（生成日报前校验，无独立阻塞清单组件） |
| 数据备份 | SQLite export/import API（`GET/POST /api/database/*`）；Web 面板已移除 |
| 云部署 | `apps/api/Dockerfile`、`docker-compose.cloud.yml`；`scripts/migrate_sqlite_to_mysql.py`；见 `docs/deploy/cloudbase.md` |
| CI / E2E | GitHub Actions：`api` 并行 pytest（**652** 项）+ `web` lint/typecheck/build + Playwright 冒烟；另有 `Factor IC Refresh` 周度/手动外部回测 workflow |
| 基金诊断 | AkShare 概况/累计收益；详情页可 AkShare **按名称查码**并持久化 |
| 分析模式 | 快速 / 深度 |
| 体验 | Markdown 导出、桌面通知、**Sora 字体 + 中文系统字体栈**（PingFang / HarmonyOS / 雅黑 / Noto）UI；**「静谧蓝海·高级克制」设计语言**（深海蓝 `#2356e0` + 暖金 `#cf9b3e`、毛玻璃 App Bar、会员方案展示区）；**客户端 SWR 缓存**（盈亏分析/详情/业绩走势）；板块刷新 fast 轮询 + accurate 手动；追问侧栏智能滚动 |
| 报告追问 | SSE + ChatMarkdown；`useChatAutoScroll` 贴底/回到底部 |
| 流式报告 | **推荐路径**：`POST /api/analyze/stream`、`POST /api/fund-discovery/stream`（SSE：`stage`/`token`/partial/`done`）；`StreamingAnalysisFloat` + `DiscoveryStreamingFloat`；日报生成前 `followup` 追加 `operator_notes` |
| 异步任务 | `/api/analyze/async` + `/api/fund-discovery/async`（流式失败回退）；`BackgroundJobsStack` 堆叠双浮层；`GET /api/jobs/{id}`（`job_kind` 区分日报/荐基） |
| 前端偏好 | localStorage：风控、**日报/荐基 AI 角色 Prompt**、分析模式、板块自动刷新 |

---

## 产品边界

| 会做 | 不会做 |
|------|--------|
| OCR、校对、风控、AI 日报（逐持仓）、可编辑角色 Prompt、**推荐基金 Tab**（窄池荐新基）、示意金额 | 自动下单、券商对接 |
| 邮箱登录、按用户隔离持仓（私有部署） | 公开大规模 SaaS |
| 本地 SQLite / 上传目录 | 默认把原始截图发往云端 |
| 公开新闻标题/摘要供模型参考 | 投资建议（报告须有 caveats） |

**隐私：** DeepSeek 收到**结构化持仓、风控、净值摘要、新闻标题/摘要**。截图识别默认走云端视觉模型（`FUND_AI_OCR_PROVIDER=auto`，配置 `FUND_AI_VLM_OCR_API_KEY` 即启用 `qwen3-vl-flash`）：**此时截图图片会发往阿里云百炼用于识别**；设 `FUND_AI_OCR_PROVIDER=local` 可强制本地 PaddleOCR 不外传截图。见 `README.md`「隐私和边界」。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js、React、TypeScript、Tailwind、Lucide；浏览器 `Notification` |
| 后端 | FastAPI、Pydantic v2、uvicorn；`lifespan` 可选 DB 自动导入 |
| 存储 | SQLite（本地）/ CloudBase MySQL（目标）：`users`、`reports`、`fund_profiles`、`portfolio_*` 等；业务表均含 `userId` |
| 鉴权 | JWT（邮箱密码）；`FUND_AI_JWT_SECRET`；`app/auth/`（middleware、models、service） |
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
│   ├── auth/                # JWT 中间件、邮箱登录
│   ├── mysql_bootstrap.py   # MySQL 建表（可选）
│   └── services/
│       ├── ocr_engine.py / ocr_parser.py / ocr_pipeline.py / alipay_holdings_parser.py / overview_pipeline.py
│       ├── index_daily_client.py   # 沪深300等指数日线（新浪优先）
│       ├── portfolio_parser.py / portfolio_snapshot.py / portfolio_holdings_service.py
│       ├── holding_validation.py / holding_metrics.py / holding_estimates.py / holding_amount_sync.py / holding_detail_service.py
│       ├── sector_quote_service.py / sector_quote_provider.py / sector_quote_resolver.py / sector_canonical.py
│       ├── fund_benchmark_sector.py / amac_benchmark_index_data.py  # 业绩基准 → 板块（含中基协 155 库）
│       ├── fund_primary_sector_service.py / fund_primary_sector_global.py / fund_primary_sector_precompute*.py
│       ├── fund_industry_theme_map.py / fund_holdings_sector_infer.py / fund_primary_sector_types.py
│       ├── theme_board_snapshot.py / dip_radar_snapshot.py / fund_dip_rebound_backtest.py / sector_daily_kline_provider.py  # 市场 Tab
│       ├── sector_registry.py / sector_registry_data.py / dip_drop_scanner.py  # 板块注册表 + 大跌预筛/雷达
│       ├── us_market_service.py / us_*_client.py  # 市场 Tab 美股概览
│       ├── fund_nav_service.py / eastmoney_spot_client.py / eastmoney_trends_client.py
│       ├── akshare_spot_client.py / sector_on_demand.py / sector_intraday_provider.py
│       ├── sector_intraday_browser_provider.py / sector_quote_browser_provider.py / sector_quote_relay_provider.py
│       ├── trade_calendar_cache.py / sector_labels.py / sector_quote_cache.py
│       ├── fund_code_resolver.py / fund_name_utils.py
│       ├── deepseek_http.py / fund_profile.py / risk.py / fund_data.py
│       ├── recommendation_guard.py / analysis_facts.py / news_citation.py
│       ├── decision_guard_shared.py  # 日报+荐基共用：resolve_escalation_floor / resolve_discovery_escalation / classify_action_bucket（AI 决策升级 M2/M4）
│       ├── market_breadth_signal.py  # 大盘情绪温度计（M1.1，新高低家数自校准 + 涨跌停/炸板 + 两融环比）
│       ├── sector_flow_divergence_backtest.py  # 量价背离信号 T→T+1 回测（M1.3）
│       ├── discovery_judge.py  # 荐基 deep 模式风控复核角色（M4，同构 report_judge.py）
│       ├── shadow_escalation_digest.py  # 灰度复盘摘要聚合（M6.3）
│       ├── recommendation_outcomes.py / rebalance_simulator.py / report_judge.py
│       ├── news_service.py / news_summarizer.py / news_cache.py
│       ├── penetration_daily_allocator.py / market_signal.py / trading_session.py
│       ├── portfolio_profit_analysis.py   # 盈亏走势、日历、TOP5
│       ├── portfolio_risk_metrics.py / fund_factors.py  # 组合风险度量 + 持仓因子打分（纯函数）
│       ├── fund_factor_nav.py / factor_ic_backtest.py   # 因子NAV共享helper + 因子IC回测引擎（模块3-3A，离线）
│       ├── fund_style_regression.py / fund_universe_sampler.py  # 价值/成长风格回归(3C) + 分层抽样池(3D)
│       ├── signal_confidence.py   # 板块信号可信度打分器（模块4-4A，纯函数，喂LLM）
│       ├── factor_ic_snapshot.py / factor_confidence.py  # IC 版本契约、共享快照/状态 + 置信映射
│       ├── risk_confidence.py     # 组合风险度量置信（模块4 竖切4，样本充足度 喂LLM）
│       ├── signal_synthesis.py    # 信号合成证据卡+组合证据总览（模块4，三路置信聚合 喂LLM/前端）
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
│       ├── deepseek_client.py / deepseek_streaming.py / analysis_runtime.py
│       ├── analyze_pipeline.py / analyze_streaming.py   # 日报同步/异步/流式
│       ├── streaming_json_parser.py / stream_session_store.py
│       ├── discovery_*.py           # 荐基：窄池、守卫、pipeline、streaming、sector_context、payload、chat…
│       ├── job_status_service.py    # GET /api/jobs/{id} 单连接查询 discovery/analysis
│       └── recommendations.py
├── apps/web/src/
│   ├── app/login/ register/ settings/   # 认证与账号设置
│   ├── lib/api.ts / reportPresentation.ts / decisionText.ts / holdingDisplay.ts / holdingMetrics.ts / portfolioHoldingsCache.ts / marketThemeBoard.ts / dipRadar.ts
│   └── components/
│       ├── AuthProvider.tsx       # JWT 与 /api/auth/me
│       ├── Dashboard.tsx          # 简报 / 持仓 / 盈亏分析 / 市场 / 推荐基金 / 生成日报（Tab sessionStorage + report URL 恢复）
│       ├── DashboardNav.tsx       # 桌面顶栏 + 移动端底栏
│       ├── TodayBriefing / BriefingDecisionCards / BriefingChatPanel
│       ├── MarketTab / ThemeSectorOverview / DipReboundRadar / UsMarketOverview
│       ├── FundDiscoveryPanel / DiscoveryReportPanel / DiscoveryChatPanel / DiscoveryJobStatusFloat
│       ├── DiscoveryHistoryWorkspace / DiscoveryHistoryRail / DiscoveryCandidatePoolPanel / DiscoveryOutcomesPanel
│       ├── YangjibaoHoldingsBoard / YangjibaoFundDetail / AddHoldingModal / AlipayOcrConfirmModal
│       ├── PortfolioDashboard / FactorIcStatusBadge / ProfitAnalysisTrendChart / ProfitLossCalendar / DailyProfitTop5 / HoldingDonutChart
│       ├── PerformanceTrendPanel / PerformanceReturnChart / NavHistoryListModal / WheelDatePicker
│       ├── SectorMappingModal / IntradayPercentChart
│       ├── TradingSessionBar / useChatAutoScroll
│       ├── RiskControls / ReportSummaryHero / ReportRecommendationList / FundRecommendationCard
│       ├── ReportDetailsHub / ReportChatDrawer / ReportChatPanel / NewsPreviewPanel / SectorSignalBacktestPanel
│       ├── MarketBreadthGauge / ShadowEscalationDigestCard  # AI 决策升级 M5/M6.3
│       ├── ReportPanel / ReportNavigator / ReportHistoryDrawer / HistoryDrawerShell / JobStatusFloat / HistoryRail / UserMenu
├── apps/api/Dockerfile
├── .github/workflows/factor-ic-refresh.yml  # 周度/手动生成并发布因子 IC 快照
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
```

### 账户汇总与持仓元数据

```text
今日页 → 支付宝/养基宝总览截图 → POST /api/ocr?preview=true
       → POST /api/portfolio/apply-holdings（秒回：写库 + 板块缓存估算）
       → 前端关闭确认弹窗 → 后台 refresh-sector-quotes(fast) 刷新最新行情
       → 自动 sync_profiles（fund_profiles 表）+ bootstrap 份额（skip_network）
打开应用 → localStorage 恢复持仓（若有）→ GET /api/portfolio/holdings（内存缓存 + refreshed_at）→ 可选自动 refresh-sector-quotes
点击持仓行 → 基金详情（业绩走势、持有天数、板块分时）
```

### 基金详情：业绩走势与持有天数

```text
业绩走势 Tab → 默认近3月；切换近1月/6月/1年/3年；蓝线本基金、橙线沪深300
下方近1月净值预览 →「查看历史净值」→ 滚动加载更早记录（每页 30 条）
点击「持有天数」→ 滚轮选择首次购入日 → PATCH /api/fund-profiles/{code} → 天数按日历递增
```

**档案合并规则：** 总览有、档案无 → 自动简略档案（`is_provisional`）；**用户主动删除** → 删快照行 + 删 `fund_profiles` / 用户级 `fund_primary_sectors`（历史日快照保留）；总览更新金额/收益/板块，不覆盖详情才有的份额/成本/持有天数。

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

### 异步分析（主流程，轮询）

```text
POST /api/analyze/async { holdings, profile, analysis_mode, system_role_prompt? } → job_id
  → 线程池 run_analysis()
  → DeepSeekClient._system_prompt(role + OUTPUT_REQUIREMENTS_SYSTEM) + build_user_payload()
  → GET /api/jobs/{id} 轮询（JobStatusFloat，1.5s；含 stage_label）
  → status=completed 时含 report → onComplete 回调 → 切换报告 Tab
```

### 流式分析（推荐，SSE）

```text
POST /api/analyze/stream { holdings, profile, analysis_mode, system_role_prompt? }
  → analyze_streaming.stream_analysis()：prefetch → generating →（deep）tool_round_N → token 流 → guarding → saving → done
  → 生成前可 POST /api/analyze/stream/{session_id}/followup { message } 追加 operator_notes
  → 前端 StreamingAnalysisFloat + ReportThinkingSidebar 打字机

POST /api/fund-discovery/stream { DiscoveryRequest }
  → discovery_streaming.stream_discovery()：同上阶段；fast/deep 均流式；DiscoveryStreamingFloat
  → 失败可回退 POST /api/fund-discovery/async 轮询
```

### 推荐基金（异步轮询，回退路径）

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
| GET | `/api/auth/me` | 当前用户（需 JWT） |
| POST | `/api/internal/factor-ic-snapshots` | 因子 IC 快照发布（不进 OpenAPI；`X-Factor-IC-Publish-Token` 专用鉴权；质量不达标 422、已有更新 409、共享存储不可用 503） |
| POST | `/api/ocr` | 截图/文本 → holdings；`preview=true` 仅解析不写入；支持支付宝列表 |
| POST | `/api/portfolio/apply-holdings` | 确认 OCR 预览写入持仓与快照：**秒回**（skip_network bootstrap + `cache_only` 板块估算）；后台由前端触发 `refresh-sector-quotes` 补全最新行情 |
| GET | `/api/funds/search?q=` | 东财基金名称表模糊查码（OCR 确认 / 改码 picker） |
| GET | `/api/funds/{code}/primary-sector` | 查询 fund_code→主关联板块（DB / 档案 / 种子） |
| POST | `/api/funds/{code}/primary-sector/refresh-holdings` | AkShare 季报重仓推荐板块并写入表 |
| POST | `/api/fund-primary-sectors/sync-from-profiles` | 从已有 `fund_profiles` 批量同步板块映射 |
| POST | `/api/analyze` | 同步生成 Report（兜底） |
| POST | `/api/analyze/async` | `{ job_id, status }`（流式失败回退） |
| POST | `/api/analyze/stream` | SSE 流式日报；事件 `stage`/`token`/`done`/`error` |
| POST | `/api/analyze/stream/{session_id}/followup` | 生成完成前追加 `operator_notes` |
| GET | `/api/trading-session` | 交易日/收盘窗口语义 |
| GET | `/api/investor-profile` | 读取持久化风控画像（未保存时返回默认） |
| PUT | `/api/investor-profile` | 保存风控画像（含 `decision_style`、`investment_preset`、激进波段参数、盯盘开关） |
| POST | `/api/swing-alerts/evaluate` | 评估持仓/全市场波段信号（服务端去重写入 `swing_alert_fired`） |
| GET | `/api/swing-alerts/today` | 当日已触发波段提醒列表 |
| GET | `/api/analysis-prompt` | 读取 AI 角色设定；含 `role_prompt`、`is_custom`、`default_role_prompt` |
| PUT | `/api/analysis-prompt` | 保存角色设定；body `{ role_prompt }`，`null`/空串恢复默认 |
| GET | `/api/reports/{id}/outcomes-weekly?days=7` | 兼容入口：按基金估值日精确 T+N 复盘，不再用相邻日报作标签；可评价 V2 observation 会持久化 |
| GET | `/api/reports/recommendation-accuracy?days=30` | 正式 DecisionEvent v2 四指标统计；legacy 单列且排除正式分母 |
| GET | `/api/diagnostics/sector-signal-backtest?days=120&sectors=半导体,商业航天` | 板块信号 T→T+1 回测；`sectors` 省略时用全部 canonical |
| GET | `/api/diagnostics/market-breadth` | 大盘情绪温度计（M1.1，全用户共享，仍需 JWT） |
| GET | `/api/diagnostics/shadow-escalation-digest?days=7` | 灰度复盘摘要（M6.3，近 N 天双向 guard 升级触发聚合，`days` 夹在 1~30） |
| GET | `/api/diagnostics/factor-ic-status` | 因子 IC 快照新鲜度（需 JWT）：来源、生成/发布时间、有效基金数、回测期数、四因子有效期、30 天过期状态 |
| GET | `/api/database/export` | 下载 SQLite |
| POST | `/api/database/import` | 上传替换 DB（自动备份 `.db.bak`） |
| GET | `/api/jobs/{id}` | 任务状态（日报或推荐基金）；`job_status_service` 单连接先查 `discovery_jobs`；含 `job_kind`、`stage`/`stage_label`；完成时含 `report` 或 `discovery_report`；DB 不可用 503 |
| GET | `/api/discovery-prompt` | 读取荐基 AI 角色设定；含 `role_prompt`、`is_custom`、`default_role_prompt` |
| PUT | `/api/discovery-prompt` | 保存荐基角色设定；body `{ role_prompt }`，`null`/空串恢复默认 |
| GET | `/api/market/sector-boards` | 全市场板块行情（**Web 已移除 UI**；`theme_board_snapshot` 合并主力净流入仍读此缓存；`view=widget|list`） |
| GET | `/api/market/theme-boards` | 主题板块（**小倍式粗粒度精选白名单 ~66**…；含 `main_force_net_yi` / `flow_tiers`；默认 `sort=change`，前端列头本地排序；`sort=inflow` 可选）；其余字段同前 |
| GET | `/api/market/board-flow-history` | 板块主力净流入历史：`sector_label` 或 `board_code`（推荐传 `theme-boards` 的 `flow_source_code`）；`range=week`（5 交易日）\| `month`（20 交易日）；响应 `available`、`points[]`（`date`、`main_force_net_yi`、`flow_tiers`）、`cumulative_net_yi`；东财 `80/82.push2his` `fflow/daykline`；BK 解析：主题白名单 → `_THEME_BOARD_FLOW` → canonical；缓存 `board-flow-hist:v1:{BK}`（盘中 15min / 收盘 1h）；拉取失败读 stale cache |
| GET | `/api/market/dip-radar` | 大跌反弹雷达：`lookback_days`（3\|5，默认 5）、可选 `sector`（注册表 label）、`limit`（默认 20）、`force_refresh`；`dip_drop_scanner.build_dip_radar_pool_fast` 全市场近 1 周跌幅榜预筛 + 头部 NAV 精算；`items[].sector_label` 经 `resolve_sector_labels_for_radar`（用户表 → global → resolve）；缓存 `dip:radar:v2:{trade_date}:{lookback}`（盘中 60s / 收盘 1h）；响应含 `rebound_score`、`rebound_signals`、**`historical_hint`**、`sector_dip_leaders` |
| GET | `/api/market/us-overview` | 美股概览快照（`UsMarketSnapshot`）：纳指/标普/道指**指数期货** + USD/CNY 汇率 + QDII「盘前参考涨跌」列表 + 美东时段（`session_kind`/`session_label`，含夏令时）+ `updated_at`；`force_refresh` 跳过服务端时段感知缓存；无需 JWT；任一数据源失败仍返回 200，经 `futures_status`/`forex_status`/`qdii_status`/`available`/`stale`/`message` 表达陈旧或不可用，绝不回退收盘价或编造数值 |
| GET | `/api/fund-discovery/sectors` | 荐基关注方向 chips：`build_sector_heat_ranking_for_ui()`（当日涨跌轻量拉取、12s 预算；超时回退全部标签）；扫描 pipeline 仍用完整 `build_sector_heat_ranking()` |
| POST | `/api/fund-discovery/async` | 创建推荐基金异步任务；body `DiscoveryRequest`；**`scan_mode=dip_swing`** 时走大跌预筛 + `dip_rebound` 选基策略（流式失败回退） |
| POST | `/api/fund-discovery/stream` | SSE 流式荐基；fast/deep 均支持；事件含 `recommendation` partial |
| GET | `/api/fund-discovery/reports` | 最近 30 条推荐报告 |
| GET | `/api/fund-discovery/reports/{id}` | 推荐报告详情 |
| GET | `/api/fund-discovery/reports/{id}/diff` | 与上一份推荐报告对比 |
| GET | `/api/fund-discovery/reports/{id}/outcomes` | 按基金自身估值日 T+5/T+20/T+60 复盘（旧 `days=7` 兼容）；V2 observation 持久化 |
| GET | `/api/fund-discovery/recommendation-accuracy` | 正式 V2 四指标统计（方向/假设费后/合同基准毛超额/合同基准费后超额） |
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
| GET | `/api/portfolio/holdings` | 恢复首页持仓；**快路径**：120s 内存缓存命中直接返回 → 否则 `build_fast_snapshot_holdings_response()`（日快照 + 官方净值缓存 overlay，不打网）→ 仍无数据再走档案慢路径；25s 超时 503；响应含 `refreshed_at` / `fast_snapshot` |
| GET | `/api/portfolio/ledger-baseline` | 实际份额/成本/现金账本状态；返回完整性、quality、pending/conflict 和 `store_authority` |
| PUT | `/api/portfolio/ledger-baseline` | 一次性或对账式确认实际份额、可选总成本/现金；主存储不可用时 503，不写 fallback |
| POST | `/api/transactions/ocr` | 支付宝交易 OCR/文本预览；解析和补全确认日，不直接写账本 |
| POST | `/api/transactions/apply` | 交易 + ledger 原子双写；实际份额优先、未知费用保持 null、冲突 409、主存储不可用 503 |
| GET | `/api/funds/{code}/transactions` | 单基金交易历史（含确认份额、费用来源与状态） |
| DELETE | `/api/portfolio/holdings/{code}` | 删除持仓并追加零份额关闭事件，避免账本 ghost |
| GET | `/api/portfolio/summary` | 账户汇总 + 全部档案 |
| GET | `/api/portfolio/dashboard` | 盈亏分析：`range` 为 today/week/month/year/all；可选 `calendar_year`、`calendar_month`；含 profit_trend、profit_calendar、daily_top5、持仓分布、**risk_metrics**（组合风险体检，样本不足时 `available=false`） |
| GET | `/api/portfolio/risk-correlation` | 持仓相关性矩阵（懒加载，逐只拉 nav-history）；`lookback_days` 默认 120（30~400）；<2 持仓或对齐 <20 日返回 `available=false` |
| GET | `/api/portfolio/factor-scores` | 持仓因子体检（懒加载）：排行榜横截面 z-score 多因子打分（动量/风险调整/回撤/规模）；响应 `available`、`universe_size`、`funds[]`（`composite_score`/`composite_grade`/`factors{percentile,z,raw}`）；排行榜池 <30 只返回 `available=false` |
| GET | `/api/portfolio/evidence-overview` | 组合证据总览（懒加载）：每持仓三路量化置信（因子IC/板块信号/风险样本）聚合 → 市值加权背书分布；响应 `available`、`overview{backed_weight_percent,weight_by_level,count_by_level,covered_holdings,summary}`、`holdings[]{evidence}`；三路 best-effort，异常不 500 |
| DELETE | `/api/portfolio/snapshots` | 清除 `on_or_before`（含）及更早的日快照（运维/重置盈亏历史） |
| GET | `/api/reports/{id}/outcomes` | 基金估值日 T+1/T+5/T+20 四指标复盘；V2 observation 持久化，终态冲突 409 |
| GET | `/api/reports/{id}/rebalance-simulation` | 按报告动作 + 示意金额模拟调仓（缺 `amount_yuan` 时自动补算；超集中度「观察」也会减） |

前端封装：`apps/web/src/lib/api.ts`。

---

## 领域模型（摘要）

| 模型 | 要点 |
|------|------|
| **Holding** | 6 位代码、金额、持有/当日/昨日收益、板块；`sector_return_percent_source`（realtime / closing_estimate）；`daily_return_percent_source`（sector_estimate / official_nav）；`yesterday_profit`；见 `holding_analysis_payload` |
| **InvestorProfile** | 稳健默认；浮亏 8%、集中度 35%、期望投入 3 万（可配置）；`prefer_dca`（弱收益时分批加仓规则）、`avoid_chasing`（板块大涨限加仓档）；随 `profile` 传入 DeepSeek；持久化 `investor_profile_state` |
| **FundRecommendation** | action、amount_*、news_bullish/bearish、points；`confidence`、`hold_horizon`、`risks`、`decision_path`、`sector_evidence`、`fund_evidence`、`validation_notes`（2026-07-01 对齐荐基结构化字段）；**`suggested_position_change_percent`**、**`suggested_position_change_basis`**（2026-07-02 M2.3：正=建议加仓、负=建议减仓，由 guard 按规则表回填，覆盖 LLM 自给数字）；`action` 词表扩展为 7 项，新增**大幅减仓评估**/**清仓评估**（按 `escalation` 触发矩阵动态开放，均带默认值向后兼容） |
| **NewsItem** | topic、title、is_today |
| **Report** | 含 `fund_recommendations`、`market_news`、`topic_briefs`、`analysis_facts`；`market_context` 保留字段恒 `[]` |
| **PortfolioLedgerEvent / PositionSnapshot** | 追加式实际份额/成本/现金账本与决策时点快照；账本含 supersession、CAS head、哈希链，快照含 ledger/valuation version、compact truth 与完整性 |
| **BenchmarkMapping** | 决策时点冻结的基金合同/跟踪指数/类别代理；仅完整 `fund_contract_exact` 可进入正式超额 |
| **DecisionEvent v2 / OutcomeObservation v2** | 固化动作、固定 horizons、费用假设与审计版本；结果 pending 可修订、成熟终态锁定，正式统计拆成四项指标 |
| **AnalysisRequest** | holdings、profile、ocr_text、**analysis_mode**、**system_role_prompt**（可选，≤4000 字；缺省用 `DEFAULT_ROLE_PROMPT`） |
| **AnalysisPromptConfig** | `role_prompt`、`is_custom`、`default_role_prompt`；持久化 `analysis_prompt_state` 按 `userId` |
| **DiscoveryRequest** | `profile`、`analysis_mode`、`focus_sectors`（≤3）、`budget_yuan`、`holdings`、**`fund_type_preference`**、**`selection_strategy`**、**`scan_mode`**（`full_market` \| `portfolio_gap` \| **`dip_swing`**）、**`dip_lookback_days`**（3\|5）、**`dip_min_drop_percent`**、**`system_role_prompt`**（可选，≤4000 字） |
| **DiscoveryPromptConfig** | `role_prompt`、`is_custom`、`default_role_prompt`；持久化 `discovery_prompt_state`（schema v6）按 `userId` |
| **DiscoveryRecommendation** | `action`、`suggested_amount_yuan`、`hold_horizon`、`confidence`、`points`、`risks`；**`dip_drop_percent`**、**`rebound_signals`**、**`target_exit_days`**、**`fee_break_even_percent`**（`dip_swing` 波段止盈语义）；**`decision_path`**、**`sector_evidence`**、**`fund_evidence`**、**`validation_notes`**（结构化决策依据，日报 `FundRecommendation` 同款字段与之对齐）；**`suggested_position_change_percent`**、**`suggested_position_change_basis`**（2026-07-02 M2.3/M4：荐基语义下正=建议提高买入金额权重、负=建议降低） |
| **EliminatedCandidate** | 2026-07-02 M4/M5：被双向 guard 因证据强烈共振剔除的候选（`fund_code`/`fund_name`/`sector_name`/`reasons`/`basis`）；结构化字段，避免前端正则解析 `caveats` 文本 |
| **FundDiscoveryReport** | 推荐报告；含 `candidate_pool`、`discovery_facts`、`recommendations`、**`eliminated_candidates`**（`EliminatedCandidate[]`，默认空列表向后兼容旧报告）；表 `fund_discovery_reports` |
| **ChatMessage** | report_id、role、content |
| **ReportChatRequest** | message、**chat_mode**（fast \| deep） |

占位码 `000000`：总览 OCR 无代码时，**仅**通过已保存 `FundProfile` 按名称补全；未知代码分析时保留 `yangjibao-ocr` 快照。用户在详情页打开基金时可东财名称表 / AkShare 按名称查码。

### fund_code → 主关联板块

混合基（如 015945 国防军工→**商业航天**、519674 创新成长→**半导体**）不能从基金名推断板块。解析优先级：

1. **用户表（高信任）** — `fund_primary_sectors`（OCR 详情沉淀 / manual）
2. **业绩基准** — `fund_benchmark_sector` 拉官方比较基准 → 跟踪指数 → `THEME_BOARD_INDEX`（source=`benchmark_index`，如 021533→半导体材料/931743）
3. **用户表（低信任）** — `alipay_overview` 总览沉淀
4. **档案** — `fund_profiles.sector_name`（养基宝详情 OCR）
5. **全局种子** — `GLOBAL_FUND_SECTOR_SEEDS`（极少数兜底）
6. **重仓推荐** — `fund_portfolio_hold_em` + 关键词投票（主动基，`holdings_infer`）
7. **名称推断** — 最后兜底（支付宝导入路径默认跳过 alipay 板块字段）

实现：`fund_primary_sector_service.py` + `fund_benchmark_sector.py`；接入 `overview_pipeline`、`ocr_pipeline`、`fund_profile.save_profile`。

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
| 盘中板块估算 | `daily_profit ≈ settled_holding_amount × sector_return% / 100` |

**昨日收益：** 再上一交易日官方净值涨跌（`compute_yesterday_profit_from_official_nav`），账户汇总「估算当日」列下展示「昨 ±xx」；OCR 详情页 `yesterday_profit` 作兜底。

**持有收益展示：** 盘中 `≈ 昨日结算持有收益 + 当日板块估算`；官方净值公布后直接使用 OCR/档案 `holding_profit`（已含当日），不再叠加 `daily_profit`。

**关键实现：**

- **`fund_nav_service.py`：** `get_official_nav_return()` 取 AkShare 日增长率；`compute_yesterday_profit_from_official_nav()` 算上一交易日收益。
- **`sector_quote_service.refresh_holdings_sector_quotes()`：** 官方 NAV 写入 `daily_return_percent` / `daily_profit` / `daily_return_percent_source`；**不**覆盖 `sector_return_percent`。
- **`holding_estimates.py`：** `overlay_official_nav_returns`（恢复持仓时补官方净值）、`compute_official_daily_profit`、`enrich_holdings_yesterday_profits`。
- **`holdingMetrics.ts`：** `applySectorDailyEstimate` 保留 `official_nav`；`computeDailyProfit` / `computeHoldingProfit` 与后端一致。
- **`YangjibaoHoldingsBoard`：** 估算当日 + 昨日收益子行；关联板块列独立展示东财涨跌；日期取自 `GET /api/trading-session` 的 `effective_trade_date`。
- **`holding_amount_sync.py`：** OCR 后 `bootstrap_holding_baselines(skip_network=True)` 锁定 `settled_holding_amount`；仅官方净值公布时滚入 `shares × 最新净值` 并重算 `holding_profit`（固定 `profile.holding_cost` 为成本基数）；同日幂等跳过认 `profit_settled_trade_date`（**不再**认 `amount_includes_today` / OCR 自洽恒等式）；刷新/恢复时盘中锁定结算额。

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

---

## 外部数据源一览

> **说明：** 项目使用 **新浪财经（Sina Finance）** 的行情/日历接口，**不使用新浪微博** 社交舆情。北向/南向资金走 **东方财富（东财）**，不是新浪。

| 数据 | 实现 | 上游来源 | 用途 |
|------|------|----------|------|
| **沪深港通资金** | `market_flow_client.py` | AkShare `stock_hsgt_fund_flow_summary_em()`（东财沪深港通汇总） | 日报/荐基 `stock_connect_flow` 仅提供可按交易日对齐的 `southbound_net_yi`；北向实时净买额不再披露，只保留 `northbound_status/reason` 审计元数据；缓存 `sector_quote_cache` 盘中 30min / 收盘 1h |
| **板块主力资金** | `board_fund_flow_history.py` + `sector_fund_flow_context.py` | 东财 `push2his` `fflow/daykline/get` | 市场 Tab 板块资金流历史；日报持仓行 / 荐基 `target_sector_context.sector_fund_flow` |
| **板块现货/热度** | `eastmoney_spot_client.py` / `theme_board_snapshot.py` | 东财 `push2delay` / `push2` | 持仓板块涨跌、主题榜、荐基 `sector_heat` |
| **A 股交易日历** | `trade_calendar_cache.py` | AkShare `tool_trade_date_hist_sina()` | 盈亏日历非交易日收益 0、今日「未更新」判定 |
| **指数日线 K 线** | `index_daily_client.py` | HTTP `money.finance.sina.com.cn` `CN_MarketData.getKLineData` | 沪深300 基准、业绩走势对比、板块日 K 备用 |
| **美股指数（备用）** | `us_index_client.py` | 主东财 `push2delay`；备 AkShare `index_us_stock_sina` | 市场 Tab 美股概览 |
| **美元/CNY（备用）** | `us_forex_client.py` | 主百度 `fx_quote_baidu`；备 AkShare `currency_boc_sina`（中行牌价） | 美股 Tab 汇率 |
| **新闻** | `news_service.py` | 东财 `stock_news_em`、财联社 `cls_news_client`、基金公告、宏观主题 | 日报/荐基 `news_titles` + `topic_briefs` |
| **基金净值/名称** | `fund_data.py` / `fund_code_resolver.py` | 天天基金、东财 `fund_name_em` 等 | 持仓、候选池、查码 |

---

## 新闻与 LLM 上下文（日报）

- **数据源（`FUND_AI_NEWS_SOURCES`）：** `eastmoney`（东财 `stock_news_em`）、`cls`（财联社）、`announcement`（基金公告）、`macro`（宏观主题，默认「上证指数」）。
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
| `decision_style=conservative` | 去掉 `stock_connect_flow`、`signal_backtest`（持仓级与组合级）、`prompt_tuning`；**保留**精简 `sector_intraday`（4 字段） |
| `analysis_mode=fast` | 再去 `portfolio_trend`；`topic_briefs` 为 minimal |

- **引用校验：** `news_citation` 守卫用完整 `market_news` 列表校验 `news_bullish`/`news_bearish`，不依赖 slim 后的 `news_titles`。
- **Tool：** 仅深度模式且 `news_tool_max_rounds > 0` 时注册 `fetch_market_news`（默认最多 3 轮）；Tool 补拉后 `merge_topic_briefs` 增量摘要。
- **缓存：** `news_cache` 表按 `topic+date` 同日复用。
- **兜底：** JSON 解析失败 → `_offline_report` + `recommendations.enrich_*`。

---

## 喂模型 user JSON（荐基）

`discovery_payload.build_user_payload()`（与日报对齐市场侧上下文；**不含**逐持仓 NAV/盈亏）：

| 字段 | 说明 |
|------|------|
| `today` / `focus_sectors` / `scan_mode` / `profile` | 扫描上下文 |
| `news_titles` / `topic_briefs` | 与日报同源 `compact_*`；fast 模式 brief 为 minimal |
| `discovery_facts` | `session`、`portfolio_gap`、`sector_heat`、`sector_opportunities`（双轨方向+资金流，不含 20 日位置/量能）、`target_sector_context`（每板块：热度+`sector_fund_flow`+`sector_intraday`+`signal_backtest`）、`stock_connect_flow`、`portfolio_snapshot`、`data_evidence`、`news`（含 `freshness_label`）、`candidate_factor_scores`、`selection_strategy`、`dip_swing`、`candidate_pool`（slim，含 `fund_quality_score` / `quality_reasons`） |
| `requirements` | 全市场 vs 缺口补全两套；须按 `data_evidence` 引用南向 `stock_connect_flow` / `target_sector_context` / 可引用标题 |

实现：`discovery_facts.py` + `discovery_sector_context.py`；存档仍保留全量 `discovery_facts`。

---

## 前端要点

- **简报 Tab：** `TodayBriefing` 组合 KPI + 板块脉搏 + 决策卡 + 内联追问；可跳转持仓/市场/日报。
- **持仓 / 生成日报 Tab：** 持有看板 vs 交易日历 + **AI 角色设定** + 风控画像 + `ReportPanel`。
- **推荐基金 Tab：** `FundDiscoveryPanel`（**扫描模式**含 `dip_swing`、19 板块关注方向、localStorage 热度缓存、荐基角色、基金类型偏好）+ `DiscoveryReportPanel` + `DiscoveryHistoryRail`（批量删除）+ `DiscoveryChatPanel`；`DiscoveryJobStatusFloat` 轮询失败自动重试；大跌雷达「深度扫描」经 `fundpilot-discovery-prefill` + `fundpilot-dashboard-tab` 事件预填并跳转；主题板块「加入关注方向」经 `fundpilot-discovery-focus-sectors` 预填 chips。
- **市场 Tab：** 子 Tab 主题板块 / **大跌雷达** / 美股；`loadMarketSubTab` + `fundpilot-dip-radar-sector` sessionStorage；主题行「看大跌基金」带板块过滤切雷达子 Tab。
- **缓存：** `clientCache.ts` / `useCachedFetch.ts` — 盈亏分析 `sessionStorage`、详情/NAV `memory`；`portfolioHoldingsCache.ts` — 持有 **localStorage** 优先展示；`loadDashboardTab` / `saveDashboardTab` — 主 Tab **sessionStorage**；`loadMarketSubTab` — 市场子 Tab；`loadDiscoverySectorHeatCache` 板块热度 30min；板块 `useSectorQuoteRefresh` 后台 `fast`、手动 `accurate`。
- **认证：** `AuthProvider` 注入 JWT；未登录访问受保护页会跳转 `/login`；`apiFetch` 自动带 `Authorization: Bearer`；CORS 中间件置于最外层（含 401 响应）。
- **用户菜单：** 仅保留**账号设置**（`/settings`）与退出；历史日报由日报阅读区的 `ReportNavigator` / `ReportHistoryDrawer` 管理，荐基历史由 `DiscoveryHistoryWorkspace` 管理；持仓元数据由 OCR 自动维护，无独立档案页。
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
| `FUND_AI_FACTOR_IC_PUBLISH_TOKEN` | — | 因子 IC 发布专用 Token；CloudBase 与 GitHub Secret `FACTOR_IC_PUBLISH_TOKEN` 使用同一随机值，不得复用 JWT/DeepSeek Secret |
| `FUND_AI_FACTOR_IC_STALE_AFTER_DAYS` | 30 | 因子 IC 快照过期提示阈值；过期仍可读，等待下次有效快照替换 |
| `FUND_AI_CLOUDBASE_ENV_ID` | — | CloudBase 环境 ID；用于 Web 静态托管域名 CORS 自动放行 |
| `FUND_AI_CORS_ORIGINS` | `http://localhost:3001,http://127.0.0.1:3001` | 允许的前端 Origin（逗号分隔）；生产设为 Web 静态托管域名 |
| （同上表 `FUND_AI_CLOUDBASE_ENV_ID`） | — | 设后会额外放行 `https://*.webapps.tcloudbase.com`（`config.resolved_cors_origin_regex`） |

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
| `FUND_AI_MARKET_BREADTH_ENABLED` | true | 大盘情绪温度计（M1.1）开关：新高/新低家数（可回测校准）+ 涨跌停/炸板当日快照 + 两融环比 |
| `FUND_AI_MARKET_BREADTH_TIMEOUT_SECONDS` | 4.0 | 情绪温度计计算超时预算 |
| `FUND_AI_FLOW_DIVERGENCE_BACKTEST_ENABLED` | true | 量价背离信号回测（M1.3）开关 |
| `FUND_AI_DECISION_ESCALATION_MODE` | shadow | 双向 guard 灰度开关（M6）：`shadow`（默认）只在 `validation_notes`/`escalation_hints` 标注"若启用会怎样"，不真正改变最终 action/剔除候选/开放新动作词表；`enforced` 真正生效。观察约 1 个月（20 个交易日）后按 `GET /api/diagnostics/shadow-escalation-digest` 摘要自行决定是否切换 |

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
| `FUND_AI_OCR_PROVIDER` | auto | 截图识别引擎：auto（有 key 走云端 VLM 否则本地）/ vlm（强制云端，失败回退本地）/ local（强制本地不外传） |
| `FUND_AI_VLM_OCR_API_KEY` | — | 阿里云百炼 Key；配置后 auto 启用云端识别（截图发往该 API） |
| `FUND_AI_VLM_OCR_BASE_URL` | dashscope compatible-mode | DashScope OpenAI 兼容端点 |
| `FUND_AI_VLM_OCR_MODEL` | qwen-vl-ocr | 文字识别专用模型（稳定版，基于 Qwen3-VL，便宜）；可切 qwen-vl-ocr-latest / qwen3.5-ocr |
| `FUND_AI_VLM_OCR_TIMEOUT_SECONDS` | 20 | VLM 读超时 |
| `FUND_AI_VLM_OCR_MIN_PIXELS` | 3072 | 图像最小像素（小于则放大）；qwen-vl-ocr 默认/最小值 |
| `FUND_AI_VLM_OCR_MAX_PIXELS` | 8388608 | 图像最大像素（大于则缩小）；token 上限兜底（≈8192 图像 token） |
| `FUND_AI_VLM_OCR_COMPRESS_ENABLED` | true | 上传前转 JPEG 压缩（减体积/延迟，不影响 token） |
| `FUND_AI_VLM_OCR_JPEG_QUALITY` | 85 | JPEG 画质 |
| `FUND_AI_VLM_OCR_MAX_IMAGE_SIDE` | 2000 | 仅当最长边超过该值才等比缩小（0=不缩放） |

修改 `.env` 后需重启 API。

---

## 本地开发

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh    # 或 scripts/dev.ps1
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q          # 922 项
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope  # 与 CI 一致
cd apps/web && npm run lint && npm run typecheck && npm run build
cd apps/web && npm run test:e2e:smoke   # CI 同款：桌面/平板/最窄手机三个代表视口
cd apps/web && npm run test:e2e         # 发布前按需跑完整七视口
```

### 测试与 CI

| 项 | 说明 |
|----|------|
| 规模 | 后端 **922** 项、前端 **333** 项单元/组件测试；完整 production UI E2E **63 passed / 21 expected skips**。日常 CI 只跑 desktop-1440、tablet-768、mobile-320 三个代表视口（36 次执行，比七视口 84 次减少 57%），完整七视口保留为 `npm run test:e2e` 手动验收 |
| 离线 | `conftest.py` autouse stub：交易日历、基金名称表、东财 spot/K 线、板块刷新、`build_sector_heat_ranking` 等 |
| 数据库 | 测试强制 `FUND_AI_DATABASE_URL=""` → SQLite 文件库；勿在 pytest 期间连生产 MySQL |
| 超时 | `pytest.ini`：`timeout = 30` |
| 并行 | CI：`python -m pytest tests -q -n auto --dist loadscope`（`pytest-xdist`） |
| CI 环境变量 | `FUND_AI_OCR_PRELOAD=false`、`FUND_AI_NEWS_ENABLED=false`、`FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED=false`、`FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED=false` |
| 保留覆盖 | 核心 API（OCR/分析/荐基）、持仓指标、OCR 解析、discovery 守卫与候选池、`test_api.py` 集成冒烟 |

Workflow：`.github/workflows/ci.yml`（`api` / `web` / `e2e-smoke` 三 job 并行启动；smoke 自带独立 API，不等待单测 job）与 `.github/workflows/factor-ic-refresh.yml`（周度/手动 IC 刷新）。

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
18. 改市场板块：`theme_board_snapshot.py` / `board_fund_flow_history.py` / `dip_radar_snapshot.py` / `dip_drop_scanner.py` / `fund_primary_sector_service.py` / `sector_daily_kline_provider.py` → `main.py` `/api/market/*` → `MarketTab.tsx` / `ThemeSectorOverview.tsx` / `BoardFlowHistoryChart.tsx` / `DipReboundRadar.tsx` / `api.ts` / `marketThemeBoard.ts` / `dipRadar.ts` → `tests/test_theme_board_snapshot.py` / `test_board_fund_flow_history.py` / `test_dip_radar_sector.py` / `test_market_shared_cache.py`；行为与 API 见本文「市场板块」。主题板块后台刷新线程在 `theme_board_snapshot.theme_board_refresh_loop`，由 `lifespan.py` 启动（env `FUND_AI_THEME_BOARD_REFRESH_*`）；刷新 `refresh_theme_board_snapshot()`（缓存 `theme:boards:v3`，并后台预热 `board-flow-hist`）。改关联板块/基准：`fund_benchmark_sector.py` / `amac_benchmark_index_data.py` / `fund_primary_sector_precompute.py` → `scripts/sync_amac_benchmark_index_library.py` / `scripts/precompute_fund_primary_sectors.py`。改日报板块资金流：`sector_fund_flow_context.py` → `analysis_facts.py` / `analysis_payload.py` → `tests/test_sector_fund_flow_context.py` / `test_analysis_facts.py`。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、用户流程 |
| `docs/PROJECT_CONTEXT.md` | **本文** — 架构、API、数据流、环境变量（维护主入口） |
| `docs/deploy/cloudbase.md` | CloudBase 云托管 + MySQL + Web 静态托管 |
| `docs/SECURITY.md` | API Key 与 Secret Scanning |
| `docs/design/holding-metrics-contract.md` | 持有列展示口径（前后端契约 + 共享 fixture） |
| `docs/design/2026-06-04-eastmoney-intraday-troubleshooting.md` | 分时 push2 换机自测、指数映射、脏缓存清理（仅运维时查阅） |
| `.kiro/specs/us-market-overview/requirements.md` | 市场 Tab — 美股概览需求 |
| `.kiro/specs/us-market-overview/design.md` | 市场 Tab — 美股概览设计 |
| `.kiro/specs/us-market-overview/tasks.md` | 美股概览实现计划 |
| `.env.example` | 环境变量模板 |

- **改功能先改 `PROJECT_CONTEXT.md`**：能力清单、API、环境变量、目录结构须与代码同步。
- **`docs/design/`** 保留运维 runbook 与前后端契约（分时 push2 排查、持有列口径）；历史过程稿不再入库，产品决策与实现细节以本文为准。
- **不保留** 已完成的一次性实现计划、清理报告、迭代日志。
