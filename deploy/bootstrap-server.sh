#!/usr/bin/env bash
#
# 新服务器一键准备（bootstrap-server.sh）：把"这台机器要能跑本项目的 CD"一次性做掉。
#
#   bash deploy/bootstrap-server.sh --check                    # 只体检，不改任何东西（退出码即结论）
#   sudo bash deploy/bootstrap-server.sh --install-docker      # 缺 docker 时允许用 apt 装
#   bash deploy/bootstrap-server.sh                            # 默认 = apply：只补缺的那几步
#   bash deploy/bootstrap-server.sh --with-traefik --acme-email you@example.com
#   bash deploy/bootstrap-server.sh --app-dir /srv/<slug>      # 换部署目录（默认 $HOME/<slug>）
#
# 为什么要有这个脚本：这些准备动作以前只存在于人的记忆里（见 report/AUTOMATION.md §6），
# 而每一条漏掉的失败方式都不一样——漏建 traefik-net 是 compose 起不来（还算响），
# 漏写 JWT_SECRET 是应用启动即崩（也响），而 JWT_SECRET 短于 32 字符则是**跑到第一次
# 登录才报错**。把"这台机器该长什么样"写成一段可重复执行的代码，迁移时就不用再靠回忆。
#
# 行为约束（与 deploy/deploy.sh 同一套原则）：
#   * **幂等**：第二次运行不改任何东西，并且明确打印"没有需要改的东西"；
#   * **只增不改**：绝不覆盖已存在的 .env（密钥只由人维护）、不重启别人的服务、不抢端口；
#   * **不删东西**：没有 rm -rf、没有 compose down -v，出问题时最坏的结果是什么都不做；
#   * **不打印密钥**：.env 里的值永远不出现在 stdout，只打印长度与权限；
#   * `--check` 是纯只读：不建目录、不建网络、不装包、不 source .env（那是执行任意代码）。

set -euo pipefail

log()  { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }
warn() { printf '  ! %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
usage() {
  cat <<'TXT'
用法: bash deploy/bootstrap-server.sh [选项]

  --check                 只体检并打印 ✓/✗ 表，退出码非零表示还缺东西（不做任何改动）
  --install-docker        缺 docker / compose 插件时允许用 apt 安装（默认只报告并失败）
  --with-traefik          装一个最小 Traefik（80/443 已被占用则中止，不抢端口）
  --acme-email <邮箱>     --with-traefik 必填：Let's Encrypt 的证书到期通知邮箱
  --app-dir <路径>        部署目录，默认 $HOME/<PROJECT_SLUG>
  -h, --help              这一屏

不带 --check 就是 apply：只做缺的那几步，做完重新体检一遍并列出改了哪些东西。
TXT
}

MODE_CHECK=false
INSTALL_DOCKER=false
WITH_TRAEFIK=false
ACME_EMAIL=""
APP_DIR_OPT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check)          MODE_CHECK=true ;;
    --install-docker) INSTALL_DOCKER=true ;;
    --with-traefik)   WITH_TRAEFIK=true ;;
    --acme-email)     shift; [ $# -gt 0 ] || fail "--acme-email 后面要跟邮箱"; ACME_EMAIL="$1" ;;
    --acme-email=*)   ACME_EMAIL="${1#*=}" ;;
    --app-dir)        shift; [ $# -gt 0 ] || fail "--app-dir 后面要跟路径"; APP_DIR_OPT="$1" ;;
    --app-dir=*)      APP_DIR_OPT="${1#*=}" ;;
    -h|--help)        usage; exit 0 ;;
    *)                fail "未知参数：$1（--help 看用法）" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# 项目身份：域名、仓库名、镜像名、目录名只有一个来源 —— project.env。
# 在这里写默认值等于制造第二份事实来源，而漏改的故障方式各不相同（见 project.env 文件头），
# 所以找不到就直接失败，不猜。
# ---------------------------------------------------------------------------
SELF_DIR=""
# `ssh host "tr -d '\r' | bash -s -- --check"` 这种管道执行方式下 BASH_SOURCE 是空的，
# 没有"脚本所在目录"可言，所以这里必须容错（后面用别的办法找 project.env）。
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

IDENTITY_FILE=""
APP_DIR_GUESS=""

