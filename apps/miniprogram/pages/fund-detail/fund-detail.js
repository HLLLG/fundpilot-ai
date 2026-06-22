// pages/fund-detail/fund-detail.js
// 基金详情页：展示 Req 8.1 全部持仓字段，并渲染关联板块分时图（Req 8.2）。
// Task 2.6 扩展：业绩对比（Req 8.3）与历史净值分页（Req 8.4）。
// Task 2.8 扩展：首次购入日选择与持有天数重算（Req 8.5, 8.6）。
//
// 数据流：
//   1. onLoad 从缓存（cache:holdings）取持仓列表与 index，免闪屏。
//   2. loadDetail 调 POST /api/holdings/detail 获取完整 HoldingDetail 并
//      并行调 GET /api/sector-quotes/intraday 拉取分时数据。
//   3. derive.buildIntradayChartOption 生成 echarts option 绑定到 chart-line。
//   4. loadPerformance 调 GET /api/market/index-daily（沪深300），结合 profit_trend
//      生成 buildLineChartOption 的两线对比图。
//   5. loadNavPage 调 GET /api/fund-profiles/{code}/nav-history/page 分页加载，
//      onReachBottom 时追加并用 mergeNavPages 合并去重排序。
//   6. onFirstPurchaseDateChange 调 PATCH /api/fund-profiles/{code} 写入
//      first_purchase_date，成功后重算持有天数。
//
// Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6

var api = require("../../utils/api");
var cache = require("../../utils/cache");
var derive = require("../../utils/derive");

// 区间配置：label（显示文案）→ days（请求天数）
var PERF_RANGES = [
  { label: "近1月",  days: 30  },
  { label: "近3月",  days: 90  },
  { label: "近6月",  days: 180 },
  { label: "近1年",  days: 365 },
  { label: "近3年",  days: 1095 },
];

