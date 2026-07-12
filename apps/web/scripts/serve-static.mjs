import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { gzipSync } from "node:zlib";

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
    const shouldGzip = Boolean(
      rawBody &&
      compressible.has(extname(file)) &&
      request.headers["accept-encoding"]?.includes("gzip"),
    );
    const body = shouldGzip && rawBody ? gzipSync(rawBody) : rawBody;
    response.writeHead(200, {
      "content-type": contentTypes[extname(file)] || "application/octet-stream",
      "cache-control": "no-store",
      ...(shouldGzip ? { "content-encoding": "gzip", vary: "Accept-Encoding" } : {}),
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
