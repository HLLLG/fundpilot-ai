import { defineConfig } from "@playwright/test";
import path from "node:path";

const apiDir = path.join(__dirname, "..", "api");
const apiServerCmd =
  process.platform === "win32"
    // The Windows venv python redirector starts a second base-Python process.
    // Playwright can terminate the redirector while leaving that child alive,
    // which makes an otherwise-passed suite hang during webServer teardown.
    ? path.join(apiDir, ".venv", "Scripts", "uvicorn.exe")
    : "python -m uvicorn";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:8000",
  },
  webServer: {
    command: `${apiServerCmd} app.main:app --host 127.0.0.1 --port 8000`,
    cwd: apiDir,
    url: "http://127.0.0.1:8000/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      ...process.env,
      // 强制 SQLite，避免本机 .env 里的 CloudBase MySQL 被 E2E 写入
      // pydantic-settings ignores an empty override and would reload the
      // developer's MySQL URL from .env.  A non-MySQL sentinel makes the
      // application's backend choice deterministically SQLite.
      FUND_AI_DATABASE_URL: "sqlite://playwright-local",
      FUND_AI_DB_PATH: process.env.FUND_AI_DB_PATH ?? path.join(__dirname, "..", "..", "data", "playwright-e2e.db"),
      // 占位值会被 config 视为未配置，避免本机 .env 里的真 Key 被读入
      FUND_AI_DEEPSEEK_API_KEY: "your-deepseek-key",
      // E2E 冒烟不测外部数据；避免 AkShare/东财/财联社拖过 60s 超时
      FUND_AI_NEWS_ENABLED: "false",
      FUND_AI_NEWS_SUMMARIZE: "false",
      FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED: "false",
      FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED: "false",
      FUND_AI_FUND_NAME_PRELOAD_ENABLED: "false",
      FUND_AI_OCR_PRELOAD: "false",
      FUND_AI_SECTOR_QUOTES_ENABLED: "false",
      FUND_AI_THEME_BOARD_REFRESH_ENABLED: "false",
      FUND_AI_MARKET_BREADTH_ENABLED: "false",
      FUND_AI_HOLDING_INTRADAY_WARMUP_ENABLED: "false",
      FUND_AI_FUND_PRIMARY_SECTOR_GLOBAL_ENABLED: "false",
      FUND_AI_FUND_PRIMARY_SECTOR_PRECOMPUTE_ENABLED: "false",
      FUND_AI_FUND_PRIMARY_SECTOR_BACKFILL_ENABLED: "false",
      FUND_AI_FUND_PRIMARY_SECTOR_LLM_INFER_ENABLED: "false",
      FUND_AI_AKSHARE_NAV_BATCH_ENABLED: "false",
      FUND_AI_FLOW_DIVERGENCE_BACKTEST_ENABLED: "false",
      FUND_AI_NAV_TREND_DAYS: "20",
    },
  },
});
