#!/usr/bin/env bash
#
# 新服务器一键准备：把这个模板要的东西一次性装好、建好，并打印剩余的人工步骤。
#
#   bash deploy/bootstrap-server.sh --check                     # 只体检，不改任何东西
#   sudo bash deploy/bootstrap-server.sh --install-docker       # 允许装 docker + compose 插件
#   bash deploy/bootstrap-server.sh                             # 建网络、建目录、生成 .env
#   bash deploy/bootstrap-server.sh --with-traefik --acme-email you@example.com
#
# 为什么要有这个脚本：以前"服务器上该有什么"只存在于人的记忆里——docker、compose 插件、
# 一个叫 traefik-net 的外部网络、一个 600 权限的 .env、以及（裸机才需要的）Traefik。
# 换一台机器就得重新踩一遍。这里把它变成可重复、可体检的一步。
#
# 行为约束（和 deploy.sh 一致）：
#   * 幂等：已经就绪的东西绝不再动，重复执行是空操作；
#   * 绝不覆盖已存在的 .env —— 密钥是人的东西，脚本只负责在"没有"的时候生成；
#   * 不碰别的项目：只在 $APP_DIR 与本项目的网络/容器上操作；
#   * --check 模式下不做任何写操作。

set -euo pipefail

APP_DIR_OVERRIDE=""
MODE_CHECK=0
INSTALL_DOCKER=0
WITH_TRAEFIK=0
ACME_EMAIL=""

log()  { printf '\n=== %s ===\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check)           MODE_CHECK=1 ;;
    --install-docker)  INSTALL_DOCKER=1 ;;
    --with-traefik)    WITH_TRAEFIK=1 ;;
    --acme-email)      shift; ACME_EMAIL="${1:-}" ;;
    --app-dir)         shift; APP_DIR_OVERRIDE="${1:-}" ;;
    -h|--help)         sed -n '2,20p' "$0"; exit 0 ;;
    *)                 fail "未知参数：$1（--help 看用法）" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# 项目身份：单一来源是仓库根的 project.env（也支持部署目录里那一份）
# ---------------------------------------------------------------------------
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ENV=""
for candidate in "$SELF_DIR/../project.env" "$SELF_DIR/project.env"; do
  [ -f "$candidate" ] && PROJECT_ENV="$candidate" && break
done
[ -n "$PROJECT_ENV" ] || fail "找不到 project.env（仓库根或脚本同目录）——它决定 slug 与域名，没有它不知道该准备什么"
# shellcheck disable=SC1090  # 路径是上面推导出来的
set -a; . "$PROJECT_ENV"; set +a
: "${PROJECT_SLUG:?project.env 里缺少 PROJECT_SLUG}"
: "${APP_DOMAIN:?project.env 里缺少 APP_DOMAIN}"

APP_DIR="${APP_DIR_OVERRIDE:-$HOME/$PROJECT_SLUG}"
REPO_HINT="${REPO_OWNER:-<owner>}/${REPO_NAME:-<repo>}"
IMAGE_HINT="ghcr.io/${REPO_OWNER:-<owner>}/$(printf '%s' "${REPO_NAME:-<repo>}" | tr '[:upper:]' '[:lower:]'):latest"

printf '项目: %s (%s)   部署目录: %s\n' "${PROJECT_NAME:-$PROJECT_SLUG}" "$APP_DOMAIN" "$APP_DIR"

# ---------------------------------------------------------------------------
# 体检：每条都给出证据，--check 模式只到这里为止
# ---------------------------------------------------------------------------
CHECKS_FAILED=0

check_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "docker 已安装（$(docker --version | cut -d, -f1)）"
  else
    bad "没有 docker"
    return 1
  fi
}

check_compose() {
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose 插件可用（$(docker compose version --short 2>/dev/null || echo '?')）"
  else
    bad "docker compose 插件不可用（需要 v2，不是老的 docker-compose）"
    return 1
  fi
}

check_docker_access() {
  if docker info >/dev/null 2>&1; then
    ok "当前用户能访问 docker（$USER）"
  else
    if id -nG "$USER" 2>/dev/null | grep -qw docker; then
      bad "已在 docker 组，但当前 shell 还没生效——请重新登录后再试"
    else
      bad "当前用户 ($USER) 不能访问 docker 套接字"
    fi
    return 1
  fi
}

