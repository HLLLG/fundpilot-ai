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
```

迁移前建议先在历史页导出 SQLite 备份。

## 3. 构建并部署 API

```bash
# 项目根目录
docker build -t fundpilot-api -f apps/api/Dockerfile .
```

云托管环境变量示例：

```text
FUND_AI_DATABASE_URL=mysql://...
FUND_AI_JWT_SECRET=<随机32字节以上>
FUND_AI_FACTOR_IC_PUBLISH_TOKEN=<独立随机发布Token>
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

## 6. 常见问题：浏览器报 CORS / Failed to fetch

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
| `FUND_AI_FACTOR_IC_STALE_AFTER_DAYS` | 快照过期提示阈值，默认 30 天 |
| `FUND_AI_CLOUDBASE_ENV_ID` | CloudBase 环境 ID；用于 Web 静态域名 CORS 自动放行 |
| `FUND_AI_CORS_ORIGINS` | 生产 Web 域名，逗号分隔 |
