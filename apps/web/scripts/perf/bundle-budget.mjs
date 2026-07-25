#!/usr/bin/env node
/**
 * 首屏资源体积报告与预算门禁（零第三方依赖）。
 *
 * 口径：只统计静态导出 HTML 真正会让浏览器在首屏下载的资源。
 * - `<script src>` 计入，但带 `noModule` 的 legacy polyfill 不计入（现代浏览器会跳过）。
 * - `<link rel="stylesheet">` 计入。
 * - RSC flight payload 内嵌的字符串引用不计入（它们不是首屏请求）。
 *
 * 用法：
 *   node scripts/perf/bundle-budget.mjs                     # 打印报告
 *   node scripts/perf/bundle-budget.mjs --json out.json     # 额外写 JSON
 *   node scripts/perf/bundle-budget.mjs --budget            # 超预算时退出码 1
 *   node scripts/perf/bundle-budget.mjs --baseline b.json   # 与基线对比
 */

import { createRequire } from "node:module";
import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..", "..");
const outDir = join(webRoot, "out");
const budgetPath = join(scriptDir, "budget.config.json");

/** 参与首屏统计的路由入口。key 是报告里显示的路由名，value 是导出的 HTML 文件。 */
const ROUTE_ENTRIES = {
  "/": "index.html",
  "/login": "login.html",
  "/register": "register.html",
  "/reset-password": "reset-password.html",
  "/settings": "settings.html",
  "/admin/users": join("admin", "users.html"),
};

function parseArgs(argv) {
  const args = { json: null, budget: false, baseline: null };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--budget") args.budget = true;
    else if (token === "--json") args.json = argv[(index += 1)];
    else if (token === "--baseline") args.baseline = argv[(index += 1)];
  }
  return args;
}

function readOptionalJson(path) {
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

/** 递归列出目录下的文件相对路径。 */
function walk(dir, base = dir, acc = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, base, acc);
    else if (entry.isFile()) acc.push(relative(base, full).split("\\").join("/"));
  }
  return acc;
}

/**
 * 从导出的 HTML 里取出首屏真正会下载的静态资源。
 * 只解析标签属性，避免把 RSC payload 里被转义的路径字符串算进来。
 */
