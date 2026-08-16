import { expect, it } from "vitest";

import { emitAgentJobStarted, subscribeAgentJobStarted } from "@/lib/agentJobEvents";

it("notifies subscribers of a valid job_started event", () => {
  const seen: Array<{ jobKind: string; jobId: string }> = [];
  const unsubscribe = subscribeAgentJobStarted((event) => {
    seen.push(event);
  });

  emitAgentJobStarted({ jobKind: "analysis", jobId: "job-1" });
  emitAgentJobStarted({ jobKind: "discovery", jobId: "  " });
  unsubscribe();
  emitAgentJobStarted({ jobKind: "analysis", jobId: "job-2" });

  expect(seen).toEqual([{ jobKind: "analysis", jobId: "job-1" }]);
});
