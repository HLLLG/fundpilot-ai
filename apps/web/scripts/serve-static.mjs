import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { gzipSync } from "node:zlib";

/**
 * 生产 nginx 会优先直接发送构建期生成的同名预压缩产物：官方镜像用 `gzip_static`
 * 发 `.gz`，自建 brotli 镜像在静态 location 里用 `brotli_static` 发 `.br`
 * （见 deploy/nginx/Dockerfile）。本地预览服务器保持同样的 `Content-Encoding`
 * 协商优先级（br 优于 gzip），避免"本地走现压、线上走预压缩"两套口径。
 */
async function readPrecompressed(file, acceptEncoding) {
  const candidates = [];
  if (acceptEncoding.includes("br")) candidates.push(["br", `${file}.br`]);
  if (acceptEncoding.includes("gzip")) candidates.push(["gzip", `${file}.gz`]);
  for (const [encoding, path] of candidates) {
    try {
      const info = await stat(path);
      if (info.isFile()) return { encoding, body: await readFile(path) };
    } catch {
      // 缺预压缩产物时继续尝试下一种编码，最后回落到实时 gzip。
    }
  }
  return null;
}

const root = join(process.cwd(), "out");
const port = Number(process.env.PORT || 3001);
const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};

function resolveFile(urlPath) {
  const clean = decodeURIComponent(urlPath.split("?")[0] || "/");
  const relative = clean === "/" ? "index.html" : clean.replace(/^\/+/, "");
  const exportedRscPath = relative.replace(
    /(\b__next\.[^/]+)\.__PAGE__\.txt$/,
    "$1/__PAGE__.txt",
  );
  const normalized = normalize(exportedRscPath);
  if (normalized.startsWith("..")) return null;
  return join(root, extname(normalized) ? normalized : `${normalized}.html`);
}

const server = createServer(async (request, response) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405).end();
    return;
  }
  const file = resolveFile(request.url || "/");
  if (!file) {
    response.writeHead(400).end();
    return;
  }
  try {
    const info = await stat(file);
    if (!info.isFile()) throw new Error("not a file");
    const rawBody = request.method === "HEAD" ? null : await readFile(file);
    const compressible = new Set([".css", ".html", ".js", ".json", ".svg", ".txt"]);
    const acceptEncoding = request.headers["accept-encoding"] ?? "";
    const canCompress = Boolean(rawBody && compressible.has(extname(file)));
    const precompressed = canCompress ? await readPrecompressed(file, acceptEncoding) : null;
    let encoding = null;
    let body = rawBody;
    if (precompressed) {
      encoding = precompressed.encoding;
      body = precompressed.body;
    } else if (canCompress && acceptEncoding.includes("gzip") && rawBody) {
      encoding = "gzip";
      body = gzipSync(rawBody);
    }
    response.writeHead(200, {
      "content-type": contentTypes[extname(file)] || "application/octet-stream",
      "cache-control": "no-store",
      ...(encoding ? { "content-encoding": encoding, vary: "Accept-Encoding" } : {}),
    });
    response.end(body);
  } catch {
    const notFound = join(root, "404.html");
    const body = request.method === "HEAD" ? null : await readFile(notFound).catch(() => null);
    response.writeHead(404, { "content-type": "text/html; charset=utf-8" });
    response.end(body);
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Static production preview listening on http://127.0.0.1:${port}`);
});
