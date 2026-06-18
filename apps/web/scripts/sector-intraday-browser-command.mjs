import { chromium } from "playwright";

function asFloat(value) {
  if (value == null) return null;
  const cleaned = String(value).replace(/%/g, "").replace(/,/g, "").trim();
  if (!cleaned || cleaned === "-") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? Math.round(n * 10000) / 10000 : null;
}

function inTradingClock(clock) {
  const [h, m] = clock.split(":").map((x) => Number(x));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return false;
  const total = h * 60 + m;
  return total >= 9 * 60 + 30 && total <= 15 * 60;
}

function parseJsonPayload(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("{")) return JSON.parse(trimmed);
  const match = trimmed.match(/\((\{.*\})\)\s*;?\s*$/s);
  if (match) return JSON.parse(match[1]);
  return JSON.parse(trimmed);
}

function priorCloseFromKlines(klines, tradeDate) {
  let priorClose = null;
  let priorDay = null;
  for (const raw of klines) {
    if (typeof raw !== "string") continue;
    const parts = raw.split(",");
    if (parts.length < 3) continue;
    const day = parts[0].trim().split(" ")[0];
    if (!tradeDate || day >= tradeDate) continue;
    const close = asFloat(parts[2]);
    if (close == null || close <= 0) continue;
    if (priorDay == null || day > priorDay) {
      priorDay = day;
      priorClose = close;
    }
  }
  return priorClose;
}

function parseKlineMinute(payload, tradeDate) {
  const data = payload?.data ?? {};
  const klines = data.klines ?? [];
  let preClose = asFloat(data.preKPrice);
  if (preClose != null && preClose <= 0) preClose = null;
  if (preClose == null) preClose = asFloat(data.preClose) ?? asFloat(data.yestclose);
  if ((preClose == null || preClose <= 0) && tradeDate) {
    preClose = priorCloseFromKlines(klines, tradeDate);
  }

  const rows = [];
  for (const raw of klines) {
    if (typeof raw !== "string") continue;
    const parts = raw.split(",");
    if (parts.length < 3) continue;
    const dt = parts[0].trim().split(" ");
    if (dt.length < 2) continue;
    const [day, clockFull] = dt;
    const clock = clockFull.slice(0, 5);
    if (tradeDate && day !== tradeDate) continue;
    if (!inTradingClock(clock)) continue;
    const close = asFloat(parts[2]);
    if (close == null || close <= 0) continue;
    const changePct = parts.length > 8 ? asFloat(parts[8]) : null;
    rows.push({ clock, close, changePct });
  }
  if (rows.length < 2) return [];

  return rows
    .map(({ clock, close, changePct }) => {
      let percent;
      if (preClose != null && preClose > 0) {
        percent = Math.round((close / preClose - 1) * 10000) / 10000;
      } else if (changePct != null) {
        percent = changePct;
      } else {
        return null;
      }
      return { time: clock, percent };
    })
    .filter(Boolean);
}

function parseTrends(payload, tradeDate) {
  const data = payload?.data ?? {};
  const trends = data.trends ?? [];
  if (!trends.length) return [];

  let preClose = asFloat(data.prePrice) ?? asFloat(data.preClose) ?? asFloat(data.yestclose);
  const points = [];
  for (const raw of trends) {
    if (typeof raw !== "string") continue;
    const parts = raw.split(",");
    if (parts.length < 3) continue;
    const dt = parts[0].trim().split(" ");
    if (dt.length < 2) continue;
    const [day, clockFull] = dt;
    const clock = clockFull.slice(0, 5);
    if (tradeDate && day !== tradeDate) continue;
    if (!inTradingClock(clock)) continue;
    let price = asFloat(parts[2]);
    if (price == null) price = asFloat(parts[parts.length - 1]);
    if (price == null) continue;
    if (preClose == null || preClose <= 0) {
      if (!points.length) preClose = price;
    }
    const percent =
      preClose != null && preClose > 0
        ? Math.round((price / preClose - 1) * 10000) / 10000
        : 0;
    points.push({ time: clock, percent });
  }
  return points.length >= 2 ? points : [];
}

function payloadFromUrl(url) {
  if (url.includes("kline/get") && /(?:^|[?&])klt=1(?:&|$)/.test(url)) {
    return "kline";
  }
  if (url.includes("trends2/get")) {
    return "trends";
  }
  return null;
}

function refererFor(secid, sourceCode) {
  const code = (sourceCode || "").trim();
  let market = "2";
  if (secid && secid.includes(".")) {
    [market] = secid.split(".");
  }
  if (code && /^\d+$/.test(code)) {
    return `https://quote.eastmoney.com/zz/${market}.${code}.html`;
  }
  if (secid?.startsWith("90.")) {
    return `https://quote.eastmoney.com/bk/${secid}.html`;
  }
  return "https://quote.eastmoney.com/";
}

