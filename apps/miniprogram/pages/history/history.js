// pages/history/history.js
// History_Page（历史日报）—— 阶段七 Task 9.8（对标 Web HistoryRail）
//
// 功能：
//   1. 列表：GET /api/reports 获取并展示历史日报列表（Req 20.1）
//   2. 详情：点击某份日报，经 md-view 展示其正文（Req 20.2）
//   3. 多选 + 批量删除：DELETE /api/reports/{id} 逐一删除所选后刷新（Req 20.3）
//
// Requirements: 20.1, 20.2, 20.3

const api = require('../../utils/api');

// 将 ISO/时间戳格式化为 "YYYY-MM-DD HH:mm"（列表展示用）。
function formatDateTime(value) {
  if (!value) return '';
  var d = new Date(value);
  if (isNaN(d.getTime())) return String(value);
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  return (
    d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
  );
}

// 从 report 对象提取可渲染的正文 Markdown（与 report 页保持一致）。
function extractMarkdown(report) {
  if (!report) return '';
  return report.body || report.markdown || report.content || '';
}

Page({
  data: {
    // ── 列表三态（state-view）─────────────────────────
    reports: [],            // 渲染列表（含派生展示字段）
    loading: false,
    error: '',
    isEmpty: false,
    reportCount: 0,         // 列表数量（num-text 展示）

    // ── 多选删除（Req 20.3）──────────────────────────
    isSelecting: false,     // 是否处于多选模式
    selectedIds: {},        // { [reportId]: true }
    selectedCount: 0,       // 已选数量（WXML 无管道过滤，需在 JS 维护）
    batchDeleting: false,

    // ── 详情（Req 20.2）──────────────────────────────
    showDetail: false,
    detailTitle: '',
    detailDate: '',
    detailMarkdown: '',
  },

  // ---------------------------------------------------------------------------
  // 生命周期
  // ---------------------------------------------------------------------------

  onShow: function () {
    if (!api.getToken()) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this._loadReports();
  },

  // ---------------------------------------------------------------------------
  // 列表（Req 20.1）
  // ---------------------------------------------------------------------------

  _loadReports: function () {
    var self = this;
    self.setData({ loading: true, error: '' });
    api.listReports()
      .then(function (data) {
        // API 返回报告数组（亦兼容 { reports: [] } 形状）。
        var list = Array.isArray(data) ? data : (data && data.reports) || [];
        var view = list.map(function (report) {
          return {
            id: report.id,
            title: report.title || '历史日报',
            created_display: formatDateTime(report.created_at),
            risk_level: (report.risk && report.risk.level) || '',
            _report: report,
          };
        });
        self.setData({
          reports: view,
          reportCount: view.length,
          isEmpty: view.length === 0,
          loading: false,
        });
      })
      .catch(function (err) {
        var msg = (err && err.message) || '加载历史日报失败';
        self.setData({ loading: false, error: msg, isEmpty: false });
      });
  },

  /** state-view 错误态「重试」 */
  onRetry: function () {
    this._loadReports();
  },

  // ---------------------------------------------------------------------------
  // 详情（Req 20.2）
  // ---------------------------------------------------------------------------

  /** 点击列表项：多选模式下切换选中，否则展示详情。 */
  onTapReport: function (e) {
    var id = e.currentTarget.dataset.id;
    if (!id) return;

    if (this.data.isSelecting) {
      this._toggleSelect(id);
      return;
    }

    var item = this.data.reports.filter(function (r) { return r.id === id; })[0];
    if (!item) return;
    var markdown = extractMarkdown(item._report);

    this.setData({
      showDetail: true,
      detailTitle: item.title,
      detailDate: item.created_display,
      detailMarkdown: markdown,
    });
  },

  /** 阻止详情面板内部点击冒泡到遮罩（避免误关闭）。 */
  noop: function () {},

  /** 关闭详情弹层 */
  onCloseDetail: function () {
    this.setData({
      showDetail: false,
      detailTitle: '',
      detailDate: '',
      detailMarkdown: '',
    });
  },

  // ---------------------------------------------------------------------------
  // 多选 + 批量删除（Req 20.3）
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

  /** checkbox 点击：切换单条选中状态。 */
  onToggleSelect: function (e) {
    var id = e.currentTarget.dataset.id;
    if (!id) return;
    this._toggleSelect(id);
  },

  _toggleSelect: function (id) {
    var sel = Object.assign({}, this.data.selectedIds);
    if (sel[id]) {
      delete sel[id];
    } else {
      sel[id] = true;
    }
    this.setData({ selectedIds: sel, selectedCount: Object.keys(sel).length });
  },

  /** 批量删除（Req 20.3）：DELETE /api/reports/{id} 逐一删除后刷新列表。 */
  onBatchDelete: function () {
    var self = this;
    var sel = self.data.selectedIds || {};
    var ids = Object.keys(sel).filter(function (k) { return sel[k]; });
    if (ids.length === 0) {
      wx.showToast({ title: '请先选择要删除的日报', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '批量删除',
      content: '确定删除选中的 ' + ids.length + ' 份日报？',
      confirmText: '删除',
      confirmColor: '#e5484d',
      success: function (res) {
        if (!res.confirm) return;
        self.setData({ batchDeleting: true });
        var promises = ids.map(function (id) {
          return api.deleteReport(id);
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
            self._loadReports();
          })
          .catch(function (err) {
            self.setData({ batchDeleting: false });
            var msg = (err && err.message) || '删除失败，请重试';
            wx.showToast({ title: msg, icon: 'none' });
          });
      },
    });
  },
});
