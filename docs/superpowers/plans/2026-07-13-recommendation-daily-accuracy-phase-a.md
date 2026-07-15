# 荐基 / 日报准确性 Phase A 实施计划

> 设计依据：`docs/design/RECOMMENDATION_DAILY_ACCURACY_V4.md`
> 状态：A1 已验收；A2 已完成实现，最终全量回归与 smoke 验证收尾中
> 目标：先修复已确认的确定性错误、入口漂移和名实不符；不采购新数据、不调整长期因子权重、不启用主报告 LLM 自主新闻工具轮次。
> A1 验证：API `1291 passed`；Web `389 passed`，typecheck、lint、production build 通过；smoke API `3 passed`、三视口 UI `30 passed / 6 expected skips / 0 failed`

## 1. 发布边界

Phase A 分为两个连续、可独立回滚的批次：

- **A1 确定性不变量**：基金身份、候选匹配来源、金额硬约束、动作集合、新闻排序/时间。
- **A2 证据契约与可观测性**：基金定期报告预取、Prompt 安全附录、实际运行快照、provider 参数一致、深度模式说明、故障兜底。

不在本阶段实现：

- 真实销售平台折扣、完整份额费用比较和申购状态门禁（Phase B）。
- 新的基金同类组/因子权重和自动调参（Phase B/D）。
- 国内基金 NAV nowcast、完整持仓归因和 Claim Validator（Phase C）。
- 主报告 agentic tool rounds、多 Agent 自动决策或自动交易。

## 2. 当前入口与唯一收敛点

| 功能 | 后台/服务入口 | SSE 入口 | 最终业务收敛点 |
| --- | --- | --- | --- |
| 日报 | `/api/analyze`、`/api/analyze/async` → `run_analysis()` → `DeepSeekClient.generate_report()` | `/api/analyze/stream` → `stream_analysis()` | `deepseek_client._build_final_report()` |
| 荐基 | `/api/fund-discovery/async` → `run_discovery()` → `DiscoveryClient.generate_report()` | `/api/fund-discovery/stream` → `stream_discovery()` | `discovery_client.build_discovery_report_from_parsed()` |

`job_store.py` 和 `discovery_job_store.py` 只负责委托和状态，不复制业务规则。所有不变量必须进入上述共享 service/收敛点；否则同步、SSE 和后台会继续漂移。

已确认的入口参数漂移：荐基后台 `_call_model()` 使用 temperature `0.3` 且未传报告 max tokens，SSE 使用共用 payload helper 的 `0.2` 和显式 max tokens。

## 3. A1：确定性不变量

### Task A1.1：保留荐基行业匹配来源

涉及文件：

- `apps/api/app/services/discovery_candidate_pool.py`
- `apps/api/app/services/discovery_candidate_llm.py`
- `apps/api/tests/test_discovery_candidate_pool_opportunity.py`
- `apps/api/tests/test_discovery_quality_v2.py`
- 新增 `apps/api/tests/test_discovery_sector_match_provenance.py`

改动合同：

1. 将私有 `_sector_match_kind` 变成公开审计字段 `sector_match_kind`。
2. 枚举限制为 `primary | name | new_issue | fallback`。
3. 召回、合并、enrichment、finalize、LLM slim 和持久化全程保留该字段。
4. `_sector_fit_score()` 以正式字段为准，短期兼容旧测试/缓存里的私有字段。
5. `quality_score_version` 升为 `fund_quality.v3`，旧报告继续保留旧版本。

硬验收：

- 主映射置信度 `.8` 的候选补全前后匹配来源不变，匹配分保持 `36.8`。
- 名称匹配仍为名称匹配，不能误升为主映射。
- `召回→enrich→finalize→Guard` 后，不再因来源丢失出现“板块匹配置信偏低”。

### Task A1.2：日报 recommendation 有序一一闭包

涉及文件：

- `apps/api/app/services/recommendations.py`
- `apps/api/app/services/report_judge.py`
- `apps/api/app/services/deepseek_client.py`
- 新增 `apps/api/tests/test_daily_recommendation_canonicalizer.py`
- 相邻回归：`test_recommendations_structured_fields.py`、`test_analyze_streaming.py`

新增纯函数：

```text
canonicalize_fund_recommendations(
  draft,
  authoritative_holdings,
  *,
  fallback_factory,
) -> CanonicalizationResult
```

