// utils/format.js
// 数字 / 金额 / 百分比格式化纯函数。
//
// 设计约束：本模块为纯 JavaScript（CommonJS），不依赖任何 `wx.*` 宿主能力，
// 因此可在 Node 环境下用单元测试与属性测试覆盖（见 design.md Property 1）。
// 由 num-text 组件复用，统一实现：红涨绿跌着色判定、正值「+」前缀、千分位、百分号。
//
// 语义（对齐 Web 端 apps/web）：
//   - 正值（按展示精度四舍五入后 > 0）→ 「涨」(up)，可加「+」前缀。
//   - 负值（四舍五入后 < 0）→ 「跌」(down)。
//   - 零 / 空 / 非数 → 中性 (flat)，不加「+」前缀。
// 千分位与百分号只改变展示形态，不改变其数值含义。

// 涨跌语义 token。组件层据此映射 WXSS 类（红涨绿跌，见 styles/tokens.wxss）。
var TONE_UP = "up";
var TONE_DOWN = "down";
var TONE_FLAT = "flat";

// 空值/非数占位符（对齐 Web）。
var PLACEHOLDER = "—";

/**
 * 宽松解析为有限数字；无法解析或非有限值返回 null。
 * 接受 number 或可被 Number() 解析的字符串（如 "1,234.5" 会被先去除千分位）。
 * @param {*} value
 * @returns {number|null}
 */
