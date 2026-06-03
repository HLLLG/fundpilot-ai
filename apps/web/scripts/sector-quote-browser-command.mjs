import { chromium } from "playwright";

const HOST_POOL = ["79", "88", "48", "17", "33", "91"];
const COMMON_PARAMS = {
  po: "1",
  np: "1",
  ut: "bd1d9ddb04089700cf9c27f6f7426281",
  fltt: "2",
  invt: "2",
};

const SPECS = [
  [
    "concept",
    {
      ...COMMON_PARAMS,
      pz: "100",
      fid: "f12",
      fs: "m:90 t:3 f:!50",
      fields: "f3,f14",
    },
  ],
  [
    "industry",
    {
      ...COMMON_PARAMS,
      pz: "100",
      fid: "f3",
      fs: "m:90 t:2 f:!50",
      fields: "f3,f14",
    },
  ],
  [
    "index_main",
    {
      ...COMMON_PARAMS,
      pz: "100",
      dect: "1",
      wbp2u: "|0|0|0|web",
      fid: "",
      fs: "b:MK0010",
      fields: "f3,f14",
    },
  ],
  [
    "index_csi",
    {
      ...COMMON_PARAMS,
      pz: "100",
      wbp2u: "|0|0|0|web",
      fid: "f12",
      fs: "m:2",
      fields: "f3,f14",
    },
  ],
];

function toUrl(host, params) {
  const search = new URLSearchParams(params);
  search.set("pn", "1");
  return `https://${host}.push2.eastmoney.com/api/qt/clist/get?${search.toString()}`;
}

function absorbRows(rows, target) {
  for (const row of rows ?? []) {
    const name = String(row?.f14 ?? "").trim();
    const change = row?.f3;
    if (!name || change == null || change === "-") {
      continue;
    }
    const numeric = Number(change);
    if (Number.isFinite(numeric)) {
      target[name] = Number(numeric.toFixed(4));
    }
  }
}

async function fetchJson(page, url) {
  return page.evaluate(async (targetUrl) => {
    try {
      const response = await fetch(targetUrl, {
        credentials: "omit",
        cache: "no-store",
      });
      const text = await response.text();
      return { ok: response.ok, status: response.status, text };
    } catch (error) {
      return { ok: false, status: 0, text: "", error: String(error) };
    }
  }, url);
}

async function main() {
  const timeoutMs = Number(process.env.FUND_AI_SECTOR_QUOTES_TIMEOUT_SECONDS || "8") * 1000;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const boards = {
    index: {},
    concept: {},
    industry: {},
  };

  try {
    await page.goto("https://quote.eastmoney.com/", {
      waitUntil: "domcontentloaded",
      timeout: Math.max(5000, timeoutMs),
    });

    for (const [key, params] of SPECS) {
      let payload = null;
      for (const host of HOST_POOL) {
        const result = await fetchJson(page, toUrl(host, params));
        if (!result.ok || !result.text) {
          continue;
        }
        try {
          payload = JSON.parse(result.text);
          break;
        } catch {
          payload = null;
        }
      }

      const rows = payload?.data?.diff ?? [];
      if (key.startsWith("index")) {
        absorbRows(rows, boards.index);
      } else {
        absorbRows(rows, boards[key]);
      }
    }
  } finally {
    await browser.close();
  }

  process.stdout.write(`${JSON.stringify({ boards })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${String(error)}\n`);
  process.exit(1);
});