规则：

1. 为输入持仓生成内部稳定 `holding_key`：合法且唯一的基金代码优先，否则使用输入索引生成，不把多个 `000000` 合并。
2. 只接受服务端权威持仓；校正错误名称，删除池外基金。
3. 固定按服务端持仓顺序输出，每个 `holding_key` 恰好一条。
4. 漏项复用现有本地规则建议补齐，默认低置信、无凭空金额，并记录 validation note。
5. 同一持仓重复且动作冲突时，不沿用当前“更激进动作优先”；清除金额并降为 `风控复核/观察`，记录冲突。
6. judge 前先规范草稿；judge 后、持久化前再次 canonicalize。最终 Guard 后断言一一映射不变量。
7. 离线报告走同一 canonicalizer。

硬验收：

- 乱序、重复、漏项、池外代码、错名和多个 `000000` 均得到有序一一映射。
- canonicalizer 幂等。
- 属性测试证明任意脏 LLM 数组都不能产生持仓外 recommendation。
- judge 不能重新引入池外基金。

### Task A1.3：荐基金额硬上限

涉及文件：

- `apps/api/app/services/discovery_guard.py`
- `apps/api/app/services/discovery_facts.py`（仅复用/补充必要真值）
- `apps/api/tests/test_discovery_guard_escalation.py`
- `apps/api/tests/test_discovery_decision_output.py`

新增纯函数：

```text
resolve_discovery_amount_cap(
  portfolio_truth,
  holdings_slim,
  candidate_sector,
  allocated_by_sector,
  request_budget,
  concentration_limit,
) -> AmountCapResult
```

硬上限：

```text
min(
  剩余请求预算,
  已确认可用现金,
  concentration_limit × 现有 resolve_weight_denominator 口径
    - 已有同板块金额
    - 本轮已分配同板块金额
)
```

规则：

- `boost` 只影响软目标，最终仍必须 `min(soft_target, hard_cap)`。
- 同板块暴露、现金或仓位真值无法核验时不得按 0；清金额并降为关注。
- cap 小于最小可执行金额时降为关注。
- Phase A 继续允许 LLM 建议额作为待校正输入；多候选最优分配留到 Phase B。

硬验收：

- 修正现有“30% 可 boost 到 36%”测试；enforced 也不得突破硬上限。
- 覆盖已有板块接近/超过上限、多个同板块候选、现金小于预算、未知现金/板块、NaN/Inf/负金额。

### Task A1.4：新闻时间、排序与去重

涉及文件：

- `apps/api/app/models.py`
- `apps/api/app/services/news_service.py`
- `apps/api/app/services/news_freshness.py`
- `apps/api/app/services/news_summarizer.py`
- `apps/api/app/services/recommendations.py`
- `apps/api/tests/test_news_service_prefetch.py`
- 新增 `apps/api/tests/test_news_decision_context.py`

规则：

1. 统一解析 ISO offset、`Z`、上海本地时间、纯日期和未知时间；“今日”以决策时点的 Asia/Shanghai 日期判断。
2. 排序为：今日优先 → 已知时间优先 → 同组最新优先 → 来源/标题稳定 tie-breaker。
3. 去重在 Top-K 截断前执行；优先规范化 URL，无 URL 时使用规范化标题 + 来源，key 不再包含 topic。
4. 重复文章合并 `related_topics`，不丢失基金/板块关联。
5. 缓存命中后也重新按当前决策时点排序。

硬验收：

- 同日 `14:20 > 11:00 > 09:30 > 今日未知时间`。
- 同一文章跨主题只占一个 Top-K 槽位。
- 并发 future 完成顺序不影响最终顺序。

### Task A1.5：动态动作与交易时点

涉及文件：

- `apps/api/app/services/analysis_facts.py`
- `apps/api/app/services/analysis_payload.py`
- `apps/api/app/services/analysis_prompt.py`
- `apps/api/app/services/deepseek_client.py`
- `apps/api/tests/test_analysis_payload_bundle.py`
- `apps/api/tests/test_decision_escalation_mode.py`

规则：

- 动作唯一合法来源为 `analysis_facts.allowed_actions`。
- 删除“五选一”和固定 14:30/15:00 的全局角色叙述；使用动态 session/decision window。
- shadow/enforced 均只接受本次 facts 真正给出的集合。

硬验收：

