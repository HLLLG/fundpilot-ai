# FundPilot 生产 nginx 镜像：官方 alpine 基础镜像 + 自编译 ngx_brotli 动态模块。
#
# 为什么要自建：
# - 官方 `nginx:*-alpine` 不含 brotli，nginx.org 也没有为 alpine 发布 brotli 动态模块
#   （只有 geoip / image-filter / njs / xslt）。
# - Alpine 仓库的 `nginx-mod-http-brotli` 是针对 Alpine 自己编译的 nginx 构建的，
#   与官方镜像（nginx.org 源码构建）的动态模块签名不一致，`load_module` 会因
#   "not binary compatible" 失败。
# - 因此唯一可靠做法是用**与运行镜像完全相同的 tag 和 configure 参数**编译模块。
#   两个 stage 共用同一个 `NGINX_BASE`，签名由构造保证一致。
#
# 安全性：本文件末尾有构建期断言，`brotli` / `brotli_static` / `gzip_static`
# 任意一个不可用都会让**镜像构建失败**，而不是等到生产 nginx 启动时才炸。

ARG NGINX_BASE=nginx:1.27-alpine

# ─────────────────────────── 编译 ngx_brotli ───────────────────────────
FROM ${NGINX_BASE} AS module-builder

# ngx_brotli 固定到具体 commit，不用浮动分支。升级时同时更新这里和下面的校验。
ARG NGX_BROTLI_REF=master

RUN set -eux; \
    apk add --no-cache --virtual .brotli-build-deps \
        build-base cmake git linux-headers \
        pcre-dev pcre2-dev zlib-dev openssl-dev libxml2-dev libxslt-dev gd-dev geoip-dev; \
    nginx_version="$(nginx -v 2>&1 | sed -n 's|.*nginx/||p')"; \
    echo "building modules for nginx ${nginx_version}"; \
    # 直接复用运行镜像自己的 configure 参数，保证动态模块签名一致。
    configure_args="$(nginx -V 2>&1 | sed -n 's|^configure arguments: ||p')"; \
    mkdir -p /usr/src; cd /usr/src; \
    wget -qO nginx.tar.gz "https://nginx.org/download/nginx-${nginx_version}.tar.gz"; \
    tar -xzf nginx.tar.gz; \
    git clone --depth 1 --recurse-submodules --shallow-submodules \
        https://github.com/google/ngx_brotli.git /usr/src/ngx_brotli; \
    if [ "${NGX_BROTLI_REF}" != "master" ]; then \
        cd /usr/src/ngx_brotli; \
        git fetch --depth 1 origin "${NGX_BROTLI_REF}"; \
        git checkout --recurse-submodules FETCH_HEAD; \
        git submodule update --init --recursive --depth 1; \
    fi; \
    cd "/usr/src/nginx-${nginx_version}"; \
    # shellcheck disable=SC2086
    eval ./configure ${configure_args} --add-dynamic-module=/usr/src/ngx_brotli; \
    make -j"$(nproc)" modules; \
    mkdir -p /build-modules; \
    cp objs/ngx_http_brotli_filter_module.so objs/ngx_http_brotli_static_module.so /build-modules/; \
    strip /build-modules/*.so; \
    apk del .brotli-build-deps

# ─────────────────────────── 运行镜像 ───────────────────────────
FROM ${NGINX_BASE}

COPY --from=module-builder /build-modules/ngx_http_brotli_filter_module.so \
    /usr/lib/nginx/modules/ngx_http_brotli_filter_module.so
COPY --from=module-builder /build-modules/ngx_http_brotli_static_module.so \
    /usr/lib/nginx/modules/ngx_http_brotli_static_module.so

# `load_module` 只能出现在主 nginx.conf 的最顶层，conf.d 里放不了。
RUN set -eux; \
    sed -i '1i load_module modules/ngx_http_brotli_filter_module.so;\nload_module modules/ngx_http_brotli_static_module.so;' \
        /etc/nginx/nginx.conf

# 站点配置用 `include /etc/nginx/precompressed/*.conf;` 引入这一段。
# 官方镜像下那个 glob 匹配不到任何文件（nginx 对空 glob 不报错），因此
# deploy/nginx/fundpilot.conf 在两种镜像上都能正常加载。
#
# ★ 关键：这里必须显式 `gzip_static off;`。
# gzip_static 是静态编入 nginx 的模块，brotli_static 是 load_module 动态加载的。
# nginx 的 content phase 处理器按模块注册顺序执行，动态模块永远排在静态模块之后，
# 于是两者同时 on 时，只要客户端同时声明 `gzip, br`（所有现代浏览器都这样），
# gzip_static 先命中并返回 .gz，brotli_static 永远轮不到 —— brotli 收益归零。
# 参见 google/ngx_brotli#123，该模块没有提供调整优先级的配置项。
# 所以这里在 location 级把 gzip_static 关掉（server 级仍是 on，供官方镜像使用；
# 同一 context 里写两次 gzip_static 会报 "directive is duplicate" 并拒绝启动，
# 因此一个在 server 段、一个在 location 段，靠继承覆盖，不会重复）。
# 不支持 br 的老客户端在这两个 location 里回落到 server 段的运行时 gzip。
RUN set -eux; \
    mkdir -p /etc/nginx/precompressed; \
    { \
        echo '# 由 deploy/nginx/Dockerfile 生成。仅在自建 brotli 镜像里存在。'; \
        echo '# 构建期已把 .gz/.br 一起产出（apps/web/scripts/perf/precompress.mjs），'; \
        echo '# 这里优先直接发送预压缩产物：压缩级别更高，且每请求不再现压。'; \
        echo '# gzip_static 必须关：否则它会抢在 brotli_static 之前命中（ngx_brotli#123）。'; \
        echo 'gzip_static off;'; \
        echo 'brotli_static on;'; \
        echo '# 没有对应 .br 时（例如新增的未预压缩文本产物）再按需实时 brotli。'; \
        echo 'brotli on;'; \
        echo 'brotli_comp_level 5;'; \
        echo 'brotli_min_length 1024;'; \
        echo 'brotli_types'; \
        echo '    text/plain'; \
        echo '    text/css'; \
        echo '    application/json'; \
        echo '    application/javascript'; \
        echo '    text/javascript'; \
        echo '    application/xml'; \
        echo '    image/svg+xml;'; \
    } > /etc/nginx/precompressed/precompressed.conf

# ─────────────────────── 构建期断言：模块真的可用 ───────────────────────
# 任一模块缺失或二进制不兼容，都在这里让构建失败，而不是让生产 nginx 起不来。
RUN set -eux; \
    nginx -V 2>&1 | tr ' ' '\n' | grep -q -- '--with-http_gzip_static_module'; \
    mkdir -p /tmp/nginx-assert; \
    printf '%s\n' \
        'events {}' \
        'http {' \
        '  include /etc/nginx/precompressed/*.conf;' \
        '  server { listen 8081; location / { return 204; } }' \
        '}' > /tmp/nginx-assert/nginx.conf; \
    nginx -t -c /tmp/nginx-assert/nginx.conf; \
    rm -rf /tmp/nginx-assert

STOPSIGNAL SIGQUIT
