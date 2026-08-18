import type { FundCodeResolution, FundSearchItem, Holding, ParsedTransaction } from "@/lib/api";
import { pickBestFundMatch, pickUniqueFundMatch } from "@/lib/fundNameMatch";
import { countSameDayKeys, sameDayTransactionKey } from "@/lib/tradeConfirmDates";

/** 一次相册多选上限。超出后仍识别前 N 张，并提示用户继续上传剩余截图。 */
export const MAX_OCR_IMAGES = 20;

/**
 * 批量识别并发数。qwen-vl-ocr 一次请求只应塞一张图（多图会混成一份文本，
 * 打乱支付宝多列版式解析），所以加速靠多路单图请求并行，而不是把截图拼进一次调用。
 */
export const OCR_UPLOAD_CONCURRENCY = 4;

export type TransactionSyncPlan = "apply_position" | "markers_only";

export type ImageFileSelection = {
  files: File[];
  truncated: boolean;
};

const IMAGE_MIME = /^image\//i;
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif)$/i;

export function isLikelyImageFile(file: File): boolean {
  if (file.type && IMAGE_MIME.test(file.type)) {
    return true;
  }
  // 微信/系统截图粘贴时 type 有时为空，只靠文件名判断。
  return IMAGE_EXT.test(file.name);
}

export function limitImageFiles(files: Iterable<File>): ImageFileSelection {
  const images = Array.from(files).filter(isLikelyImageFile);
  return {
    files: images.slice(0, MAX_OCR_IMAGES),
    truncated: images.length > MAX_OCR_IMAGES,
  };
}

/** 往待识别队列追加截图，超出上限时只留下前面的。 */
export function appendQueuedImageFiles(
  existing: File[],
  incoming: File[],
): ImageFileSelection {
  return limitImageFiles([...existing, ...incoming]);
}

export function collectImageFiles(
  fileList: FileList | null | undefined,
): ImageFileSelection {
  return limitImageFiles(fileList ?? []);
}

/** 拖放或 Ctrl+V。微信复制的图片常见于 items，不一定出现在 files。 */
export function collectDataTransferImages(
  data: DataTransfer | null | undefined,
): ImageFileSelection {
  if (!data) {
    return { files: [], truncated: false };
  }
  const fromItems: File[] = [];
  const items = data.items;
  if (items?.length) {
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      if (item.kind !== "file") {
        continue;
      }
      const file = item.getAsFile();
      if (file) {
        fromItems.push(file);
      }
    }
  }
  if (fromItems.length) {
    return limitImageFiles(fromItems);
  }
  return limitImageFiles(Array.from(data.files ?? []));
}

function mimeExtension(type: string): string {
  if (type.includes("jpeg")) {
    return "jpg";
  }
  if (type.includes("png")) {
    return "png";
  }
  if (type.includes("webp")) {
    return "webp";
  }
  if (type.includes("gif")) {
    return "gif";
  }
  return "png";
}

export function pasteShortcutLabel(): string {
  if (typeof navigator === "undefined") {
    return "Ctrl+V";
  }
  const platform = navigator.platform || "";
  const ua = navigator.userAgent || "";
  if (/Mac|iPhone|iPad/i.test(platform) || /Mac OS X/i.test(ua)) {
    return "⌘V";
  }
  return "Ctrl+V";
}

/** 点「粘贴截图」时走 Clipboard API；权限被拒时仍可用 Ctrl+V。 */
export async function readImagesFromClipboard(): Promise<File[]> {
  const clipboard = navigator.clipboard;
  if (!clipboard || typeof clipboard.read !== "function") {
    return [];
  }
  const items = await clipboard.read();
  const files: File[] = [];
  for (const [index, item] of items.entries()) {
    const type = item.types.find((candidate) => candidate.startsWith("image/"));
    if (!type) {
      continue;
    }
    const blob = await item.getType(type);
    const ext = mimeExtension(blob.type || type);
    files.push(
      new File([blob], `粘贴截图-${index + 1}.${ext}`, {
        type: blob.type || type,
      }),
    );
  }
  return limitImageFiles(files).files;
}

export function batchTransactionKey(tx: ParsedTransaction): string {
  return sameDayTransactionKey(tx);
}

/** 多张交易截图合并：按「同码同天同方向同金额」占用已有名额，避免翻页把同一笔再加一次。 */
export function mergeParsedTransactions(
  existing: ParsedTransaction[],
  incoming: ParsedTransaction[],
): ParsedTransaction[] {
  const remaining = countSameDayKeys(existing);
  const merged = [...existing];
  for (const tx of incoming) {
    const key = batchTransactionKey(tx);
    const available = remaining.get(key) ?? 0;
    if (available > 0) {
      remaining.set(key, available - 1);
      continue;
    }
    merged.push(tx);
  }
  return merged;
}

