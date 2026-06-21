const { API_BASE, CLOUDBASE_ENV_ID, CLOUD_SERVICE_NAME } = require("./config");

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

function shouldUseCallContainer() {
  return Boolean(
    CLOUDBASE_ENV_ID && wx.cloud && typeof wx.cloud.callContainer === "function",
  );
}

function handleResponse(res, resolve, reject, options) {
  const statusCode = res.statusCode || 0;
  if (statusCode === 401 && !options.allowUnauthorized) {
    clearToken();
    wx.reLaunch({ url: "/pages/login/login" });
    reject(new Error("未登录"));
    return;
  }
  if (statusCode >= 400) {
    const detail = (res.data && res.data.detail) || "请求失败";
    reject(new Error(typeof detail === "string" ? detail : "请求失败"));
    return;
  }
  resolve(res.data);
}

function requestViaCallContainer(path, options, headers) {
  return new Promise((resolve, reject) => {
    wx.cloud.callContainer({
      config: { env: CLOUDBASE_ENV_ID },
      path: path,
      method: options.method || "GET",
      data: options.data,
      header: Object.assign({}, headers, {
        "X-WX-SERVICE": CLOUD_SERVICE_NAME,
      }),
      success(res) {
        handleResponse(res, resolve, reject, options);
      },
      fail(err) {
        reject(new Error((err && err.errMsg) || "云托管调用失败"));
      },
    });
  });
}

function requestViaHttp(path, options, headers) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: API_BASE + path,
      method: options.method || "GET",
      data: options.data,
      header: headers,
      success(res) {
        handleResponse(res, resolve, reject, options);
      },
      fail(err) {
        reject(err);
      },
    });
  });
}

function request(path, options) {
  const opts = options || {};
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    opts.header || {},
  );
  const token = getToken();
  if (token) {
    headers.Authorization = "Bearer " + token;
  }
  if (shouldUseCallContainer()) {
    return requestViaCallContainer(path, opts, headers);
  }
  return requestViaHttp(path, opts, headers);
}

function wechatLogin(payload) {
  return request("/api/auth/wechat-login", {
    method: "POST",
    data: payload || {},
    allowUnauthorized: true,
  });
}

function fetchHoldings() {
  return request("/api/portfolio/holdings");
}

function refreshSectorQuotes(holdings) {
  return request("/api/holdings/refresh-sector-quotes", {
    method: "POST",
    data: { holdings: holdings, force_refresh: false, budget: "fast" },
  });
}

function fetchHoldingDetail(holdings, index) {
  return request("/api/holdings/detail", {
    method: "POST",
    data: { holdings: holdings, index: index },
  });
}

module.exports = {
  getToken: getToken,
  setToken: setToken,
  clearToken: clearToken,
  wechatLogin: wechatLogin,
  fetchHoldings: fetchHoldings,
  refreshSectorQuotes: refreshSectorQuotes,
  fetchHoldingDetail: fetchHoldingDetail,
  shouldUseCallContainer: shouldUseCallContainer,
};
