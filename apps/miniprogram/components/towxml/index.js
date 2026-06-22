/**
 * towxml/index.js — Markdown→节点树解析入口（PLACEHOLDER）。
 *
 * ⚠️ 这不是真正的 towxml 运行时，仅为占位 / API 契约存根。对应 design.md
 *    「Markdown 渲染方案（Markdown_Renderer，A5）」。它暴露了 md-view 封装组件
 *    所需的最小调用契约：
 *
 *      const towxml = require('../towxml/index');
 *      const nodes = towxml(markdown, 'markdown', { theme, events });
 *      // -> <towxml nodes="{{nodes}}" />
 *
 *    以便：
 *      1) md-view 封装组件可以编译通过并先行开发；
 *      2) 上层业务页面（日报/荐基/简报追问）可以基于 { markdown } 契约接入。
 *
 *    占位实现「不解析」Markdown 语法，只把原文包装成一个带 __placeholder 标记的
 *    节点对象。md-view 检测到该标记后会优雅降级为纯文本渲染（见 md-view.js）。
 *
 * ✅ 上线 / 真机渲染前，请用官方 towxml 源码替换本目录：
 *    源码仓库：https://github.com/sbfkcel/towxml
 *    集成步骤（与 design.md A5 一致）：
 *      1) 从 towxml 仓库构建/下载 dist 源码（含 towxml.js 组件、parse 逻辑、
 *         decode.wxml、style/ 等），整体覆盖 components/towxml/ 目录；
 *      2) 官方入口同样导出 `towxml(content, type, options)` 函数，
 *         md-view 无需改动即可切换到真实渲染；
 *      3) towxml 体积较大，建议随日报/荐基分包加载（见本目录 README「分包加载」）。
 *
 *    替换后下方占位逻辑会被官方源码整体覆盖，无需保留。
 */

const PLACEHOLDER_MESSAGE =
  "[towxml] components/towxml 为占位存根，尚未替换为官方 towxml 源码。" +
  "Markdown 不会被解析为富文本，md-view 已降级为纯文本渲染。" +
  "请按 components/towxml/README.md 从 https://github.com/sbfkcel/towxml " +
  "获取源码覆盖本目录。";

let warnedOnce = false;
function warnPlaceholder() {
  if (!warnedOnce) {
    // eslint-disable-next-line no-console
    console.warn(PLACEHOLDER_MESSAGE);
    warnedOnce = true;
  }
}

/**
 * 占位解析函数，签名对齐官方 towxml：
 * @param {string} content  Markdown 原文
 * @param {string} type     内容类型，固定 'markdown'
 * @param {object} [options] { theme, events, ... }
 * @returns {object} 节点树占位对象，带 __placeholder 标记供 md-view 识别。
 */
function towxml(content, type, options) {
  warnPlaceholder();
  const theme =
    (options && options.theme) === "dark" ? "dark" : "light";
  return {
    // 与官方输出近似的外层结构，便于真实替换后 <towxml> 直接消费。
    theme: theme,
    base: (options && options.base) || "",
    child: [],
    // ↓ 占位专属字段：md-view 检测到后降级为纯文本。
    __placeholder: true,
    __raw: typeof content === "string" ? content : "",
  };
}

module.exports = towxml;
