// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { startVisibilityAwarePolling } from "@/lib/visibilityPolling";

describe("startVisibilityAwarePolling", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("pauses while hidden, catches up on restore, and cleans up", () => {
    vi.useFakeTimers();
    let visibilityState: DocumentVisibilityState = "hidden";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibilityState);
    const onTick = vi.fn();

    const cleanup = startVisibilityAwarePolling({ intervalMs: 1_000, onTick });

    vi.advanceTimersByTime(3_000);
    expect(onTick).not.toHaveBeenCalled();

    visibilityState = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onTick).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1_000);
    expect(onTick).toHaveBeenCalledTimes(2);

    visibilityState = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(3_000);
    expect(onTick).toHaveBeenCalledTimes(2);

    visibilityState = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onTick).toHaveBeenCalledTimes(3);

    cleanup();
    vi.advanceTimersByTime(3_000);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onTick).toHaveBeenCalledTimes(3);
  });

  it("keeps the existing interval cadence on an initially visible page", () => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const onTick = vi.fn();

    const cleanup = startVisibilityAwarePolling({ intervalMs: 1_000, onTick });

    expect(onTick).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1_000);
    expect(onTick).toHaveBeenCalledTimes(1);
    cleanup();
  });
});
