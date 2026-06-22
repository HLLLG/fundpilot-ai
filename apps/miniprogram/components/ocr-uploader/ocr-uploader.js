// components/ocr-uploader/ocr-uploader.js
// OCR 上传入口组件（轻量封装）。
//
// 此组件作为「跳转入口」包装：触发 bindtap 后导航到 pages/ocr-import/ocr-import
// 完整页面。OCR 主逻辑（wx.chooseMedia / wx.uploadFile / 编辑结果 / apply-holdings）
// 全部在页面层（pages/ocr-import）实现，以利用完整的页面生命周期和全屏布局。
//
// 对照 design.md「ocr-uploader」组件定义：
//   wx.chooseMedia 选图 → wx.uploadFile 以 preview=true 调 /api/ocr →
//   展示可编辑识别结果 → 确认调 /api/portfolio/apply-holdings 刷新看板缓存
//
// 对应 requirements: 6.1–6.7（实现在 pages/ocr-import/ocr-import）。

Component({
  properties: {
    // 按钮文案，默认「截图录入持仓」
    label: {
      type: String,
      value: "截图录入持仓",
    },
    // 按钮样式类（允许外部覆盖）
    btnClass: {
      type: String,
      value: "btn btn-primary",
    },
  },

  methods: {
    // 点击后跳转到 ocr-import 完整页面（主体 OCR 流程）
    onTap: function () {
      wx.navigateTo({ url: "/pages/ocr-import/ocr-import" });
    },
  },
});
