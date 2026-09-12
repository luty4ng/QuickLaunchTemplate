#!/usr/bin/env bash
#
# 服务器侧部署脚本：从源码构建镜像并上线。
#
#   ./deploy.sh <git-ref> [保留镜像数]
#   ./deploy.sh v1.2.0            # tag
#   ./deploy.sh main              # 分支
#   ./deploy.sh v1.2.0 3
#
# 为什么在服务器上构建，而不是拉 GHCR 镜像：
#   实测这台机器到 ghcr.io 的吞吐是 74 B/s（20 秒只下到 74 字节），而
#   git clone 本仓库只要 7 秒、npm 有 17.7 Mbps。所以"拉源码 + 本地构建"是这台
#   机器上唯一可行的部署路径。代价是构建消耗服务器 CPU、回滚需要重新构建；
#   如果以后接了国内 registry（如腾讯云 TCR），把 build 换成 pull 即可。
#
# 行为约束（与项目一贯原则一致）：
#   * 只在 $APP_DIR 内操作，不碰这台机器上的其他项目；
#   * 绝不覆盖 .env（密钥由人工维护），只检查完整性；
#   * 迁移是独立的一步，失败即停；
#   * 不加 -v，永远不会删 Postgres 数据卷；
#   * 失败时打印容器状态与日志，便于从 CI 日志定位。

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/quicklaunch}"
SRC_DIR="$APP_DIR/src"
KEEP_IMAGES="${2:-3}"
IMAGE_NAME="quicklaunch"
COMPOSE=(docker compose -f compose.yaml -f compose.server.yaml)

log()  { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

GIT_REF="${1:-}"
[ -n "$GIT_REF" ] || fail "用法: $0 <git-ref> [保留镜像数]"

mkdir -p "$APP_DIR"
cd "$APP_DIR"

[ -f compose.yaml ]        || fail "缺少 $APP_DIR/compose.yaml（应由 CI 同步）"
[ -f compose.server.yaml ] || fail "缺少 $APP_DIR/compose.server.yaml（应由 CI 同步）"
[ -f .env ]                || fail "缺少 $APP_DIR/.env —— 密钥必须人工写在服务器上，脚本不会替你创建"

for key in JWT_SECRET POSTGRES_PASSWORD; do
  grep -qE "^${key}=.+" .env || fail ".env 里缺少或为空的变量: $key"
done
grep -qE '^JWT_SECRET=.{32,}$' .env || fail ".env 里的 JWT_SECRET 短于 32 字符，HS256 不接受"

# ---------------------------------------------------------------------------
# 1. 取源码
# ---------------------------------------------------------------------------
log "获取源码（$GIT_REF）"
if [ -d "$SRC_DIR/.git" ]; then
  git -C "$SRC_DIR" fetch --depth 1 origin "$GIT_REF"
  git -C "$SRC_DIR" checkout -q --force FETCH_HEAD
else
  rm -rf "$SRC_DIR"
  git clone --depth 1 --branch "$GIT_REF" \
    https://github.com/luty4ng/QuickLaunchTemplate.git "$SRC_DIR"
fi
REV="$(git -C "$SRC_DIR" rev-parse --short HEAD)"
printf '源码版本: %s\n' "$REV"

# ---------------------------------------------------------------------------
# 2. 构建镜像（打两个 tag：可回滚的 rev，以及 latest）
# ---------------------------------------------------------------------------
log "构建镜像（首次构建较慢，之后有层缓存）"
# 这台机器到 pypi.org 只有 0.5 Mbps，直连会在 200 秒后失败；阿里云镜像实测
# 4.4 Mbps。npm 官方源在这里有 21 Mbps，无需替换。
docker build \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}" \
  --tag "${IMAGE_NAME}:${REV}" \
  --tag "${IMAGE_NAME}:latest" \
  "$SRC_DIR"

# ---------------------------------------------------------------------------
# 3. 用本次版本覆盖 .env 里的 APP_IMAGE（只改这一行）
# ---------------------------------------------------------------------------
if grep -qE '^APP_IMAGE=' .env; then
  sed -i "s|^APP_IMAGE=.*|APP_IMAGE=${IMAGE_NAME}:${REV}|" .env
else
  printf 'APP_IMAGE=%s:%s\n' "$IMAGE_NAME" "$REV" >> .env
fi
export APP_IMAGE="${IMAGE_NAME}:${REV}"
printf 'APP_IMAGE=%s\n' "$APP_IMAGE"

# ---------------------------------------------------------------------------
# 4. 数据库先健康
# ---------------------------------------------------------------------------
log "启动数据库并等待健康"
"${COMPOSE[@]}" up -d --wait db
db_state="$(docker inspect --format '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q db)")"
printf 'db 健康状态: %s\n' "$db_state"
[ "$db_state" = "healthy" ] || fail "数据库未达到 healthy"

# ---------------------------------------------------------------------------
# 5. 迁移：独立一步，失败即停
# ---------------------------------------------------------------------------
log "执行数据库迁移（一次性服务）"
"${COMPOSE[@]}" up --exit-code-from migrate migrate

# ---------------------------------------------------------------------------
# 6. 起应用并等健康
# ---------------------------------------------------------------------------
log "启动应用"
"${COMPOSE[@]}" up -d --wait app

app_cid="$("${COMPOSE[@]}" ps -q app)"
state=unknown
for _ in $(seq 1 24); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$app_cid" 2>/dev/null || echo unknown)"
  printf 'app 健康状态: %s\n' "$state"
  [ "$state" = "healthy" ] && break
  sleep 5
done
if [ "$state" != "healthy" ]; then
  "${COMPOSE[@]}" logs --tail 60 app || true
  fail "应用未达到 healthy（当前: $state）"
fi

# ---------------------------------------------------------------------------
# 7. 服务内自检（不依赖公网）
# ---------------------------------------------------------------------------
log "容器内健康检查"
"${COMPOSE[@]}" exec -T app \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read().decode())" \
  || fail "应用自身 /api/health 不通"

# ---------------------------------------------------------------------------
# 8. 清理旧镜像（只清本项目，保留最近 N 个 + latest）
# ---------------------------------------------------------------------------
log "清理旧镜像（保留最近 ${KEEP_IMAGES} 个）"
mapfile -t old < <(
  docker images --format '{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}' "$IMAGE_NAME" \
    | sort -k2 -r | awk 'NR>'"$KEEP_IMAGES"' {print $1}'
)
for img in "${old[@]:-}"; do
  [ -n "$img" ] || continue
  case "$img" in *:latest) continue ;; esac
  echo "  删除 $img"
  docker rmi "$img" >/dev/null 2>&1 || true
done

log "部署完成：$REV"
"${COMPOSE[@]}" ps
printf '\n回滚方式：./deploy.sh <上一个 rev>   （或把 .env 的 APP_IMAGE 改回旧 rev 后 up -d app）\n'