- 有效 Prompt 不再包含固定动作数量或固定更新时间。
- shadow 五档与 enforced 扩展档都无 Prompt 冲突。
- 盘前、盘中、收盘后、非交易日口径正确。

## 4. A2：证据契约与可观测性

### Task A2.1：基金定期报告独立预取

涉及文件：

- `apps/api/app/config.py`、`.env.example`
- `apps/api/app/services/eastmoney_news_client.py`
- `apps/api/app/services/news_service.py`
- `apps/api/app/services/news_freshness.py`
- `apps/api/app/services/deepseek_client.py`
- `apps/api/app/services/analyze_streaming.py`
- `apps/api/app/services/discovery_pipeline.py`
- `apps/api/app/services/discovery_streaming.py`
- 新增/扩展新闻与 pipeline 测试

规则：

- `announcement` 当前只接入 AkShare `fund_announcement_report_em` 的基金定期报告，不宣称覆盖经理变更、临时公告、清盘提示等广义基金公告。
- 定期报告拥有独立的最大基金数、每基金条数、缓存和总超时，不占 `news_max_topics`。
- 日报对权威真实持仓代码预取；荐基只对最终候选 Top-N 预取，不查询 prescreen/全市场目录。
- 返回兼容 list 使用的 `items`，并记录 requested/ok/empty/error/timeout、覆盖率、`fetched_at`、每基金最新定期报告时间。
- `empty` 与 provider failure 必须区分；部分失败保留已成功市场新闻和定期报告。

### Task A2.2：恢复紧凑画像/基金事实

涉及文件：

- `apps/api/app/services/analysis_payload.py`
- `apps/api/tests/test_analysis_payload_bundle.py`

规则：

- `slim_profile_for_llm()` 始终保留 `style`、`horizon`。
- LLM holding facts 恢复 `fund_type`。
- `fund_scale_yi` 必须连同来源/时点/新鲜度；缺少这些元数据时保持不可作为强动作依据。
- `management_fee` 若恢复，字段名/说明明确“已体现在净值的经常性费用，不是本次申赎费用”，禁止重复扣费。
- 保持标量和 payload 体积回归测试；真实份额费用留在 Phase B。

### Task A2.3：Prompt 安全附录与旧数据兼容

涉及文件：

- `apps/api/app/services/analysis_prompt.py`
- `apps/api/app/services/discovery_prompt.py`
- `apps/web/src/components/RolePromptEditor.tsx`
- `apps/web/src/components/RiskControls.tsx`
- `apps/web/src/components/FundDiscoveryPanel.tsx`
- 新增 `apps/api/tests/test_prompt_contract.py`

规则：

- 不可覆盖系统契约与用户偏好分层组合；偏好不能覆盖事实、动作、金额、引用和 schema。
- 暂不改 `role_prompt/system_role_prompt` API 与数据库列名；服务端把它解释为 appendix。
- 请求值恰好等于当前默认模板时视为空附录，兼容日报总是发送默认模板的旧行为。
- 已保存旧自定义全文不做破坏性迁移，作为明确标记的 legacy appendix 包裹；UI 提供重置和新语义提示。
- 附录长度限制只截附录，不截系统契约；恶意附录仍由 canonicalizer/Guard 硬兜底。

### Task A2.4：统一 provider request 并冻结运行合同

建议新增：

- `apps/api/app/services/prompt_provenance.py`
- 共用 provider request builder（可放现有 `deepseek_client.py` 后再按职责拆分）

涉及现有文件：

- `analysis_runtime.py`
- `deepseek_client.py`、`deepseek_streaming.py`
- `discovery_client.py`、`discovery_streaming.py`
- `analyze_streaming.py`
- `report_pipeline.py`
- `decision_contract.py`
- 新增 `test_prompt_provenance.py`、扩展持久化测试

`prompt_contract.v1` 至少冻结：

```text
template_version / template_snapshot / template_hash
user_appendix_snapshot / user_appendix_hash
effective_system_prompt_hash
user_payload_hash / effective_messages_hash
analysis_mode / model / temperature / max_tokens / response_format
news_retrieval_policy / news_tool_rounds_configured / executed
judge_mode / attempted / applied
decision_escalation_mode / policy_version
```

规则：

