# 安全说明

## DeepSeek API Key

- **只放在** 项目根目录 `.env` 的 `FUND_AI_DEEPSEEK_API_KEY` 中；`.env` 已在 `.gitignore` 内，不要提交。
- 若 Key 曾出现在聊天、截图或误提交仓库，请到 [DeepSeek 控制台](https://platform.deepseek.com/) **作废并重新创建**，再更新本地 `.env`。
- 仓库测试代码使用 `fundpilot-pytest-only-...` 等**非真实**占位字符串，避免触发 GitHub Secret Scanning。

## 收到 GitHub「Secrets detected」邮件时

1. 在 DeepSeek 控制台**轮换 Key**（最稳妥）。
2. 更新本地 `.env`，重启 API。
3. 在 GitHub 仓库 **Security → Secret scanning alerts** 中将对应告警标为已解决（若仅为测试占位符误报，轮换后仍可关闭告警）。
