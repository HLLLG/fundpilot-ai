# 追问助手聊天区与智能滚动 — 设计说明

**日期：** 2026-06-16  
**范围：** `ReportChatPanel`（日报追问助手）、`DiscoveryChatPanel`（推荐报告追问）

## 背景与问题

1. **聊天区过小：** 日报侧栏 `compact` 模式整体高度约 `52vh / 360px`，头部模式切换与输入框占用较多，消息区实际可见仅约 1～2 条气泡。
2. **强制滚底：** 两组件均在 `messages` 每次变更时 `scrollTop = scrollHeight`，流式输出时用户上滑阅读会被拉回底部。

## 竞品调研（摘要）

| 产品 / 来源 | 行为 |
|-------------|------|
| ChatGPT / Claude 等主流对话 | 默认跟随流式输出滚底；用户上滑后暂停自动滚动；滚回底部或点「回到底部」后恢复跟随 |
| Stack Overflow / GitHub Issues | 常见实现：`distanceFromBottom < threshold` 判定是否在底部；发送新消息时强制滚底 |
| 社区共识 | 阈值通常 40～80px；有选中文本时可额外暂停（本期未做，YAGNI） |

## 方案对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 仅加大 `max-h` | 改动最小 | 不解决滚底问题 |
| B. 各组件内联滚动逻辑 | 无新文件 | 两处重复、易漂移 |
| **C. 共享 `useChatAutoScroll` + 加大布局（推荐）** | 行为一致、可测、易扩展 | 少量新代码 |

## 最终实现（方案 C）

### 1. 尺寸调整

**`ReportChatPanel` compact（侧栏）：**

- 面板：`52vh/360px` → **`70vh/480px`**
- 消息区：`min-h-[min(42vh,380px)]`，`flex-1` 占满剩余高度

**`globals.css` 侧栏列宽：**

- `minmax(260px, 22rem)` → **`minmax(280px, 26rem)`**

**`DiscoveryChatPanel`：**

- 固定 `max-h-64`（256px）→ 整体 **`min-h 400px`，`62vh` 上限**，消息区 `min-h-[min(36vh,320px)]`

### 2. 智能滚动（`useChatAutoScroll`）

- `isChatScrollNearBottom`：距底部 ≤ **64px** 视为「贴底」
- `onScroll`：更新 `isPinnedToBottom`
- `onContentChange`（`messages` 变化 / 流式 token）：**仅当贴底或 `forceNextScroll` 时**滚底
- `pinToBottomForSend`：用户发送时强制 smooth 滚底
- `onHistoryLoaded`：切换报告后首次加载历史滚底一次
- `resetKey={reportId}`：换报告重置 pin 状态
- 非贴底时显示悬浮 **「回到底部」** 按钮

### 3. 不在本期范围

- 选中文本时暂停滚动
- 虚拟列表 / 超长历史优化

## 验证

- `npm run lint` / `npm run typecheck` / `npm run build`（`apps/web`）
- 手动：流式输出时上滑 → 位置保持；滚回底部或点按钮 → 恢复跟随；发送新消息 → 滚底
