// 能力函数封装层
//
// 底层请求（callContainer 重试 / HTTP 回退 / 401 / 错误提取 / 鉴权头）已抽离至
// utils/request.js。本模块在其之上**按域**逐一封装能力函数，命名对齐 Web
// （apps/web/src/lib/api.ts 的导出全集），并保留对 token 与底层能力的透传导出
// 以兼容现有页面调用。
//
// 域分组（见 design.md「Web 能力面映射」「API 层封装规范」，Req 2）：
//   auth / holdings / ocr / funds / profit / market / report / discovery / risk / session
//
// 统一行为（由 request.js 保证）：注入 Authorization: Bearer（Req 2.2）、
// 401 非登录入口清 token 跳登录（Req 2.3）、callContainer 基础设施错误重试 ≤3 后
// 回退公网 HTTP（Req 2.4）、状态码 ≥400 抛含后端 detail 的错误（Req 2.5）。

const request = require("./request");
const { API_BASE } = require("./config");

const LOGIN_PAGE = "/pages/login/login";

// ---------------------------------------------------------------------------
// multipart 上传（Req 2.6 / Req 6，BC2）
// ---------------------------------------------------------------------------
//
// wx.uploadFile 不走 callContainer 的 wx.request 管道，经已配置的公网/合法上传
// 域名提交。注入 Bearer，解析字符串响应为 JSON，并复用 request.js 的纯函数
// helper（shouldClearToken / extractError / buildAuthHeader）以与 request() 行为一致。
function uploadFile(path, filePath, formData, name) {
  return new Promise((resolve, reject) => {
    const header = Object.assign({}, request.buildAuthHeader(request.getToken()));
    wx.uploadFile({
      url: API_BASE + path,
      filePath: filePath,
      name: name || "file",
      formData: formData || {},
      header: header,
      success(res) {
        const statusCode = res.statusCode || 0;
        // wx.uploadFile 的响应体始终为字符串，需要 JSON.parse。
        let body = {};
        let parseFailed = false;
        try {
          body = res.data ? JSON.parse(res.data) : {};
        } catch (e) {
          parseFailed = true;
        }
        // 401 非登录入口（上传端点恒非登录入口）→ 清 token 跳登录。
        if (request.shouldClearToken(statusCode, false, false)) {
          request.clearToken();
          wx.reLaunch({ url: LOGIN_PAGE });
          reject(new Error("未登录"));
          return;
        }
        // ≥400（含响应解析失败）→ 抛含后端 detail 的错误。
        if (statusCode >= 400) {
          reject(request.extractError(statusCode, body));
          return;
        }
        if (parseFailed) {
          reject(new Error("上传响应解析失败"));
          return;
        }
        resolve(body);
      },
      fail(err) {
        reject(err);
      },
    });
  });
}

// ===========================================================================
// 鉴权/账号（/api/auth/*）
// ===========================================================================

function registerUser(payload) {
  return request.request("/api/auth/register", {
    method: "POST",
    data: payload || {},
    allowUnauthorized: true,
  });
}

function loginUser(payload) {
  return request.request("/api/auth/login", {
    method: "POST",
    data: payload || {},
    allowUnauthorized: true,
  });
}

function fetchCurrentUser() {
  return request.request("/api/auth/me");
}

function bindWechatAccount(payload) {
  return request.request("/api/auth/bind-wechat", {
    method: "POST",
    data: payload || {},
  });
}

// 既有能力（保留）：微信一键登录 / 绑定邮箱账号。
function wechatLogin(payload) {
  return request.request("/api/auth/wechat-login", {
    method: "POST",
    data: payload || {},
    allowUnauthorized: true,
  });
}

function linkEmail(account, password) {
  return request.request("/api/auth/link-email", {
    method: "POST",
    data: { userAccount: account, password: password },
  });
}

// ===========================================================================
// 持仓（/api/portfolio/*、/api/holdings/*、/api/sector-mappings/apply）
// ===========================================================================

function fetchPortfolioHoldings() {
  return request.request("/api/portfolio/holdings");
}

function fetchPortfolioSummary() {
  return request.request("/api/portfolio/summary");
}

function applyPortfolioHoldings(holdings) {
  return request.request("/api/portfolio/apply-holdings", {
    method: "POST",
    data: { holdings: holdings },
  });
}

