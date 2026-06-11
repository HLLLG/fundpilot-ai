# 用户认证与多租户数据隔离 — 设计规格

> **版本：** 2026-06-11  
> **状态：** 待审阅  
> **范围：** 邮箱登录、登录门槛、基金数据按用户隔离、Web + 微信小程序双端、面向 CloudBase 部署

---

## 1. 背景与目标

FundPilot AI 当前为**单用户本地应用**（`portfolio_state id=1`、无真实鉴权）。需要扩展为：

1. 用户注册/登录（**邮箱 + 密码**），未登录不可查看持有基金
2. 所有基金相关业务数据按 **userId** 隔离
3. 支持 **浏览器（Next.js Web）** 与 **微信小程序** 双端访问同一后端
4. 面向未来部署到 **腾讯云 CloudBase**（云托管 + MySQL），≤5 人使用
5. **不转 Java**；Phase 1 本地 SQLite 开发，上云迁移 MySQL；**不使用 Redis**

### 非目标（本阶段）

- 企业 SSO / 多设备互踢
- 微搭 WeDa 低代码重做前端
- 券商对接、自动下单

---

## 2. 技术决策摘要

| 决策 | 选择 | 理由 |
|------|------|------|
| 后端 | 保留 FastAPI | OCR/AkShare/AI 流水线均在 Python；296 项 pytest |
| 前端 Web | 保留 Next.js | 现有 Dashboard 等组件可直接扩展 |
| 小程序 | 独立小程序 + CloudBase SDK | 与 Web 共用 API；微信登录走 CloudBase |
| 本地 DB | SQLite（Phase 1） | 快速迭代、测试友好 |
| 云上 DB | CloudBase MySQL（Phase 2） | 云托管无持久磁盘，SQLite 不可靠 |
| 鉴权 Web | 自建 JWT（邮箱密码） | 本地/云上行为一致，实现可控 |
| 鉴权小程序 | CloudBase 微信登录 + Custom Ticket | 与 CloudBase 生态对齐，免维护 openid |
| 缓存/会话 | 无 Redis | 5 人规模、仅需登录门槛 |
| 用户表字段命名 | **驼峰 camelCase** | 按产品要求（仅 `users` 表及关联外键列） |

---

## 3. 部署架构（目标态）

```text
┌──────────────────────┐     ┌──────────────────────┐
│  Web（Next.js）       │     │  微信小程序           │
│  /login /register    │     │  CloudBase 微信登录   │
│  JWT in HttpOnly     │     │  + Custom Ticket     │
└──────────┬───────────┘     └──────────┬───────────┘
           │                            │
           └────────────┬───────────────┘
                        │ HTTPS + Authorization
           ┌────────────▼───────────────┐
           │  CloudBase 云托管 Docker    │
           │  FastAPI（OCR/AI/盈亏/日报） │
           └────────────┬───────────────┘
                        │
           ┌────────────▼───────────────┐
           │  CloudBase MySQL           │
           │  users + 业务表 userId      │
           └────────────────────────────┘
```

**OCR 说明：** PaddleOCR 包体大，部署用**云托管常驻容器**，不用 HTTP 云函数。

---

## 4. 数据模型

### 4.1 `users` 表（驼峰字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER/BIGINT PK | 自增主键 |
| `userRole` | VARCHAR(32) | `user` \| `admin`，默认 `user` |
| `username` | VARCHAR(64) | 显示昵称 |
| `userAccount` | VARCHAR(128) UNIQUE | 登录邮箱 |
| `passwordHash` | VARCHAR(255) | bcrypt；API 永不返回 |
| `bio` | VARCHAR(500) | 简介，默认空 |
| `avatarUrl` | VARCHAR(512) | 头像 URL，默认空 |
| `cloudbaseUid` | VARCHAR(64) NULL | CloudBase 账号 UID（小程序绑定后写入） |
| `createdAt` | TEXT/DATETIME | 创建时间 ISO8601 |
| `updatedAt` | TEXT/DATETIME | 更新时间 |
| `isDeleted` | INTEGER/BOOLEAN | 软删除，0/1 |
| `deletedAt` | TEXT/DATETIME NULL | 删除时间 |

**索引：** `userAccount`（唯一）、`cloudbaseUid`、`isDeleted`

> **命名约定：** 仅 `users` 表使用驼峰列名；业务表新增外键统一用 `userId`（驼峰）。既有 JSON payload 内部字段保持现有 snake_case，避免大范围序列化破坏。

### 4.2 `refresh_tokens` 表（可选，推荐）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `userId` | INTEGER | FK → users.id |
| `tokenHash` | VARCHAR(255) | 仅存 hash |
| `expiresAt` | TEXT | 过期时间 |
| `createdAt` | TEXT | 签发时间 |
| `revokedAt` | TEXT NULL | 撤销时间 |

用于 Web 端 Refresh Token 轮换与登出；5 人规模可放 SQLite/MySQL，无需 Redis。

