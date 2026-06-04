# 板块行情刷新修复总结

## 结论

PC 端无法稳定拉到真实关联板块，核心不是基金持仓映射本身，而是当前网络环境下东财 push2 行情链路容易被直连阻断。养基宝 App 很可能走的是移动端/WebView/服务端转发或更完整的浏览器态链路，所以同一公司 Wi-Fi 下手机能刷新，PC 后端的普通 HTTP 请求不一定能刷新。

本次修复后的策略是：真实板块优先，失败时明确使用天天基金基金估值兜底，不再把兜底误展示为真实板块行情。

## 诊断

运行诊断脚本，查看各链路是否可用：

```bash
bash scripts/diagnose-sector-quotes.sh
# 或
apps/api/.venv/Scripts/python.exe apps/api/scripts/diagnose_sector_quotes.py --pretty
```

API：`GET /api/sector-quotes/diagnostic?timeout_seconds=8`

根据 `recommendation` 字段选择下一步：

| 结果 | 建议 |
|------|------|
| `eastmoney_batch` OK | 无需中继，手动刷新 8s 预算即可 |
| `browser` OK | 启用本机 Playwright（见下） |
| `deploy_relay` | 在 VPS/NAS 部署 `apps/sector-relay` |

## 当前刷新链路

1. `eastmoney_spot_client.py`：优先直连东财 push2，快刷预算 8s。
2. `sector_quote_relay_provider.py`：可选服务端中继，配置 `FUND_AI_SECTOR_QUOTES_RELAY_URL`。
3. `sector_quote_browser_provider.py`：可选浏览器命令链路（Playwright）。
4. `sector_canonical.py`：并发 secid 直查（商业航天、半导体等）。
5. `akshare_spot_client.py` / `sector_on_demand.py`：`budget=accurate` 自动刷新时启用。
6. `fund_estimate_provider.py`：真实板块全失败时才兜底。

## 部署板块中继（推荐）

在能访问东财的机器上：

```bash
cd apps/sector-relay
docker compose up -d --build
```

本机 `.env`：

```env
FUND_AI_SECTOR_QUOTES_RELAY_URL=http://<relay-host>:8787/boards
FUND_AI_SECTOR_QUOTES_RELAY_TIMEOUT_SECONDS=3
# 若中继设置了 RELAY_TOKEN：
FUND_AI_SECTOR_QUOTES_RELAY_TOKEN=your-token
```

中继返回 `{ index, concept, industry }` 或 `{ boards: { ... } }` 均可。

## 本机浏览器链路（备选）

```bash
cd apps/web
npm install
npx playwright install chromium
```

`.env`：

```env
FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true
FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND=node scripts/sector-quote-browser-command.mjs
FUND_AI_SECTOR_QUOTES_BROWSER_TIMEOUT_SECONDS=20
```

## 刷新预算

| 场景 | `budget` | 超时 |
|------|----------|------|
| 手动点刷新 | `fast` | 8s |
| 120s 自动刷新 | `accurate` | 无上限，可走 AkShare |

## 已验证命令

```text
apps/api/.venv/Scripts/python.exe -m pytest tests/test_sector_quote_diagnostic.py tests/test_fund_estimate_provider.py tests/test_sector_quote_service.py tests/test_sector_quote_provider.py tests/test_sector_quote_api.py -q
npm run lint
npm run typecheck
npm run build
```
