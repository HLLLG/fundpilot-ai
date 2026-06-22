// Feature: miniprogram-web-parity, Task 1.13 — utils/api.js 按域封装能力函数
//
// Validates: Requirements 2.1, 2.6
//
// 用例要点：
//   - 让 shouldUseCallContainer() 返回 false（wx.cloud 缺省）→ 请求走 wx.request，
//     便于断言 URL/method/data 构造与响应字段抽取（job_id/markdown/items/...）。
//   - uploadFile：注入 Bearer、JSON.parse 字符串响应、复用 401 与 ≥400 错误处理。

import { describe, it, expect, beforeEach } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const api = require("../utils/api");
const { API_BASE } = require("../utils/config");

const TOKEN = "tkn-123";

// 安装一个最小 wx mock：wx.cloud 缺省 → request 走 wx.request 公网路径。
function installWx(overrides) {
  const calls = { request: [], upload: [], reLaunch: [], removeStorage: [] };
  global.wx = Object.assign(
    {
      getStorageSync: () => TOKEN,
      setStorageSync: () => {},
      removeStorageSync: (k) => calls.removeStorage.push(k),
      reLaunch: (opts) => calls.reLaunch.push(opts),
      // 默认 request：回 200 + 给定 body
      request: (opts) => {
        calls.request.push(opts);
        opts.success({ statusCode: 200, data: (overrides && overrides.body) || {} });
      },
    },
    overrides && overrides.wx,
  );
  return calls;
}

describe("Task 1.13: utils/api.js 按域封装", () => {
  beforeEach(() => {
    delete global.wx;
  });

  it("注入 Bearer 并构造 GET 路径（fetchTradingSession）", async () => {
    const calls = installWx({ body: { is_trading_day: true } });
    const res = await api.fetchTradingSession();
    expect(res).toEqual({ is_trading_day: true });
    expect(calls.request).toHaveLength(1);
    expect(calls.request[0].url).toBe(API_BASE + "/api/trading-session");
    expect(calls.request[0].header.Authorization).toBe("Bearer " + TOKEN);
  });

  it("POST 体与方法（applyPortfolioHoldings）", async () => {
    const calls = installWx({ body: { holdings: [] } });
    await api.applyPortfolioHoldings([{ fund_code: "001" }]);
    const c = calls.request[0];
    expect(c.method).toBe("POST");
    expect(c.url).toBe(API_BASE + "/api/portfolio/apply-holdings");
    expect(c.data).toEqual({ holdings: [{ fund_code: "001" }] });
  });

  it("抽取嵌套字段 job_id（startAnalyzeJob）", async () => {
    installWx({ body: { job_id: "job-1" } });
    const jobId = await api.startAnalyzeJob([], { style: "x" }, "ocr", "fast", "  role  ");
    expect(jobId).toBe("job-1");
  });

  it("抽取 body.markdown（fetchReportMarkdown）", async () => {
    installWx({ body: { markdown: "# hi" } });
    expect(await api.fetchReportMarkdown("r1")).toBe("# hi");
  });

  it("抽取 body.items 并兜底空数组（searchFunds）", async () => {
    const calls = installWx({ body: { items: [{ fund_code: "001", fund_name: "A" }] } });
    const items = await api.searchFunds("白酒", 5);
    expect(items).toEqual([{ fund_code: "001", fund_name: "A" }]);
    expect(calls.request[0].url).toContain("q=" + encodeURIComponent("白酒"));
    expect(calls.request[0].url).toContain("limit=5");
  });

  it("抽取 body.labels / body.sectors / body.messages 兜底空数组", async () => {
    installWx({ body: {} });
    expect(await api.fetchSectorLabels()).toEqual([]);
    expect(await api.fetchDiscoverySectors()).toEqual([]);
    expect(await api.fetchReportChatHistory("r1")).toEqual([]);
    expect(await api.fetchDiscoveryChatHistory("d1")).toEqual([]);
  });

  it("查询串仅含非空参数（fetchDipRadar 不带 sector/force_refresh）", async () => {
    const calls = installWx({ body: { items: [] } });
    await api.fetchDipRadar({ lookbackDays: 3 });
    const url = calls.request[0].url;
    expect(url).toContain("lookback_days=3");
    expect(url).toContain("limit=20");
    expect(url).not.toContain("sector=");
    expect(url).not.toContain("force_refresh");
  });

  it("force_refresh 仅在 true 时出现（fetchUsMarketOverview）", async () => {
    const calls = installWx({ body: {} });
    await api.fetchUsMarketOverview(false);
    expect(calls.request[0].url).toBe(API_BASE + "/api/market/us-overview");
    await api.fetchUsMarketOverview(true);
    expect(calls.request[1].url).toContain("force_refresh=true");
  });

  it("fetchHoldingDetail 兼容数组与 payload 两种入参", async () => {
    const calls = installWx({ body: { index: 0 } });
    await api.fetchHoldingDetail([{ fund_code: "001" }], 2);
    expect(calls.request[0].data).toEqual({
      holdings: [{ fund_code: "001" }],
      index: 2,
      portfolio_summary: null,
      sector_quote_meta: null,
    });
    await api.fetchHoldingDetail({ holdings: [{ fund_code: "002" }], index: 1, portfolio_summary: { x: 1 } });
    expect(calls.request[1].data).toEqual({
      holdings: [{ fund_code: "002" }],
      index: 1,
      portfolio_summary: { x: 1 },
      sector_quote_meta: null,
    });
  });

  it("≥400 抛含后端 detail 的错误（request 路径）", async () => {
    installWx({
      wx: {
        request: (opts) => opts.success({ statusCode: 422, data: { detail: "校验失败" } }),
      },
    });
    await expect(api.fetchTradingSession()).rejects.toThrow("校验失败");
  });

  // --- uploadFile -----------------------------------------------------------

  it("uploadFile 注入 Bearer、解析 JSON 响应（parseOcrUpload preview）", async () => {
    installWx({});
    const captured = [];
    global.wx.uploadFile = (opts) => {
      captured.push(opts);
      opts.success({ statusCode: 200, data: JSON.stringify({ holdings: [1], preview: true }) });
    };
    const res = await api.parseOcrUpload("/tmp/a.png", { preview: true });
    expect(res).toEqual({ holdings: [1], preview: true });
    expect(captured[0].url).toBe(API_BASE + "/api/ocr");
    expect(captured[0].name).toBe("file");
    expect(captured[0].filePath).toBe("/tmp/a.png");
    expect(captured[0].formData).toEqual({ preview: "true" });
    expect(captured[0].header.Authorization).toBe("Bearer " + TOKEN);
  });

  it("uploadFile 在 401 时清 token 并跳登录", async () => {
    const calls = installWx({});
    global.wx.uploadFile = (opts) => {
      opts.success({ statusCode: 401, data: "" });
    };
    await expect(api.transactionsOcr("/tmp/b.png")).rejects.toThrow("未登录");
    expect(calls.removeStorage.length).toBeGreaterThan(0);
    expect(calls.reLaunch.length).toBeGreaterThan(0);
  });

  it("uploadFile 在 ≥400 时抛含后端 detail 的错误", async () => {
    installWx({});
    global.wx.uploadFile = (opts) => {
      opts.success({ statusCode: 500, data: JSON.stringify({ detail: "上传失败" }) });
    };
    await expect(api.transactionsOcr("/tmp/c.png")).rejects.toThrow("上传失败");
  });
});
