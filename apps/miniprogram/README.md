# FundPilot 微信小程序

## 功能（Phase 2 MVP）

- 微信 / CloudBase 登录 → 获取与 Web 相同的 JWT
- 持有列表、刷新板块涨跌
- 基金详情（只读）

## 本地联调

1. 启动后端 `bash scripts/dev.sh`
2. 微信开发者工具打开 `apps/miniprogram`
3. `utils/config.js` 中设置 `API_BASE`（开发可勾选「不校验合法域名」）
4. 后端 `.env` 开启开发模式：`FUND_AI_CLOUDBASE_AUTH_DEV_MODE=true`
5. 登录页点击「微信一键登录」

## CloudBase 上线

1. CloudBase 控制台创建环境，开启「微信小程序登录」
2. 将 `CLOUDBASE_ENV_ID` 填入 `utils/config.js` 与后端 `FUND_AI_CLOUDBASE_ENV_ID`
3. API 部署到云托管后，把 HTTPS 域名填入 `API_BASE` 与小程序 request 合法域名
4. 生产环境关闭 `FUND_AI_CLOUDBASE_AUTH_DEV_MODE`，配置自定义登录私钥路径

## 与 Web 账号绑定

1. Web 邮箱登录 → 用户菜单 → **账号设置**（`/settings`）
2. 填写 CloudBase 用户 ID（开发联调时可在登录响应或控制台查看）并绑定
3. 小程序用同一 CloudBase 微信账号登录 → 持仓与 Web 一致

也可直接调用 `POST /api/auth/bind-wechat`（需 JWT，body 含 `cloudbaseUid` 或 `cloudbaseAccessToken`）。
