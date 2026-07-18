// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useLazyAsyncResource } from "@/lib/useLazyAsyncResource";


afterEach(() => {
  cleanup();
});


function Harness({
  enabled,
  load,
}: {
  enabled: boolean;
  load: () => Promise<string>;
}) {
  const resource = useLazyAsyncResource({
    enabled,
    load,
    errorMessage: "fallback error",
  });
  return (
    <div>
      <output aria-label="state">
        {resource.loading
          ? "loading"
          : resource.error
            ? `error:${resource.error}`
            : resource.data ?? "idle"}
      </output>
      <button type="button" onClick={resource.retry}>
        retry
      </button>
    </div>
  );
}


it("does not load before it is enabled and caches the successful result", async () => {
  const load = vi.fn().mockResolvedValue("ready");
  const view = render(<Harness enabled={false} load={load} />);

  expect(load).not.toHaveBeenCalled();
  expect(screen.getByLabelText("state")).toHaveTextContent("idle");

  view.rerender(<Harness enabled load={load} />);
  await waitFor(() => expect(screen.getByLabelText("state")).toHaveTextContent("ready"));
  expect(load).toHaveBeenCalledTimes(1);

  view.rerender(<Harness enabled={false} load={load} />);
  view.rerender(<Harness enabled load={load} />);
  expect(load).toHaveBeenCalledTimes(1);
});


it("exposes one explicit retry after a failed request", async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error("network down"))
    .mockResolvedValueOnce("recovered");
  render(<Harness enabled load={load} />);

  await waitFor(() =>
    expect(screen.getByLabelText("state")).toHaveTextContent("error:network down"),
  );
  fireEvent.click(screen.getByRole("button", { name: "retry" }));
  await waitFor(() =>
    expect(screen.getByLabelText("state")).toHaveTextContent("recovered"),
  );
  expect(load).toHaveBeenCalledTimes(2);
});
