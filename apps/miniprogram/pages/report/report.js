// pages/report/report.js
// 生成日报页（Daily_Report_Page）
//
// 功能（阶段五 Task 6.8 + 6.9）：
//   1. 配置区：风控画像/投资预设 selector、AI 角色设定、分析模式（快速/深度）（Req 13.1）
//   2. 新闻预览：POST /api/news/preview（Req 13.2）
//   3. 生成日报：POST /api/analyze/async 建任务，轮询 GET /api/jobs/{id}（Req 13.3, 13.4）
//   4. 完成时 md-view 渲染日报正文（Req 13.5, 13.6）
//   5. 调仓示意 GET /api/reports/{id}/rebalance-simulation（Req 13.7）
//   6. 复盘 GET /api/reports/{id}/outcomes（Req 13.8）
//   7. 导出 Markdown GET /api/reports/{id}/markdown（Req 13.9）
//   8. 失败展示错误并允许重新生成（Req 13.10）
//   9. 嵌入 chat-panel 追问（Req 14）
//  10. 「更多」二级入口：盈亏分析/历史日报/账号设置/风控设置/盯盘（Req 3.2）
//
// Requirements: 13.1–13.10, 14（通过 chat-panel）, 3.2

const api = require('../../utils/api');
const derive = require('../../utils/derive');

// 「更多」二级入口（非 Tab 页，经 wx.navigateTo 跳转，Req 3.2）
const MORE_ROUTES = {
  profit:   '/pages/profit/profit',
  history:  '/pages/history/history',
  settings: '/pages/settings/settings',
  risk:     '/pages/risk/risk',
  swing:    '/pages/swing/swing',
};

// 轮询间隔（毫秒）
const POLL_INTERVAL_MS = 3000;

// 分析模式选项
const MODE_OPTIONS = [
  { label: '快速', value: 'fast' },
  { label: '深度', value: 'deep' },
];

// 风控画像/投资预设选项
const PRESET_OPTIONS = [
  { label: '均衡持有', value: 'conservative_hold' },
  { label: '积极波段', value: 'aggressive_swing' },
];