### 4.3 业务表 `userId` 改造

以下表/逻辑增加 `userId`，并调整主键/唯一约束：

| 表 | 原主键/约束 | 新约束 |
|----|-------------|--------|
| `fund_profiles` | `fund_code` | `(userId, fund_code)` |
| `portfolio_state` | `id=1` | `userId` UNIQUE |
| `portfolio_daily_snapshots` | `snapshot_date` | `(userId, snapshot_date)` |
| `portfolio_intraday_curves` | `trade_date` | `(userId, tradeDate)` |
| `reports` | `id` | 加 `userId` 索引 |
| `report_chat_messages` | `id` | 经 `report_id` 间接隔离 |
| `investor_profile_state` | `id=1` | `userId` UNIQUE |
| `sector_mappings` | `sector_label` | `(userId, sector_label)` |
| `ocr_text_cache` | `cache_key` | `(userId, cache_key)` 或 key 含 userId 前缀 |

### 4.4 存量数据迁移

- 引入 `users` 后，将现有 SQLite 数据归属到 **迁移用户**（`userAccount=migration@local`，仅开发/导入用）或首个注册用户
- 提供 `scripts/migrate_single_tenant_to_user.py`：为所有表补 `userId=1`

---

## 5. 鉴权设计

### 5.1 Web — 邮箱密码 + JWT

**注册** `POST /api/auth/register`

```json
{ "userAccount": "a@b.com", "password": "...", "username": "昵称" }
```

**登录** `POST /api/auth/login`

```json
{ "userAccount": "a@b.com", "password": "..." }
```

**响应**

```json
{
  "accessToken": "<jwt>",
  "expiresIn": 7200,
  "user": {
    "id": 1,
    "userRole": "user",
    "username": "昵称",
    "userAccount": "a@b.com",
    "bio": "",
    "avatarUrl": ""
  }
}
```

- Access Token：JWT，2h，`sub=userId`
- Refresh Token：HttpOnly Cookie 或 body 返回（Phase 1 用 Cookie `fundpilot_refresh`）
- 密码：bcrypt，`passwordHash` 不入响应

**受保护路由：** 所有 `/api/portfolio/*`、`/api/fund-profiles/*`、`/api/analyze/*`、`/api/reports/*`、`/api/investor-profile`、`/api/ocr`（写操作）、`/api/database/*` 等需 `Authorization: Bearer <accessToken>`

**公开路由：** `/health`、`/api/auth/register`、`/api/auth/login`、`/api/auth/refresh`、`GET /api/trading-session`（可选公开）

**中间件：** `get_current_user()` 依赖注入，从 JWT 解析 `userId`；禁止客户端传 `userId` 越权。

### 5.2 微信小程序 — CloudBase 微信登录

**流程（Phase 2，架构预留）：**

```text
小程序 wx.login() → code
  → 云函数/后端换 openid（CloudBase 身份认证）
  → 查 users.cloudbaseUid 或自动注册
  → 后端签发与 Web 相同格式的 JWT（sub=userId）
  → 小程序 storage 存 accessToken，请求头带 Bearer
```

**绑定策略：**

- 新微信用户：创建 `users` 行（`userAccount` 占位 `wx_<openid>@wechat.local` 或仅填 `cloudbaseUid`）
- 已有邮箱用户：设置页「绑定微信」— Web 登录后调用 `POST /api/auth/bind-wechat`（带 CloudBase ticket）

**Custom Login（CloudBase）：** 后端用 CloudBase 私钥 `createTicket(customUserId)`，`customUserId` = `str(users.id)`，与官方文档对齐。

### 5.3 双端 API 契约统一

- Web 与小程序调用**同一套 REST API**（`NEXT_PUBLIC_API_BASE_URL` / 小程序 config）
- 响应 JSON 字段统一 **camelCase**（auth 相关）；现有 API 逐步兼容，auth 模块先行
- CORS：云托管配置 Web 域名；小程序走合法域名白名单

---

## 6. 前端设计（Web）

### 6.1 路由

| 路径 | 说明 | 鉴权 |
|------|------|------|
| `/login` | 邮箱登录 | 公开 |
| `/register` | 注册 | 公开 |
| `/` | Dashboard（持有/盈亏/日报） | 需登录 |
| `/settings` | 头像/简介/改密（Phase 1 可简化为 UserMenu 内） | 需登录 |

### 6.2 鉴权状态

- `AuthProvider`：启动时 `GET /api/auth/me` 或读 localStorage accessToken
- 401 → 清 token → 跳转 `/login?redirect=...`
- `api.ts`：`fetch` 封装自动带 `Authorization`
- `UserMenu`：展示真实 `username`/`avatarUrl`；增加「退出登录」

### 6.3 UI 要求

- 与现有 Plus Jakarta / 蓝色渐变风格一致
- 登录/注册表单：邮箱、密码、确认密码（注册）、昵称（注册可选）

---

## 7. 微信小程序（Phase 2 范围）

