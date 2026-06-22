// pages/discovery/discovery.js
// Discovery_Page（推荐基金）—— 阶段六（Req 15 + 16）
//
// 功能：
//   1. 配置区：扫描模式 / 关注方向（≤3）/ 选基策略 / 基金类型偏好 / 预算 / 分析模式（Req 15.1）
//   2. 关注方向候选：GET /api/fund-discovery/sectors（Req 15.2）
//   3. 荐基 AI 角色设定：GET /PUT /api/discovery-prompt（Req 15.3）
//   4. 提交扫描：POST /api/fund-discovery/async 建任务（Req 15.4）
//   5. 轮询进度：GET /api/jobs/{id}（Req 15.5）
//   6. 荐基报告：md-view 渲染（市场观点/候选池/推荐列表/风险）（Req 15.6）
//   7. 推荐基金详情预览（Req 15.7）
//   8. 历史荐基报告：列表/多选/批量删除（Req 16.1, 16.2）
//   9. 复盘：GET .../outcomes（Req 16.3）
//  10. 历史追问：GET .../chat + POST .../chat/sync 非流式（Req 16.4–16.6）
//
// Requirements: 15.1–15.7, 16.1–16.6

const api = require('../../utils/api');
const derive = require('../../utils/derive');
const navState = require('../../utils/nav-state');

// 轮询间隔（毫秒）
const POLL_INTERVAL_MS = 3000;

// 扫描模式选项（Req 15.1）
const SCAN_MODES = [
  { label: '全市场', value: 'full_market' },
  { label: '持仓补全', value: 'portfolio_gap' },
  { label: '大跌波段', value: 'dip_swing' },
];

// 选基策略选项（Req 15.1）
const STRATEGY_OPTIONS = [
  { label: '均衡', value: 'balanced' },
  { label: '成长', value: 'growth' },
  { label: '价值', value: 'value' },
  { label: '波段', value: 'swing' },
];

// 基金类型偏好（Req 15.1）
const FUND_TYPE_OPTIONS = [
  { label: '不限', value: 'any' },
  { label: '宽基指数', value: 'broad_index' },
  { label: '行业 ETF', value: 'sector_etf' },
  { label: '主动基金', value: 'active' },
];

// 分析模式（Req 15.1）
const MODE_OPTIONS = [
  { label: '快速', value: 'fast' },
  { label: '深度', value: 'deep' },
];

