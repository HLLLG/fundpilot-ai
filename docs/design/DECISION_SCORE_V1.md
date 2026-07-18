# DecisionScore v1 影子量化决策预注册

## 目的与边界

DecisionScore v1 用于回答一个受限问题：在荐基流程已经形成的最终候选池内，若只使用决策时点已经冻结、可审计的量化证据，候选基金的研究顺序会不会与当前生产顺序不同。

它不是自动交易模型，也不是收益预测器。当前版本固定为 `shadow_record_only`：

- 不改变候选池、候选顺序、LLM Prompt 或 LLM 输出；
- 不改变质量门、交易门、买卖动作、建议金额或确定性分配器；
- 不向 LLM 数据包暴露评分结果，评分只在现有决策完成后生成并随报告落库；
- 不允许自动晋级，任何线上使用都必须经过独立样本、成熟结果和人工评审。

## 固定版本

- Artifact schema：`decision_score_shadow.v1`
- 模型版本：`decision_score.v1`
- 模式：`shadow_record_only`
- 候选范围：`final_candidate_pool`
- 排序规则：总分降序，同分时按六位基金代码升序
- 缺失规则：不插值、不填 0、不按剩余指标重新归一化权重

## 前置硬门

候选必须同时满足以下条件，才有资格计算分数：

1. 基金代码为有效六位代码；
2. `quality_gate.status=eligible`；
3. 当前申赎、限额和费用证据通过 `tradeability_gate`；
4. 以策略最短持有期计算的费用上界可以执行。

任一硬门失败时，候选状态为 `hard_gate_blocked`，分数保持 `null`。评分绝不能覆盖适当性、质量、流动性、集中度或费用门禁。

## 固定公式

所有分项分数均位于 0～100，分项置信度位于 0～1：

```text
BaseScore = 0.30 × FactorPeer
          + 0.25 × BenchmarkConsistency
          + 0.20 × DownsideControl
          + 0.15 × PortfolioDiversification
          + 0.10 × CostEfficiency

DataConfidence = 0.30 × FactorConfidence
               + 0.25 × BenchmarkConfidence
               + 0.20 × DownsideConfidence
               + 0.15 × DiversificationConfidence
               + 0.10 × CostConfidence

DecisionScore = BaseScore × DataConfidence
```

五个分项缺一不可。任何分项不可用时，候选状态为 `insufficient_evidence`，不产生 BaseScore、DataConfidence 或 DecisionScore，也不重新分配该分项权重。

## 五个分项

### 1. FactorPeer，权重 30%

只接纳满足全部条件的因子证据：

- Factor IC 快照状态为当前、可用且 `confidence_eligible=true`；
- schema 至少为 v3；
- 目标基金通过 `pit_v3_statistical_and_economic` 统计与经济双门；
- 分类型因子完整，且存在至少一个执行合格因子键；
- 使用目标基金的同类综合分，不进行跨类型直接比较。

成员 PIT、NAV 修订未冻结时置信度固定为 0.8；完整 NAV observation PIT 才可取 1.0。旧版、过期、损坏、未覆盖或只具描述性的因子证据全部缺失处理。

### 2. BenchmarkConsistency，权重 25%

仅使用已经通过同类样本资格、覆盖率与点时审计的 `peer_rank.v2` 百分位：

- 主动股票 / 混合：正式基金合同基准近一年超额；
- 被动指数：跟踪误差与跟踪差的同类百分位均值；
- 增强指数：正式基准超额、跟踪误差与跟踪差的同类百分位均值。

债券、QDII、FOF 等当前没有预注册的正式“基准一致性”组合，因此 v1 对这些类型保持缺失，不用普通收益率冒充基准能力。可用分项置信度固定为 0.9。

### 3. DownsideControl，权重 20%

同样只使用合格的类型内 `peer_rank.v2` 百分位：

- 主动股票 / 混合：近一年最大回撤与下行捕获百分位均值；
- 债券、指数、QDII、FOF：近一年最大回撤百分位。

