"use client";

import Image from "next/image";
import { ClipboardPaste, Plus, X } from "lucide-react";
import type { QueuedScreenshot } from "@/lib/useScreenshotIntake";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { MAX_OCR_IMAGES } from "@/lib/ocrBatchUpload";

/** 鼠标/触控板设备才适合点按钮读剪贴板；手机几乎读不到图片。 */
export const CLIPBOARD_IMAGE_PASTE_QUERY = "(hover: hover) and (pointer: fine)";

export function ScreenshotDropOverlay({ active }: { active: boolean }) {
  if (!active) {
    return null;
  }
  return (
    <div
      className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-[inherit] bg-[color-mix(in_srgb,var(--brand-soft)_88%,white)]/95 px-6"
      aria-hidden
    >
      <p className="rounded-2xl border border-dashed border-[var(--brand)] bg-white/90 px-5 py-4 text-center text-[15px] font-bold text-[var(--brand-strong)] shadow-sm">
        松开即可加入待识别
      </p>
    </div>
  );
}

export function ScreenshotPasteButton({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  const canPasteFromClipboard = useMediaQuery(CLIPBOARD_IMAGE_PASTE_QUERY);
  if (!canPasteFromClipboard) {
    return null;
  }
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="btn-ghost w-[200px] min-h-8 py-1.5 text-[13px]"
    >
      <ClipboardPaste size={15} strokeWidth={2.25} />
      粘贴截图
    </button>
  );
}

export function ScreenshotQueue({
  items,
  disabled,
  onRemove,
}: {
  items: QueuedScreenshot[];
  disabled: boolean;
  onRemove: (id: string) => void;
}) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="w-full">
      <p className="mb-2 text-[12px] font-semibold text-slate-600">
        待识别 {items.length} 张，可继续上传
      </p>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {items.map((item, index) => (
          <li key={item.id} className="relative shrink-0">
            {item.previewUrl ? (
              // blob: 预览不走 next/image
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={item.previewUrl}
                alt={`待识别截图 ${index + 1}`}
                className="h-[72px] w-[52px] rounded-lg border border-slate-200 bg-white object-cover"
              />
            ) : (
              <div
                className="flex h-[72px] w-[52px] items-center justify-center rounded-lg border border-slate-200 bg-white text-xs font-bold text-slate-500"
                aria-label={`待识别截图 ${index + 1}`}
              >
                {index + 1}
              </div>
            )}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(item.id)}
              className="absolute -right-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-white shadow-sm disabled:opacity-50"
              aria-label={`移除第 ${index + 1} 张截图`}
            >
              <X size={12} strokeWidth={2.5} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 同步持仓 / 导入交易共用的手机示意图框，保证预览高宽一致。 */
export function ScreenshotPhoneGuide({
  src,
  alt,
  width = 472,
  height = 1024,
}: {
  src: string;
  alt: string;
  width?: number;
  height?: number;
}) {
  return (
    <div className="relative mx-auto h-[80%] max-h-full aspect-[472/1024] sm:h-full">
      <div className="h-full overflow-hidden rounded-[1.15rem] border-[3px] border-slate-800 bg-white shadow-[0_12px_24px_rgba(15,23,42,0.14)]">
        <Image
          src={src}
          alt={alt}
          width={width}
          height={height}
          className="h-full w-full object-cover object-top"
          sizes="(min-width: 640px) 360px, 220px"
          priority
          draggable={false}
        />
      </div>
    </div>
  );
}

export function ScreenshotComposerGrid({
  items,
  disabled,
  onAdd,
  onRemove,
}: {
  items: QueuedScreenshot[];
  disabled: boolean;
  onAdd: () => void;
  onRemove: (id: string) => void;
}) {
  const canAdd = items.length < MAX_OCR_IMAGES;
  return (
    <div className="w-full">
      <ul className="grid grid-cols-3 gap-2">
        {items.map((item, index) => (
          <li key={item.id} className="relative aspect-square">
            {item.previewUrl ? (
              // blob: 预览不走 next/image
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={item.previewUrl}
                alt={`待识别截图 ${index + 1}`}
                className="h-full w-full rounded-lg border border-slate-200 bg-white object-cover"
              />
            ) : (
              <div
                className="flex h-full w-full items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500"
                aria-label={`待识别截图 ${index + 1}`}
              >
                {index + 1}
              </div>
            )}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(item.id)}
              className="absolute -right-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-white shadow-sm disabled:opacity-50"
              aria-label={`移除第 ${index + 1} 张截图`}
            >
              <X size={12} strokeWidth={2.5} />
            </button>
          </li>
        ))}
        {canAdd ? (
          <li>
            <button
              type="button"
              disabled={disabled}
              onClick={onAdd}
              aria-label="从本地选择图片"
              className="flex aspect-square w-full flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-slate-300 bg-[#f0f2f5] text-slate-500 transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)] hover:text-[var(--brand)] disabled:opacity-50"
            >
              <Plus size={28} strokeWidth={2} />
            </button>
          </li>
        ) : null}
      </ul>
      <p className="mt-3 text-center text-[12px] leading-5 text-slate-500">
        点击加号从电脑选择，也可直接粘贴截图
      </p>
    </div>
  );
}
