# 因子 IC 回测数据自动重算（方案 C3）— 设计方案

**状态：** 设计已与用户逐项确认，未实现
**范围：** `apps/api`（后端后台任务 + 诊断接口）+ `apps/web`（状态小卡片）
**关联文档：** `docs/TODO_factor_ic_refresh.md`（本方案落地的待办项来源，实现完成后应更新/归档该文档）、`docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md`（M1.1 大盘情绪温度计的 `_run_budgeted_enhancement` 模式与本方案的后台 daemon 模式是同一项目已验证过的两类不同基础设施，互不影响）

---

## 0. 背景

`var/factor_ic/summary.json`（`scripts/run_factor_ic.py` 生成）供 `factor_confidence.py::load_ic_summary()` 读取，给持仓因子分（动量/风险调整/回撤）挂上"是否可回测、置信高低"的标签，是日报"量化证据卡"三路证据之一。

排查生产日报"量化证据缺失"故障时发现两层问题：

1. **打包问题（已修复，2026-07-04 方案 B）：** `.gitignore` 曾整体排除 `apps/api/var/`，导致该文件从未打进生产 Docker 镜像。已通过 `.gitkeep` 占位 + 两个 Dockerfile 新增 `COPY .../var/factor_ic` 修复。
2. **数据新鲜度问题（本方案要解决）：** 即使打包问题修复，`summary.json` 本身仍然没有任何定期重新生成的机制——当前数据是 2026-06-24 手动跑一次的静态快照（25 只基金样本），且只存在于跑脚本那台机器的本地文件系统，不会随部署同步到生产环境。`docs/TODO_factor_ic_refresh.md` 记录了三个候选方案（C1 云托管定时任务 / C2 GitHub Actions 定时提交 / C3 应用启动时检测过期自动重算），用户选择了 **C3**。

---

## 1. 现状架构（as-is）

```
scripts/run_factor_ic.py（手动运行，CLI）
  └─ build_ic_report()：排行榜池 → 线程池拉 NAV → compute_factor_ic() → 落盘
       var/factor_ic/{report.txt, summary.json}   # 仅本地文件系统，不共享

factor_confidence.py::load_ic_summary()
  └─ 读本地 var/factor_ic/summary.json（进程内 30min 缓存）→ 缺失/损坏 → {}
```

两个问题在这个 as-is 图里都能看到：没有任何箭头指向"生产环境"或"多个副本共享"，也没有任何箭头会自动触发 `build_ic_report()`。

---

## 2. 设计目标 / 非目标

### 目标

1. 数据超过 **30 天** 未更新时，后端能在无人工干预的情况下自动重新计算并让所有生产副本读到最新结果。
2. 多副本（CloudBase 云托管建议 ≥2 副本 × 2 uvicorn worker，即同时可能有 4 个进程）部署下，**同一时刻全局最多一个进程执行重算**，不产生重复计算或对 AkShare 的并发冲击。
3. 手动跑 CLI 脚本调试（如 `--limit-funds 5` 小样本）时，**默认不能污染生产数据**。
4. 提供一个只读诊断接口 + 前端小状态条，用户能随时确认"最近一次跑的时间、新不新鲜"，不需要再手动开文件看。

### 非目标

- 不改变因子 IC 回测算法本身（`factor_ic_backtest.py::compute_factor_ic`）。
- 不实现 C1（云托管定时任务）或 C2（GitHub Actions 定时提交），三选一后不做冗余兜底。
- 不引入任务队列/消息中间件等重量级基础设施——复用项目已有的"数据库原子操作 + daemon 线程轮询"模式（与 `market_shared_refresh.py`、`fund_primary_sector_precompute_loop.py` 同构）。

---

## 3. 详细设计

### 3.1 新鲜度阈值

固定 **30 天**（对齐 `docs/TODO_factor_ic_refresh.md` 里已经写的建议口径），做成配置项而非硬编码常量，允许运维按需覆盖：

```python
# app/config.py 新增
factor_ic_refresh_enabled: bool = True
factor_ic_refresh_stale_after_days: int = 30
factor_ic_refresh_check_interval_hours: int = 12
factor_ic_refresh_startup_delay_seconds: int = 300
```

（env 前缀自动为 `FUND_AI_`，如 `FUND_AI_FACTOR_IC_REFRESH_STALE_AFTER_DAYS`。）

### 3.2 结果存储：从本地文件改为共享缓存 + 本地文件兜底

