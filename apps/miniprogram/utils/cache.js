// utils/cache.js
// 缓存优先读写封装（Cache_Layer）。
//
// 封装 wx.setStorageSync / wx.getStorageSync，存储后端可注入，以便在 Node 环境
// （无 wx.*）下做属性测试。页面在 onLoad/onShow 先读缓存即时渲染，再后台请求替换。
//
// Design: miniprogram-web-parity / Property 6（缓存往返）：
//   for any (key, data)，cache.set(key, data) 后 cache.get(key) 应深度相等于 data；
//   未写入键的读取返回空值（null）而非抛错。
//
// 设计中约定的缓存键（见 design.md 本地缓存模型）：
//   - cache:holdings                 持仓看板（Req 5.2）
//   - cache:dashboard:{range}        盈亏分析（Req 4.3）
//   - cache:theme:{sort}             主题板块（Req 4.3）
//   - cache:dip:{lookback}           大跌雷达（Req 4.3）
//   - cache:us                       美股概览（Req 4.3）

const CACHE_PREFIX = "cache:";

// 缓存键构造器：集中管理键命名，避免页面层各自拼字符串造成口径漂移。
const KEYS = {
  holdings: CACHE_PREFIX + "holdings",
  us: CACHE_PREFIX + "us",
  dashboard(range) {
    return CACHE_PREFIX + "dashboard:" + String(range);
  },
  theme(sort) {
    return CACHE_PREFIX + "theme:" + String(sort);
  },
  dip(lookback) {
    return CACHE_PREFIX + "dip:" + String(lookback);
  },
};

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

// 通过 JSON 序列化保证 round-trip 深度相等，并隔离引用别名
// （写入后再改原对象不影响已存值）。
function encode(value) {
  return JSON.stringify(value === undefined ? null : value);
}

function decode(raw) {
  // 未写入键的读取返回空值（null）而非抛错（Property 6）。
  if (raw === undefined || raw === null || raw === "") {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

// 工厂：基于注入的存储后端创建一个 cache 实例。
function createCache(storage) {
  const backend = storage || createWxStorage();

  // 写入缓存（Property 6 的核心：与 get 构成 round-trip）。
  function set(key, data) {
    backend.set(key, encode(data));
  }

  // 读取缓存，命中返回深度相等的数据；未写入/异常返回 null（不抛错）。
  function get(key) {
    return decode(backend.get(key));
  }

  // 是否存在已写入的缓存值。
  function has(key) {
    const raw = backend.get(key);
    return raw !== undefined && raw !== null && raw !== "";
  }

  // 移除单个缓存键。
  function remove(key) {
    backend.remove(key);
  }

  return {
    set,
    get,
    has,
    remove,
  };
}

// 默认实例（运行时使用 wx 存储后端）。
const defaultCache = createCache();

module.exports = {
  KEYS,
  CACHE_PREFIX,
  createWxStorage,
  createMemoryStorage,
  createCache,
  // 默认实例方法直接导出，页面层可直接 require 使用。
  set: defaultCache.set,
  get: defaultCache.get,
  has: defaultCache.has,
  remove: defaultCache.remove,
};
