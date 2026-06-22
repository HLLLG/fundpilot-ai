const api = require("../../utils/api");

Page({
  data: {
    holdings: [],
    summary: null,
    loading: true,
    refreshing: false,
    error: "",
  },

  onShow() {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this.loadHoldings();
  },

  async loadHoldings() {
    this.setData({ loading: true, error: "" });
    try {
      const data = await api.fetchHoldings();
      this.setData({
        holdings: data.holdings || [],
        summary: data.portfolio_summary || null,
      });
    } catch (err) {
      this.setData({ error: (err && err.message) || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async onRefreshSectors() {
    if (!this.data.holdings.length) return;
    this.setData({ refreshing: true, error: "" });
    try {
      const result = await api.refreshSectorQuotes(this.data.holdings);
      this.setData({ holdings: result.holdings || this.data.holdings });
    } catch (err) {
      this.setData({ error: (err && err.message) || "刷新失败" });
    } finally {
      this.setData({ refreshing: false });
    }
  },

  onOpenDetail(e) {
    const index = e.currentTarget.dataset.index;
    wx.navigateTo({
      url: `/pages/fund-detail/fund-detail?index=${index}`,
    });
  },

  onLogout() {
    api.clearToken();
    wx.reLaunch({ url: "/pages/login/login" });
  },

  onGoLink() {
    wx.navigateTo({ url: "/pages/link-email/link-email" });
  },
});
