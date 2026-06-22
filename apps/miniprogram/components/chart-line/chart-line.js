// components/chart-line/chart-line.js
// 折线/分时图业务封装，包裹 ec-canvas（官方 echarts 桥接组件）。
//
// 用法：
//   <chart-line option="{{chartOption}}" canvas-id="intraday-chart" />
//
// option 由 utils/derive.js 的 buildIntradayChartOption 或 buildLineChartOption 生成，
// 组件只负责把 option 传给 echarts 实例的 setOption。
//
// Requirements: 8.2（Chart_Renderer 展示板块分时图，数据来自 /api/sector-quotes/intraday）
// Design: miniprogram-web-parity / 图表方案 / chart-line 业务封装

var echarts = require('../ec-canvas/echarts');

function initChart(canvas, width, height, dpr) {
  var chart = echarts.init(canvas, null, {
    width: width,
    height: height,
    devicePixelRatio: dpr,
  });
  canvas.setChart(chart);
  return chart;
}

Component({
  properties: {
    // echarts option 对象，由外部（derive.js 纯函数）生成后传入。
    option: {
      type: Object,
      value: null,
    },
    // canvas-id，同页面内多图表时需各自唯一。
    canvasId: {
      type: String,
      value: 'chart-line-canvas',
    },
    // 图表高度（rpx 字符串或数字，默认 400rpx）。
    height: {
      type: String,
      value: '400rpx',
    },
  },

  data: {
    // ec 对象传给 ec-canvas：绑定 onInit 回调。
    ec: null,
  },

  lifetimes: {
    attached: function () {
      var self = this;
      this.setData({
        ec: {
          onInit: function (canvas, w, h, dpr) {
            var chart = initChart(canvas, w, h, dpr);
            self._chart = chart;
            // 如果 option 已经准备好，立即设置。
            if (self.data.option) {
              chart.setOption(self.data.option);
            }
            return chart;
          },
        },
      });
    },
  },

  observers: {
    // 当 option 变化（包括首次设置）时更新图表。
    option: function (newOption) {
      if (this._chart && newOption) {
        this._chart.setOption(newOption, true);
      }
    },
  },
});
