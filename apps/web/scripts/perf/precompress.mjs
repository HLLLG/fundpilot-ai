#!/usr/bin/env node
/**
 * 构建后预压缩静态产物（零第三方依赖，只用 node 内置 zlib）。
 *
 * 目的：nginx 的 `gzip_static` / `brotli_static` 会优先直接发送同名 `.gz` / `.br`，于是
 * - 压缩级别从运行时的 gzip level 5 提升到构建期的 gzip -9 / brotli q11；
 * - 每个请求都不再花 CPU 现压。
 *
 * 实测（2026-07-25 产物）：首屏合计 gzip -9 171.1 KiB -> brotli q11 143.2 KiB（-16.3%）；
 * 全量可压缩产物 639.3 -> 538.3 KiB（-15.8%）。
 *
 * `.br` 只有在配套的自建 brotli nginx 镜像下才会被用到（见 deploy/nginx/Dockerfile）；
 * 官方镜像下它只是多出来的文件，不影响任何请求。
 *
 * 只处理文本类产物，且跳过压不动的小文件。产物与源文件同批生成，
 * 不会出现「预压缩文件与源文件不同步」这种会导致解压失败的情况。
 */

import { brotliCompressSync, constants, gzipSync } from "node:zlib";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const outDir = join(webRoot, "out");

const COMPRESSIBLE = new Set([".js", ".css", ".html", ".json", ".svg", ".txt", ".xml", ".map"]);
/** 小于这个体积时压缩基本没有收益，还会多出文件。 */
const MIN_BYTES = 1024;
/** 压不下去（例如已压缩过的内容）就不产出对应文件。 */
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

function brotli(buffer) {
  return brotliCompressSync(buffer, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
      [constants.BROTLI_PARAM_SIZE_HINT]: buffer.byteLength,
    },
  });
}

function main() {
  const files = walk(outDir);
  if (files.length === 0) {
    console.error(`跳过预压缩：找不到产物目录 ${relative(webRoot, outDir)}`);
    process.exit(0);
  }

  const stats = {
    raw: 0,
    gzip: { written: 0, bytes: 0 },
    brotli: { written: 0, bytes: 0 },
    skipped: 0,
  };

  for (const file of files) {
    if (file.endsWith(".gz") || file.endsWith(".br")) continue;
    if (!COMPRESSIBLE.has(extname(file))) continue;
    const raw = readFileSync(file);
    if (raw.byteLength < MIN_BYTES) {
      stats.skipped += 1;
      continue;
    }
    stats.raw += raw.byteLength;

    const gz = gzipSync(raw, { level: constants.Z_BEST_COMPRESSION });
    if (gz.byteLength < raw.byteLength * MIN_RATIO) {
      writeFileSync(`${file}.gz`, gz);
      stats.gzip.written += 1;
      stats.gzip.bytes += gz.byteLength;
    }

    const br = brotli(raw);
    if (br.byteLength < raw.byteLength * MIN_RATIO) {
      writeFileSync(`${file}.br`, br);
      stats.brotli.written += 1;
      stats.brotli.bytes += br.byteLength;
    }
  }

  const kib = (n) => `${(n / 1024).toFixed(1)} KiB`;
  const ratio = (n) => (stats.raw > 0 ? `${((1 - n / stats.raw) * 100).toFixed(1)}%` : "0.0%");
  console.log(
    `预压缩完成（跳过 ${stats.skipped} 个小文件）：原始 ${kib(stats.raw)}\n` +
      `  gzip -9    ${stats.gzip.written} 个  ${kib(stats.gzip.bytes)}  压缩率 ${ratio(stats.gzip.bytes)}\n` +
      `  brotli q11 ${stats.brotli.written} 个  ${kib(stats.brotli.bytes)}  压缩率 ${ratio(stats.brotli.bytes)}`,
  );
}

main();
