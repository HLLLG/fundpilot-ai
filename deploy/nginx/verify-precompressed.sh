#!/usr/bin/env bash
# 验证 nginx 静态层的「预压缩产物协商」行为是否真的生效。
#
# 为什么需要这个脚本：`nginx -t` 只能证明配置语法正确，证明不了
#   1) 客户端同时声明 `gzip, br` 时到底发的是哪一种；
#   2) 发出去的是构建期预压缩产物，还是运行时重新压的。
# 而这两点恰好是最容易静默失效的地方 —— gzip_static 是静态编入模块、
# brotli_static 是动态模块，nginx 的 content phase 按模块注册顺序执行，
# 动态模块永远在后面，两者同时 on 时 brotli 永远轮不到（google/ngx_brotli#123）。
# 所以必须用真实请求验证，而不是"配置写了就当生效"。
#
# 判定「是否预压缩」的依据：预压缩文件由静态模块直接发送，响应带精确的
# Content-Length；运行时压缩的响应是 chunked，没有 Content-Length。
#
# 用法：
#   deploy/nginx/verify-precompressed.sh <镜像> <gzip+br 时期望的编码>
# 例：
#   deploy/nginx/verify-precompressed.sh nginx:1.27-alpine       gzip
#   deploy/nginx/verify-precompressed.sh fundpilot-nginx:brotli  br

set -Eeuo pipefail

image="${1:?usage: $0 <image> <expected-encoding: gzip|br>}"
expected="${2:?usage: $0 <image> <expected-encoding: gzip|br>}"
case "$expected" in
    gzip | br) ;;
    *)
        echo "expected encoding must be gzip or br, got: $expected" >&2
        exit 64
        ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
site_conf="$repo_root/deploy/nginx/fundpilot.conf"
if [[ ! -f "$site_conf" ]]; then
    echo "site config not found: $site_conf" >&2
    exit 66
fi

workdir="$(mktemp -d)"
container="nginx-precompress-probe-$$"
host_port=""
failures=0

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    rm -rf "$workdir"
}
trap cleanup EXIT

cert_dir="$workdir/letsencrypt/live/hllingxi.cn"
web_root="$workdir/web"
mkdir -p "$cert_dir" "$web_root/_next/static/chunks"

# 一次性自签证书：`listen 443 ssl` 要求 nginx 能打开证书文件，连 `nginx -t` 也会打开。
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj "/CN=www.hllingxi.cn" \
    -addext "subjectAltName=DNS:www.hllingxi.cn,DNS:hllingxi.cn" \
    -keyout "$cert_dir/privkey.pem" \
    -out "$cert_dir/fullchain.pem" 2>/dev/null

# 造两个 >1KB 的可压缩探针，并按 nginx 期望的命名产出 .gz / .br。
# 这里直接复用 node 内置 zlib，与 apps/web/scripts/perf/precompress.mjs 同一口径。
PROBE_WEB_ROOT="$web_root" node - <<'NODE'
const { brotliCompressSync, constants, gzipSync } = require("node:zlib");
const { writeFileSync } = require("node:fs");
const { join } = require("node:path");

const webRoot = process.env.PROBE_WEB_ROOT;
const filler = "// precompression probe payload ".repeat(120);
const targets = [
  [join(webRoot, "index.html"), `<!doctype html><html lang="zh-CN"><body><!--${filler}--></body></html>`],
  [join(webRoot, "_next", "static", "chunks", "probe.js"), `export const probe = 1;\n${filler}\n`],
];

