/**
 * ec-canvas.js — 官方 echarts-for-weixin 桥接组件。
 *
 * 用法（业务图表组件 chart-line / chart-donut 内）：
 *   <ec-canvas id="chart" canvas-id="line-canvas" ec="{{ ec }}"></ec-canvas>
 * 在页面/组件 data 中提供：
 *   ec: { onInit: initChart }
 * 其中 initChart(canvas, width, height, dpr) 返回 echarts.init(...) 的实例。
 *
 * echarts 依赖来自同目录 echarts.js（精简自定义构建：line/pie + grid/tooltip）。
 * 详见 README.md 获取/替换自定义构建产物的说明。
 */
import WxCanvas from './wx-canvas';
import * as echarts from './echarts';

let ctx;

Component({
  properties: {
    canvasId: {
      type: String,
      value: 'ec-canvas'
    },
    ec: {
      type: Object
    },
    forceUseOldCanvas: {
      type: Boolean,
      value: false
    }
  },

  data: {
    isUseNewCanvas: false
  },

  ready: function () {
    if (!this.data.ec) {
      console.warn(
        '组件需绑定 ec 变量，例：<ec-canvas id="mychart" canvas-id="mychart" ec="{{ ec }}"></ec-canvas>'
      );
      return;
    }

    if (!this.data.ec.lazyLoad) {
      this.init();
    }
  },

  methods: {
    init: function (callback) {
      const version = wx.getSystemInfoSync().SDKVersion;

      const canUseNewCanvas = this.compareVersion(version, '2.9.0') >= 0;
      const forceUseOldCanvas = this.data.forceUseOldCanvas;
      const isUseNewCanvas = canUseNewCanvas && !forceUseOldCanvas;
      this.setData({ isUseNewCanvas });

      if (forceUseOldCanvas && canUseNewCanvas) {
        console.warn('开发者强制使用旧canvas,建议关闭');
      }

      if (isUseNewCanvas) {
        // 2.9.0 以上使用 <canvas type="2d"></canvas>
        this.initByNewWay(callback);
      } else {
        const isValid = this.compareVersion(version, '1.9.91') >= 0;
        if (!isValid) {
          console.error(
            '微信基础库版本过低，需大于等于 1.9.91。' +
              '参见：https://github.com/ecomfe/echarts-for-weixin' +
              '#%E5%BE%AE%E4%BF%A1%E7%89%88%E6%9C%AC%E8%A6%81%E6%B1%82'
          );
          return;
        } else {
          console.warn('建议将微信基础库调整大于等于 2.9.0 版本。升级后绘图将有更好性能');
          this.initByOldWay(callback);
        }
      }
    },

    initByOldWay(callback) {
      // 1.9.91 <= version < 2.9.0：原 ctx 方式
      const ctxObj = wx.createCanvasContext(this.data.canvasId, this);
      const canvas = new WxCanvas(ctxObj, this.data.canvasId, false);

      echarts.setCanvasCreator(() => {
        return canvas;
      });

      const canvasDpr = 1;
      var query = wx.createSelectorQuery().in(this);
      query
        .select('.ec-canvas')
        .boundingClientRect(res => {
          if (typeof callback === 'function') {
            this.chart = callback(canvas, res.width, res.height, canvasDpr);
          } else if (this.data.ec && typeof this.data.ec.onInit === 'function') {
            this.chart = this.data.ec.onInit(canvas, res.width, res.height, canvasDpr);
          } else {
            this.triggerEvent('init', {
              canvas: canvas,
              width: res.width,
              height: res.height,
              canvasDpr: canvasDpr
            });
          }
        })
        .exec();
    },

    initByNewWay(callback) {
      // version >= 2.9.0：使用 <canvas type="2d"></canvas>
      const query = wx.createSelectorQuery().in(this);
      query
        .select('.ec-canvas')
        .fields({ node: true, size: true })
        .exec(res => {
          const canvasNode = res[0].node;
          this.canvasNode = canvasNode;

          const canvasDpr = wx.getSystemInfoSync().pixelRatio;
          const canvasWidth = res[0].width;
          const canvasHeight = res[0].height;

          const ctxObj = canvasNode.getContext('2d');
          ctx = ctxObj;

          const canvas = new WxCanvas(ctxObj, this.data.canvasId, true, canvasNode);
          echarts.setCanvasCreator(() => {
            return canvas;
          });

          if (typeof callback === 'function') {
            this.chart = callback(canvas, canvasWidth, canvasHeight, canvasDpr);
          } else if (this.data.ec && typeof this.data.ec.onInit === 'function') {
            this.chart = this.data.ec.onInit(canvas, canvasWidth, canvasHeight, canvasDpr);
          } else {
            this.triggerEvent('init', {
              canvas: canvas,
              width: canvasWidth,
              height: canvasHeight,
              dpr: canvasDpr
            });
          }
        });
    },

    canvasToTempFilePath(opt) {
      if (this.data.isUseNewCanvas) {
        const query = wx.createSelectorQuery().in(this);
        query
          .select('.ec-canvas')
          .fields({ node: true, size: true })
          .exec(res => {
            const canvasNode = res[0].node;
            opt.canvas = canvasNode;
            wx.canvasToTempFilePath(opt);
          });
      } else {
        if (!opt.canvasId) {
          opt.canvasId = this.data.canvasId;
        }
        ctx.draw(true, () => {
          wx.canvasToTempFilePath(opt, this);
        });
      }
    },

    touchStart(e) {
      if (this.chart && e.touches.length > 0) {
        var touch = e.touches[0];
        var handler = this.chart.getZr().handler;
        handler.dispatch('mousedown', {
          zrX: touch.x,
          zrY: touch.y,
          preventDefault: () => {},
          stopImmediatePropagation: () => {},
          stopPropagation: () => {}
        });
        handler.dispatch('mousemove', {
          zrX: touch.x,
          zrY: touch.y,
          preventDefault: () => {},
          stopImmediatePropagation: () => {},
          stopPropagation: () => {}
        });
        handler.processGesture(this.wrapTouch(e), 'start');
      }
    },

    touchMove(e) {
      if (this.chart && e.touches.length > 0) {
        var touch = e.touches[0];
        var handler = this.chart.getZr().handler;
        handler.dispatch('mousemove', {
          zrX: touch.x,
          zrY: touch.y,
          preventDefault: () => {},
          stopImmediatePropagation: () => {},
          stopPropagation: () => {}
        });
        handler.processGesture(this.wrapTouch(e), 'change');
      }
    },

    touchEnd(e) {
      if (this.chart) {
        const touch = e.changedTouches ? e.changedTouches[0] : {};
        var handler = this.chart.getZr().handler;
        handler.dispatch('mouseup', {
          zrX: touch.x,
          zrY: touch.y,
          preventDefault: () => {},
          stopImmediatePropagation: () => {},
          stopPropagation: () => {}
        });
        handler.dispatch('click', {
          zrX: touch.x,
          zrY: touch.y,
          preventDefault: () => {},
          stopImmediatePropagation: () => {},
          stopPropagation: () => {}
        });
        handler.processGesture(this.wrapTouch(e), 'end');
      }
    },

    wrapTouch(event) {
      for (let i = 0; i < event.touches.length; ++i) {
        const touch = event.touches[i];
        touch.offsetX = touch.x;
        touch.offsetY = touch.y;
      }
      return event;
    },

    compareVersion(v1, v2) {
      v1 = v1.split('.');
      v2 = v2.split('.');
      const len = Math.max(v1.length, v2.length);

      while (v1.length < len) {
        v1.push('0');
      }
      while (v2.length < len) {
        v2.push('0');
      }

      for (let i = 0; i < len; i++) {
        const num1 = parseInt(v1[i]);
        const num2 = parseInt(v2[i]);

        if (num1 > num2) {
          return 1;
        } else if (num1 < num2) {
          return -1;
        }
      }
      return 0;
    }
  }
});