if [ -n "$APP_DIR_OPT" ]; then
  # 允许 --app-dir '~/xxx'：引号里的 ~ 不会被 shell 展开，这里补一次。
  case "$APP_DIR_OPT" in "~/"*) APP_DIR_OPT="$HOME/${APP_DIR_OPT#\~/}" ;; esac
  if [ -f "$APP_DIR_OPT/project.env" ]; then
    IDENTITY_FILE="$APP_DIR_OPT/project.env"
    APP_DIR_GUESS="$APP_DIR_OPT"
  fi
fi

# 正常情况：脚本在仓库的 deploy/ 下，仓库根就是 ..
if [ -z "$IDENTITY_FILE" ] && [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/../project.env" ]; then
  IDENTITY_FILE="$SELF_DIR/../project.env"
fi

# 次之：脚本和 project.env 一起被 CD 同步到部署目录（deploy.sh 就是这样运行的）
if [ -z "$IDENTITY_FILE" ] && [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/project.env" ]; then
  IDENTITY_FILE="$SELF_DIR/project.env"
  APP_DIR_GUESS="$SELF_DIR"
fi

# 再次：当前目录就是部署目录
if [ -z "$IDENTITY_FILE" ] && [ -f "$PWD/project.env" ]; then
  IDENTITY_FILE="$PWD/project.env"
  APP_DIR_GUESS="$PWD"
fi

# 最后：管道执行（bash -s）时既没有脚本路径、cwd 也不一定是部署目录，于是在 $HOME 下找
# **唯一**一份 project.env。多于一份就报错让人用 --app-dir 指定——猜错的代价是往别人的
# 项目目录里写东西，宁可多问一句。
if [ -z "$IDENTITY_FILE" ]; then
  candidates=()
  for candidate in "$HOME"/*/project.env; do
    [ -f "$candidate" ] && candidates+=("$candidate")
  done
  if [ "${#candidates[@]}" -eq 1 ]; then
    IDENTITY_FILE="${candidates[0]}"
    APP_DIR_GUESS="$(dirname "${candidates[0]}")"
  elif [ "${#candidates[@]}" -gt 1 ]; then
    printf '  在 %s 下找到多个 project.env：\n' "$HOME" >&2
    printf '    %s\n' "${candidates[@]}" >&2
    fail "请用 --app-dir 指定要准备哪一个（脚本不猜）"
  fi
fi

[ -n "$IDENTITY_FILE" ] || fail "找不到 project.env（找过 \$SELF_DIR/../、\$SELF_DIR/、\$PWD/、\$HOME/*/）。
    它在仓库根；服务器上由 CD 同步到部署目录，也可以手工复制一份过去。"

set -a
# shellcheck disable=SC1090  # 路径不固定：仓库根或部署目录，取决于脚本从哪里跑
. "$IDENTITY_FILE"
set +a

for key in PROJECT_NAME PROJECT_SLUG REPO_OWNER REPO_NAME APP_DOMAIN; do
  [ -n "${!key:-}" ] || fail "$IDENTITY_FILE 里缺少 $key（project.env 是项目身份的唯一来源）"
done

# 部署目录优先级：--app-dir > 环境变量 APP_DIR（deploy.sh 也是这个约定）> project.env 所在目录
# > $HOME/<slug>（默认值，与 CD 里 ~/$APP_DIR/ 的用法一致）。
if [ -n "$APP_DIR_OPT" ]; then
  APP_DIR="$APP_DIR_OPT"
elif [ -n "${APP_DIR:-}" ]; then
  APP_DIR="$APP_DIR"
elif [ -n "$APP_DIR_GUESS" ]; then
  APP_DIR="$APP_DIR_GUESS"
else
  APP_DIR="$HOME/$PROJECT_SLUG"
fi

# GHCR 拒绝大写仓库名，所以镜像名一律小写（与 scripts/project_env.py 的 IMAGE_REPO 同源）。
REPO_LOWER="$(printf '%s' "$REPO_NAME" | tr '[:upper:]' '[:lower:]')"
IMAGE_REPO="ghcr.io/${REPO_OWNER}/${REPO_LOWER}"
ENV_FILE="$APP_DIR/.env"

# ---------------------------------------------------------------------------
# 探测函数：只读，返回 0/1，并把"为什么"写进全局变量。
#
# 检查逻辑只有一份：--check 跑它，apply 前后也跑它。同一段代码，结论必然一致，
# 也就不存在"体检说没问题、apply 却不认"这种自相矛盾。
# ---------------------------------------------------------------------------
DOCKER_VERSION=""
COMPOSE_VERSION=""
DOCKER_INFO_ERR=""
NETWORK_ID=""
ENV_JWT_LEN=0
ENV_PG_LEN=0
ENV_MODE="?"
TRAEFIK_IMAGE=""

probe_docker() {
  DOCKER_VERSION="$(docker --version 2>/dev/null)" || { DOCKER_VERSION=""; return 1; }
  [ -n "$DOCKER_VERSION" ]
}

probe_compose() {
  COMPOSE_VERSION="$(docker compose version 2>/dev/null)" || { COMPOSE_VERSION=""; return 1; }
  COMPOSE_VERSION="${COMPOSE_VERSION%%$'\n'*}"   # 只留第一行，免得挤坏表格
  [ -n "$COMPOSE_VERSION" ]
}

probe_docker_info() {
  # 只收 stderr：失败原因（socket 权限 / 守护进程没跑）比一句 "exit 1" 有用得多。
  DOCKER_INFO_ERR="$(docker info 2>&1 >/dev/null)" && { DOCKER_INFO_ERR=""; return 0; }
  return 1
}

probe_network() {
  NETWORK_ID="$(docker network inspect --format '{{.Id}}' traefik-net 2>/dev/null)" \
    || { NETWORK_ID=""; return 1; }
  [ -n "$NETWORK_ID" ]
}

probe_traefik_running() {
  local running
  TRAEFIK_IMAGE=""
  running="$(docker inspect --format '{{.State.Running}}' traefik 2>/dev/null)" || return 1
  [ "$running" = "true" ] || return 1
  TRAEFIK_IMAGE="$(docker inspect --format '{{.Config.Image}}' traefik 2>/dev/null || true)"
  return 0
}

probe_traefik_present() {
  docker inspect --format '{{.Id}}' traefik >/dev/null 2>&1
}

in_docker_group() {
  case " $(id -nG) " in *" docker "*) return 0 ;; esac
  return 1
}

# .env 只当**数据**读，绝不 source：source 等于执行文件里的任意代码，而这个文件将来由人手写。
# （deploy.sh 用的也是 sed/grep，不是 source——同一个理由。）
env_value() {
  awk -v key="$1" 'index($0, key "=") == 1 { sub(/\r$/, ""); print substr($0, length(key) + 2); exit }' "$2"
}

file_mode() {
  # Ubuntu 是 GNU stat；BSD 写法留作兜底，免得在 macOS 上调试时莫名报错。
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || printf '?'
}

probe_env() {
  ENV_JWT_LEN=0
  ENV_PG_LEN=0
  ENV_MODE="?"
  [ -f "$ENV_FILE" ] || return 1
  ENV_MODE="$(file_mode "$ENV_FILE")"
  local jwt pg
  jwt="$(env_value JWT_SECRET "$ENV_FILE")"
  pg="$(env_value POSTGRES_PASSWORD "$ENV_FILE")"
  ENV_JWT_LEN="${#jwt}"
  ENV_PG_LEN="${#pg}"
  [ "$ENV_JWT_LEN" -ge 32 ] && [ "$ENV_PG_LEN" -gt 0 ]
}

# 端口占用：优先问 ss（能看到所有监听者，包括非 docker 的）。不用 `ss | grep -- ...`——
# 这里判断的是"有没有"，用 awk 精确匹配本地地址那一列；误报会直接导致误判"端口被占"，
# 而误判的代价是拒绝在一台干净机器上装 Traefik。
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk -v p=":$port" '$1 == "LISTEN" && $4 ~ p"$" {found=1} END {exit !found}'
    return
  fi
  # 没有 ss 的极小化系统：退化成只看 docker 发布的端口，至少能挡住容器之间的冲突。
  docker ps --format '{{.Ports}}' 2>/dev/null | awk -v p=":$port->" 'index($0, p) {found=1} END {exit !found}'
}

