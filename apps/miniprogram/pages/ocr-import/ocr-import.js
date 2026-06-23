// pages/ocr-import/ocr-import.js
// OCR 截图录入页（完整页面，非组件）。
//
// 设计：miniprogram-web-parity / design.md「OCR 上传方案」序列图。
//
// Requirements:
//   6.1 wx.chooseMedia 选图（相册/相机）
//   6.2 wx.uploadFile 以 preview=true 调 /api/ocr
//   6.3 截图来源：支付宝「全部持有」总览
//   6.4 展示可编辑的识别结果（基金代码 / 名称 / 金额）
//   6.5 确认调 POST /api/portfolio/apply-holdings 并刷新持仓看板缓存
//   6.6 holding_warnings 展示供用户确认或修正
//   6.7 wx.uploadFile 以 multipart 提交（BC2 直传），数据口径与 Web 等价
//
// 状态机：
//   idle     → 来源选择 + 「选择截图」按钮
//   choosing → wx.chooseMedia 系统选图弹层
//   uploading→ 上传中（loading）
//   editing  → 展示可编辑识别结果（holdings + warnings）
//   saving   → 调 apply-holdings 中
//   done     → 成功，导航回持仓看板
//   error    → 上传/保存失败，展示错误 + 重试

var api = require("../../utils/api");
var cache = require("../../utils/cache");

// 截图来源：仅支付宝「全部持有」
var SOURCE_OPTIONS = [
  { label: "支付宝全部持有", value: "alipay" },
];

