# FundPilot 性能 P1/P2 实施记录（2026-07-24）

## 1. 结论

本轮已完成只读审计列出的 P1、P2 共 20 项，并补齐轻量可观测性、容量探针和故障矩阵。实现继续保持以下边界：

- 不引入 Redis、消息队列、微服务或更多 Uvicorn worker。
- 不缓存认证 principal；用户停用、改密、角色变化和会话撤销仍按请求读取权威数据库并立即生效。
- 用户档案缓存严格以 `database + userId` 隔离，写后立即失效，跨 worker 最长陈旧窗口默认为 5 秒。
- 行情 stale fallback 保留真实 `fetched_at`、`source_visible_at`、交易日与来源；不会把重新读缓存的时间冒充采集时间。
- 历史决策继续使用 `decision_at`、PIT、no-lookahead 和 fail-closed 规则；TTL jitter 只改变刷新边界，不改变证据可见时间。
- 当前 2 worker、单机拓扑没有足够证据证明 Redis 收益大于运维成本，结论仍为 `need_now=false`。

## 2. P1：缓存与数据库治理

| ID | 状态 | 实施结果 |
| --- | --- | --- |
| P1-01 | 完成 | thread-local MySQL 连接增加 30 分钟最大寿命、500 次最大复用、无自动重连的 `ping` 校验，并把 session `wait_timeout` 校准为高于客户端寿命 |
| P1-02 | 完成 | immutable decision 写入的 `SELECT ... FOR UPDATE` 不再读取大 `payload`，只锁定/比较紧凑元数据列；内容仍由 canonical hash 约束 |
| P1-03 | 完成 | `FundProfileService` 使用进程共享、按用户隔离的 5 秒 TTL cache；同用户 miss 单飞，保存/删除后立即失效 |
| P1-04 | 完成 | 决策质量快照的大表分页由 OFFSET 改为带唯一尾键、兼容 NULL 排序的 keyset pagination |
| P1-05 | 完成 | Analysis/Discovery Job 元数据初始化按数据库目标单飞；MySQL request/heartbeat 路径不再执行 DDL |
| P1-06 | 完成 | MySQL named lock 使用独立、每进程最多 2 条 session 的小池；归还前执行 `RELEASE_ALL_LOCKS()`，异常连接直接丢弃 |
| P1-07 | 完成 | PyMySQL 执行统一返回受管 cursor；读取完、无结果 DML、异常和上下文退出均显式关闭 cursor |
| P1-08 | 完成 | 主板块、benchmark evidence、候选档案等逐基金读取收敛为最多 500 条一批的 `IN (...)` 查询；独立 provider 可并行批取 |
| P1-09 | 完成 | Schema v20 新增 `fund_transactions(userId, status, confirm_date, trade_time)` 组合索引，服务 pending filter 与确定性排序 |
| P1-10 | 完成 | 生产 MySQL runtime bootstrap 后台执行，`/health`、`/ready` 可报告初始化状态；业务路由在 schema/清理完成前 fail-closed 返回 503 |

### MySQL 连接预算

默认生产仍为 2 个 Uvicorn worker。request 连接按真正使用数据库的线程惰性建立，并受寿命/复用次数回收；named-lock session 与 request session 隔离，每个进程最多额外 2 条。运维接口同时暴露：

- `Threads_connected`、`Threads_running`、`Max_used_connections`、`max_connections`、`wait_timeout`；
- dedicated session pool 的 `total / idle / in_use`；
- 共享 executor 的 active/queued；
- AkShare worker 的 available/busy；
- Analysis/Discovery Job 容量。

## 3. P2：并发与容量

| ID | 状态 | 实施结果 |
| --- | --- | --- |
| P2-01 | 完成 | SSE fan-out 统一到进程共享的 IO 32、Analysis context 2、Discovery context 2 三类有界 executor，保留资源隔离 |
| P2-02 | 完成 | 分析数据、候选池、上下文增强和 judge 等等待循环每 250ms 检查 `stop_event`；取消后不再继续排队昂贵工作 |
| P2-03 | 完成 | 通用 AkShare 调用改为 2 个长驻隔离子进程；每 50 个任务或 30 分钟回收，超时杀掉当前进程；每次任务使用新 globals 并恢复 cwd/env |
| P2-04 | 完成 | 板块实时行情并行竞速已配置的东方财富、relay、browser 快速路径，采用首个通过身份与完整性校验的结果；失败后才进入 AkShare |
| P2-05 | 完成 | 公共热点 TTL 增加进程/键稳定 jitter；基金目录、档案、排行与板块 spot 刷新使用数据库 advisory singleflight，锁忙时只允许明确 stale 或 fail-closed |
| P2-06 | 完成 | 首页初始持仓、画像、Prompt、行情状态合并为 `/api/portfolio/refresh-and-hydrate`；聚合成功不再重复拉状态；非交易时段轮询使用 idle interval |
| P2-07 | 完成 | `ChatMarkdown` 重型 renderer 动态加载；报告详情、荐基详情与组合 summary 使用按登录作用域隔离的并发 GET 去重 |
| P2-08 | 完成 | 普通 `apiFetch` 默认 60 秒总超时并联动调用者 AbortSignal；只有显式 SSE 传输可用 `timeoutMs=0` |
| P2-09 | 完成 | 交易时段接口使用短公共缓存；不可变报告详情使用 `private, must-revalidate + ETag + Vary: Authorization`，304 不传正文；认证/管理员/持仓状态继续 no-store |
| P2-10 | 完成 | Nginx `_next/static` 一年 immutable、HTML no-cache、JSON gzip/buffering 与 SSE no-buffer 分治保持生效 |

