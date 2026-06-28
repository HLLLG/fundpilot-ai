// pages/market/market.js
// 市场页（Market_Page）
//
// 三个子视图：主题板块 (boards) / 大跌雷达 (dip) / 美股 (us)。
// 子视图在 onShow 时从 nav-state 恢复，并通过 navState.setSubView('market', v) 持久化（Req 3.5）。
//
// 本任务（5.1）实现主题板块子视图（全功能），大跌雷达与美股保留占位桩（任务 5.4/5.5 实现）。
//
// 主题板块功能（Req 10.1–10.8 / 3.5）：
//   - GET /api/market/theme-boards 拉取列表，缓存优先（cache:theme:{sort}）
//   - 每条板块：名称/类型标签/日涨跌幅/5日涨跌/主力净流入/持仓标签/资金流四档（Req 10.2/10.4/10.5）
//   - sort 切换 change/inflow（Req 10.3）
//   - 展开/折叠资金流四档明细（Req 10.4）
//   - 「看大跌基金」：写 navState.setDipRadarSector + 切子视图 dip（Req 10.6）
//   - 「加入关注方向」：derive.addFocusSector + 写 navState.setDiscoveryFocusSectors + toast（Req 10.7）
//   - refreshed_at 展示（Req 10.8）
//   - state-view 三态（Req 4.1/4.2）

var api = require("../../utils/api");
var derive = require("../../utils/derive");
var cache = require("../../utils/cache");
var navState = require("../../utils/nav-state");

// 子视图定义
var SUB_VIEWS = [
  { key: "boards", label: "主题板块" },
  { key: "dip", label: "大跌雷达" },
  { key: "us", label: "美股" },
];

// 排序选项
var SORT_OPTIONS = [
  { label: "涨跌", value: "change" },
  { label: "主力", value: "inflow" },
];

// 板块类型标签映射
var BOARD_KIND_LABELS = {
  industry: "行业",
  concept: "概念",
  index: "指数",
};

// ── 美股：美东交易时段 session_kind → 中文标签映射（Req 12.5）──────────
var SESSION_KIND_LABELS = {
  pre_market: "盘前",
  market_open: "盘中",
  regular: "盘中",
  after_hours: "盘后",
  closed: "休市",
  weekend: "休市",
  holiday: "休市",
};

// ── 美股：期货品种 symbol → 显示名称 ──────────────────────────────────
var FUTURES_NAMES = {
  nasdaq: "纳指", NQ: "纳指",
  sp500: "标普",  ES: "标普",
  dow: "道指",    YM: "道指",
};

/** ISO 时间字符串 → HH:MM（用于美股更新时间展示，Req 12.5）*/
function _formatUsTime(isoStr) {
  if (!isoStr) return "";
  try {
    var str = String(isoStr).replace("T", " ").replace("Z", "").split(".")[0];
    var parts = str.split(" ");
    return parts.length >= 2 ? parts[1].substring(0, 5) : String(isoStr);
  } catch (_) {
    return String(isoStr);
  }
}

// 查找子视图下标（不依赖 Array.prototype.findIndex，兼容更多运行时）
function findSubViewIndex(key) {
  for (var i = 0; i < SUB_VIEWS.length; i++) {
    if (SUB_VIEWS[i].key === key) return i;
  }
  return -1;
}