function refreshSectorQuotes(holdings, options) {
  const opts = options || {};
  return request.request("/api/holdings/refresh-sector-quotes", {
    method: "POST",
    data: {
      holdings: holdings,
      force_refresh: opts.forceRefresh != null ? opts.forceRefresh : false,
      budget: opts.budget || "fast",
    },
  });
}

function applySectorMapping(holdings, payload) {
  const p = payload || {};
  return request.request("/api/sector-mappings/apply", {
    method: "POST",
    data: {
      holdings: holdings,
      index: p.index,
      source_type: p.source_type,
      source_name: p.source_name,
      source_code: p.source_code != null ? p.source_code : null,
    },
  });
}

// 既有能力（保留）：持仓列表（页面沿用）。
function fetchHoldings() {
  return request.request("/api/portfolio/holdings");
}

// ---------------------------------------------------------------------------
// 持仓明细/图表（/api/holdings/detail、/api/sector-quotes/*）
// ---------------------------------------------------------------------------

// 兼容既有调用 fetchHoldingDetail(holdings, index) 与 Web 形态 fetchHoldingDetail(payload)。
function fetchHoldingDetail(holdingsOrPayload, index) {
  let data;
  if (Array.isArray(holdingsOrPayload)) {
    data = {
      holdings: holdingsOrPayload,
      index: index,
      portfolio_summary: null,
      sector_quote_meta: null,
    };
  } else {
    const p = holdingsOrPayload || {};
    data = {
      holdings: p.holdings,
      index: p.index,
      portfolio_summary: p.portfolio_summary != null ? p.portfolio_summary : null,
      sector_quote_meta: p.sector_quote_meta != null ? p.sector_quote_meta : null,
    };
  }
  return request.request("/api/holdings/detail", { method: "POST", data: data });
}

function fetchSectorIntraday(payload, options) {
  const p = payload || {};
  const opts = options || {};
  const query = request.buildQuery({
    source_type: p.source_type,
    source_name: p.source_name,
    force_refresh: opts.forceRefresh ? "true" : "",
  });
  return request.request("/api/sector-quotes/intraday" + query);
}

function fetchSectorQuotesStatus() {
  return request.request("/api/sector-quotes/status");
}

// ===========================================================================
// OCR/交易（/api/ocr、/api/transactions/*、/api/funds/{code}/transactions）
// ===========================================================================

// 解析持仓截图：multipart 上传（Req 6）。filePath 为 wx.chooseMedia 选取的临时路径。
function parseOcrUpload(filePath, options) {
  const opts = options || {};
  const formData = {};
  if (opts.preview) {
    formData.preview = "true";
  }
  return uploadFile("/api/ocr", filePath, formData, "file");
}

// 解析交易记录截图：multipart 上传。
function transactionsOcr(filePath) {
  return uploadFile("/api/transactions/ocr", filePath, {}, "file");
}

function applyTransactions(transactions) {
  return request.request("/api/transactions/apply", {
    method: "POST",
    data: { transactions: transactions },
  });
}

function getFundTransactions(code) {
  return request.request(
    "/api/funds/" + encodeURIComponent(code) + "/transactions",
  );
}

// ===========================================================================
// 基金档案/净值（/api/funds/search、/api/fund-profiles/*）
// ===========================================================================

async function searchFunds(query, limit) {
  const q = request.buildQuery({ q: query, limit: String(limit != null ? limit : 12) });
  const body = await request.request("/api/funds/search" + q);
  return (body && body.items) || [];
}

function updateFundProfile(fundCode, patch) {
  return request.request(
    "/api/fund-profiles/" + encodeURIComponent(fundCode),
    { method: "PATCH", data: patch || {} },
  );
}

function updateFundProfilePurchaseDate(fundCode, firstPurchaseDate) {
  return updateFundProfile(fundCode, { first_purchase_date: firstPurchaseDate });
}

function fetchFundNavHistory(fundCode, days) {
  const query = request.buildQuery({ days: String(days != null ? days : 90) });
  return request.request(
    "/api/fund-profiles/" + encodeURIComponent(fundCode) + "/nav-history" + query,
  );
}

function fetchFundNavHistoryPage(fundCode, options) {
  const opts = options || {};
  const query = request.buildQuery({
    limit: String(opts.limit != null ? opts.limit : 30),
    before_date: opts.before || "",
  });
  return request.request(
    "/api/fund-profiles/" + encodeURIComponent(fundCode) + "/nav-history/page" + query,
  );
}

