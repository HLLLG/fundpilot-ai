import { afterEach, expect, it, vi } from "vitest";

import { streamReportChat } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

it("forwards an optional abort signal to the report-chat request", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response("", { status: 200 }));
  const controller = new AbortController();

  await streamReportChat(
    "report-1",
    "继续追问",
    "fast",
    {
      onToken: vi.fn(),
      onDone: vi.fn(),
    },
    controller.signal,
  );

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/reports/report-1/chat"),
    expect.objectContaining({ signal: controller.signal }),
  );
});

it("forwards job_started events from the report-chat stream", async () => {
  const body = [
    "data: {\"type\":\"job_started\",\"job_kind\":\"analysis\",\"job_id\":\"job-9\"}\n\n",
    "data: {\"type\":\"token\",\"content\":\"已排队\"}\n\n",
  ].join("");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
  );
  const onJobStarted = vi.fn();
  const onToken = vi.fn();
  const onDone = vi.fn();

  await streamReportChat("report-1", "再生成一份日报", "deep", {
    onJobStarted,
    onToken,
    onDone,
  });

  expect(onJobStarted).toHaveBeenCalledWith({ jobKind: "analysis", jobId: "job-9" });
  expect(onToken).toHaveBeenCalledWith("已排队");
});

it("surfaces completed LangGraph nodes as status hints", async () => {
  const body = [
    "data: {\"type\":\"graph\",\"run_id\":\"run-1\",\"node\":\"tools\",\"status\":\"completed\",\"owner\":\"code\"}\n\n",
    "data: {\"type\":\"token\",\"content\":\"已核对\"}\n\n",
  ].join("");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
  );
  const onStatus = vi.fn();
  const onToken = vi.fn();

  await streamReportChat("report-1", "为什么减仓", "deep", {
    onStatus,
    onToken,
    onDone: vi.fn(),
  });

  expect(onStatus).toHaveBeenCalledWith("节点 tools 完成");
  expect(onToken).toHaveBeenCalledWith("已核对");
});