check_network() {
  if docker network inspect traefik-net >/dev/null 2>&1; then
    ok "外部网络 traefik-net 存在"
  else
    bad "外部网络 traefik-net 不存在（compose 覆盖文件要接它）"
    return 1
  fi
}

check_app_dir() {
  if [ -d "$APP_DIR" ]; then
    ok "部署目录存在：$APP_DIR"
  else
    bad "部署目录不存在：$APP_DIR"
    return 1
  fi
}

check_env_file() {
  local env_file="$APP_DIR/.env"
  [ -f "$env_file" ] || { bad "缺少 $env_file（密钥由人工维护，脚本只在没有时生成）"; return 1; }
  local perms
  perms="$(stat -c '%a' "$env_file")"
  [ "$perms" = "600" ] || { bad "$env_file 权限是 $perms，应当是 600"; return 1; }
  grep -qE '^JWT_SECRET=.{32,}$' "$env_file" || { bad "$env_file 里的 JWT_SECRET 少于 32 字符"; return 1; }
  grep -qE '^POSTGRES_PASSWORD=.+' "$env_file" || { bad "$env_file 里缺少 POSTGRES_PASSWORD"; return 1; }
  ok "$env_file 存在且合法（600，JWT_SECRET ≥32 字符）"
}

check_traefik() {
  if docker ps --format '{{.Names}}' | grep -qx traefik; then
    ok "Traefik 正在运行"
  elif ss -ltn 2>/dev/null | grep -qE ':(80|443)\s'; then
    bad "80/443 已被占用，但没有名为 traefik 的容器（别的东西在监听）"
    return 1
  else
    bad "没有 Traefik 在跑，80/443 也没有监听（裸机需要用 --with-traefik 装一个）"
    return 1
  fi
}

run_checks() {
  log "体检"
  check_docker      || CHECKS_FAILED=1
  check_compose     || CHECKS_FAILED=1
  check_docker_access || CHECKS_FAILED=1
  check_network     || CHECKS_FAILED=1
  check_app_dir     || CHECKS_FAILED=1
  check_env_file    || CHECKS_FAILED=1
  check_traefik     || CHECKS_FAILED=1
}

run_checks
if [ "$MODE_CHECK" = "1" ]; then
  if [ "$CHECKS_FAILED" = "0" ]; then
    printf '\n全部就绪。\n'
    exit 0
  fi
  printf '\n还有未就绪的项：去掉 --check 跑一次即可补齐（--install-docker 才允许装 docker）。\n' >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. docker 与 compose
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  if [ "$INSTALL_DOCKER" != "1" ]; then
    fail "没有 docker。要装的话请显式加 --install-docker（并确认用的是官方源或发行版源）：
    sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2"
  fi
  log "安装 docker 与 compose 插件（apt）"
  sudo apt-get update -qq
  sudo apt-get install -y docker.io docker-compose-v2
  ok "安装完成"
fi

if ! docker info >/dev/null 2>&1; then
  if ! id -nG "$USER" 2>/dev/null | grep -qw docker; then
    log "把 $USER 加入 docker 组"
    sudo usermod -aG docker "$USER"
    ok "已加入；**需要重新登录**（或 newgrp docker）后本 shell 才能免 sudo 用 docker"
  fi
  fail "当前 shell 仍无 docker 权限——重新登录后再跑一次本脚本"
fi

# ---------------------------------------------------------------------------
# 2. 外部网络
# ---------------------------------------------------------------------------
if docker network inspect traefik-net >/dev/null 2>&1; then
  ok "traefik-net 已存在，未改动"
else
  log "创建外部网络 traefik-net"
  docker network create traefik-net >/dev/null
  ok "已创建"
fi

# ---------------------------------------------------------------------------
# 3. 部署目录与 .env
# ---------------------------------------------------------------------------
if [ -d "$APP_DIR" ]; then
  ok "$APP_DIR 已存在，未改动"
else
  log "创建部署目录 $APP_DIR"
  mkdir -p "$APP_DIR"
  ok "已创建"
fi

ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE 已存在，**未改动**（密钥是人的东西，脚本不碰）"
else
  log "生成 $ENV_FILE"
  # 密钥在服务器本地生成，绝不过 CI、绝不进仓库。
  jwt_secret="$(openssl rand -base64 48 | tr -d '\n=')"
  pg_password="$(openssl rand -base64 24 | tr -d '\n=/+')"
  cat > "$ENV_FILE" <<EOF