Page({
  data: {
    // 子视图
    subViews: SUB_VIEWS,
    subView: "boards",     // 'boards' | 'dip' | 'us'
    subViewIndex: 0,

    // --- 主题板块 (boards) ---
    sortOptions: SORT_OPTIONS,
    sortIndex: 0,          // 当前排序索引（对应 SORT_OPTIONS）
    sortValue: "change",   // 当前排序值

    boardItems: [],        // 处理后的板块列表（含 kindLabel / expandedFlow 等）
    refreshedAt: "",       // 数据更新时间（Req 10.8）
    boardsLoading: true,
    boardsError: "",
    boardsEmpty: false,

    // ─── 大跌雷达 (dip)（Req 11）─────────────────────────────────
    dipLookbackDays: 5,
    dipList: [],
    dipLoading: false,
    dipError: "",
    dipUnavailable: false,
    dipMessage: "",

    // ─── 美股概览 (us)（Req 12）──────────────────────────────────
    usLoading: false,
    usError: "",
    usOverview: null,         // 原始响应（加载完毕后非空）
    usAvailable: false,       // 整体数据源可用性
    usFutures: [],            // 期货列表（derive.mapUsQuoteDisplay 处理后，Req 12.2）
    usUsdCny: null,           // USD/CNY（mapUsQuoteDisplay 结果，Req 12.3）
    usQdii: [],               // QDII 盘前参考列表（Req 12.4）
    usSessionLabel: "—",      // 美东时段标签（盘前/盘中/盘后/休市，Req 12.5）
    usUpdatedAt: "",          // 更新时间文字（Req 12.5）
  },

  onLoad: function () {
    // 恢复上次子视图（Req 3.5）
    var savedView = navState.getSubView("market");
    if (savedView && ["boards", "dip", "us"].indexOf(savedView) !== -1) {
      var idx = findSubViewIndex(savedView);
      this.setData({ subView: savedView, subViewIndex: idx >= 0 ? idx : 0 });
    }
  },

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    // 检查是否有通过 navState 预填的子视图（如「看大跌基金」跳转后，再回到 market，恢复 dip 视图）
    var savedView = navState.getSubView("market");
    if (savedView && savedView !== this.data.subView) {
      var idx = findSubViewIndex(savedView);
      this.setData({ subView: savedView, subViewIndex: idx >= 0 ? idx : 0 });
    }

    // 根据当前子视图加载数据
    this._loadCurrentSubView();
  },

  // ---------------------------------------------------------------------------
  // 子视图切换
  // ---------------------------------------------------------------------------

  /** 点击子视图 Tab（主题板块/大跌雷达/美股） */
  onSubViewTap: function (e) {
    var key = e.currentTarget.dataset.key;
    var idx = findSubViewIndex(key);
    if (idx === -1 || key === this.data.subView) return;
    this.setData({ subView: key, subViewIndex: idx });
    navState.setSubView("market", key);
    this._loadCurrentSubView();
  },

  /** 根据当前子视图加载对应数据 */
  _loadCurrentSubView: function () {
    var subView = this.data.subView;
    if (subView === "boards") {
      this._loadThemeBoards(this.data.sortValue);
    } else if (subView === "dip") {
      // 大跌雷达将在任务 5.4 实现，此处为占位
    } else if (subView === "us") {
      // 只在尚未加载或明确重试时触发（Req 12.1）
      if (!this.data.usOverview && !this.data.usLoading) {
        this._loadUsOverview();
      }
    }
  },

  // ---------------------------------------------------------------------------
  // 主题板块数据加载（Req 10.1）
  // ---------------------------------------------------------------------------

  /**
   * 拉取主题板块列表，缓存优先（Req 4.3）。
   * @param {string} sort  'change' | 'inflow'（Req 10.3）
   */
  _loadThemeBoards: function (sort) {
    var self = this;
    var sortVal = sort || "change";

    this.setData({ boardsLoading: true, boardsError: "", boardsEmpty: false });

    // 缓存优先即时渲染（Req 4.3，design.md cache:theme:{sort}）
    var cacheKey = cache.KEYS.theme(sortVal);
    var cached = cache.get(cacheKey);
    if (cached) {
      self._applyBoardsData(cached);
    }

    api
      .fetchMarketThemeBoards({ sort: sortVal })
      .then(function (data) {
        cache.set(cacheKey, data);
        self._applyBoardsData(data);
        self.setData({ boardsLoading: false, boardsError: "" });
      })
      .catch(function (err) {
        self.setData({
          boardsLoading: false,
          boardsError: (err && err.message) || "加载失败，请稍后重试",
        });
      });
  },

  /**
   * 把后端响应数据转换为页面可渲染的 boardItems。
   * 每条附加：kindLabel（行业/概念/指数）、expandedFlow（是否展开资金流）。
   * @param {object} data  MarketThemeBoardResponse
   */
  _applyBoardsData: function (data) {
    if (!data) return;

    var items = Array.isArray(data.items) ? data.items : [];
    var processedItems = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item) continue;
      processedItems.push({
        // 原始字段（Req 10.2）
        sector_label: item.sector_label || "",
        board_kind: item.board_kind || "",
        change_1d_percent: item.change_1d_percent != null ? item.change_1d_percent : null,
        change_5d_percent: item.change_5d_percent != null ? item.change_5d_percent : null,
        main_force_net_yi: item.main_force_net_yi != null ? item.main_force_net_yi : null,
        flow_tiers: item.flow_tiers || null,   // 资金流四档明细（Req 10.4）
        in_portfolio: !!item.in_portfolio,     // 持仓标签（Req 10.5）
        // 派生字段
        kindLabel: BOARD_KIND_LABELS[item.board_kind] || item.board_kind || "",
        expandedFlow: false,                   // 资金流展开状态
        _idx: i,                               // 列表下标（bindtap dataset 用）
      });
    }

    var isEmpty = processedItems.length === 0;
    var refreshedAt = data.refreshed_at || "";

    this.setData({
      boardItems: processedItems,
      refreshedAt: refreshedAt,
      boardsEmpty: isEmpty,
    });
  },

  // ---------------------------------------------------------------------------
  // 排序切换（Req 10.3）
  // ---------------------------------------------------------------------------

  /** 点击排序 Tab：change / inflow */
  onSortTap: function (e) {
    var idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.sortIndex) return;
    var sortVal = SORT_OPTIONS[idx] ? SORT_OPTIONS[idx].value : "change";
    this.setData({ sortIndex: idx, sortValue: sortVal });
    this._loadThemeBoards(sortVal);
  },

  // ---------------------------------------------------------------------------
  // 资金流四档明细展开/折叠（Req 10.4）
  // ---------------------------------------------------------------------------

  /** 展开/折叠某条板块的资金流明细 */
  onToggleFlow: function (e) {
    var idx = Number(e.currentTarget.dataset.idx);
    var items = this.data.boardItems.slice();
    if (!items[idx]) return;
    var updated = {};
    updated["boardItems[" + idx + "].expandedFlow"] = !items[idx].expandedFlow;
    this.setData(updated);
  },

  // ---------------------------------------------------------------------------
  // 「看大跌基金」（Req 10.6）
  // ---------------------------------------------------------------------------

  /**
   * 点击「看大跌基金」：写 navState.dipRadarSector + 切换到大跌雷达子视图。
   * 任务 5.4 实现大跌雷达后，此处跳转将触发对应数据加载。
   */
  onViewDipFunds: function (e) {
    var sectorLabel = e.currentTarget.dataset.sector;
    // 写入预填 sector，大跌雷达子视图用于按板块筛选（Req 10.6）
    navState.setDipRadarSector(sectorLabel || "");
    // 切换到大跌雷达子视图
    var dipIdx = findSubViewIndex("dip");
    this.setData({ subView: "dip", subViewIndex: dipIdx >= 0 ? dipIdx : 1 });
    navState.setSubView("market", "dip");
    // 大跌雷达加载（任务 5.4 接管）
    this._loadCurrentSubView();
  },

  // ---------------------------------------------------------------------------
  // 「加入关注方向」（Req 10.7）
  // ---------------------------------------------------------------------------

  /**
   * 点击「加入关注方向」：
   * 1. 读取当前 discoveryFocusSectors（≤3）
   * 2. derive.addFocusSector 加入新板块（去重、上限 3）
   * 3. 写回 navState.setDiscoveryFocusSectors
   * 4. 展示 toast
   */
  onAddFocusSector: function (e) {
    var sectorLabel = e.currentTarget.dataset.sector;
    if (!sectorLabel) return;

    var current = navState.getDiscoveryFocusSectors();
    var next = derive.addFocusSector(current, sectorLabel);
    navState.setDiscoveryFocusSectors(next);

    // 判断是否实际添加了（长度增加 = 成功）
    if (next.length > current.length) {
      wx.showToast({
        title: "已加入关注方向",
        icon: "success",
        duration: 1500,
      });
    } else if (next.indexOf(sectorLabel) !== -1) {
      wx.showToast({
        title: "该方向已在关注中",
        icon: "none",
        duration: 1500,
      });
    } else {
      wx.showToast({
        title: "关注方向最多3个",
        icon: "none",
        duration: 1500,
      });
    }
  },

  // ---------------------------------------------------------------------------
  // 错误重试
  // ---------------------------------------------------------------------------

  onBoardsRetry: function () {
    this._loadThemeBoards(this.data.sortValue);
  },

  onDipRetry: function () {
    // 大跌雷达将在任务 5.4 实现
    this.setData({ dipError: "" });
  },

  // ---------------------------------------------------------------------------
  // 美股概览（Req 12.1–12.6）
  // ---------------------------------------------------------------------------

  /**
   * 加载美股概览快照（Req 12.1）。
   * 使用 derive.mapUsQuoteDisplay 处理 unavailable 状态（Req 12.6）。
   */
  _loadUsOverview: function () {
    var self = this;
    this.setData({ usLoading: true, usError: "" });

    api.fetchUsMarketOverview()
      .then(function (data) {
        self._applyUsOverview(data);
      })
      .catch(function (err) {
        self.setData({
          usLoading: false,
          usError: (err && err.message) || "加载失败，请稍后重试",
        });
      });
  },

  /**
   * 将美股概览 API 响应（UsMarketSnapshot）映射到页面数据。
   * @param {object} data  UsMarketSnapshot
   */
  _applyUsOverview: function (data) {
    if (!data) {
      this.setData({ usLoading: false, usError: "数据为空" });
      return;
    }

    var available = data.available !== false;

    // ── 期货（纳指/标普/道指，Req 12.2）────────────────────────────────────
    var rawFutures = Array.isArray(data.futures) ? data.futures : [];
    var usFutures = rawFutures.map(function (item) {
      var mapped = derive.mapUsQuoteDisplay(item);
      var sym = (item.symbol || "").toLowerCase();
      var name = FUTURES_NAMES[sym] || FUTURES_NAMES[item.symbol] || item.name || item.symbol || "—";
      return Object.assign({}, mapped, { symbol: item.symbol || "", name: name });
    });

    // ── USD/CNY 汇率（Req 12.3）──────────────────────────────────────────────
    var rawUsdCny = data.usd_cny || null;
    var usUsdCny = rawUsdCny
      ? Object.assign({}, derive.mapUsQuoteDisplay(rawUsdCny), { label: "USD/CNY" })
      : null;

    // ── QDII 盘前参考（Req 12.4）────────────────────────────────────────────
    var rawQdii = Array.isArray(data.qdii) ? data.qdii : [];
    var usQdii = rawQdii.map(function (item) {
      var mapped = derive.mapUsQuoteDisplay(item);
      return Object.assign({}, mapped, {
        name: item.name || item.fund_name || item.symbol || "—",
        symbol: item.symbol || "",
      });
    });

    // ── 美东时段标签与更新时间（Req 12.5）───────────────────────────────────
    var sessionKind = data.session_kind || "";
    var usSessionLabel = SESSION_KIND_LABELS[sessionKind] || (sessionKind ? sessionKind : "—");
    var usUpdatedAt = _formatUsTime(data.updated_at || data.refreshed_at || "");

    this.setData({
      usLoading: false,
      usError: "",
      usOverview: data,
      usAvailable: available,
      usFutures: usFutures,
      usUsdCny: usUsdCny,
      usQdii: usQdii,
      usSessionLabel: usSessionLabel,
      usUpdatedAt: usUpdatedAt,
    });
  },

  onUsRetry: function () {
    this.setData({ usOverview: null });
    this._loadUsOverview();
  },

  onUsRefresh: function () {
    this.setData({ usOverview: null });
    this._loadUsOverview();
  },
});
