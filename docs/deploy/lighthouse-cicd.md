# Lighthouse 自动部署配置指南

本文用于一次性启用 FundPilot 的 GitHub Actions → 腾讯云 Lighthouse 自动部署。启用后，`main` 分支的 `CI` 全部通过会自动部署同一个 commit；数据库、上传文件、缓存和 `.env.production` 始终留在服务器。

## 1. 发布链路

    main CI 成功
      → GitHub Runner 构建 apps/web/out
      → SSH/rsync 暂存到 /srv/fundpilot/releases/<sha>/web
      → 服务器锁定 /srv/fundpilot/deploy.lock
      → 服务器确认 sha 属于 origin/main
      → 构建并更新 API 容器
      → 发布静态前端并重建 Nginx
      → 验证 API、首页和 /api/ 代理

发布脚本不会导入、删除或重建 MySQL，也不会上传 `.env.production`。

## 2. 两把 SSH 密钥不能混用

自动发布有两个方向：

1. GitHub Actions 登录 Lighthouse：使用新建的 `fundpilot_github_actions` 私钥，私钥保存在 GitHub `production` Environment。
2. Lighthouse 拉取 GitHub 仓库：继续使用服务器现有的只读 GitHub Deploy Key，并通过 `ssh.github.com:443` 访问 GitHub。

不要把个人 IDEA/终端私钥或服务器拉仓库的 Deploy Key 复制到 Actions。

## 3. 先关闭首次自动执行

合并自动部署分支前，在 GitHub 仓库进入：

`Settings → Secrets and variables → Actions → Variables`

创建两个 Repository variables：

| Name | 初始值 |
|---|---|
| `LIGHTHOUSE_DEPLOY_ENABLED` | `false` |
| `FACTOR_IC_REFRESH_ENABLED` | `false` |

变量不是 `true` 时，对应 job 会显示 skipped。完成服务器和密钥配置后再逐个改为 `true`。

## 4. 创建 Actions 专用 SSH 密钥

在本机 PowerShell 执行：

    ssh-keygen -t ed25519 -a 100 -f "$env:USERPROFILE\.ssh\fundpilot_github_actions" -C "fundpilot-github-actions"

该密钥供无人值守部署，Passphrase 留空。生成后有两个文件：

- `fundpilot_github_actions`：私钥，只放 GitHub Secret。
- `fundpilot_github_actions.pub`：公钥，追加到服务器。

显示公钥：

    Get-Content "$env:USERPROFILE\.ssh\fundpilot_github_actions.pub"

通过当前可用的 SSH/腾讯云终端，以 `ubuntu` 登录服务器：

    mkdir -p ~/.ssh
    chmod 700 ~/.ssh
    nano ~/.ssh/authorized_keys

在文件末尾追加整行公钥，然后执行：

    chmod 600 ~/.ssh/authorized_keys

本机验证新密钥：

    ssh -i "$env:USERPROFILE\.ssh\fundpilot_github_actions" -o IdentitiesOnly=yes ubuntu@124.221.246.191 "echo actions-ssh-ok"

必须输出 `actions-ssh-ok`。

## 5. 准备服务器

安装发布脚本依赖并创建目录：

    sudo apt update
    sudo apt install -y rsync util-linux
    sudo mkdir -p /srv/fundpilot/releases
    sudo chown -R ubuntu:ubuntu /srv/fundpilot/releases
    sudo usermod -aG docker ubuntu

确认服务器拉 GitHub 仍走 SSH 443：

    cat ~/.ssh/config

应包含：

    Host github.com
        HostName ssh.github.com
        Port 443
        User git
        IdentityFile ~/.ssh/id_ed25519_fundpilot_github
        IdentitiesOnly yes

确认仓库 remote 和读取权限：

    cd /srv/fundpilot/repo
    git remote set-url origin git@github.com:HLLLG/fundpilot-ai.git
    git ls-remote origin HEAD

最后一条必须返回 commit SHA。

## 6. 创建 GitHub production Environment

进入：

`GitHub 仓库 → Settings → Environments → New environment → production`

添加 Environment secrets：