function toNumber(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    var trimmed = value.trim();
    if (trimmed === "") {
      return null;
    }
    // 去除千分位逗号后再解析，便于对已格式化文本做往返解析。
    var n = Number(trimmed.replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * 按指定小数位四舍五入（half-up，对齐 Web 的 Math.round(v*100)/100 口径）。
 * @param {number} n
 * @param {number} digits
 * @returns {number}
 */
function roundTo(n, digits) {
  var factor = Math.pow(10, digits);
  // 加极小 epsilon 抵消二进制浮点在 .5 边界的表示误差（如 1.005）。
  var rounded = Math.round((n + (n >= 0 ? 1 : -1) * Number.EPSILON) * factor) / factor;
  // 归一化 -0 为 0，避免出现「-0.00」。
  return rounded === 0 ? 0 : rounded;
}

/**
 * 判定涨跌语义 token（基于展示精度四舍五入后的值，保证与 +/- 前缀、配色一致）。
 * @param {*} value
 * @param {number} [digits=2] 用于判定的展示精度
 * @returns {"up"|"down"|"flat"}
 */
function tone(value, digits) {
  var d = typeof digits === "number" ? digits : 2;
  var n = toNumber(value);
  if (n === null) {
    return TONE_FLAT;
  }
  var rounded = roundTo(n, d);
  if (rounded > 0) {
    return TONE_UP;
  }
  if (rounded < 0) {
    return TONE_DOWN;
  }
  return TONE_FLAT;
}

/**
 * 返回涨跌语义对应的 WXSS 类名（默认前缀 "num"，得到 num-up/num-down/num-flat）。
 * @param {*} value
 * @param {object} [options]
 * @param {string} [options.prefix="num"]
 * @param {number} [options.digits=2]
 * @returns {string}
 */
function toneClass(value, options) {
  var opts = options || {};
  var prefix = typeof opts.prefix === "string" ? opts.prefix : "num";
  return prefix + "-" + tone(value, opts.digits);
}

/**
 * 给「绝对值」的定点小数字符串添加千分位（不改变数值含义）。
 * @param {string} absFixed 形如 "1234.56" 或 "1234" 的非负字符串
 * @returns {string}
 */
function groupThousands(absFixed) {
  var parts = absFixed.split(".");
  var intPart = parts[0];
  var decPart = parts.length > 1 ? parts[1] : null;
  var grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return decPart !== null ? grouped + "." + decPart : grouped;
}

/**
 * 核心格式化：把数值渲染为定点小数字符串，可选千分位与「+」前缀。
 * @param {*} value
 * @param {object} [options]
 * @param {number} [options.digits=2] 小数位
 * @param {boolean} [options.signed=false] 正值是否加「+」前缀
 * @param {boolean} [options.grouping=true] 是否启用千分位
 * @param {string} [options.suffix=""] 追加后缀（如 "%"、"亿"）
 * @param {string} [options.placeholder="—"] 空值占位
 * @returns {string}
 */
function format(value, options) {
  var opts = options || {};
  var digits = typeof opts.digits === "number" ? opts.digits : 2;
  var signed = opts.signed === true;
  var grouping = opts.grouping !== false;
  var suffix = typeof opts.suffix === "string" ? opts.suffix : "";
  var placeholder =
    typeof opts.placeholder === "string" ? opts.placeholder : PLACEHOLDER;

  var n = toNumber(value);
  if (n === null) {
    return placeholder;
  }

  var rounded = roundTo(n, digits);
  var sign = "";
  if (rounded > 0 && signed) {
    sign = "+";
  } else if (rounded < 0) {
    sign = "-";
  }

  var absFixed = Math.abs(rounded).toFixed(digits);
  var body = grouping ? groupThousands(absFixed) : absFixed;
  return sign + body + suffix;
}

/**
 * 金额格式化：默认 2 位小数 + 千分位。正值是否加「+」由 signed 控制。
 * @param {*} value
 * @param {object} [options] 见 format 的 options（digits/signed/grouping/placeholder）
 * @returns {string}
 */
function formatMoney(value, options) {
  var opts = options || {};
  return format(value, {
    digits: typeof opts.digits === "number" ? opts.digits : 2,
    signed: opts.signed === true,
    grouping: opts.grouping !== false,
    placeholder: opts.placeholder,
  });
}

/**
 * 百分比格式化：默认 2 位小数 + 「%」后缀，正值默认加「+」前缀（对齐 Web formatPercent）。
 * @param {*} value
 * @param {object} [options]
 * @returns {string}
 */
function formatPercent(value, options) {
  var opts = options || {};
  return format(value, {
    digits: typeof opts.digits === "number" ? opts.digits : 2,
    // 百分比默认带符号；显式传 signed:false 可关闭。
    signed: opts.signed !== false,
    grouping: opts.grouping === true, // 百分比默认不分组
    suffix: "%",
    placeholder: opts.placeholder,
  });
}

/**
 * 普通数字格式化：默认 2 位小数 + 千分位，不带「+」前缀。
 * @param {*} value
 * @param {object} [options]
 * @returns {string}
 */
function formatPlain(value, options) {
  var opts = options || {};
  return format(value, {
    digits: typeof opts.digits === "number" ? opts.digits : 2,
    signed: opts.signed === true,
    grouping: opts.grouping !== false,
    placeholder: opts.placeholder,
  });
}

/**
 * num-text 组件的统一描述器：根据 kind 返回展示文本与涨跌语义/类名。
 * @param {*} value
 * @param {"percent"|"money"|"plain"} [kind="plain"]
 * @param {boolean} [signed] 是否强制带「+」前缀；不传时按 kind 默认
 * @param {object} [options] 透传 digits/grouping/placeholder/classPrefix
 * @returns {{ text: string, tone: "up"|"down"|"flat", toneClass: string, isPlaceholder: boolean }}
 */
function numText(value, kind, signed, options) {
  var opts = options || {};
  var k = kind || "plain";
  var fmtOptions = {
    digits: opts.digits,
    grouping: opts.grouping,
    placeholder: opts.placeholder,
  };
  if (typeof signed === "boolean") {
    fmtOptions.signed = signed;
  }

  var text;
  if (k === "percent") {
    text = formatPercent(value, fmtOptions);
  } else if (k === "money") {
    text = formatMoney(value, fmtOptions);
  } else {
    text = formatPlain(value, fmtOptions);
  }

  var digits = typeof opts.digits === "number" ? opts.digits : 2;
  var t = tone(value, digits);
  var placeholder =
    typeof opts.placeholder === "string" ? opts.placeholder : PLACEHOLDER;

  return {
    text: text,
    tone: t,
    toneClass: toneClass(value, { prefix: opts.classPrefix, digits: digits }),
    isPlaceholder: text === placeholder,
  };
}

module.exports = {
  TONE_UP: TONE_UP,
  TONE_DOWN: TONE_DOWN,
  TONE_FLAT: TONE_FLAT,
  PLACEHOLDER: PLACEHOLDER,
  toNumber: toNumber,
  roundTo: roundTo,
  tone: tone,
  toneClass: toneClass,
  groupThousands: groupThousands,
  format: format,
  formatMoney: formatMoney,
  formatPercent: formatPercent,
  formatPlain: formatPlain,
  numText: numText,
};
