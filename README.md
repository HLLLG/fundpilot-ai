# FundPilot AI

私人基金投研助手 MVP：上传支付宝基金截图，校对持仓，按个人风控规则生成 DeepSeek V4 Pro 投研日报。

你的默认终端路径是：

```bash
/d/Code/HL_Project/fundpilot-ai
```

所以下面的命令都按 **Git Bash / MINGW64** 写法提供。

## 功能

- 支持上传截图或粘贴 OCR 文本。
- PaddleOCR 可选本地识别；未安装时仍可手动录入。
- 支持上传养基宝单基金详情截图建立基金档案。
- 总览截图缺少基金代码时，会用本地基金档案自动补全完整名称和代码。
- 持仓校对表支持基金代码、名称、金额、持有/当日收益、板块涨跌和备注。
- 内置稳健型风控规则：最大浮亏线、单只基金集中度、偏定投、拒绝追高。
- DeepSeek V4 Pro 生成日报；模型可调用 `fetch_market_news` 从东方财富补拉板块/主题新闻（也可关闭，仅用预取新闻）。
- 报告含**逐基金操作建议**（动作、参考金额、板块利好/利空要点）与新闻列表；离线模式同样预拉新闻并用本地规则生成建议。
- **今日一键分析**：上传总览图后自动识别并生成日报；支持**快速/深度**两种分析模式。
- 报告支持与上一份日报**对比差异**；可**导出 Markdown**。
- 基金档案支持 **JSON 导入/导出**；风控参数保存在浏览器本地。
- SQLite 本地保存历史日报，支持在历史侧栏删除旧报告。

### 自动化（阶段 2）

- **收件箱文件夹**：把总览截图保存到 `uploads/inbox`（路径见页面「自动化」面板），后端自动 OCR 并推送浏览器通知。
- **后台异步分析**：提交任务后不必干等页面；完成后桌面通知（需点击「开启桌面通知」）。
- **交易日定时提醒**：默认工作日 14:25 推送提醒（可在 `.env` 配置）；可选 `FUND_AI_SCHEDULE_AUTO_ANALYZE=true` 在到点且收件箱有待处理持仓时自动快速分析。
- 可选：**识别完成后自动分析**、**后台异步分析**（前端开关，保存在浏览器）。

**分析模式说明：**

| 模式 | 说明 |
|------|------|
| 快速 | 使用 `deepseek-v4-flash`；仍会预取东方财富新闻，但不让模型多轮调用新闻 Tool，适合交易日赶时间。 |
| 深度 | 使用 `.env` 中的 Pro 模型；模型可主动 `fetch_market_news` 补拉新闻，更慢但更全。 |

## 目录

```text
apps/api        FastAPI 后端
apps/web        Next.js 前端
data            SQLite 数据库
uploads/inbox   拖入总览截图 → 自动 OCR（阶段 2）
uploads         其他本地上传
scripts         dev.sh / dev.ps1 一键启动
docs            项目文档（含 AI 上下文）
```

面向 AI 或新开发者的架构与业务说明见 **[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)**，可在新对话开头 `@` 该文件，避免重复扫描全仓库。

## 环境变量

在项目根目录复制模板：

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 DeepSeek API Key：

