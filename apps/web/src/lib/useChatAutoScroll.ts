import { useCallback, useEffect, useRef, useState } from "react";

/** Distance from bottom (px) still counts as "following" the stream. */
export const CHAT_SCROLL_BOTTOM_THRESHOLD_PX = 64;

export function isChatScrollNearBottom(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  threshold = CHAT_SCROLL_BOTTOM_THRESHOLD_PX,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}

type UseChatAutoScrollOptions = {
  /** Scroll to bottom once when history first loads. */
  scrollOnInitialLoad?: boolean;
  /** When this changes (e.g. report id), reset pin state for a new conversation. */
  resetKey?: string;
};

export function useChatAutoScroll<T extends HTMLElement = HTMLDivElement>(
  options: UseChatAutoScrollOptions = {},
) {
  const { scrollOnInitialLoad = true, resetKey } = options;
  const scrollRef = useRef<T>(null);
  const isPinnedToBottomRef = useRef(true);
  const forceNextScrollRef = useRef(false);
  const hasInitialScrolledRef = useRef(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const syncPinnedState = useCallback(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    const pinned = isChatScrollNearBottom(node.scrollTop, node.scrollHeight, node.clientHeight);
    isPinnedToBottomRef.current = pinned;
    setShowScrollToBottom(!pinned);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    node.scrollTo({ top: node.scrollHeight, behavior });
    isPinnedToBottomRef.current = true;
    setShowScrollToBottom(false);
  }, []);

  const handleScroll = useCallback(() => {
    syncPinnedState();
  }, [syncPinnedState]);

  /** Call when message list/content changes (including streaming tokens). */
  const onContentChange = useCallback(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    if (forceNextScrollRef.current || isPinnedToBottomRef.current) {
      node.scrollTop = node.scrollHeight;
      isPinnedToBottomRef.current = true;
      setShowScrollToBottom(false);
    } else {
      setShowScrollToBottom(true);
    }
    forceNextScrollRef.current = false;
  }, []);

  /** Call when the user sends a new message — always follow the reply. */
  const pinToBottomForSend = useCallback(() => {
    forceNextScrollRef.current = true;
    isPinnedToBottomRef.current = true;
    scrollToBottom("smooth");
  }, [scrollToBottom]);

  /** Call after async history finishes loading. */
  const onHistoryLoaded = useCallback(() => {
    if (!scrollOnInitialLoad || hasInitialScrolledRef.current) {
      return;
    }
    hasInitialScrolledRef.current = true;
    forceNextScrollRef.current = true;
    scrollToBottom("auto");
  }, [scrollOnInitialLoad, scrollToBottom]);

  useEffect(() => {
    hasInitialScrolledRef.current = false;
    isPinnedToBottomRef.current = true;
    forceNextScrollRef.current = false;
    setShowScrollToBottom(false);
  }, [resetKey]);

  return {
    scrollRef,
    handleScroll,
    onContentChange,
    pinToBottomForSend,
    onHistoryLoaded,
    scrollToBottom,
    showScrollToBottom,
  };
}
