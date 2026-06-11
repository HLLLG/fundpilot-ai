const { API_BASE, CLOUDBASE_ENV_ID } = require("./utils/config");

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
  },
});