for (const [file, body] of targets) {
  const raw = Buffer.from(body, "utf8");
  if (raw.byteLength < 1024) throw new Error(`probe too small to be compressed: ${file}`);
  writeFileSync(file, raw);
  writeFileSync(`${file}.gz`, gzipSync(raw, { level: constants.Z_BEST_COMPRESSION }));
  writeFileSync(
    `${file}.br`,
    brotliCompressSync(raw, {
      params: {
        [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
        [constants.BROTLI_PARAM_SIZE_HINT]: raw.byteLength,
      },
    }),
  );
}
console.log("probe assets written");
NODE

# 只放开 web 根的读权限（nginx worker 以 nginx 用户读静态文件）。
# 证书目录保持默认权限：容器里读证书的是 master 进程（root），不需要放宽。
chmod -R a+rX "$web_root"

# `--add-host api:127.0.0.1`：nginx 在解析配置阶段就会解析 upstream 主机名，
# 一次性容器不在 compose 网络里，没有这一行会以 "host not found in upstream" 失败。
docker_common=(
    --add-host api:127.0.0.1
    -v "$site_conf:/etc/nginx/conf.d/default.conf:ro"
    -v "$workdir/letsencrypt:/etc/letsencrypt:ro"
    -v "$web_root:/usr/share/nginx/html:ro"
)

echo "== [$image] nginx -t =="
docker run --rm "${docker_common[@]}" "$image" nginx -t

echo "== [$image] 启动探针容器 =="
docker run -d --name "$container" "${docker_common[@]}" -p 127.0.0.1:0:443 "$image" >/dev/null
host_port="$(docker port "$container" 443/tcp | head -n 1 | sed 's/.*://')"
if [[ -z "$host_port" ]]; then
    echo "could not resolve published port for $container" >&2
    docker logs "$container" >&2 || true
    exit 70
fi

ready=false
for _ in $(seq 1 30); do
    if curl -ksS -o /dev/null "https://127.0.0.1:$host_port/" 2>/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != "true" ]]; then
    echo "nginx did not become reachable on port $host_port" >&2
    docker logs "$container" >&2 || true
    exit 70
fi

# 返回 "<content-encoding>|<content-length>"，缺失的字段返回 "-"。
probe() {
    local accept_encoding="$1" path="$2" headers encoding length
    headers="$(curl -ksS -o /dev/null -D - \
        --resolve "www.hllingxi.cn:$host_port:127.0.0.1" \
        -H "Accept-Encoding: $accept_encoding" \
        "https://www.hllingxi.cn:$host_port$path" | tr -d '\r')"
    encoding="$(printf '%s\n' "$headers" | sed -n 's/^[Cc]ontent-[Ee]ncoding: *//p' | tail -n 1)"
    length="$(printf '%s\n' "$headers" | sed -n 's/^[Cc]ontent-[Ll]ength: *//p' | tail -n 1)"
    printf '%s|%s' "${encoding:--}" "${length:--}"
}

check() {
    local label="$1" actual="$2" want="$3"
    if [[ "$actual" == "$want" ]]; then
        echo "  PASS  $label -> $actual"
    else
        echo "  FAIL  $label -> $actual (expected $want)" >&2
        failures=$((failures + 1))
    fi
}

for path in /_next/static/chunks/probe.js /index.html; do
    file="$web_root$path"
    raw_size="$(stat -c %s "$file")"
    gz_size="$(stat -c %s "$file.gz")"
    br_size="$(stat -c %s "$file.br")"

    echo "== [$image] $path =="

    # 现代浏览器发的就是这一种。必须发预压缩产物，且编码符合镜像能力。
    case "$expected" in
        gzip) want="gzip|$gz_size" ;;
        br) want="br|$br_size" ;;
    esac
    check "Accept-Encoding: gzip, br" "$(probe 'gzip, deflate, br' "$path")" "$want"

    # 只声明 gzip 的老客户端：官方镜像发预压缩 .gz；自建镜像在这两个 location 里
    # 关掉了 gzip_static，于是回落到运行时 gzip（chunked，没有 Content-Length）。
    case "$expected" in
        gzip) want="gzip|$gz_size" ;;
        br) want="gzip|-" ;;
    esac
    check "Accept-Encoding: gzip" "$(probe 'gzip' "$path")" "$want"

    # 不接受任何压缩：必须原样返回，长度等于源文件。
    check "Accept-Encoding: identity" "$(probe 'identity' "$path")" "-|$raw_size"
done

if [[ "$failures" -ne 0 ]]; then
    echo "$failures 项预压缩协商断言失败（镜像 $image）" >&2
    exit 1
fi
echo "预压缩协商断言全部通过（镜像 $image，gzip+br 客户端拿到 $expected）"