# ---------------------------------------------------------------------------
# 体检表：✓ 通过 / ✗ 缺失（计入退出码）/ · 与这台机器无关（跳过）
# 标签用 ASCII 并对齐、原因列放中文：中文是双宽字符，塞进 %-Ns 会把表格挤歪。
# ---------------------------------------------------------------------------
CHECKS_FAILED=0
ok()   { printf '  ✓ %-12s %s\n' "$1" "$2"; }
bad()  { printf '  ✗ %-12s %s\n' "$1" "$2"; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
skip() { printf '  · %-12s %s\n' "$1" "$2"; }

check_docker() {
  if probe_docker; then
    ok docker "$DOCKER_VERSION"
  else
    bad docker "没有 docker —— 允许脚本装就加 --install-docker，或手工 sudo apt-get install -y docker.io docker-compose-v2"
  fi
}

check_compose() {
  if probe_compose; then
    ok compose "$COMPOSE_VERSION"
  else
    bad compose "docker compose 子命令不可用 —— 装插件：sudo apt-get install -y docker-compose-v2"
  fi
}

check_docker_info() {
  if probe_docker_info; then
    ok "docker info" "当前用户可以访问 docker socket（已在 docker 组）"
  else
    local first="${DOCKER_INFO_ERR%%$'\n'*}"
    if in_docker_group; then
      bad "docker info" "失败：${first:-未知错误}；当前用户已在 docker 组，所以更像守护进程没跑：sudo systemctl enable --now docker"
    else
      bad "docker info" "失败：${first:-未知错误}；当前用户不在 docker 组 —— apply 会 usermod -aG docker 并提示重新登录"
    fi
  fi
}

check_network() {
  if probe_network; then
    ok traefik-net "外部网络已存在（${NETWORK_ID:0:12}）"
  else
    bad traefik-net "外部网络不存在 —— 建它：docker network create traefik-net"
  fi
}

check_app_dir() {
  if [ -d "$APP_DIR" ]; then
    ok "app dir" "$APP_DIR（权限 $(file_mode "$APP_DIR")）"
  else
    bad "app dir" "目录不存在 —— apply 会创建（默认 \$HOME/<PROJECT_SLUG>，可用 --app-dir 换）"
  fi
}

check_env() {
  probe_env || true
  if [ ! -f "$ENV_FILE" ]; then
    bad .env "不存在 —— apply 会生成一份新的（绝不覆盖已有的）"
    return 0
  fi
  local problems=""
  [ "$ENV_JWT_LEN" -ge 32 ] || problems="${problems}JWT_SECRET 只有 ${ENV_JWT_LEN} 字符（HS256 需要 ≥32）; "
  [ "$ENV_PG_LEN" -gt 0 ]  || problems="${problems}POSTGRES_PASSWORD 为空; "
  [ "$ENV_MODE" = "600" ]  || problems="${problems}权限是 ${ENV_MODE}（应为 600）; "
  if [ -n "$problems" ]; then
    bad .env "${problems%; }（apply 不会替你改它：密钥只由人维护）"
  else
    ok .env "JWT_SECRET ${ENV_JWT_LEN} 字符 / POSTGRES_PASSWORD ${ENV_PG_LEN} 字符 / 权限 ${ENV_MODE}"
  fi
}

check_traefik() {
  if probe_traefik_running; then
    ok traefik "traefik 容器运行中（${TRAEFIK_IMAGE:-未知镜像}）"
  elif [ "$WITH_TRAEFIK" = true ]; then
    bad traefik "要求了 --with-traefik，但没有名为 traefik 的运行中容器"
  elif probe_traefik_present; then
    bad traefik "容器 traefik 存在但没在运行（docker start traefik）"
  else
    # 这台机器上没有叫 traefik 的容器：如果它本来就不用 Traefik（或入口不叫这个名），
    # 这一条与它无关，所以只跳过、不计入失败（否则一台正常机器会被判成"没准备好"）。
    if port_in_use 443; then
      skip traefik "没有名为 traefik 的容器，但 443 有人监听（别的入口）；未要求 --with-traefik"
    else
      skip traefik "没有名为 traefik 的容器，443 也空着；未要求 --with-traefik"
    fi
  fi
}

run_checks() {
  CHECKS_FAILED=0
  log "体检${1:-}"
  printf '  身份文件    %s\n' "$IDENTITY_FILE"
  printf '  项目        %s（slug=%s，域名=%s）\n' "$PROJECT_NAME" "$PROJECT_SLUG" "$APP_DOMAIN"
  printf '  部署目录    %s\n' "$APP_DIR"
  printf '  镜像        %s:latest\n\n' "$IMAGE_REPO"
  check_docker
  check_compose
  check_docker_info
  check_network
  check_app_dir
  check_env
  check_traefik
  printf '\n'
  if [ "$CHECKS_FAILED" -eq 0 ]; then
    printf '  结论：就绪，可以跑 deploy/deploy.sh 了。\n'
  else
    printf '  结论：还有 %s 项没满足（每条 ✗ 后面是修法）。\n' "$CHECKS_FAILED"
  fi
}

# ---------------------------------------------------------------------------
# apply：只做缺的那几步
# ---------------------------------------------------------------------------
CHANGES=()
changed() { CHANGES+=("$*"); printf '  + %s\n' "$*"; }
NEED_RELOGIN=false

# 需要 root 的动作统一走这里：非交互会话（CI、管道执行）里 sudo 要密码时会**挂住**，
# 与其挂着不如直接给出确切命令让人去做。
run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  command -v sudo >/dev/null 2>&1 || fail "需要 root 权限，但这台机器上没有 sudo。请用 root 执行：$*"
  if [ -t 0 ] || sudo -n true 2>/dev/null; then
    sudo "$@"
  else
    fail "需要 root 权限，但当前是非交互会话（sudo 要密码）。请手工执行：sudo $*"
  fi
}

