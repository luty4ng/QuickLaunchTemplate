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

log()  { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

# 项目标识（域名、仓库、镜像名、目录名）只有一个来源：仓库根的 project.env。
# CI 每次部署都会把 deploy.sh 和 project.env 一起同步到 ~/<slug>/，所以脚本
# 总是躺在自己的项目目录里。这里**不写默认值**是有意的——默认值等于第二份
# 事实来源，而漏改的故障方式各不相同：漏改仓库地址是部署失败（还算好），
# 漏改 desktop 的 publish.owner 则是自动更新**静默失效**。
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SELF_DIR}"
[ -f "$APP_DIR/project.env" ] || fail "缺少 $APP_DIR/project.env —— 它由 CI 同步，也可从仓库根复制一份"
set -a
# shellcheck disable=SC1091  # 部署目录里的文件，不在仓库的固定路径上
. "$APP_DIR/project.env"
set +a

KEEP_IMAGES="${2:-3}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
IMAGE_NAME="$PROJECT_SLUG"
REPO_SLUG="$REPO_OWNER/$REPO_NAME"
SRC_DIR="$APP_DIR/src"
COMPOSE=(docker compose -f compose.yaml -f compose.server.yaml)

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
#
#    这台机器到 github.com 的链路会**周期性被掐断**：v1.2.0 的第一次部署就是死在
#      fatal: unable to access 'https://github.com/...':
#      GnuTLS recv error (-110): The TLS connection was non-properly terminated.
#    ——同一个仓库的 codeload tarball 那一刻却是通的（实测 3 秒 223 KB）。
#    所以"取源码"不是一条命令，而是三种办法依次尝试，每种都有自己的超时，
#    谁成功就用谁，并把用的是哪一种打进日志（CI 日志里能直接看到）。
#
#    另外两个参数是给这种链路用的：lowSpeedLimit/lowSpeedTime 让**卡住的**传输
#    在 30 秒内报错，而不是一直挂着（手工复现时 `git fetch` 挂了 3 分钟没有任何输出）。
# ---------------------------------------------------------------------------
log "获取源码（$GIT_REF）"

# REPO_SLUG 来自 project.env（见文件头）；URL 由它派生，避免第二份事实来源。
REPO_URL="https://github.com/${REPO_SLUG}.git"
GIT_NET=(-c http.version=HTTP/1.1 -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30)
SOURCE_METHOD=""

# 1a) 已有克隆：增量拉取（最快，也最常被掐）
if [ -d "$SRC_DIR/.git" ]; then
  for attempt in 1 2 3; do
    printf '  [1/3] git fetch（第 %s 次）…\n' "$attempt"
    if timeout 120 git -C "$SRC_DIR" "${GIT_NET[@]}" fetch --depth 1 origin "$GIT_REF" \
       && git -C "$SRC_DIR" checkout -q --force FETCH_HEAD; then
      SOURCE_METHOD="git fetch（第 ${attempt} 次）"
      break
    fi
    sleep $((attempt * 5))
  done
fi

# 1b) 整仓浅克隆（走的是另一条路径，常常还能用）
if [ -z "$SOURCE_METHOD" ]; then
  printf '  [2/3] 重新浅克隆…\n'
  rm -rf "$SRC_DIR"
  if timeout 300 git "${GIT_NET[@]}" clone --depth 1 --branch "$GIT_REF" "$REPO_URL" "$SRC_DIR"; then
    SOURCE_METHOD="git clone"
  fi
fi

# 1c) 最后一招：下载源码压缩包（不经过 git，实测这条路在 fetch 失败时仍然通）
if [ -z "$SOURCE_METHOD" ]; then
  printf '  [3/3] 下载 codeload tarball…\n'
  rm -rf "$SRC_DIR"
  mkdir -p "$SRC_DIR"
  if timeout 300 curl -fsSL --retry 3 --retry-delay 3 --retry-connrefused \
       "https://codeload.github.com/${REPO_SLUG}/tar.gz/${GIT_REF}" \
       | tar -xz -C "$SRC_DIR" --strip-components=1; then
    SOURCE_METHOD="codeload tarball"
  fi
fi

[ -n "$SOURCE_METHOD" ] || fail "三种取源码方式都失败（$GIT_REF）——这台机器到 github.com 的链路当前不通，稍后重试即可"
printf '取源码方式: %s\n' "$SOURCE_METHOD"

if [ -d "$SRC_DIR/.git" ]; then
  REV="$(git -C "$SRC_DIR" rev-parse --short HEAD)"
else
  # 从 tarball 解出来的源码没有 .git。镜像 tag 需要一个稳定的短 id：问一次
  # GitHub API；连 API 也不通就退化成 ref 名（仍然可用，只是没那么好看）。
  REV="$(curl -fsSL --max-time 30 --retry 2 "https://api.github.com/repos/${REPO_SLUG}/commits/${GIT_REF}" 2>/dev/null \
          | sed -n 's/^ *"sha": *"\([0-9a-f]\{7,\}\)".*/\1/p' | head -1 | cut -c1-7)"
  [ -n "$REV" ] || REV="$(printf '%s' "$GIT_REF" | tr -c 'A-Za-z0-9._-' '-')"
fi
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
# 4a. 迁移之前先备份数据库
#
#     这是整条部署路径上唯一"不可恢复"的一步：代码能回滚、镜像能切回，
#     但一条写错数据的迁移没有备份就是真的回不来。备份在**迁移之前**做，
#     失败就停（宁可这次不部署，也不要带着不可回滚的迁移往前走）。
#
#     刻意保持土办法：pg_dump | gzip 落到同机目录、保留最近 N 份。
#     它挡得住"迁移写坏了数据"，挡不住"整台机器没了"——异地备份是另一件事，
#     已在报告的已知限制里写明。
# ---------------------------------------------------------------------------
log "备份数据库（迁移前）"
BACKUP_DIR="$APP_DIR/backups"
mkdir -p "$BACKUP_DIR"
# 用 .env 里的库名/用户名（compose 读的就是它），读不到才退回项目 slug。
pg_user="$(sed -n 's/^POSTGRES_USER=//p' .env | head -1)"
pg_db="$(sed -n 's/^POSTGRES_DB=//p' .env | head -1)"
backup_file="$BACKUP_DIR/db-$(date +%Y%m%d-%H%M%S)-before-${REV}.sql.gz"
if "${COMPOSE[@]}" exec -T db pg_dump -U "${pg_user:-$PROJECT_SLUG}" -d "${pg_db:-$PROJECT_SLUG}" \
     | gzip > "$backup_file"; then
  printf '备份完成: %s (%s)\n' "$backup_file" "$(du -h "$backup_file" | cut -f1)"
else
  rm -f "$backup_file"
  fail "数据库备份失败 —— 迁移前必须有可用备份，本次部署中止"
fi

# 只保留最近 N 份，避免把磁盘吃满（磁盘满会让部署和数据库一起挂）。
mapfile -t stale < <(ls -1t "$BACKUP_DIR"/db-*.sql.gz 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))")
for old_backup in "${stale[@]:-}"; do
  [ -n "$old_backup" ] || continue
  rm -f "$old_backup" && printf '  删除旧备份 %s\n' "$(basename "$old_backup")"
done

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