**写入端**：复用 `sector_quote_cache.py` 已有的 `save_spot_snapshot(cache_key, payload)`（背后是 `app/database._connect()`，SQLite/MySQL 双方言透明切换，即你们现有 CloudBase MySQL 共享缓存机制）。新增缓存 key 常量 `factor_ic:summary:v1`（不带日期后缀——这是一份滚动覆盖的"当前最新"快照，不是按日归档）。

**读取端**：`factor_confidence.py::load_ic_summary()` 改为优先读共享缓存，缓存为空（如刚部署、后台任务还没跑完第一轮）时回退读本地 `var/factor_ic/summary.json`（即现有行为，作为兜底）。本地文件继续由 CLI 脚本写入，供手动跑脚本时人读 `report.txt`/`summary.json`；`Dockerfile` 现有的 `COPY var/factor_ic` 保留，作为首次部署后台任务尚未完成前的兜底数据源（不是本方案改动范围，但功能上仍然互补，不冲突）。

### 3.3 多副本协调：数据库原子锁

新增通用小工具 `app/services/background_job_lock.py`，提供两个函数：

```python
def try_acquire_lock(lock_key: str, holder: str, *, ttl_seconds: float) -> bool: ...
def release_lock(lock_key: str, holder: str) -> None: ...
```

底层用一张新表 `background_job_locks`（`lock_key` 主键），复用项目里 `database.py::insert_fund_transaction` 已经验证过的 `INSERT OR IGNORE`（MySQL 侧经 `db_connect.py::adapt_sql` 转成 `INSERT IGNORE`）原子性：多个进程同时插入同一个 `lock_key`，只有一个的 `rowcount==1`（抢到锁），其余全部 `rowcount==0`（跳过，不重试、不等待）。

`release_lock` 执行 `DELETE FROM background_job_locks WHERE lock_key=? AND holder=?`——按 `holder` 精确匹配才删除，不是无条件删除该 `lock_key`。这样即使本进程的锁已经因为超过 `ttl_seconds` 被判定过期、被另一个进程清理并重新抢占，本进程收尾时调用 `release_lock` 也不会误删新持有者的锁（`WHERE` 条件里的 `holder` 对不上，`DELETE` 影响 0 行，静默无操作）。

```sql
CREATE TABLE IF NOT EXISTS background_job_locks (
    lock_key TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
```

抢锁前先清理过期锁（`DELETE ... WHERE lock_key=? AND expires_at<?`），保证进程崩溃（未走到 `release_lock`）不会让锁永久卡死。过期时间 `_LOCK_TTL_SECONDS = 7200`（2 小时）写成代码常量而非配置项——这是纯粹的崩溃兜底值，不是需要按环境调整的参数。

这张表是通用基础设施，非因子 IC 专属；项目里其余几个后台 daemon（`market_shared_refresh.py`、`fund_primary_sector_precompute_loop.py` 等）目前实际上也有同样的多副本重复执行问题，但它们不在本次任务范围内，本方案不做退休/改造，仅新建这一个可复用的锁原语供本次任务使用。

### 3.4 计算逻辑归位：从脚本移到 service 层

当前 `build_ic_report()` 定义在 `scripts/run_factor_ic.py` 里，是纯 CLI 脚本。后台 daemon 线程需要调用同一份逻辑，但服务层代码不适合 import `scripts/`（这个项目里 `scripts/precompute_fund_primary_sectors.py` 已经是"厚 service + 薄 CLI 包装"的先例）。

**改动：** 把 `build_ic_report()` 及其辅助函数（`_render_report`、`_verdict`、`_default_fetch_rank`、`_default_fetch_nav`、`_CAVEATS`、`_FACTOR_LABEL` 等）整体移到新文件 `app/services/factor_ic_report.py`；`scripts/run_factor_ic.py` 改为薄包装（只剩 argparse + 调用 + 打印结果），行为对现有用户不变。

新增 `publish: bool = False` 参数：

```python
def build_ic_report(..., publish: bool = False) -> dict:
    ...  # 计算 + 落盘本地文件逻辑不变
    if publish and summary.get("available"):
        _publish_to_shared_cache(summary)  # save_spot_snapshot(SHARED_CACHE_KEY, summary)
    return summary
```

`available=False`（本次回测失败/AkShare 异常）时即使 `publish=True` 也不会推送——不能让一次失败的重算用空数据把"新鲜度"的计时器骗过去，这条规则做在 `build_ic_report` 内部，而不是要求每个调用方各自记住。

CLI 侧新增 `--publish` 显式开关（默认关闭）：

```bash
python scripts/run_factor_ic.py --universe-size 300 --nav-days 750 --publish
```

不加 `--publish` 时行为与现在完全一样（只写本地文件），避免"手动调试时随手跑一下，结果生产共享数据被小样本静默污染"的意外。后台自动任务内部调用永远带 `publish=True`。

