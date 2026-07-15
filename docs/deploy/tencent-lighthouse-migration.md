# FundPilot AI：迁移到腾讯云轻量服务器 Runbook

> 目标：将现有 CloudBase 静态托管、云托管 API 与 CloudBase MySQL 迁移到一台腾讯云轻量应用服务器。旧环境保留 7 天，作为受控回滚源。

## 1. 方案边界

本 Runbook 对应已选定的方案 A：

- 腾讯云轻量：4 核、4GB、3Mbps、Ubuntu 24.04、单台服务器。
- Nginx 托管 Next.js 静态前端，并将同域名的 `/api/` 代理到 FastAPI。
- FastAPI、MySQL 8.0 通过 Docker Compose 运行。
- 数据库、上传文件、应用缓存、静态前端与备份存放在宿主机 `/srv/fundpilot/`，不使用容器临时文件系统。
- CloudBase 仅在迁移后的 7 天内保留，不再接收正式流量。

不在本次范围内：多机高可用、RDS/Redis、负载均衡、自动发布流水线、同机 Playwright 行情浏览器。这些需求出现后再迁到标准 CVM/ECS。

## 2. 必须遵守的迁移规则

1. 最终导出开始后，旧站必须停止写入：不能新增持仓、生成报告、注册账号或修改配置。
2. 不要把 `.env.production`、SQL 导出文件、DeepSeek Key、百炼 Key 提交 Git 或发送到聊天记录。
3. MySQL 不开放公网端口 3306；只有 API 容器能通过 Docker 内网访问它。
4. DNS 切换后若新站已产生新数据，不能直接切回 CloudBase；先停止新写入并导出新库，否则会丢失切换后的数据。

