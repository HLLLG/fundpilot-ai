/**
 * echarts.js — 精简自定义构建占位文件（PLACEHOLDER）。
 *
 * ⚠️ 这不是真正的 echarts 运行时，仅为占位 / API 契约存根。
 *    它暴露了 ec-canvas.js 与业务图表组件（chart-line / chart-donut）所需的
 *    最小 API 表面（setCanvasCreator / init / registerXxx 等），以便：
 *      1) ec-canvas 桥接组件可以编译通过；
 *      2) 上层业务图表组件可以先行开发并基于 `option` 配置编写逻辑。
 *
 * ✅ 上线 / 真机渲染前，请用官方精简自定义构建产物替换本文件：
 *    在线构建：https://echarts.apache.org/zh/builder.html
 *    勾选项（与 design.md「图表方案」一致，控制体积）：
 *      - 图表（Charts）：折线图 line、饼图 pie
 *      - 组件（Components）：直角坐标系 grid、提示框 tooltip
 *      - 其它（Others）：可选 SVGRenderer/CanvasRenderer（默认 Canvas）
 *      - 语言：ZH（中文）
 *    生成后将下载的 echarts.min.js 重命名为 echarts.js 覆盖本文件即可。
 *    （约 300–500KB；若导致主包超限，按 README「分包加载」一节配置分包。）
 *
 * 替换后下方占位逻辑会被官方构建整体覆盖，无需保留。
 */

const PLACEHOLDER_MESSAGE =
  '[ec-canvas] echarts.js 为占位存根，尚未替换为官方精简自定义构建。' +
  '图表无法实际渲染。请按 components/ec-canvas/README.md 从 ' +
  'https://echarts.apache.org/zh/builder.html 生成 line/pie + grid/tooltip ' +
  '自定义构建并覆盖 echarts.js。';

let warnedOnce = false;
function warnPlaceholder() {
  if (!warnedOnce) {
    // eslint-disable-next-line no-console
    console.warn(PLACEHOLDER_MESSAGE);
    warnedOnce = true;
  }
}

/**
 * ec-canvas 在初始化时会调用 setCanvasCreator 注册画布工厂。
 * 占位实现仅记录工厂，不做实际渲染。
 */
let canvasCreator = null;
export function setCanvasCreator(creator) {
  warnPlaceholder();
  canvasCreator = creator;
}

/**
 * 占位 init：返回一个具备 echarts 实例最小接口的对象，
 * 使上层 onInit(canvas, width, height, dpr) 流程不致抛错。
 * 真正渲染需替换为官方构建。
 */
export function init() {
  warnPlaceholder();
  const noop = () => {};
  const fakeHandler = {
    dispatch: noop,
    processGesture: noop
  };
  const fakeZr = {
    handler: fakeHandler
  };
  return {
    setOption: noop,
    resize: noop,
    clear: noop,
    dispose: noop,
    on: noop,
    off: noop,
    getZr: () => fakeZr,
    // 暴露注册的画布工厂便于调试
    __placeholderCanvas: canvasCreator
  };
}

// echarts 官方构建中常见的注册类 API，占位为 noop 以兼容潜在调用。
export function use() {
  warnPlaceholder();
}
export function registerMap() {
  warnPlaceholder();
}
export function registerTheme() {
  warnPlaceholder();
}
export function connect() {
  warnPlaceholder();
}

export const version = '0.0.0-placeholder';

export default {
  setCanvasCreator,
  init,
  use,
  registerMap,
  registerTheme,
  connect,
  version
};
