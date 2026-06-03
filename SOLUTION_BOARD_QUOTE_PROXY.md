# 板块行情刷新修复总结

## 结论

PC 端无法稳定拉到真实关联板块，核心不是基金持仓映射本身，而是当前网络环境下东财 push2 行情链路容易被直连阻断。养基宝 App 很可能走的是移动端/WebView/服务端转发或更完整的浏览器态链路，所以同一公司 Wi-Fi 下手机能刷新，PC 后端的普通 HTTP 请求不一定能刷新。

本次修复后的策略是：真实板块优先，失败时明确使用天天基金基金估值兜底，不再把兜底误展示为真实板块行情。

## 当前刷新链路

1. `eastmoney_spot_client.py`：优先直连东财 push2，短预算下每表只试首个 host，避免首页卡顿。
2. `sector_quote_relay_provider.py`：可选服务端中继，配置 `FUND_AI_SECTOR_QUOTES_RELAY_URL` 后接入能访问东财的代理/服务端。
3. `sector_quote_browser_provider.py`：可选浏览器命令链路，配置 `FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true` 和 `FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND` 后可接入本机浏览器态脚本。
4. `akshare_spot_client.py` / `sector_on_demand.py`：无前端短预算时作为慢兜底补全真实板块，避免影响首页响应。
5. `fund_estimate_provider.py`：真实板块不足或单只基金未匹配时，用天天基金估值补位，并在前端标记为“估值兜底”。

## 用户体验变化

- 首页标题改为“真实板块优先，失败时估值兜底”。
- 全局提示会明确显示“当前使用天天基金估值兜底”。
- 每只基金行内会显示“估值兜底”，不会再误标为“实时板块”。
- 刷新失败文案只在真实板块、缓存、估值都不可用时出现。
- 天天基金估值改为并发请求，本机 4 只基金刷新从约 14.6 秒降到约 2.2 秒。

## 本机验证结果

当前 PC 网络下，真实关联板块仍未拉通，接口返回：

```text
holding_count=4
matched=4
board_matched=0
estimate_fallback=4
unresolved=0
provider_path=fund_estimate_live
elapsed_ms≈2201
```

这说明首页可稳定刷新并估算收益，但当前显示的是天天基金估值兜底，不是真实关联板块行情。

## 后续增强方向

要进一步逼近养基宝效果，优先接入 `FUND_AI_SECTOR_QUOTES_RELAY_URL`。只要有一台服务端或代理能稳定访问东财 push2，并返回 `{ index, concept, industry }` 或 `{ boards: { index, concept, industry } }`，PC 端就能通过中继拿到真实板块涨跌。

浏览器命令链路已预留，示例脚本为：

```text
apps/web/scripts/sector-quote-browser-command.mjs
```

该链路适合继续尝试本机浏览器态、登录态或 Playwright 抓取方案。

## 已验证命令

```text
apps/api/.venv/Scripts/python.exe -m pytest tests/test_fund_estimate_provider.py tests/test_sector_quote_service.py tests/test_sector_quote_provider.py tests/test_sector_quote_api.py -q
npm run lint
npm run typecheck
npm run build
```
