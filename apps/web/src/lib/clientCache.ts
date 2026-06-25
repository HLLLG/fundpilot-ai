type CacheEnvelope<T> = {
  fetchedAt: number;
  data: T;
};

const memoryStore = new Map<string, CacheEnvelope<unknown>>();

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
    return null;
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
  memoryStore.set(key, envelope);
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
