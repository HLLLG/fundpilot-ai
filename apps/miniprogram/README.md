# 好基灵微信小程序

## 功能（Phase 2 MVP）

- 微信登录（`wx.cloud.callContainer` 内网调用云托管）→ 获取与 Web 相同的 JWT
- 持有列表、刷新板块涨跌
- 基金详情（只读）

## 配置（`utils/config.js`）

| 变量 | 说明 |
|------|------|
| `CLOUDBASE_ENV_ID` | CloudBase 环境 ID |
| `CLOUD_SERVICE_NAME` | 云托管服务名（如 `fundpilot-api`） |
| `API_BASE` | 公网 API 地址；**仅本地 HTTP 回退**，线上优先 `callContainer` |

小程序内 API 请求默认走 **`wx.cloud.callContainer`**，**无需**在微信后台配置 `*.tcloudbase.com` 为 request 合法域名（该默认域名仅测试用，无法通过合法域名校验）。

## 上线前检查

1. CloudBase 控制台 **扫码授权** 绑定小程序 AppID
2. 小程序后台 **开通云开发**（与 CloudBase 环境关联）
3. 云托管环境变量设置 `FUND_AI_CLOUDBASE_ENV_ID`（与 `config.js` 一致）
4. `project.config.json` 中 `appid` 为真实小程序 AppID
5. 微信开发者工具 **编译** → **上传** → 公众平台设为 **体验版**

## 本地联调

1. 启动后端 `bash scripts/dev.sh`
2. 微信开发者工具打开 `apps/miniprogram`
3. `config.js` 可改 `API_BASE=http://127.0.0.1:8000` 并清空 `CLOUDBASE_ENV_ID`（走 HTTP）
4. 详情 → 本地设置 → 勾选「不校验合法域名」
5. 后端 `.env` 设 `FUND_AI_CLOUDBASE_AUTH_DEV_MODE=true`（HTTP 模式无 openid 头时走开发 UID）
6. 登录页点击「微信一键登录」

## 与 Web 账号绑定

**推荐（小程序内一步打通）：**

1. 微信一键登录后，若「我的持有」显示空，点击 **「关联已有邮箱账号」**
2. 输入你在 Web 端注册的邮箱与密码 → 关联成功
3. 此后每次微信登录都会命中该邮箱账号，看到与 Web 一致的持仓

原理：小程序 `callContainer` 登录由微信网关注入 openid，后端 `POST /api/auth/link-email`（需微信登录 JWT）校验邮箱密码后，把本次 openid 迁移到邮箱账号并软删微信占位账号。相比「Web 端填 CloudBase UID」更可靠（Web 拿到的是 CloudBase uid，与小程序 openid 命名空间不同，无法直接互通）。

**备用（Web 侧绑定 / 自定义登录场景）：**

调用 `POST /api/auth/bind-wechat`（需邮箱登录 JWT，body 含 `cloudbaseUid` 或 `cloudbaseAccessToken`）。仅当小程序登录与该标识同源时才生效。
