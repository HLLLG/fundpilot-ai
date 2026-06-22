// components/num-text/num-text.js
// 红涨绿跌 + 千分位 + 正值「+」前缀的数字展示组件。
//
// Requirements: 1.6（展示金额/收益按红涨绿跌着色，正值显示「+」前缀）。
//
// 所有格式化与涨跌判定逻辑复用 utils/format.js 纯函数（numText 描述器），
// 组件本身只负责把结果绑定到视图，并把 tone 映射到 WXSS 配色类
// （up→var(--up) 红 / down→var(--down) 绿 / flat→var(--neutral) 中性）。
//
// 用法：
//   <num-text value="{{rate}}" kind="percent" />            <!-- 收益率：默认带「+」 -->
//   <num-text value="{{amount}}" kind="money" signed="{{false}}" /> <!-- 金额：不带「+」 -->
//   <num-text value="{{n}}" kind="plain" />

var format = require("../../utils/format.js");

Component({
  properties: {
    // 待格式化的值：number 或可解析字符串；空/非数渲染占位符。
    value: {
      type: null,
      value: null,
    },
    // 格式种类：'percent' | 'money' | 'plain'。
    kind: {
      type: String,
      value: "plain",
    },
    // 是否对正值加「+」前缀。不传（null）时按 kind 默认：percent 默认带，其余不带。
    signed: {
      type: null,
      value: null,
    },
    // 小数位（默认 2）。
    digits: {
      type: Number,
      value: 2,
    },
    // 是否启用千分位（透传 format options.grouping；不传时按各 kind 默认）。
    grouping: {
      type: null,
      value: null,
    },
    // 空值占位符。
    placeholder: {
      type: String,
      value: "—",
    },
  },

  data: {
    // 展示文本
    text: "—",
    // 涨跌语义："up" | "down" | "flat"
    tone: "flat",
    // 是否为占位（空/非数）
    isPlaceholder: true,
  },

  observers: {
    "value, kind, signed, digits, grouping, placeholder": function () {
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
      var options = {
        digits: typeof this.data.digits === "number" ? this.data.digits : 2,
        placeholder: this.data.placeholder,
      };
      // grouping 仅在显式传入布尔值时透传，否则交由 format 按 kind 取默认。
      if (typeof this.data.grouping === "boolean") {
        options.grouping = this.data.grouping;
      }

      // signed 仅在显式布尔时透传，否则按 kind 默认（percent 带「+」，其余不带）。
      var signed =
        typeof this.data.signed === "boolean" ? this.data.signed : undefined;

      var result = format.numText(
        this.data.value,
        this.data.kind,
        signed,
        options
      );

      this.setData({
        text: result.text,
        tone: result.tone,
        isPlaceholder: result.isPlaceholder,
      });
    },
  },
});
