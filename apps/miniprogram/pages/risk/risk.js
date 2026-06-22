// pages/risk/risk.js
// Risk_Profile_Page（风控画像与日报角色设定）—— 阶段七（Req 18）
//
// 功能：
//   1. onShow 读取 Investor_Profile：GET /api/investor-profile（Req 18.1）
//   2. 表单字段：浮亏线 / 单只集中度 / 期望投入总额 / 偏定投 / 拒绝追高（Req 18.2）
//   3. 投资预设切换：conservative_hold ↔ aggressive_swing，联动风控参数
//      （derive.applyInvestmentPreset，Req 18.3 / Property 18）
//   4. 保存风控画像：PUT /api/investor-profile（Req 18.4）
//   5. 日报 AI 角色设定：GET / PUT /api/analysis-prompt（Req 18.5）
//
// Requirements: 18.1, 18.2, 18.3, 18.4, 18.5

const api = require("../../utils/api");
const derive = require("../../utils/derive");

// 投资预设选项（与 derive.PRESET_DEFAULTS 对齐，Req 18.3）
const PRESET_OPTIONS = [
  { label: "稳健持有", value: "conservative_hold" },
  { label: "进取波段", value: "aggressive_swing" },
];

// 表单数值字段默认（GET 失败或缺字段时回退）
const DEFAULT_FORM = {
  investment_preset: "conservative_hold",
  max_drawdown_percent: "",
  concentration_limit_percent: "",
  expected_investment_amount: "",
  prefer_dca: true,
  avoid_chasing: true,
  decision_style: "conservative",
};

