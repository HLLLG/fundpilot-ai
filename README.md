# FundPilot AI

私人基金投研助手：上传支付宝/养基宝总览截图更新账户汇总，按个人风控规则生成 DeepSeek V4 Pro 投研日报。

你的默认终端路径是：

```bash
/d/Code/HL_Project/fundpilot-ai
```

所以下面的命令都按 **Git Bash / MINGW64** 写法提供。

## 功能

- 支持上传截图或粘贴 OCR 文本。
- PaddleOCR 可选本地识别；未安装时仍可手动录入。
- 支持上传养基宝总览截图，以及**支付宝「我的基金」持有列表**截图（预览确认后更新账户汇总）。
- 总览 OCR 缺少基金代码时，优先用 **AkShare 基金名称表**查码，本地 `fund_profiles` 元数据按名称兜底。
- 份额、成本、板块、购入日等元数据由 OCR **自动维护**（SQLite `fund_profiles`），无需单独「基金档案」页。
- 养基宝总览 OCR：识别多只基金、区分当日/持有收益（未收盘时当日列为 `-` 则不填当日收益）；**OCR 漏负号**时按收益率/账户总收益/独立行减号规则补符号（当日收益额、板块涨跌与收益率一致）。详见 `docs/PROJECT_CONTEXT.md`。
- **持有**页养基宝式账户汇总：基金代码、名称、金额、持有/当日收益、板块涨跌；**估算当日收益率**（≈ 板块涨跌 + 持有收益率，与传给模型一致）。
- **盈亏分析**页：收益走势（我的收益 vs 沪深300）、盈亏日历、当日 TOP5、持仓分布图。
- 内置稳健型风控规则：最大浮亏线、单只基金集中度、偏定投、拒绝追高（规则守卫 + 传入 AI `profile`）。
- DeepSeek V4 Pro 生成日报；新闻来自东财/基金公告/宏观主题（可配置），生成前用 **Flash 按主题摘要**（`topic_briefs`），深度模式还可 `fetch_market_news` 补拉。
- 报告含**逐基金操作建议**、**主题要闻摘要**与**新闻原文列表**；利好/利空标题须能对应原文（`news_citation` 守卫）。
- **后台异步分析**：点击「生成报告」后后台执行，右下角悬浮面板查看进度；完成后桌面通知（需开启通知权限）。
- 支持**快速/深度**两种分析模式（生成日报时选择）。
- 报告支持与上一份日报**对比差异**；可**导出 Markdown**。
- **报告追问**：分析页「决策建议」右侧可与 AI 流式对话（Markdown 渲染、加高可滚动面板）；支持快速/深度追问模式，深度模式可按需拉取最新新闻；对话按报告存 SQLite，可**导出对话 Markdown**。
- **SQLite 全库备份/恢复**（历史页）；风控参数、分析模式、追问模式保存在浏览器本地，风控画像同步至 SQLite。
- SQLite 本地保存历史日报，支持在历史侧栏删除旧报告。
- 基金详情页：**业绩走势**（近1月～3年、本基金 vs 沪深300）、历史净值滚动加载；**持有天数**可点选首次购入日期；关联板块分时图。

**分析模式（生成日报）：**

| 模式 | 说明 |
|------|------|
| 快速 | 使用 `deepseek-v4-flash`；预取新闻 + 主题摘要，不调用新闻 Tool，适合交易日赶时间。 |
| 深度 | 使用 `.env` 中的 Pro 模型；主题摘要 + 可主动 `fetch_market_news` 补拉，更慢但更全。 |

**追问模式（报告页右侧对话）：**

| 模式 | 说明 |
|------|------|
| 快速 | Flash；仅基于已生成日报与历史对话作答。 |
| 深度 | Pro；模型在需要时可调用 `fetch_market_news` 补充当日新闻后再流式回答。 |

## 目录

```text
apps/api        FastAPI 后端
apps/web        Next.js 前端
data            SQLite 数据库
uploads         本地上传截图
scripts         dev.sh / dev.ps1 一键启动
docs            项目文档（含 AI 上下文）
```

面向 AI 或新开发者的架构与业务说明见 **[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)**，可在新对话开头 `@` 该文件，避免重复扫描全仓库。

## 环境变量

在项目根目录复制模板：

```bash
cp .env.example .env
```

编辑 `.env`，将 DeepSeek 控制台复制的 API Key 填入 `FUND_AI_DEEPSEEK_API_KEY`（勿提交到 Git）：

```text
FUND_AI_DEEPSEEK_API_KEY=
FUND_AI_DEEPSEEK_MODEL=deepseek-v4-pro
FUND_AI_DEEPSEEK_MODEL_FAST=deepseek-v4-flash
FUND_AI_DEEPSEEK_TIMEOUT_SECONDS=300
FUND_AI_DEEPSEEK_MAX_TOKENS=384000
FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT=384000
FUND_AI_NEWS_ENABLED=true
FUND_AI_NEWS_MAX_TOPICS=5
FUND_AI_NEWS_PER_TOPIC=5
FUND_AI_NEWS_TOOL_MAX_ROUNDS=3
FUND_AI_NEWS_SOURCES=eastmoney,announcement,macro
FUND_AI_NEWS_SUMMARIZE=true
FUND_AI_NEWS_MACRO_TOPIC=上证指数
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
| `FUND_AI_NEWS_SOURCES` | 新闻源：`eastmoney`、`announcement`、`macro` |
| `FUND_AI_NEWS_SUMMARIZE` | 是否用 Flash 按主题生成 `topic_briefs` |
| `FUND_AI_NEWS_MACRO_TOPIC` | 宏观主题检索词（如上证指数） |

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

```text
1. bash scripts/dev.sh → 打开 http://127.0.0.1:3000（默认「持有」Tab）
2. 启动后自动恢复上次持仓；需更新金额时点击「新增持有」上传支付宝/养基宝总览截图
3. 预览确认后写入账户汇总，并自动同步份额、查码、刷新板块涨跌
4. 「盈亏分析」查看收益走势、盈亏日历、当日 TOP5
5. 点击持仓行可查看基金详情（业绩走势、持有天数、板块分时）
6. 切到「生成日报」→ 确认风控画像（偏定投/拒绝追高等）→ 选快速/深度 → 生成日报
7. 右下角 JobStatusFloat 查看进度；完成后可在报告页追问（可选深度模式拉最新新闻）
8. 换机迁移：用户菜单 → 历史日报 → SQLite 备份导出/导入
9. 需要留存时可导出日报 Markdown 或导出对话 Markdown
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

本项目面向个人自用。截图、数据库和上传文件默认保存在本地项目目录。DeepSeek 会收到你确认后的持仓、风控参数、净值摘要、主题新闻摘要（Flash 生成）、新闻标题/短摘要，以及已生成日报全文（追问时）；不会自动上传原始截图。报告与对话只用于个人投研辅助，不构成投资建议，也不会执行任何交易。