### 3.5 触发机制：后台 daemon 线程

新文件 `app/services/factor_ic_refresh_loop.py`，与 `fund_primary_sector_precompute_loop.py` 同构：

```python
def factor_ic_refresh_loop() -> None:
    if not _enabled():
        return
    time.sleep(_startup_delay_seconds())      # 默认 300s，错开与其他启动任务抢 AkShare
    while True:
        try:
            _run_once_if_stale()
        except Exception as exc:
            logger.info("factor ic refresh cycle failed: %s", exc)
        time.sleep(_check_interval_seconds())  # 默认 12h

def _run_once_if_stale() -> None:
    status = build_factor_ic_status(stale_after_days=_stale_after_days())
    if status.get("available") and not status.get("stale"):
        return                                 # 还新鲜，什么都不做
    holder = f"{socket.gethostname()}:{os.getpid()}"
    if not try_acquire_lock(_LOCK_KEY, holder, ttl_seconds=_LOCK_TTL_SECONDS):
        return                                 # 别的进程正在跑或刚跑完，本轮跳过
    try:
        build_ic_report(publish=True)          # universe_size=300, nav_days=750（对齐现有手动口径）
    finally:
        release_lock(_LOCK_KEY, holder)
```

`app/lifespan.py` 新增一个 daemon 线程启动项，与现有 7 个后台线程并列。

**为什么是"检查间隔 12h + 阈值 30 天"而不是直接睡 30 天：** 用短间隔轮询"是否过期"，而不是长间隔定时硬跑，好处是任何原因（比如某次触发失败、或运维手动改了阈值配置）都能在最多 12 小时内被下一轮检查自我纠正，不需要等一个完整的 30 天周期。轮询本身极轻量（一次共享缓存读 + 日期比较），12 小时一次没有性能顾虑。

### 3.6 诊断接口

```python
# app/main.py
@app.get("/api/diagnostics/factor-ic-status")
def factor_ic_status() -> dict:
    """因子 IC 回测数据新鲜度诊断；全用户共享（全市场口径，非按用户区分）。"""
    return build_factor_ic_status()
```

`build_factor_ic_status()` 定义在 `factor_confidence.py`（该模块本来就是"读 IC summary"逻辑的归属地）：

```python
def build_factor_ic_status(*, stale_after_days: int | None = None) -> dict:
    threshold = stale_after_days if stale_after_days is not None else get_settings().factor_ic_refresh_stale_after_days
    raw, source = _load_raw_summary()   # 共享缓存优先，本地文件兜底；均失败→ (None, "unavailable")
    if not raw or not raw.get("run_date"):
        return {"available": False, "stale_after_days": threshold, "source": source}
    age_days = (date.today() - date.fromisoformat(str(raw["run_date"]))).days
    return {
        "available": True,
        "run_date": raw["run_date"],
        "age_days": age_days,
        "stale": age_days >= threshold,
        "stale_after_days": threshold,
        "source": source,                              # "shared_cache" | "local_file"（均缺失时上面已提前返回 available=False）
        "universe_size": raw.get("universe_size"),
        "universe_mode": (raw.get("params") or {}).get("universe_mode"),
    }
```

沿用 `/api/diagnostics/*` 既有前缀（与 `market-breadth`、`shadow-escalation-digest` 一致），走标准鉴权（该前缀下现有接口都需要登录，不做特殊豁免）。

响应示例：

```json
{
  "available": true,
  "run_date": "2026-06-24",
  "age_days": 10,
  "stale": false,
  "stale_after_days": 30,
  "source": "local_file",
  "universe_size": 25,
  "universe_mode": "top"
}
```

### 3.7 前端：因子面板里的一行小状态条

不做成独立大卡片（像情绪温度计那种），因为这不是一个需要用户经常关注、需要解读的"信号"，只是一个背景元数据。挂在"持仓因子体检"面板的标题行——用户已经在这个面板里看每个因子的 `IC·高/中/低/不足` 标签，看到这些标签时自然会想知道"这个数据多新"，就近展示最省认知负担。

新组件 `apps/web/src/components/FactorIcStatusBadge.tsx`：自包含请求（对齐 `MarketBreadthGauge`/`SectorSignalBacktestPanel` 的自包含模式），三种展示态：

- 新鲜：`因子 IC 回测数据：2026-06-24 生成（10 天前）` （灰色小字，不抢眼）
- 过期：`因子 IC 回测数据：2026-05-20 生成（已超过 30 天，等待后台自动重算）`（黄色小字）
- 无数据：`因子 IC 回测数据暂未生成`（灰色小字）

