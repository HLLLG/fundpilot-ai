// components/md-view/md-view.js
// towxml Markdown 渲染封装组件。对应 design.md「Markdown 渲染方案
// （Markdown_Renderer，A5）」。
//
// Requirements: 13.6（日报正文 Markdown 渲染）；并复用于日报追问（14.4）、
//               荐基追问（16.6）、简报内联追问（17.5）。
//
// 契约：props { markdown }。内部调用 towxml(markdown, 'markdown', { theme })
// 生成节点树，再交给 <towxml nodes="{{nodes}}"/> 渲染。
//
// 优雅降级：当 components/towxml 仍是占位存根（解析结果带 __placeholder 标记）时，
// 本组件降级为纯文本渲染，保证内容不丢失；替换为官方 towxml 源码后自动切换为
// 富文本渲染，业务侧无需改动。
//
// 用法（页面/父组件 WXML）：
//   <md-view markdown="{{report.body}}" />
//
// 主题/链接颜色与设计 token 对齐见 md-view.wxss。

const towxml = require("../towxml/index");

Component({
  options: {
    // 让外部样式（token）可透传到 towxml 渲染出的富文本节点。
    styleIsolation: "apply-shared",
  },

  properties: {
    // Markdown 原文。
    markdown: {
      type: String,
      value: "",
    },
    // 主题：light | dark，透传给 towxml（与设计 token 对齐，默认 light）。
    theme: {
      type: String,
      value: "light",
    },
  },

  data: {
    // towxml 解析出的节点树。
    nodes: null,
    // 是否处于占位降级（纯文本）模式。
    isPlaceholder: false,
    // 降级模式下展示的纯文本。
    plainText: "",
  },

  observers: {
    "markdown, theme": function () {
      this._render();
    },
  },

  lifetimes: {
    attached: function () {
      this._render();
    },
  },

  methods: {
    _render: function () {
      const markdown = this.data.markdown || "";
      if (!markdown) {
        this.setData({ nodes: null, isPlaceholder: false, plainText: "" });
        return;
      }

      let nodes = null;
      try {
        nodes = towxml(markdown, "markdown", {
          theme: this.data.theme === "dark" ? "dark" : "light",
        });
      } catch (err) {
        // 解析异常时降级为纯文本，避免渲染崩溃。
        // eslint-disable-next-line no-console
        console.error("[md-view] towxml 解析失败，降级为纯文本：", err);
        this.setData({
          nodes: null,
          isPlaceholder: true,
          plainText: markdown,
        });
        return;
      }

      // 占位存根：towxml 尚未替换为官方源码 → 纯文本降级。
      if (nodes && nodes.__placeholder) {
        this.setData({
          nodes: null,
          isPlaceholder: true,
          plainText: String(nodes.__raw || markdown),
        });
        return;
      }

      // 官方 towxml：交给 <towxml> 组件渲染富文本。
      this.setData({ nodes: nodes, isPlaceholder: false, plainText: "" });
    },
  },
});