# 生成随机密钥。优先 openssl（Ubuntu 自带），没有就退回 /dev/urandom + base64。
# 结果只写进文件，永远不打印值本身。
random_b64() {
  local bytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 "$bytes" | tr -d '\n='
  else
    head -c "$bytes" /dev/urandom | base64 | tr -d '\n='
  fi
}

# 数据库口令专用：只要 0-9a-f。**不是**把 base64 结果过滤一遍——base64 字母表里只有
# 16/64 个字符是十六进制数字，过滤后长度会掉到原来的四分之一（实测 32 字符 → 10~15 字符），
# 也就是"看着随机、其实很弱"。所以直接从字节生成 hex：24 字节 → 48 字符。
random_hex() {
  local bytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$bytes"
  else
    head -c "$bytes" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# 只写 compose 真正会**插值**的键。写了却不会生效的键（比如 JWT_TTL_SECONDS、
# BCRYPT_ROUNDS——compose.yaml 的 environment 里没有它们，不会被转发进容器）比不写更坏：
# 人会以为改了它就生效，而实际改的是一份没人读的配置。
write_env_file() {
  printf '# 由 deploy/bootstrap-server.sh 于 %s 生成（只生成这一次，之后由人维护）。\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '# 密钥只存在于这台服务器：CI 从不传输、从不覆盖它，bootstrap 也绝不改写已有的 .env。\n'
  printf '# deploy.sh 每次部署只会改 APP_IMAGE 这一行，其余行是人的领地。\n'
  printf 'JWT_SECRET=%s\n' "$1"
  printf '# 数据库口令刻意用 hex：compose 会把它拼进 DATABASE_URL，\n'
  printf '# base64 里的 / + = 会把 URL 拆坏（症状是迁移连不上库，却看不出为什么）。\n'
  printf 'POSTGRES_USER=%s\n' "$PROJECT_SLUG"
  printf 'POSTGRES_PASSWORD=%s\n' "$2"
  printf 'POSTGRES_DB=%s\n' "$PROJECT_SLUG"
  printf '\n# 部署标识：与 project.env 同源（本脚本从那里读出来），compose 只认环境变量。\n'
  printf 'APP_IMAGE=%s:latest\n' "$IMAGE_REPO"
  printf 'APP_DOMAIN=%s\n' "$APP_DOMAIN"
  printf 'COMPOSE_PROJECT_NAME=%s\n' "$PROJECT_SLUG"
}

setup_traefik() {
  [ -n "$ACME_EMAIL" ] || fail "--with-traefik 需要同时给 --acme-email you@example.com（Let's Encrypt 要用它通知证书到期）"
  case "$ACME_EMAIL" in
    *@*.*) ;;
    *) fail "--acme-email 看起来不像邮箱：$ACME_EMAIL" ;;
  esac

  # 先看 80/443 有没有人。这两个端口是共享资源，本脚本的立场是"发现冲突就停下"，
  # 而不是把别人的服务挤掉——这台机器上很可能已经有一个 Traefik 在跑（本项目的服务器
  # 就是），那说明**不该**再装一个，而应确认它接在 traefik-net 上、证书解析器叫 letsencrypt。
  local port
  for port in 80 443; do
    if port_in_use "$port"; then
      printf '\n  当前监听 %s 的进程：\n' "$port" >&2
      ss -ltnp 2>/dev/null | awk -v p=":$port" '$1 == "LISTEN" && $4 ~ p"$"' >&2 || true
      fail "端口 $port 已被占用 —— 不抢别人的端口，本次什么都没改。
    如果占用者已经是一个 Traefik，请不要用 --with-traefik：只要确认它接在 traefik-net 上、
    且证书解析器命名为 letsencrypt（deploy/compose.server.yaml 里 app 的 label 引用的就是这个名字）。"
    fi
  done

  local dir="$APP_DIR/traefik"
  mkdir -p "$dir"
  # 两个文件各自判断、各自不覆盖：人手改过的那份配置不该被 bootstrap 冲掉。
  if [ -e "$dir/compose.yaml" ]; then
    printf '  %s/compose.yaml 已存在，不覆盖\n' "$dir"
  else
    cat > "$dir/compose.yaml" <<'YAML'
