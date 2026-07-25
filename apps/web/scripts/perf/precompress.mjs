#!/usr/bin/env node
/**
 * 构建后预压缩静态产物（零第三方依赖，只用 node 内置 zlib）。
 *
 * 目的：nginx 的 `gzip_static on` 会优先直接发送同名 `.gz`，于是
 * - 压缩级别从运行时的 gzip level 5 提升到构建期的 level 9；
 * - 每个请求都不再花 CPU 现压。
 *
 * 只处理文本类产物，且跳过压不动的小文件。产物与源文件同批生成，
 * 不会出现「`.gz` 与源文件不同步」这种会导致解压失败的情况。
 */

import { constants, gzipSync } from "node:zlib";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const outDir = join(webRoot, "out");

const COMPRESSIBLE = new Set([".js", ".css", ".html", ".json", ".svg", ".txt", ".xml", ".map"]);
/** 小于这个体积时 gzip 基本没有收益，还会多出一个文件。 */
const MIN_BYTES = 1024;
/** 压不下去（例如已压缩过的内容）就不产出 `.gz`。 */
const MIN_RATIO = 0.95;

function walk(dir, acc = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, acc);
    else if (entry.isFile()) acc.push(full);
  }
  return acc;
}

function main() {
  let files;
  try {
    files = walk(outDir);
  } catch {
    files = [];
  }
  if (files.length === 0) {
    console.error(`跳过预压缩：找不到产物目录 ${relative(webRoot, outDir)}`);
    process.exit(0);
  }

  let written = 0;
  let rawBytes = 0;
  let gzBytes = 0;
  let skipped = 0;

  for (const file of files) {
    if (file.endsWith(".gz") || file.endsWith(".br")) continue;
    if (!COMPRESSIBLE.has(extname(file))) continue;
    const raw = readFileSync(file);
    if (raw.byteLength < MIN_BYTES) {
      skipped += 1;
      continue;
    }
    const gz = gzipSync(raw, { level: constants.Z_BEST_COMPRESSION });
    if (gz.byteLength >= raw.byteLength * MIN_RATIO) {
      skipped += 1;
      continue;
    }
    writeFileSync(`${file}.gz`, gz);
    written += 1;
    rawBytes += raw.byteLength;
    gzBytes += gz.byteLength;
  }

  const kib = (n) => `${(n / 1024).toFixed(1)} KiB`;
  const ratio = rawBytes > 0 ? ((1 - gzBytes / rawBytes) * 100).toFixed(1) : "0.0";
  console.log(
    `预压缩完成：${written} 个 .gz（跳过 ${skipped} 个），` +
      `${kib(rawBytes)} -> ${kib(gzBytes)}，压缩率 ${ratio}%`,
  );
}

main();
