const { API_BASE } = require("./config");

const TOKEN_KEY = "fundpilot_access_token";

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token);
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
}

function request(path, options = {}) {
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    options.header || {},
  );
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: headers,
      success(res) {
        if (res.statusCode === 401) {
          clearToken();
          wx.reLaunch({ url: "/pages/login/login" });
          reject(new Error("未登录"));
          return;
        }
        if (res.statusCode >= 400) {
          const detail = (res.data && res.data.detail) || "请求失败";
          reject(new Error(typeof detail === "string" ? detail : "请求失败"));
          return;
        }
        resolve(res.data);
      },
      fail(err) {
        reject(err);
      },
    });
  });
}

function wechatLogin(payload) {
  return request("/api/auth/wechat-login", {
    method: "POST",
    data: payload,
  });
}

function fetchHoldings() {
  return request("/api/portfolio/holdings");
}

function refreshSectorQuotes(holdings) {
  return request("/api/holdings/refresh-sector-quotes", {
    method: "POST",
    data: { holdings, force_refresh: false, budget: "fast" },
  });
}

function fetchHoldingDetail(holdings, index) {
  return request("/api/holdings/detail", {
    method: "POST",
    data: { holdings, index },
  });
}

module.exports = {
  getToken,
  setToken,
  clearToken,
  wechatLogin,
  fetchHoldings,
  refreshSectorQuotes,
  fetchHoldingDetail,
};
