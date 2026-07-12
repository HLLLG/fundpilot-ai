import { defineConfig, devices } from "@playwright/test";

const UI_BASE_URL = process.env.PLAYWRIGHT_UI_BASE_URL ?? "http://127.0.0.1:3001";
const UI_PORT = new URL(UI_BASE_URL).port || "3001";

export default defineConfig({
  testDir: "./e2e-ui",
  outputDir: "./test-results/ui",
  timeout: 45_000,
  expect: {
    timeout: 8_000,
  },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: UI_BASE_URL,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-1440",
      use: {
        ...devices["Desktop Chrome"],
        browserName: "chromium",
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "desktop-1280",
      use: {
        ...devices["Desktop Chrome"],
        browserName: "chromium",
        viewport: { width: 1280, height: 800 },
      },
    },
    {
      name: "desktop-1024",
      use: {
        ...devices["Desktop Chrome"],
        browserName: "chromium",
        viewport: { width: 1024, height: 900 },
      },
    },
    {
      name: "tablet-768",
      use: {
        browserName: "chromium",
        viewport: { width: 768, height: 1024 },
        deviceScaleFactor: 1,
        hasTouch: true,
      },
    },
    {
      name: "mobile-430",
      use: {
        browserName: "chromium",
        viewport: { width: 430, height: 932 },
        deviceScaleFactor: 1,
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "mobile-390",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "mobile-320",
      use: {
        browserName: "chromium",
        viewport: { width: 320, height: 844 },
        deviceScaleFactor: 1,
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: {
    command: "npm run build && node scripts/serve-static.mjs",
    url: UI_BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      ...process.env,
      NEXT_TELEMETRY_DISABLED: "1",
      PORT: UI_PORT,
      // Authenticated-shell tests intercept this same-origin API namespace.
      NEXT_PUBLIC_API_BASE_URL: UI_BASE_URL,
    },
  },
});
