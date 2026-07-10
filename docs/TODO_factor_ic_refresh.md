# 已解决：因子 IC 回测数据定期重新生成

**创建日期：** 2026-07-04
**解决日期：** 2026-07-10
**状态：** 已解决，待生产 Secret 配置与首次手动 workflow 验收
**背景任务：** 排查日报「量化证据缺失」故障时发现

## 最终方案

采用已确认的[外部刷新设计](superpowers/specs/2026-07-04-factor-ic-refresh-automation-design.md)：

1. [Factor IC Refresh workflow](../.github/workflows/factor-ic-refresh.yml) 每周日北京时间
   03:23 或手动运行固定 `sampled 500→300` 生产口径。
2. runner 在 GitHub 临时目录生成 schema v1 快照；发布器和 API 共同执行 240 只有效
   基金、12 个回测期、四因子有效统计等质量门槛。
3. `POST /api/internal/factor-ic-snapshots` 用独立发布 Token 鉴权，向共享
   MySQL `factor_ic_snapshots` 追加快照；重复发布幂等，旧快照和低质量结果不会覆盖
   最后一份有效数据。
4. `factor_confidence` 数据库优先、本地文件兜底；`GET
   /api/diagnostics/factor-ic-status` 和前端 `FactorIcStatusBadge` 展示日期、样本数与
   30 天过期状态。

生产 API 不运行回测、不增加 refresh daemon、分布式锁或任务队列。一次性配置与验收
步骤见 [`docs/deploy/cloudbase.md`](deploy/cloudbase.md)。

## 为什么不采用旧 C3

旧 C3 计划由 `lifespan.py` 后台线程在本地文件缺失或过期时计算。当前 CloudBase 生产
服务使用 **2～5 个自动伸缩实例**：后台线程可能重复运行，并可能在缩容时中断；MySQL
故障回落到各容器 SQLite 后也无法提供真正的全局锁或共享结果。因此旧 C3 已明确拒绝，
由容器外 GitHub Actions 承担可靠调度和重计算。

## 原始背景（保留审计）

`var/factor_ic/summary.json` 由 `scripts/run_factor_ic.py` 生成，曾是
`factor_confidence.py::load_ic_summary()` 的唯一数据源，用于给持仓因子分挂可回测
置信标签，也是日报「量化证据卡」三路证据之一。

2026-07-04 的方案 B 只解决 Docker 构建问题：`.gitkeep` 保证
`apps/api/var/factor_ic/` 在干净 checkout 中存在，两个 Dockerfile 可以安全 COPY；
真实 `summary.json` 仍受 `.gitignore` 排除，不会提交或打进镜像。此前本地静态快照由
2026-06-24 手动命令生成，实际仅 25 只有效基金，无法长期代表当前市场。

当时评估过：

- C1：CloudBase 定时任务写持久化存储；
- C2：GitHub Actions 生成后提交源码或写对象存储；
- C3：应用启动后台线程重算。

最终实现保留 C2 的“容器外 GitHub Actions 计算”，但不把生成物提交源码，而是通过
最小权限内部 API 写入共享 MySQL，解决了源码污染、多副本和容器本地文件易失问题。

## 相关文件

- `.github/workflows/factor-ic-refresh.yml`
- `apps/api/scripts/run_factor_ic.py`
- `apps/api/scripts/publish_factor_ic.py`
- `apps/api/app/services/factor_ic_snapshot.py`
- `apps/api/app/services/factor_confidence.py`
- `apps/web/src/components/FactorIcStatusBadge.tsx`
- `docs/superpowers/specs/2026-07-04-factor-ic-refresh-automation-design.md`