### 7.1 目录规划

```text
apps/miniprogram/          # 新建
├── app.js / app.json
├── pages/
│   ├── login/             # 微信一键登录
│   ├── holdings/          # 持有列表（调 GET /api/portfolio/holdings）
│   └── fund-detail/       # 简化版详情
└── utils/api.js           # 带 JWT 的 request 封装
```

### 7.2 MVP 功能

- 微信登录 → 获取 JWT
- 持有列表 + 刷新板块
- 基金详情（只读）
- 盈亏分析、AI 日报生成放 Phase 3（Web 先行完整功能）

### 7.3 CloudBase 配置

- 小程序 AppID 绑定 CloudBase 环境
- 身份认证：开启微信小程序登录
- request 合法域名：云托管 API 域名

---

## 8. 后端模块划分

```text
apps/api/app/
├── auth/
│   ├── dependencies.py    # get_current_user, optional_user
│   ├── jwt.py               # encode/decode
│   ├── passwords.py         # bcrypt
│   ├── models.py            # UserCreate, UserPublic, TokenResponse
│   └── service.py           # register, login, refresh
├── database.py              # users CRUD + 各表 userId 过滤
└── main.py                  # /api/auth/* 路由 + 全局依赖
```

**测试：**

- `test_auth_register_login.py`：注册、重复邮箱、错误密码
- `test_auth_isolation.py`：用户 A 无法读用户 B 的 holdings/reports
- 改造现有 `conftest.py`：fixture `auth_headers(user_id)` 供全量 API 测试

---

## 9. 分阶段交付

### Phase 1 — Web 用户体系（本次实现重点）

- [ ] `users` / `refresh_tokens` 表（驼峰列名）
- [ ] 业务表 `userId` + 迁移脚本
- [ ] `/api/auth/*` + JWT 中间件
- [ ] 全 API 按 `userId` 隔离
- [ ] Web `/login` `/register` + AuthProvider
- [ ] 更新 pytest + E2E 冒烟
- [ ] 更新 `PROJECT_CONTEXT.md`

### Phase 2 — 小程序 + CloudBase 身份

- [x] `apps/miniprogram` 脚手架
- [x] CloudBase 微信登录 + Custom Ticket 绑定 `cloudbaseUid`
- [x] `POST /api/auth/wechat-login`、`POST /api/auth/bind-wechat`
- [ ] 合法域名与真机联调（需你的 CloudBase 环境）

### Phase 3 — 上云

- [x] Dockerfile（`apps/api/Dockerfile`）+ `docker-compose.cloud.yml`
- [x] SQLite → MySQL 迁移脚本（`scripts/migrate_sqlite_to_mysql.py`）
- [x] 云托管部署文档（`docs/deploy/cloudbase.md`）

---

## 10. 安全要点

- `passwordHash` 仅服务端存储；日志脱敏
- JWT `secret` 来自环境变量 `FUND_AI_JWT_SECRET`（≥32 字节随机）
- 注册可限流（Phase 1 简单 IP 计数即可，5 人场景可选）
- 软删除用户：`isDeleted=1` 后拒绝登录
- 数据库备份/导入 API 仅 `admin` 或关闭公网

---

## 11. 环境变量（新增）

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_JWT_SECRET` | — | JWT 签名密钥（必填） |
| `FUND_AI_JWT_ACCESS_EXPIRE_MINUTES` | 120 | Access Token 有效期 |
| `FUND_AI_JWT_REFRESH_EXPIRE_DAYS` | 30 | Refresh Token 有效期 |
| `FUND_AI_CLOUDBASE_ENV_ID` | — | Phase 2 小程序 |
| `FUND_AI_CLOUDBASE_CUSTOM_LOGIN_KEY` | — | Phase 2 Custom Ticket 私钥路径 |

---

## 12. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 全 API 加 userId 改动面大 | 先改 `database.py` 统一入口 + 测试覆盖 |
| SQLite 驼峰列名与 Python 习惯冲突 | 仅 users 表驼峰；ORM/原生 SQL 显式列名 |
| OCR 上云体积大 | 云托管 Docker；OCR 可选关闭 `FUND_AI_OCR_PRELOAD` |
| 小程序与 Web 功能差距 | Phase 2 MVP 只读持有；复杂功能保留 Web |

---

## 13. 验收标准

**Phase 1**

1. 未登录访问 `/` 跳转登录页
2. 邮箱注册后可登录，看到空持仓
3. 用户 A 上传 OCR 后，用户 B 看不到 A 的基金
4. 退出登录后 API 返回 401
5. `pytest` 全绿；`npm run build` + E2E 冒烟通过

**Phase 2**

1. 小程序微信登录后可拉取持有列表
2. 同一 CloudBase 账号与 Web 绑定后数据一致

---

## 审阅确认

请确认本规格无误后回复，将据此编写实现计划 `docs/superpowers/plans/2026-06-11-user-auth.md` 并开始 Phase 1 开发。
