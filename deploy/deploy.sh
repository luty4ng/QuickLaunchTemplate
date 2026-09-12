#!/usr/bin/env bash
#
# 服务器侧部署脚本。由 CI 在 tag 发布时通过 SSH 调用，也可以人工执行。
#
#   ./deploy.sh <image-tag>         例如 ./deploy.sh sha-1a2b3c4
#   ./deploy.sh sha-1a2b3c4 3       同时清理旧镜像，只保留最近 3 个
#
# 设计要点：
#   * 只操作 ~/quicklaunch/，不碰这台机器上的其他项目；
#   * 绝不覆盖 .env（密钥由人工写在服务器上，脚本只检查它是否完整）；
#   * 迁移是独立的一步，失败即停，不带着坏 schema 起新版本；
#   * 不加 -v，永远不会删 Postgres 数据卷；
#   * 失败时把容器状态和日志打出来，方便从 CI 日志里定位。

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/quicklaunch}"
KEEP_IMAGES="${2:-3}"
COMPOSE=(docker compose -f compose.yaml -f compose.server.yaml)

cd "$APP_DIR"

log()  { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 参数与前置检查
# ---------------------------------------------------------------------------
IMAGE_TAG="${1:-}"
[ -n "$IMAGE_TAG" ] || fail "用法: $0 <image-tag> [保留镜像数]"

[ -f compose.yaml ]        || fail "缺少 $APP_DIR/compose.yaml"
[ -f compose.server.yaml ] || fail "缺少 $APP_DIR/compose.server.yaml"
[ -f .env ]                || fail "缺少 $APP_DIR/.env —— 请先按 README 生成（脚本不会替你创建密钥）"

# .env 必须提供这些，缺一个就停：宁可部署失败，也不要带着空密钥启动。
for key in JWT_SECRET POSTGRES_PASSWORD; do
  grep -qE "^${key}=.+" .env || fail ".env 里缺少或为空的变量: $key"
done
if grep -qE '^JWT_SECRET=.{0,31}$' .env; then
  fail ".env 里的 JWT_SECRET 短于 32 字符，HS256 不接受"
fi

# 记录当前镜像，失败时用于回滚提示。
CURRENT_IMAGE="$(grep -E '^APP_IMAGE=' .env | head -1 | cut -d= -f2- || true)"

log "部署 $IMAGE_TAG（当前: ${CURRENT_IMAGE:-无}）"

# ---------------------------------------------------------------------------
# 1. 拉取镜像
# ---------------------------------------------------------------------------
log "拉取镜像"
docker pull "ghcr.io/luty4ng/quicklaunchtemplate:${IMAGE_TAG}"

# 用本次要部署的 tag 覆盖 .env 里的 APP_IMAGE（就地改写，保持其余行不动）
if grep -qE '^APP_IMAGE=' .env; then
  sed -i "s|^APP_IMAGE=.*|APP_IMAGE=ghcr.io/luty4ng/quicklaunchtemplate:${IMAGE_TAG}|" .env
else
  printf 'APP_IMAGE=ghcr.io/luty4ng/quicklaunchtemplate:%s\n' "$IMAGE_TAG" >> .env
fi
export APP_IMAGE="ghcr.io/luty4ng/quicklaunchtemplate:${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# 2. 数据库先健康
# ---------------------------------------------------------------------------
log "启动数据库并等待健康"
"${COMPOSE[@]}" up -d --wait db
db_state="$(docker inspect --format '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q db)")"
printf 'db 健康状态: %s\n' "$db_state"
[ "$db_state" = "healthy" ] || fail "数据库未达到 healthy"

# ---------------------------------------------------------------------------
# 3. 迁移：独立一步，失败即停
# ---------------------------------------------------------------------------
log "执行数据库迁移（一次性服务）"
"${COMPOSE[@]}" up --exit-code-from migrate migrate

# ---------------------------------------------------------------------------
# 4. 起应用并等健康
# ---------------------------------------------------------------------------
log "启动应用"
"${COMPOSE[@]}" up -d --wait app

app_cid="$("${COMPOSE[@]}" ps -q app)"
for _ in $(seq 1 24); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$app_cid" 2>/dev/null || echo unknown)"
  printf 'app 健康状态: %s\n' "$state"
  [ "$state" = "healthy" ] && break
  sleep 5
done
[ "$state" = "healthy" ] || {
  "${COMPOSE[@]}" logs --tail 60 app || true
  fail "应用未达到 healthy（当前: $state）"
}

# ---------------------------------------------------------------------------
# 5. 清理旧镜像，只留最近 N 个（仅限本项目镜像）
# ---------------------------------------------------------------------------
log "清理旧镜像（保留最近 ${KEEP_IMAGES} 个）"
mapfile -t old < <(
  docker images --format '{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}' \
    'ghcr.io/luty4ng/quicklaunchtemplate' \
    | sort -k2 -r | awk 'NR>'"$KEEP_IMAGES"' {print $1}'
)
for img in "${old[@]:-}"; do
  [ -n "$img" ] || continue
  case "$img" in
    *:latest) continue ;;   # latest 始终保留
  esac
  echo "  删除 $img"
  docker rmi "$img" >/dev/null 2>&1 || true
done

log "部署完成：$IMAGE_TAG"
"${COMPOSE[@]}" ps
