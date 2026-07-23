import type { Page, Route } from "@playwright/test";
import { expect, test } from "./ui-test";
import { expectNoHorizontalOverflow } from "./ui-assertions";

const TRADING_SESSION = {
  timezone: "Asia/Shanghai",
  local_datetime: "2026-07-12T10:00:00+08:00",
  calendar_date: "2026-07-12",
  effective_trade_date: "2026-07-10",
  is_trading_day: false,
  session_kind: "non_trading_day",
  market_open_time: "09:30",
  decision_window: "closed",
  market_close_time: "15:00",
};

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "authorization, content-type",
      "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
    },
    body: JSON.stringify(body),
  });
}

function discoveryReports(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: `discovery-${index + 1}`,
    created_at: new Date(Date.UTC(2026, 6, 12, 2, 0) - index * 86_400_000).toISOString(),
    title: `历史推荐 ${String(index + 1).padStart(3, "0")}`,
    summary: `第 ${index + 1} 份推荐摘要`,
    focus_sectors: ["人工智能"],
    target_sectors: [index % 2 ? "半导体" : "人工智能"],
    recommendations: [],
    caveats: [],
    provider: "ui-fixture",
  }));
}

function dailyReports(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: `report-${index + 1}`,
    created_at: new Date(Date.UTC(2026, 6, 12, 2, 0) - index * 86_400_000).toISOString(),
    title: index === 0 ? "今日组合观察" : `历史日报 ${String(index + 1).padStart(2, "0")}`,
    risk: {
      level: index % 3 === 0 ? "high" : "medium",
      suggested_action: index % 3 === 0 ? "risk_review" : "watch",
      weighted_return_percent: Number((1.2 - index * 0.1).toFixed(2)),
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    fund_recommendations: [],
    summary: `第 ${index + 1} 份日报摘要，当前内容应在同一阅读区连续切换。`,
    recommendations: [],
    caveats: [],
    provider: "ui-fixture",
  }));
}

async function installHistoryStubs(
  page: Page,
  options: { discoveryCount?: number; reportCount?: number; failReportRefresh?: boolean } = {},
) {
  const discoveries = discoveryReports(options.discoveryCount ?? 100);
  let reports = dailyReports(options.reportCount ?? 8);
  let reportListRequests = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*" } });
      return;
    }
    if (request.method() === "POST" && pathname === "/api/telemetry/web-vitals") {
      await json(route, 202, { accepted: true });
      return;
    }
    if (pathname === "/api/auth/me") {
      await json(route, 200, { id: 9002, username: "历史验收用户", userAccount: "history@example.com" });
      return;
    }
    if (pathname === "/api/portfolio/refresh-and-hydrate") {
      await json(route, 200, {
        portfolio: {
          holdings: [],
          source: "empty",
          refreshed_at: null,
          portfolio_summary: null,
        },
        investor_profile: {},
        analysis_prompt: {
          role_prompt: "",
          is_custom: false,
          default_role_prompt: "",
        },
        sector_quotes_status: {
          enabled: false,
          ttl_seconds: 60,
          auto_interval_seconds: 180,
          idle_interval_seconds: 10_800,
          auto_refresh_allowed: false,
          session: TRADING_SESSION,
        },
      });
      return;
    }
    if (pathname === "/api/portfolio/holdings") {
      await json(route, 200, { holdings: [], source: "empty", refreshed_at: null, portfolio_summary: null });
      return;
    }
    if (pathname === "/api/investor-profile") {
      await json(route, 200, {
        style: "稳健",
        horizon: "半年到一年",
        max_drawdown_percent: 8,
        concentration_limit_percent: 35,
        expected_investment_amount: 30000,
        prefer_dca: true,
        avoid_chasing: true,
        decision_style: "conservative",
        investment_preset: "conservative_hold",
        round_trip_fee_percent: 1.5,
        min_net_profit_percent: 1,
        swing_alerts_enabled: false,
        swing_monitor_scope: "both",
      });
      return;
    }
    if (pathname === "/api/analysis-prompt") {
      await json(route, 200, { role_prompt: "", is_custom: false, default_role_prompt: "" });
      return;
    }
    if (pathname === "/api/discovery-prompt") {
      await json(route, 200, { role_prompt: "", is_custom: false, default_role_prompt: "" });
      return;
    }
    if (pathname === "/api/sector-quotes/status") {
      await json(route, 200, { enabled: false, ttl_seconds: 60, auto_interval_seconds: 180, idle_interval_seconds: 10_800, auto_refresh_allowed: false, session: TRADING_SESSION });
      return;
    }
    if (pathname === "/api/trading-session") {
      await json(route, 200, TRADING_SESSION);
      return;
    }
    if (pathname === "/api/fund-discovery/sectors") {
      await json(route, 200, []);
      return;
    }
    if (pathname === "/api/fund-discovery/reports") {
      await json(route, 200, discoveries);
      return;
    }
    if (
      request.method() === "GET" &&
      /^\/api\/fund-discovery\/reports\/[^/]+$/.test(pathname)
    ) {
      const id = decodeURIComponent(pathname.split("/").at(-1) ?? "");
      const detail = discoveries.find((item) => item.id === id);
      await json(route, detail ? 200 : 404, detail ?? { detail: "report not found" });
      return;
    }
    if (pathname.includes("/api/fund-discovery/reports/") && pathname.endsWith("/outcomes")) {
      await json(route, 200, { has_data: false, message: "暂无可配对复盘", items: [] });
      return;
    }
    if (pathname === "/api/reports") {
      reportListRequests += 1;
      if (options.failReportRefresh && reportListRequests > 1) {
        // 200 + malformed payload simulates a client-side parse failure without making the
        // browser emit an expected network console.error that would mask the UI recovery check.
        await route.fulfill({ status: 200, contentType: "application/json", body: "not-json" });
      } else {
        await json(route, 200, reports);
      }
      return;
    }
    if (request.method() === "GET" && /^\/api\/reports\/[^/]+$/.test(pathname)) {
      const id = decodeURIComponent(pathname.split("/").at(-1) ?? "");
      const detail = reports.find((item) => item.id === id);
      await json(route, detail ? 200 : 404, detail ?? { detail: "report not found" });
      return;
    }
    if (request.method() === "DELETE" && pathname.startsWith("/api/reports/")) {
      const id = pathname.split("/").at(-1);
      reports = reports.filter((item) => item.id !== id);
      await json(route, 200, { ok: true });
      return;
    }
    await json(route, 200, {});
  });
}

