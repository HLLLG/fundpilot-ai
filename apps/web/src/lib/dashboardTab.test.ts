import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadDashboardTab, saveDashboardTab } from "@/lib/storage";

describe("dashboard tab persistence", () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    const sessionStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    vi.stubGlobal("window", { sessionStorage });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to today when nothing stored", () => {
    expect(loadDashboardTab()).toBe("today");
  });

  it("restores a saved primary tab", () => {
    saveDashboardTab("market");
    expect(loadDashboardTab()).toBe("market");
  });

  it("restores holdings tab", () => {
    saveDashboardTab("holdings");
    expect(loadDashboardTab()).toBe("holdings");
  });

  it("ignores invalid stored values", () => {
    window.sessionStorage.setItem("fundpilot-dashboard-tab", "invalid");
    expect(loadDashboardTab()).toBe("today");
  });
});
