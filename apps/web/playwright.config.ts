import { defineConfig } from "@playwright/test";
import path from "node:path";

const apiDir = path.join(__dirname, "..", "api");
const pythonCmd =
  process.platform === "win32"
    ? path.join(apiDir, ".venv", "Scripts", "python.exe")
    : "python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:8000",
  },
  webServer: {
    command: `${pythonCmd} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
    cwd: apiDir,
    url: "http://127.0.0.1:8000/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      ...process.env,
      FUND_AI_DB_PATH: process.env.FUND_AI_DB_PATH ?? path.join(__dirname, "..", "..", "data", "playwright-e2e.db"),
      // 占位值会被 config 视为未配置，避免本机 .env 里的真 Key 被读入
      FUND_AI_DEEPSEEK_API_KEY: "your-deepseek-key",
      // E2E 冒烟不测外部数据；避免 AkShare/东财/财联社拖过 60s 超时
      FUND_AI_NEWS_ENABLED: "false",
      FUND_AI_NEWS_SUMMARIZE: "false",
      FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED: "false",
      FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED: "false",
      FUND_AI_OCR_PRELOAD: "false",
      FUND_AI_NAV_TREND_DAYS: "20",
    },
  },
});