# 最小 Traefik（由 deploy/bootstrap-server.sh --with-traefik 生成）
services:
  traefik:
    # 钉住 minor 版本：Traefik v2 → v3 改过配置键名，让 bootstrap 静默跳到新大版本
    # 只会在某天重启时炸掉，而那时没人记得这台机器上跑的是什么。
    image: traefik:v3.2
    container_name: traefik
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "80:80"
      - "443:443"
    volumes:
      # 只读挂载 docker.sock：Traefik 只需要"看"容器的 label，不需要写。
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./traefik.yml:/etc/traefik/traefik.yml:ro
      # 挂目录而不是挂单个 acme.json：bind mount 一个不存在的文件会被建成目录，
      # 而 Traefik 要求这个文件存在、权限 600，挂目录两件事都能避开。
      - ./acme:/acme
    networks:
      - traefik-net

networks:
  traefik-net:
    external: true
YAML
    changed "写入 $dir/compose.yaml"
  fi

  if [ -e "$dir/traefik.yml" ]; then
    printf '  %s/traefik.yml 已存在，不覆盖（要确认里面的证书解析器仍叫 letsencrypt）\n' "$dir"
  else
    cat > "$dir/traefik.yml" <<YAML
# Traefik 静态配置（由 deploy/bootstrap-server.sh --with-traefik 生成）
certificatesResolvers:
  # 解析器名字**必须**是 letsencrypt：部署覆盖文件 deploy/compose.server.yaml 里
  #   traefik.http.routers.<slug>.tls.certresolver: letsencrypt
  # 是字面量，改名等于路由找不到解析器——表现是证书签不下来、公网 HTTPS 不通，
  # 而容器本身 healthy、部署脚本还会报成功。这类"名字对不上"是最难查的一种失败。
  letsencrypt:
    acme:
      email: ${ACME_EMAIL}
      storage: /acme/acme.json
      # HTTP-01：Let's Encrypt 从公网访问 http://<域名>/.well-known/acme-challenge/…
      # 所以 DNS 必须先指向这台机器、云安全组必须放行 80，否则签不下来。
      httpChallenge:
        entryPoint: web

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        # 平时 80 全部跳到 443；ACME 的校验路径由 Traefik 自己优先接管，不受影响。
        entryPoint:
          to: websecure
          scheme: https
          permanent: true
  websecure:
    address: ":443"
    http:
      tls:
        certResolver: letsencrypt

