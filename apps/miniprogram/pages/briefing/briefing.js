// pages/briefing/briefing.js
// 简报首页（Briefing_Page）
//
// 功能（阶段七 Task 9.3，Req 17）：
//   1. 登录后默认落地（Req 17.1，由 login → switchTab 简报 Tab 处理；本页 onShow 复核登录）。
//   2. 组合 KPI 摘要：总资产 / 当日收益 / 持有收益（Req 17.2，derive.summarizeHoldings + 持有收益汇总）。
//   3. 板块脉搏摘要：取主题板块涨跌排序的前几条（Req 17.3）。
//   4. 最新日报决策卡摘要：listReports 取最新一份，展示标题/风险/建议摘要（Req 17.4）。
//   5. 内联非流式追问：复用 chat-panel（chatType='report'），reportId 指向最新日报（Req 17.5，A3/BC1）。
//
// 缓存优先：持仓走 cache:holdings 即时渲染再后台替换（Req 4.3）。
// 三态：主数据（持仓 KPI）用 state-view 包裹 loading/error（Req 4.1/4.2）。
//
// Requirements: 17.1, 17.2, 17.3, 17.4, 17.5

const api = require("../../utils/api");
const derive = require("../../utils/derive");
const cache = require("../../utils/cache");

// 板块脉搏摘要展示条数
const SECTOR_PULSE_LIMIT = 4;

// 板块类型标签
const BOARD_KIND_LABELS = {
  industry: "行业",
  concept: "概念",
  index: "指数",
};

// 风险等级 → 中文标签
const RISK_LEVEL_LABELS = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

// 建议动作 → 中文标签
const SUGGESTED_ACTION_LABELS = {
  watch: "观望",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "风险复核",
};

/** ISO 时间字符串 → YYYY-MM-DD HH:MM（用于决策卡时间展示） */
function formatDateTime(isoStr) {
  if (!isoStr) return "";
  try {
    var str = String(isoStr).replace("T", " ").replace("Z", "").split(".")[0];
    var parts = str.split(" ");
    var date = parts[0] || "";
    var time = parts.length >= 2 ? parts[1].substring(0, 5) : "";
    return time ? date + " " + time : date;
  } catch (_) {
    return String(isoStr);
  }
}