Page({
  data: {
    // 来源选择
    sourceOptions: SOURCE_OPTIONS,
    sourceIndex: 0,          // 当前选中来源下标

    // 选图后的临时路径
    filePath: "",
    fileThumb: "",           // 缩略图临时路径（wx.chooseMedia 返回的 thumbTempFilePath）

    // 状态
    phase: "idle",           // idle | uploading | editing | saving | error
    errorMsg: "",
    uploadProgress: 0,       // 上传进度（0–100）

    // 识别结果（Req 6.4）
    holdings: [],            // [{ fund_code, fund_name, amount }]
    warnings: [],            // holding_warnings（Req 6.6）
    rawResult: null,         // 完整 OCR 响应（调试备用）

    // 是否显示 warnings 面板
    showWarnings: false,
  },

  onLoad: function () {
    // 登录守卫
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
    }
  },

  // ─── 来源选择（Req 6.3） ────────────────────────────────────────
  onSourceChange: function (e) {
    // WXML 使用 bindtap + data-index，从 dataset 取下标（Req 6.3）
    this.setData({ sourceIndex: e.currentTarget.dataset.index });
  },

  // ─── 选择截图（Req 6.1） ────────────────────────────────────────
  onChooseImage: function () {
    var self = this;
    // phase 为 uploading 或 saving 时不允许再次选图
    if (self.data.phase === "uploading" || self.data.phase === "saving") return;

    // Req 6.1：wx.chooseMedia（mediaType: image，来自相册/相机）
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      success: function (res) {
        var file = res.tempFiles && res.tempFiles[0];
        if (!file) return;
        var tempFilePath = file.tempFilePath;
        var thumbTempFilePath = file.thumbTempFilePath || tempFilePath;
        self.setData({
          filePath: tempFilePath,
          fileThumb: thumbTempFilePath,
          phase: "uploading",
          errorMsg: "",
          holdings: [],
          warnings: [],
          showWarnings: false,
          uploadProgress: 0,
        });
        // 立即上传
        self._uploadOcr(tempFilePath);
      },
      fail: function (err) {
        // 用户取消选图不展示错误
        if (err && err.errMsg && err.errMsg.indexOf("cancel") !== -1) return;
        self.setData({
          phase: "error",
          errorMsg: "选图失败，请重试",
        });
      },
    });
  },

  // ─── 上传 OCR（Req 6.2 / 6.7 BC2 直传） ─────────────────────────
  _uploadOcr: function (filePath) {
    var self = this;
    // api.parseOcrUpload → uploadFile({ preview: true }) → wx.uploadFile
    // formData: { preview: "true" }（BC2：multipart file + preview 字段）
    api.parseOcrUpload(filePath, { preview: true })
      .then(function (result) {
        // result 应含 holdings / holding_warnings / fund_code_resolutions 等
        var holdings = (result && result.holdings) || [];
        // 规范化为可编辑结构（Req 6.4）
        var editableHoldings = holdings.map(function (h, i) {
          return {
            _key: String(i),
            fund_code: h.fund_code || "",
            fund_name: h.fund_name || "",
            // amount 优先取 holding_amount，回退 amount
            amount: String(
              h.holding_amount != null ? h.holding_amount :
              (h.amount != null ? h.amount : "")
            ),
          };
        });
        var warnings = (result && result.holding_warnings) || [];
        self.setData({
          phase: "editing",
          holdings: editableHoldings,
          warnings: warnings,
          showWarnings: warnings.length > 0,
          rawResult: result,
          uploadProgress: 100,
        });
      })
      .catch(function (err) {
        self.setData({
          phase: "error",
          errorMsg: (err && err.message) || "截图识别失败，请重试",
          uploadProgress: 0,
        });
      });
  },

  // ─── 编辑识别结果（Req 6.4） ─────────────────────────────────────
  // 基金代码编辑
  onFundCodeInput: function (e) {
    var index = e.currentTarget.dataset.index;
    var holdings = this.data.holdings.slice();
    holdings[index] = Object.assign({}, holdings[index], {
      fund_code: e.detail.value,
    });
    this.setData({ holdings: holdings });
  },

  // 基金名称编辑
  onFundNameInput: function (e) {
    var index = e.currentTarget.dataset.index;
    var holdings = this.data.holdings.slice();
    holdings[index] = Object.assign({}, holdings[index], {
      fund_name: e.detail.value,
    });
    this.setData({ holdings: holdings });
  },

  // 金额编辑
  onAmountInput: function (e) {
    var index = e.currentTarget.dataset.index;
    var holdings = this.data.holdings.slice();
    holdings[index] = Object.assign({}, holdings[index], {
      amount: e.detail.value,
    });
    this.setData({ holdings: holdings });
  },

  // 删除某条
  onRemoveHolding: function (e) {
    var index = e.currentTarget.dataset.index;
    var holdings = this.data.holdings.slice();
    holdings.splice(index, 1);
    this.setData({ holdings: holdings });
  },

  // 展开/收起 warnings 面板（Req 6.6）
  onToggleWarnings: function () {
    this.setData({ showWarnings: !this.data.showWarnings });
  },

  // ─── 重新选图（从 editing 或 error 状态重选） ───────────────────
  onReselect: function () {
    this.setData({
      phase: "idle",
      filePath: "",
      fileThumb: "",
      holdings: [],
      warnings: [],
      errorMsg: "",
      showWarnings: false,
      uploadProgress: 0,
    });
  },

  // ─── 确认写入（Req 6.5） ─────────────────────────────────────────
  onConfirm: function () {
    var self = this;
    var holdings = this.data.holdings;
    if (!holdings.length) {
      wx.showToast({ title: "无可录入持仓", icon: "none" });
      return;
    }

    // 校验：金额必须是有效数字
    for (var i = 0; i < holdings.length; i++) {
      var amt = parseFloat(holdings[i].amount);
      if (isNaN(amt) || amt < 0) {
        wx.showToast({
          title: "第 " + (i + 1) + " 条金额不合法",
          icon: "none",
          duration: 2000,
        });
        return;
      }
    }

    this.setData({ phase: "saving", errorMsg: "" });

    // 构造 apply-holdings 载荷（Req 6.5）
    var payload = holdings.map(function (h) {
      return {
        fund_code: h.fund_code.trim(),
        fund_name: h.fund_name.trim(),
        holding_amount: parseFloat(h.amount),
      };
    });

    // Req 6.5：POST /api/portfolio/apply-holdings
    api.applyPortfolioHoldings(payload)
      .then(function (result) {
        // Req 6.5：刷新持仓看板缓存（用新返回的完整数据覆写缓存键）
        if (result) {
          cache.set(cache.KEYS.holdings, result);
        } else {
          // apply 无返回时清除缓存，迫使看板页重新拉取
          cache.remove(cache.KEYS.holdings);
        }
        self.setData({ phase: "idle" });
        wx.showToast({ title: "录入成功", icon: "success", duration: 1500 });
        // 延迟跳回持仓看板
        setTimeout(function () {
          wx.switchTab({ url: "/pages/holdings/holdings" });
        }, 1600);
      })
      .catch(function (err) {
        self.setData({
          phase: "editing",   // 回到编辑态，允许用户修正后重试
          errorMsg: (err && err.message) || "录入失败，请检查数据后重试",
        });
      });
  },

  // ─── 错误态重试（重新上传）────────────────────────────────────────
  onRetry: function () {
    if (!this.data.filePath) {
      // 没有文件则回到 idle 重新选图
      this.setData({ phase: "idle", errorMsg: "" });
      return;
    }
    this.setData({ phase: "uploading", errorMsg: "", uploadProgress: 0 });
    this._uploadOcr(this.data.filePath);
  },
});
