#!/usr/bin/env node
/**
 * 工作台交互与 Web Vitals 本机基准（复用仓库已有的 Playwright 与静态预览服务器，
 * 不引入新依赖）。
 *
 * 度量口径：
 * - 加载类指标（TTFB / FCP / LCP / CLS）来自浏览器自身的 PerformanceObserver，与
 *   线上 `WebVitalsReporter` 上报的是同一批指标定义。
 * - 交互延迟取 `event` timing 里带 `interactionId` 的最大 duration，也就是 INP 的
 *   同口径取法；同时记录该次切换期间的 long task 总时长与端到端墙钟时间。
 * - 每个 tab 切换重复多轮取中位数；这是单机样本，不能外推生产。
 *
 * 用法：
 *   node scripts/perf/ui-interaction-benchmark.mjs
 *   node scripts/perf/ui-interaction-benchmark.mjs --json report.json --rounds 5
 */

import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function parseArgs(argv) {
  const args = { json: null, rounds: 5, port: 3199 };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--json") args.json = argv[(i += 1)];
    else if (argv[i] === "--rounds") args.rounds = Number(argv[(i += 1)]);
    else if (argv[i] === "--port") args.port = Number(argv[(i += 1)]);
  }
  return args;
}

const TRADING_SESSION = {
  timezone: "Asia/Shanghai",
  local_datetime: "2026-07-25T10:00:00+08:00",
  calendar_date: "2026-07-25",
  effective_trade_date: "2026-07-24",
  is_trading_day: false,
  session_kind: "non_trading_day",
  market_open_time: "09:30",
  decision_window: "closed",
  market_close_time: "15:00",
};

/** 造一批持仓，让持仓看板与组合看板都有真实数量的行要渲染。 */
function holdings(count) {
  return Array.from({ length: count }, (_, index) => ({
    fund_code: String(110000 + index),
    fund_name: `基准测试基金${index + 1}`,
    holding_amount: 10_000 + index * 137.5,
    holding_profit: 120.5 - index * 3,
    holding_return_percent: 1.2 - index * 0.05,
    daily_profit: 12.3 - index * 0.4,
    daily_return_percent: 0.31 - index * 0.01,
    sector_name: index % 2 === 0 ? "食品饮料" : "电子",
    sector_return_percent: 0.42,
  }));
}

/**
 * 每个 tab 用一个"面板已挂载"的稳定标记；选择器给的是并集，任一命中即算到位，
 * 这样不依赖 stub 数据是否完整。超时故意设得短，miss 不会把整轮时间拖长。
 */
const TABS = [
  { label: "持仓", selector: ".holdings-ledger" },
  {
    label: "我的",
    extraClick: "盈亏分析",
    selector: '[data-testid="portfolio-allocation-section"], .pl-page, .analysis-hero, .pl-range-bar',
  },
  { label: "市场", selector: ".market-nav, .market-workspace, #main-content section" },
  { label: "日报", selector: ".report-navigator" },
];
const PANEL_TIMEOUT_MS = 3000;

function median(values) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

async function installApiStubs(page) {
  const portfolio = {
    holdings: holdings(12),
    source: "database",
    refreshed_at: "2026-07-24T15:00:00+08:00",
    portfolio_summary: null,
  };
  await page.route("**/api/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204 });
    if (pathname === "/api/auth/me") {
      return json({
        id: 9001,
        userRole: "user",
        username: "基准用户",
        userAccount: "perf@example.com",
        bio: "",
        avatarUrl: "",
      });
    }
    if (pathname === "/api/portfolio/refresh-and-hydrate" || pathname === "/api/dashboard/bootstrap") {
      return json({
        portfolio,
        investor_profile: {},
        analysis_prompt: { role_prompt: "", is_custom: false, default_role_prompt: "" },
        sector_quotes_status: {
          enabled: false,
          ttl_seconds: 60,
          auto_interval_seconds: 180,
          idle_interval_seconds: 10_800,
          auto_refresh_allowed: false,
          session: TRADING_SESSION,
        },
      });
    }
    if (pathname === "/api/portfolio/holdings") return json(portfolio);
    if (pathname === "/api/trading-session") return json(TRADING_SESSION);
    if (pathname === "/api/reports" || pathname === "/api/fund-discovery/reports") return json([]);
    // 其余读接口一律给空成功体：基准只关心渲染与交互成本，不关心业务内容。
    return json({});
  });
}

