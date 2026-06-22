// pages/fund-edit/fund-edit.js
// 手动新增持仓 / 改代码 / 改名称 / 改金额（含东财搜索查码）
//
// Requirements:
//   7.1 输入基金名称关键字 → GET /api/funds/search 展示候选
//   7.2 选择搜索结果 → 自动回填代码与名称
//   7.3 提交新增/修改金额 → POST /api/portfolio/apply-holdings 持久化
//   7.4 修改基金档案代码/名称 → PATCH /api/fund-profiles/{code} 持久化
//   7.5 代码无法解析时展示错误并保留输入
//
// 页面参数（wx.navigateTo options.query）：
//   fund_code  — 当前持仓代码（有则为编辑模式）
//   fund_name  — 当前持仓名称
//   index      — 持仓列表下标（用于回传位置）
//
// 状态：
//   idle      — 空白/可编辑
//   searching — 搜索结果加载中
//   saving    — 保存中
//   error     — 保存/代码解析失败（保留输入）

var api = require("../../utils/api");
var cache = require("../../utils/cache");

// 搜索防抖延迟（ms）
var SEARCH_DEBOUNCE = 400;

Page({
  data: {
    // ── 编辑模式标志 ───────────────────────────────────────────────
    isEditMode: false,       // true = 编辑已有持仓；false = 新增
    originalCode: "",        // 编辑模式下的原始代码（用于 PATCH 端点路径）
    holdingIndex: -1,        // 编辑模式下的持仓列表下标

    // ── 表单字段 ───────────────────────────────────────────────────
    fundCode: "",            // 基金代码
    fundName: "",            // 基金名称
    amount: "",              // 持有金额（元）

    // ── 搜索（Req 7.1） ────────────────────────────────────────────
    searchQuery: "",         // 搜索输入框文本
    searchResults: [],       // 候选基金列表 [{ fund_code, fund_name }]
    showSearchDropdown: false,// 是否展示搜索下拉
    searching: false,        // 搜索请求进行中

    // ── 状态 ───────────────────────────────────────────────────────
    phase: "idle",           // idle | saving | error
    errorMsg: "",            // 错误信息（Req 7.5）
  },

  // 防抖定时器句柄
  _searchTimer: null,

  // ─────────────────────────────────────────────────────────────────
  // 生命周期
  // ─────────────────────────────────────────────────────────────────

  onLoad: function (options) {
    // 登录守卫
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    var fundCode = options.fund_code || "";
    var fundName = options.fund_name || "";
    var idx = options.index != null ? parseInt(options.index, 10) : -1;
    var isEdit = !!fundCode;

    this.setData({
      isEditMode: isEdit,
      originalCode: fundCode,
      holdingIndex: isNaN(idx) ? -1 : idx,
      fundCode: fundCode,
      fundName: fundName,
    });

    wx.setNavigationBarTitle({
      title: isEdit ? "编辑持仓" : "新增持仓",
    });
  },

  onUnload: function () {
    // 清理防抖定时器
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
  },

  // ─────────────────────────────────────────────────────────────────
  // 搜索输入与防抖（Req 7.1）
  // ─────────────────────────────────────────────────────────────────

  onSearchInput: function (e) {
    var query = e.detail.value || "";
    this.setData({ searchQuery: query });

    // 清除上一轮防抖
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }

    // 输入为空时收起下拉
    if (!query.trim()) {
      this.setData({
        searchResults: [],
        showSearchDropdown: false,
        searching: false,
      });
      return;
    }

    // 防抖后触发搜索
    var self = this;
    this._searchTimer = setTimeout(function () {
      self._doSearch(query.trim());
    }, SEARCH_DEBOUNCE);
  },

  _doSearch: function (query) {
    var self = this;
    this.setData({ searching: true, showSearchDropdown: true });

    api.searchFunds(query, 10)
      .then(function (items) {
        // searchFunds 已返回 items 数组（api.js 中解包了 body.items）
        self.setData({
          searchResults: Array.isArray(items) ? items : [],
          searching: false,
          showSearchDropdown: true,
        });
      })
      .catch(function () {
        // 搜索失败静默处理，不展示错误（不阻断用户手动输入）
        self.setData({
          searchResults: [],
          searching: false,
          showSearchDropdown: false,
        });
      });
  },

  // 搜索框失焦时延迟收起下拉（给 tap 事件让路）
  onSearchBlur: function () {
    var self = this;
    setTimeout(function () {
      self.setData({ showSearchDropdown: false });
    }, 200);
  },

  // ─────────────────────────────────────────────────────────────────
  // 选择搜索结果（Req 7.2）
  // ─────────────────────────────────────────────────────────────────

  onSelectSearchResult: function (e) {
    var item = e.currentTarget.dataset.item;
    if (!item) return;

    this.setData({
      fundCode: item.fund_code || "",
      fundName: item.fund_name || "",
      searchQuery: item.fund_name || "",
      showSearchDropdown: false,
      searchResults: [],
      // 清除可能残留的错误提示
      errorMsg: "",
      phase: "idle",
    });
  },

  // ─────────────────────────────────────────────────────────────────
  // 表单字段输入事件
  // ─────────────────────────────────────────────────────────────────

  onFundCodeInput: function (e) {
    this.setData({
      fundCode: e.detail.value,
      errorMsg: "",
      phase: "idle",
    });
  },

  onFundNameInput: function (e) {
    this.setData({
      fundName: e.detail.value,
      errorMsg: "",
      phase: "idle",
    });
  },

  onAmountInput: function (e) {
    this.setData({ amount: e.detail.value });
  },

  // ─────────────────────────────────────────────────────────────────
  // 保存（Req 7.3 / 7.4 / 7.5）
  // ─────────────────────────────────────────────────────────────────

  onSave: function () {
    var self = this;

    var fundCode = (this.data.fundCode || "").trim();
    var fundName = (this.data.fundName || "").trim();
    var amount = (this.data.amount || "").trim();

    // 基本校验
    if (!fundCode) {
      wx.showToast({ title: "请填写基金代码", icon: "none" });
      return;
    }
    if (!fundName) {
      wx.showToast({ title: "请填写基金名称", icon: "none" });
      return;
    }
    var amountNum = parseFloat(amount);
    if (!amount || isNaN(amountNum) || amountNum < 0) {
      wx.showToast({ title: "请填写合法的持有金额", icon: "none" });
      return;
    }

    this.setData({ phase: "saving", errorMsg: "" });

    // ── Step 1: 如果是编辑模式且代码或名称变更，先 PATCH fund-profile（Req 7.4）
    var patchPromise = Promise.resolve();

    if (this.data.isEditMode) {
      var originalCode = this.data.originalCode;
      var codeChanged = fundCode !== originalCode;
      var nameChanged = fundName !== (this.data.fundName || "").trim();

      // 只要代码或名称有变化就发 PATCH
      if (codeChanged || nameChanged) {
        var patch = {};
        if (codeChanged) {
          patch.fund_code = fundCode;
        }
        // 始终同步 fund_name
        patch.fund_name = fundName;

        patchPromise = api.updateFundProfile(originalCode, patch);
      }
    }

    patchPromise
      .then(function () {
        // ── Step 2: POST /api/portfolio/apply-holdings（Req 7.3）
        var holdingsPayload = [
          {
            fund_code: fundCode,
            fund_name: fundName,
            holding_amount: amountNum,
          },
        ];
        return api.applyPortfolioHoldings(holdingsPayload);
      })
      .then(function (result) {
        // 成功：使缓存失效（下次进入持仓页会重新拉取）
        if (result) {
          cache.set(cache.KEYS.holdings, result);
        } else {
          cache.remove(cache.KEYS.holdings);
        }

        self.setData({ phase: "idle" });
        wx.showToast({
          title: self.data.isEditMode ? "修改成功" : "新增成功",
          icon: "success",
          duration: 1500,
        });
        setTimeout(function () {
          wx.navigateBack({ delta: 1 });
        }, 1600);
      })
      .catch(function (err) {
        // Req 7.5: 展示错误并保留用户输入
        var msg = (err && err.message) || "保存失败，请检查基金代码后重试";
        self.setData({
          phase: "error",
          errorMsg: msg,
        });
      });
  },

  // 清除错误后重试
  onDismissError: function () {
    this.setData({ phase: "idle", errorMsg: "" });
  },
});