- 荐基后台与 SSE 使用同一 request builder，统一 temperature `0.2`、max tokens 和 response format。
- discovery `_judge_meta` 不再丢弃，写入 `discovery_facts.pipeline`。
- Prompt 组件快照和 hash 进入现有报告/DecisionEvent JSON；不新增 DDL，不把敏感 header、API key、原始 provider error body 写入日志或事件。
- 快照沿用当前报告访问控制；用户附录不进入普通日志。
- DecisionEvent 继续为 `decision_event.v2`，避免现有 settlement 精确版本判断排除新事件。
- 升级 `POLICY_VERSION`、日报/荐基 Prompt template version 和基金质量版本。

硬验收：

- hash 由实际发送给 provider 的 messages 捕获计算，而非事后猜测重建。
- 同输入稳定；附录、模型、温度、模式改变对应组件 hash。
- SQLite round-trip 后快照/hash 不变。
- 后台与 SSE 的 provider 参数及最终 Guard 语义一致。

### Task A2.5：深度模式名实一致

涉及文件：

- `analysis_runtime.py`、`report_pipeline.py`
- `deepseek_client.py`、`discovery_client.py`
- `apps/web/src/components/AnalysisModeToggle.tsx`
- `apps/web/src/components/FundDiscoveryPanel.tsx`
- `apps/web/src/components/Dashboard.tsx`
- `.env.example`

规则：

- 主报告深度模式为 `Pro + bounded_prefetch + 可选 judge`，实际 tool rounds 为 0。
- configured rounds 和 executed rounds 分开记录，不能把配置值写成已执行。
- Prompt 不再声称可以调用未挂载工具；UI 不再暗示自主浏览。
- 保留未调用 helper 一版兼容；报告追问链已有工具能力不在本项改造范围。

### Task A2.6：Provider 失败降级

涉及文件：

- `discovery_client.py`
- `discovery_streaming.py`
- `discovery_offline.py`
- `deepseek_http.py`
- 日报 SSE 对应离线收敛点
- 新增 `test_provider_fallback_contract.py`

规则：

- timeout、429/5xx、空内容、脏 JSON 均映射为脱敏错误分类。
- 荐基返回 `provider=offline-fallback`，记录尝试模型和失败类别；动作只允许关注/等待、金额为空、低置信、execution blocked。
- SSE 零 token 失败时生成并保存 fallback 后发 `done`；有合法 partial 时允许 salvage，但仍走最终 Guard。
- 后台任务由 service fallback 自动变为 completed，不在 job store 重写逻辑。
- 日报 SSE 与后台采用相同离线语义。

### Task A2.7：单请求决策时钟贯穿

涉及文件：

- `analyze_pipeline.py`、`analyze_streaming.py`、`deepseek_client.py`
- `analysis_payload.py`、`analysis_facts.py`
- `discovery_pipeline.py`、`discovery_streaming.py`、`discovery_facts.py`
- `news_service.py` 及潜在 tool-round helper

规则：

- 四个 runner 在 worker/请求实际开始时只捕获一次上海时区 `decision_at`。
- 新闻预取与去重、交易 session、facts.news、user payload、最终 facts overlay 和 tool-round 必须复用该时点。
- 增加跨 `23:59:59 → 00:00:01` 的 service/SSE 回放，断言 cache 日期、`is_today`、`session.calendar_date`、`facts.news.calendar_date` 与 `payload.today` 一致。
- 该边界当前只会把证据降级为 stale 并触发更保守动作，列为 P2；A1 不以局部签名补丁冒险绕过线程池和 tool-round。

## 5. Phase A 测试矩阵

建议新增：

1. `test_daily_recommendation_canonicalizer.py`
2. `test_discovery_sector_match_provenance.py`
3. `test_news_decision_context.py`
4. `test_prompt_contract.py`
5. `test_analysis_runtime_contract.py`
6. `test_provider_fallback_contract.py`
7. `test_phase_a_path_consistency.py`

路径一致性 fixture 固定 holdings、candidate pool、facts、LLM draft 和预期语义投影，参数化：

- fast/deep；
- 日报 service/SSE/background；
- 荐基 service/SSE/background；
- provider 正常/失败；
- shadow/enforced。

只比较稳定投影：身份/顺序、动作、金额、execution block、Guard notes、provider 状态、Prompt/runtime hash；不比较 ID、时间、耗时或自由文案。

故障注入要求：

