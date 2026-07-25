#!/usr/bin/env node
/**
 * 跨平台启用 bundle 分析的 production build。
 * 报告写到 `.next/analyze/`，正常 `npm run build` 完全不受影响。
 */
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const child = spawn(
  process.execPath,
  [resolve(webRoot, "node_modules", "next", "dist", "bin", "next"), "build", "--webpack"],
  {
    cwd: webRoot,
    stdio: "inherit",
    env: { ...process.env, ANALYZE: "true", NEXT_TELEMETRY_DISABLED: "1" },
  },
);
child.on("exit", (code) => process.exit(code ?? 1));