Page({
  data: {
    // ── 子视图切换（'scan' | 'history'）────────────────
    subView: 'scan',

    // ── 配置区（Req 15.1）──────────────────────────────
    scanModes: SCAN_MODES,
    scanModeIndex: 0,                  // 对应 SCAN_MODES 下标

    // 关注方向（≤3 个）
    focusSectors: [],                  // 已选关注方向 string[]
    sectorsAvailable: [],              // API 返回的候选板块 { sector_name, ... }[]
    sectorsLoading: false,

    // 选基策略
    strategyOptions: STRATEGY_OPTIONS,
    strategyIndex: 0,

    // 基金类型偏好
    fundTypeOptions: FUND_TYPE_OPTIONS,
    fundTypeIndex: 0,

    // 预算（元）
    budgetYuan: '',                    // string，提交时转 Number 或 null

    // 分析模式
    modeOptions: MODE_OPTIONS,
    modeIndex: 1,                      // 默认深度

    // AI 角色设定（Req 15.3）
    systemRolePrompt: '',
    promptLoading: false,

    // ── 持仓（传给 API）──────────────────────────────
    holdings: [],
    holdingsLoading: false,

    // ── 任务状态（Req 15.4, 15.5）──────────────────
    // 'idle' | 'submitting' | 'polling' | 'completed' | 'failed'
    jobPhase: 'idle',
    jobId: '',
    jobStage: '',
    jobStageLabel: '',
    jobError: '',

    // ── 完成后的荐基报告（Req 15.6）────────────────
    discoveryReport: null,
    reportMarkdown: '',

    // ── 推荐基金详情预览（Req 15.7）────────────────
    previewFund: null,               // 当前预览的推荐基金对象
    showFundPreview: false,

    // ── 追问面板（Req 16.4–16.6）───────────────────
    showChat: false,
    chatReportId: '',

    // ── 历史荐基报告（Req 16.1）────────────────────
    historyList: [],                 // FundDiscoveryReport[]
    historyLoading: false,
    historyError: '',

    // ── 多选删除（Req 16.2）────────────────────────
    selectedIds: {},                 // { [reportId]: true }
    selectedCount: 0,                // 已选数量（WXML 无管道过滤，需在 JS 维护）
    isSelecting: false,              // 是否进入多选模式
    batchDeleting: false,

    // ── 复盘（Req 16.3）────────────────────────────
    showOutcomes: false,
    outcomesReportId: '',
    outcomesData: null,
    outcomesLoading: false,
    outcomesError: '',
  },

  // 轮询 timer handle
  _pollTimer: null,

  // ---------------------------------------------------------------------------
  // 生命周期
  // ---------------------------------------------------------------------------

  onLoad: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    // 读取 navState 预填（Req 11.5 / 10.7）
    var presetScanMode = navState.getDiscoveryScanMode();
    var presetFocusSectors = navState.getDiscoveryFocusSectors();

    // 清除用完的 navState（避免下次进入仍保留旧值）
    navState.clearDiscoveryScanMode();
    navState.clearDiscoveryFocusSectors();

    // 应用预填扫描模式
    var scanModeIndex = 0;
    if (presetScanMode) {
      for (var i = 0; i < SCAN_MODES.length; i++) {
        if (SCAN_MODES[i].value === presetScanMode) {
          scanModeIndex = i;
          break;
        }
      }
    }

    // 恢复 subView（Req 3.5）
    var savedSubView = navState.getSubView('discovery') || 'scan';

    this.setData({
      scanModeIndex: scanModeIndex,
      focusSectors: Array.isArray(presetFocusSectors) ? presetFocusSectors : [],
      subView: savedSubView,
    });

    this._loadHoldings();
    this._loadDiscoveryPrompt();
    this._loadSectors();

    if (savedSubView === 'history') {
      this._loadHistory();
    }
  },

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: '/pages/login/login' });
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
        self.setData({
          holdings: Array.isArray(list) ? list : [],
          holdingsLoading: false,
        });
      })
      .catch(function () {
        self.setData({ holdingsLoading: false });
      });
  },

  _loadDiscoveryPrompt: function () {
    var self = this;
    self.setData({ promptLoading: true });
    api.fetchDiscoveryPrompt()
      .then(function (data) {
        var prompt = (data && data.role_prompt) || '';
        self.setData({ systemRolePrompt: prompt, promptLoading: false });
      })
      .catch(function () {
        self.setData({ promptLoading: false });
      });
  },

  // 关注方向候选（Req 15.2）
  _loadSectors: function () {
    var self = this;
    self.setData({ sectorsLoading: true });
    api.fetchDiscoverySectors()
      .then(function (sectors) {
        // fetchDiscoverySectors 已提取 sectors 数组
        self.setData({
          sectorsAvailable: Array.isArray(sectors) ? sectors : [],
          sectorsLoading: false,
        });
      })
      .catch(function () {
        self.setData({ sectorsLoading: false });
      });
  },

  // ---------------------------------------------------------------------------
  // 配置区交互（Req 15.1）
  // ---------------------------------------------------------------------------

  /** 切换扫描模式 */
  onScanModeChange: function (e) {
    var idx = Number(e.detail.value);
    this.setData({ scanModeIndex: idx });
  },

  /** 切换选基策略 */
  onStrategyChange: function (e) {
    this.setData({ strategyIndex: Number(e.detail.value) });
  },

  /** 切换基金类型偏好 */
  onFundTypeChange: function (e) {
    this.setData({ fundTypeIndex: Number(e.detail.value) });
  },

  /** 切换分析模式（分段控件，data-value 为下标） */
  onModeChange: function (e) {
    var idx = e.currentTarget
      ? Number(e.currentTarget.dataset.value)
      : Number(e.detail.value);
    this.setData({ modeIndex: idx });
  },

  /** 预算输入 */
  onBudgetInput: function (e) {
    this.setData({ budgetYuan: e.detail.value || '' });
  },

  /** AI 角色设定输入 */
  onPromptInput: function (e) {
    this.setData({ systemRolePrompt: e.detail.value || '' });
  },

  /** 保存角色设定到远端（Req 15.3） */
  onSavePrompt: function () {
    var self = this;
    var prompt = self.data.systemRolePrompt || '';
    wx.showLoading({ title: '保存中…' });
    api.saveDiscoveryPromptRemote(prompt)
      .then(function () {
        wx.hideLoading();
        wx.showToast({ title: '角色设定已保存', icon: 'success' });
      })
      .catch(function (err) {
        wx.hideLoading();
        var msg = (err && err.message) || '保存失败';
        wx.showToast({ title: msg, icon: 'none' });
      });
  },

  // ---------------------------------------------------------------------------
  // 关注方向管理（≤3，Req 15.1 / 10.7）
  // ---------------------------------------------------------------------------

  /** 从候选板块列表添加关注方向 */
  onAddFocusSector: function (e) {
    var sector = e.currentTarget.dataset.sector;
    if (!sector) return;
    var current = this.data.focusSectors || [];
    var updated = derive.addFocusSector(current, sector);
    if (updated.length === current.length && current.indexOf(sector) !== -1) {
      wx.showToast({ title: '已在关注方向中', icon: 'none' });
      return;
    }
    if (updated.length === current.length) {
      wx.showToast({ title: '最多添加 3 个关注方向', icon: 'none' });
      return;
    }
    this.setData({ focusSectors: updated });
  },

  /** 移除某关注方向 */
  onRemoveFocusSector: function (e) {
    var sector = e.currentTarget.dataset.sector;
    var updated = derive.removeFocusSector(this.data.focusSectors, sector);
    this.setData({ focusSectors: updated });
  },

  // ---------------------------------------------------------------------------
  // 提交扫描（Req 15.4）
  // ---------------------------------------------------------------------------

  onStartScan: function () {
    var self = this;
    if (self.data.jobPhase === 'submitting' || self.data.jobPhase === 'polling') return;

    var holdings = self.data.holdings;
    var profile = self._buildProfile();
    var mode = MODE_OPTIONS[self.data.modeIndex].value;
    var budget = self.data.budgetYuan ? Number(self.data.budgetYuan) : null;
    if (budget !== null && (!Number.isFinite(budget) || budget <= 0)) {
      wx.showToast({ title: '预算金额格式有误', icon: 'none' });
      return;
    }

    self.setData({
      jobPhase: 'submitting',
      jobId: '',
      jobStage: '',
      jobStageLabel: '',
      jobError: '',
      discoveryReport: null,
      reportMarkdown: '',
      showFundPreview: false,
      previewFund: null,
      showChat: false,
      chatReportId: '',
    });

    api.startDiscoveryJob(holdings, profile, {
      analysisMode: mode,
      focusSectors: self.data.focusSectors || [],
      budgetYuan: budget,
      fundTypePreference: FUND_TYPE_OPTIONS[self.data.fundTypeIndex].value,
      selectionStrategy: STRATEGY_OPTIONS[self.data.strategyIndex].value,
      scanMode: SCAN_MODES[self.data.scanModeIndex].value,
      systemRolePrompt: self.data.systemRolePrompt || null,
    })
      .then(function (jobId) {
        if (!jobId) {
          self.setData({ jobPhase: 'failed', jobError: '创建任务失败（未返回 job_id）' });
          return;
        }
        self.setData({ jobId: jobId, jobPhase: 'polling' });
        self._startPolling(jobId);
      })
      .catch(function (err) {
        var msg = (err && err.message) || '提交扫描请求失败';
        self.setData({ jobPhase: 'failed', jobError: msg });
      });
  },

  /** 失败后重新扫描 */
  onReScan: function () {
    this.setData({
      jobPhase: 'idle',
      jobId: '',
      jobError: '',
      discoveryReport: null,
      reportMarkdown: '',
    });
    this.onStartScan();
  },

  // ---------------------------------------------------------------------------
  // 轮询（Req 15.5）
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
              var msg = job.error || '荐基扫描失败，请重试';
              self.setData({ jobPhase: 'failed', jobError: msg });
            }
          } else {
            self._pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          }
        })
        .catch(function (err) {
          console.warn('[discovery] 轮询出错，将重试：', err && err.message);
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
  // 任务完成（Req 15.6）
  // ---------------------------------------------------------------------------

  _onJobCompleted: function (job) {
    var report = job.discovery_report || job.report || null;
    var markdown = '';
    if (report) {
      markdown = report.body || report.markdown || report.content || '';
    }
    this.setData({
      jobPhase: 'completed',
      discoveryReport: report,
      reportMarkdown: markdown,
      chatReportId: report ? (report.id || '') : '',
      showChat: false,
    });
  },

  // ---------------------------------------------------------------------------
  // 推荐基金详情预览（Req 15.7）
  // ---------------------------------------------------------------------------

  onFundPreviewTap: function (e) {
    var fund = e.currentTarget.dataset.fund;
    if (!fund) return;
    this.setData({ previewFund: fund, showFundPreview: true });
  },

  onCloseFundPreview: function () {
    this.setData({ showFundPreview: false, previewFund: null });
  },

  onGoToFundDetail: function () {
    var fund = this.data.previewFund;
    if (!fund || !fund.fund_code) return;
    // 跳转基金详情页（Holdings_Board 的 Fund_Detail_Page）
    wx.navigateTo({
      url: '/pages/fund-detail/fund-detail?code=' + encodeURIComponent(fund.fund_code),
    });
  },

  // ---------------------------------------------------------------------------
  // 追问面板（Req 16.4–16.6）
  // ---------------------------------------------------------------------------

  onToggleChat: function () {
    this.setData({ showChat: !this.data.showChat });
  },

  // ---------------------------------------------------------------------------
  // 子视图切换（扫描 / 历史）
  // ---------------------------------------------------------------------------

  onSwitchToScan: function () {
    navState.setSubView('discovery', 'scan');
    this.setData({ subView: 'scan' });
  },

  onSwitchToHistory: function () {
    navState.setSubView('discovery', 'history');
    this.setData({ subView: 'history' });
    this._loadHistory();
  },

  // ---------------------------------------------------------------------------
  // 历史荐基报告（Req 16.1）
  // ---------------------------------------------------------------------------

  /** 加载历史报告列表 */
  _loadHistory: function () {
    var self = this;
    self.setData({ historyLoading: true, historyError: '' });
    api.listDiscoveryReports()
      .then(function (data) {
        // API 可能返回数组或 { reports: [] } 形状
        var list = Array.isArray(data) ? data : (data && data.reports) || [];
        self.setData({ historyList: list, historyLoading: false });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '加载历史失败';
        self.setData({ historyLoading: false, historyError: msg });
      });
  },

  onRetryHistory: function () {
    this._loadHistory();
  },

  // ---------------------------------------------------------------------------
  // 多选（Req 16.2）
  // ---------------------------------------------------------------------------

  /** 进入/退出多选模式 */
  onToggleSelectMode: function () {
    var entering = !this.data.isSelecting;
    this.setData({
      isSelecting: entering,
      selectedIds: {},
      selectedCount: 0,
    });
  },

  /** 切换单条报告的选中状态 */
  onToggleSelectReport: function (e) {
    if (!this.data.isSelecting) return;
    var id = e.currentTarget.dataset.id;
    if (!id) return;
    var sel = Object.assign({}, this.data.selectedIds);
    if (sel[id]) {
      delete sel[id];
    } else {
      sel[id] = true;
    }
    this.setData({ selectedIds: sel, selectedCount: Object.keys(sel).length });
  },

  /** 批量删除（Req 16.2）*/
  onBatchDelete: function () {
    var self = this;
    var sel = self.data.selectedIds || {};
    var ids = Object.keys(sel).filter(function (k) { return sel[k]; });
    if (ids.length === 0) {
      wx.showToast({ title: '请先选择要删除的报告', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '批量删除',
      content: '确定删除选中的 ' + ids.length + ' 份荐基报告？',
      confirmText: '删除',
      confirmColor: '#e5484d',
      success: function (res) {
        if (!res.confirm) return;
        self.setData({ batchDeleting: true });
        // 串行删除：依次删除，最终刷新列表
        var promises = ids.map(function (id) {
          return api.deleteDiscoveryReport(id);
        });
        Promise.all(promises)
          .then(function () {
            self.setData({
              batchDeleting: false,
              isSelecting: false,
              selectedIds: {},
              selectedCount: 0,
            });
            wx.showToast({ title: '删除成功', icon: 'success' });
            self._loadHistory();
          })
          .catch(function (err) {
            self.setData({ batchDeleting: false });
            var msg = (err && err.message) || '删除失败，请重试';
            wx.showToast({ title: msg, icon: 'none' });
          });
      },
    });
  },

  // ---------------------------------------------------------------------------
  // 复盘（Req 16.3）
  // ---------------------------------------------------------------------------

  /** 查看某份报告的复盘结果 */
  onViewOutcomes: function (e) {
    var reportId = e.currentTarget.dataset.id;
    if (!reportId) return;
    var self = this;
    self.setData({
      showOutcomes: true,
      outcomesReportId: reportId,
      outcomesData: null,
      outcomesLoading: true,
      outcomesError: '',
    });
    api.fetchDiscoveryOutcomes(reportId)
      .then(function (data) {
        self.setData({ outcomesData: data, outcomesLoading: false });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '加载复盘失败';
        self.setData({ outcomesLoading: false, outcomesError: msg });
      });
  },

  /** 关闭复盘弹层 */
  onCloseOutcomes: function () {
    this.setData({ showOutcomes: false, outcomesData: null, outcomesError: '' });
  },

  // ---------------------------------------------------------------------------
  // 打开历史报告追问（Req 16.4–16.6）
  // ---------------------------------------------------------------------------

  /** 进入某份历史报告的追问（切换到追问视图并加载 chat-panel） */
  onOpenHistoryReport: function (e) {
    var reportId = e.currentTarget.dataset.id;
    if (!reportId) return;
    this.setData({
      chatReportId: reportId,
      showChat: true,
      subView: 'scan',            // 切回扫描视图以显示 chat-panel
      jobPhase: 'completed',      // chat-panel 只在 completed 时显示
      discoveryReport: { id: reportId },
      reportMarkdown: '',
    });
  },

  // ---------------------------------------------------------------------------
  // 辅助：构造 profile 参数
  // ---------------------------------------------------------------------------

  _buildProfile: function () {
    // 使用默认均衡预设；荐基 profile 主要用于传递风险偏好给后端
    var preset = 'balanced';
    var defaults = derive.applyInvestmentPreset('conservative_hold') || {};
    return Object.assign({ investment_preset: preset }, defaults);
  },
});