挂载点：`PortfolioDashboard.tsx`"持仓因子体检"`section` 的 `pl-panel-head` 里，标题和"展开因子评分"按钮之间。

`lib/api.ts` 新增 `FactorIcStatus` 类型与 `fetchFactorIcStatus()`，与 `fetchMarketBreadth`/`fetchShadowEscalationDigest` 同款写法。

---

## 4. 数据契约变更一览

| 位置 | 变更 |
|---|---|
| 新表 `background_job_locks` | `lock_key`(PK) / `holder` / `acquired_at` / `expires_at`；SQLite 侧懒建表，MySQL 侧加进 `mysql_bootstrap.py` |
| 共享缓存新 key | `factor_ic:summary:v1`（复用 `sector_spot_cache` 表，`sector_quote_cache.py` 现有读写函数） |
| `app/config.py` 新增 | `factor_ic_refresh_enabled` / `factor_ic_refresh_stale_after_days` / `factor_ic_refresh_check_interval_hours` / `factor_ic_refresh_startup_delay_seconds` |
| 新增只读接口 | `GET /api/diagnostics/factor-ic-status` |
| `scripts/run_factor_ic.py` | 新增 `--publish` 标志（默认关闭） |

---

## 5. 文件改动清单

**新增：**
- `app/services/background_job_lock.py`（通用分布式锁原语）
- `app/services/factor_ic_report.py`（从 `scripts/run_factor_ic.py` 抽取的计算/落盘逻辑 + `publish` 支持）
- `app/services/factor_ic_refresh_loop.py`（后台 daemon）
- `apps/web/src/components/FactorIcStatusBadge.tsx`

**修改：**
- `scripts/run_factor_ic.py`（改薄，加 `--publish`）
- `app/services/factor_confidence.py`（`load_ic_summary()` 改共享缓存优先；新增 `build_factor_ic_status()`）
- `app/lifespan.py`（新增一个 daemon 线程启动项）
- `app/config.py`（4 个新配置）
- `app/main.py`（新增诊断端点）
- `app/mysql_bootstrap.py`（新表 DDL）
- `apps/web/src/lib/api.ts`（`FactorIcStatus` 类型 + fetch 函数）
- `apps/web/src/components/PortfolioDashboard.tsx`（挂载状态条）
- `.env.example` / `docs/PROJECT_CONTEXT.md` 环境变量表（补充新配置项说明）
- `docs/TODO_factor_ic_refresh.md`（实现完成后标记该待办已解决，保留背景说明或归档）

---

## 6. 测试计划

- `test_background_job_lock.py`：抢锁互斥（同 key 第二次抢锁失败）、过期锁可被新持有者接管、按 holder 精确释放（不会误删别人重新抢到的锁）。
- `test_factor_ic_report.py`：`publish=True` 且 `available=True` 时写入共享缓存；`available=False` 时不写；`publish=False`（默认）不碰共享缓存；本地文件落盘行为不变（现有手动验证方式的回归保护）。
- `test_factor_confidence.py`（新文件）：共享缓存有数据时优先于本地文件；共享缓存为空回退本地文件；`build_factor_ic_status()` 在 29/30/31 天边界的 `stale` 判定；无任何数据源时返回 `available=False`。
- `test_factor_ic_refresh_loop.py`：`enabled=False` 时整个循环直接返回；数据新鲜时不抢锁不重算；数据过期且抢锁成功时调用 `build_ic_report(publish=True)`；抢锁失败时跳过本轮且不报错。
- `test_factor_ic_status_endpoint.py`：端点需要鉴权、返回 service 层 payload（对齐 `test_market_breadth_endpoint.py`/`test_shadow_escalation_digest_endpoint.py` 现有模式）。

全部新增测试遵循项目现有的"注入 fetch 函数/monkeypatch 依赖，不依赖真实网络"的离线测试规范（如 `conftest.py` 现有 autouse fixtures 的 stub 模式）。

---

## 7. 兼容性

- 现有 `Dockerfile`/`apps/api/Dockerfile` 的 `COPY var/factor_ic` 不受影响，继续作为部署后首次启动、后台任务尚未跑完第一轮时的本地文件兜底数据源。
- 现有本地开发流程（不配置 MySQL，纯 SQLite）不受影响——`sector_quote_cache.py` 的读写函数本身就是 SQLite/MySQL 双方言透明的，本地开发时锁表和共享缓存都落在本地 SQLite 文件里，行为和"多副本协调"的设计意图一致（本地场景下永远只有一个进程，锁形同虚设但不会出错）。
- 手动运行 CLI 脚本的现有用法（不加 `--publish`）行为完全不变。