// ===========================================================================
// 盈亏分析（/api/portfolio/dashboard、/api/market/index-daily）
// ===========================================================================

function fetchPortfolioDashboard(options) {
  const opts = options || {};
  const query = request.buildQuery({
    range: opts.range || "",
    calendar_year: opts.calendarYear != null ? String(opts.calendarYear) : "",
    calendar_month: opts.calendarMonth != null ? String(opts.calendarMonth) : "",
  });
  return request.request("/api/portfolio/dashboard" + query);
}

function fetchIndexDailyHistory(symbol, days) {
  const query = request.buildQuery({
    symbol: symbol || "000300",
    days: String(days != null ? days : 252),
  });
  return request.request("/api/market/index-daily" + query);
}

// ===========================================================================
// 市场（/api/market/*、/api/fund-discovery/sectors）
// ===========================================================================

function fetchMarketThemeBoards(options) {
  const opts = options || {};
  const query = request.buildQuery({
    sort: opts.sort || "change",
    force_refresh: opts.forceRefresh ? "true" : "",
  });
  return request.request("/api/market/theme-boards" + query);
}

function fetchDipRadar(options) {
  const opts = options || {};
  const query = request.buildQuery({
    lookback_days: String(opts.lookbackDays != null ? opts.lookbackDays : 5),
    limit: String(opts.limit != null ? opts.limit : 20),
    sector: opts.sector || "",
    force_refresh: opts.forceRefresh ? "true" : "",
  });
  return request.request("/api/market/dip-radar" + query);
}

function fetchUsMarketOverview(forceRefresh) {
  const query = request.buildQuery({ force_refresh: forceRefresh ? "true" : "" });
  return request.request("/api/market/us-overview" + query);
}

async function fetchDiscoverySectors() {
  const body = await request.request("/api/fund-discovery/sectors");
  return (body && body.sectors) || [];
}

async function fetchSectorLabels() {
  const body = await request.request("/api/market/sector-labels");
  return (body && body.labels) || [];
}

// ===========================================================================
// 日报（/api/analyze/async、/api/jobs/{id}、/api/reports/*、/api/news/preview、诊断）
// ===========================================================================

async function startAnalyzeJob(holdings, profile, ocrText, analysisMode, systemRolePrompt) {
  const body = await request.request("/api/analyze/async", {
    method: "POST",
    data: {
      holdings: holdings,
      profile: profile,
      ocr_text: ocrText,
      analysis_mode: analysisMode || "deep",
      system_role_prompt: (systemRolePrompt && systemRolePrompt.trim()) || null,
    },
  });
  return body && body.job_id;
}

function fetchAnalysisJob(jobId) {
  return request.request("/api/jobs/" + jobId);
}

function previewNewsForHoldings(holdings, profile) {
  return request.request("/api/news/preview", {
    method: "POST",
    data: { holdings: holdings, profile: profile },
  });
}

function listReports() {
  return request.request("/api/reports");
}

function deleteReport(reportId) {
  return request.request("/api/reports/" + reportId, { method: "DELETE" });
}

function fetchReportOutcomes(reportId) {
  return request.request("/api/reports/" + reportId + "/outcomes");
}

function fetchReportWeeklyOutcomes(reportId, days) {
  const query = request.buildQuery({ days: String(days != null ? days : 7) });
  return request.request("/api/reports/" + reportId + "/outcomes-weekly" + query);
}

function fetchRebalanceSimulation(reportId) {
  return request.request("/api/reports/" + reportId + "/rebalance-simulation");
}

async function fetchReportMarkdown(reportId) {
  const body = await request.request("/api/reports/" + reportId + "/markdown");
  return body && body.markdown;
}

function fetchRecommendationAccuracy(limitReports) {
  const query = request.buildQuery({
    days: String(limitReports != null ? limitReports : 30),
  });
  return request.request("/api/reports/recommendation-accuracy" + query);
}

function fetchSectorSignalBacktest(days, sectors) {
  const params = { days: String(days != null ? days : 120) };
  if (sectors && sectors.length) {
    params.sectors = sectors.join(",");
  }
  const query = request.buildQuery(params);
  return request.request("/api/diagnostics/sector-signal-backtest" + query);
}

