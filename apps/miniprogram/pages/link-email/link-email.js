const api = require("../../utils/api");

Page({
  data: {
    account: "",
    password: "",
    loading: false,
    error: "",
  },

  onAccountInput(e) {
    this.setData({ account: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  async onLink() {
    const account = (this.data.account || "").trim();
    const password = this.data.password || "";
    if (!account || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const session = await api.linkEmail(account, password);
      api.setToken(session.accessToken);
      wx.showToast({ title: "关联成功", icon: "success" });
      wx.reLaunch({ url: "/pages/holdings/holdings" });
    } catch (err) {
      this.setData({ error: (err && err.message) || "关联失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onCancel() {
    wx.navigateBack({ delta: 1 });
  },
});