export function mergeFundCodeResolutions(
  previous: FundCodeResolution[],
  incoming: FundCodeResolution[],
): FundCodeResolution[] {
  const byName = new Map(previous.map((item) => [item.fund_name, item]));
  for (const item of incoming) {
    byName.set(item.fund_name, item);
  }
  return [...byName.values()];
}

export function ocrProgressLabel(
  isUploading: boolean,
  progress: { current: number; total: number } | null | undefined,
  idleLabel: string,
): string {
  if (!isUploading) {
    return idleLabel;
  }
  if (progress && progress.total > 1 && progress.current > 0) {
    return `识别中 ${progress.current}/${progress.total}`;
  }
  return "识别中...";
}

/** 有限并发地跑 mapper，结果按输入顺序返回（失败也占位，不打乱合并顺序）。 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  concurrency: number,
  mapper: (item: T, index: number) => Promise<R>,
  onProgress?: (done: number, total: number) => void,
): Promise<PromiseSettledResult<R>[]> {
  const total = items.length;
  const results = new Array<PromiseSettledResult<R>>(total);
  if (total === 0) {
    return results;
  }
  const limit = Math.max(1, Math.min(concurrency, total));
  let nextIndex = 0;
  let done = 0;

  const runWorker = async () => {
    while (nextIndex < total) {
      const index = nextIndex;
      nextIndex += 1;
      try {
        const value = await mapper(items[index] as T, index);
        results[index] = { status: "fulfilled", value };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
      done += 1;
      onProgress?.(done, total);
    }
  };

  await Promise.all(Array.from({ length: limit }, () => runWorker()));
  return results;
}

function holdingNeedsFundCode(holding: Holding): boolean {
  return !holding.fund_code || holding.fund_code === "000000";
}

/** 识别阶段就把最相近代码填上，确认页打开时不再先闪「待匹配」。 */
export async function fillClosestFundCodes(
  holdings: Holding[],
  resolutions: FundCodeResolution[],
  search: (query: string) => Promise<FundSearchItem[]>,
): Promise<{ holdings: Holding[]; resolutions: FundCodeResolution[] }> {
  const nextHoldings = holdings.map((holding) => ({ ...holding }));
  const nextResolutions = resolutions.map((item) => ({ ...item }));
  const pending = nextHoldings
    .map((holding, index) => ({ holding, index }))
    .filter(({ holding }) => holdingNeedsFundCode(holding));
  if (!pending.length) {
    return { holdings: nextHoldings, resolutions: nextResolutions };
  }

  const hits = await Promise.all(
    pending.map(async ({ holding, index }) => {
      const items = await search(holding.fund_name).catch(() => []);
      const match =
        pickBestFundMatch(holding.fund_name, items) ??
        items.find((item) => item.fund_code && item.fund_code !== "000000") ??
        null;
      return { index, match };
    }),
  );

  for (const { index, match } of hits) {
    if (!match) {
      continue;
    }
    const holding = nextHoldings[index];
    if (!holding || !holdingNeedsFundCode(holding)) {
      continue;
    }
    nextHoldings[index] = { ...holding, fund_code: match.fund_code };
    const resolutionIndex = nextResolutions.findIndex(
      (item) => item.fund_name === holding.fund_name,
    );
    const similarResolution: FundCodeResolution = {
      fund_name: holding.fund_name,
      fund_code: match.fund_code,
      source: "similar",
      resolved: true,
      message: "请确认基金",
    };
    if (resolutionIndex >= 0) {
      nextResolutions[resolutionIndex] = similarResolution;
    } else {
      nextResolutions.push(similarResolution);
    }
  }

  return { holdings: nextHoldings, resolutions: nextResolutions };
}

/** 识别阶段就把最相近代码填上，确认页打开时不再先闪「请选择基金」。 */
export async function fillClosestTransactionFundCodes(
  transactions: ParsedTransaction[],
  search: (query: string) => Promise<FundSearchItem[]>,
): Promise<ParsedTransaction[]> {
  const next = transactions.map((tx) => ({ ...tx }));
  const pending = next
    .map((tx, index) => ({ tx, index }))
    .filter(({ tx }) => !tx.fund_code);
  if (!pending.length) {
    return next;
  }

  const hits = await Promise.all(
    pending.map(async ({ tx, index }) => {
      const items = await search(tx.fund_name).catch(() => []);
      const unique = pickUniqueFundMatch(tx.fund_name, items);
      const match =
        unique ??
        pickBestFundMatch(tx.fund_name, items) ??
        items.find((item) => item.fund_code && item.fund_code !== "000000") ??
        null;
      return { index, match, similar: Boolean(match) && !unique };
    }),
  );

  for (const { index, match, similar } of hits) {
    if (!match) {
      continue;
    }
    const tx = next[index];
    if (!tx || tx.fund_code) {
      continue;
    }
    next[index] = {
      ...tx,
      fund_code: match.fund_code,
      ...(similar ? { match_source: "similar" } : {}),
    };
  }
  return next;
}
