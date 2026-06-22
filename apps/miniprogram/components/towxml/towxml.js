// components/towxml/towxml.js — towxml 渲染组件（PLACEHOLDER）。
//
// ⚠️ 官方 towxml 提供同名 <towxml nodes="{{nodes}}" /> 组件，递归渲染 index.js
//    解析出的节点树（标题/段落/列表/代码/表格等）。本文件是占位存根，仅保证
//    <towxml> 标签可被 usingComponents 解析、md-view 能编译通过。
//
//    正常情况下 md-view 检测到占位标记后会优雅降级为纯文本，不会走到本组件；
//    本组件仅在被直接使用时给出明显的占位提示。
//
// ✅ 用官方 towxml 源码整体覆盖 components/towxml/ 后，本文件会被官方组件取代。
//    见 components/towxml/README.md。

Component({
  options: {
    // 官方 towxml 同样开启多 slot；保持一致以减少替换差异。
    multipleSlots: true,
  },

  properties: {
    // 由 index.js（towxml 解析函数）生成的节点树。
    nodes: {
      type: Object,
      value: null,
    },
  },

  data: {
    // 占位文案：当确实把占位节点交给本组件渲染时展示。
    placeholderRaw: "",
    isPlaceholder: false,
  },

  observers: {
    nodes: function (nodes) {
      const isPlaceholder = !!(nodes && nodes.__placeholder);
      this.setData({
        isPlaceholder: isPlaceholder,
        placeholderRaw: isPlaceholder ? String(nodes.__raw || "") : "",
      });
    },
  },
});
