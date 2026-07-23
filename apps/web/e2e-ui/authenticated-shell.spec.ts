import type { Page, Route } from "@playwright/test";
import { expect, test } from "./ui-test";
import {
  expectMinimumTapTarget,
  expectNoHorizontalOverflow,
} from "./ui-assertions";

const TRADING_SESSION = {
  timezone: "Asia/Shanghai",
  local_datetime: "2026-07-11T10:00:00+08:00",
  calendar_date: "2026-07-11",
  effective_trade_date: "2026-07-10",
  is_trading_day: false,
  session_kind: "non_trading_day",
  market_open_time: "09:30",
  decision_window: "closed",
  market_close_time: "15:00",
};

async function fulfillJson(route: Route, status: number, body: unknown): Promise<void> {
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

type ApiStubAudit = {
  seen: Set<string>;
  unexpected: string[];
};

const OCR_HOLDING = {
  fund_code: "110022",
  fund_name: "易方达消费行业股票",
  holding_amount: 28_640.5,
  holding_profit: 2_224.6,
  holding_return_percent: 8.42,
  daily_profit: 426.74,
  daily_return_percent: 1.49,
  sector_name: "食品饮料",
  sector_return_percent: 1.95,
};

async function installStableApiStubs(
  page: Page,
  options: { enableCoreFlow?: boolean } = {},
): Promise<ApiStubAudit> {
  const audit: ApiStubAudit = {
    seen: new Set<string>(),
    unexpected: [],
  };
  let currentHoldings: Array<Record<string, unknown>> = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    audit.seen.add(`${request.method()} ${pathname}`);

    if (request.method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "authorization, content-type",
          "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
        },
      });
      return;
    }

    if (request.method() === "POST" && pathname === "/api/telemetry/web-vitals") {
      await fulfillJson(route, 202, { accepted: true });
      return;
    }
    if (pathname === "/api/auth/me") {
      await fulfillJson(route, 200, {
        id: 9001,
        userRole: "user",
        username: "验收用户",
        userAccount: "ui-check@example.com",
        bio: "",
        avatarUrl: "",
      });
      return;
    }
    if (pathname === "/api/portfolio/refresh-and-hydrate") {
      await fulfillJson(route, 200, {
        portfolio: {
          holdings: currentHoldings,
          source: currentHoldings.length > 0 ? "database" : "empty",
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
      await fulfillJson(route, 200, {
        holdings: currentHoldings,
        source: currentHoldings.length > 0 ? "database" : "empty",
        refreshed_at: null,
        portfolio_summary: null,
      });
      return;
    }
    if (options.enableCoreFlow && pathname === "/api/ocr") {
      await fulfillJson(route, 200, {
        raw_text: "UI acceptance OCR fixture",
        preview: true,
        ocr_source: "local_fixture",
        holdings: [OCR_HOLDING],
        fund_code_resolutions: [],
        holding_warnings: [],
      });
      return;
    }
    if (options.enableCoreFlow && pathname === "/api/portfolio/apply-holdings") {
      const payload = request.postDataJSON() as { holdings?: Array<Record<string, unknown>> };
      currentHoldings = payload.holdings ?? [];
      await fulfillJson(route, 200, {
        holdings: currentHoldings,
        portfolio_summary: null,
      });
      return;
    }
    if (options.enableCoreFlow && pathname === "/api/holdings/detail") {
      const payload = request.postDataJSON() as {
        holdings?: Array<Record<string, unknown>>;
        index?: number;
      };
      const index = payload.index ?? 0;
      const holding = payload.holdings?.[index] ?? OCR_HOLDING;
      await fulfillJson(route, 200, {
        index,
        holding,
        holding_days: 120,
        first_purchase_date: "2026-03-13",
        latest_nav: 3.214,
        nav_date: "2026-07-10",
        year_return_percent: 12.4,
        fund_code_resolved: true,
        provenance: { holding_days: "user" },
      });
      return;
    }
    if (
      options.enableCoreFlow &&
      pathname === "/api/funds/110022/holdings-distribution"
    ) {
      await fulfillJson(route, 200, {
        fund_code: "110022",
        status: "available",
        report_period: "2026-Q1",
        as_of_date: "2026-03-31",
        disclosed_at: "2026-04-23T00:00:00+08:00",
        freshness: "fresh",
        previous_report_period: "2025-Q4",
        previous_as_of_date: "2025-12-31",
        display_weight_basis: "fund_nav",
        stock_allocation_percent: 82.5,
        disclosed_weight_percent: 6.8,
        holdings: [
          {
            rank: 1,
            security_code: "600519",
            security_name: "Fixture Stock",
            security_market: "CN",
            quote_change_percent: 1.25,
            nav_weight_percent: 6.8,
            display_weight_percent: 6.8,
            display_weight_basis: "fund_nav",
            previous_nav_weight_percent: 6.3,
            previous_display_weight_percent: 6.3,
            change_percent_points: 0.5,
            change_direction: "increased",
            comparison_basis: "fund_nav",
          },
        ],
        source: "e2e_fixture",
        allocation_source: "e2e_fixture",
        quote_session_date: "2026-07-10",
        quote_updated_at: "2026-07-10T15:00:00+08:00",
        quote_source: "e2e_fixture",
        data_note: "Stable quarterly disclosure fixture.",
        generated_at: "2026-07-11T10:00:00+08:00",
        reason_codes: [],
      });
      return;
    }
    if (options.enableCoreFlow && pathname === "/api/holdings/refresh-sector-quotes") {
      const payload = request.postDataJSON() as { holdings?: Array<Record<string, unknown>> };
      currentHoldings = payload.holdings ?? currentHoldings;
      await fulfillJson(route, 200, {
        ok: true,
        message: "fixture refreshed",
        provider_path: "fresh_cache",
        holdings: currentHoldings,
        items: [],
        summary: { matched: currentHoldings.length, unresolved: 0, needs_mapping: 0 },
        fetched_at: "2026-07-11T10:00:00+08:00",
      });
      return;
    }
    if (options.enableCoreFlow && pathname === "/api/sector-quotes/intraday") {
      await fulfillJson(route, 200, {
        source_type: "concept",
        source_name: "食品饮料",
        session_date: "2026-07-10",
        close_change_percent: 1.95,
        points: [
          { time: "09:30", percent: 0.1 },
          { time: "10:30", percent: 1.1 },
          { time: "15:00", percent: 1.95 },
        ],
      });
      return;
    }
    if (pathname === "/api/investor-profile") {
      await fulfillJson(route, 200, {
        style: "稳健",
        horizon: "半年到一年",
        max_drawdown_percent: 8,
        concentration_limit_percent: 35,
        expected_investment_amount: 30_000,
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
      await fulfillJson(route, 200, {
        role_prompt: "",
        is_custom: false,
        default_role_prompt: "",
      });
      return;
    }
    if (pathname === "/api/sector-quotes/status") {
      await fulfillJson(route, 200, {
        enabled: false,
        ttl_seconds: 60,
        auto_interval_seconds: 180,
        idle_interval_seconds: 10_800,
        auto_refresh_allowed: false,
        session: TRADING_SESSION,
      });
      return;
    }
    if (pathname === "/api/trading-session") {
      await fulfillJson(route, 200, TRADING_SESSION);
      return;
    }

    audit.unexpected.push(`${request.method()} ${pathname}`);
    await fulfillJson(route, 200, {});
  });

  return audit;
}

test("模拟登录态可进入响应式应用壳层", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("fundpilot_access_token", "ui-acceptance-token");
  });
  const apiAudit = await installStableApiStubs(page);

  const response = await page.goto("/");
  expect(response?.ok()).toBeTruthy();

  await expect(page.getByRole("heading", { level: 1, name: "账户持仓" })).toBeAttached();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  const holdingsTab = page.getByRole("button", { name: "持仓" });
  await expect(holdingsTab).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
    "href",
    "#main-content",
  );

  const accountMenuTrigger = page.getByRole("button", { name: "打开账号菜单" });
  await expect(accountMenuTrigger).toHaveAttribute("aria-haspopup", "menu");
  await expectMinimumTapTarget(accountMenuTrigger);
  await accountMenuTrigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu", { name: "账号菜单" })).toBeVisible();
  const settingsMenuItem = page.getByRole("menuitem", { name: "账号设置" });
  await expect(settingsMenuItem).toBeFocused();
  await expectMinimumTapTarget(settingsMenuItem);
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "退出登录" })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(settingsMenuItem).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("menuitem", { name: "退出登录" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu", { name: "账号菜单" })).toBeHidden();
  await expect(accountMenuTrigger).toBeFocused();

  if ((page.viewportSize()?.width ?? 1440) < 1024) {
    const moreTrigger = page.getByRole("button", { name: "更多导航" });
    await expect(moreTrigger).toHaveAttribute("aria-haspopup", "menu");
    await expectMinimumTapTarget(moreTrigger);
    await moreTrigger.focus();
    await page.keyboard.press("Space");
    await expect(page.getByRole("menu", { name: "更多页面" })).toBeVisible();
    const discoveryMenuItem = page.getByRole("menuitem", { name: "发现基金" });
    await expect(discoveryMenuItem).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "生成日报" })).toBeFocused();
    await page.keyboard.press("End");
    await expect(page.getByRole("menuitem", { name: "生成日报" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu", { name: "更多页面" })).toBeHidden();
    await expect(moreTrigger).toBeFocused();
  }

  await page.waitForTimeout(100);
  expect(apiAudit.unexpected, "出现了尚未登记的初始化 API 请求").toEqual([]);
  expect([...apiAudit.seen]).toEqual(
    expect.arrayContaining([
      "GET /api/auth/me",
      "GET /api/portfolio/refresh-and-hydrate",
      "POST /api/telemetry/web-vitals",
    ]),
  );
  await expectNoHorizontalOverflow(page);
});