function collectEntryAssets(html) {
  const assets = [];
  const seen = new Set();

  const push = (href, kind) => {
    if (!href || !href.startsWith("/_next/")) return;
    const clean = href.split("?")[0];
    const key = `${kind}:${clean}`;
    if (seen.has(key)) return;
    seen.add(key);
    assets.push({ url: clean, kind });
  };

  for (const match of html.matchAll(/<script\b[^>]*>/gi)) {
    const tag = match[0];
    if (/\bnomodule\b/i.test(tag)) continue;
    const src = /\ssrc=["']([^"']+)["']/i.exec(tag);
    if (src) push(src[1], "js");
  }

  for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
    const tag = match[0];
    if (!/\brel=["']stylesheet["']/i.test(tag)) continue;
    const href = /\shref=["']([^"']+)["']/i.exec(tag);
    if (href) push(href[1], "css");
  }

  return assets;
}

function measureAsset(asset) {
  const filePath = join(outDir, asset.url.replace(/^\//, ""));
  let raw = null;
  try {
    raw = readFileSync(filePath);
  } catch {
    return { ...asset, missing: true, bytes: 0, gzipBytes: 0 };
  }
  return {
    ...asset,
    missing: false,
    bytes: raw.byteLength,
    gzipBytes: gzipSync(raw, { level: 9 }).byteLength,
  };
}

function summarizeRoute(htmlFile) {
  const htmlPath = join(outDir, htmlFile);
  let html;
  try {
    html = readFileSync(htmlPath, "utf8");
  } catch {
    return null;
  }
  const assets = collectEntryAssets(html).map(measureAsset);
  const sum = (kind, field) =>
    assets.filter((a) => a.kind === kind).reduce((total, a) => total + a[field], 0);

  // HTML 必须一起统计。否则「把 CSS 内联进 HTML」这类改动会让 cssBytes 归零、
  // totalBytes 大幅"改善"，而浏览器实际下载的字节反而变多 —— 指标必须挡住这种假收益。
  const htmlBytes = Buffer.byteLength(html);
  const htmlGzipBytes = gzipSync(Buffer.from(html), { level: 9 }).byteLength;

  return {
    html: htmlFile.split("\\").join("/"),
    htmlBytes,
    htmlGzipBytes,
    jsBytes: sum("js", "bytes"),
    jsGzipBytes: sum("js", "gzipBytes"),
    cssBytes: sum("css", "bytes"),
    cssGzipBytes: sum("css", "gzipBytes"),
    totalBytes: sum("js", "bytes") + sum("css", "bytes"),
    totalGzipBytes: sum("js", "gzipBytes") + sum("css", "gzipBytes"),
    // 首屏真实下载量 = 文档 + 首屏 JS + 首屏 CSS
    firstScreenBytes: htmlBytes + sum("js", "bytes") + sum("css", "bytes"),
    firstScreenGzipBytes: htmlGzipBytes + sum("js", "gzipBytes") + sum("css", "gzipBytes"),
    assetCount: assets.length,
    assets: assets
      .slice()
      .sort((left, right) => right.bytes - left.bytes)
      .map((asset) => ({
        url: asset.url,
        kind: asset.kind,
        bytes: asset.bytes,
        gzipBytes: asset.gzipBytes,
        ...(asset.missing ? { missing: true } : {}),
      })),
  };
}

function summarizeOutput() {
  const staticDir = join(outDir, "_next", "static");
  const files = walk(staticDir);
  const totals = { jsFiles: 0, jsBytes: 0, cssFiles: 0, cssBytes: 0 };
  for (const file of files) {
    const full = join(staticDir, file);
    const size = statSync(full).size;
    if (file.endsWith(".js")) {
      totals.jsFiles += 1;
      totals.jsBytes += size;
    } else if (file.endsWith(".css")) {
      totals.cssFiles += 1;
      totals.cssBytes += size;
    }
  }
  return totals;
}

function readNextVersion() {
  try {
    return require("next/package.json").version;
  } catch {
    return "unknown";
  }
}

function kib(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

function delta(current, baseline) {
  if (typeof baseline !== "number") return "";
  const diff = current - baseline;
  if (diff === 0) return "  (±0)";
  const sign = diff > 0 ? "+" : "-";
  return `  (${sign}${kib(Math.abs(diff))})`;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const baseline = readOptionalJson(args.baseline);
  const budget = readOptionalJson(budgetPath) ?? {};

  const routes = {};
  for (const [route, htmlFile] of Object.entries(ROUTE_ENTRIES)) {
    const summary = summarizeRoute(htmlFile);
    if (summary) routes[route] = summary;
  }

  if (Object.keys(routes).length === 0) {
    console.error(
      "找不到任何导出的 HTML。请先运行 `npm run build`（产物目录 apps/web/out）。",
    );
    process.exit(2);
  }

  const output = summarizeOutput();
  const report = {
    generatedAt: new Date().toISOString(),
    nextVersion: readNextVersion(),
    routes,
    output,
  };

  const lines = [];
  lines.push("首屏关键资源（不含 noModule legacy polyfill）");
  lines.push("");
  lines.push(
    "| 路由 | HTML | JS 原始 | CSS 原始 | 首屏合计原始 | 首屏合计 gzip -9 | 请求数 |",
  );
  lines.push("| --- | ---: | ---: | ---: | ---: | ---: | ---: |");
  for (const [route, summary] of Object.entries(routes)) {
    const base = baseline?.routes?.[route];
    lines.push(
      `| \`${route}\` | ${kib(summary.htmlBytes)}${delta(summary.htmlBytes, base?.htmlBytes)} | ` +
        `${kib(summary.jsBytes)}${delta(summary.jsBytes, base?.jsBytes)} | ` +
        `${kib(summary.cssBytes)}${delta(summary.cssBytes, base?.cssBytes)} | ` +
        `${kib(summary.firstScreenBytes)}${delta(summary.firstScreenBytes, base?.firstScreenBytes)} | ` +
        `${kib(summary.firstScreenGzipBytes)}${delta(summary.firstScreenGzipBytes, base?.firstScreenGzipBytes)} | ` +
        `${summary.assetCount} |`,
    );
  }
  lines.push("");
  lines.push(
    `全量产物：JS ${output.jsFiles} 个 / ${kib(output.jsBytes)}` +
      delta(output.jsBytes, baseline?.output?.jsBytes) +
      `，CSS ${output.cssFiles} 个 / ${kib(output.cssBytes)}` +
      delta(output.cssBytes, baseline?.output?.cssBytes),
  );
  lines.push("");
  lines.push("`/` 首屏资源明细：");
  for (const asset of routes["/"]?.assets ?? []) {
    lines.push(
      `  ${kib(asset.bytes).padStart(11)}  gzip ${kib(asset.gzipBytes).padStart(10)}  ${asset.kind}  ${asset.url}` +
        (asset.missing ? "  [缺失]" : ""),
    );
  }
  console.log(lines.join("\n"));

  if (args.json) {
    writeFileSync(args.json, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.log(`\n已写入 ${args.json}`);
  }

  if (!args.budget) return;

  const failures = [];
  for (const [route, limits] of Object.entries(budget.routes ?? {})) {
    const summary = routes[route];
    if (!summary) {
      failures.push(`预算配置了 ${route}，但产物里没有对应 HTML`);
      continue;
    }
    for (const [field, limit] of Object.entries(limits)) {
      if (typeof limit !== "number") continue;
      const actual = summary[field];
      if (typeof actual !== "number") continue;
      if (actual > limit) {
        failures.push(
          `${route} 的 ${field} 为 ${kib(actual)}，超过预算 ${kib(limit)}`,
        );
      }
    }
  }
  for (const [field, limit] of Object.entries(budget.output ?? {})) {
    if (typeof limit !== "number") continue;
    const actual = output[field];
    if (typeof actual === "number" && actual > limit) {
      failures.push(`全量产物 ${field} 为 ${actual}，超过预算 ${limit}`);
    }
  }

  if (failures.length > 0) {
    console.error(`\n体积预算未通过：\n- ${failures.join("\n- ")}`);
    process.exit(1);
  }
  console.log("\n体积预算通过。");
}

main();
