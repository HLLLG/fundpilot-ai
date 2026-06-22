# ec-canvas 图表桥接组件

官方 [echarts-for-weixin](https://github.com/ecomfe/echarts-for-weixin) 桥接组件，配合 echarts
精简自定义构建，用于小程序内渲染折线/饼（甜甜圈）图表。对应 design.md「图表方案（Chart_Renderer，A2）」。

> 需求覆盖：分时图（Req 8.2）、收益走势曲线（Req 9.3）、持仓分布甜甜圈（Req 9.7）。
> 盈亏日历（Req 9.4）走 `chart-calendar` 的 WXML 自绘，不依赖本组件。

## 文件清单

| 文件 | 作用 |
|------|------|
| `ec-canvas.js` | 组件逻辑：初始化画布（兼容 2.9.0+ 的 `<canvas type="2d">` 与旧版 `canvas-id`）、转发触摸事件给 echarts |
| `ec-canvas.wxml` | 画布模板 |
| `ec-canvas.wxss` | 画布样式（铺满父容器） |
| `ec-canvas.json` | 组件声明 |
| `wx-canvas.js` | 小程序 Canvas → echarts 画布适配层 |
| `echarts.js` | **占位存根**，需替换为官方精简自定义构建（见下） |

## ⚠️ 替换 echarts.js（上线/真机渲染前必做）

仓库内的 `echarts.js` 是**占位存根**：它仅暴露 `setCanvasCreator` / `init` 等最小 API，
使桥接组件能编译、业务图表组件（`chart-line` / `chart-donut`）能基于 `option` 先行开发，
但**无法实际渲染**。真机渲染前请用官方精简自定义构建替换：

1. 打开 [echarts 在线自定义构建](https://echarts.apache.org/zh/builder.html)。
2. 仅勾选以下模块（与设计一致，控制体积 ~300–500KB）：
   - 图表（Charts）：**折线图 line**、**饼图 pie**
   - 坐标系/组件（Components）：**网格 grid**、**提示框 tooltip**
   - 渲染器：CanvasRenderer（默认）
   - 语言：ZH（中文）
3. 点击下载，得到 `echarts.min.js`。
4. 重命名为 `echarts.js`，覆盖 `components/ec-canvas/echarts.js`。

替换后占位逻辑被官方构建整体覆盖，无需保留。

## 使用示例（在业务图表组件中）

```json
// chart-line.json
{
  "component": true,
  "usingComponents": {
    "ec-canvas": "../ec-canvas/ec-canvas"
  }
}
```

```xml
<!-- chart-line.wxml -->
<view class="chart-wrap">
  <ec-canvas id="line" canvas-id="line-canvas" ec="{{ ec }}"></ec-canvas>
</view>
```

```js
// chart-line.js
import * as echarts from '../ec-canvas/echarts';

Component({
  data: {
    ec: { onInit: null }
  },
  lifetimes: {
    attached() {
      this.setData({ ec: { onInit: this.initChart.bind(this) } });
    }
  },
  methods: {
    initChart(canvas, width, height, dpr) {
      const chart = echarts.init(canvas, null, { width, height, devicePixelRatio: dpr });
      canvas.setChart(chart);
      // option 由 utils/derive.js 纯函数生成（可测），组件只负责 setOption
      chart.setOption(this.properties.option || {});
      return chart;
    }
  }
});
```

> 约定：图表 `option` 一律由 `utils/derive.js` 纯函数从接口数据生成（可单测），
> 组件只调用 `chart.setOption(option)`，便于属性测试覆盖映射逻辑（Property 8）。

## 包体积与分包加载

echarts 自定义构建产物（约 300–500KB）放在 `components/ec-canvas/echarts.js`。
小程序主包上限为 2MB。若主包接近超限，按需启用分包，把图表密集页面（市场 / 盈亏分析 /
基金详情）划入分包，使 echarts 仅随分包按需下载：

```json
// app.json 示例
{
  "pages": [
    "pages/login/login",
    "pages/holdings/holdings"
  ],
  "subpackages": [
    {
      "root": "packageChart",
      "name": "chart",
      "pages": [
        "pages/profit/profit",
        "pages/market/market",
        "pages/fund-detail/fund-detail"
      ]
    }
  ],
  "preloadRule": {
    "pages/holdings/holdings": {
      "network": "all",
      "packages": ["chart"]
    }
  }
}
```

将 `components/ec-canvas/` 与业务图表组件一并放入分包目录（如 `packageChart/components/`），
即可让 echarts 体积计入分包而非主包。`preloadRule` 可在主包页面空闲时预下载分包，减少首次进入图表页的等待。

> 注：是否启用分包视实际主包体积而定。当前阶段先以单包接入，待集成真实 echarts 构建后按主包体积体检结果决定。
