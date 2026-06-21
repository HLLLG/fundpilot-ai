const DISCOVERY_FOCUS_SECTORS_KEY = "fundpilot-discovery-focus-sectors";
export const DISCOVERY_FOCUS_CHANGED_EVENT = "fundpilot-discovery-focus-changed";
export const DISCOVERY_FOCUS_TOAST_EVENT = "fundpilot-focus-sector-toast";

export type FocusSectorActionResult = {
  sectors: string[];
  message: string;
  kind: "added" | "removed" | "duplicate" | "full";
};

export function loadDiscoveryFocusSectors(): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.sessionStorage.getItem(DISCOVERY_FOCUS_SECTORS_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map((value) => (typeof value === "string" ? value.trim() : ""))
      .filter(Boolean)
      .slice(0, 3);
  } catch {
    return [];
  }
}

export function saveDiscoveryFocusSectors(sectors: string[]): void {
  if (typeof window === "undefined") {
    return;
  }
  const normalized = sectors.map((s) => s.trim()).filter(Boolean).slice(0, 3);
  if (!normalized.length) {
    window.sessionStorage.removeItem(DISCOVERY_FOCUS_SECTORS_KEY);
  } else {
    window.sessionStorage.setItem(DISCOVERY_FOCUS_SECTORS_KEY, JSON.stringify(normalized));
  }
  window.dispatchEvent(
    new CustomEvent(DISCOVERY_FOCUS_CHANGED_EVENT, { detail: normalized }),
  );
}

export function showFocusSectorToast(message: string): void {
  if (typeof window === "undefined" || !message.trim()) {
    return;
  }
  window.dispatchEvent(new CustomEvent(DISCOVERY_FOCUS_TOAST_EVENT, { detail: message }));
}

function dispatchResult(result: FocusSectorActionResult): string[] {
  saveDiscoveryFocusSectors(result.sectors);
  showFocusSectorToast(result.message);
  return result.sectors;
}

/** Prepend sector; cap at 3 unique labels. */
export function addDiscoveryFocusSector(sector: string): string[] {
  const trimmed = sector.trim();
  if (!trimmed) {
    return loadDiscoveryFocusSectors();
  }
  const current = loadDiscoveryFocusSectors();
  if (current.includes(trimmed)) {
    return dispatchResult({
      sectors: current,
      message: `「${trimmed}」已在关注方向中`,
      kind: "duplicate",
    });
  }
  if (current.length >= 3) {
    return dispatchResult({
      sectors: current,
      message: "关注方向已满（最多 3 个），请先在推荐基金页取消一项",
      kind: "full",
    });
  }
  const next = [trimmed, ...current].slice(0, 3);
  return dispatchResult({
    sectors: next,
    message: `已加入关注方向：${trimmed}`,
    kind: "added",
  });
}

export function removeDiscoveryFocusSector(sector: string): string[] {
  const trimmed = sector.trim();
  const current = loadDiscoveryFocusSectors();
  if (!trimmed || !current.includes(trimmed)) {
    return current;
  }
  const next = current.filter((item) => item !== trimmed);
  return dispatchResult({
    sectors: next,
    message: `已取消关注：${trimmed}`,
    kind: "removed",
  });
}

/** 市场页：已关注则取消，否则加入。 */
export function toggleDiscoveryFocusSector(sector: string): string[] {
  const trimmed = sector.trim();
  const current = loadDiscoveryFocusSectors();
  if (current.includes(trimmed)) {
    return removeDiscoveryFocusSector(trimmed);
  }
  return addDiscoveryFocusSector(trimmed);
}

export function setDiscoveryFocusSectors(sectors: string[]): string[] {
  const next = sectors.map((s) => s.trim()).filter(Boolean).slice(0, 3);
  saveDiscoveryFocusSectors(next);
  return next;
}
