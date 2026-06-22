const api = require("../../utils/api");
const { CLOUDBASE_ENV_ID } = require("../../utils/config");

Page({
  data: {
    loading: false,
    error: "",
  },

  async onLogin() {
    this.setData({ loading: true, error: "" });
    try {
      let payload = {};
      if (!api.shouldUseCallContainer()) {
        if (CLOUDBASE_ENV_ID && wx.cloud && wx.cloud.auth) {
          const state = await wx.cloud.auth().getLoginState();
          if (state && state.user) {
            payload.cloudbaseAccessToken = state.accessToken;
            payload.cloudbaseUid = state.user.uid;
          }
        }
        if (!payload.cloudbaseUid && !payload.cloudbaseAccessToken) {
          payload.cloudbaseUid = "dev-mp-" + Date.now();
        }
      }
      const session = await api.wechatLogin(payload);
      api.setToken(session.accessToken);
      // 登录默认落地简报首页（Req 17.1）；briefing 为 TabBar 页，reLaunch 合法。
      wx.reLaunch({ url: "/pages/briefing/briefing" });
    } catch (err) {
      this.setData({
        error: (err && err.message) || "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