test("截图识别可校对写入并打开基金详情", async ({ page }, testInfo) => {
  test.skip(
    !["desktop-1440", "mobile-320"].includes(testInfo.project.name),
    "核心纵向任务流在桌面与最窄手机视口验收",
  );

  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("fundpilot_access_token", "ui-acceptance-token");
  });
  const apiAudit = await installStableApiStubs(page, { enableCoreFlow: true });

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "账户持仓" })).toBeVisible();
  await page.getByRole("button", { name: /上传截图.*新增持有/ }).click();

  const importDialog = page.getByRole("dialog", { name: "导入持有" });
  await expect(importDialog).toBeVisible();
  await importDialog.locator('input[type="file"]').setInputFiles({
    name: "holding.png",
    mimeType: "image/png",
    buffer: Buffer.from("stable-ui-ocr-fixture"),
  });

  const confirmDialog = page.getByRole("dialog", { name: "确认识别结果" });
  await expect(confirmDialog).toBeVisible();
  const codeInput = confirmDialog.getByRole("textbox", { name: /基金代码/ });
  const amountInput = confirmDialog.getByRole("textbox", { name: /持有金额/ });
  await expect(codeInput).toHaveValue("110022");
  await expect(amountInput).toHaveValue("28640.5");
  await expectMinimumTapTarget(codeInput);
  await expectMinimumTapTarget(amountInput);
  await confirmDialog.getByRole("button", { name: "完成（1）" }).click();

  const holdingCard = page.getByRole("button", {
    name: /易方达消费行业股票，持有金额 28,640\.50/,
  });
  await expect(holdingCard).toBeVisible();
  await holdingCard.click();

  const detailDialog = page.getByRole("dialog", { name: "易方达消费行业股票" });
  await expect(detailDialog).toBeVisible();
  await expect(detailDialog.getByRole("button", { name: "修改持仓" })).toBeVisible();
  await expect(detailDialog.getByText("Fixture Stock")).toBeVisible();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await page.keyboard.press("Escape");
  await expect(detailDialog).toBeHidden();
  await expect(holdingCard).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("");

  await expectNoHorizontalOverflow(page);
  expect(apiAudit.unexpected, "核心任务流出现未登记 API 请求").toEqual([]);
  expect([...apiAudit.seen]).toEqual(
    expect.arrayContaining([
      "POST /api/ocr",
      "POST /api/portfolio/apply-holdings",
      "POST /api/holdings/detail",
      "GET /api/funds/110022/holdings-distribution",
    ]),
  );
});
