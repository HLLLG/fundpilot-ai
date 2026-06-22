// utils/nav-state.js
// 跨页预填 / 子视图选择保持（sessionStorage 等价）。
//
// 小程序无 sessionStorage，这里基于会话存储键封装跨页状态，存储后端可注入，
// 以便在 Node 环境（无 wx.*）下做属性测试。
//
// Design: miniprogram-web-parity / Property 5（导航状态往返）：
//   for any (pageKey, value)，写入后读取应得到与写入深度相等的值（round-trip）。
//
// 支持的预填/保持键：
//   - dipRadarSector            主题板块「看大跌基金」预填板块（Req 10.6）
//   - discoveryFocusSectors     主题板块「加入关注方向」（≤3 去重，Req 10.7）
//   - discoveryScanMode         大跌雷达「深度扫描」扫描模式，如 'dip_swing'（Req 11.5）
//   - subView:{pageKey}         各页子视图/Tab 选择保持（Req 3.5）

const NAV_PREFIX = "navState.";

const KEYS = {
  dipRadarSector: NAV_PREFIX + "dipRadarSector",
  discoveryFocusSectors: NAV_PREFIX + "discoveryFocusSectors",
  discoveryScanMode: NAV_PREFIX + "discoveryScanMode",
};

function subViewKey(pageKey) {
  return NAV_PREFIX + "subView:" + String(pageKey);
}

// 默认存储后端：包裹 wx.*，读取不存在/异常的键返回 undefined 而非抛错。
function createWxStorage() {
  return {
    get(key) {
      try {
        const value = wx.getStorageSync(key);
        return value === "" ? undefined : value;
      } catch (err) {
        return undefined;
      }
    },
    set(key, value) {
      wx.setStorageSync(key, value);
    },
    remove(key) {
      try {
        wx.removeStorageSync(key);
      } catch (err) {
        /* noop */
      }
    },
  };
}

// 测试用内存存储后端（无 wx.* 依赖）。
// 用 Object.create(null) 建立无原型的字典，使 "__proto__"/"constructor" 等
// 原型链键被当作普通 own property 读写，避免赋值到原型上导致 round-trip 失真。
function createMemoryStorage() {
  const store = Object.create(null);
  return {
    get(key) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : undefined;
    },
    set(key, value) {
      store[key] = value;
    },
    remove(key) {
      delete store[key];
    },
  };
}

// 通过 JSON 序列化保证 round-trip 深度相等，并隔离引用别名（写入后再改原对象不影响已存值）。
function encode(value) {
  return JSON.stringify(value === undefined ? null : value);
}

function decode(raw) {
  if (raw === undefined || raw === null || raw === "") {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

// 工厂：基于注入的存储后端创建一个 nav-state 实例。
function createNavState(storage) {
  const backend = storage || createWxStorage();

  // 通用 round-trip 读写（Property 5 的核心）。
  function setState(key, value) {
    backend.set(key, encode(value));
  }

  function getState(key) {
    return decode(backend.get(key));
  }

  function clearState(key) {
    backend.remove(key);
  }

  return {
    // 通用接口
    setState,
    getState,
    clearState,

    // dipRadarSector（Req 10.6）
    setDipRadarSector(sector) {
      setState(KEYS.dipRadarSector, sector);
    },
    getDipRadarSector() {
      return getState(KEYS.dipRadarSector);
    },
    clearDipRadarSector() {
      clearState(KEYS.dipRadarSector);
    },

    // discoveryFocusSectors（Req 10.7）
    setDiscoveryFocusSectors(sectors) {
      setState(KEYS.discoveryFocusSectors, sectors);
    },
    getDiscoveryFocusSectors() {
      const value = getState(KEYS.discoveryFocusSectors);
      return Array.isArray(value) ? value : [];
    },
    clearDiscoveryFocusSectors() {
      clearState(KEYS.discoveryFocusSectors);
    },

    // discoveryScanMode（Req 11.5）
    setDiscoveryScanMode(mode) {
      setState(KEYS.discoveryScanMode, mode);
    },
    getDiscoveryScanMode() {
      return getState(KEYS.discoveryScanMode);
    },
    clearDiscoveryScanMode() {
      clearState(KEYS.discoveryScanMode);
    },

    // 各页子视图保持（Req 3.5）
    setSubView(pageKey, view) {
      setState(subViewKey(pageKey), view);
    },
    getSubView(pageKey) {
      return getState(subViewKey(pageKey));
    },
    clearSubView(pageKey) {
      clearState(subViewKey(pageKey));
    },
  };
}

// 默认实例（运行时使用 wx 存储后端）。
const defaultNavState = createNavState();

module.exports = {
  KEYS,
  subViewKey,
  createWxStorage,
  createMemoryStorage,
  createNavState,
  // 默认实例方法直接导出，页面层可直接 require 使用。
  setState: defaultNavState.setState,
  getState: defaultNavState.getState,
  clearState: defaultNavState.clearState,
  setDipRadarSector: defaultNavState.setDipRadarSector,
  getDipRadarSector: defaultNavState.getDipRadarSector,
  clearDipRadarSector: defaultNavState.clearDipRadarSector,
  setDiscoveryFocusSectors: defaultNavState.setDiscoveryFocusSectors,
  getDiscoveryFocusSectors: defaultNavState.getDiscoveryFocusSectors,
  clearDiscoveryFocusSectors: defaultNavState.clearDiscoveryFocusSectors,
  setDiscoveryScanMode: defaultNavState.setDiscoveryScanMode,
  getDiscoveryScanMode: defaultNavState.getDiscoveryScanMode,
  clearDiscoveryScanMode: defaultNavState.clearDiscoveryScanMode,
  setSubView: defaultNavState.setSubView,
  getSubView: defaultNavState.getSubView,
  clearSubView: defaultNavState.clearSubView,
};
