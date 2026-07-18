# 安全说明

## DeepSeek API Key

- **只放在** 项目根目录 `.env` 的 `FUND_AI_DEEPSEEK_API_KEY` 中；`.env` 已在 `.gitignore` 内，不要提交。
- 若 Key 曾出现在聊天、截图或误提交仓库，请到 [DeepSeek 控制台](https://platform.deepseek.com/) **作废并重新创建**，再更新本地 `.env`。
- 仓库测试代码使用 `fundpilot-pytest-only-...` 等**非真实**占位字符串，避免触发 GitHub Secret Scanning。

## 收到 GitHub「Secrets detected」邮件时

1. 在 DeepSeek 控制台**轮换 Key**（最稳妥）。
2. 更新本地 `.env`，重启 API。
3. 在 GitHub 仓库 **Security → Secret scanning alerts** 中将对应告警标为已解决（若仅为测试占位符误报，轮换后仍可关闭告警）。

## 管理员、用户隐私与密码重置

- 管理员权限以数据库当前 `userRole` 为准，不信任浏览器字段或 JWT 中的角色；每个受保护请求还校验账户启用状态和 `authVersion`。
- 初始管理员只能显式提升一个已注册、启用中的精确账户。禁止按邮箱自动提权，避免未验证邮箱注册导致权限接管。
- 用户列表默认隐藏完整邮箱，只有管理员详情接口返回完整账户信息；管理员接口统一 `Cache-Control: no-store`。
- 邮箱搜索使用 POST body，不把邮箱写入 access-log URL。管理员页面不读取持仓金额、报告内容或交易金额，也不会把用户信息发送到模型、分析埋点或导出文件。
- 管理员不能查看或直接设置用户密码。重置流程仅返回一次 30 分钟有效的随机链接；数据库只保存 token 的 SHA-256，链接在浏览器 URL fragment 中传递并在读取后立即移除。成功重置会撤销全部旧会话。
- 用户资料、角色、启停、会话撤销和密码重置均写入 `admin_audit_events`；审计内容只含允许字段，不含密码哈希、原始 token 或资金数据。SQLite/MySQL 都用触发器禁止审计记录更新和删除。
- 禁止管理员停用或降级自己，也禁止停用或降级最后一名启用中的管理员；并发管理操作使用 `updatedAt` 冲突检测，过期编辑返回 HTTP 409。
