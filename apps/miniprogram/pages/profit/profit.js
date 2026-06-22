// pages/profit/profit.js
// 盈亏分析页（Profit_Analysis_Page）
//
// 数据来源：GET /api/portfolio/dashboard（fetchPortfolioDashboard）
// 功能：
//   - onShow：以默认 range='month' 加载仪表盘数据（Req 9.1）
//   - 区间切换 today/week/month/year/all：重新请求（Req 9.2）
//   - chart-line：收益走势 vs 沪深300（Req 9.3，Property 8）
//   - chart-donut：持仓分布（Req 9.7，Property 8）
//   - 当日盈亏 TOP5 盈利/亏损列表（Req 9.6，Property 11）
//   - state-view 三态（Req 4.1/4.2）
//
// Requirements: 9.1, 9.2, 9.3, 9.6, 9.7

var api = require("../../utils/api");
var derive = require("../../utils/derive");
var cache = require("../../utils/cache");

// 区间选项
var RANGES = [
  { label: "今日", value: "today" },
  { label: "本周", value: "week" },
  { label: "本月", value: "month" },
  { label: "今年", value: "year" },
  { label: "全部", value: "all" },
];

// 图表颜色（配合设计 token）
var DONUT_COLORS = [
  "#2356e0", "#cf9b3e", "#2bae66", "#e5484d", "#7c3aed",
  "#0891b2", "#d97706", "#16a34a", "#dc2626", "#6366f1",
];

Page({
  data: {
    // 区间选项与当前选中下标
    ranges: RANGES,
    rangeIndex: 2, // 默认 'month'

    // 仪表盘原始数据
    dashboardData: null,

    // 图表 option
    lineChartOption: null,
    donutChartOption: null,

    // 当日 TOP5
    gainers: [],
    losers: [],

    // 摘要 KPI（总资产、持有收益、当日收益）
    summary: null,

    // 状态
    loading: true,
    error: "",
    isEmpty: false,
  },

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    // 首次或重新显示时加载数据（用当前选中的区间）
    this._loadDashboard(RANGES[this.data.rangeIndex].value);
  },

  // -------------------------------------------------------------------------
  // 数据加载
  // -------------------------------------------------------------------------

  /**
   * 拉取仪表盘数据并更新所有派生状态。
   * @param {string} range  today/week/month/year/all（Req 9.2）
   */
  _loadDashboard: function (range) {
    var self = this;
    this.setData({ loading: true, error: "", isEmpty: false });

    // 缓存优先展示（Req 4.3）
    var cacheKey = "cache:dashboard:" + range;
    var cached = cache.get(cacheKey);
    if (cached) {
      self._applyDashboardData(cached);
    }

    api
      .fetchPortfolioDashboard({ range: range })
      .then(function (data) {
        cache.set(cacheKey, data);
        self._applyDashboardData(data);
        self.setData({ loading: false, error: "" });
      })
      .catch(function (err) {
        self.setData({
          loading: false,
          error: (err && err.message) || "加载失败，请稍后重试",
        });
      });
  },

  /**
   * 将后端响应数据转换为页面所需的所有派生数据。
   * @param {object} data  PortfolioDashboardData
   */
  _applyDashboardData: function (data) {
    if (!data) return;

    // 收益走势折线图（Req 9.3）
    var trendPoints = (data.profit_trend && data.profit_trend.points) || [];
    var lineOption = derive.buildLineChartOption(trendPoints);
    // 更语义化的系列名
    if (lineOption && lineOption.series && lineOption.series.length >= 2) {
      lineOption.series[0].name = "我的收益";
      lineOption.series[1].name = "沪深300";
      // 配色：主色 + 暖金
      lineOption.series[0].lineStyle = { color: "#2356e0", width: 2 };
      lineOption.series[0].itemStyle = { color: "#2356e0" };
      lineOption.series[1].lineStyle = { color: "#cf9b3e", width: 2 };
      lineOption.series[1].itemStyle = { color: "#cf9b3e" };
    }

    // 持仓分布甜甜圈图（Req 9.7）
    var allocation = data.allocation || [];
    var donutOption = derive.buildDonutChartOption(allocation);
    // 扩充样式（颜色、label）
    if (donutOption && donutOption.series && donutOption.series[0]) {
      donutOption.series[0].color = DONUT_COLORS;
      donutOption.series[0].label = {
        show: allocation.length <= 8,
        formatter: "{b}\n{d}%",
        fontSize: 10,
      };
      donutOption.series[0].itemStyle = {
        borderRadius: 4,
        borderWidth: 2,
        borderColor: "#ffffff",
      };
      donutOption.tooltip = { trigger: "item", formatter: "{b}: {d}%" };
    }

    // 当日 TOP5（Req 9.6，Property 11）
    var holdings = (data.summary && data.summary.holdings) || [];
    var top5 = derive.deriveDailyTop5(holdings);

    // 摘要 KPI
    var summary = data.summary || null;

    // 空态判断：走势点与持仓都为空
    var isEmpty = trendPoints.length === 0 && allocation.length === 0 && holdings.length === 0;

    this.setData({
      dashboardData: data,
      lineChartOption: lineOption,
      donutChartOption: donutOption,
      gainers: top5.gainers,
      losers: top5.losers,
      summary: summary,
      isEmpty: isEmpty,
    });
  },

  // -------------------------------------------------------------------------
  // 交互事件
  // -------------------------------------------------------------------------

  /** 区间 Tab 切换（Req 9.2） */
  onRangeTap: function (e) {
    var idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.rangeIndex) return;
    this.setData({ rangeIndex: idx });
    this._loadDashboard(RANGES[idx].value);
  },

  /** 错误态「重试」 */
  onRetry: function () {
    this._loadDashboard(RANGES[this.data.rangeIndex].value);
  },
});