- provider 在引用模块处 monkeypatch；显式 placeholder key，禁止读真实 Key。
- 新闻并发使用 Event 和短 timeout，不使用长 sleep。
- 落库只使用 `tmp_path` 数据库；测试前后校验业务数据库文件无变化。
- 429/5xx、连接/读取超时、空内容、脏 JSON、部分流、定期报告部分失败/超时均覆盖。

## 6. 迁移、兼容和回滚

- 不需要 SQLite/MySQL DDL。
- 旧 `role_prompt` 列/API 字段保留；仅服务端组合语义变化，旧全文被标记为 legacy appendix。
- News/candidate/report/prompt metadata 都是可选 JSON 增量字段，旧缓存和旧报告继续读取。
- 旧报告保留 `fund_quality.v2`；新报告使用 `fund_quality.v3`。
- DecisionEvent 保持 `decision_event.v2`，只增加可选 `prompt_contract`。
- 每个 Task 尽量由独立纯函数和版本开关隔离；若发生回归，可回滚新 policy/prompt/quality 版本，不改历史报告和事件。
- Provider fallback 只产生不可执行观察，不进入买入命中分母。

## 7. 验收命令与基线

当前改动前基线（2026-07-13）：

- API：`1208 passed`。
- Web：`388 passed`。
- Web typecheck、lint：通过。

Phase A 新增测试（`apps/api`）：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_daily_recommendation_canonicalizer.py `
  tests/test_discovery_sector_match_provenance.py `
  tests/test_news_decision_context.py `
  tests/test_prompt_contract.py `
  tests/test_analysis_runtime_contract.py `
  tests/test_provider_fallback_contract.py `
  tests/test_phase_a_path_consistency.py `
  -q -p no:cacheprovider
```

相邻回归后运行：

```powershell
# apps/api
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pytest -q

# apps/web
npm.cmd test
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
npm.cmd run test:e2e:smoke
```

最终硬门槛：

- 新增、相邻和全量测试全部通过。
- 无真实网络调用、无真实 Key、无业务库变化。
- 日报 recommendation 与服务端持仓有序一一对应。
- 荐基匹配来源补全后不退化。
- 荐基金额不突破现金、预算、已有/本轮同板块集中度。
- 基金定期报告成功、空结果、失败和超时可区分。
- 自定义附录不能绕过确定性 Guard。
- 同一模式的 service/SSE/background 最终硬约束语义一致。
- 所有版本、Prompt 组件和实际运行参数可审计、可重放。

## 8. 人工确认与执行记录

原计划的 A1/A2 人工确认门均已满足：用户先验收 A1，随后授权后续按推荐方案自主执行、不再逐项询问。只有真实外部阻塞或需要扩大授权边界时才重新请求人工决策；发现回归或证据不足仍须先修正，不带病交付。

**A1 执行记录（2026-07-13，已验收）：** A1 已完成实现、多轮独立对抗复审、service/SSE/background 一致性回放和全量回归。最终验证为 API `1291 passed`，Web `389 passed`，typecheck、lint、production build 通过；smoke API `3 passed`，三视口 UI `30 passed / 6 expected skips / 0 failed`。审计中发现的持仓/重复代码身份覆盖、placeholder 跨绑、Judge 权重覆盖、执行文本与 raw partial 旁路、否定动作反转、金额/同板块别名上限、新闻来源归属、CLS 时间分列/旧缓存/oldest-first、Top-K 与 limit 缓存污染均已关闭；用户已明确验收通过。

**A2 执行记录（2026-07-14，已完成自主验证）：** A2.1～A2.7 已进入代码：基金定期报告使用独立预算、缓存和结构化覆盖状态；风控画像与持仓事实恢复安全字段；系统 Prompt、用户附录及实际 provider messages/参数形成可持久化 `prompt_contract.v1`；主报告深度模式明确为 Pro + `bounded_prefetch.v1` + 可选 judge，自主新闻工具实际轮次为 0；provider timeout、限流/服务错误、空内容和脏结构统一脱敏并 fail-closed 降级；四个 runner 复用单一上海时区 `decision_at`。最终验证为 API `1400 passed`，Web `397 passed`，typecheck、lint、production build 通过；smoke API `3 passed`、三视口 UI `30 passed / 6 expected skips / 0 failed`；真实 AkShare 冒烟确认 519674 基金概览可返回净资产规模/披露日/管理费，基金定期报告按最新日期优先返回。
