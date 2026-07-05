# 待办：因子 IC 回测数据需要定期重新生成

**创建日期：** 2026-07-04
**优先级：** 中（不阻塞当前功能，但长期影响「量化证据」因子分量的可用性）
**背景任务：** 排查日报「量化证据缺失」故障时发现（见 `docs/PROJECT_CONTEXT.md` 2026-07-04 更新记录）

> GitHub issue 创建失败（当前配置的 personal access token 无 issue 创建权限），
> 改为落这份文档追踪，待你决定处理方式后可删除本文件或转成正式 issue。

## 背景

`var/factor_ic/summary.json`（`scripts/run_factor_ic.py` 生成）供
`factor_confidence.py::load_ic_summary()` 读取，给持仓因子分（动量/风险调整/回撤）
挂上「是否可回测、置信高低」的标签，是日报「量化证据卡」三路证据之一。

## 已修复的部分（2026-07-04，方案 B）

1. `var/` 整体被 `.gitignore` 排除，导致这份数据从未打进生产 Docker 镜像——容器里
   该文件永远不存在，因子分这一路在线上恒为「不足」。
2. 修复：`apps/api/var/factor_ic/.gitkeep` 占位文件保证目录在任何 checkout 里都
   存在；`Dockerfile`（根目录 + `apps/api/`）新增
   `COPY .../var/factor_ic .../var/factor_ic`；目录必然存在故不会导致构建失败，
   `summary.json` 本身仍受 `.gitignore` 排除、不会被提交或打入镜像。
3. 已用临时 git worktree 验证：全新 checkout 里 `var/factor_ic/` 目录存在（仅含
   `.gitkeep`），`summary.json` 正确缺失，Docker COPY 指令的源路径不会报"不存在"。

## 待办（本文档跟踪的长期任务）

即使方案 B 落地，生产环境目前**依然拿不到** `summary.json`——因为它从未被提交，
镜像里的 `var/factor_ic/` 目录永远只有 `.gitkeep`。这只是让"构建不报错"，没有
解决"生产环境有这份数据"的根本问题。

当前数据现状：`var/factor_ic/summary.json` 是 2026-06-24 手动跑一次
`scripts/run_factor_ic.py --universe-size 300 --nav-days 750` 生成的静态快照
（实际样本 25 只基金），随时间推移会越来越不能代表当前市场状态。

需要设计并实现一套机制，定期（建议每周或每月）重新运行该脚本并把产物同步到生产
环境，可选方向：

- **方案 C1**：CloudBase 云托管支持的定时任务/cron job，按 schedule 跑一次
  `scripts/run_factor_ic.py`，把结果写到持久化存储（而不是容器本地文件系统，
  重启会丢）。
- **方案 C2**：GitHub Actions 定时 workflow（`schedule` trigger），跑完后把
  `summary.json` 提交进仓库某个专门允许追踪的路径（需要评估「回测结果快照」混进
  源码仓库是否合适）或推送到对象存储，部署时拉取。
- **方案 C3**：应用启动时（`lifespan.py`）如果发现本地缓存的 `summary.json`
  缺失或过期（比如超过 30 天），后台异步触发一次重新计算并写入持久化存储/缓存
  （复用现有 `sector_quote_cache.py` 的 MySQL 共享缓存机制），不阻塞启动。

## 相关文件

- `apps/api/app/services/factor_confidence.py`（读取方）
- `apps/api/scripts/run_factor_ic.py`（生成脚本）
- `apps/api/var/factor_ic/.gitkeep`（方案 B 占位文件，含详细背景说明）
- `Dockerfile` / `apps/api/Dockerfile`（方案 B 改动）
- `docs/PROJECT_CONTEXT.md`（2026-07-04 更新记录）
