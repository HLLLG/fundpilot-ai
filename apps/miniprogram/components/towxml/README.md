# towxml Markdown 渲染源码（第三方）

官方 [towxml](https://github.com/sbfkcel/towxml) 第三方源码目录，用于把 Markdown 解析为
WXML 节点树并在小程序内渲染。对应 design.md「Markdown 渲染方案（Markdown_Renderer，A5）」。

> 需求覆盖：日报正文（Req 13.6）、日报追问（Req 14.4）、荐基追问（Req 16.6）、
> 简报内联追问（Req 17.5）。封装组件见 `components/md-view/`，业务侧只依赖 `md-view`。

## 文件清单

| 文件 | 作用 |
|------|------|
| `index.js` | **占位存根**，导出 `towxml(content, type, options)` 解析函数，需替换为官方源码（见下） |
| `towxml.js` | **占位存根**，`<towxml nodes="{{nodes}}"/>` 渲染组件，需替换为官方源码 |
| `towxml.wxml` | 占位渲染模板（回显原文，保证不丢内容） |
| `towxml.wxss` | 占位样式（与设计 token 对齐） |
| `towxml.json` | 组件声明 |

## ⚠️ 替换为官方 towxml 源码（上线/真机渲染前必做）

本目录的 `index.js` 与 `towxml.js` 是**占位存根**：

- `index.js` 不解析 Markdown 语法，只把原文包成带 `__placeholder` 标记的节点对象；
- `md-view` 检测到该标记后**优雅降级为纯文本渲染**（不丢内容，但无富文本排版）；
- 这样可让 `md-view` 封装组件能编译、业务页面（日报/荐基/简报追问）能基于
  `{ markdown }` 契约先行开发，但**无法实际渲染富文本**。

真机渲染前请用官方 towxml 源码替换本目录：

1. 打开 [towxml 仓库](https://github.com/sbfkcel/towxml)，按 README 构建/下载 `dist` 产物
   （包含 `index.js` 解析入口、`towxml.js` 渲染组件、`decode.wxml`、`parse/`、`style/` 等）。
2. 将产物**整体覆盖** `components/towxml/` 目录（替换占位的 `index.js` / `towxml.js` 等）。
3. 官方入口同样导出 `towxml(content, type, options)` 函数，签名一致；
   `md-view` 无需改动即可从占位降级切换到真实富文本渲染。
4. 如需暗色/主题，按官方 `options.theme` 传入（`md-view` 已透传 `theme` prop）。

替换后占位逻辑被官方源码整体覆盖，无需保留。

## 使用示例（在 md-view 封装组件中）

```json
// md-view.json
{
  "component": true,
  "usingComponents": {
    "towxml": "../towxml/towxml"
  }
}
```

```js
// md-view.js
const towxml = require("../towxml/index");
const nodes = towxml(markdown, "markdown", { theme: "light" });
this.setData({ nodes });
```

```xml
<!-- md-view.wxml -->
<towxml nodes="{{nodes}}" />
```

## 包体积与分包加载

towxml 源码体积较大（解析器 + 样式 + 组件）。小程序主包上限为 2MB。日报/荐基/简报追问
都属于 Markdown 渲染密集页面，建议把 `components/towxml/` 与 `components/md-view/`
一并随这些页面划入分包，使 towxml 体积计入分包而非主包：

```json
// app.json 示例
{
  "subpackages": [
    {
      "root": "packageReport",
      "name": "report",
      "pages": [
        "pages/report/report",
        "pages/discovery/discovery"
      ]
    }
  ],
  "preloadRule": {
    "pages/briefing/briefing": {
      "network": "all",
      "packages": ["report"]
    }
  }
}
```

将 `components/towxml/` 与 `components/md-view/` 放入分包目录（如 `packageReport/components/`），
即可让 towxml 体积随分包按需下载；`preloadRule` 可在首页空闲时预下载，减少首次进入日报页的等待。

> 注：是否启用分包视实际主包体积而定。当前阶段先以单包接入（占位存根体积极小），
> 待集成官方 towxml 源码后按主包体积体检结果决定是否分包。
