# 设计文档：删除自动化工作流，统一异步分析 + 悬浮任务面板

**日期：** 2026-05-31  
**状态：** 已批准  
**范围：** 后端 Python + 前端 TypeScript/React

---

## 背景与目标

当前项目有两套分析触发路径：
1. 手动上传截图 → 同步/异步分析（核心路径）
2. 自动化工作流：inbox 目录监听 + 定时 scheduler → 自动 OCR + 自动分析

用户反馈自动化工作流实际体验一般，且后台轮询持续占用资源。决定：
- **删除**：自动化工作流（inbox watcher、scheduler、收件箱 UI）
- **保留并强化**：异步分析执行流程
- **统一**：点击"生成报告"始终走后台异步，不再提供同步/异步切换
- **新增**：右下角悬浮任务面板（取代原来的 banner 提示）

---

## 后端设计

### 删除的文件（完整删除）

| 文件 | 原用途 |
|------|--------|
| `apps/api/app/services/inbox_watcher.py` | 轮询监听 inbox 目录 |
| `apps/api/app/services/inbox_processor.py` | 处理 inbox 图片 → OCR → holding |
| `apps/api/app/services/scheduler.py` | 工作日定时提醒 + 自动分析 |
| `apps/api/app/services/inbox_store.py` | 存储 inbox_events（仅自动化使用） |

### 删除的 API 端点（在 main.py 中移除）

| 方法 | 路径 | 原用途 |
|------|------|--------|
| GET | `/api/automation/status` | 收件箱/定时配置状态 |
| GET | `/api/inbox/events` | 列出待处理收件箱事件 |
| POST | `/api/inbox/events/{id}/consume` | 标记事件已处理 |
| POST | `/api/inbox/events/{id}/analyze` | 从事件提交异步分析 |

### 保留的异步端点（核心流程）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/analyze/async` | 提交分析任务，返回 `{job_id, status}` |
| GET | `/api/jobs/{job_id}` | 轮询任务状态；completed 时含 report |

### 修改 lifespan.py

移除：
- `start_inbox_watcher()` / `stop_inbox_watcher()` 调用
- `start_scheduler()` / `stop_scheduler()` 调用
- 相关 import

保留：
- `job_store` 的 ThreadPoolExecutor 初始化（异步分析任务需要）

### 数据库

- 删除 `inbox_events` 表（不再写入，迁移脚本中标注废弃）
- 保留 `analysis_jobs` 表（异步任务状态）

---

## 前端设计

### 删除的文件（完整删除）

| 文件 | 原用途 |
|------|--------|
| `apps/web/src/components/AutomationPanel.tsx` | 收件箱事件 + 自动化开关 UI |
| `apps/web/src/components/DailyWorkflowBar.tsx` | 每日工作流提示条 |

### 修改 Dashboard.tsx

**删除：**
- `pollInboxEvents()` 函数
- 4 秒轮询 `useEffect`（收件箱轮询）
- `autoAnalyzeOnOcr`、`useAsyncAnalyze` 状态
- `handleAnalyzeFromInbox()` 函数
- `<AutomationPanel />` 和 `<DailyWorkflowBar />` 引用
- "自动化" Tab（如有）

**修改 `runAnalyze()`：**
- 移除同步路径（直接调用 `/api/analyze`）
- 统一走异步：`startAnalyzeJob()` → 得到 `job_id` → 触发 `<JobStatusFloat />` 显示

**新增状态：**
```typescript
const [activeJobId, setActiveJobId] = useState<string | null>(null)
const [jobDone, setJobDone] = useState<boolean>(false)
```

### 新增 JobStatusFloat 组件

**文件：** `apps/web/src/components/JobStatusFloat.tsx`

**位置：** 右下角固定悬浮，`fixed bottom-6 right-6 z-50`

**状态与 UI：**

```
分析中（running/pending）：
┌─────────────────────────────┐
│ ⟳  正在生成报告...           │
│    预计 10-30 秒             │
└─────────────────────────────┘

完成（completed）：
┌─────────────────────────────┐
│ ✓  报告已生成                │
│ [查看报告]          [关闭]   │
└─────────────────────────────┘

失败（failed）：
┌─────────────────────────────┐
│ ✕  分析失败                  │
│  <错误摘要>                  │
│ [重试]              [关闭]   │
└─────────────────────────────┘
```

**Props：**
```typescript
interface JobStatusFloatProps {
  jobId: string | null        // null 时不渲染
  onComplete: (report: Report) => void
  onClose: () => void
  onRetry: () => void
}
```

**内部轮询：** 组件内部调用 `waitForAnalysisJob(jobId)`，完成后通过 `onComplete` 回调通知 Dashboard 切换到报告 Tab。

### 修改 api.ts

**删除的函数：**
- `listInboxEvents()`
- `consumeInboxEvent()`
- `analyzeInboxEvent()`
- `fetchAutomationStatus()`

**保留的函数：**
- `startAnalyzeJob()`
- `fetchAnalysisJob()`
- `waitForAnalysisJob()`
- `analyzeHoldings()`（同步端点保留作兜底，但前端不主动调用）

### 修改 storage.ts

**删除的 localStorage key：**
- `useAsyncAnalyze`
- `autoAnalyzeOnOcr`
- inbox seen 事件记录

**保留：**
- `analysisMode`（fast/deep）
- `investorProfile`
- 其他偏好

### 修改 notifications.ts

删除 inbox 相关通知逻辑（收件箱 OCR 完成通知）。保留分析完成通知（由 JobStatusFloat 触发）。

---

## 数据流（新）

```
用户点击"生成报告"
  ↓
Dashboard.runAnalyze()
  → POST /api/analyze/async
  → 得到 job_id
  → setActiveJobId(job_id)
  ↓
<JobStatusFloat jobId={activeJobId} />
  → 内部 waitForAnalysisJob(jobId) 轮询（1.5s 间隔）
  → status=completed → onComplete(report)
  ↓
Dashboard.onComplete(report)
  → setReport(report)
  → 切换到报告 Tab
  → 可选：桌面通知
```

---

## 清理范围（额外）

以下代码/注释也一并清理：

- `main.py` 中 inbox/scheduler 相关 import
- `database.py` 中 `inbox_events` 表的创建语句（标注废弃，不删除迁移逻辑）
- `config.py` 中 `INBOX_*`、`SCHEDULE_*` 相关配置变量
- `.env.example` 中自动化相关的环境变量注释

---

## 测试要点

1. 点击"生成报告"→ 右下角出现悬浮面板（转圈）
2. 分析完成 → 悬浮面板变为"查看报告"按钮 → 点击跳转报告 Tab
3. 分析失败 → 悬浮面板显示错误 + 重试按钮
4. 分析中可以自由操作页面其他区域（校对持仓、切换 Tab）
5. 后端不再启动 inbox_watcher 和 scheduler 线程
6. 已有的 38 项 pytest 测试全部通过