const COLLECTOR = `
  window.__perf = { longTasks: [], interactions: [], shifts: [], cls: 0, lcp: null, fcp: null, ttfb: null };
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__perf.longTasks.push({ start: entry.startTime, duration: entry.duration });
    }).observe({ type: "longtask", buffered: true });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.interactionId) window.__perf.interactions.push({ start: entry.startTime, duration: entry.duration, name: entry.name });
      }
    }).observe({ type: "event", buffered: true, durationThreshold: 0 });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.hadRecentInput) continue;
        window.__perf.cls += entry.value;
        // 同时记录位移来源。只有指标没有来源，CLS 是没法定位的。
        for (const source of entry.sources ?? []) {
          const node = source.node;
          window.__perf.shifts.push({
            value: Number(entry.value.toFixed(4)),
            startTime: Math.round(entry.startTime),
            node: node
              ? \`\${node.nodeName.toLowerCase()}\${node.id ? "#" + node.id : ""}\${
                  node.className && typeof node.className === "string"
                    ? "." + node.className.trim().split(/\\s+/).slice(0, 4).join(".")
                    : ""
                }\`
              : "(detached)",
            from: source.previousRect
              ? \`\${Math.round(source.previousRect.x)},\${Math.round(source.previousRect.y)} \${Math.round(source.previousRect.width)}x\${Math.round(source.previousRect.height)}\`
              : null,
            to: source.currentRect
              ? \`\${Math.round(source.currentRect.x)},\${Math.round(source.currentRect.y)} \${Math.round(source.currentRect.width)}x\${Math.round(source.currentRect.height)}\`
              : null,
          });
        }
      }
    }).observe({ type: "layout-shift", buffered: true });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      window.__perf.lcp = entries[entries.length - 1]?.startTime ?? window.__perf.lcp;
    }).observe({ type: "largest-contentful-paint", buffered: true });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (entry.name === "first-contentful-paint") window.__perf.fcp = entry.startTime;
    }).observe({ type: "paint", buffered: true });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      const nav = list.getEntries()[0];
      if (nav) window.__perf.ttfb = nav.responseStart;
    }).observe({ type: "navigation", buffered: true });
  } catch {}
`;

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const baseUrl = `http://127.0.0.1:${args.port}`;

  const server = spawn(process.execPath, [resolve(webRoot, "scripts/serve-static.mjs")], {
    cwd: webRoot,
    env: { ...process.env, PORT: String(args.port) },
    stdio: "ignore",
  });

  const browser = await chromium.launch();
  const report = {
    generatedAt: new Date().toISOString(),
    rounds: args.rounds,
    note: "单机 headless 样本，只用于同一台机器上的前后对比，不代表生产指标。",
  };

  try {
    // 等静态服务器起来。
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const res = await fetch(baseUrl, { method: "HEAD" });
        if (res.ok || res.status === 404) break;
      } catch {
        await new Promise((r) => setTimeout(r, 250));
      }
    }

    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
    });
    const page = await context.newPage();
    await page.addInitScript(COLLECTOR);
    await page.addInitScript(() => {
      try {
        localStorage.setItem("fundpilot_access_token", "perf-benchmark-token");
      } catch {}
    });
    await installApiStubs(page);

    await page.goto(baseUrl, { waitUntil: "load" });
    await page.waitForSelector(".holdings-ledger", { timeout: 30_000 });
    await page.waitForTimeout(1200);

    report.load = await page.evaluate(() => ({
      // stub 不完整会让工作台弹出一条 InlineNotice，它插在顶栏与页头之间、高约 66px，
      // 会把整页下推并污染 CLS。这里显式披露，避免把"测量环境造出来的位移"当成产品缺陷。
      noticePresent: Boolean(document.querySelector(".inline-notice")),
      noticeText: document.querySelector(".inline-notice")?.textContent?.trim().slice(0, 120) ?? null,
      ttfbMs: window.__perf.ttfb,
      fcpMs: window.__perf.fcp,
      lcpMs: window.__perf.lcp,
      cls: Number(window.__perf.cls.toFixed(4)),
      shifts: window.__perf.shifts,
      longTaskCount: window.__perf.longTasks.length,
      longTaskTotalMs: Number(
        window.__perf.longTasks.reduce((sum, t) => sum + t.duration, 0).toFixed(1),
      ),
    }));

    const nav = page.locator('nav.dashboard-top-nav button[type="button"]');
    const transitions = {};
    for (let round = 0; round < args.rounds; round += 1) {
      for (const tab of TABS) {
        await page.evaluate(() => {
          window.__perf.longTasks.length = 0;
          window.__perf.interactions.length = 0;
        });
        const started = Date.now();
        await nav.filter({ hasText: tab.label }).first().click();
        if (tab.extraClick) {
          await page.getByRole("button", { name: tab.extraClick, exact: true }).click();
        }
        let panelHit = true;
        try {
          await page.waitForSelector(tab.selector, { timeout: PANEL_TIMEOUT_MS });
        } catch {
          // 面板标记没命中时仍记录墙钟时间，报告里通过 panelMissRounds 标出来。
          panelHit = false;
        }
        const wallMs = Date.now() - started;
        await page.waitForTimeout(250);
        const sample = await page.evaluate(() => ({
          maxInteractionMs: window.__perf.interactions.reduce((max, e) => Math.max(max, e.duration), 0),
          longTaskTotalMs: window.__perf.longTasks.reduce((sum, t) => sum + t.duration, 0),
        }));
        const bucket = (transitions[tab.label] ??= {
          wall: [],
          interaction: [],
          longTask: [],
          miss: 0,
        });
        bucket.wall.push(wallMs);
        bucket.interaction.push(sample.maxInteractionMs);
        bucket.longTask.push(sample.longTaskTotalMs);
        if (!panelHit) bucket.miss += 1;
      }
    }

    report.tabSwitch = Object.fromEntries(
      Object.entries(transitions).map(([label, b]) => [
        label,
        {
          wallMsMedian: median(b.wall),
          maxInteractionMsMedian: median(b.interaction),
          longTaskMsMedian: median(b.longTask),
          panelMissRounds: b.miss,
        },
      ]),
    );

    console.log("加载指标（1440x900, headless）");
    console.log(`  TTFB ${report.load.ttfbMs?.toFixed(1)} ms`);
    console.log(`  FCP  ${report.load.fcpMs?.toFixed(1)} ms`);
    console.log(`  LCP  ${report.load.lcpMs?.toFixed(1)} ms`);
    console.log(`  CLS  ${report.load.cls}`);
    if (report.load.noticePresent) {
      console.log(
        `  [注意] 工作台出现了提示条，CLS 含它插入造成的位移：${report.load.noticeText}`,
      );
    }
    for (const shift of report.load.shifts) {
      console.log(
        `    位移 ${shift.value} @${shift.startTime}ms  ${shift.node}  ${shift.from} -> ${shift.to}`,
      );
    }
    console.log(`  长任务 ${report.load.longTaskCount} 个 / ${report.load.longTaskTotalMs} ms`);
    console.log("\ntab 切换（每项取中位数）");
    console.log("| tab | 墙钟 ms | 最大交互 ms | 长任务 ms |");
    console.log("| --- | ---: | ---: | ---: |");
    for (const [label, value] of Object.entries(report.tabSwitch)) {
      console.log(
        `| ${label} | ${value.wallMsMedian} | ${value.maxInteractionMsMedian?.toFixed(1)} | ${value.longTaskMsMedian?.toFixed(1)} |`,
      );
    }

    await context.close();
  } finally {
    await browser.close().catch(() => undefined);
    server.kill();
  }

  if (args.json) {
    writeFileSync(args.json, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.log(`\n已写入 ${args.json}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
