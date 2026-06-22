// pages/swing/swing.js
// 波段盯盘提醒页（Swing_Alerts）
//
// Requirements: 19.1, 19.2, 19.3, 19.4
//
// 功能：
//   - onLoad: GET /api/swing-alerts/today 拉取当日提醒（Req 19.2）
//   - 可选「重新评估」：POST /api/swing-alerts/evaluate 评估（Req 19.1）
//   - 展示每条提醒的类型/标题/内容/优先级（Req 19.3）
//   - 标记新提醒（Req 19.4）
//   - state-view 三态（Req 4.1/4.2）

var api = require("../../utils/api");

// 优先级中文标签映射
var PRIORITY_LABELS = {
  high: "高",
  medium: "中",
  low: "低",
};

// 优先级对应的徽标样式类
var PRIORITY_BADGE_CLASSES = {
  high: "badge--up",
  medium: "badge--warning",
  low: "badge--muted",
};

// 提醒类型中文映射（后端可能返回英文 key，做一层安全展示）
var ALERT_TYPE_LABELS = {
  take_profit: "止盈",
  add_position: "加仓",
  stop_loss: "止损",
  watch: "盯盘",
  rebound: "反弹",
  warning: "预警",
};

/**
 * 将后端 SwingAlert 条目转换为页面可渲染的对象。
 * @param {object} item  后端返回的单条提醒对象
 * @param {number} idx   下标（作为 wx:key）
 * @returns {object}
 */
function processAlertItem(item, idx) {
  if (!item) return null;

  var priority = item.priority || "low";
  var alertType = item.alert_type || item.type || "";
  var typeLabel = ALERT_TYPE_LABELS[alertType] || alertType || "提醒";
  var priorityLabel = PRIORITY_LABELS[priority] || priority;
  var priorityBadgeClass = PRIORITY_BADGE_CLASSES[priority] || "badge--muted";
  var isNew = !!item.is_new;

  return {
    _idx: idx,
    alert_type: alertType,
    typeLabel: typeLabel,
    title: item.title || "",
    content: item.content || item.body || item.message || "",
    priority: priority,
    priorityLabel: priorityLabel,
    priorityBadgeClass: priorityBadgeClass,
    isNew: isNew,
    // 保留原始字段供调试
    fund_name: item.fund_name || item.fund_code || "",
    created_at: item.created_at || "",
  };
}

Page({
  data: {
    loading: true,
    error: "",
    empty: false,

    alerts: [],           // 处理后的提醒列表
    evaluating: false,    // 是否正在执行「重新评估」
    evaluateError: "",    // 评估错误文案
  },

  onLoad: function () {
    // 不需要先检查 token，onShow 已处理，此处直接拉取数据
    this._loadTodayAlerts();
  },

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
    }
  },

  // ---------------------------------------------------------------------------
  // 拉取当日提醒（Req 19.2）
  // ---------------------------------------------------------------------------

  _loadTodayAlerts: function () {
    var self = this;
    this.setData({ loading: true, error: "", empty: false });

    api.fetchSwingAlertsToday()
      .then(function (data) {
        self._applyAlertsData(data);
      })
      .catch(function (err) {
        self.setData({
          loading: false,
          error: (err && err.message) || "加载当日提醒失败，请稍后重试",
        });
      });
  },

  /**
   * 将 API 响应映射到页面数据。
   * @param {object|Array} data  后端响应（可能是数组，也可能是包含 alerts 字段的对象）
   */
  _applyAlertsData: function (data) {
    var rawAlerts = [];

    if (Array.isArray(data)) {
      rawAlerts = data;
    } else if (data && Array.isArray(data.alerts)) {
      rawAlerts = data.alerts;
    } else if (data && Array.isArray(data.items)) {
      rawAlerts = data.items;
    }

    var alerts = [];
    for (var i = 0; i < rawAlerts.length; i++) {
      var processed = processAlertItem(rawAlerts[i], i);
      if (processed) {
        alerts.push(processed);
      }
    }

    // 优先级排序：high > medium > low
    var priorityOrder = { high: 0, medium: 1, low: 2 };
    alerts.sort(function (a, b) {
      var pa = priorityOrder[a.priority] != null ? priorityOrder[a.priority] : 3;
      var pb = priorityOrder[b.priority] != null ? priorityOrder[b.priority] : 3;
      return pa - pb;
    });

    this.setData({
      loading: false,
      error: "",
      empty: alerts.length === 0,
      alerts: alerts,
    });
  },

  // ---------------------------------------------------------------------------
  // 错误重试（Req 4.2）
  // ---------------------------------------------------------------------------

  onRetry: function () {
    this._loadTodayAlerts();
  },

  // ---------------------------------------------------------------------------
  // 重新评估（Req 19.1）：POST /api/swing-alerts/evaluate
  // ---------------------------------------------------------------------------

  /**
   * 用户主动触发「重新评估」：先拉取持仓与风控画像，再发起评估，
   * 评估完成后刷新当日提醒列表。
   */
  onEvaluate: function () {
    if (this.data.evaluating) return;

    var self = this;
    this.setData({ evaluating: true, evaluateError: "" });

    // 并行获取持仓与风控画像（评估需要）
    Promise.all([
      api.fetchPortfolioHoldings(),
      api.fetchInvestorProfile(),
    ])
      .then(function (results) {
        var holdingsData = results[0];
        var profile = results[1];

        var holdings = [];
        if (holdingsData && Array.isArray(holdingsData.holdings)) {
          holdings = holdingsData.holdings;
        } else if (Array.isArray(holdingsData)) {
          holdings = holdingsData;
        }

        return api.evaluateSwingAlerts(holdings, profile || {});
      })
      .then(function () {
        // 评估完成后刷新当日提醒（Req 19.2）
        return api.fetchSwingAlertsToday();
      })
      .then(function (data) {
        self.setData({ evaluating: false, evaluateError: "" });
        self._applyAlertsData(data);
        wx.showToast({ title: "评估完成", icon: "success", duration: 1500 });
      })
      .catch(function (err) {
        self.setData({
          evaluating: false,
          evaluateError: (err && err.message) || "评估失败，请稍后重试",
        });
      });
  },
});
