// components/trading-session-bar/trading-session-bar.js
// 交易日语义条：挂 GET /api/trading-session，展示当前交易时段语义与生效交易日。
//
// Requirements:
//   4.4 通过 GET /api/trading-session 获取并展示当前交易时段语义
//       （非交易日/盘前/盘中/盘尾/盘后）与生效交易日期。
//   4.5 当前时间处于交易日 9:30 之前，展示回溯至上一交易日的生效交易日期
//       （由后端 trading-session 响应的 effective_trade_date 承载，此处直接渲染）。
//
// 用法（页面 WXML）：
//   <trading-session-bar />
// 组件在 attached 生命周期自动拉取并渲染；拉取失败时显示降级提示，不影响其他功能。
//
// 数据契约（镜像 apps/web/src/lib/api.ts 的 TradingSession，见 design.md Data Models）：
//   { session_kind, effective_trade_date, market_open_time, minutes_to_close?, ... }

const api = require("../../utils/api");

// session_kind → 中文时段语义标签（Req 4.4）。
const SESSION_LABEL = {
  non_trading_day: "非交易日",
  trading_day_pre_open: "盘前",
  trading_day_intraday: "盘中",
  trading_day_pre_close: "盘尾",
  trading_day_after_close: "盘后",
};

// session_kind → 语义着色基调（用于徽标 class 后缀）。
const SESSION_TONE = {
  non_trading_day: "muted",
  trading_day_pre_open: "muted",
  trading_day_intraday: "active",
  trading_day_pre_close: "accent",
  trading_day_after_close: "muted",
};

Component({
  options: {
    // 允许外部页面通过 external-class 调整外层间距。
    addGlobalClass: true,
  },

  externalClasses: ["custom-class"],

  data: {
    // 当前态："loading" | "ready" | "error"
    phase: "loading",
    // 时段语义标签（Req 4.4）
    label: "",
    // 时段着色基调
    tone: "muted",
    // 生效交易日（Req 4.4 / 4.5，9:30 前已由后端回溯至上一交易日）
    effectiveTradeDate: "",
    // 距收盘分钟数（盘中/盘尾时展示）
    minutesToClose: null,
    // 是否交易日（用于辅助文案）
    isTradingDay: false,
  },

  lifetimes: {
    attached: function () {
      this.loadSession();
    },
  },

  methods: {
    // 拉取交易时段语义。
    loadSession: function () {
      this.setData({ phase: "loading" });
      api
        .fetchTradingSession()
        .then((session) => {
          this.applySession(session || {});
        })
        .catch(() => {
          this.setData({ phase: "error" });
        });
    },

    // 将后端响应映射到展示数据。
    applySession: function (session) {
      const kind = session.session_kind;
      const label = SESSION_LABEL[kind] || "交易日";
      const tone = SESSION_TONE[kind] || "muted";
      const minutesToClose =
        typeof session.minutes_to_close === "number" && session.minutes_to_close >= 0
          ? session.minutes_to_close
          : null;

      this.setData({
        phase: "ready",
        label: label,
        tone: tone,
        // Req 4.5：直接渲染后端给出的 effective_trade_date（已回溯）。
        effectiveTradeDate: session.effective_trade_date || "",
        minutesToClose: minutesToClose,
        isTradingDay: !!session.is_trading_day,
      });
    },

    // 错误态点击重试。
    onRetryTap: function () {
      this.loadSession();
    },
  },
});
