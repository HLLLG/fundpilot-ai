# CloudBase 云托管部署指南

面向 ≤5 人的私有部署：FastAPI 云托管 + CloudBase MySQL + Web 静态托管。

## 架构

```text
Web 静态托管 ──HTTPS──► CloudBase 云托管（FastAPI Docker）
                                  │
                                  ▼
                          CloudBase MySQL
```

微信小程序已从当前项目下线；本文只保留 Web/API 部署流程。

## 1. 准备 CloudBase 环境

1. 登录 [腾讯云 CloudBase 控制台](https://tcb.cloud.tencent.com/)。
2. 创建环境，记录环境 ID（`FUND_AI_CLOUDBASE_ENV_ID`）。
3. 开通 MySQL 数据库，记录连接串。
4. 准备 DeepSeek API Key 与 32 字符以上 JWT Secret。
5. 准备一枚独立的因子 IC 发布 Token；不得复用 JWT 或 DeepSeek Secret。

## 2. 迁移数据（本地 SQLite → MySQL）

本地已有数据时执行：

```bash
export FUND_AI_DATABASE_URL="mysql://user:pass@host:3306/db"
python scripts/migrate_sqlite_to_mysql.py --sqlite data/app.db
python scripts/migrate_sqlite_to_mysql.py --sqlite data/app.db --apply
```

第一条命令只做只读 dry-run；确认计划后，第二条才实际写入。迁移前建议先在历史页导出
SQLite 备份。schema v14+ 源库的 `decision_quality_contract_rollouts` marker 会在 MySQL
bootstrap 时原样注入并复核；源库缺失或篡改 marker、目标库已有不同边界时迁移失败关闭，
不得用迁移时当前时间重新生成边界。质量输入、artifact commit receipt、provider receipt、
evaluation snapshot 和 rollout marker 五类追加式账本均采用 insert-only + content hash 冲突检查。
当前 schema v16 另含 `prompt_shadow_runs` / `prompt_shadow_budget_counters` 两张可变运营表；
它们随迁移复制并验真精确列、索引与 InnoDB，但不伪装成追加式质量账。

## 3. 构建并部署 API

```bash
# 项目根目录
docker build -t fundpilot-api -f apps/api/Dockerfile apps/api
```

云托管环境变量示例：

```text
FUND_AI_DATABASE_URL=mysql://...
FUND_AI_JWT_SECRET=<随机32字节以上>
FUND_AI_FACTOR_IC_PUBLISH_TOKEN=<独立随机发布Token>
FUND_AI_DECISION_QUALITY_READ_TOKEN=<可选；独立随机只读Token>
FUND_AI_PROMPT_SHADOW_ENABLED=false
# 只有受控启用 paired Prompt 实验时才填写独立随机值：
FUND_AI_PROMPT_SHADOW_ASSIGNMENT_SECRET=
FUND_AI_PROMPT_SHADOW_SAMPLE_BASIS_POINTS=10000
FUND_AI_PROMPT_SHADOW_MAX_CHALLENGER_CALLS_PER_DAY=100
FUND_AI_FACTOR_IC_STALE_AFTER_DAYS=30
FUND_AI_DEEPSEEK_API_KEY=<你的Key>
FUND_AI_CLOUDBASE_ENV_ID=<环境ID>
FUND_AI_CORS_ORIGINS=https://你的Web域名
FUND_AI_OCR_PRELOAD=false
FUND_AI_NEWS_ENABLED=true
```

建议：

- 单副本 2 核时仍建议云托管实例副本数 ≥2，避免日报/荐基长任务占满 worker。
- 生产环境设置 `FUND_AI_DB_FALLBACK_SQLITE=false`，让数据库故障尽早暴露。
- 数据库运行账号必须对应用 schema 拥有 `TRIGGER` 与质量账 additive DDL 所需权限。API 会创建并逐次精确校验
  `decision_quality_contract_rollouts`、`decision_quality_input_artifacts`、
  `decision_quality_evaluation_snapshots`、`decision_quality_artifact_receipts`、
  `decision_quality_provider_receipts` 的 10 个 `BEFORE UPDATE/DELETE` 触发器，以及
  `(userId, artifact_type, logical_key)` 非前缀唯一索引和 `logical_key VARCHAR(255) NULL`。
  schema v16 还会校验 `prompt_shadow_runs` / `prompt_shadow_budget_counters` 的列、唯一键、
  worker 索引与 InnoDB；这两张表必须可更新，不能套用不可变触发器。
  权限缺失、同名 no-op/条件触发器、错误/前缀索引、列契约不符或 DDL 后无法验真都会直接阻断
  MySQL bootstrap，且不会伪装成网络故障回落 SQLite；多副本并发首次启动只有在异常后重读
  metadata 确认精确契约已由另一 worker 建成时才视为幂等成功。
- OCR 本地模型占内存较高；云端部署默认先关闭 `FUND_AI_OCR_PRELOAD`。

## 4. 部署 Web 前端

自动部署：

`main` 分支 CI 通过后，GitHub Actions 会构建并发布到 CloudBase 静态应用 `fundpilot-web`（见 `.github/workflows/deploy-web.yml`）。

手动部署：

```bash
cd apps/web
NEXT_PUBLIC_API_BASE_URL=https://你的API域名 npm run build
npm i -g @cloudbase/cli
tcb login
tcb app deploy --force
```

## 5. 配置因子 IC 自动刷新

先部署包含 `factor_ic_snapshots` 表和内部发布端点的 API，再执行一次性配置：

```text
python -c "import secrets; print(secrets.token_urlsafe(48))"
CloudBase 云托管环境变量：FUND_AI_FACTOR_IC_PUBLISH_TOKEN=<生成值>
GitHub Actions Secret：FACTOR_IC_PUBLISH_TOKEN=<同一值>
GitHub → Actions → Factor IC Refresh → Run workflow
```

两端必须使用同一值。该 Token 只允许发布经过质量校验的因子 IC 快照，不得复用
`FUND_AI_JWT_SECRET` 或 `FUND_AI_DEEPSEEK_API_KEY`。正常情况下
`.github/workflows/factor-ic-refresh.yml` 每周日北京时间 03:23 自动运行；首次上线用
`workflow_dispatch` 手动触发，并在 Actions Summary 核对生成时间、有效基金数、
回测期数和四个因子 IC。Token 不应出现在命令行、日志或 Summary 中。

## 6. 配置每日 outcome 结算与 D4 质量快照

仓库中的 `.github/workflows/outcome-settlement.yml` 是 **Lighthouse SSH 专用**运维流程：
它依赖 `LIGHTHOUSE_*` 凭证，进入 `/srv/fundpilot/repo` 的 Docker Compose API 容器执行，
不能直接调度 CloudBase 云托管实例。继续使用 CloudBase 时，必须在 CloudBase 定时触发器、
CI 任务或另一受控调度服务中配置一个等价 runner。该 runner 应使用与 API 相同版本的镜像、
生产环境变量和 MySQL 网络/账号权限，并在净值数据基本发布后每日执行一次。

任务容器以 `/app` 为工作目录时，可采用以下执行契约：

```bash
#!/usr/bin/env bash
set -uo pipefail

python scripts/settle_pending_outcomes.py
settlement_status=$?

evaluation_as_of="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
python scripts/evaluate_decision_quality.py \
  --all-users \
  --evaluation-as-of "$evaluation_as_of" \
  --window-days 365 \
  --format summary
snapshot_status=$?

if [ "$settlement_status" -ne 0 ] || [ "$snapshot_status" -ne 0 ]; then
  echo "settlement=$settlement_status quality_snapshot=$snapshot_status" >&2
  exit 1
fi
```

两个命令必须独立尝试，再统一汇总失败；不要用 `&&` 串联，否则结算失败会跳过当日点时快照，
也不要让快照失败抹掉已经成功写入的终态。结算结果中的 `completed_with_pending` 表示证据尚未
齐备，是可在后续交易日重试的成功状态；缺失数据保持 pending，不会被写成 0。settlement
会先独立补齐已提交质量制品缺失的 post-commit receipt，再分别尝试常规 T+N 与候选 T+20；
任一步失败都不会阻止其余步骤。D4 候选正式标签只接受 live adapter output receipt 与 outcome
post-commit receipt，旧 v3/v2 候选制品只进入 manifest 诊断。若返回
`failed_user_ids`，CLI 退出码为 2，以便调度器告警，但隔离成功的健康租户结果已经落库，
不得因告警回滚或覆盖这些不可变记录。

如需调用隐藏的只读质量快照接口，可额外配置
`FUND_AI_DECISION_QUALITY_READ_TOKEN`，请求时放入
`X-Decision-Quality-Read-Token`。该值必须是独立随机 Secret，不得复用 JWT、因子 IC 发布
Token 或模型 Key；不需要该运维读面时可以不配置。

## 7. 常见问题：浏览器报 CORS / Failed to fetch

### 现象

浏览器控制台出现：

```text
Access to fetch at 'https://fundpilot-api-xxx.sh.run.tcloudbase.com/api/portfolio/holdings'
from origin 'https://xxx.webapps.tcloudbase.com' has been blocked by CORS policy
```

### 原因 A：API 未允许 Web 域名

确认 API 环境变量：

```text
FUND_AI_CORS_ORIGINS=https://你的Web域名
```

如果使用 CloudBase 默认静态域名，也可设置：

```text
FUND_AI_CLOUDBASE_ENV_ID=<环境ID>
```

后端会自动放行 `https://*.webapps.tcloudbase.com`。

### 原因 B：网关 504 被浏览器误报为 CORS

长任务或冷启动超时也可能在浏览器侧表现为 CORS。排查顺序：

1. 打开云托管日志，看是否有 504 / worker 超时。
2. 检查 MySQL 是否自动暂停或连接超时。
3. 日报/荐基进行中，前端会优先使用 localStorage 缓存持仓；任务完成后刷新即可。

## 环境变量速查

| 变量 | 说明 |
|------|------|
| `FUND_AI_DATABASE_URL` | `mysql://user:pass@host:3306/db` |
| `FUND_AI_JWT_SECRET` | JWT 签名密钥 |
| `FUND_AI_FACTOR_IC_PUBLISH_TOKEN` | 因子 IC 发布专用 Token；与 GitHub Secret `FACTOR_IC_PUBLISH_TOKEN` 同值 |
| `FUND_AI_DECISION_QUALITY_READ_TOKEN` | 可选；决策质量快照内部只读 Token，必须独立生成 |
| `FUND_AI_PROMPT_SHADOW_ENABLED` | paired Prompt 后台实验开关，默认 `false`；不影响冠军报告 |
| `FUND_AI_PROMPT_SHADOW_ASSIGNMENT_SECRET` | 仅启用实验时填写的独立 HMAC 随机密钥；不会持久化 |
| `FUND_AI_PROMPT_SHADOW_SAMPLE_BASIS_POINTS` | 确定性抽样阈值，1～10000；10000 表示全部合格请求 |
| `FUND_AI_PROMPT_SHADOW_MAX_CHALLENGER_CALLS_PER_DAY` | 上海自然日全局挑战调用上限，默认 100 |
| `FUND_AI_FACTOR_IC_STALE_AFTER_DAYS` | 快照过期提示阈值，默认 30 天 |
| `FUND_AI_CLOUDBASE_ENV_ID` | CloudBase 环境 ID；用于 Web 静态域名 CORS 自动放行 |
| `FUND_AI_CORS_ORIGINS` | 生产 Web 域名，逗号分隔 |
