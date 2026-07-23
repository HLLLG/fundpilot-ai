# FundPilot 性能 P0 实施记录（2026-07-24）

## 1. 实施状态

| ID | 状态 | 落地结果 |
| --- | --- | --- |
| P0-01 | 完成 | 缓存读取统一 UTC 时间语义，stale/any-age 返回保留数据库真实 `updated_at`，不再被重置成“刚更新” |
| P0-02 | 完成 | SSE producer 接入 `stop_event`、断连轮询、bounded queue、协作取消与线程回收；contextvars 正确传播 |
| P0-03 | 安全等价实现 | 权威 principal 查询改为 `asyncio.to_thread`；刻意不加 TTL cache，保留禁用/改密/改角色/authVersion 的即时跨 worker 生效 |
| P0-04 | 完成 | DeepSeek 单请求共享 180 秒 deadline、60 秒首字节 watchdog、解析并封顶 `Retry-After`；stream 断连可关闭请求私有客户端 |
| P0-05 | 完成 | 东方财富共享 httpx/requests 连接池，移除 `Connection: close`；news 现路径为有界 AkShare subprocess，不存在可替换的逐请求 HTTP Client |
| P0-06 | 完成 | 两类 Job 增加 canonical dedup、活动唯一约束、15 秒 heartbeat、15 分钟 stale recovery、2 worker + 8 queue 容量和 `429 Retry-After` |
| P0-07 | 完成 | 东方财富顶层调用共享 30 秒预算、每 host circuit breaker、jitter backoff、并发/获取超时闸门 |
| P0-08 | 完成 | Nginx 精确拆分 SSE 与普通 JSON；JSON buffering/gzip、upstream keepalive、静态 immutable 与 HTML no-cache 分治 |
| P0-09 | 完成 | analysis/discovery/chat SSE 每进程最多 4 条；超限返回 `429` 与 5 秒 `Retry-After`，finally 释放 permit |
| P0-10 | 完成 | StreamSession 改为 DB 持久化，按 user 隔离、2 小时 TTL、原子追加、stage gate、启动清理；不依赖 sticky routing |

本地基准额外发现并修复了报告列表的大 payload 行访问：

- `reports` 与 `fund_discovery_reports` 保留完整 payload 为事实源。
- 新增窄表 `report_summaries` 与 `fund_discovery_report_summaries`。
- 列表只读取 ID/时间和窄摘要；详情仍按需加载完整 payload。
- 老数据首次列表访问会惰性生成摘要并回填，此后稳态不再解析大 payload。
- 保留用户已经在 `Dashboard.tsx`、`FundDiscoveryPanel.tsx`、`api.ts` 中完成的列表/详情懒加载行为。

## 2. SSE 取消与准入

统一桥接层现在负责：

- 通过 `request.is_disconnected()` 感知浏览器断连。
- producer/consumer 共享 `threading.Event`。
- queue put 有界等待，避免 producer 在已无人消费的队列上阻塞 600 秒。
- `GeneratorExit`、async cancellation 与业务 `StreamCancelled` 走同一收尾路径。
- producer 线程在退出前关闭迭代器并有界 join。
- 分析准备阶段、发现准备阶段、heartbeat 和 DeepSeek stream 都检查 stop event。
- chat stream 从同步端点改为 async bridge，以便断连检查生效。

准入是每进程 semaphore，适合当前 2 worker 单机。它不是全局分布式配额；扩到 4 worker 或跨机时需重新评估。

## 3. DeepSeek 与东方财富预算

DeepSeek：

- 总预算：180 秒。
- 首字节：60 秒。
- tool rounds、judge、chat、discovery 与 report 共享同一请求 deadline，不能各自重新获得完整预算。
- 429 尊重可解析的 `Retry-After`，但任何退避都不能越过总 deadline。
- 非流式调用继续复用共享池；流式调用使用请求私有、可中断 client，避免断连前尚未收到 headers 时卡住。

东方财富：