// ---------------------------------------------------------------------------
// 日报追问（/api/reports/{id}/chat[/sync]，BC1 非流式）
// ---------------------------------------------------------------------------

async function fetchReportChatHistory(reportId) {
  const body = await request.request("/api/reports/" + reportId + "/chat");
  return (body && body.messages) || [];
}

// 非流式聚合追问（替代 Web 的 streamReportChat，命中 */chat/sync，BC1）。
function sendReportChat(reportId, message, chatMode) {
  return request.request("/api/reports/" + reportId + "/chat/sync", {
    method: "POST",
    data: { message: message, chat_mode: chatMode },
  });
}

async function fetchReportChatMarkdown(reportId) {
  const body = await request.request("/api/reports/" + reportId + "/chat/markdown");
  return body && body.markdown;
}

// ===========================================================================
// 荐基（/api/fund-discovery/*、/api/discovery-prompt）
// ===========================================================================

async function startDiscoveryJob(holdings, profile, options) {
  const opts = options || {};
  const body = await request.request("/api/fund-discovery/async", {
    method: "POST",
    data: {
      holdings: holdings,
      profile: profile,
      analysis_mode: opts.analysisMode || "deep",
      focus_sectors: opts.focusSectors || [],
      budget_yuan: opts.budgetYuan != null ? opts.budgetYuan : null,
      fund_type_preference: opts.fundTypePreference || "any",
      selection_strategy: opts.selectionStrategy || "balanced",
      scan_mode: opts.scanMode || "full_market",
      dip_lookback_days: opts.dipLookbackDays != null ? opts.dipLookbackDays : 5,
      dip_min_drop_percent: opts.dipMinDropPercent != null ? opts.dipMinDropPercent : 3.0,
      system_role_prompt: opts.systemRolePrompt != null ? opts.systemRolePrompt : null,
    },
  });
  return body && body.job_id;
}

function listDiscoveryReports() {
  return request.request("/api/fund-discovery/reports");
}

function deleteDiscoveryReport(reportId) {
  return request.request("/api/fund-discovery/reports/" + reportId, {
    method: "DELETE",
  });
}

function fetchDiscoveryOutcomes(reportId, days) {
  const query = request.buildQuery({ days: String(days != null ? days : 7) });
  return request.request(
    "/api/fund-discovery/reports/" + reportId + "/outcomes" + query,
  );
}

function fetchDiscoveryPrompt() {
  return request.request("/api/discovery-prompt");
}

function saveDiscoveryPromptRemote(rolePrompt) {
  return request.request("/api/discovery-prompt", {
    method: "PUT",
    data: { role_prompt: rolePrompt },
  });
}

async function fetchDiscoveryChatHistory(reportId) {
  const body = await request.request(
    "/api/fund-discovery/reports/" + reportId + "/chat",
  );
  return (body && body.messages) || [];
}

// 非流式聚合荐基追问（替代 Web 的 streamDiscoveryChat，命中 */chat/sync，BC1）。
function sendDiscoveryChat(reportId, message, chatMode) {
  return request.request(
    "/api/fund-discovery/reports/" + reportId + "/chat/sync",
    { method: "POST", data: { message: message, chat_mode: chatMode } },
  );
}

// ===========================================================================
// 风控/盯盘/Prompt（/api/investor-profile、/api/swing-alerts/*、/api/analysis-prompt）
// ===========================================================================

function fetchInvestorProfile() {
  return request.request("/api/investor-profile");
}

function saveInvestorProfileRemote(profile) {
  return request.request("/api/investor-profile", {
    method: "PUT",
    data: profile || {},
  });
}

function evaluateSwingAlerts(holdings, profile) {
  const p = profile || {};
  return request.request("/api/swing-alerts/evaluate", {
    method: "POST",
    data: {
      holdings: holdings,
      profile: p,
      monitor_scope: p.swing_monitor_scope || "both",
    },
  });
}

function fetchSwingAlertsToday() {
  return request.request("/api/swing-alerts/today");
}

function fetchAnalysisPrompt() {
  return request.request("/api/analysis-prompt");
}

function saveAnalysisPromptRemote(rolePrompt) {
  return request.request("/api/analysis-prompt", {
    method: "PUT",
    data: { role_prompt: rolePrompt },
  });
}