## 3. 目标架构

    浏览器
      │ HTTPS https://app.example.com
      ▼
    Nginx
      ├─ /       → Next.js 静态导出文件
      └─ /api/*  → FastAPI（1 个 worker）
                       │ Docker 内网
                       ▼
                    MySQL 8.0

    宿主机持久目录：
    /srv/fundpilot/{mysql,data,uploads,backups,web}

当前前端已经采用静态导出，不需要长期运行 Node.js。构建时把 `NEXT_PUBLIC_API_BASE_URL` 设为空字符串，前端会访问同源的 `/api/...`；Nginx 转发该路径，因此没有跨域和双域名证书问题。

4GB 内存下的强制约束：

- API 只运行一个 Uvicorn worker，避免后台行情刷新线程、预热线程和内存随 worker 数翻倍。
- 设置 `FUND_AI_OCR_PRELOAD=false`。
- 如果已经配置百炼 OCR Key，保留 `FUND_AI_OCR_PROVIDER=auto`，优先走云端 VLM OCR。
- 第一周不要启用 Playwright 浏览器行情抓取或 `apps/sector-relay`。

## 4. 迁移时间线

| 阶段 | 旧站能否写入 | 主要动作 | 完成条件 |
|---|---:|---|---|
| T-1 天 | 可以 | 备份、搭新机、导入预演数据 | 新站可查看历史数据 |
| 切换窗口 | 不可以 | 最终导出、最终导入、DNS 切换 | 新站核心验收通过 |
| T+0 至 T+7 天 | 仅新站 | 监控、备份、保留 CloudBase | 无核心错误 |
| T+7 天后 | 仅新站 | 停 CloudBase、移除旧发布流程 | 已验证备份可恢复 |

建议在非交易高峰安排 30～60 分钟维护窗口。

## 5. 购买与 CloudBase 迁移前准备

### 5.1 购买腾讯云轻量

- 选择已看到的 **4 核 4GB 3Mbps、上海、99 元/年** 套餐。
- 操作系统选 Ubuntu 24.04 LTS。
- 系统盘至少 50GB；不足时增加数据盘。
- 先关闭自动续费并记录续费价。99 元是首年促销，不代表后续价格。
- 使用中国内地自定义域名时，先完成 ICP 备案或接入备案；不要提前将未备案域名解析到公网 IP。

### 5.2 保存旧环境信息

在密码管理器或离线迁移记录中保存：

- CloudBase MySQL 的主机、端口、数据库、用户名；确认迁移电脑或新服务器 IP 已加白名单。
- 当前 Web 域名、DNS 服务商、TTL、CloudBase API 域名。
- 当前生产环境变量的变量名与来源。密钥本身只留在密码管理器。
- 用户数、报告数、基金档案数、持仓状态数的截图或 SQL 统计。

### 5.3 先做一次预演备份

在可信电脑安装 MySQL 8 客户端，并将 `SOURCE_*` 替换成 CloudBase 控制台信息：

    mysqldump --single-transaction --routines --events --triggers --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 -h SOURCE_HOST -P SOURCE_PORT -u SOURCE_USER -p SOURCE_DATABASE > fundpilot-cloudbase-precheck.sql
    gzip -9 fundpilot-cloudbase-precheck.sql

密码由命令交互输入，不要写在命令行。此预演文件只用于部署验证；正式切换前必须重新导出。

## 6. 初始化 Ubuntu 与网络

先在腾讯云控制台安全组放行 SSH 22。登录服务器后执行：

    sudo apt update
    sudo apt -y upgrade
    sudo apt install -y ca-certificates curl git ufw fail2ban docker.io docker-compose-v2
    sudo usermod -aG docker $USER

    sudo ufw allow OpenSSH
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    sudo ufw enable
    sudo ufw status verbose

    sudo mkdir -p /srv/fundpilot/mysql /srv/fundpilot/data /srv/fundpilot/uploads /srv/fundpilot/backups /srv/fundpilot/web
    sudo chown -R $USER:$USER /srv/fundpilot

重新登录 SSH 后检查：

    docker version
    docker compose version
    free -h
    df -h /srv

推荐补充：只使用 SSH Key 登录、禁用 root 密码登录、开启腾讯云告警和每周快照。修改 SSH 配置前先保持另一个终端已成功登录。

## 7. 获取代码和生产环境变量

    cd /srv/fundpilot
    git clone https://github.com/HLLLG/fundpilot-ai.git repo
    cd repo
    openssl rand -hex 24
    openssl rand -base64 48
    umask 077
    nano .env.production
    chmod 600 .env.production

`.env.production` 示例。所有“替换为”都必须填入真实值；数据库密码使用 `openssl rand -hex` 的值，避免 URL 编码问题。

    MYSQL_DATABASE=fundpilot
    MYSQL_USER=fundpilot
    MYSQL_PASSWORD=替换为24字节以上十六进制密码
    MYSQL_ROOT_PASSWORD=替换为另一条十六进制密码

    FUND_AI_DATABASE_URL=mysql://fundpilot:与MYSQL_PASSWORD相同@mysql:3306/fundpilot
    FUND_AI_DB_FALLBACK_SQLITE=false
    FUND_AI_JWT_SECRET=替换为随机Base64值
    FUND_AI_DECISION_QUALITY_READ_TOKEN=
    FUND_AI_PROMPT_SHADOW_ENABLED=false
    FUND_AI_PROMPT_SHADOW_ASSIGNMENT_SECRET=
    FUND_AI_PROMPT_SHADOW_SAMPLE_BASIS_POINTS=10000
    FUND_AI_PROMPT_SHADOW_MAX_CHALLENGER_CALLS_PER_DAY=100
    FUND_AI_CORS_ORIGINS=https://app.example.com
    FUND_AI_DEEPSEEK_API_KEY=替换为现有DeepSeek_Key
    FUND_AI_OCR_PRELOAD=false
    FUND_AI_OCR_PROVIDER=auto
    FUND_AI_VLM_OCR_API_KEY=

    FUND_AI_NEWS_ENABLED=true
    FUND_AI_NEWS_SUMMARIZE=true
    FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED=true
    FUND_AI_THEME_BOARD_REFRESH_ENABLED=true

不填写 `FUND_AI_CLOUDBASE_ENV_ID`。新站不应复用本机 `.env`，以免带入本地数据库路径或旧 Key。

`FUND_AI_DECISION_QUALITY_READ_TOKEN` 是可选的内部只读 Token。需要读取最新预计算质量快照时，
用独立随机值填写，并只通过 `X-Decision-Quality-Read-Token` 请求头传递；不得复用 JWT、因子 IC
发布 Token 或模型 Key。不开放该运维读面时保持为空。

`FUND_AI_PROMPT_SHADOW_ENABLED` 默认必须保持 `false`。只有准备承担额外模型调用成本、
并需要积累荐基 `full_market + fast + 默认角色` 的真实 paired 样本时才开启；同时用新的
独立随机值填写 `FUND_AI_PROMPT_SHADOW_ASSIGNMENT_SECRET`，按流量调整抽样 basis points 和
上海自然日上限。挑战者只在后台运行，不替换用户报告；密钥不得复用或写入数据库。

`MYSQL_USER` 必须对 `MYSQL_DATABASE` 拥有 `TRIGGER` 与质量账 additive DDL 所需权限。API
bootstrap 会为 `decision_quality_contract_rollouts`、`decision_quality_input_artifacts`、
`decision_quality_evaluation_snapshots`、`decision_quality_artifact_receipts`、
`decision_quality_provider_receipts` 创建并逐次校验 10 个不可变触发器，同时验真候选终态的
`logical_key VARCHAR(255) NULL` 与 `(userId, artifact_type, logical_key)` 非前缀唯一索引；权限不足、
同名触发器不是精确无条件 `SIGNAL`、列/索引冲突或 DDL 后无法验真时都会失败关闭，不会回落本地
SQLite。多 worker 并发 bootstrap 只有在异常后重读 metadata 确认契约已完整建立时才幂等成功。
schema v16 还要求 `prompt_shadow_runs` 与 `prompt_shadow_budget_counters` 使用 InnoDB 并满足
精确列/索引契约；它们是 lease、状态机和预算计数用的可变运营表，不创建不可变触发器。
Docker 官方 MySQL 初始化账号后，首次上线仍应通过 `SHOW GRANTS`、`SHOW TRIGGERS`、
`SHOW COLUMNS FROM decision_quality_input_artifacts` 与
`SHOW INDEX FROM decision_quality_input_artifacts` 核对该能力。

## 8. 创建 Docker Compose 和初始 Nginx 配置

在 `/srv/fundpilot/repo/docker-compose.production.yml` 写入：

    name: fundpilot

    services:
      mysql:
        image: mysql:8.0
        restart: unless-stopped
        env_file: .env.production
        command: ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci"]
        volumes:
          - /srv/fundpilot/mysql:/var/lib/mysql
        healthcheck:
          test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -u root -p$$MYSQL_ROOT_PASSWORD --silent"]
          interval: 10s
          timeout: 5s
          retries: 18

      api:
        build:
          context: .
          dockerfile: Dockerfile
        restart: unless-stopped
        env_file: .env.production
        environment:
          FUND_AI_PROJECT_ROOT: /app
        command: ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
        volumes:
          - /srv/fundpilot/data:/app/data
          - /srv/fundpilot/uploads:/app/uploads
        ports:
          - "127.0.0.1:8000:8000"
        depends_on:
          mysql:
            condition: service_healthy

      nginx:
        image: nginx:1.27-alpine
        restart: unless-stopped
        ports:
          - "80:80"
          - "443:443"
        volumes:
          - /srv/fundpilot/web:/usr/share/nginx/html:ro
          - ./deploy/nginx/fundpilot.conf:/etc/nginx/conf.d/default.conf:ro
          - /etc/letsencrypt:/etc/letsencrypt:ro
          - /var/www/certbot:/var/www/certbot:ro
        depends_on:
          - api

创建初始 HTTP 配置：

    mkdir -p deploy/nginx /var/www/certbot
    nano deploy/nginx/fundpilot.conf

写入以下内容，将 `app.example.com` 换成正式域名：

    server {
        listen 80;
        server_name app.example.com;
        root /usr/share/nginx/html;
        index index.html;

        location /.well-known/acme-challenge/ { root /var/www/certbot; }

        location /api/ {
            proxy_pass http://api:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
            add_header X-Accel-Buffering no;
        }

        location / { try_files $uri $uri/ /index.html; }
    }

`proxy_buffering off`、`X-Accel-Buffering no` 和长读写超时不可删：日报、荐基与追问使用 SSE 流式返回。

## 9. 构建静态前端、启动预演环境

安装 Node.js 22（只用于构建，运行时由 Nginx 托管）：

    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt install -y nodejs

构建前端。空 API 地址让它使用同源 `/api`：

    cd /srv/fundpilot/repo/apps/web
    npm ci
    NEXT_PUBLIC_API_BASE_URL="" npm run build
    rsync -a --delete out/ /srv/fundpilot/web/

把预演 SQL 安全复制到服务器后，启动并导入：

    scp fundpilot-cloudbase-precheck.sql.gz <服务器用户>@<服务器IP>:/srv/fundpilot/backups/
    ssh <服务器用户>@<服务器IP>
    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml up -d mysql

    set -a
    . ./.env.production
    set +a
    gzip -dc /srv/fundpilot/backups/fundpilot-cloudbase-precheck.sql.gz | docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"

    docker compose --env-file .env.production -f docker-compose.production.yml up -d --build api nginx
    curl -fsS http://127.0.0.1:8000/docs >/dev/null && echo "API 已就绪"
    docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=150 api

统计预演导入结果：

    set -a; . ./.env.production; set +a
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e 'SELECT "users" AS table_name, COUNT(*) AS row_count FROM users UNION ALL SELECT "reports", COUNT(*) FROM reports UNION ALL SELECT "fund_profiles", COUNT(*) FROM fund_profiles UNION ALL SELECT "portfolio_state", COUNT(*) FROM portfolio_state;'

预演至少验证：已有账号登录、持仓、旧报告、上传脱敏截图、快速日报、SSE 追问。预演失败时可重建目标库并反复导入预演 SQL，旧 CloudBase 正式环境不受影响。

## 10. 正式切换

### 10.1 冻结旧站并导出最终库

1. 通知用户停止操作。
2. CloudBase 云托管 API 缩到 0 副本或开启维护页，确保没有新写入。
3. 保留 CloudBase MySQL 和静态站，暂时不要删除。
4. 重新导出最终 SQL：

    mysqldump --single-transaction --routines --events --triggers --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 --add-drop-table -h SOURCE_HOST -P SOURCE_PORT -u SOURCE_USER -p SOURCE_DATABASE > fundpilot-cloudbase-final.sql
    gzip -9 fundpilot-cloudbase-final.sql

记录 `users`、`reports`、`fund_profiles`、`portfolio_state` 的最终行数。

### 10.2 导入最终库并验收

    scp fundpilot-cloudbase-final.sql.gz <服务器用户>@<服务器IP>:/srv/fundpilot/backups/
    ssh <服务器用户>@<服务器IP>
    cd /srv/fundpilot/repo
    set -a; . ./.env.production; set +a
    gzip -dc /srv/fundpilot/backups/fundpilot-cloudbase-final.sql.gz | docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"
    docker compose --env-file .env.production -f docker-compose.production.yml restart api
    docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=100 api

再次执行第 9 节行数统计。验证已有用户登录、持仓、旧报告、生成快速报告和上传 OCR；任一核心项失败都不要切 DNS。

### 10.3 DNS 与 HTTPS

1. 先把域名 TTL 降至 300 秒。
2. 将 `app.example.com` 的 A 记录指向轻量服务器公网 IP，确认 `nslookup app.example.com` 已返回新 IP。
3. 签发证书：

    sudo apt install -y certbot
    sudo certbot certonly --webroot -w /var/www/certbot -d app.example.com

4. 将 Nginx 配置替换为 HTTPS 版本：

    server {
        listen 80;
        server_name app.example.com;
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { return 301 https://$host$request_uri; }
    }

    server {
        listen 443 ssl http2;
        server_name app.example.com;
        root /usr/share/nginx/html;
        index index.html;
        ssl_certificate /etc/letsencrypt/live/app.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;

        location /api/ {
            proxy_pass http://api:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
            add_header X-Accel-Buffering no;
        }

        location / { try_files $uri $uri/ /index.html; }
    }

5. 测试并重载：

    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml exec nginx nginx -t
    docker compose --env-file .env.production -f docker-compose.production.yml restart nginx
    sudo systemctl enable --now certbot.timer

从电脑和手机网络访问 HTTPS 域名。浏览器应无证书警告，Network 面板中 API 为同域名的 `/api/...`，且 SSE 连接持续有数据。

## 11. 7 天回滚期与 CloudBase 发布流程

当前仓库的 `.github/workflows/deploy-web.yml` 会在 CI 成功后继续发布前端到 CloudBase，且其中固定了旧 API 地址。新站验收后，应停用该工作流或替换为服务器发布流程；否则之后合并 `main` 仍会覆盖旧静态站。

7 天内：

- 保留 CloudBase MySQL、云托管配置和静态站，但不再让正式流量进入旧站。
- 每天检查新站备份、日志、CPU、内存与磁盘。
- 发生致命问题时先停止新站写入，再选择 DNS 回切或修复；不要无视新数据直接回切。

## 12. 每日备份、巡检与版本发布

创建 `/usr/local/sbin/fundpilot-backup`：

    #!/usr/bin/env bash
    set -euo pipefail
    cd /srv/fundpilot/repo
    set -a
    . ./.env.production
    set +a
    stamp=$(date +%F-%H%M%S)
    target="/srv/fundpilot/backups/mysql-$stamp.sql.gz"
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events --triggers --no-tablespaces "$MYSQL_DATABASE" | gzip -9 > "$target"
    find /srv/fundpilot/backups -type f -name 'mysql-*.sql.gz' -mtime +14 -delete

执行、检查并设置 cron：

    sudo chmod 700 /usr/local/sbin/fundpilot-backup
    sudo /usr/local/sbin/fundpilot-backup
    ls -lh /srv/fundpilot/backups/
    crontab -e

在 crontab 增加：

    15 3 * * * /usr/local/sbin/fundpilot-backup >> /srv/fundpilot/backups/backup.log 2>&1

本地备份不等于灾备。每周将最新 SQL 与 `/srv/fundpilot/uploads/` 同步至 COS 或另一受控存储，并每季度完整恢复测试一次。

恢复测试不能只核对业务表行数。恢复后先启动一次 API 让 bootstrap 验真，再执行：

    cd /srv/fundpilot/repo
    set -a; . ./.env.production; set +a
    docker compose --env-file .env.production -f docker-compose.production.yml up -d mysql api
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e 'SHOW TRIGGERS;'
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SHOW COLUMNS FROM decision_quality_input_artifacts LIKE 'logical_key';"
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SHOW INDEX FROM decision_quality_input_artifacts WHERE Key_name = 'uq_decision_quality_artifact_logical_key';"
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SHOW CREATE TABLE decision_quality_artifact_receipts; SHOW CREATE TABLE decision_quality_provider_receipts;"
    docker compose --env-file .env.production -f docker-compose.production.yml exec -T mysql mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SHOW CREATE TABLE prompt_shadow_runs; SHOW CREATE TABLE prompt_shadow_budget_counters;"

结果必须包含质量账 10 个 `BEFORE UPDATE/DELETE` 不可变触发器；`logical_key` 必须为可空的
`varchar(255)`；唯一索引必须按 `userId`、`artifact_type`、`logical_key` 三列完整排列，
`Non_unique=0` 且各列 `Sub_part` 为空（不能是前缀索引）。同时复核第 9 节的业务表行数，
API bootstrap 日志中不得出现质量账契约或 SQLite fallback 错误。
两张 Prompt shadow 运营表必须为 InnoDB，且不能带质量账的 UPDATE/DELETE 阻断触发器。

### 12.1 配置每日 outcome 结算与 D4 质量快照

`.github/workflows/outcome-settlement.yml` 是当前轻量服务器的生产定时任务。它通过 SSH 进入
`/srv/fundpilot/repo`，再在 API 容器内执行结算和不可变质量快照。先在 GitHub 的
`production` Environment 中配置以下 Secrets：

- `LIGHTHOUSE_HOST`：轻量服务器域名或公网 IP。
- `LIGHTHOUSE_USER`：仅具备所需部署目录和 Docker 权限的 SSH 用户。
- `LIGHTHOUSE_SSH_PRIVATE_KEY`：对应服务器 `authorized_keys` 的专用私钥。
- `LIGHTHOUSE_KNOWN_HOSTS`：已在可信通道核对指纹的服务器 known_hosts 记录；不要在任务运行时盲目信任新主机密钥。

再将 GitHub Actions Variable `LIGHTHOUSE_DEPLOY_ENABLED` 设为 `true`（可放在仓库级或
`production` Environment）。未设置时定时 job 会按设计跳过；手动触发时应从 `main` 分支运行。
私钥、known_hosts 和生产 `.env` 都不得写入仓库或 Actions Summary。

工作流会让 settlement 与 point-in-time snapshot **各自独立尝试**，最后统一汇总两步状态；
不得改成 `command1 && command2`，否则第一步失败会跳过第二步。结算输出
`completed_with_pending` 表示证据尚未齐备，是后续交易日可重试的成功状态，缺失证据不会被
写成 0。若任一结算层返回 `failed_user_ids`，CLI 会以退出码 2 触发告警，但健康租户已经成功
落库的不可变结果必须保留；修复失败租户后重跑即可，不要回滚整批结果。

D4 settlement 在两条 outcome 链之前先做 artifact receipt anti-join reconcile；三步均独立失败
隔离。正式候选样本必须同时具备 audit post-commit receipt、live calendar/NAV adapter output
receipt、outcome post-commit receipt。缺 audit/outcome receipt 的 v4 样本仍留在覆盖率分母但不
产生指标；provider receipt 缺失或绑定冲突会按租户失败关闭。旧 audit v3 / plan v2 / outcome v2
不得回填成 D4，只进入 input manifest 的 ignored 诊断。所有阶段均保持
`automatic_promotion_allowed=false`。

日常巡检：

    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml ps
    docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=200 api
    docker stats --no-stream
    free -h
    df -h /srv

如果内存长期超过 80%、发生 OOM 重启、交易时段 CPU 长期超过 70%，或多个用户分析明显排队：先保持单 worker、关闭本地 OCR/浏览器抓取，再考虑升配。

手动发布新版本：

    cd /srv/fundpilot/repo
    git fetch origin
    git switch main
    git pull --ff-only
    docker compose --env-file .env.production -f docker-compose.production.yml up -d --build api
    cd apps/web
    npm ci
    NEXT_PUBLIC_API_BASE_URL="" npm run build
    rsync -a --delete out/ /srv/fundpilot/web/
    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml exec nginx nginx -t
    docker compose --env-file .env.production -f docker-compose.production.yml restart nginx

## 13. 最终关停 CloudBase 清单

- [ ] 新域名连续运行 7 天；登录、持仓、OCR、日报、荐基、历史报告、SSE 追问均正常。
- [ ] 已至少恢复验证过一份新 MySQL 备份。
- [ ] 轻量服务器没有持续内存、CPU、磁盘或网络告警。
- [ ] 已保存 CloudBase MySQL 的最终只读备份。
- [ ] 已停用或替换 `.github/workflows/deploy-web.yml`。
- [ ] 已确认不再需要 CloudBase 云托管、静态托管和套餐资源。

满足全部条件后，才停止 CloudBase 云托管并关闭/降配 CloudBase 环境；不要删除服务器与异地备份。

## 14. 故障速查

| 现象 | 优先检查 | 处理 |
|---|---|---|
| 页面能开，API 失败 | Nginx 的 `location /api/`、前端构建变量 | 必须以 `NEXT_PUBLIC_API_BASE_URL=""` 构建，并确认 Nginx 代理到 API。 |
| SSE 一直转圈/中断 | Nginx 与 API 日志 | 保留 `proxy_buffering off`、`X-Accel-Buffering no` 和 3600 秒超时。 |
| API 数据库连接失败 | MySQL healthcheck、环境变量 | 不暴露 3306；确认数据库 URL 中密码与 `MYSQL_PASSWORD` 一致。 |
| 容器被 OOM 杀死 | `docker stats`、`dmesg -T` | 单 worker、OCR 不预加载、优先云端 VLM OCR、暂不开浏览器抓取。 |
| Certbot 失败 | DNS、80 端口、防火墙 | 域名必须已解析到新 IP，公网能访问 80，ACME 路径不能被重定向拦截。 |
| 回滚后数据不一致 | 切换后是否有写入 | 先冻结写入并导出新库；不能直接丢弃切换后的新数据。 |

## 15. 本项目中的相关文件

- [现有 CloudBase 部署说明](cloudbase.md)：旧架构参考。
- `Dockerfile`：仓库根目录的 FastAPI 镜像入口。
- `apps/web/next.config.ts`：前端静态导出配置。
- `.github/workflows/deploy-web.yml`：当前 CloudBase 前端发布流程。
- `scripts/migrate_mysql_to_sqlite.py` 和 `scripts/migrate_sqlite_to_mysql.py`：用于 MySQL/SQLite 场景；本次 MySQL 到 MySQL 全量迁移使用 `mysqldump`，以保留全部表结构和数据。