- httpx pool：最大连接 32、keepalive 16、expiry 30 秒。
- requests Session 使用连接池。
- 供应商并发：每进程 8。
- 获取闸门超时：5 秒。
- 顶层调用预算：30 秒，通过 contextvar 让嵌套 fallback 共享。
- circuit：同 host 连续 3 次失败后冷却 15 秒。
- circuit-open 与本地 pool shedding 不重复记为 provider 失败。
- stale/identity 不确定时继续遵循金融数据 fail-closed 规则。

## 4. Job 韧性

Analysis 与 Discovery Job 采用相同模型：

- 请求参数规范化后计算 SHA-256 dedup key。
- `(userId, active_dedup_key)` 唯一；仅活动状态持有 active key。
- 重复活动提交返回已有 Job ID，不重复消耗 LLM。
- 终态把 active key 置空，允许未来合法重跑。
- 进程容量固定为 2 个 worker 和最多 8 个排队任务。
- 本地队列已满时在插入前失败，API 返回 `429`。
- 运行任务每 15 秒刷新 heartbeat。
- 启动时只清理超过 900 秒的 running/pending，降低滚动发布误判其他 worker 活任务的风险。

## 5. StreamSession

Schema v19 新增 `stream_sessions`：

- session 与 `userId` 绑定，跨用户读取返回不可见。
- operator notes 使用 JSON 保存并原子追加。
- SQLite 使用 `BEGIN IMMEDIATE`；MySQL 使用 `SELECT ... FOR UPDATE`。
- follow-up stage gate 在事务中校验。
- 默认 TTL 7,200 秒。
- create/current-user 操作做局部过期清理，lifespan 启动做全局清理。
- analysis 在真正构造 LLM prompt 前重新从 DB 读取 session，避免 worker 间旧内存状态。

## 6. Schema v19

SQLite `SCHEMA_VERSION` 与 MySQL `MYSQL_SCHEMA_VERSION` 均从 18 升到 19。变更只有新增：

- `reports.summary_payload`
- `fund_discovery_reports.summary_payload`
- `report_summaries`
- `fund_discovery_report_summaries`
- `analysis_jobs.dedup_key`
- `analysis_jobs.active_dedup_key`
- `analysis_jobs.heartbeat_at`
- `discovery_jobs.dedup_key`
- `discovery_jobs.active_dedup_key`
- `discovery_jobs.heartbeat_at`
- 两类 Job 的活动唯一索引和 heartbeat 索引
- `stream_sessions` 及 user/expiry 索引

迁移脚本已同步 SQLite → MySQL 的表、列、默认值和 v19 契约。没有 DROP、重命名或历史 payload 重写。

## 7. 配置默认值

| 环境变量 | 默认值 |
| --- | ---: |
| `FUND_AI_DEEPSEEK_REQUEST_BUDGET_SECONDS` | 180 |
| `FUND_AI_DEEPSEEK_FIRST_BYTE_TIMEOUT_SECONDS` | 60 |
| `FUND_AI_ASYNC_JOB_MAX_WORKERS` | 2 |
| `FUND_AI_ASYNC_JOB_QUEUE_CAPACITY` | 8 |
| `FUND_AI_ASYNC_JOB_HEARTBEAT_INTERVAL_SECONDS` | 15 |
| `FUND_AI_ASYNC_JOB_STALE_SECONDS` | 900 |
| `FUND_AI_ASYNC_JOB_RETRY_AFTER_SECONDS` | 5 |
| `FUND_AI_SSE_MAX_CONCURRENT_PER_PROCESS` | 4 |
| `FUND_AI_SSE_RETRY_AFTER_SECONDS` | 5 |
| `FUND_AI_STREAM_SESSION_TTL_SECONDS` | 7200 |
| `FUND_AI_EASTMONEY_CALL_DEADLINE_SECONDS` | 30 |
| `FUND_AI_EASTMONEY_MAX_CONCURRENCY` | 8 |
| `FUND_AI_EASTMONEY_ACQUIRE_TIMEOUT_SECONDS` | 5 |
| `FUND_AI_EASTMONEY_CIRCUIT_FAILURE_THRESHOLD` | 3 |
| `FUND_AI_EASTMONEY_CIRCUIT_COOLDOWN_SECONDS` | 15 |

