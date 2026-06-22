// 底层请求层与决策 helper
//
// 本模块从 utils/api.js 抽离底层请求实现（行为不变）：
//   - callContainer 调用 + 基础设施错误重试（≤3，退避 600ms×n）后回退公网 HTTP
//   - 401 非登录入口清 token 跳登录
//   - 状态码 ≥400 抛出含后端 `detail` 的错误
//
// 同时暴露 4 个**纯函数** helper（不依赖 wx.*，可在 Node 下属性测试）：
//   - buildAuthHeader(token)
//   - shouldClearToken(statusCode, isAuthEntrypoint, allowUnauthorized)
//   - extractError(status, body)
//   - buildQuery(params)
//
// 注意：模块顶层不触碰 wx.*，仅 require 常量配置，使纯函数可在 Node 环境直接 require。

const { API_BASE, CLOUDBASE_ENV_ID, CLOUD_SERVICE_NAME } = require("./config");

const TOKEN_KEY = "fundpilot_access_token";
const CONTAINER_MAX_ATTEMPTS = 3;
const DEFAULT_ERROR_MESSAGE = "请求失败";

// ---------------------------------------------------------------------------
// 纯函数决策 helper（无 wx.* 依赖，可属性测试）
// ---------------------------------------------------------------------------

// Property 2: 鉴权头注入
// 非空 token → { Authorization: "Bearer <token>" }；无 token → 空对象（不注入）。
function buildAuthHeader(token) {
  if (token) {
    return { Authorization: "Bearer " + token };
  }
  return {};
}

// Property 3: 401 清登态决策
// 当且仅当 statusCode === 401 且非登录/注册入口且未显式允许未授权时，判定应清 token 跳登录。
function shouldClearToken(statusCode, isAuthEntrypoint, allowUnauthorized) {
  return statusCode === 401 && !isAuthEntrypoint && !allowUnauthorized;
}

// Property 4: 错误文案提取
// status ≥ 400 时，若响应体含字符串 `detail` 则取之，否则取默认文案；构造 Error 且不抛异常。
function extractError(status, body) {
  let message = DEFAULT_ERROR_MESSAGE;
  if (body && typeof body.detail === "string" && body.detail) {
    message = body.detail;
  }
  return new Error(message);
}

// Property 7: 枚举请求参数构造
// 从 params 对象构造查询串，包含且仅包含非空参数；空对象/无参数返回 ""。
function buildQuery(params) {
  if (!params || typeof params !== "object") {
    return "";
  }
  const parts = [];
  Object.keys(params).forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") {
      return;
    }
    parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
  });
  return parts.length ? "?" + parts.join("&") : "";
}

// ---------------------------------------------------------------------------
// Token 存储（依赖 wx.*，仅在运行时调用）
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// 响应处理与底层请求（依赖 wx.*，行为与原 api.js 保持一致）
// ---------------------------------------------------------------------------

function handleResponse(res, resolve, reject, options) {
  const opts = options || {};
  const statusCode = res.statusCode || 0;
  if (shouldClearToken(statusCode, opts.isAuthEntrypoint, opts.allowUnauthorized)) {
    clearToken();
    wx.reLaunch({ url: "/pages/login/login" });
    reject(new Error("未登录"));
    return;
  }
  if (statusCode >= 400) {
    reject(extractError(statusCode, res.data));
    return;
  }
  resolve(res.data);
}

// callContainer 网关瞬时错误（冷启动/系统错误，如 102002），可重试或回退 HTTP。
function _containerInfraError(err) {
  const message = (err && err.errMsg) || "云托管暂时不可用";
  const wrapped = new Error(message);
  wrapped.containerInfra = true;
  return wrapped;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
        // 仅网关/系统层失败（非业务状态码）才标记为可重试的基础设施错误
        reject(_containerInfraError(err));
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

async function requestViaCallContainerWithRetry(path, options, headers) {
  let lastError;
  for (let attempt = 0; attempt < CONTAINER_MAX_ATTEMPTS; attempt++) {
    try {
      return await requestViaCallContainer(path, options, headers);
    } catch (err) {
      // 业务错误（401/4xx 等）直接抛出，不重试
      if (!err || !err.containerInfra) {
        throw err;
      }
      lastError = err;
      if (attempt < CONTAINER_MAX_ATTEMPTS - 1) {
        await delay(600 * (attempt + 1));
      }
    }
  }
  throw lastError;
}

async function request(path, options) {
  const opts = options || {};
  const headers = Object.assign(
    { "Content-Type": "application/json" },
    opts.header || {},
    buildAuthHeader(getToken()),
  );

  if (!shouldUseCallContainer()) {
    return requestViaHttp(path, opts, headers);
  }

  try {
    return await requestViaCallContainerWithRetry(path, opts, headers);
  } catch (err) {
    if (err && err.containerInfra) {
      // callContainer 网关持续不可用（冷启动失败/系统错误）→ 回退公网 HTTP。
      // 开发者工具需勾选「不校验合法域名」；真机需配置 request 合法域名。
      try {
        return await requestViaHttp(path, opts, headers);
      } catch (httpErr) {
        const fallbackFailed = new Error("服务暂时不可用，请稍后重试");
        fallbackFailed.cause = httpErr;
        throw fallbackFailed;
      }
    }
    throw err;
  }
}

module.exports = {
  // 纯函数决策 helper
  buildAuthHeader: buildAuthHeader,
  shouldClearToken: shouldClearToken,
  extractError: extractError,
  buildQuery: buildQuery,
  // token 存储
  getToken: getToken,
  setToken: setToken,
  clearToken: clearToken,
  // 底层请求
  request: request,
  requestViaCallContainer: requestViaCallContainer,
  requestViaCallContainerWithRetry: requestViaCallContainerWithRetry,
  requestViaHttp: requestViaHttp,
  handleResponse: handleResponse,
  shouldUseCallContainer: shouldUseCallContainer,
  // 常量（便于测试/复用）
  TOKEN_KEY: TOKEN_KEY,
  CONTAINER_MAX_ATTEMPTS: CONTAINER_MAX_ATTEMPTS,
  DEFAULT_ERROR_MESSAGE: DEFAULT_ERROR_MESSAGE,
};
