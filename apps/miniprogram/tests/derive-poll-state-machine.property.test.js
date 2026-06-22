// Feature: miniprogram-web-parity, Property 16: 任务轮询状态机
//
// Property 16 (design.md):
//   For any 任务状态 status ∈ {pending, running, completed, failed}，
//   轮询决策函数对 pending/running 判定为「继续轮询」、对 completed/failed 判定为「停止」，
//   且阶段展示标签映射对每个已知 stage 唯一确定。
//
// Validates: Requirements 13.4, 15.5
//
// 实现要点：
//   - pollDecision('pending') 和 pollDecision('running') 均返回 POLL_CONTINUE（'continue'）。
//   - pollDecision('completed') 和 pollDecision('failed') 均返回 POLL_STOP（'stop'）。
//   - 任意未知 status 同样返回 POLL_STOP（"fail-safe" 停止）。
//   - getStagLabel 对 STAGE_LABELS 中每个 stage 返回唯一非空字符串标签。
//   - getStagLabel 对已知 stage 的返回值与 STAGE_LABELS 映射一一对应（无冲突）。
//   - numRuns ≥ 100（本特性属性测试约定）。

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { createRequire } from "node:module";

// derive.js 为 CommonJS 模块，用 createRequire 加载以保留其原生导出形态。
const require = createRequire(import.meta.url);
const derive = require("../utils/derive");

const {
  pollDecision,
  getStagLabel,
  POLL_CONTINUE,
  POLL_STOP,
  STAGE_LABELS,
} = derive;

const NUM_RUNS = 100; // ≥ 100 次迭代

// 已知 stage 列表（从 STAGE_LABELS 的 key 集合派生）。
const KNOWN_STAGES = Object.keys(STAGE_LABELS);

describe("Property 16: 任务轮询状态机", () => {
  // -----------------------------------------------------------------------
  // pollDecision: pending / running → 继续
  // -----------------------------------------------------------------------
  it("pollDecision 对 pending 和 running 返回 POLL_CONTINUE（继续轮询）", () => {
    const continueStatuses = ["pending", "running"];
    fc.assert(
      fc.property(
        fc.constantFrom(...continueStatuses),
        (status) => {
          const decision = pollDecision(status);
          expect(decision).toBe(POLL_CONTINUE);
          expect(decision).toBe("continue");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // pollDecision: completed / failed → 停止
  // -----------------------------------------------------------------------
  it("pollDecision 对 completed 和 failed 返回 POLL_STOP（停止轮询）", () => {
    const stopStatuses = ["completed", "failed"];
    fc.assert(
      fc.property(
        fc.constantFrom(...stopStatuses),
        (status) => {
          const decision = pollDecision(status);
          expect(decision).toBe(POLL_STOP);
          expect(decision).toBe("stop");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // pollDecision: 任意未知 status → 停止（fail-safe）
  // -----------------------------------------------------------------------
  it("pollDecision 对任意未知状态返回 POLL_STOP（fail-safe 停止）", () => {
    const knownStatuses = new Set(["pending", "running", "completed", "failed"]);
    fc.assert(
      fc.property(
        fc.string({ minLength: 0, maxLength: 30 }).filter(
          (s) => !knownStatuses.has(s)
        ),
        (unknownStatus) => {
          const decision = pollDecision(unknownStatus);
          expect(decision).toBe(POLL_STOP);
          expect(decision).toBe("stop");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // pollDecision: 返回值仅为 POLL_CONTINUE 或 POLL_STOP（值域封闭）
  // -----------------------------------------------------------------------
  it("pollDecision 的返回值仅为 POLL_CONTINUE 或 POLL_STOP", () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 0, maxLength: 30 }),
        (status) => {
          const decision = pollDecision(status);
          expect([POLL_CONTINUE, POLL_STOP]).toContain(decision);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // getStagLabel: 每个已知 stage 返回唯一非空字符串标签
  // -----------------------------------------------------------------------
  it("getStagLabel 对每个已知 stage 返回非空字符串", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...KNOWN_STAGES),
        (stage) => {
          const label = getStagLabel(stage);
          expect(typeof label).toBe("string");
          expect(label.length).toBeGreaterThan(0);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // getStagLabel: 已知 stage 的标签与 STAGE_LABELS 映射精确匹配
  // -----------------------------------------------------------------------
  it("getStagLabel 对已知 stage 返回与 STAGE_LABELS 精确匹配的标签", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...KNOWN_STAGES),
        (stage) => {
          const label = getStagLabel(stage);
          expect(label).toBe(STAGE_LABELS[stage]);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // getStagLabel: 每个已知 stage 的标签唯一确定（同一 stage 多次调用结果一致）
  // -----------------------------------------------------------------------
  it("getStagLabel 对相同 stage 多次调用返回相同标签（确定性）", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...KNOWN_STAGES),
        (stage) => {
          const label1 = getStagLabel(stage);
          const label2 = getStagLabel(stage);
          expect(label1).toBe(label2);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // getStagLabel: 已知 stage 的标签相互唯一（无两个 stage 映射相同标签）
  // -----------------------------------------------------------------------
  it("STAGE_LABELS 中各 stage 的标签互不相同（唯一确定性）", () => {
    // 这是一个枚举断言，遍历所有已知 stage 的 label 集合，验证无重复。
    const labels = KNOWN_STAGES.map((s) => STAGE_LABELS[s]);
    const uniqueLabels = new Set(labels);
    expect(uniqueLabels.size).toBe(KNOWN_STAGES.length);
  });

  // -----------------------------------------------------------------------
  // getStagLabel: 未知 stage 返回空字符串
  // -----------------------------------------------------------------------
  it("getStagLabel 对未知 stage 返回空字符串", () => {
    const knownStageSet = new Set(KNOWN_STAGES);
    fc.assert(
      fc.property(
        fc.string({ minLength: 0, maxLength: 30 }).filter(
          (s) => !knownStageSet.has(s)
        ),
        (unknownStage) => {
          const label = getStagLabel(unknownStage);
          expect(label).toBe("");
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });

  // -----------------------------------------------------------------------
  // 综合：四个规范状态的轮询决策与设计描述完全一致
  // -----------------------------------------------------------------------
  it("四个规范任务状态的轮询决策与设计规格完全一致", () => {
    const specTable = [
      { status: "pending",   expected: POLL_CONTINUE },
      { status: "running",   expected: POLL_CONTINUE },
      { status: "completed", expected: POLL_STOP },
      { status: "failed",    expected: POLL_STOP },
    ];
    fc.assert(
      fc.property(
        fc.constantFrom(...specTable),
        ({ status, expected }) => {
          expect(pollDecision(status)).toBe(expected);
        }
      ),
      { numRuns: NUM_RUNS }
    );
  });
});
