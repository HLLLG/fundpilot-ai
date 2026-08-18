import { describe, expect, it, vi } from "vitest";
import type { ParsedTransaction } from "@/lib/api";
import {
  MAX_OCR_IMAGES,
  OCR_UPLOAD_CONCURRENCY,
  appendQueuedImageFiles,
  batchTransactionKey,
  collectDataTransferImages,
  collectImageFiles,
  fillClosestFundCodes,
  fillClosestTransactionFundCodes,
  isLikelyImageFile,
  mapWithConcurrency,
  mergeFundCodeResolutions,
  mergeParsedTransactions,
  ocrProgressLabel,
} from "@/lib/ocrBatchUpload";

function tx(
  overrides: Partial<ParsedTransaction> = {},
): ParsedTransaction {
  return {
    direction: "buy",
    fund_name: "万家宏观择时多策略灵活配置混合C",
    fund_code: "019164",
    amount_yuan: 1500,
    trade_time: "2026-08-07 14:32:15",
    confirm_date: "2026-08-07",
    in_progress: false,
    ...overrides,
  };
}

describe("collectImageFiles", () => {
  it("caps the album selection and reports truncation", () => {
    const files = Array.from({ length: MAX_OCR_IMAGES + 3 }, (_, index) =>
      new File(["x"], `page-${index}.png`, { type: "image/png" }),
    );
    const list = {
      length: files.length,
      item: (index: number) => files[index] ?? null,
      *[Symbol.iterator]() {
        yield* files;
      },
    } as FileList;
    const selected = collectImageFiles(list);
    expect(selected.files).toHaveLength(MAX_OCR_IMAGES);
    expect(selected.truncated).toBe(true);
  });
});

describe("appendQueuedImageFiles", () => {
  it("keeps previously queued screenshots when appending the next paste", () => {
    const first = new File(["a"], "one.png", { type: "image/png" });
    const second = new File(["b"], "two.png", { type: "image/png" });
    const queued = appendQueuedImageFiles([first], [second]);
    expect(queued.files).toEqual([first, second]);
    expect(queued.truncated).toBe(false);
  });
});

describe("collectDataTransferImages", () => {
  it("reads a WeChat-style paste from clipboard items when files is empty", () => {
    const file = new File(["img"], "微信截图.png", { type: "image/png" });
    const selected = collectDataTransferImages({
      files: [] as unknown as FileList,
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile: () => file,
        },
      ],
    } as unknown as DataTransfer);
    expect(selected.files).toEqual([file]);
  });

  it("keeps unnamed clipboard bitmaps that still report an image MIME type", () => {
    const file = new File(["img"], "image.png", { type: "image/png" });
    expect(isLikelyImageFile(file)).toBe(true);
    expect(isLikelyImageFile(new File(["x"], "notes.txt", { type: "text/plain" }))).toBe(
      false,
    );
  });
});

describe("mergeParsedTransactions", () => {
  it("drops identical buy/sell points across screenshots", () => {
    const firstPage = [
      tx(),
      tx({
        fund_name: "招商医疗保健股票A",
        fund_code: "000979",
        amount_yuan: 2000,
        trade_time: "2026-08-13 14:55:30",
      }),
    ];
    const overlapPage = [
      tx(),
      tx({
        direction: "sell",
        amount_yuan: 300,
        trade_time: "2026-08-07 15:01:00",
      }),
    ];
    const merged = mergeParsedTransactions(firstPage, overlapPage);
    expect(merged).toHaveLength(3);
    expect(merged.map((item) => batchTransactionKey(item))).toEqual([
      batchTransactionKey(firstPage[0]),
      batchTransactionKey(firstPage[1]),
      batchTransactionKey(overlapPage[1]),
    ]);
  });

  it("merges 交易记录 and 交易分析 rows that only differ by seconds", () => {
    const fromRecords = [
      tx({
        fund_name: "南方黄金股C",
        fund_code: "021959",
        amount_yuan: 1000,
        trade_time: "2026-08-17 14:55:30",
      }),
    ];
    const fromAnalysis = [
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 1000,
        trade_time: "2026-08-17 14:59:52",
      }),
    ];
    expect(mergeParsedTransactions(fromRecords, fromAnalysis)).toHaveLength(1);
  });

  it("keeps two same-day same-amount buys from one screenshot", () => {
    const page = [
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:50:00",
      }),
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:52:00",
      }),
    ];
    expect(mergeParsedTransactions([], page)).toHaveLength(2);
  });

  it("consumes overlapping analysis rows without dropping the second genuine buy", () => {
    const fromRecords = [
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:50:00",
      }),
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:52:00",
      }),
    ];
    const fromAnalysis = [
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:55:30",
      }),
      tx({
        fund_name: "南方黄金股指数C",
        fund_code: "021959",
        amount_yuan: 500,
        trade_time: "2026-08-17 14:59:52",
      }),
    ];
    expect(mergeParsedTransactions(fromRecords, fromAnalysis)).toHaveLength(2);
  });
});

