"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import {
  MAX_OCR_IMAGES,
  collectDataTransferImages,
  limitImageFiles,
  pasteShortcutLabel,
  readImagesFromClipboard,
} from "@/lib/ocrBatchUpload";

export type QueuedScreenshot = {
  id: string;
  file: File;
  previewUrl: string;
};

type ScreenshotIntakeOptions = {
  open: boolean;
  accepting: boolean;
};

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

function createPreviewUrl(file: File): string {
  if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(file);
  }
  return "";
}

function revokePreviewUrl(url: string) {
  if (url && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
}

function nextQueueId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useScreenshotIntake({ open, accepting }: ScreenshotIntakeOptions) {
  const [dragActive, setDragActive] = useState(false);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [items, setItems] = useState<QueuedScreenshot[]>([]);
  const dragDepthRef = useRef(0);

  useEffect(() => {
    if (open) {
      return;
    }
    setDragActive(false);
    dragDepthRef.current = 0;
    setPasteError(null);
    setItems((current) => {
      for (const item of current) {
        revokePreviewUrl(item.previewUrl);
      }
      return [];
    });
  }, [open]);

  const appendFiles = useCallback((files: File[]) => {
    const incoming = limitImageFiles(files).files;
    if (!incoming.length) {
      setPasteError("没有可用的图片。请粘贴截图，或从相册选择。");
      return;
    }
    setItems((current) => {
      const room = MAX_OCR_IMAGES - current.length;
      if (room <= 0) {
        setPasteError(`一次最多 ${MAX_OCR_IMAGES} 张，请先识别或删掉部分截图。`);
        return current;
      }
      const added = incoming.slice(0, room).map((file) => ({
        id: nextQueueId(),
        file,
        previewUrl: createPreviewUrl(file),
      }));
      if (incoming.length > room) {
        setPasteError(`一次最多 ${MAX_OCR_IMAGES} 张，多出的请识别后再继续添加。`);
      } else {
        setPasteError(null);
      }
      return [...current, ...added];
    });
  }, []);

  const removeItem = useCallback((id: string) => {
    setItems((current) => {
      const target = current.find((item) => item.id === id);
      if (target) {
        revokePreviewUrl(target.previewUrl);
      }
      return current.filter((item) => item.id !== id);
    });
    setPasteError(null);
  }, []);

  const clearItems = useCallback(() => {
    setItems((current) => {
      for (const item of current) {
        revokePreviewUrl(item.previewUrl);
      }
      return [];
    });
    setPasteError(null);
  }, []);

  useEffect(() => {
    if (!accepting) {
      setDragActive(false);
      dragDepthRef.current = 0;
      return;
    }

    const onPaste = (event: ClipboardEvent) => {
      if (isEditableTarget(event.target)) {
        return;
      }
      const selected = collectDataTransferImages(event.clipboardData);
      if (!selected.files.length) {
        return;
      }
      event.preventDefault();
      appendFiles(selected.files);
    };

    document.addEventListener("paste", onPaste);
    return () => {
      document.removeEventListener("paste", onPaste);
    };
  }, [accepting, appendFiles]);

  const resetDrag = () => {
    dragDepthRef.current = 0;
    setDragActive(false);
  };

  const dropHandlers = {
    onDragEnter: (event: DragEvent<HTMLElement>) => {
      if (!accepting) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dragDepthRef.current += 1;
      setDragActive(true);
    },
    onDragOver: (event: DragEvent<HTMLElement>) => {
      if (!accepting) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (event: DragEvent<HTMLElement>) => {
      if (!accepting) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) {
        setDragActive(false);
      }
    },
    onDrop: (event: DragEvent<HTMLElement>) => {
      if (!accepting) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      resetDrag();
      const selected = collectDataTransferImages(event.dataTransfer);
      if (!selected.files.length) {
        return;
      }
      appendFiles(selected.files);
    },
  };

  const pasteFromClipboard = useCallback(async () => {
    if (!accepting) {
      return;
    }
    setPasteError(null);
    try {
      const files = await readImagesFromClipboard();
      if (files.length) {
        appendFiles(files);
        return;
      }
    } catch {
      // 无权限或不支持 Clipboard API 时，引导用快捷键；paste 事件不需要授权。
    }
    setPasteError(`没读到图片。请复制截图后按 ${pasteShortcutLabel()} 粘贴。`);
  }, [accepting, appendFiles]);

  return {
    items,
    files: items.map((item) => item.file),
    dragActive,
    pasteError,
    dropHandlers,
    appendFiles,
    removeItem,
    clearItems,
    pasteFromClipboard,
  };
}