| Secret | 值 |
|---|---|
| `LIGHTHOUSE_HOST` | `124.221.246.191` |
| `LIGHTHOUSE_USER` | `ubuntu` |
| `LIGHTHOUSE_SSH_PRIVATE_KEY` | 本机 `fundpilot_github_actions` 私钥的完整内容 |
| `LIGHTHOUSE_KNOWN_HOSTS` | 已验证的 Lighthouse SSH host-key 记录 |

将私钥复制到剪贴板：

    Get-Content -Raw "$env:USERPROFILE\.ssh\fundpilot_github_actions" | Set-Clipboard

不要选择 `.pub` 文件。

本机已经成功 SSH 登录过该服务器时，可查找已验证的 known-hosts 记录：

    ssh-keygen -F 124.221.246.191 -f "$env:USERPROFILE\.ssh\known_hosts"

把输出中的 host-key 数据行复制到 `LIGHTHOUSE_KNOWN_HOSTS`。不要关闭 Actions 中的 `StrictHostKeyChecking`。

## 7. 合并后对齐服务器 checkout

自动部署分支合并到 `main` 后，保持 `LIGHTHOUSE_DEPLOY_ENABLED=false`。先在服务器备份手工配置：

    cd /srv/fundpilot/repo
    stamp=$(date +%F-%H%M%S)
    mkdir -p "/srv/fundpilot/backups/deploy-config-$stamp"
    cp -a Dockerfile docker-compose.production.yml deploy/nginx/fundpilot.conf "/srv/fundpilot/backups/deploy-config-$stamp/"
    git status --short

服务器当前的 Dockerfile、Compose 和 Nginx 是手工修改版本。用可恢复的 stash 收起这些文件，再拉取仓库正式版本：

    git stash push --include-untracked -m "server deployment files before CI/CD" -- Dockerfile docker-compose.production.yml deploy/nginx/fundpilot.conf
    git switch main
    git pull --ff-only
    test -f .env.production && echo ".env.production retained"
    git status --short

不要执行 `git stash pop`；仓库版本已经包含对应生产配置。`.env.production` 被 Git 忽略，不会被 pull 删除。

验证配置但暂不发布：

    docker compose --env-file .env.production -f docker-compose.production.yml config -q

该命令无输出且退出码为 0 即通过。若 `git status --short` 仍有 tracked 文件改动，先核对，不要启用流水线。

## 8. 首次启用部署

在 GitHub Repository variables 将：

    LIGHTHOUSE_DEPLOY_ENABLED=true

进入 `Actions → Deploy to Lighthouse → Run workflow`，手动触发一次。成功后在服务器检查：

    cat /srv/fundpilot/DEPLOYED_SHA
    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml ps
    curl -fsS http://127.0.0.1:8000/health
    curl -I http://127.0.0.1/

之后每次 `main` 的 CI 成功都会自动发布；CI 失败不会连接生产服务器。

## 9. 启用 Factor IC 定时发布

因子 IC 发布携带生产 Token，只允许 HTTPS。域名和证书尚未完成时，保持：

    FACTOR_IC_REFRESH_ENABLED=false

HTTPS 正常后，在服务器生成独立 Token：

    openssl rand -hex 32

将同一个值配置到两个位置：

1. 服务器 `/srv/fundpilot/repo/.env.production`：

       FUND_AI_FACTOR_IC_PUBLISH_TOKEN=生成值

2. GitHub `production` Environment secret：

       FACTOR_IC_PUBLISH_TOKEN=同一个生成值

在 `production` Environment variable 添加：

    FACTOR_IC_PUBLISH_URL=https://正式域名/api/internal/factor-ic-snapshots

重启 API 让 Token 生效：

    cd /srv/fundpilot/repo
    docker compose --env-file .env.production -f docker-compose.production.yml up -d api

然后将 Repository variable 改为：

    FACTOR_IC_REFRESH_ENABLED=true

手动运行一次 `Factor IC Refresh`，确认发布成功后再依赖每周日的定时任务。

## 10. 故障定位

Actions 构建失败时，生产服务器不会执行部署。SSH/rsync 失败时检查四个 Environment secrets。服务器脚本失败时检查：

    cd /srv/fundpilot/repo
    git status --short
    docker compose --env-file .env.production -f docker-compose.production.yml ps
    docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=200 api

发布脚本拒绝非 `origin/main` commit、tracked 脏文件和缺少 `index.html` 的前端暂存目录。不要为了通过发布而删除 `.env.production`、MySQL 目录或上传目录。