providers:
  docker:
    # 只暴露显式打了 traefik.enable=true 的容器，且只在 traefik-net 上找它们。
    # 这台机器通常还有别的项目（别的 compose 栈、别的面板），默认暴露会把它们一起发到公网。
    exposedByDefault: false
    network: traefik-net

log:
  level: INFO
YAML
    changed "写入 $dir/traefik.yml（ACME 邮箱 $ACME_EMAIL，解析器 letsencrypt）"
  fi

  # acme.json 必须先存在且是 600：Traefik 对它的权限很挑（组/其他人可读就拒绝启动），
  # 而 bind mount 一个不存在的单文件会被建成目录。所以挂目录 + 预建空文件。
  mkdir -p "$dir/acme"
  if [ ! -e "$dir/acme/acme.json" ]; then
    ( umask 077; : > "$dir/acme/acme.json" )
    changed "预建 $dir/acme/acme.json（600，ACME 证书写在这里）"
  fi
  chmod 600 "$dir/acme/acme.json"

  if ! docker compose -f "$dir/compose.yaml" up -d; then
    fail "Traefik 没起来（原因看上面的 docker 输出）。常见三种：acme.json 权限不是 600、80/443 被别的进程抢先占了、镜像拉不下来"
  fi
  changed "启动 Traefik（docker compose -f $dir/compose.yaml up -d）"
}

