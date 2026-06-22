// pages/settings/settings —— 账号设置（Settings_Page）
//
// 能力（「更多」二级入口，Req 21）：
//   - onShow → api.fetchCurrentUser()（GET /api/auth/me）展示当前账号与微信绑定状态（Req 21.1）
//   - 当前为微信占位账号时，提供「关联已有邮箱账号」入口，
//     api.linkEmail(account, password)（POST /api/auth/link-email）完成打通（Req 21.2）
//   - 关联成功用返回的新令牌更新本地登录态 api.setToken(accessToken)（Req 21.3）
//   - 关联失败展示后端错误文案并保留用户输入（Req 21.4）
//   - state-view 三态（Req 4.1/4.2）
//
// Requirements: 21.1, 21.2, 21.3, 21.4

const api = require("../../utils/api");

// 微信占位账号后缀（与后端 _WECHAT_ACCOUNT_SUFFIX 对齐）。
const WECHAT_ACCOUNT_SUFFIX = "@wechat.fundpilot";

function isWechatPlaceholder(account) {
  return typeof account === "string" && account.endsWith(WECHAT_ACCOUNT_SUFFIX);
}

Page({
  data: {
    // 账号信息态（Req 4.1/4.2）
    loading: false,
    error: "",
    user: null, // { username, userAccount, wechatBound }
    // 展示派生字段
    isPlaceholder: false, // 当前是否为微信占位账号（决定是否展示关联入口）
    wechatStatusText: "",
    accountDisplay: "",
    // 关联邮箱表单（Req 21.2）
    account: "",
    password: "",
    linking: false,
    linkError: "", // 后端返回的错误文案（Req 21.4）
  },

  onShow() {
    if (!api.getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this.loadCurrentUser();
  },

  // GET /api/auth/me → 展示账号与微信绑定状态（Req 21.1）
  async loadCurrentUser() {
    this.setData({ loading: true, error: "" });
    try {
      const user = await api.fetchCurrentUser();
      const account = (user && user.userAccount) || "";
      const placeholder = isWechatPlaceholder(account);
      this.setData({
        loading: false,
        error: "",
        user: user,
        isPlaceholder: placeholder,
        wechatBound: !!(user && user.wechatBound),
        wechatStatusText: user && user.wechatBound ? "已绑定" : "未绑定",
        // 占位账号的内部邮箱无展示价值，统一以「微信账号」呈现。
        accountDisplay: placeholder ? "微信账号" : account,
      });
    } catch (err) {
      this.setData({
        loading: false,
        error: (err && err.message) || "加载失败，请稍后重试",
      });
    }
  },

  onRetry() {
    this.loadCurrentUser();
  },

  onAccountInput(e) {
    this.setData({ account: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  // POST /api/auth/link-email → 关联已有邮箱账号（Req 21.2/21.3/21.4）
  async onLinkEmail() {
    if (this.data.linking) {
      return;
    }
    const account = (this.data.account || "").trim();
    const password = this.data.password || "";
    if (!account || !password) {
      this.setData({ linkError: "请输入邮箱和密码" });
      return;
    }
    this.setData({ linking: true, linkError: "" });
    try {
      const session = await api.linkEmail(account, password);
      // 关联成功：用返回的新令牌更新本地登录态（Req 21.3）。
      if (session && session.accessToken) {
        api.setToken(session.accessToken);
      }
      wx.showToast({ title: "关联成功", icon: "success" });
      // 清空表单，刷新账号信息（关联后不再是占位账号）。
      this.setData({ account: "", password: "", linkError: "" });
      this.loadCurrentUser();
    } catch (err) {
      // 关联失败：展示后端错误文案并保留用户输入（Req 21.4）。
      this.setData({ linkError: (err && err.message) || "关联失败，请稍后重试" });
    } finally {
      this.setData({ linking: false });
    }
  },
});
