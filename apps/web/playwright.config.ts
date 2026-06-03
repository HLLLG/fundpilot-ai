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
      FUND_AI_DEEPSEEK_API_KEY: "",
    },
  },
});
