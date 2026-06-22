// Feature: miniprogram-web-parity, Property 15: 对话追加顺序
//
// Property 15 (design.md):
//   For any 历史对话列表与一轮 (userMessage, assistantMessage)，追加后列表长度
//   增加 2，且新增的末两条顺序为先 user 后 assistant；原有消息相对顺序不变。
//
// Validates: Requirements 14.4, 16.6
//
// 实现要点：
//   - chat-panel.js 的追加逻辑是内联的（非导出函数），在此以等价纯函数建模：
//       appendChat(chatList, userMsg, assistantMsg)
//         => [...chatList, userMsg, assistantMsg]
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";

const NUM_RUNS = 100; // ≥ 100 次迭代

// ---------------------------------------------------------------------------
// 纯函数：appendChat
//   等价于 chat-panel.js 中的追加逻辑：
//     chatList.concat([userMsg])  (立即追加 user)
//     chatList.concat([assistantMsg])  (收到响应后追加 assistant)
//   合并为一步纯函数以便属性测试。
// ---------------------------------------------------------------------------
/**
 * 将一轮追问（用户消息 + 助手消息）追加到对话列表末尾。
 * 返回新数组（不修改原列表）。
 *
 * @param {Array<{role: string, content: string}>} chatList - 原始对话列表
 * @param {{role: string, content: string}} userMsg       - 用户消息
 * @param {{role: string, content: string}} assistantMsg  - 助手消息
 * @returns {Array<{role: string, content: string}>}
 */
function appendChat(chatList, userMsg, assistantMsg) {
  return chatList.concat([userMsg, assistantMsg]);
}

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

/** 生成单条消息对象（role + content） */
const arbMessage = fc.record({
  role: fc.constantFrom("user", "assistant", "system"),
  content: fc.string({ minLength: 0, maxLength: 200 }),
});

/** 生成任意长度的历史对话列表（0–20 条） */
const arbChatList = fc.array(arbMessage, { minLength: 0, maxLength: 20 });

/** 生成一对 (userMessage, assistantMessage) */
const arbUserMsg = fc.record({
  role: fc.constant("user"),
  content: fc.string({ minLength: 0, maxLength: 200 }),
});

const arbAssistantMsg = fc.record({
  role: fc.constant("assistant"),
  content: fc.string({ minLength: 0, maxLength: 500 }),
});

// ---------------------------------------------------------------------------
// Property 15 Tests
// ---------------------------------------------------------------------------

describe("Property 15: 对话追加顺序", () => {
  // -----------------------------------------------------------------------
  // 核心属性：追加后长度增加 2
  // -----------------------------------------------------------------------
  it("追加一轮对话后，列表长度恰好增加 2", () => {
    fc.assert(
      fc.property(arbChatList, arbUserMsg, arbAssistantMsg, (chatList, userMsg, assistantMsg) => {
        const result = appendChat(chatList, userMsg, assistantMsg);
        expect(result.length).toBe(chatList.length + 2);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 核心属性：末两条顺序为先 user 后 assistant
  // -----------------------------------------------------------------------
  it("追加后末两条的顺序为先 user 后 assistant", () => {
    fc.assert(
      fc.property(arbChatList, arbUserMsg, arbAssistantMsg, (chatList, userMsg, assistantMsg) => {
        const result = appendChat(chatList, userMsg, assistantMsg);
        const secondToLast = result[result.length - 2];
        const last = result[result.length - 1];

        // 倒数第二条是 user 消息
        expect(secondToLast).toBe(userMsg);
        expect(secondToLast.role).toBe("user");

        // 最后一条是 assistant 消息
        expect(last).toBe(assistantMsg);
        expect(last.role).toBe("assistant");
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 核心属性：原有消息相对顺序不变
  // -----------------------------------------------------------------------
  it("追加后原有消息的相对顺序保持不变", () => {
    fc.assert(
      fc.property(arbChatList, arbUserMsg, arbAssistantMsg, (chatList, userMsg, assistantMsg) => {
        const result = appendChat(chatList, userMsg, assistantMsg);
        const originalSlice = result.slice(0, chatList.length);

        // 每条原有消息与原列表中对应位置相同（引用相等）
        for (let i = 0; i < chatList.length; i++) {
          expect(originalSlice[i]).toBe(chatList[i]);
        }
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 核心属性：appendChat 不修改原列表（纯函数不变性）
  // -----------------------------------------------------------------------
  it("appendChat 返回新数组，不修改原始 chatList", () => {
    fc.assert(
      fc.property(arbChatList, arbUserMsg, arbAssistantMsg, (chatList, userMsg, assistantMsg) => {
        const originalLength = chatList.length;
        const originalSnapshot = chatList.slice(); // 浅拷贝用于比对

        const result = appendChat(chatList, userMsg, assistantMsg);

        // 原列表长度不变
        expect(chatList.length).toBe(originalLength);

        // 原列表内容不变
        for (let i = 0; i < originalLength; i++) {
          expect(chatList[i]).toBe(originalSnapshot[i]);
        }

        // 返回结果是新的数组引用（空列表时也成立）
        expect(result).not.toBe(chatList);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 组合属性：新增的两条消息 content 与传入值精确匹配
  // -----------------------------------------------------------------------
  it("追加后末两条的 content 与传入的 userMsg / assistantMsg 精确匹配", () => {
    fc.assert(
      fc.property(arbChatList, arbUserMsg, arbAssistantMsg, (chatList, userMsg, assistantMsg) => {
        const result = appendChat(chatList, userMsg, assistantMsg);
        const secondToLast = result[result.length - 2];
        const last = result[result.length - 1];

        expect(secondToLast.content).toBe(userMsg.content);
        expect(last.content).toBe(assistantMsg.content);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 空列表边界：空历史列表追加后长度恰好为 2
  // -----------------------------------------------------------------------
  it("空历史列表追加一轮对话后长度恰好为 2，且顺序正确", () => {
    fc.assert(
      fc.property(arbUserMsg, arbAssistantMsg, (userMsg, assistantMsg) => {
        const result = appendChat([], userMsg, assistantMsg);
        expect(result.length).toBe(2);
        expect(result[0]).toBe(userMsg);
        expect(result[1]).toBe(assistantMsg);
      }),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 多轮追加：连续两轮追加后顺序全部正确
  // -----------------------------------------------------------------------
  it("连续两轮追加后，所有消息的相对顺序均正确", () => {
    fc.assert(
      fc.property(
        arbChatList,
        arbUserMsg,
        arbAssistantMsg,
        arbUserMsg,
        arbAssistantMsg,
        (chatList, user1, assistant1, user2, assistant2) => {
          const after1 = appendChat(chatList, user1, assistant1);
          const after2 = appendChat(after1, user2, assistant2);

          // 总长度 = 原始 + 4
          expect(after2.length).toBe(chatList.length + 4);

          // 第一轮追加的消息在正确位置
          expect(after2[chatList.length]).toBe(user1);
          expect(after2[chatList.length + 1]).toBe(assistant1);

          // 第二轮追加的消息在末尾
          expect(after2[after2.length - 2]).toBe(user2);
          expect(after2[after2.length - 1]).toBe(assistant2);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
