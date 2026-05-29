# FundPilot AI

私人基金投研助手 MVP：上传支付宝基金截图，校对持仓，按个人风控规则生成 DeepSeek V4 Pro 投研日报。

## 功能

- 支持上传截图或粘贴 OCR 文本。
- PaddleOCR 可选本地识别；未安装时仍可手动录入。
- 持仓校对表支持基金代码、名称、金额、收益率和备注。
- 内置稳健型风控规则：最大浮亏线、单只基金集中度、偏定投、拒绝追高。
- DeepSeek V4 Pro 生成日报；未配置 API Key 时会使用本地规则生成离线报告。
- SQLite 本地保存历史日报。

## 目录

```text
apps/api   FastAPI 后端
apps/web   Next.js 前端
data       SQLite 数据库
uploads    本地截图上传目录
scripts    本地启动脚本
```

## 环境变量

复制 `.env.example` 为 `.env`，填入你的 DeepSeek API Key：

```text
FUND_AI_DEEPSEEK_API_KEY=sk-your-deepseek-key
FUND_AI_DEEPSEEK_MODEL=deepseek-v4-pro
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

## 安装

后端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\api
D:\Users\hegl\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

前端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\web
npm install
```

可选 OCR：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\api
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

PaddleOCR 依赖较大，首次安装和首次识别会比较慢。你也可以先用文本粘贴和手动校对跑完整流程。

## 启动

后端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\api
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\web
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:3000
```

## 验证

后端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\api
.\.venv\Scripts\python.exe -m pytest tests -v
```

前端：

```powershell
cd D:\Code\HL_Project\fundpilot-ai\apps\web
npm run lint
npm run typecheck
```

## 隐私和边界

本项目面向个人自用。截图、数据库和上传文件默认保存在本地项目目录。DeepSeek 只会收到你确认后的持仓和风控信息，不会自动上传原始截图。报告只用于个人投研辅助，不构成投资建议，也不会执行任何交易。