## 8. 验证结果

当前工作区完成：

- API 全量：`1366 passed, 1 warning`，约 215 秒。
- Web Vitest：`104 files / 463 passed`，Windows 单工作进程。
- TypeScript typecheck：通过。
- ESLint：通过。
- Next.js production build：通过，10 个静态页面。
- Python `compileall`：通过。
- `git diff --check`：通过。
- 重点覆盖：SSE 断连/context 传播、DeepSeek pre-header 取消与预算、东方财富池/熔断/deadline、Job 去重/容量/心跳/stale、StreamSession 跨 worker 数据契约、schema v19、SQLite→MySQL 迁移、Nginx location 契约、摘要窄表与 legacy backfill。

唯一警告是 Starlette TestClient/httpx 的弃用提醒，不是本轮业务失败。

未在本机执行：

- 真实 `nginx -t`：当前 Windows 环境没有 Nginx/Docker 二进制。
- 生产 MySQL 迁移或生产流量压测。
- 真实 DeepSeek、东方财富、AkShare 外部调用。
- 生产等价双 worker + Nginx 的长时间 soak。

## 9. 上线门禁

部署前按顺序执行：

1. 备份生产数据库，并确认可恢复。
2. 在 staging/生产副本运行 schema v19 migration，核对表、索引与 `schema_meta=19`。
3. 在目标 Nginx 容器执行 `nginx -t`。仓库的 Lighthouse 部署脚本已有该门禁，失败不得 reload。
4. 验证普通 JSON：
   - `Accept-Encoding: gzip` 返回压缩。
   - 普通 `/api/` 不再使用 SSE 的一小时超时和全局 buffering-off。
5. 验证 SSE：
   - 首事件及时到达且不被 gzip/buffer。
   - 中途断连后 provider/producer 在数秒内退出。
   - 第 5 条同 worker SSE 返回 `429` 与 `Retry-After`。
6. 在两个 worker 上完成 create session → append note → analyze follow-up，确认不再随机 404。
7. 重复提交相同 Job，确认活动记录只有一条；模拟 stale heartbeat 后确认启动清理。
8. mock DeepSeek 429、无首字节、总预算和客户端断连四种路径。
9. mock 东方财富 host brownout，确认 deadline/circuit 生效且不会把不确定数据标成 fresh。
10. 观察 MySQL `Threads_connected`、API RSS/threads/fd、SSE active 数、Job queue 深度、429 比例和外部调用错误率。

## 10. 回滚

- 应用代码可按 P0 功能面回滚；schema v19 的新增列/表/索引保留，不做紧急 DROP。
- Nginx 可回退单一配置文件，但必须再次 `nginx -t` 后 reload。
- DeepSeek、东方财富、Job、SSE 的预算均可通过环境值放宽；不要用无限值掩盖真实卡死。
- StreamSession 应优先保留 DB 表；若回退到内存实现，必须同时配置 sticky routing 并接受滚动发布丢 session 的限制。
- Auth 不存在 TTL cache 开关，因为本轮没有引入该缓存。

## 11. 后续 P1/P2

本轮没有把 P1/P2 伪装成已完成。下一批优先级建议：

1. MySQL 连接寿命/reuse、cursor 关闭与 dedicated pool。
2. keyset pagination、N+1 批处理和 pending transaction 索引。
3. 共享业务 executor 与 stop event 深层传播。
4. AkShare 长驻池和跨 worker singleflight。
5. 生产可观测性、SLO 与用户/端点级 LLM 成本归因。
6. 达到至少 4 worker、跨机或共享 circuit/session 热点后，再评估 Redis。
