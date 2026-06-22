// components/chat-panel/chat-panel.js
// 非流式追问对话面板组件（Req 14, 16.4–16.6, A3, BC1）。
//
// 功能：
//   1. onAttached 与 reportId 变更时拉取对话历史（Req 14.1）。
//   2. 用户输入消息 + 切换快速/深度模式后提交，调用非流式 /chat/sync（Req 14.2, 14.3）。
//   3. 立即追加 user 消息，收到响应后追加 assistant 消息，md-view 渲染（Req 14.4）。
//   4. 请求失败时展示错误与「重试」按钮（Req 14.5）。
//
// Props:
//   reportId  {String}  日报或荐基报告的 id
//   chatType  {String}  'report' | 'discovery'，决定调用哪组 API（默认 'report'）
//   loading   {Boolean} 外部传入的加载状态（用于屏蔽 UI，可选）
//
// Feature: miniprogram-web-parity

const api = require('../../utils/api');

Component({
  options: {
    styleIsolation: 'apply-shared',
  },

  properties: {
    // 日报或荐基报告的 ID
    reportId: {
      type: String,
      value: '',
    },
    // 'report' | 'discovery'
    chatType: {
      type: String,
      value: 'report',
    },
    // 外部传入的加载态（如报告本身还在轮询中可屏蔽输入）
    loading: {
      type: Boolean,
      value: false,
    },
  },

  data: {
    // 对话消息列表：[{ role: 'user'|'assistant', content: string }]
    chatList: [],
    // 用户输入框内容
    inputValue: '',
    // 快速/深度模式
    chatMode: 'fast',
    // 是否正在发送（屏蔽重复提交）
    sending: false,
    // 发送错误文案（空字符串表示无错误）
    sendError: '',
    // 待重试的消息与模式（发送失败时暂存）
    _retryMessage: '',
    _retryChatMode: 'fast',
    // 是否正在加载历史
    historyLoading: false,
    historyError: '',
  },

  observers: {
    // reportId 变化时重新拉取历史（Req 14.1）
    reportId: function (newVal) {
      if (newVal) {
        this._loadHistory();
      } else {
        this.setData({ chatList: [], historyError: '', sendError: '' });
      }
    },
  },

  lifetimes: {
    attached: function () {
      if (this.data.reportId) {
        this._loadHistory();
      }
    },
  },

  methods: {
    // -----------------------------------------------------------------------
    // 拉取历史消息
    // -----------------------------------------------------------------------
    _loadHistory: function () {
      var self = this;
      var reportId = self.data.reportId;
      var chatType = self.data.chatType;
      if (!reportId) return;

      self.setData({ historyLoading: true, historyError: '' });

      var fetchFn = chatType === 'discovery'
        ? api.fetchDiscoveryChatHistory
        : api.fetchReportChatHistory;

      fetchFn(reportId)
        .then(function (messages) {
          // messages 是 [{ role, content }] 数组
          var list = Array.isArray(messages) ? messages : [];
          self.setData({ chatList: list, historyLoading: false });
        })
        .catch(function (err) {
          var msg = (err && err.message) ? err.message : '加载历史失败';
          self.setData({ historyLoading: false, historyError: msg });
        });
    },

    // -----------------------------------------------------------------------
    // 输入框内容变化
    // -----------------------------------------------------------------------
    onInput: function (e) {
      this.setData({ inputValue: e.detail.value || '' });
    },

    // -----------------------------------------------------------------------
    // 切换快速/深度模式
    // -----------------------------------------------------------------------
    onModeChange: function (e) {
      var mode = e.currentTarget.dataset.mode;
      if (mode === 'fast' || mode === 'deep') {
        this.setData({ chatMode: mode });
      }
    },

    // -----------------------------------------------------------------------
    // 提交追问（Req 14.2, 14.3, 14.4, 14.5）
    // -----------------------------------------------------------------------
    onSubmit: function () {
      var message = (this.data.inputValue || '').trim();
      if (!message) return;
      if (this.data.sending) return;
      if (!this.data.reportId) return;

      this._sendMessage(message, this.data.chatMode);
    },

    // -----------------------------------------------------------------------
    // 重试上次失败的请求（Req 14.5）
    // -----------------------------------------------------------------------
    onRetry: function () {
      var message = this.data._retryMessage;
      var mode = this.data._retryChatMode;
      if (!message) return;
      this.setData({ sendError: '' });
      this._sendMessage(message, mode);
    },

    // -----------------------------------------------------------------------
    // 重试拉取历史
    // -----------------------------------------------------------------------
    onRetryHistory: function () {
      this._loadHistory();
    },

    // -----------------------------------------------------------------------
    // 核心：发送消息
    // -----------------------------------------------------------------------
    _sendMessage: function (message, chatMode) {
      var self = this;
      var reportId = self.data.reportId;
      var chatType = self.data.chatType;

      // 立即追加 user 消息（Req 14.4）
      var userMsg = { role: 'user', content: message };
      var newList = self.data.chatList.concat([userMsg]);
      self.setData({
        chatList: newList,
        inputValue: '',
        sending: true,
        sendError: '',
        _retryMessage: message,
        _retryChatMode: chatMode,
      });

      var sendFn = chatType === 'discovery'
        ? api.sendDiscoveryChat
        : api.sendReportChat;

      sendFn(reportId, message, chatMode)
        .then(function (result) {
          // result: { user_message, message, chat_mode, model? }
          // message 字段为 assistant 的完整回答
          var assistantContent = '';
          if (result && result.message) {
            var m = result.message;
            assistantContent = m.content || m.markdown || (typeof m === 'string' ? m : '');
          }
          var assistantMsg = { role: 'assistant', content: assistantContent };
          self.setData({
            chatList: self.data.chatList.concat([assistantMsg]),
            sending: false,
            sendError: '',
          });
          self._scrollToBottom();
        })
        .catch(function (err) {
          var msg = (err && err.message) ? err.message : '追问失败，请重试';
          self.setData({
            sending: false,
            sendError: msg,
          });
        });
    },

    // -----------------------------------------------------------------------
    // 滚动到底部
    // -----------------------------------------------------------------------
    _scrollToBottom: function () {
      // 通过更新 scrollTo anchor 触发滚动到最新消息
      this.setData({ scrollAnchor: 'chat-bottom-anchor' });
    },
  },
});
