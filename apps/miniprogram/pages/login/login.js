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
      if (CLOUDBASE_ENV_ID && wx.cloud) {
        const loginState = await wx.cloud.callFunction({ name: "login" }).catch(() => null);
        if (loginState && loginState.result && loginState.result.uid) {
          payload.cloudbaseUid = loginState.result.uid;
        } else {
          const auth = wx.cloud.CloudID ? null : null;
          void auth;
          const cloud = wx.cloud;
          if (cloud && cloud.auth) {
            const state = await cloud.auth().getLoginState();
            if (state && state.user) {
              payload.cloudbaseAccessToken = state.accessToken;
              payload.cloudbaseUid = state.user.uid;
            }
          }
        }
      }
      if (!payload.cloudbaseUid && !payload.cloudbaseAccessToken) {
        payload.cloudbaseUid = `dev-mp-${Date.now()}`;
      }
      const session = await api.wechatLogin(payload);
      api.setToken(session.accessToken);
      wx.reLaunch({ url: "/pages/holdings/holdings" });
    } catch (err) {
      this.setData({
        error: (err && err.message) || "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
