// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CLIENT_CACHE_MEMORY_MAX_ENTRIES,
  deleteClientCache,
  readClientCache,
  writeClientCache,
} from "@/lib/clientCache";

const TEST_PREFIX = "client-cache-test";

function cacheKey(index: number): string {
  return `${TEST_PREFIX}:${index}`;
}

function clearTestEntries(): void {
  for (let index = 0; index <= CLIENT_CACHE_MEMORY_MAX_ENTRIES; index += 1) {
    deleteClientCache(cacheKey(index), "memory");
  }
  deleteClientCache(`${TEST_PREFIX}:session`, "session");
}

describe("clientCache memory storage", () => {
  beforeEach(() => {
    clearTestEntries();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  });

  afterEach(() => {
    clearTestEntries();
    vi.useRealTimers();
  });

  it("deletes an expired memory entry when it is read", () => {
    const key = cacheKey(0);
    writeClientCache(key, "stale");
    vi.advanceTimersByTime(101);

    expect(readClientCache(key, 100)).toBeNull();
    expect(readClientCache(key, -1)).toBeNull();
  });

  it("evicts the least recently used memory entry at the capacity limit", () => {
    for (let index = 0; index < CLIENT_CACHE_MEMORY_MAX_ENTRIES; index += 1) {
      writeClientCache(cacheKey(index), index);
    }

    expect(readClientCache(cacheKey(0), -1)).toBe(0);
    writeClientCache(cacheKey(CLIENT_CACHE_MEMORY_MAX_ENTRIES), "overflow");

    expect(readClientCache(cacheKey(1), -1)).toBeNull();
    expect(readClientCache(cacheKey(0), -1)).toBe(0);
    expect(readClientCache(cacheKey(CLIENT_CACHE_MEMORY_MAX_ENTRIES), -1)).toBe("overflow");
  });

  it("moves an overwritten memory entry to the most-recent position", () => {
    for (let index = 0; index < CLIENT_CACHE_MEMORY_MAX_ENTRIES; index += 1) {
      writeClientCache(cacheKey(index), index);
    }

    writeClientCache(cacheKey(0), "updated");
    writeClientCache(cacheKey(CLIENT_CACHE_MEMORY_MAX_ENTRIES), "overflow");

    expect(readClientCache(cacheKey(1), -1)).toBeNull();
    expect(readClientCache(cacheKey(0), -1)).toBe("updated");
  });

  it("does not evict or delete expired session storage entries", () => {
    const sessionKey = `${TEST_PREFIX}:session`;
    writeClientCache(sessionKey, "session-value", "session");

    for (let index = 0; index <= CLIENT_CACHE_MEMORY_MAX_ENTRIES; index += 1) {
      writeClientCache(cacheKey(index), index);
    }
    vi.advanceTimersByTime(101);

    expect(readClientCache(sessionKey, 100, "session")).toBeNull();
    expect(window.sessionStorage.getItem(sessionKey)).not.toBeNull();
    expect(readClientCache(sessionKey, -1, "session")).toBe("session-value");
  });
});