async function enterDashboard(page: Page, url = "/") {
  await page.clock.setFixedTime(new Date(TRADING_SESSION.local_datetime));
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("fundpilot_access_token", "history-ui-token");
  });
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1, name: /账户持仓|投研日报/ })).toBeVisible();
}

async function openPrimary(page: Page, destination: "discovery" | "report") {
  if ((page.viewportSize()?.width ?? 1440) >= 1024) {
    await page.getByRole("button", { name: destination === "discovery" ? "发现" : "日报", exact: true }).click();
    return;
  }
  await page.getByRole("button", { name: /更多导航/ }).click();
  await page.getByRole("menuitem", { name: destination === "discovery" ? "发现基金" : "生成日报" }).click();
}

test("100 条发现历史保持有界，并在统一历史抽屉内连续切换", async ({ page }, testInfo) => {
  await installHistoryStubs(page, { discoveryCount: 100 });
  await enterDashboard(page);
  await openPrimary(page, "discovery");
  await expect(page.getByRole("heading", { level: 1, name: "发现基金" })).toBeVisible();

  const trigger = page.getByRole("button", { name: /历史推荐/ });
  await expect(trigger).toBeVisible();
  await trigger.focus();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "历史推荐" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("discovery-history-item")).toHaveCount(20);
  await dialog.evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
  });
  const metrics = await dialog.getByTestId("history-drawer-scroll-region").evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      bottom: rect.bottom,
      viewport: window.innerHeight,
      overflowY: getComputedStyle(element).overflowY,
    };
  });
  expect(metrics.bottom).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.overflowY).toBe("auto");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(dialog).toBeVisible();
  await page.waitForTimeout(260);
  await page.screenshot({ path: testInfo.outputPath("discovery-history-after.png") });
  const search = dialog.getByRole("searchbox", { name: "搜索历史推荐" });
  await search.fill("080");
  await dialog.locator('button[aria-pressed]').filter({ hasText: "历史推荐 080" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { name: "历史推荐 080" })).toBeVisible();
  await expect(page.getByLabel("推荐报告阅读区")).toBeFocused();
  await expect(page.getByTestId("discovery-config-summary")).toContainText("当前运行条件");
  await expectNoHorizontalOverflow(page);
});

test("日报导航器支持前后切换、回到今日、URL 恢复和失败保留", async ({ page }, testInfo) => {
  await installHistoryStubs(page, { reportCount: 8, failReportRefresh: true });
  await enterDashboard(page, "/?report=report-3");

  await expect(page.getByRole("heading", { level: 1, name: "投研日报" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "历史日报 03" })).toBeVisible();
  await page.getByRole("button", { name: "上一份日报" }).click();
  await expect(page.getByRole("heading", { name: "历史日报 04" })).toBeVisible();
  await expect(page).toHaveURL(/report=report-4/);
  await page.getByRole("button", { name: "下一份日报" }).click();
  await expect(page.getByRole("heading", { name: "历史日报 03" })).toBeVisible();

  await page.getByRole("button", { name: "回到今日" }).click();
  await expect(page.getByRole("heading", { name: "今日组合观察" })).toBeVisible();
  await expect(page).not.toHaveURL(/report=/);

  await page.getByRole("button", { name: /全部历史/ }).click();
  const dialog = page.getByRole("dialog", { name: "全部历史日报" });
  await expect(dialog).toBeVisible();
  await page.waitForTimeout(260);
  await page.screenshot({ path: testInfo.outputPath("report-history-after.png") });
  await expect(
    dialog.locator('button[aria-pressed]').filter({ hasText: "今日组合观察" }),
  ).toHaveAttribute("aria-current", "true");
  await dialog.getByRole("button", { name: "刷新历史日报" }).click();
  await expect(dialog.getByRole("alert")).toContainText("当前日报仍保留");
  await expect(page.getByLabel("日报阅读区")).toContainText("今日组合观察");

  await dialog.getByRole("searchbox", { name: "搜索历史日报" }).fill("05");
  await dialog.locator('button[aria-pressed]').filter({ hasText: "历史日报 05" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { name: "历史日报 05" })).toBeVisible();
  await expect(page.getByLabel("日报阅读区")).toBeFocused();

  await page.getByRole("button", { name: /全部历史/ }).click();
  const deleteDialog = page.getByRole("dialog", { name: "全部历史日报" });
  await deleteDialog.getByRole("button", { name: "删除日报 历史日报 05" }).click();
  const confirmation = page.getByRole("alertdialog", { name: "删除这份日报？" });
  await confirmation.getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByLabel("日报阅读区")).toContainText("历史日报 06");
  await deleteDialog.getByRole("button", { name: "返回并关闭全部历史日报" }).click();
  await expect(page.getByRole("heading", { name: "历史日报 06" })).toBeVisible();

  await page.goBack();
  await expect(page.getByRole("heading", { name: "今日组合观察" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