Page({
  data: {
    // ── 配置区 ─────────────────────────────────────────
    presets: PRESET_OPTIONS,
    presetIndex: 0,                    // 选中的预设下标
    modeOptions: MODE_OPTIONS,
    modeIndex: 1,                      // 默认深度
    systemRolePrompt: '',              // AI 角色设定（可编辑）
    promptLoading: false,              // 拉取 analysisPrompt 中

    // ── 新闻预览 ────────────────────────────────────────
    newsPreview: null,
    newsLoading: false,
    newsError: '',

    // ── 持仓（用于传递给 API）──────────────────────────
    holdings: [],
    holdingsLoading: false,

    // ── 任务状态 ────────────────────────────────────────
    // 'idle' | 'submitting' | 'polling' | 'completed' | 'failed'
    jobPhase: 'idle',
    jobId: '',
    jobStage: '',
    jobStageLabel: '',
    jobError: '',

    // ── 完成后的日报 ────────────────────────────────────
    report: null,                      // 完整 report 对象
    reportMarkdown: '',                // md-view 渲染的正文

    // ── 调仓示意 ────────────────────────────────────────
    rebalanceData: null,
    rebalanceLoading: false,
    rebalanceError: '',
    showRebalance: false,

    // ── 复盘 ────────────────────────────────────────────
    outcomesData: null,
    outcomesLoading: false,
    outcomesError: '',
    showOutcomes: false,

    // ── 「更多」入口 ────────────────────────────────────
    moreItems: [
      { key: 'profit',   label: '盈亏分析' },
      { key: 'history',  label: '历史日报' },
      { key: 'settings', label: '账号设置' },
      { key: 'risk',     label: '风控设置' },
      { key: 'swing',    label: '盯盘' },
    ],

    // ── 追问面板 ────────────────────────────────────────
    showChat: false,              // 是否展开追问面板
    chatReportId: '',             // 传给 chat-panel 的 reportId
  },

  // ── 轮询 timer handle ────────────────────────────────
  _pollTimer: null,

  // ---------------------------------------------------------------------------
  // 生命周期
  // ---------------------------------------------------------------------------

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    // 首次进入时加载：持仓、AI 角色设定
    if (this.data.holdings.length === 0) {
      this._loadHoldings();
    }
    if (!this.data.systemRolePrompt) {
      this._loadAnalysisPrompt();
    }
  },

  onUnload: function () {
    this._stopPolling();
  },

  // ---------------------------------------------------------------------------
  // 初始化数据
  // ---------------------------------------------------------------------------

  _loadHoldings: function () {
    var self = this;
    self.setData({ holdingsLoading: true });
    api.fetchPortfolioHoldings()
      .then(function (data) {
        var list = (data && data.holdings) || data || [];
        self.setData({ holdings: Array.isArray(list) ? list : [], holdingsLoading: false });
      })
      .catch(function () {
        self.setData({ holdingsLoading: false });
      });
  },

  _loadAnalysisPrompt: function () {
    var self = this;
    self.setData({ promptLoading: true });
    api.fetchAnalysisPrompt()
      .then(function (data) {
        var prompt = (data && data.role_prompt) || '';
        self.setData({ systemRolePrompt: prompt, promptLoading: false });
      })
      .catch(function () {
        self.setData({ promptLoading: false });
      });
  },

  // ---------------------------------------------------------------------------
  // 配置区交互
  // ---------------------------------------------------------------------------

  /** 切换投资预设（Req 13.1） */
  onPresetChange: function (e) {
    this.setData({ presetIndex: Number(e.detail.value) });
  },

  /** 切换分析模式（Req 13.1）—— 分段控件 bindtap，读取 dataset.value 作为下标 */
  onModeChange: function (e) {
    // 分段控件通过 bindtap + data-value="{{index}}" 传递下标
    var idx = e.currentTarget ? Number(e.currentTarget.dataset.value) : Number(e.detail.value);
    this.setData({ modeIndex: idx });
  },

  /** AI 角色设定输入 */
  onPromptInput: function (e) {
    this.setData({ systemRolePrompt: e.detail.value || '' });
  },

  // ---------------------------------------------------------------------------
  // 新闻预览（Req 13.2）
  // ---------------------------------------------------------------------------

  onPreviewNews: function () {
    var self = this;
    var holdings = self.data.holdings;
    if (!holdings || holdings.length === 0) {
      wx.showToast({ title: '请先加载持仓', icon: 'none' });
      return;
    }
    var profile = self._buildProfile();
    self.setData({ newsLoading: true, newsError: '' });
    api.previewNewsForHoldings(holdings, profile)
      .then(function (data) {
        self.setData({ newsPreview: data, newsLoading: false });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '获取新闻预览失败';
        self.setData({ newsLoading: false, newsError: msg });
      });
  },

  // ---------------------------------------------------------------------------
  // 生成日报（Req 13.3, 13.4, 13.5, 13.6, 13.10）
  // ---------------------------------------------------------------------------

  onGenerate: function () {
    var self = this;
    if (self.data.jobPhase === 'submitting' || self.data.jobPhase === 'polling') return;

    var holdings = self.data.holdings;
    if (!holdings || holdings.length === 0) {
      wx.showToast({ title: '暂无持仓，无法生成日报', icon: 'none' });
      return;
    }

    var profile = self._buildProfile();
    var mode = MODE_OPTIONS[self.data.modeIndex].value;
    var prompt = self.data.systemRolePrompt || '';

    self.setData({
      jobPhase: 'submitting',
      jobId: '',
      jobStage: '',
      jobStageLabel: '',
      jobError: '',
      report: null,
      reportMarkdown: '',
      showChat: false,
      chatReportId: '',
      showRebalance: false,
      rebalanceData: null,
      showOutcomes: false,
      outcomesData: null,
    });

    // POST /api/analyze/async（Req 13.3）
    api.startAnalyzeJob(holdings, profile, '', mode, prompt)
      .then(function (jobId) {
        if (!jobId) {
          self.setData({ jobPhase: 'failed', jobError: '创建任务失败（未返回 job_id）' });
          return;
        }
        self.setData({ jobId: jobId, jobPhase: 'polling' });
        self._startPolling(jobId);
      })
      .catch(function (err) {
        var msg = (err && err.message) || '提交生成请求失败';
        self.setData({ jobPhase: 'failed', jobError: msg });
      });
  },

  /** 失败后重新生成（Req 13.10） */
  onReGenerate: function () {
    this.setData({
      jobPhase: 'idle',
      jobId: '',
      jobError: '',
      report: null,
      reportMarkdown: '',
    });
    this.onGenerate();
  },

  // ---------------------------------------------------------------------------
  // 轮询（Req 13.4）
  // ---------------------------------------------------------------------------

  _startPolling: function (jobId) {
    var self = this;
    self._stopPolling();

    function poll () {
      api.fetchAnalysisJob(jobId)
        .then(function (job) {
          if (!job) return;
          var stage = job.stage || '';
          var stageLabel = derive.getStagLabel(stage) || stage;
          self.setData({ jobStage: stage, jobStageLabel: stageLabel });

          var decision = derive.pollDecision(job.status);
          if (decision === derive.POLL_STOP) {
            self._stopPolling();
            if (job.status === 'completed') {
              self._onJobCompleted(job);
            } else {
              var msg = (job.error) || '日报生成失败，请重试';
              self.setData({ jobPhase: 'failed', jobError: msg });
            }
          } else {
            // 继续轮询
            self._pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          }
        })
        .catch(function (err) {
          // 轮询网络错误时继续重试（不立即 fail）
          console.warn('[report] 轮询出错，将重试：', err && err.message);
          self._pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
        });
    }

    poll();
  },

  _stopPolling: function () {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
  },

  // ---------------------------------------------------------------------------
  // 任务完成（Req 13.5, 13.6）
  // ---------------------------------------------------------------------------

  _onJobCompleted: function (job) {
    var report = job.report || null;
    var markdown = '';
    if (report) {
      markdown = report.body || report.markdown || report.content || '';
    }
    this.setData({
      jobPhase: 'completed',
      report: report,
      reportMarkdown: markdown,
      chatReportId: report ? (report.id || '') : '',
      showChat: false,
    });
  },

  // ---------------------------------------------------------------------------
  // 调仓示意（Req 13.7）
  // ---------------------------------------------------------------------------

  onShowRebalance: function () {
    var self = this;
    if (!self.data.report || !self.data.report.id) return;
    if (self.data.showRebalance) {
      self.setData({ showRebalance: false });
      return;
    }
    if (self.data.rebalanceData) {
      self.setData({ showRebalance: true });
      return;
    }
    self.setData({ rebalanceLoading: true, rebalanceError: '' });
    api.fetchRebalanceSimulation(self.data.report.id)
      .then(function (data) {
        self.setData({ rebalanceData: data, rebalanceLoading: false, showRebalance: true });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '加载调仓示意失败';
        self.setData({ rebalanceLoading: false, rebalanceError: msg });
      });
  },

  // ---------------------------------------------------------------------------
  // 复盘（Req 13.8）
  // ---------------------------------------------------------------------------

  onShowOutcomes: function () {
    var self = this;
    if (!self.data.report || !self.data.report.id) return;
    if (self.data.showOutcomes) {
      self.setData({ showOutcomes: false });
      return;
    }
    if (self.data.outcomesData) {
      self.setData({ showOutcomes: true });
      return;
    }
    self.setData({ outcomesLoading: true, outcomesError: '' });
    api.fetchReportOutcomes(self.data.report.id)
      .then(function (data) {
        self.setData({ outcomesData: data, outcomesLoading: false, showOutcomes: true });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '加载复盘失败';
        self.setData({ outcomesLoading: false, outcomesError: msg });
      });
  },

  // ---------------------------------------------------------------------------
  // 导出 Markdown（Req 13.9）
  // ---------------------------------------------------------------------------

  onExportMarkdown: function () {
    var self = this;
    if (!self.data.report || !self.data.report.id) return;
    wx.showLoading({ title: '导出中…' });
    api.fetchReportMarkdown(self.data.report.id)
      .then(function (md) {
        wx.hideLoading();
        if (md) {
          wx.setClipboardData({
            data: md,
            success: function () {
              wx.showToast({ title: 'Markdown 已复制到剪贴板', icon: 'success' });
            },
          });
        } else {
          wx.showToast({ title: '暂无 Markdown 内容', icon: 'none' });
        }
      })
      .catch(function (err) {
        wx.hideLoading();
        var msg = (err && err.message) || '导出失败';
        wx.showToast({ title: msg, icon: 'none' });
      });
  },

  // ---------------------------------------------------------------------------
  // 追问面板（Req 14）
  // ---------------------------------------------------------------------------

  onToggleChat: function () {
    var show = !this.data.showChat;
    this.setData({ showChat: show });
  },

  // ---------------------------------------------------------------------------
  // 「更多」二级入口（Req 3.2）
  // ---------------------------------------------------------------------------

  onMore: function (e) {
    var target = e.currentTarget.dataset.target;
    var url = MORE_ROUTES[target];
    if (url) {
      wx.navigateTo({ url: url });
    }
  },

  // ---------------------------------------------------------------------------
  // 辅助：构造 profile 参数
  // ---------------------------------------------------------------------------

  _buildProfile: function () {
    var preset = PRESET_OPTIONS[this.data.presetIndex].value;
    var defaults = derive.applyInvestmentPreset(preset) || {};
    return Object.assign({ investment_preset: preset }, defaults);
  },
});
