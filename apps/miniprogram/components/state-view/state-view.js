// components/state-view/state-view.js
// 通用三态包裹组件：加载 / 错误（带「重试」）/ 空态。
//
// Requirements: 1.5（提供加载态/空态/错误态三种通用展示组件）、
//               4.1（拉取数据时显示加载态）、4.2（拉取失败显示错误态并提供「重试」）。
//
// 用法（页面 WXML）：
//   <state-view loading="{{loading}}" error="{{error}}" empty="{{isEmpty}}" bind:retry="onRetry">
//     <view>真正的内容（仅在三态都不命中时通过 slot 渲染）</view>
//   </state-view>
//
// `onRetry` prop 模式：错误态点击「重试」时触发 `retry` 自定义事件，页面绑定
// `bind:retry` 重新发起当前请求即可。
//
// 三态优先级：loading > error > empty > 默认内容(slot)。

Component({
  options: {
    // 同时支持默认 slot（内容态）与具名 slot（空态自定义操作入口）。
    multipleSlots: true,
  },

  properties: {
    // 是否处于加载中。
    loading: {
      type: Boolean,
      value: false,
    },
    // 错误信息：truthy 时进入错误态。可传 string（错误文案）或 boolean。
    error: {
      type: null,
      value: null,
    },
    // 是否为空数据（在非 loading / 非 error 时生效）。
    empty: {
      type: Boolean,
      value: false,
    },
    // 各态文案（可定制）。
    loadingText: {
      type: String,
      value: "加载中…",
    },
    emptyText: {
      type: String,
      value: "暂无数据",
    },
    retryText: {
      type: String,
      value: "重试",
    },
    // 默认错误文案（当 error 为 boolean true 而非具体文案时使用）。
    errorText: {
      type: String,
      value: "加载失败，请稍后重试",
    },
  },

  data: {
    // 计算出的当前态："loading" | "error" | "empty" | "content"
    phase: "content",
    // 错误态展示文案
    errorMessage: "",
  },

  observers: {
    "loading, error, empty, errorText": function () {
      this._recompute();
    },
  },

  lifetimes: {
    attached: function () {
      this._recompute();
    },
  },

  methods: {
    _recompute: function () {
      var loading = this.data.loading;
      var error = this.data.error;
      var empty = this.data.empty;

      var phase;
      var errorMessage = "";
      if (loading) {
        phase = "loading";
      } else if (error) {
        phase = "error";
        // error 为字符串则作为文案；否则回退到默认错误文案。
        errorMessage =
          typeof error === "string" && error.trim() !== ""
            ? error
            : this.data.errorText;
      } else if (empty) {
        phase = "empty";
      } else {
        phase = "content";
      }

      this.setData({ phase: phase, errorMessage: errorMessage });
    },

    // 错误态「重试」点击：触发 retry 事件（onRetry prop 模式）。
    onRetryTap: function () {
      this.triggerEvent("retry");
    },
  },
});