Page({
  data: {
    // ── 持仓明细（HoldingDetail 响应，含嵌套 holding 字段） ──────────────
    detail: null,

    // ── 板块分时图（Req 8.2） ────────────────────────────────────────────
    intradayOption: null,
    hasIntraday: false,

    // ── 全局加载/错误状态 ────────────────────────────────────────────────
    loading: true,
    error: "",
    // 分时数据独立加载状态（不阻塞主卡片渲染）
    chartLoading: false,
    chartError: "",

    // ── 业绩对比（Req 8.3） ──────────────────────────────────────────────
    perfRanges: PERF_RANGES,
    perfRangeIndex: 0,          // 当前选中的区间下标
    perfOption: null,           // echarts option（两线对比）
    perfLoading: false,
    perfError: "",

    // ── 历史净值（Req 8.4） ──────────────────────────────────────────────
    navList: [],                // 已合并的净值列表（mergeNavPages 结果）
    navCursor: null,            // 下一页游标（before_date），null 表示已到底
    navHasMore: false,          // 是否还有更多
    navLoading: false,
    navLoadingMore: false,
    navError: "",

    // ── 首次购入日（Req 8.5, 8.6） ───────────────────────────────────────
    purchaseDateSaving: false,  // PATCH 请求进行中
  },

  onLoad: function (query) {
    var index = Number(query.index || 0);
    this.index = index;
    // 用于游标分页的原始页数组（传给 mergeNavPages）。
    this._navPages = [];

    // 尝试从缓存取持仓列表，供 fetchHoldingDetail 使用。
    var cached = cache.get(cache.KEYS.holdings);
    this._cachedHoldings = (cached && cached.holdings) || null;

    this.loadDetail();
  },

  onShow: function () {
    // 若已有数据且页面重新可见，不重复加载
  },

  // 滚动到底部时加载下一页净值（Req 8.4）
  onReachBottom: function () {
    if (this.data.navHasMore && !this.data.navLoadingMore && !this.data.navLoading) {
      this._loadNavMore();
    }
  },

  // -------------------------------------------------------------------------
  // 主数据加载
  // -------------------------------------------------------------------------

  loadDetail: function () {
    var self = this;
    this.setData({ loading: true, error: "", chartLoading: true, chartError: "" });

    // 优先使用缓存持仓，如无则回退到接口取。
    var holdingsPromise = this._cachedHoldings
      ? Promise.resolve(this._cachedHoldings)
      : api.fetchHoldings().then(function (data) {
          return (data && data.holdings) || [];
        });

    holdingsPromise
      .then(function (holdings) {
        self._holdings = holdings;
        return api.fetchHoldingDetail(holdings, self.index);
      })
      .then(function (detail) {
        self.setData({ detail: detail, loading: false, error: "" });
        // 并行加载分时图（有 sector_name/source 信息后才能请求）。
        self._loadIntraday(detail);
        // 加载业绩对比（默认近1月）。
        self._loadPerformance(detail, 0);
        // 加载历史净值首页。
        var fundCode = detail && detail.holding && detail.holding.fund_code;
        if (fundCode) {
          self._fundCode = fundCode;
          self._loadNavFirst(fundCode);
        }
      })
      .catch(function (err) {
        self.setData({
          loading: false,
          error: (err && err.message) || "加载失败，请稍后重试",
        });
      });
  },

  // -------------------------------------------------------------------------
  // 板块分时图（Req 8.2）
  // -------------------------------------------------------------------------

  _loadIntraday: function (detail) {
    var self = this;
    var holding = detail && detail.holding;
    var sourceType = holding && (holding.sector_source_type || holding.source_type);
    var sourceName = holding && (holding.sector_source_name || holding.source_name || holding.sector_name);

    if (!sourceName) {
      this.setData({ chartLoading: false, hasIntraday: false });
      return;
    }

    api
      .fetchSectorIntraday({ source_type: sourceType || "sector", source_name: sourceName })
      .then(function (intradayData) {
        var points = [];
        if (Array.isArray(intradayData)) {
          points = intradayData;
        } else if (intradayData && Array.isArray(intradayData.points)) {
          points = intradayData.points;
        } else if (intradayData && Array.isArray(intradayData.data)) {
          points = intradayData.data;
        }

        if (points.length === 0) {
          self.setData({ chartLoading: false, hasIntraday: false });
          return;
        }

        var option = derive.buildIntradayChartOption(points, {
          yField: "change_percent",
          seriesName: sourceName,
        });

        self.setData({
          intradayOption: option,
          hasIntraday: true,
          chartLoading: false,
          chartError: "",
        });
      })
      .catch(function (err) {
        self.setData({
          chartLoading: false,
          hasIntraday: false,
          chartError: (err && err.message) || "分时图加载失败",
        });
      });
  },

  // -------------------------------------------------------------------------
  // 业绩对比（Req 8.3）
  // -------------------------------------------------------------------------

  /**
   * 加载指定区间的业绩对比数据。
   * @param {object} detail  HoldingDetail 响应（含 profit_trend 等）
   * @param {number} rangeIndex  PERF_RANGES 中的下标
   */
  _loadPerformance: function (detail, rangeIndex) {
    var self = this;
    var range = PERF_RANGES[rangeIndex] || PERF_RANGES[0];
    var days = range.days;

    this.setData({ perfLoading: true, perfError: "", perfRangeIndex: rangeIndex });

    // 并行拉取：沪深300 历史（index-daily）
    api
      .fetchIndexDailyHistory("000300", days)
      .then(function (indexResp) {
        // indexResp 结构：{ points: [{date, change_percent, ...}] } 或直接数组
        var indexPoints = [];
        if (Array.isArray(indexResp)) {
          indexPoints = indexResp;
        } else if (indexResp && Array.isArray(indexResp.points)) {
          indexPoints = indexResp.points;
        } else if (indexResp && Array.isArray(indexResp.data)) {
          indexPoints = indexResp.data;
        }

        // 本基金区间涨跌：优先使用 detail 中的 profit_trend，回退到 index_percent 为 null
        // profit_trend 结构：{ points: [{date, portfolio_percent, index_percent}] }
        var fundPoints = [];
        if (detail && detail.profit_trend && Array.isArray(detail.profit_trend.points)) {
          fundPoints = detail.profit_trend.points;
        }

        // 合并：以沪深300的日期为基准，构造 buildLineChartOption 需要的 points 格式
        // buildLineChartOption 期望 { time/date, portfolio_percent, index_percent }
        var mergedPoints = self._mergePerformancePoints(indexPoints, fundPoints);

        var option = derive.buildLineChartOption(mergedPoints);
        // 给系列改更语义化的名字
        if (option && option.series && option.series.length >= 2) {
          option.series[0].name = "本基金";
          option.series[1].name = "沪深300";
        }

        self.setData({
          perfOption: option,
          perfLoading: false,
          perfError: "",
        });
      })
      .catch(function (err) {
        self.setData({
          perfLoading: false,
          perfError: (err && err.message) || "业绩对比加载失败",
        });
      });
  },

  /**
   * 把 indexPoints（沪深300）和 fundPoints（本基金 profit_trend）合并为
   * buildLineChartOption 格式的 points 数组。
   * @param {Array} indexPoints  [{date, change_percent}]
   * @param {Array} fundPoints   [{date, portfolio_percent}]
   * @returns {Array} [{date, portfolio_percent, index_percent}]
   */
  _mergePerformancePoints: function (indexPoints, fundPoints) {
    // 用 date 建立本基金数据的 lookup
    var fundMap = Object.create(null);
    for (var i = 0; i < fundPoints.length; i++) {
      var fp = fundPoints[i];
      if (fp && fp.date) {
        fundMap[fp.date] = fp.portfolio_percent != null ? fp.portfolio_percent : null;
      }
    }

    return indexPoints.map(function (ip) {
      var d = ip.date || ip.time || "";
      return {
        date: d,
        // buildLineChartOption 读 portfolio_percent
        portfolio_percent: fundMap[d] != null ? fundMap[d] : null,
        // buildLineChartOption 读 index_percent；沪深300 的涨跌幅字段
        index_percent: ip.change_percent != null ? ip.change_percent : (ip.index_percent != null ? ip.index_percent : null),
      };
    });
  },

  // -------------------------------------------------------------------------
  // 历史净值分页（Req 8.4）
  // -------------------------------------------------------------------------

  _loadNavFirst: function (fundCode) {
    var self = this;
    this._navPages = [];
    this.setData({ navLoading: true, navError: "", navList: [], navCursor: null, navHasMore: false });

    api
      .fetchFundNavHistoryPage(fundCode, { limit: 30 })
      .then(function (resp) {
        self._handleNavResponse(resp, false);
      })
      .catch(function (err) {
        self.setData({
          navLoading: false,
          navError: (err && err.message) || "净值历史加载失败",
        });
      });
  },

  _loadNavMore: function () {
    var self = this;
    var cursor = this.data.navCursor;
    var fundCode = this._fundCode;
    if (!fundCode || !cursor) return;

    this.setData({ navLoadingMore: true });

    api
      .fetchFundNavHistoryPage(fundCode, { limit: 30, before: cursor })
      .then(function (resp) {
        self._handleNavResponse(resp, true);
      })
      .catch(function (err) {
        self.setData({
          navLoadingMore: false,
          navError: (err && err.message) || "加载更多失败",
        });
      });
  },

  /**
   * 处理 nav-history/page 响应，合并去重排序。
   * 响应结构预期：{ items: [...], next_cursor?: string } 或直接数组
   * @param {object|Array} resp
   * @param {boolean} isLoadMore
   */
  _handleNavResponse: function (resp, isLoadMore) {
    var items = [];
    var nextCursor = null;

    if (Array.isArray(resp)) {
      items = resp;
    } else if (resp && Array.isArray(resp.items)) {
      items = resp.items;
      nextCursor = resp.next_cursor || resp.cursor || null;
    } else if (resp && Array.isArray(resp.data)) {
      items = resp.data;
      nextCursor = resp.next_cursor || resp.cursor || null;
    }

    // 把新页加入 pages 数组，再用 mergeNavPages 合并去重排序
    this._navPages.push(items);
    var merged = derive.mergeNavPages(this._navPages);

    this.setData({
      navList: merged,
      navCursor: nextCursor || null,
      navHasMore: !!(nextCursor),
      navLoading: false,
      navLoadingMore: false,
      navError: "",
    });
  },

  // -------------------------------------------------------------------------
  // 交互事件
  // -------------------------------------------------------------------------

  onRetry: function () {
    this.loadDetail();
  },

  onChartRetry: function () {
    if (this.data.detail) {
      this.setData({ chartLoading: true, chartError: "" });
      this._loadIntraday(this.data.detail);
    }
  },

  onPerfRetry: function () {
    if (this.data.detail) {
      this._loadPerformance(this.data.detail, this.data.perfRangeIndex);
    }
  },

  /** 用户点击区间 tab */
  onPerfRangeTap: function (e) {
    var idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.perfRangeIndex) return;
    if (this.data.detail) {
      this._loadPerformance(this.data.detail, idx);
    }
  },

  /** 用户点击「加载更多」按钮（兜底，主要靠 onReachBottom） */
  onNavLoadMore: function () {
    if (this.data.navHasMore && !this.data.navLoadingMore && !this.data.navLoading) {
      this._loadNavMore();
    }
  },

  onNavRetry: function () {
    if (this._fundCode) {
      this._loadNavFirst(this._fundCode);
    }
  },

  // -------------------------------------------------------------------------
  // 首次购入日（Req 8.5, 8.6）
  // -------------------------------------------------------------------------

  /**
   * 滚轮日期选择器 change 事件。
   * picker value 为 'YYYY-MM-DD' 字符串。
   * 调用 PATCH /api/fund-profiles/{code} 写入 first_purchase_date，
   * 成功后更新 detail.holding.first_purchase_date 与重算持有天数。
   *
   * Requirements: 8.5, 8.6
   */
  onFirstPurchaseDateChange: function (e) {
    var self = this;
    var dateStr = e.detail && e.detail.value;
    if (!dateStr) return;

    var fundCode = this._fundCode || (this.data.detail && this.data.detail.holding && this.data.detail.holding.fund_code);
    if (!fundCode) return;

    this.setData({ purchaseDateSaving: true });

    api.updateFundProfilePurchaseDate(fundCode, dateStr)
      .then(function () {
        // 重算持有天数：今天减去首次购入日
        var today = new Date();
        var parts = dateStr.split("-");
        var purchaseDate = new Date(
          Number(parts[0]),
          Number(parts[1]) - 1,
          Number(parts[2])
        );
        var diffMs = today.getTime() - purchaseDate.getTime();
        var holdingDays = Math.max(0, Math.floor(diffMs / (1000 * 60 * 60 * 24)));

        // 深度更新 detail 中的两个字段
        self.setData({
          "detail.holding.first_purchase_date": dateStr,
          "detail.holding_days": holdingDays,
          purchaseDateSaving: false,
        });

        wx.showToast({ title: "购入日已更新", icon: "success", duration: 1500 });
      })
      .catch(function (err) {
        self.setData({ purchaseDateSaving: false });
        wx.showToast({
          title: (err && err.message) || "更新失败，请重试",
          icon: "none",
          duration: 2000,
        });
      });
  },
});
