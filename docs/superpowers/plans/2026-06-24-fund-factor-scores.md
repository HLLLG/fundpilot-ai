# 模块2 因子思维（持仓因子体检）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development。每个任务先写失败测试、看它失败、最小实现、看它通过、提交。

**Goal:** 给每只持仓在「排行榜横截面」里打净值系因子分（动量/风险调整/回撤/规模），盈亏分析 Tab 展示 + Pro 门控。

**Architecture:** 纯函数引擎 `fund_factors.py`（去极值→z-score→合成→百分位）← 装配层 `portfolio_snapshot.build_factor_scores_payload`（拉排行榜池 + 持仓净值兜底）← 懒加载接口 `GET /api/portfolio/factor-scores` ← 前端 `PortfolioFactorScoresPanel` + `lib/fundFactors.ts`。

**Tech Stack:** Python 标准库（math/statistics）、FastAPI、pytest + hypothesis；Next.js/React/TS、vitest。

**Spec:** `docs/superpowers/specs/2026-06-24-fund-factor-scores-design.md`（含完整代码骨架，实现以 spec 为准）。

**测试命令：**
- 后端单文件：`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_fund_factors.py -q`
- 后端全量：`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q`
- 前端：`cd apps/web && npx vitest run src/lib/fundFactors.test.ts` / `npm run lint && npm run typecheck && npm run build`
- **git 用真实 git：** `/mingw64/bin/git`（包装器 `.git-ai/bin/git` 在本机 git 2.30 下注入 `--trailer` 会报错）

---

## Task 1: 纯函数引擎 `fund_factors.py`

**Files:**
- Create: `apps/api/app/services/fund_factors.py`
- Test: `apps/api/tests/test_fund_factors.py`

TDD 顺序（每个 helper 一轮 红→绿）：
- [ ] 工具：`_percentile_value` / `_winsorize`（已知答案：极端值被压回）
- [ ] `_factor_stats` + `_zscore`（已知均值/标准差手算；零方差退化为 0）
- [ ] `_percentile_rank`（最高值=100；边界）
- [ ] 原始值：`_blend_momentum`（缺窗口归一）、`_calmar`（取绝对值）、`_size_raw`
- [ ] `_composite_z`（缺因子按剩余权重归一）+ `_grade`
- [ ] 主函数 `compute_factor_scores`（池<30→available=false；持仓不在池用自身原始值；空持仓不崩）
- [ ] hypothesis 不变量：z∈[-3,3]、percentile∈[0,100]

实现代码见 spec 第 5 章（逐字落地）。测试用例见 spec 第 9.1/9.2/9.3。

- [ ] **提交：** `feat(api): 因子打分纯函数引擎 fund_factors + 单测`

---

## Task 2: 装配层 `build_factor_scores_payload`

**Files:**
- Modify: `apps/api/app/services/portfolio_snapshot.py`（与 `build_risk_metrics_payload` 并列）
- Test: `apps/api/tests/test_fund_factors.py`（新增装配层离线用例，注入 `fetch_rank`/`fetch_nav`）

- [ ] 红：写装配层测试——注入构造的 ~40 行假排行榜 + 假净值，断言返回结构（`available=true`、`funds` 长度=持仓数、含 composite_score / factors）。持仓不在榜走 `_target_from_nav`。
- [ ] 绿：实现 `build_factor_scores_payload(holdings_models, *, fetch_rank=None, fetch_nav=None)` + `_target_from_nav`（净值切片算 3/6/12 月收益、复用 `portfolio_risk_metrics._max_drawdown`）。见 spec 第 6 章。
- [ ] 验证：单文件测试全绿。
- [ ] **提交：** `feat(api): 因子打分装配层 build_factor_scores_payload`

---

## Task 3: API `GET /api/portfolio/factor-scores`

**Files:**
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_api.py`（新增接口冒烟，沿用 conftest 离线 stub）

- [ ] 红：写接口测试——登录后 GET，断言 200 + 含 `available` 字段（离线 stub 下 `available=false` 也算通过结构契约）。
- [ ] 绿：加路由（需 JWT，按 userId 取持仓 → `build_factor_scores_payload` → 返回 dict）。参考现有 `/api/portfolio/risk-correlation` 路由写法。
- [ ] 验证：`pytest tests/test_api.py -q` 全绿 + 全量 `pytest tests -q` 全绿。
- [ ] **提交：** `feat(api): GET /api/portfolio/factor-scores 懒加载接口`

---

## Task 4: 前端类型 + 解读 helper `lib/fundFactors.ts`

**Files:**
- Modify: `apps/web/src/lib/api.ts`（类型 + `fetchPortfolioFactorScores`）
- Create: `apps/web/src/lib/fundFactors.ts`
- Test: `apps/web/src/lib/fundFactors.test.ts`

- [ ] 红：vitest 覆盖 `gradeTone` / `momentumHint` / `drawdownHint` / `compositeSummary` 等（边界：percentile=null/0/100）。
- [ ] 绿：实现 helper（话术见 spec 第 3 章解读话术）+ api.ts 类型与 fetch 封装（见 spec 第 7 章）。
- [ ] 验证：`npx vitest run src/lib/fundFactors.test.ts` 全绿。
- [ ] **提交：** `feat(web): 因子解读 helper fundFactors + api 类型`

---

## Task 5: 前端面板 `PortfolioFactorScoresPanel` + Pro 门控

**Files:**
- Create: `apps/web/src/components/PortfolioFactorScoresPanel.tsx`
- Modify: `apps/web/src/components/PortfolioDashboard.tsx`（风险体检面板下方挂载，懒加载）
- Modify: `apps/web/src/app/globals.css`（因子卡片/迷你条样式，复用现有 token）

- [ ] 实现面板：展开懒加载 `fetchPortfolioFactorScores`；每只持仓卡片（综合分+等级+因子迷你条+解读）；空态用 `message`。
- [ ] Pro 门控：免费显综合分+等级+动量，Pro 解锁其余（复用模块1 `isPro` + `.plan-card.is-pro` 蒙层）。
- [ ] 验证：`npm run lint && npm run typecheck && npm run build` 通过。
- [ ] **提交：** `feat(web): 持仓因子体检面板 + Pro 门控`

---

## Task 6: 文档同步 + 全量验收

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`（更新记录 + 能力清单「组合风险体检」行补因子 + API 表 + 目录）

- [ ] 更新 PROJECT_CONTEXT.md。
- [ ] 全量验收：后端 `pytest tests -q` 全绿；前端 `lint/typecheck/build` 通过；vitest 全绿。
- [ ] **提交：** `docs: 模块2 因子体检接入 PROJECT_CONTEXT`

---

## Self-Review

- **Spec 覆盖：** 纯函数(T1)/装配(T2)/API(T3)/前端类型+helper(T4)/面板+Pro(T5)/文档(T6) 对齐 spec 第 10.1 任务表 1–9。✓
- **类型一致：** `FundFactorInput`/`FactorDetail`/`FundFactorScore`/`FactorScoreResult`（后端）与 `FactorDetail`/`FundFactorScore`/`FactorScoresResponse`（前端）命名与 spec 第 5、7 章一致。✓
- **无占位：** 实现代码逐字以 spec 为准，无 TBD。✓