Page({
  data: {
    title: "简报",

    // ── 组合 KPI 摘要（Req 17.2）─────────────────────────
    kpi: { totalAssets: 0, dailyProfit: 0, holdingProfit: 0 },
    hasHoldings: false,
    refreshedAt: "",

    // 主数据（持仓）三态
    loading: true,
    error: "",

    // ── 板块脉搏摘要（Req 17.3）──────────────────────────
    sectorPulse: [],
    sectorLoading: false,
    sectorError: "",

    // ── 最新日报决策卡摘要（Req 17.4）────────────────────
    latestReport: null,       // 处理后的决策卡摘要对象（无日报时为 null）
    reportsLoading: false,

    // ── 内联追问（Req 17.5）──────────────────────────────
    chatReportId: "",         // 传给 chat-panel 的 reportId（最新日报 id）
    showChat: false,          // 是否展开追问面板
  },

  onShow: function () {
    // Tab 页登录复核（Req 3.4 / 17.1）
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this._loadAll();
  },

  // ---------------------------------------------------------------------------
  // 统一加载入口
  // ---------------------------------------------------------------------------

  _loadAll: function () {
    this._loadHoldings();
    this._loadSectorPulse();
    this._loadLatestReport();
  },

  // ---------------------------------------------------------------------------
  // 组合 KPI 摘要（Req 17.2）
  // ---------------------------------------------------------------------------

  _loadHoldings: function () {
    var self = this;
    self.setData({ loading: true, error: "" });

    // 缓存优先即时渲染（Req 4.3）
    var cached = cache.get(cache.KEYS.holdings);
    if (cached) {
      self._applyHoldings(cached);
    }

    api
      .fetchPortfolioHoldings()
      .then(function (data) {
        cache.set(cache.KEYS.holdings, data);
        self._applyHoldings(data);
        self.setData({ loading: false, error: "" });
      })
      .catch(function (err) {
        // 已有缓存时不覆盖为错误态，仅在无缓存可展示时报错
        if (self.data.hasHoldings) {
          self.setData({ loading: false });
        } else {
          self.setData({
            loading: false,
            error: (err && err.message) || "加载失败，请稍后重试",
          });
        }
      });
  },

  /**
   * 把持仓 payload 汇总为 KPI 摘要。
   * 总资产 / 当日收益来自 derive.summarizeHoldings（Property 17），
   * 持有收益由各持仓 holding_profit 汇总。
   * @param {object|Array} payload  PortfolioHoldingsPayload 或 holdings 数组
   */
  _applyHoldings: function (payload) {
    var holdings = [];
    if (payload && Array.isArray(payload.holdings)) {
      holdings = payload.holdings;
    } else if (Array.isArray(payload)) {
      holdings = payload;
    }

    var summary = derive.summarizeHoldings(holdings);

    // 持有收益：汇总各持仓 holding_profit（容差内求和，空/非数跳过）
    var holdingProfit = 0;
    for (var i = 0; i < holdings.length; i++) {
      var h = holdings[i];
      if (!h) continue;
      var hp = Number(h.holding_profit);
      if (Number.isFinite(hp)) holdingProfit += hp;
    }

    this.setData({
      kpi: {
        totalAssets: summary.totalAssets,
        dailyProfit: summary.dailyProfit,
        holdingProfit: holdingProfit,
      },
      hasHoldings: holdings.length > 0,
      refreshedAt: (payload && payload.refreshed_at) || "",
    });
  },

  onHoldingsRetry: function () {
    this._loadHoldings();
  },

  // ---------------------------------------------------------------------------
  // 板块脉搏摘要（Req 17.3）
  // ---------------------------------------------------------------------------

  _loadSectorPulse: function () {
    var self = this;
    self.setData({ sectorLoading: true, sectorError: "" });

    // 缓存优先（cache:theme:change）
    var cacheKey = cache.KEYS.theme("change");
    var cached = cache.get(cacheKey);
    if (cached) {
      self._applySectorPulse(cached);
    }

    api
      .fetchMarketThemeBoards({ sort: "change" })
      .then(function (data) {
        cache.set(cacheKey, data);
        self._applySectorPulse(data);
        self.setData({ sectorLoading: false, sectorError: "" });
      })
      .catch(function (err) {
        self.setData({
          sectorLoading: false,
          sectorError: (err && err.message) || "板块数据加载失败",
        });
      });
  },

  /**
   * 取主题板块前 SECTOR_PULSE_LIMIT 条作为板块脉搏摘要。
   * @param {object} data  MarketThemeBoardResponse
   */
  _applySectorPulse: function (data) {
    if (!data) return;
    var items = Array.isArray(data.items) ? data.items : [];
    var pulse = [];
    for (var i = 0; i < items.length && pulse.length < SECTOR_PULSE_LIMIT; i++) {
      var item = items[i];
      if (!item) continue;
      pulse.push({
        sector_label: item.sector_label || "",
        kindLabel: BOARD_KIND_LABELS[item.board_kind] || item.board_kind || "",
        change_1d_percent:
          item.change_1d_percent != null ? item.change_1d_percent : null,
        consecutive_up_days:
          item.consecutive_up_days != null ? item.consecutive_up_days : null,
        in_portfolio: !!item.in_portfolio,
      });
    }
    this.setData({ sectorPulse: pulse });
  },

  onSectorRetry: function () {
    this._loadSectorPulse();
  },

  // ---------------------------------------------------------------------------
  // 最新日报决策卡摘要（Req 17.4）+ 内联追问绑定（Req 17.5）
  // ---------------------------------------------------------------------------

  _loadLatestReport: function () {
    var self = this;
    self.setData({ reportsLoading: true });

    api
      .listReports()
      .then(function (data) {
        var reports = Array.isArray(data) ? data : (data && data.reports) || [];
        var latest = self._pickLatestReport(reports);
        if (latest) {
          self.setData({
            latestReport: self._buildDecisionCard(latest),
            // 最新日报存在时，绑定内联追问的 reportId（Req 17.5）
            chatReportId: latest.id || "",
            reportsLoading: false,
          });
        } else {
          self.setData({
            latestReport: null,
            chatReportId: "",
            reportsLoading: false,
          });
        }
      })
      .catch(function () {
        // 决策卡为辅助信息，失败时静默降级（不阻塞 KPI 主数据）
        self.setData({ latestReport: null, chatReportId: "", reportsLoading: false });
      });
  },

  /** 取 created_at 最新的一份日报（防御性排序，不假设后端已排序）。 */
  _pickLatestReport: function (reports) {
    if (!Array.isArray(reports) || reports.length === 0) return null;
    var latest = null;
    for (var i = 0; i < reports.length; i++) {
      var r = reports[i];
      if (!r) continue;
      if (!latest) {
        latest = r;
        continue;
      }
      var a = String(r.created_at || "");
      var b = String(latest.created_at || "");
      if (a > b) latest = r;
    }
    return latest;
  },

  /**
   * 从 Report 构造决策卡摘要展示对象。
   * @param {object} report  Report
   */
  _buildDecisionCard: function (report) {
    var risk = report.risk || {};
    var recommendations = Array.isArray(report.recommendations)
      ? report.recommendations.slice(0, 3)
      : [];
    return {
      id: report.id || "",
      title: report.title || "最新日报",
      createdAt: formatDateTime(report.created_at || ""),
      summary: report.summary || "",
      riskLevel: risk.level || "",
      riskLevelLabel: RISK_LEVEL_LABELS[risk.level] || "",
      suggestedAction: risk.suggested_action || "",
      suggestedActionLabel: SUGGESTED_ACTION_LABELS[risk.suggested_action] || "",
      weightedReturnPercent:
        risk.weighted_return_percent != null ? risk.weighted_return_percent : null,
      recommendations: recommendations,
    };
  },

  // ---------------------------------------------------------------------------
  // 内联追问（Req 17.5）
  // ---------------------------------------------------------------------------

  /** 展开/收起内联追问面板。 */
  onToggleChat: function () {
    if (!this.data.chatReportId) return;
    this.setData({ showChat: !this.data.showChat });
  },

  /** 跳转到完整日报页（生成日报 Tab）。 */
  onOpenReport: function () {
    wx.switchTab({ url: "/pages/report/report" });
  },
});