## 4. 轻量可观测性

没有为了指标系统再部署 Prometheus/OTel。每个 API 进程维护有界聚合样本，管理员通过 `GET /api/admin/performance` 读取：

- request ID、路由模板、状态码、响应字节、完整延迟与 TTFB 的 p50/p95/p99；
- 每请求 DB 次数/耗时，以及去除字符串和数字字面量的 SQL fingerprint；
- 东方财富、AkShare、DeepSeek 的调用次数、耗时、HTTP 状态和错误分类；
- cache hit/miss/stale/refresh；
- executor active/queued、SSE active、Job capacity 与后台任务耗时；
- CPU、RSS、线程、磁盘、Linux 网络累计字节；
- 已登录浏览器上报的 CLS/FCP/INP/LCP/TTFB。

指标只保存聚合值和有限样本，不记录 request body、query string、SQL 参数、Token、Cookie、Authorization、邮箱、持仓或外部密钥。慢请求和抽样请求输出结构化日志，默认慢阈值 1000ms、正常请求抽样率 1%。

## 5. Schema v20

v20 是纯增量迁移：

```sql
CREATE INDEX idx_fund_tx_pending_confirm
ON fund_transactions (userId, status, confirm_date, trade_time);
```

服务查询：

```sql
SELECT ...
FROM fund_transactions
WHERE userId = ? AND status = 'pending'
ORDER BY confirm_date, trade_time;
```

索引把等值过滤列放前、排序列放后。成本是每次交易写入维护一个额外二级索引；没有 DROP、列重写或历史 payload 变更。回滚应用时保留该索引，不做紧急 DROP。

## 6. 本地容量结果

脚本：`scripts/perf/local_api_benchmark.py`

固定口径为单个本地 Uvicorn worker、临时 SQLite、禁用外部 provider、每端点每层 80 次请求。P1/P2 当前样本在 1/10/25/50 并发下共 2,560 次请求，成功率 100%。

50 并发 P95 与 P0 后基线对比：

| 端点 | P0 后 P95 ms | P1/P2 P95 ms | 变化 |
| --- | ---: | ---: | ---: |
| `/health` | 216.36 | 226.77 | +4.8% |
| `/api/auth/me` | 565.74 | 330.15 | -41.6% |
| `/api/investor-profile` | 499.61 | 546.88 | +9.5% |
| `/api/portfolio/holdings` | 344.56 | 448.09 | +30.0% |
| `/api/portfolio/summary` | 770.43 | 644.59 | -16.3% |
| `/api/fund-profiles` | 529.36 | 469.25 | -11.4% |
| `/api/reports` | 648.11 | 704.35 | +8.7% |
| `/api/fund-discovery/reports` | 619.66 | 693.90 | +12.0% |

这些是两个独立短样本，Windows 调度、SQLite 单写者、指标中间件和进程启动状态都会造成波动，不能把差值全部归因于某一改动，也不能外推生产 QPS。可以确认的边界是：无错误、无线程/连接持续增长迹象，且 Auth、组合 summary、FundProfile 三条目标路径在该样本下降；其他路径必须以生产长期指标继续观察。

本次结束快照为单 Uvicorn 根进程约 170.47 MB、62 threads、54 TCP。该值包含并发客户端与 Windows 运行时噪声，只用于后续同口径比较。

## 7. 故障与回归验证

- 故障矩阵：43 passed，覆盖 SSE 断连、DeepSeek 预算/429、东方财富 pool/circuit、Job 去重/heartbeat、Worker leader、跨进程锁、provider race、共享 executor 取消、指标隐私。
- API 全量：1387 passed，1 个 Starlette TestClient/httpx 弃用 warning。
- Web 全量：105 files / 470 passed。
- TypeScript typecheck、ESLint、Next.js production build、Python `compileall`、`git diff --check`：通过。
- 本轮新增聚焦回归覆盖：MySQL 连接寿命/复用/cursor/dedicated pool、keyset pagination、Schema v20、FundProfile user isolation、批量主板块读取、HTTP revalidation、provider singleflight/race、共享 executor cancellation、performance endpoint。

## 8. 生产验收与回滚

发布必须按以下顺序执行：

1. 使用 `mysqldump --single-transaction --routines --triggers` 创建 schema v20 前备份，校验 gzip 和 SHA-256。
2. 由部署的一次性 admin bootstrap 执行 v20；API runtime 用户不授予 DDL。
3. 核对 `schema_meta=20`、`idx_fund_tx_pending_confirm`，并对 pending 查询执行 `EXPLAIN`。
4. 在目标容器执行 `nginx -t`，检查普通 JSON gzip、SSE `X-Accel-Buffering: no`、静态 immutable、HTML no-cache。
5. 分别检查 2 个 API worker 的 `/health`/`/ready`，以及 Worker heartbeat。
6. 运行生产安全容量探针，远端最高 25 并发、每端点最多 100 次；默认只读，不自动触发 SSE、写入或外部 provider。
7. 对东方财富与 AkShare 各执行一个有界真实探针，只输出 provider path、记录数和耗时，不输出用户数据或密钥。
8. 观察 `Threads_connected / Max_used_connections`、API RSS/thread、executor queue、SSE active、provider error 至少一个业务高峰。

应用回滚使用上一个健康镜像和静态发布目录。v20 索引与 P0/v19 新增结构全部保留；连接池、AkShare pool、bootstrap 后台化、指标和前端聚合均可通过对应环境开关或代码回滚关闭，不以破坏性数据库操作作为第一响应。
