import { test, expect } from "@playwright/test";

test("health endpoint returns ok", async ({ request }) => {
  const response = await request.get("/health");
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body.status).toBe("ok");
});

test("trading session endpoint returns session kind", async ({ request }) => {
  const response = await request.get("/api/trading-session");
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body.session_kind).toBeTruthy();
  expect(body.decision_window).toBeTruthy();
  expect(body.effective_trade_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
});

test("offline analyze persists report", async ({ request }) => {
  test.setTimeout(120_000);
  const response = await request.post("/api/analyze", {
    data: {
      holdings: [
        {
          fund_code: "000000",
          fund_name: "测试基金",
          holding_amount: 1000,
          return_percent: 1,
        },
      ],
      profile: {
        style: "稳健",
        horizon: "半年到一年",
        max_drawdown_percent: 8,
        concentration_limit_percent: 35,
        prefer_dca: true,
        avoid_chasing: true,
      },
    },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body.id).toBeTruthy();
  expect(body.provider).toContain("offline");
});
