type CacheEnvelope<T> = {
  fetchedAt: number;
  data: T;
};

export const CLIENT_CACHE_MEMORY_MAX_ENTRIES = 256;

const memoryStore = new Map<string, CacheEnvelope<unknown>>();

function touchMemoryEntry(key: string, envelope: CacheEnvelope<unknown>): void {
  memoryStore.delete(key);
  memoryStore.set(key, envelope);
}

function evictMemoryOverflow(): void {
  while (memoryStore.size > CLIENT_CACHE_MEMORY_MAX_ENTRIES) {
    const oldestKey = memoryStore.keys().next().value;
    if (oldestKey === undefined) {
      return;
    }
    memoryStore.delete(oldestKey);
  }
}

function readSession(key: string): CacheEnvelope<unknown> | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as CacheEnvelope<unknown>;
  } catch {
    return null;
  }
}

function writeSession<T>(key: string, data: T) {
  if (typeof window === "undefined") {
    return;
  }
  const envelope: CacheEnvelope<T> = { fetchedAt: Date.now(), data };
  window.sessionStorage.setItem(key, JSON.stringify(envelope));
}

export type ClientCacheStorage = "memory" | "session";

export function buildClientCacheKey(...parts: Array<string | number | boolean | null | undefined>) {
  return parts
    .map((part) => (part == null ? "" : String(part)))
    .filter(Boolean)
    .join(":");
}

export function readClientCache<T>(
  key: string,
  maxAgeMs: number,
  storage: ClientCacheStorage = "memory",
): T | null {
  const envelope =
    storage === "session" ? readSession(key) : (memoryStore.get(key) as CacheEnvelope<T> | undefined);
  if (!envelope) {
    return null;
  }
  if (maxAgeMs >= 0 && Date.now() - envelope.fetchedAt > maxAgeMs) {
    if (storage === "memory") {
      memoryStore.delete(key);
    }
    return null;
  }
  if (storage === "memory") {
    touchMemoryEntry(key, envelope);
  }
  return envelope.data as T;
}

export function writeClientCache<T>(
  key: string,
  data: T,
  storage: ClientCacheStorage = "memory",
) {
  const envelope: CacheEnvelope<T> = { fetchedAt: Date.now(), data };
  if (storage === "session") {
    writeSession(key, data);
    return;
  }
  touchMemoryEntry(key, envelope);
  evictMemoryOverflow();
}

export function deleteClientCache(key: string, storage: ClientCacheStorage = "memory"): void {
  if (storage === "session") {
    if (typeof window !== "undefined") {
      try {
        window.sessionStorage.removeItem(key);
      } catch {
        // ignore
      }
    }
    return;
  }
  memoryStore.delete(key);
}

export function peekClientCacheAgeMs(
  key: string,
  storage: ClientCacheStorage = "memory",
): number | null {
  const envelope =
    storage === "session" ? readSession(key) : (memoryStore.get(key) as CacheEnvelope<unknown> | undefined);
  if (!envelope) {
    return null;
  }
  return Date.now() - envelope.fetchedAt;
}