Page({
  data: {
    // 画像加载/保存态（state-view 用）
    loading: false,
    error: "",
    saving: false,

    // 预设选项
    presetOptions: PRESET_OPTIONS,
    presetIndex: 0, // 当前选中预设下标

    // 表单字段（Req 18.2）—— 数值以 string 存储便于输入框绑定
    form: Object.assign({}, DEFAULT_FORM),

    // 日报 AI 角色设定（Req 18.5）
    rolePrompt: "",
    promptLoading: false,
    promptSaving: false,
  },

  // ---------------------------------------------------------------------------
  // 生命周期
  // ---------------------------------------------------------------------------

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this._loadProfile();
    this._loadAnalysisPrompt();
  },

  // ---------------------------------------------------------------------------
  // 读取 Investor_Profile（Req 18.1）
  // ---------------------------------------------------------------------------

  _loadProfile: function () {
    var self = this;
    self.setData({ loading: true, error: "" });
    api
      .fetchInvestorProfile()
      .then(function (data) {
        var profile = data && data.profile ? data.profile : data || {};
        self.setData({
          loading: false,
          form: self._profileToForm(profile),
          presetIndex: self._presetIndexOf(profile.investment_preset),
        });
      })
      .catch(function (err) {
        var msg = (err && err.message) || "加载风控画像失败";
        self.setData({ loading: false, error: msg });
      });
  },

  onRetry: function () {
    this._loadProfile();
  },

  // 日报角色设定（Req 18.5）
  _loadAnalysisPrompt: function () {
    var self = this;
    self.setData({ promptLoading: true });
    api
      .fetchAnalysisPrompt()
      .then(function (data) {
        var prompt = (data && data.role_prompt) || "";
        self.setData({ rolePrompt: prompt, promptLoading: false });
      })
      .catch(function () {
        self.setData({ promptLoading: false });
      });
  },

  // ---------------------------------------------------------------------------
  // 预设切换联动（Req 18.3 / Property 18）
  // ---------------------------------------------------------------------------

  onPresetChange: function (e) {
    var idx = e.currentTarget
      ? Number(e.currentTarget.dataset.value)
      : Number(e.detail.value);
    if (!Number.isFinite(idx) || idx < 0 || idx >= PRESET_OPTIONS.length) return;
    var preset = PRESET_OPTIONS[idx].value;
    var applied = derive.applyInvestmentPreset(preset);
    if (!applied) {
      this.setData({ presetIndex: idx });
      return;
    }
    // 将预设字段联动应用到表单（数值转 string 绑定）。
    var form = Object.assign({}, this.data.form, {
      investment_preset: applied.investment_preset,
      max_drawdown_percent: this._numToInput(applied.max_drawdown_percent),
      concentration_limit_percent: this._numToInput(applied.concentration_limit_percent),
      prefer_dca: !!applied.prefer_dca,
      avoid_chasing: !!applied.avoid_chasing,
      decision_style: applied.decision_style,
    });
    this.setData({ presetIndex: idx, form: form });
  },

  // ---------------------------------------------------------------------------
  // 表单字段输入（Req 18.2）
  // ---------------------------------------------------------------------------

  onMaxDrawdownInput: function (e) {
    this.setData({ "form.max_drawdown_percent": e.detail.value || "" });
  },

  onConcentrationInput: function (e) {
    this.setData({ "form.concentration_limit_percent": e.detail.value || "" });
  },

  onExpectedAmountInput: function (e) {
    this.setData({ "form.expected_investment_amount": e.detail.value || "" });
  },

  onPreferDcaChange: function (e) {
    this.setData({ "form.prefer_dca": !!e.detail.value });
  },

  onAvoidChasingChange: function (e) {
    this.setData({ "form.avoid_chasing": !!e.detail.value });
  },

  // ---------------------------------------------------------------------------
  // 保存风控画像（Req 18.4）
  // ---------------------------------------------------------------------------

  onSaveProfile: function () {
    var self = this;
    if (self.data.saving) return;
    var profile = self._formToProfile(self.data.form);

    // 数值合法性校验：浮亏线/集中度若填写须为有效非负数。
    if (profile._invalid) {
      wx.showToast({ title: profile._invalidMsg || "输入格式有误", icon: "none" });
      return;
    }
    delete profile._invalid;
    delete profile._invalidMsg;

    self.setData({ saving: true });
    wx.showLoading({ title: "保存中…" });
    api
      .saveInvestorProfileRemote(profile)
      .then(function () {
        wx.hideLoading();
        self.setData({ saving: false });
        wx.showToast({ title: "风控画像已保存", icon: "success" });
      })
      .catch(function (err) {
        wx.hideLoading();
        self.setData({ saving: false });
        var msg = (err && err.message) || "保存失败，请重试";
        wx.showToast({ title: msg, icon: "none" });
      });
  },

  // ---------------------------------------------------------------------------
  // 日报角色设定保存（Req 18.5）
  // ---------------------------------------------------------------------------

  onPromptInput: function (e) {
    this.setData({ rolePrompt: e.detail.value || "" });
  },

  onSavePrompt: function () {
    var self = this;
    if (self.data.promptSaving) return;
    self.setData({ promptSaving: true });
    wx.showLoading({ title: "保存中…" });
    api
      .saveAnalysisPromptRemote(self.data.rolePrompt || "")
      .then(function () {
        wx.hideLoading();
        self.setData({ promptSaving: false });
        wx.showToast({ title: "角色设定已保存", icon: "success" });
      })
      .catch(function (err) {
        wx.hideLoading();
        self.setData({ promptSaving: false });
        var msg = (err && err.message) || "保存失败，请重试";
        wx.showToast({ title: msg, icon: "none" });
      });
  },

  // ---------------------------------------------------------------------------
  // 辅助：profile ↔ form 转换
  // ---------------------------------------------------------------------------

  _profileToForm: function (profile) {
    var p = profile || {};
    return {
      investment_preset: p.investment_preset || "conservative_hold",
      max_drawdown_percent: this._numToInput(p.max_drawdown_percent),
      concentration_limit_percent: this._numToInput(p.concentration_limit_percent),
      expected_investment_amount: this._numToInput(p.expected_investment_amount),
      prefer_dca: p.prefer_dca != null ? !!p.prefer_dca : true,
      avoid_chasing: p.avoid_chasing != null ? !!p.avoid_chasing : true,
      decision_style: p.decision_style || "conservative",
    };
  },

  _formToProfile: function (form) {
    var f = form || {};
    var result = {
      investment_preset: f.investment_preset || "conservative_hold",
      prefer_dca: !!f.prefer_dca,
      avoid_chasing: !!f.avoid_chasing,
      decision_style: f.decision_style || "conservative",
    };

    var drawdown = this._inputToNum(f.max_drawdown_percent);
    if (f.max_drawdown_percent !== "" && drawdown === null) {
      return { _invalid: true, _invalidMsg: "浮亏线格式有误" };
    }
    result.max_drawdown_percent = drawdown;

    var concentration = this._inputToNum(f.concentration_limit_percent);
    if (f.concentration_limit_percent !== "" && concentration === null) {
      return { _invalid: true, _invalidMsg: "单只集中度格式有误" };
    }
    result.concentration_limit_percent = concentration;

    var amount = this._inputToNum(f.expected_investment_amount);
    if (f.expected_investment_amount !== "" && amount === null) {
      return { _invalid: true, _invalidMsg: "期望投入金额格式有误" };
    }
    result.expected_investment_amount = amount;

    return result;
  },

  // 数值 → 输入框字符串（null/非有限数 → 空串）
  _numToInput: function (v) {
    if (v == null) return "";
    var n = Number(v);
    return Number.isFinite(n) ? String(n) : "";
  },

  // 输入框字符串 → 数值（空串 → null；非法或负数 → null 视为无效）
  _inputToNum: function (s) {
    if (s == null || s === "") return null;
    var n = Number(s);
    if (!Number.isFinite(n) || n < 0) return null;
    return n;
  },

  // 预设 value → 下标
  _presetIndexOf: function (preset) {
    for (var i = 0; i < PRESET_OPTIONS.length; i++) {
      if (PRESET_OPTIONS[i].value === preset) return i;
    }
    return 0;
  },
});