```text
FUND_AI_DEEPSEEK_API_KEY=sk-your-deepseek-key
FUND_AI_DEEPSEEK_MODEL=deepseek-v4-pro
FUND_AI_DEEPSEEK_TIMEOUT_SECONDS=300
FUND_AI_DEEPSEEK_MAX_TOKENS=384000
FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT=384000
FUND_AI_NEWS_ENABLED=true
FUND_AI_NEWS_MAX_TOPICS=5
FUND_AI_NEWS_PER_TOPIC=5
FUND_AI_NEWS_TOOL_MAX_ROUNDS=3
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

`.env` 已被 `.gitignore` 忽略，不会提交到 Git。

如果 DeepSeek 报 `read operation timed out`，通常是 `deepseek-v4-pro` 响应较慢。可以先把
`FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` 调大，例如 `300`；如果你更看重速度，也可以把
`FUND_AI_DEEPSEEK_MODEL` 改成 `deepseek-v4-flash`。

新闻相关（可选）：

| 变量 | 说明 |
|------|------|
| `FUND_AI_NEWS_ENABLED` | `false` 时不注册新闻 Tool，仍可在分析前预取新闻 |
| `FUND_AI_NEWS_MAX_TOPICS` | 从持仓推导的检索主题上限 |
| `FUND_AI_NEWS_PER_TOPIC` | 每个主题保留的新闻条数 |
| `FUND_AI_NEWS_TOOL_MAX_ROUNDS` | 模型调用 `fetch_market_news` 的最大轮数 |

自动化（阶段 2，可选）：

| 变量 | 说明 |
|------|------|
| `FUND_AI_INBOX_ENABLED` | 是否监视 `FUND_AI_INBOX_DIR` |
| `FUND_AI_INBOX_DIR` | 收件箱绝对路径，默认 `uploads/inbox` |
| `FUND_AI_INBOX_POLL_SECONDS` | 扫描间隔（秒） |
| `FUND_AI_SCHEDULE_ENABLED` | 是否启用交易日定时提醒 |
| `FUND_AI_SCHEDULE_TIME` | 提醒时间，如 `14:25` |
| `FUND_AI_SCHEDULE_WEEKDAYS_ONLY` | 仅周一至周五 |
| `FUND_AI_SCHEDULE_AUTO_ANALYZE` | 到点且收件箱有待处理持仓时自动快速分析 |

## 安装

后端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api

# 如果 .venv 已存在，可以跳过这行
/d/Users/hegl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -m venv .venv

./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm install
```

可选 OCR：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m pip install -r requirements-ocr.txt
```

PaddleOCR 依赖较大，首次安装和首次识别会比较慢。你也可以先用文本粘贴和手动校对跑完整流程。

## 启动

**推荐：一条命令同时启动前后端（Git Bash）：**

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh
```

或 Windows PowerShell：

```powershell
.\scripts\dev.ps1
```

也可以分别开两个终端：

开第一个 Git Bash 终端启动后端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

开第二个 Git Bash 终端启动前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm run dev
```

前端 dev 脚本使用 Next.js 的 webpack 模式，避开 Windows 下偶发的 Turbopack panic。

浏览器打开：

```text
http://127.0.0.1:3000
```

## 推荐使用流程

首次使用时：

```text
1. 打开养基宝单基金详情页。
2. 每只基金上传一次详情截图，建立基金档案。
3. 档案会保存基金代码、完整名称、持仓成本、份额、持仓占比、持有天数、关联板块等信息。
```

日常使用时（网页上传）：

```text
1. 只上传一张养基宝总览截图。
2. 系统识别当日收益、关联板块涨跌和持仓收益。
3. 系统用基金档案自动补全基金代码。
4. 校对后生成每日基金操作日报（可选快速/深度模式）。
```

日常使用时（收件箱，推荐）：

```text
1. 启动 dev.sh，浏览器打开工作台并开启桌面通知。
2. 手机/养基宝截图 → 保存到 uploads/inbox/（路径见「自动化」面板）。
3. 等待通知「已识别 N 条持仓」→ 校对 → 生成日报（可开「识别后自动分析」）。
4. 交易日 14:25 会收到提醒（需保持页面打开以便轮询）。
```

## 验证

后端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m pytest tests -v
```

前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm run lint
npm run typecheck
npm run build
```

## 常见 Git Bash 路径写法

Git Bash 里不要写：

```bash
cd D:\Code\HL_Project\fundpilot-ai
```

要写成：

```bash
cd /d/Code/HL_Project/fundpilot-ai
```

Windows 可执行文件路径也要用 `/d/...` 形式，或者使用已有虚拟环境里的：

```bash
./.venv/Scripts/python.exe
```

## 隐私和边界

本项目面向个人自用。截图、数据库和上传文件默认保存在本地项目目录。DeepSeek 会收到你确认后的持仓、风控参数、净值摘要，以及经 AkShare 从公开渠道拉取的新闻标题/摘要（用于生成报告），不会自动上传原始截图。报告只用于个人投研辅助，不构成投资建议，也不会执行任何交易。
