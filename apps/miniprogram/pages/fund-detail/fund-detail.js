const api = require("../../utils/api");

Page({
  data: {
    detail: null,
    loading: true,
    error: "",
  },

  onLoad(query) {
    const index = Number(query.index || 0);
    this.index = index;
    this.loadDetail();
  },

  async loadDetail() {
    this.setData({ loading: true, error: "" });
    try {
      const holdingsData = await api.fetchHoldings();
      const holdings = holdingsData.holdings || [];
      const detail = await api.fetchHoldingDetail(holdings, this.index);
      this.setData({ detail });
    } catch (err) {
      this.setData({ error: (err && err.message) || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },
});