describe("mergeFundCodeResolutions", () => {
  it("lets a later page overwrite the same fund name", () => {
    const merged = mergeFundCodeResolutions(
      [
        {
          fund_name: "招商医疗保健股票A",
          fund_code: null,
          source: null,
          resolved: false,
        },
      ],
      [
        {
          fund_name: "招商医疗保健股票A",
          fund_code: "000979",
          source: "exact",
          resolved: true,
        },
      ],
    );
    expect(merged).toEqual([
      {
        fund_name: "招商医疗保健股票A",
        fund_code: "000979",
        source: "exact",
        resolved: true,
      },
    ]);
  });
});

describe("ocrProgressLabel", () => {
  it("shows batch progress only while several images are in flight", () => {
    expect(ocrProgressLabel(false, { current: 1, total: 3 }, "相册选择")).toBe(
      "相册选择",
    );
    expect(ocrProgressLabel(true, { current: 2, total: 3 }, "相册选择")).toBe(
      "识别中 2/3",
    );
    expect(ocrProgressLabel(true, { current: 0, total: 3 }, "相册选择")).toBe(
      "识别中...",
    );
    expect(ocrProgressLabel(true, { current: 1, total: 1 }, "相册选择")).toBe(
      "识别中...",
    );
  });
});

describe("mapWithConcurrency", () => {
  it("caps in-flight work, keeps input order, and records rejections", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const started: number[] = [];
    const progress: Array<[number, number]> = [];
    const outcomes = await mapWithConcurrency(
      [40, 10, 25, 5],
      2,
      async (delay, index) => {
        started.push(index);
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((resolve) => {
          setTimeout(resolve, delay);
        });
        inFlight -= 1;
        if (index === 2) {
          throw new Error(`fail-${index}`);
        }
        return `ok-${index}`;
      },
      (done, total) => {
        progress.push([done, total]);
      },
    );
    expect(OCR_UPLOAD_CONCURRENCY).toBe(4);
    expect(maxInFlight).toBe(2);
    expect(started.slice(0, 2).sort()).toEqual([0, 1]);
    expect(outcomes).toHaveLength(4);
    expect(outcomes[0]).toEqual({ status: "fulfilled", value: "ok-0" });
    expect(outcomes[1]).toEqual({ status: "fulfilled", value: "ok-1" });
    expect(outcomes[2]).toMatchObject({ status: "rejected" });
    expect(outcomes[3]).toEqual({ status: "fulfilled", value: "ok-3" });
    expect(progress).toEqual([
      [1, 4],
      [2, 4],
      [3, 4],
      [4, 4],
    ]);
  });
});

describe("fillClosestFundCodes", () => {
  it("fills the closest search hit before the confirm list opens", async () => {
    const filled = await fillClosestFundCodes(
      [
        {
          fund_code: "000000",
          fund_name: "招商医疗保健股票A",
          holding_amount: 3513.5,
          return_percent: 0,
          holding_profit: 13.5,
        },
      ],
      [
        {
          fund_name: "招商医疗保健股票A",
          fund_code: null,
          source: null,
          resolved: false,
        },
      ],
      async () => [
        { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
        { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
      ],
    );
    expect(filled.holdings[0]?.fund_code).toBe("011373");
    expect(filled.resolutions[0]).toMatchObject({
      fund_code: "011373",
      source: "similar",
      message: "请确认基金",
    });
  });

  it("leaves already resolved codes alone", async () => {
    const search = vi.fn(async () => [
      { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
    ]);
    const filled = await fillClosestFundCodes(
      [
        {
          fund_code: "015788",
          fund_name: "易方达消费行业股票",
          holding_amount: 1000,
          return_percent: 0,
          holding_profit: 0,
        },
      ],
      [],
      search,
    );
    expect(search).not.toHaveBeenCalled();
    expect(filled.holdings[0]?.fund_code).toBe("015788");
  });
});

describe("fillClosestTransactionFundCodes", () => {
  it("fills the closest search hit and marks it for confirm", async () => {
    const filled = await fillClosestTransactionFundCodes(
      [
        tx({
          fund_name: "招商医疗保健股票A",
          fund_code: null,
          amount_yuan: 2000,
        }),
      ],
      async () => [
        { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
        { fund_code: "011374", fund_name: "招商前沿医疗保健股票C" },
      ],
    );
    expect(filled[0]?.fund_code).toBe("011373");
    expect(filled[0]?.match_source).toBe("similar");
  });

  it("leaves already resolved codes alone", async () => {
    const search = vi.fn(async () => [
      { fund_code: "011373", fund_name: "招商前沿医疗保健股票A" },
    ]);
    const filled = await fillClosestTransactionFundCodes(
      [tx({ fund_code: "000979", fund_name: "招商医疗保健股票A" })],
      search,
    );
    expect(search).not.toHaveBeenCalled();
    expect(filled[0]?.fund_code).toBe("000979");
    expect(filled[0]?.match_source).toBeUndefined();
  });
});
