# 因子 IC 回测数据自动刷新（GitHub Actions 外部计算 + API 发布）— 设计方案

**状态：** 2026-07-10 重新调研并与用户逐节确认，未实现
**替代方案：** 本文替代原先基于 CloudBase 后台 daemon 的 C3 设计
**范围：** GitHub Actions、`apps/api` 快照发布/读取/诊断、`apps/web` 状态展示
**关联文档：** `docs/TODO_factor_ic_refresh.md`、`docs/PROJECT_CONTEXT.md`

---

## 0. 结论

采用方案 A：**GitHub Actions 在生产容器外运行因子 IC 回测，通过受保护的内部 API 将通过质量门槛的结果发布到 CloudBase MySQL。**

不再采用旧设计中的 CloudBase `lifespan` 后台 daemon、轮询线程和数据库分布式锁。当前生产云托管配置为 2～5 个自动伸缩实例。腾讯云官方说明，脱离请求运行的后台线程可能在实例缩容时被终止；可靠定时任务需要固定实例运行，因此 daemon 不能作为该数据的唯一刷新机制：

- [腾讯云：云托管 CloudBase Run 服务开发说明](https://cloud.tencent.com/document/product/1243/53551)
- [腾讯云：云托管 CloudBase Run 版本配置说明](https://cloud.tencent.com/document/product/1243/49177/)

GitHub Actions 已是本项目现有 CI/前端部署基础设施；API 服务又会在 `main` 推送后自动构建发布。外部计算方案不占用线上 API 的 CPU、线程和 AkShare 请求额度，也不会被实例扩缩容打断。

---

## 1. 背景与现状

`apps/api/scripts/run_factor_ic.py` 生成 `var/factor_ic/summary.json`，`factor_confidence.py::load_ic_summary()` 读取其中的 `factors`，给持仓因子分（动量、风险调整、回撤）挂上“是否可回测、置信高低”标签。这是日报“量化证据卡”三路证据之一。

现状问题：

1. `summary.json` 是本地生成物，受 `.gitignore` 排除；生产镜像只有 `var/factor_ic/.gitkeep`，没有真实快照。
2. 数据没有自动刷新机制，线上因子 IC 置信长期降级为“数据不足”。
3. 原 C3 设计依赖云托管后台线程，与当前 2～5 自动伸缩配置不兼容。
4. 原 C3 设计拟复用 `sector_quote_cache.get_spot_snapshot_any_age()`；该函数命中过进程内 `_MEMORY` 后不会重新查询数据库，其他 worker 发布的新值可能永久不可见。
5. 原锁表草案使用 `lock_key TEXT PRIMARY KEY`，无法直接用于 MySQL/CynosDB；且生产 MySQL 故障时若回落到各容器本地 SQLite，“全局锁”和“共享发布”都会退化为每副本独立状态。
6. 当前 `compute_factor_ic()` 只要净值面板不少于 10 只就返回 `available=True`，即使因子有效回测期数不足；仅凭 `available` 发布会让低质量结果覆盖旧的有效快照。
7. 因子 IC 引擎、置信映射、NAV helper 和分层抽样的核心测试曾在测试精简中被删除，本次需要恢复关键回归保护。

2026-07-10 只读实时探测结果：

- AkShare 当前可返回 500 条开放式基金排行。
- 跨榜单等距抽取的 20 只基金全部取得不少于 250 个净值点。
- 完整 `sampled 500→300`、`nav_days=750` 回测耗时约 51 秒，取得 300 只有效基金、34 个有效因子期，说明 GitHub-hosted runner 足以承担该任务。

---

## 2. 目标与非目标

### 2.1 目标

1. 每周自动生成一次符合固定生产口径的因子 IC 快照，并支持 GitHub Actions 手动补跑。
2. 回测、质量校验或发布失败时保留最后一份有效快照，下一周自动重试。
3. 发布操作幂等、可追溯，并且任何调试小样本都不能意外污染生产数据。
4. 所有 API 副本通过 MySQL 读取同一份最新快照；生产 MySQL 不可用时发布必须失败关闭。
5. 用户能在“持仓因子体检”附近看到生成日期、样本数和过期状态。
6. 恢复这条证据链的关键回归测试，并完成后端、前端和本地端到端验证。

### 2.2 非目标

- 不修改 `factor_ic_backtest.py::compute_factor_ic()` 的 IC 算法、显著性阈值或前视偏差规则。
- 不引入 Celery、Redis、消息队列或 CloudBase 常驻定时实例。
- 不把 `summary.json` 提交进源码仓库，也不因每次数据刷新重新部署 API。
- 不把“不显著”当作回测失败；不显著是应被诚实发布和展示的有效结论。
- 不在本次任务中改造其他已有后台刷新线程。

---

## 3. 总体架构

```text
GitHub Actions（每周 / workflow_dispatch）
  ├─ checkout 默认分支
  ├─ 安装 apps/api/requirements.txt
  ├─ run_factor_ic.py 在 Runner 临时目录生成 summary.json
  ├─ publish_factor_ic.py 本地执行第一层契约/质量校验
  └─ X-Factor-IC-Publish-Token + HTTPS POST
          │
          ▼
POST /api/internal/factor-ic-snapshots
  ├─ 常量时间 Token 校验
  ├─ 服务端再次执行权威契约/质量校验
  ├─ 拒绝重复、旧、未来或低质量结果
  └─ 原子追加到 CloudBase MySQL factor_ic_snapshots
          │
          ├─ factor_confidence：DB 优先，本地文件兜底，进程缓存 5min
          └─ diagnostics：直接读取最新 DB 行，立即反映发布结果
```

保留现有 `run_factor_ic.py` 的计算和本地报告职责。旧 C3 为了让 daemon import 脚本逻辑而计划抽取 `factor_ic_report.py`；方案 A 没有服务端计算调用方，因此不做这项无必要重构。

---

## 4. 回测生成口径

GitHub Actions 固定使用：

```bash
python scripts/run_factor_ic.py \
  --universe-mode sampled \
  --sample-pool-size 500 \
  --universe-size 300 \
  --nav-days 750 \
  --rebalance-step 21 \
  --forward-days 20 \
  --factor-lookback 250 \
  --max-workers 8 \
  --out-dir "$RUNNER_TEMP/factor-ic"
```

相比旧快照的 `top 300`，生产口径采用已实现的 `sampled 500→300`，在 AkShare 当前最多 500 条排行数据的边界内跨业绩段等距抽样，降低只取榜首强势基金造成的选择偏差。它仍然存在当前在榜/幸存者偏差，`caveats` 必须继续保留这一事实。

`run_factor_ic.py` 的输出补充：

```json
{
  "schema_version": 1,
  "run_date": "2026-07-10",
  "generated_at": "2026-07-10T08:23:45.123456+00:00",
  "params": {},
  "available": true,
  "universe_size": 300,
  "rebalance_count": 35,
  "factors": []
}
```

CLI 始终只生成本地文件，不增加隐式生产发布。生产发布只能显式运行 `publish_factor_ic.py` 并提供 Token。

---

## 5. 发布契约与质量门槛

### 5.1 请求体

```json
{
  "summary": {
    "schema_version": 1,
    "run_date": "2026-07-10",
    "generated_at": "2026-07-10T08:23:45.123456+00:00",
    "params": {
      "universe_size": 300,
      "universe_mode": "sampled",
      "sample_pool_size": 500,
      "nav_days": 750,
      "rebalance_step": 21,
      "forward_days": 20,
      "factor_lookback": 250
    },
    "available": true,
    "universe_size": 300,
    "rebalance_count": 35,
    "forward_days": 20,
    "factors": []
  },
  "source_commit": "40-character-git-sha",
  "source_run_id": "github-actions-run-id"
}
```

`caveats`、`message` 和每个因子的其余统计字段沿用现有 summary 契约。`source_commit` 和 `source_run_id` 由 GitHub 环境提供，不由回测引擎生成。

### 5.2 权威质量校验

GitHub 发布脚本和 API 共用同一套纯函数校验，API 是最终权威边界：

1. `schema_version == 1`。
2. 参数严格等于第 4 节的生产口径。
3. `available is True`。
4. `universe_size >= 240`，即目标样本的 80%。
5. `rebalance_count >= 12`。
6. `factors` 恰好包含且只包含 `momentum`、`risk_adjusted`、`drawdown`、`composite`，不可缺失或重复。
7. 每个因子 `n_periods >= 12`、`mean_ic` 非空且在 `[-1, 1]`；存在的 `ic_std`、`icir`、`t_stat` 和 `positive_ratio` 必须是有限数字，`positive_ratio` 位于 `[0, 1]`。
8. 不要求 `significant=True`。
9. `generated_at` 必须含时区、不得超过服务端 UTC 当前时间 5 分钟、不得早于当前时间 24 小时以上；`run_date` 必须等于 `generated_at` 的 UTC 日期。
10. 若数据库已有更新的 `generated_at`，当前请求不覆盖它。

结构错误和质量不达标返回 `422`；已存在更新快照返回 `409`，发布脚本把该响应视为安全跳过；相同 payload 重复发布返回 `200` 且 `created=false`。

### 5.3 幂等标识

服务端以规范化后的 `summary` JSON 加 `source_commit` 计算 SHA-256，作为 `snapshot_id`。相同结果重复提交不会产生重复记录；不同周、不同生成时间或不同源码版本会产生新记录。

---

## 6. 发布安全

内部端点：

```text
POST /api/internal/factor-ic-snapshots
```

- `include_in_schema=False`，不出现在 OpenAPI 文档。
- 请求头使用 `X-Factor-IC-Publish-Token`。
- CloudBase 环境变量：`FUND_AI_FACTOR_IC_PUBLISH_TOKEN`。
- GitHub Secret：`FACTOR_IC_PUBLISH_TOKEN`。
- 用 `secrets.compare_digest()` 做常量时间比较。
- 未配置服务端 Token 返回 `503`；缺失或错误 Token 返回 `401`。
- 该精确路径从普通用户 JWT 中间件豁免，但路由依赖必须先完成发布 Token 校验。
- Token 不写日志、不出现在响应、Actions Summary 或命令行参数中。
- Token 应由 `secrets.token_urlsafe(48)` 一次性生成；轮换时同时更新 CloudBase 与 GitHub。

发布端点只接受一份结构化快照，不接受任意 SQL、文件路径、命令或回测参数，不能被用于远程执行计算。

---

## 7. 持久化模型

新增全局表 `factor_ic_snapshots`，不含 `userId`：

| 字段 | MySQL | 含义 |
|---|---|---|
| `snapshot_id` | `VARCHAR(64) PRIMARY KEY` | 内容 SHA-256 |
| `schema_version` | `INT NOT NULL` | 契约版本 |
| `run_date` | `VARCHAR(16) NOT NULL` | 回测日期 |
| `generated_at` | `VARCHAR(64) NOT NULL` | Runner 生成 UTC 时间 |
| `published_at` | `VARCHAR(64) NOT NULL` | API 入库 UTC 时间 |
| `source_commit` | `VARCHAR(64) NOT NULL` | 源码提交 |
| `source_run_id` | `VARCHAR(64) NOT NULL` | Actions run id |
| `payload` | `LONGTEXT NOT NULL` | 完整 JSON |

建立 `INDEX idx_factor_ic_generated (generated_at)`。SQLite 使用同字段的 `TEXT`/`INTEGER` 类型；MySQL DDL 显式加入 `mysql_bootstrap.py`，不使用 `TEXT PRIMARY KEY`。

表采用追加式历史记录。按 `generated_at DESC, published_at DESC` 读取最新快照；每周约一条，数据量很小，本期不做清理。历史记录支持审计和必要时回看旧结果。

### 7.1 严格共享存储

`publish_factor_ic_snapshot()` 使用现有数据库连接包装，但增加强约束：

- 未配置 MySQL的本地开发环境允许写 SQLite。
- `Settings.uses_mysql=True` 时，若连接因故回落为 `dialect="sqlite"`，发布抛出存储不可用错误并返回 `503`。
- 数据库事务失败时回滚，不留下半条记录。
- 读取失败可以继续回退本地 `var/factor_ic/summary.json`，但发布绝不把本地回落伪装成生产成功。

---

## 8. 读取、缓存与状态

新增 `app/services/factor_ic_snapshot.py`，负责：

- Pydantic 数据契约与纯质量校验。
- 规范化 payload、计算 `snapshot_id`。
- 追加发布与最新快照读取。
- 本地文件回退。
- 构造诊断状态。

`factor_confidence.load_ic_summary()` 改为：

1. 通过 `factor_ic_snapshot` 读 MySQL 最新快照。
2. 数据库无记录或读取失败时读现有 `SUMMARY_PATH`。
3. 两者都不可用或损坏时返回 `{}`。
4. 进程缓存 TTL 从 1800 秒缩短为 300 秒；发布成功后清理处理该请求的进程缓存，其他 worker 最多 5 分钟后切换。

诊断状态不经过因子映射缓存，直接读取最新数据库记录，因此发布完成后立即可见。

新增配置：

```python
factor_ic_publish_token: str | None = None
factor_ic_stale_after_days: int = 30
```

对应环境变量：

- `FUND_AI_FACTOR_IC_PUBLISH_TOKEN`
- `FUND_AI_FACTOR_IC_STALE_AFTER_DAYS`

删除旧 C3 设计中的 `refresh_enabled`、`check_interval_hours` 和 `startup_delay_seconds` 等配置。

---

## 9. GitHub Actions 调度

新增 `.github/workflows/factor-ic-refresh.yml`：

- `schedule`：每周日北京时间 03:23，使用 `timezone: Asia/Shanghai`，避开整点高负载。
- `workflow_dispatch`：允许手动补跑。
- `permissions: contents: read`：不提交源码或生成物。
- `concurrency.group: factor-ic-refresh`、`cancel-in-progress: false`：不并行运行两次回测，也不取消已经在发布的任务。
- `timeout-minutes: 45`。
- 只安装 `apps/api/requirements.txt`，不安装 OCR 依赖。
- 产物写入 `$RUNNER_TEMP/factor-ic`。
- 使用当前生产 API 域名拼出内部发布 URL；Token 仅从 GitHub Secret 注入环境变量。

GitHub 官方说明，计划任务可能在高负载时延迟或丢弃；避开整点并每周重试，使一次调度异常不会长期阻塞数据更新：

- [GitHub：Events that trigger workflows — schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

本方案不采用 Actions 自动提交 `summary.json`。使用仓库 `GITHUB_TOKEN` 推送的提交不会触发新的 GitHub Actions workflow，若改用 PAT 又会扩大密钥权限和递归触发风险：

- [GitHub：GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)

### 9.1 发布脚本重试

`apps/api/scripts/publish_factor_ic.py`：

- 从文件读取 summary，先执行本地质量校验。
- 从环境读取 URL、Token、`GITHUB_SHA`、`GITHUB_RUN_ID`。
- 网络错误或 `5xx` 按 5 秒、15 秒、45 秒最多重试三次。
- 普通 `4xx` 直接失败，不盲目重试。
- `409` 表示已有更新快照，记录为安全跳过并以成功退出。
- 成功后向 `$GITHUB_STEP_SUMMARY` 写入生成时间、有效基金数、有效期数和各因子 IC；绝不写 Token。

---

## 10. 失败与降级

| 场景 | 行为 |
|---|---|
| 排行榜或净值获取失败 | runner/质量校验失败，不调用发布 API |
| 有效基金少于 240 | 拒绝发布，保留旧快照 |
| 因子有效期少于 12 | 拒绝发布，保留旧快照 |
| 回测结论不显著 | 正常发布；置信映射诚实显示低 |
| Token 缺失或错误 | 401/503，workflow 失败 |
| 发布网络错误或 5xx | 三次退避重试，仍失败则 workflow 失败 |
| MySQL 不可用并回落 SQLite | 发布 503，绝不写容器本地假共享数据 |
| 同一 payload 重复提交 | 200 幂等成功，不新增记录 |
| 已有更新快照 | 409，publisher 安全跳过 |
| 数据超过 30 天 | 继续使用最后有效快照，同时状态标记过期 |
| DB 无数据、本地文件有数据 | 使用本地兜底，状态 source=`local_file` |
| DB 与本地都无数据 | 因子置信诚实降级为“数据不足” |

任意失败都会在下一周自动重试，也可在 Actions 页面手动重跑。

---

## 11. 诊断 API

新增普通登录鉴权接口：

```text
GET /api/diagnostics/factor-ic-status
```

示例：

```json
{
  "available": true,
  "run_date": "2026-07-10",
  "generated_at": "2026-07-10T08:23:45.123456+00:00",
  "published_at": "2026-07-10T08:23:47.654321+00:00",
  "age_days": 0,
  "stale": false,
  "stale_after_days": 30,
  "source": "database",
  "target_universe_size": 300,
  "universe_size": 300,
  "universe_mode": "sampled",
  "rebalance_count": 35,
  "factor_periods": {
    "momentum": 34,
    "risk_adjusted": 34,
    "drawdown": 34,
    "composite": 34
  },
  "source_commit": "abcdef1"
}
```

本地旧格式 summary 没有 `generated_at` 时，以 `run_date` 的 UTC 零点做兼容状态计算，`source="local_file"`。无数据返回 `available=false`、`source="unavailable"` 和阈值，不伪造日期或样本数。

---

## 12. 前端状态条

新增 `FactorIcStatusBadge.tsx`，挂在 `PortfolioDashboard.tsx` 的“持仓因子体检”标题区域，不做独立大卡片。

展示状态：

- 加载中：短灰色骨架/占位，不推动主要布局。
- 新鲜：`IC 回测：7月10日 · 300只基金`。
- 过期：`IC 回测已超过30天，系统将继续自动重试`，使用警示色和文字双重表达。
- 无数据：`IC 回测暂未生成`。
- 接口异常：`IC 状态暂不可用`，不与“无数据”混淆。

桌面端标题与状态同行、按钮在右侧；移动端标题和状态纵向排列，展开按钮保持可点击。组件用 `role="status"`，不只依赖颜色传达状态。

`apps/web/src/lib/api.ts` 新增 `FactorIcStatus` 类型与 `fetchFactorIcStatus()`，走现有带 JWT 的 API helper。

---

## 13. 文件改动

### 新增

- `.github/workflows/factor-ic-refresh.yml`
- `apps/api/app/services/factor_ic_snapshot.py`
- `apps/api/scripts/publish_factor_ic.py`
- `apps/api/tests/test_factor_ic_backtest.py`（恢复关键引擎回归）
- `apps/api/tests/test_factor_ic_snapshot.py`
- `apps/api/tests/test_factor_ic_publish_endpoint.py`
- `apps/api/tests/test_publish_factor_ic.py`
- `apps/api/tests/test_factor_ic_status_endpoint.py`
- `apps/api/tests/test_factor_ic_workflow_contract.py`
- `apps/web/src/components/FactorIcStatusBadge.tsx`
- `apps/web/src/components/FactorIcStatusBadge.test.tsx`

### 修改

- `apps/api/scripts/run_factor_ic.py`：补 `schema_version`、`generated_at`，计算行为不变。
- `apps/api/app/services/factor_confidence.py`：数据库优先、本地兜底、缓存 5 分钟。
- `apps/api/app/config.py`：发布 Token 与 30 天过期阈值。
- `apps/api/app/auth/middleware.py`：仅豁免精确内部发布路径的普通 JWT。
- `apps/api/app/main.py`：内部发布端点和登录诊断端点。
- `apps/api/app/mysql_bootstrap.py`：MySQL 表与索引。
- `apps/api/app/db_migrations.py`：`SCHEMA_VERSION` 从 8 升至 9，新增并无条件调用 `_migrate_factor_ic_snapshots()`，保证新库和已处于当前版本的 SQLite 都能补表与索引。
- `apps/web/src/lib/api.ts`：状态类型与请求函数。
- `apps/web/src/components/PortfolioDashboard.tsx`：挂载状态条并处理响应式标题布局。
- `.env.example`：两个新环境变量。
- `docs/PROJECT_CONTEXT.md`：实现完成后更新能力、数据流、API、环境变量和验证记录。
- `docs/TODO_factor_ic_refresh.md`：实现完成后标记已解决并链接本文。

明确不新增：`background_job_lock.py`、`factor_ic_report.py`、`factor_ic_refresh_loop.py`；不修改 `app/lifespan.py`。

---

## 14. 测试与验证

### 14.1 后端

1. IC 引擎：植入正向信号、噪声不显著、横截面不足、前视偏差守卫、IC 范围。
2. 快照契约：固定参数、240 样本边界、12 期边界、因子缺失/重复、非有限数字、时间窗口。
3. 存储：SQLite 追加、幂等、最新排序、本地文件回退、MySQL DDL、生产回落 SQLite 拒绝发布。
4. 发布接口：未配置 Token、缺失/错误 Token、合法发布、非法 payload、旧快照、数据库失败。
5. 发布脚本：网络异常、5xx 三次重试、普通 4xx 不重试、409 安全跳过、Summary 不泄露 Token。
6. 状态接口：数据库优先、本地兜底、29/30/31 天边界、JWT 鉴权。
7. workflow 静态契约：计划/手动触发、只读权限、并发组、超时、固定参数和 Secret 引用。

运行：

```bash
cd apps/api
python -m pytest tests -q -n auto --dist loadscope
```

### 14.2 前端

`FactorIcStatusBadge.test.tsx` 覆盖加载、新鲜、过期、无数据和接口异常；同时运行：

```bash
cd apps/web
npm test
npm run typecheck
npm run lint
npm run build
```

### 14.3 本地集成验证

使用临时 SQLite 和临时输出目录完成一次：

```text
生成有效 summary
  → 使用测试 Token 发布到本地 TestClient/API
  → 查询 diagnostics 状态
  → load_ic_summary 读取同一快照 factors
  → 重复发布验证幂等
  → 低质量结果验证旧快照不被覆盖
```

自动测试不使用生产 Token，也不访问生产数据库。

### 14.4 生产验收

代码部署后：

1. 用 `secrets.token_urlsafe(48)` 生成 Token。
2. 在 CloudBase 设置 `FUND_AI_FACTOR_IC_PUBLISH_TOKEN`。
3. 在 GitHub 设置 Secret `FACTOR_IC_PUBLISH_TOKEN`。
4. 手动触发 `Factor IC Refresh` workflow。
5. 验证 workflow 绿色、Summary 显示 240 只以上有效基金和 12 期以上有效期。
6. 登录 Web，验证“持仓因子体检”显示当日日期、有效样本数且非过期。
7. 验证诊断响应 `source=database`、`stale=false`，因子评分在最多 5 分钟内读取新结果。

---

## 15. 兼容性与上线顺序

- 现有 `var/factor_ic/.gitkeep`、`.gitignore` 和两个 Dockerfile 的目录 COPY 保留，本地文件继续作为灾难兜底。
- 旧格式本地 `summary.json` 可继续读取；新字段只对发布契约强制。
- 因子置信映射函数及前端已有 `IC·高/中/低/不足` 标签契约不变。
- 发布端点上线但 Token 未配置时返回 503，不影响其他 API。
- 推荐顺序：先部署代码和表结构，再配置 CloudBase/GitHub Token，最后手动触发 workflow。
- Token 配置完成前，系统维持现有“数据不足”降级，不阻塞日报或持仓功能。