// ===========================================================================
// 交易日语义（/api/trading-session）
// ===========================================================================

function fetchTradingSession() {
  return request.request("/api/trading-session");
}

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

module.exports = {
  // token 存储（透传自 request.js）
  getToken: request.getToken,
  setToken: request.setToken,
  clearToken: request.clearToken,
  shouldUseCallContainer: request.shouldUseCallContainer,
  // multipart 上传
  uploadFile: uploadFile,
  // 鉴权/账号
  registerUser: registerUser,
  loginUser: loginUser,
  fetchCurrentUser: fetchCurrentUser,
  bindWechatAccount: bindWechatAccount,
  wechatLogin: wechatLogin,
  linkEmail: linkEmail,
  // 持仓
  fetchPortfolioHoldings: fetchPortfolioHoldings,
  fetchPortfolioSummary: fetchPortfolioSummary,
  applyPortfolioHoldings: applyPortfolioHoldings,
  refreshSectorQuotes: refreshSectorQuotes,
  applySectorMapping: applySectorMapping,
  fetchHoldings: fetchHoldings,
  // 持仓明细/图表
  fetchHoldingDetail: fetchHoldingDetail,
  fetchSectorIntraday: fetchSectorIntraday,
  fetchSectorQuotesStatus: fetchSectorQuotesStatus,
  // OCR/交易
  parseOcrUpload: parseOcrUpload,
  transactionsOcr: transactionsOcr,
  applyTransactions: applyTransactions,
  getFundTransactions: getFundTransactions,
  // 基金档案/净值
  searchFunds: searchFunds,
  updateFundProfile: updateFundProfile,
  updateFundProfilePurchaseDate: updateFundProfilePurchaseDate,
  fetchFundNavHistory: fetchFundNavHistory,
  fetchFundNavHistoryPage: fetchFundNavHistoryPage,
  // 盈亏分析
  fetchPortfolioDashboard: fetchPortfolioDashboard,
  fetchIndexDailyHistory: fetchIndexDailyHistory,
  // 市场
  fetchMarketThemeBoards: fetchMarketThemeBoards,
  fetchDipRadar: fetchDipRadar,
  fetchUsMarketOverview: fetchUsMarketOverview,
  fetchDiscoverySectors: fetchDiscoverySectors,
  fetchSectorLabels: fetchSectorLabels,
  // 日报
  startAnalyzeJob: startAnalyzeJob,
  fetchAnalysisJob: fetchAnalysisJob,
  previewNewsForHoldings: previewNewsForHoldings,
  listReports: listReports,
  deleteReport: deleteReport,
  fetchReportOutcomes: fetchReportOutcomes,
  fetchReportWeeklyOutcomes: fetchReportWeeklyOutcomes,
  fetchRebalanceSimulation: fetchRebalanceSimulation,
  fetchReportMarkdown: fetchReportMarkdown,
  fetchRecommendationAccuracy: fetchRecommendationAccuracy,
  fetchSectorSignalBacktest: fetchSectorSignalBacktest,
  // 日报追问
  fetchReportChatHistory: fetchReportChatHistory,
  sendReportChat: sendReportChat,
  fetchReportChatMarkdown: fetchReportChatMarkdown,
  // 荐基
  startDiscoveryJob: startDiscoveryJob,
  listDiscoveryReports: listDiscoveryReports,
  deleteDiscoveryReport: deleteDiscoveryReport,
  fetchDiscoveryOutcomes: fetchDiscoveryOutcomes,
  fetchDiscoveryPrompt: fetchDiscoveryPrompt,
  saveDiscoveryPromptRemote: saveDiscoveryPromptRemote,
  fetchDiscoveryChatHistory: fetchDiscoveryChatHistory,
  sendDiscoveryChat: sendDiscoveryChat,
  // 风控/盯盘/Prompt
  fetchInvestorProfile: fetchInvestorProfile,
  saveInvestorProfileRemote: saveInvestorProfileRemote,
  evaluateSwingAlerts: evaluateSwingAlerts,
  fetchSwingAlertsToday: fetchSwingAlertsToday,
  fetchAnalysisPrompt: fetchAnalysisPrompt,
  saveAnalysisPromptRemote: saveAnalysisPromptRemote,
  // 交易日语义
  fetchTradingSession: fetchTradingSession,
};
