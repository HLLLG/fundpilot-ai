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
