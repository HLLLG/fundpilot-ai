// pages/holdings/holdings.js
// Holdings_Board 持仓看板页。
//
// Requirements: 5.1（GET /api/portfolio/holdings 获取并展示持仓）
//               5.2（缓存优先即时展示，后端响应替换）
//               5.3（展示基金名称/持有金额/持有收益率/当日收益/板块涨跌）
//               5.4（刷新板块：POST /api/holdings/refresh-sector-quotes）
//               5.5（点击跳转基金详情）
//               5.6（持仓为空：展示空态 + OCR 录入 + 关联邮箱入口）
//               5.7（展示数据更新时间 refreshed_at）

var api = require("../../utils/api");
var cache = require("../../utils/cache");
var derive = require("../../utils/derive");

Page({
  data: {
    holdings: [],
    summary: null,
    refreshedAt: "",
    loading: true,
    refreshing: false,
    error: "",
    isEmpty: false,
    // 是否正在使用缓存数据（stale 标记，后台刷新期间为 true）
    stale: false,
  },

  onShow: function () {
    // 登录守卫
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    // 缓存优先即时渲染（Req 5.2）
    var cached = cache.get(cache.KEYS.holdings);
    if (cached && cached.holdings) {
      this._applyHoldingsData(cached, true);
    } else {
      this.setData({ loading: true, error: "" });
    }
    // 后台请求替换
    this._fetchHoldings();
  },

  // 内部：后台拉取持仓并替换渲染
  _fetchHoldings: function () {
    var self = this;
    api.fetchPortfolioHoldings()
      .then(function (data) {
        // 写缓存（供下次缓存优先读取，Req 5.2）
        cache.set(cache.KEYS.holdings, data);
        self._applyHoldingsData(data, false);
      })
      .catch(function (err) {
        // 若已有缓存数据展示，仅静默标记 stale；否则展示错误态
        if (!self.data.holdings.length) {
          self.setData({
            loading: false,
            error: (err && err.message) || "加载失败，请稍后重试",
          });
        } else {
          self.setData({ loading: false, stale: true });
        }
      });
  },

  // 将接口数据（或缓存数据）应用到页面 data
  _applyHoldingsData: function (data, isFromCache) {
    var holdings = (data && data.holdings) || [];
    // 按当日收益降序排列（Req 5.3 体现，derive.js 纯函数）
    var sorted = derive.holdingsSortByDailyProfit(holdings);
    this.setData({
      holdings: sorted,
      summary: (data && data.portfolio_summary) || null,
      refreshedAt: (data && data.refreshed_at) || "",
      loading: false,
      error: "",
      stale: isFromCache,
      isEmpty: sorted.length === 0,
    });
  },

  // 刷新板块（Req 5.4）
  onRefreshSectors: function () {
    if (this.data.refreshing) return;
    if (!this.data.holdings.length) return;
    var self = this;
    this.setData({ refreshing: true, error: "" });
    api.refreshSectorQuotes(this.data.holdings)
      .then(function (result) {
        var newHoldings = (result && result.holdings) || self.data.holdings;
        var sorted = derive.holdingsSortByDailyProfit(newHoldings);
        self.setData({
          holdings: sorted,
          refreshing: false,
          refreshedAt: (result && result.fetched_at) || self.data.refreshedAt,
        });
        // 更新缓存（刷新板块后的结果也保存）
        var cached = cache.get(cache.KEYS.holdings) || {};
        cache.set(cache.KEYS.holdings, Object.assign({}, cached, {
          holdings: sorted,
          refreshed_at: (result && result.fetched_at) || cached.refreshed_at,
        }));
      })
      .catch(function (err) {
        self.setData({
          refreshing: false,
          error: (err && err.message) || "刷新板块失败",
        });
      });
  },

  // 点击持仓跳详情（Req 5.5）
  onOpenDetail: function (e) {
    var index = e.currentTarget.dataset.index;
    wx.navigateTo({
      url: "/pages/fund-detail/fund-detail?index=" + index,
    });
  },

  // 空态入口：OCR 录入（Req 5.6）
  onGoOcrImport: function () {
    wx.navigateTo({ url: "/pages/ocr-import/ocr-import" });
  },

  // 空态入口：关联邮箱账号（Req 5.6）
  onGoLink: function () {
    wx.navigateTo({ url: "/pages/link-email/link-email" });
  },

  // 错误态重试
  onRetry: function () {
    this.setData({ loading: true, error: "", isEmpty: false });
    this._fetchHoldings();
  },

  // 退出登录（保留兼容性）
  onLogout: function () {
    api.clearToken();
    wx.reLaunch({ url: "/pages/login/login" });
  },
});