async function captureFromPage(page, pageUrl, tradeDate, timeoutMs) {
  let best = [];

  const onResponse = async (response) => {
    const kind = payloadFromUrl(response.url());
    if (!kind) return;
    try {
      const text = await response.text();
      const payload = parseJsonPayload(text);
      const points =
        kind === "kline"
          ? parseKlineMinute(payload, tradeDate)
          : parseTrends(payload, tradeDate);
      if (points.length > best.length) {
        best = points;
      }
    } catch {
      /* ignore parse errors */
    }
  };

  page.on("response", onResponse);
  try {
    await page.goto(pageUrl, {
      waitUntil: "domcontentloaded",
      timeout: Math.max(12000, timeoutMs),
    });
    await page.waitForTimeout(6000);
  } finally {
    page.off("response", onResponse);
  }
  return best;
}

const KLINE_UT = "fa5fd1943c7b386f172d6893dbfba10b";
const TRENDS_UT = "bd1d9ddb04089700cf9c27f6f7426281";

async function fetchJson(page, url) {
  return page.evaluate(async (targetUrl) => {
    try {
      const response = await fetch(targetUrl, { credentials: "omit", cache: "no-store" });
      const text = await response.text();
      return { ok: response.ok, status: response.status, text };
    } catch (error) {
      return { ok: false, status: 0, text: "", error: String(error) };
    }
  }, url);
}

async function manualMinuteFetch(page, secid, tradeDate) {
  const ymd = (tradeDate || "").replace(/-/g, "");
  const hosts = ["push2delay.eastmoney.com", "88.push2.eastmoney.com", "79.push2.eastmoney.com"];
  const begEnds = ymd ? [[ymd, ymd], ["0", "20500000"]] : [["0", "20500000"]];
  const preferKline = secid.startsWith("2.");

  const tryKline = async () => {
    for (const [beg, end] of begEnds) {
      for (const host of hosts) {
        const params = new URLSearchParams({
          secid,
          klt: "1",
          fqt: "0",
          beg,
          end,
          fields1: "f1,f2,f3,f4,f5,f6",
          fields2: "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
          ut: KLINE_UT,
          invt: "2",
          fltt: "2",
          lmt: "1000000",
        });
        const result = await fetchJson(page, `https://${host}/api/qt/stock/kline/get?${params}`);
        if (!result.ok || !result.text) continue;
        try {
          const points = parseKlineMinute(parseJsonPayload(result.text), tradeDate);
          if (points.length >= 2) return points;
        } catch {
          /* next */
        }
      }
    }
    return [];
  };

  const tryTrends = async () => {
    for (const host of hosts) {
      const params = new URLSearchParams({
        secid,
        fields1: "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        fields2: "f51,f52,f53,f54,f55,f56,f57,f58",
        iscr: "0",
        ndays: tradeDate ? "1" : "5",
        ut: TRENDS_UT,
        invt: "2",
        fltt: "2",
      });
      const result = await fetchJson(
        page,
        `https://${host}/api/qt/stock/trends2/get?${params}`,
      );
      if (!result.ok || !result.text) continue;
      try {
        const points = parseTrends(parseJsonPayload(result.text), tradeDate);
        if (points.length >= 2) return points;
      } catch {
        /* next */
      }
    }
    return [];
  };

  if (preferKline) {
    const k = await tryKline();
    if (k.length) return k;
    return tryTrends();
  }
  const t = await tryTrends();
  if (t.length) return t;
  return tryKline();
}

async function main() {
  const secid = (process.env.FUND_AI_INTRADAY_SECID || "").trim();
  const sourceCode = (process.env.FUND_AI_INTRADAY_SOURCE_CODE || "").trim();
  const tradeDate = (process.env.FUND_AI_INTRADAY_TRADE_DATE || "").trim() || null;
  if (!secid) {
    process.stderr.write("missing FUND_AI_INTRADAY_SECID\n");
    process.exit(1);
  }

  const timeoutMs =
    Number(process.env.FUND_AI_SECTOR_QUOTES_TIMEOUT_SECONDS || "20") * 1000;
  const pageUrl = refererFor(secid, sourceCode);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    let points = await captureFromPage(page, pageUrl, tradeDate, timeoutMs);
    if (points.length < 2) {
      points = await manualMinuteFetch(page, secid, tradeDate);
    }
    process.stdout.write(
      `${JSON.stringify({ secid, trade_date: tradeDate, points })}\n`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${String(error)}\n`);
  process.exit(1);
});
