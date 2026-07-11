type ControlledMediaQueryList = MediaQueryList & {
  setMatches: (matches: boolean) => void;
};

export type MatchMediaController = {
  setMatches: (query: string, matches: boolean) => void;
  restore: () => void;
};

function createMediaQueryList(query: string, initialMatches: boolean): ControlledMediaQueryList {
  const eventTarget = new EventTarget();
  let matches = initialMatches;
  let onchange: ((this: MediaQueryList, event: MediaQueryListEvent) => unknown) | null = null;

  const mediaQueryList = {
    get matches() {
      return matches;
    },
    media: query,
    get onchange() {
      return onchange;
    },
    set onchange(listener) {
      onchange = listener;
    },
    addListener(listener: ((this: MediaQueryList, event: MediaQueryListEvent) => unknown) | null) {
      if (listener) eventTarget.addEventListener("change", listener as EventListener);
    },
    removeListener(listener: ((this: MediaQueryList, event: MediaQueryListEvent) => unknown) | null) {
      if (listener) eventTarget.removeEventListener("change", listener as EventListener);
    },
    addEventListener(...args: Parameters<EventTarget["addEventListener"]>) {
      eventTarget.addEventListener(...args);
    },
    removeEventListener(...args: Parameters<EventTarget["removeEventListener"]>) {
      eventTarget.removeEventListener(...args);
    },
    dispatchEvent(event: Event) {
      return eventTarget.dispatchEvent(event);
    },
    setMatches(nextMatches: boolean) {
      if (matches === nextMatches) return;
      matches = nextMatches;
      const event = new Event("change") as MediaQueryListEvent;
      Object.defineProperties(event, {
        matches: { value: matches },
        media: { value: query },
      });
      eventTarget.dispatchEvent(event);
      onchange?.call(mediaQueryList, event);
    },
  } satisfies ControlledMediaQueryList;

  return mediaQueryList;
}

export function installMatchMedia(
  initialMatches: Record<string, boolean> = {},
): MatchMediaController {
  const originalDescriptor = Object.getOwnPropertyDescriptor(window, "matchMedia");
  const mediaQueryLists = new Map<string, ControlledMediaQueryList>();

  const getMediaQueryList = (query: string) => {
    const existing = mediaQueryLists.get(query);
    if (existing) return existing;

    const mediaQueryList = createMediaQueryList(query, initialMatches[query] ?? false);
    mediaQueryLists.set(query, mediaQueryList);
    return mediaQueryList;
  };

  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => getMediaQueryList(query),
  });

  return {
    setMatches(query, matches) {
      getMediaQueryList(query).setMatches(matches);
    },
    restore() {
      if (originalDescriptor) {
        Object.defineProperty(window, "matchMedia", originalDescriptor);
      } else {
        Reflect.deleteProperty(window, "matchMedia");
      }
    },
  };
}