# 由 deploy/bootstrap-server.sh 生成于 $(date -Is)。这个文件只存在于服务器上：
# CI 从不传输、从不覆盖它。要改密钥就直接改这里，然后重新部署。
JWT_SECRET=$jwt_secret
JWT_TTL_SECONDS=604800
BCRYPT_ROUNDS=12
COOKIE_SECURE=true
POSTGRES_USER=$PROJECT_SLUG
POSTGRES_PASSWORD=$pg_password
POSTGRES_DB=$PROJECT_SLUG
# 首次部署时由 deploy.sh 覆盖成本次构建的 rev
APP_IMAGE=$IMAGE_HINT
APP_DOMAIN=$APP_DOMAIN
COMPOSE_PROJECT_NAME=$PROJECT_SLUG
EOF
  chmod 600 "$ENV_FILE"
  ok "已生成（600）。内容只有 JWT_SECRET 与 POSTGRES_PASSWORD 的随机值，其余是项目标识"
fi

# ---------------------------------------------------------------------------
# 4. 可选：在一台裸机上装 Traefik
#
#    compose 覆盖文件引用的是"名为 letsencrypt 的证书 resolver + traefik-net 网络"，
#    所以这里装的 Traefik 必须用这个名字——换名字会让入口指向不存在的 resolver。
# ---------------------------------------------------------------------------
if [ "$WITH_TRAEFIK" = "1" ]; then
  [ -n "$ACME_EMAIL" ] || fail "--with-traefik 需要同时给 --acme-email you@example.com（Let's Encrypt 要联系邮箱）"
  if ss -ltn 2>/dev/null | grep -qE ':(80|443)\s'; then
    fail "80/443 已经有东西在监听。这台机器很可能已经有一个反向代理了——
    这种情况不要用 --with-traefik，直接让现有 Traefik 接入 traefik-net 即可。"
  fi
  log "安装 Traefik（$APP_DIR/traefik）"
  mkdir -p "$APP_DIR/traefik"
  cat > "$APP_DIR/traefik/traefik.yml" <<EOF
# Traefik 静态配置。resolver 必须叫 letsencrypt：项目的 compose 覆盖文件按这个名字引用。
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: traefik-net

certificatesResolvers:
  letsencrypt:
    acme:
      email: $ACME_EMAIL
      storage: /acme/acme.json
      httpChallenge:
        entryPoint: web

log:
  level: INFO
EOF
  cat > "$APP_DIR/traefik/compose.yaml" <<'EOF'
services:
  traefik:
    image: traefik:v3.2
    container_name: traefik
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./traefik.yml:/etc/traefik/traefik.yml:ro
      - ./acme:/acme
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks: [traefik-net]

networks:
  traefik-net:
    external: true
EOF
  touch "$APP_DIR/traefik/acme/acme.json" 2>/dev/null || { mkdir -p "$APP_DIR/traefik/acme"; touch "$APP_DIR/traefik/acme/acme.json"; }
  chmod 600 "$APP_DIR/traefik/acme/acme.json"
  ( cd "$APP_DIR/traefik" && docker compose up -d )
  ok "Traefik 已启动（证书会按需自动签发）"
fi

# ---------------------------------------------------------------------------
# 5. 剩余人工步骤
# ---------------------------------------------------------------------------
run_checks
cat <<EOF

下一步（这些必须由人来做，脚本不代替）：

  1. 部署公钥：把 CI 用的公钥追加到 ~/.ssh/authorized_keys
       echo '<公钥内容>' >> ~/.ssh/authorized_keys
     （私钥只放进 GitHub Secret，不要经过任何第三方）

  2. GitHub 仓库设置：
       Secret   DEPLOY_SSH_KEY  = 上一步那把私钥
       Variable DEPLOY_HOST     = <这台机器的公网地址>
                                  （本机自报的地址是 $(hostname -I 2>/dev/null | awk '{print $1}')；
                                   如果服务器在 NAT/内网后面，这里要填 CI 能连上的公网地址或域名）
       Variable DEPLOY_USER     = $USER

  3. 打一个 tag 触发首次发布与部署（剩下的验证、发布、部署、公网冒烟都是流水线的事）：
       git tag -a v0.1.0 -m "first release" && git push origin v0.1.0

  4. （可选）接支付：把 STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET /
     STRIPE_PRICE_PLUS / STRIPE_PRICE_PRO 写进 $ENV_FILE，然后重新部署。
     不配也能跑：/api/billing/* 返回 503，界面会说明"本服务器未配置支付"。
EOF