百分位已按指标方向统一为“越高越好”。样本不足、覆盖不达标或指标不适用时保持缺失；可用分项置信度固定为 0.9。

### 4. PortfolioDiversification，权重 15%

当前只使用决策前组合的板块容量代理；组合中所有现有持仓的板块暴露必须完整，否则该分项保持缺失：

```text
SectorLimit = 当前组合权重分母 × 用户单板块集中度上限
Utilization = min(当前该板块持仓金额 / SectorLimit, 1)
DiversificationScore = 100 × (1 - Utilization)
```

该分项置信度固定为 0.75。它不是相关性、协方差或边际风险贡献，也不读取 LLM 建议金额，避免先使用当前决策结果再评价当前决策。未来如为全部候选冻结同一套点时 NAV 与协方差矩阵，应另起模型版本替换本代理。

### 5. CostEfficiency，权重 10%

以当前可执行起购金额和策略最短持有期计算申购、赎回及销售服务费的标准费率上界，再在相同 `metric_profile` 的硬门合格候选中计算“费用越低越好”的中位秩百分位。只有一个合格候选时分数为 50；费用任一必要组成缺失时不评分。可用分项置信度固定为 0.9。

## P0 因子置信修复

因子快照仍可为了诊断而显示原始 `available=true`，但只有通过当前版本化 `FactorIcSummary` 完整校验、未过期且无需契约升级时，才会得到 `confidence_eligible=true`。旧 v1 小样本、参数不合规、内容损坏或过期快照会携带结构化阻断原因，并在消费上下文中统一降为 `state=unavailable`；其 `factors` 与 `research_model` 不得进入评分、置信度或执行白名单。

## 落库、审计与诊断

- Artifact 保存在 `FundDiscoveryReport.discovery_facts.decision_score_shadow`，不新增数据库列。
- 每个候选行和整个 artifact 使用排序 JSON + UTF-8 的 SHA-256；读取诊断摘要时重新校验，而不是信任落库的旧 `validation` 字段。
- Artifact 引用同一决策的 `candidate_selection_audit` schema、哈希、时点和校验状态，以便追溯候选来源。
- 同时保留生产源顺序、可比较的已评分源顺序、影子顺序、Top-K 是否变化、缺失分项计数和覆盖率。
- `GET /api/diagnostics/decision-score-shadow?limit=30` 汇总当前用户最近 1～100 份荐基报告，只返回覆盖与差异摘要，不返回候选明细。

## 晋级门槛

下列条件只允许进入人工评审，不会自动切换线上行为：

1. 至少覆盖 60 个相互独立、结果已成熟的决策日；
2. 可评价标签覆盖率至少 80%；
3. 至少 20 个报告出现可比较的 Top-K 变化，避免只证明“模型与现状相同”；
4. 按冻结的 T+20 / T+60 费后总收益、合同基准超额、MAE、路径最大回撤与 CVaR 做成对比较，并报告置信区间；
5. 股票、混合、指数等适用子组不存在明显反向劣化，费用与换手增加不抵消收益改善；
6. Artifact 校验、缺失率、数据源时点和结果结算均通过人工审计。

任何门槛未满足、样本外结果无改善或尾部风险变差时，继续保持 shadow。若未来改变权重、分项、缺失规则、硬门或候选范围，必须发布新模型版本并重新累计独立样本，不能回写 v1 历史。

## 当前已知局限

- 只重排最终候选池，不能证明上游全市场召回是否遗漏更好的基金；
- 因子主链最多覆盖既有最终候选中的有限数量，严格缺失纪律可能使初期评分覆盖率很低；
- 债券、QDII、FOF 在 v1 缺少预注册的基准一致性分项，因此不会得到完整分数；
- 分散度只是板块容量代理，尚不等于相关性或组合边际风险贡献；
- 费用使用公开标准费率上界，不能冒充用户渠道实际折扣；
- 该模型输出相对研究排序，不输出预期收益率、胜率或买入金额。
