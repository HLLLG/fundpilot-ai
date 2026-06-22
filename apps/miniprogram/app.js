const { API_BASE, CLOUDBASE_ENV_ID } = require("./utils/config");
const request = require("./utils/request");

const LOGIN_ROUTE = "pages/login/login";
const LOGIN_URL = "/pages/login/login";

App({
  globalData: {
    apiBase: API_BASE,
    cloudbaseEnvId: CLOUDBASE_ENV_ID,
  },

  onLaunch() {
    if (CLOUDBASE_ENV_ID && wx.cloud) {
      wx.cloud.init({
        env: CLOUDBASE_ENV_ID,
        traceUser: true,
      });
    }
    this.enforceLoginGuard();
  },

  onShow() {
    this.enforceLoginGuard();
  },

  // 全局登录守卫（Req 3.4）：未登录则将用户导向登录页而非主导航页面。
  // 各 Tab 页 onShow 亦会复核（Tab 切换不会触发 App.onShow），形成双重保障。
  // 返回 true 表示已登录，false 表示未登录（已触发跳转或当前已在登录页）。
  enforceLoginGuard() {
    let token = "";
    try {
      token = request.getToken();
    } catch (err) {
      token = "";
    }
    if (token) {
      return true;
    }

    const pages = (typeof getCurrentPages === "function" && getCurrentPages()) || [];
    const current = pages.length ? pages[pages.length - 1] : null;
    const route = current ? current.route || current.__route__ || "" : "";
    // 登录页自身不再守卫，避免重复 reLaunch 造成循环。
    if (route && route.indexOf(LOGIN_ROUTE) !== -1) {
      return false;
    }
    wx.reLaunch({ url: LOGIN_URL });
    return false;
  },
});
