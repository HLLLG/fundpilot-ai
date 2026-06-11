# CloudBase 云托管部署指南

面向 ≤5 人的私有部署：FastAPI 云托管 + CloudBase MySQL + 静态 Web + 微信小程序。

## 架构

```text
微信小程序 ──HTTPS──► CloudBase 云托管（FastAPI Docker）
Web 静态托管  ──HTTPS──► 同上 API
                              │
                              ▼
                    CloudBase MySQL
```

## 1. 准备 CloudBase 环境

### 用哪种账号登录控制台？

| 场景 | 建议 |
|------|------|
| 近期要做**微信小程序 + 微信登录** | 用 **微信公众平台** 账号登录 CloudBase，便于绑定小程序 AppID、配置合法域名 |
| 先上 **Web + API**，小程序稍后 | **腾讯云账号** 亦可；后续再在控制台关联小程序 |

### 创建环境

1. 登录 [腾讯云 CloudBase 控制台](https://tcb.cloud.tencent.com/)
2. 创建环境，记录 **环境 ID**（`FUND_AI_CLOUDBASE_ENV_ID`）
3. 开通 **MySQL** 数据库，记录连接串
4. 身份认证 → 开启 **微信小程序登录**
5. （可选）自定义登录 → 下载私钥 JSON → 保存到服务器

## 2. 迁移数据（本地 SQLite → MySQL）

```bash
cd apps/api
pip install pymysql

python ../../scripts/migrate_sqlite_to_mysql.py \
  --sqlite ../../data/app.db \
  --mysql-url "mysql://user:pass@host:3306/fundpilot"
```

## 3. 构建并部署 API（云托管）

```bash
# 项目根目录
docker build -f apps/api/Dockerfile -t fundpilot-api .

# 在 CloudBase 云托管创建服务，上传镜像或绑定 CI
# 环境变量示例：
#   FUND_AI_DATABASE_URL=mysql://...
#   FUND_AI_JWT_SECRET=<随机32字节以上>
#   FUND_AI_DEEPSEEK_API_KEY=<你的Key>
#   FUND_AI_CLOUDBASE_ENV_ID=<环境ID>
#   FUND_AI_CORS_ORIGINS=https://你的Web域名
#   FUND_AI_OCR_PRELOAD=false   # 建议关闭，减小内存
```

本地可先验证：

```bash
export FUND_AI_JWT_SECRET=your-secret-32chars-minimum
docker compose -f docker-compose.cloud.yml up --build
```

## 4. 部署 Web 前端

```bash
cd apps/web
# 构建时指定 API 地址
NEXT_PUBLIC_API_BASE_URL=https://你的云托管API域名 npm run build
```

将 `apps/web/out` 或 `.next/static` 产物上传到 **CloudBase 静态网站托管**。

## 5. 微信小程序

1. `apps/miniprogram/utils/config.js` 设置 `API_BASE` 与 `CLOUDBASE_ENV_ID`
2. 微信公众平台 → 开发管理 → 服务器域名 → 添加云托管 API 域名
3. 生产环境设置 `FUND_AI_CLOUDBASE_AUTH_DEV_MODE=false`
4. 用微信开发者工具上传审核

开发联调：后端 `.env` 设 `FUND_AI_CLOUDBASE_AUTH_DEV_MODE=true`，小程序登录会走开发 UID。

## 6. Web 与小程序账号统一

1. Web 邮箱注册登录
2. 打开 **账号设置**（`/settings`）填写 CloudBase 用户 ID 绑定；或调用 `POST /api/auth/bind-wechat`（body 含 `cloudbaseUid` 或 `cloudbaseAccessToken`）
3. 小程序用同一 CloudBase 微信账号登录 → 看到相同持仓

开发联调时可在 CloudBase 控制台或小程序登录响应中查看 UID；生产环境更推荐用 `cloudbaseAccessToken` 绑定（设置页后续可扩展）。

## 环境变量速查

| 变量 | 说明 |
|------|------|
| `FUND_AI_DATABASE_URL` | `mysql://user:pass@host:3306/db` |
| `FUND_AI_JWT_SECRET` | JWT 签名密钥 |
| `FUND_AI_CLOUDBASE_ENV_ID` | 云开发环境 ID |
| `FUND_AI_CLOUDBASE_CUSTOM_LOGIN_KEY` | 自定义登录私钥 JSON 路径 |
| `FUND_AI_CLOUDBASE_AUTH_DEV_MODE` | `true` 时小程序可用 dev UID（仅开发） |