print_manual_steps() {
  log "剩下的必须由人做的事（脚本不代替，理由见 report/AUTOMATION.md §1）"
  cat <<TXT
  1) 部署公钥：把 CI 用的公钥追加进 ~/.ssh/authorized_keys
       echo '<公钥内容>' >> ~/.ssh/authorized_keys
     私钥一旦由脚本或流水线代传，就等于多了一份能被读取的副本——
     这一步的"麻烦"本身就是它的安全边界。

  2) GitHub 侧：Secret 放私钥，Variables 放地址与用户名
       gh secret   set DEPLOY_SSH_KEY < <私钥文件>
       gh variable set DEPLOY_HOST --body <CI 能连上的公网地址或域名>
       gh variable set DEPLOY_USER --body $(id -un)

  3) 推一个 tag 触发首次发版（验证、构建、发布、部署、公网冒烟都是流水线的事）
       git tag -a v0.1.0 -m "first release" && git push origin v0.1.0

  4) DNS：把 $APP_DOMAIN 解析到这台机器（一辈子一次，脚本不动 DNS）。
     云安全组同时放行 80 与 443：Let's Encrypt 的 HTTP-01 校验要从公网访问 80，
     否则证书签不下来、公网 HTTPS 不通。

  5) （可选）接支付：把 STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET /
     STRIPE_PRICE_PLUS / STRIPE_PRICE_PRO 写进 $ENV_FILE 再重新部署。
     不配也能跑：/api/billing/* 返回 503，界面会说明"本服务器未配置支付"。
TXT
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
if [ "$MODE_CHECK" = true ]; then
  run_checks "（--check：只读，不做任何改动）"
  [ "$CHECKS_FAILED" -eq 0 ] || exit 1
  exit 0
fi

log "准备这台机器（apply：只补缺的东西）"
printf '  机器      %s@%s\n' "$(id -un)" "$(hostname)"
printf '  部署目录  %s\n' "$APP_DIR"

# --- 1. docker 与 compose 插件 --------------------------------------------
if probe_docker && probe_compose; then
  printf '  docker 与 compose 插件都在（%s / %s）\n' "$DOCKER_VERSION" "$COMPOSE_VERSION"
elif [ "$INSTALL_DOCKER" != true ]; then
  fail "缺 docker 或 compose 插件。允许脚本安装就加 --install-docker，或手工执行：
    sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2"
else
  log "安装 docker（apt）"
  # 为什么用 apt 而不是 `curl -fsSL https://get.docker.com | sh`：
  #   官方 convenience script 会往 apt 里塞一个新仓库、总是装最新版，而且要"下载一个
  #   脚本直接执行"——供应链上多一跳。Ubuntu 仓库里的 docker.io + docker-compose-v2
  #   版本旧一些，但本项目只用得到 compose 文件、healthcheck、`up --wait` 这些多年稳定
  #   的功能，换来的是 apt upgrade 能跟着走、包可审计。
  #   代价：Ubuntu 22.04 及更早没有 docker-compose-v2 包，下面的兜底会提示改用官方脚本。
  run_root apt-get update
  run_root apt-get install -y docker.io
  if ! run_root apt-get install -y docker-compose-v2; then
    warn "这个发行版没有 docker-compose-v2 包（Ubuntu 22.04 及更早），改试 docker-compose-plugin"
    run_root apt-get install -y docker-compose-plugin \
      || fail "compose 插件装不上。手工装官方脚本版：curl -fsSL https://get.docker.com | sh（总是装最新版）"
  fi
  changed "apt 安装 docker.io 与 compose 插件"

  # 刚装完守护进程可能还没起来（apt 一般会起，但没有 systemd 的环境不会）。
  if ! probe_docker_info; then
    warn "docker 已安装但守护进程还没响应，尝试启动它"
    run_root systemctl enable --now docker || warn "systemctl 启动失败，稍后手工确认：sudo systemctl status docker"
  fi
fi

# --- 2. 当前用户要能直接用 docker（不是 root 也能跑部署）--------------------
if probe_docker_info; then
  printf '  docker 可用：当前用户已能访问 socket（组：%s）\n' "$(id -nG)"
elif in_docker_group; then
  fail "当前用户已在 docker 组，但 docker info 仍失败：多半是守护进程没跑。
    sudo systemctl enable --now docker   # 然后重跑本脚本"
else
  log "把 $(id -un) 加入 docker 组"
  run_root usermod -aG docker "$(id -un)"
  changed "把 $(id -un) 加入 docker 组"
  NEED_RELOGIN=true
  warn "组变更对已登录的会话无效：**需要重新登录**（退出 SSH 重连，或 newgrp docker）后再跑一次本脚本"
fi

DOCKER_USABLE=false
probe_docker_info && DOCKER_USABLE=true

# --- 3. traefik-net：compose.server.yaml 把它声明为 external -----------------
if [ "$DOCKER_USABLE" != true ]; then
  warn "docker 当前不可用（见上一条），跳过建网络。重新登录后执行：docker network create traefik-net"
elif probe_network; then
  printf '  traefik-net 已存在（%s）\n' "${NETWORK_ID:0:12}"
else
  docker network create traefik-net >/dev/null
  changed "创建 docker 网络 traefik-net（compose.server.yaml 把它声明为 external）"
fi

# --- 4. 部署目录 -----------------------------------------------------------
if [ -d "$APP_DIR" ]; then
  printf '  部署目录已存在：%s（权限 %s）\n' "$APP_DIR" "$(file_mode "$APP_DIR")"
else
  mkdir -p "$APP_DIR"
  chmod 755 "$APP_DIR"
  changed "创建部署目录 $APP_DIR（755）"
fi

# --- 5. .env：只在不存在时生成 --------------------------------------------
if [ -e "$ENV_FILE" ]; then
  warn "已存在 $ENV_FILE —— 一个字都不改（密钥只由人维护；deploy.sh 也只改 APP_IMAGE 一行）"
else
  jwt="$(random_b64 48)"
  # 数据库口令用 hex：它会被拼进 DATABASE_URL，base64 的 / + = 会拆坏 URL。
  pg="$(random_hex 24)"
  [ "${#jwt}" -ge 32 ] || fail "生成的 JWT_SECRET 只有 ${#jwt} 字符（HS256 需要 ≥32），检查 openssl 是否正常"
  [ "${#pg}" -ge 32 ]  || fail "生成的 POSTGRES_PASSWORD 只有 ${#pg} 字符，检查 openssl 是否正常"

  # set -C（noclobber）是第二道保险：万一有人在这几毫秒里建了同名文件，宁可报错也绝不
  # 覆盖——生产环境的 .env 被覆盖是不可逆的事故，而"多问一次"的代价只是重跑。
  if ( set -C; write_env_file "$jwt" "$pg" > "$ENV_FILE" ); then
    chmod 600 "$ENV_FILE"
    changed "生成 $ENV_FILE（JWT_SECRET ${#jwt} 字符 / POSTGRES_PASSWORD ${#pg} 字符 / 权限 600）"
    unset jwt pg
  else
    fail "写 $ENV_FILE 失败（可能已存在或权限不足）——没有覆盖任何东西，请手工检查这个文件"
  fi
fi

# --- 6. 可选：最小 Traefik -------------------------------------------------
if [ "$WITH_TRAEFIK" = true ]; then
  [ "$DOCKER_USABLE" = true ] || fail "--with-traefik 需要能用的 docker（见上一条），本次什么都没改"
  probe_network || fail "--with-traefik 需要 traefik-net 网络，但它还不存在（见上一条），本次什么都没做"
  setup_traefik
fi

# --- 7. 人的那几步 + 复检 --------------------------------------------------
print_manual_steps
run_checks "（apply 之后复检）"

if [ "${#CHANGES[@]}" -eq 0 ]; then
  printf '\n  本次没有改动任何东西：这台机器已经是准备好的状态（幂等）。\n'
else
  printf '\n  本次改了 %s 项：\n' "${#CHANGES[@]}"
  printf '    - %s\n' "${CHANGES[@]}"
fi
[ "$NEED_RELOGIN" = true ] && printf '  另：docker 组变更需要重新登录后才生效，登录后请再跑一次确认。\n'

[ "$CHECKS_FAILED" -eq 0 ] || exit 1
exit 0
