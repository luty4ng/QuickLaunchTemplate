# QuickLaunch 上线方案（部署到个人服务器 + Stripe 订阅支付）

> 状态：**已完成**（阶段 0–5 全部落地；线上 https://quicklaunch.luty.tech）。
> 唯一待你提供的是真实 Stripe 密钥与两个 `price_*`（见 §10），给到即可生效，代码不用改。
> 本文不含任何密钥。
> 编写依据：对 `luty-server` 的实际只读探测 + 仓库代码 + 已核实的第三方源码/文档。

---

## 0. 决策记录（已与用户确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 发版触发 | **只认 tag**：`v1.2.0` 形式。推送 main 分支只跑 CI + 临时栈冒烟，**不发布、不部署** |
| 2 | tag 粒度 | **单 tag 发全套**（网页端镜像 + 桌面产物 + `latest.yml`），**不用** `_web` / `_desktop` 后缀 |
| 3 | 订阅档位 | **Free 10 条 / Plus 200 条 / Pro 无限**，暂只分额度 |
| 4 | 计费周期 | 先只做**月付** |
| 5 | 部署方式 | **SSH**（不复用 9router 的 webhook 模式） |
| 6 | 入口 | 挂 Traefik + `traefik-net`，**不占新端口** |
| 7 | 域名 | `quicklaunch.luty.tech` A → `122.152.219.10`（**已生效**） |
| 8 | SSH 密钥 | **一对部署专用密钥，所有项目共用**（官方：一台服务器一把即可）；是否收紧为单命令待定 |
| 9 | 回滚 | 换 `APP_IMAGE` 到上一个 `sha-` tag 重跑 |
| 10 | 镜像清理 | 服务器只保留最近 3 个 tag |
| 11 | `.env` 写入 | **由用户在服务器上执行**，密钥不经过 AI、不进 GitHub |

### 0.1 为什么用「单 tag 发全套」而不是后缀分开

这是本次方案里唯一一处推翻了初始设想的决策，依据是**读源码得到的硬约束**：

`electron-updater@6.8.9` 的 `GitHubProvider.getLatestVersion()` 只取 GitHub `releases.atom` 的
**第一个 entry（即最新 Release）**，然后在该 Release 内找 `latest.yml`；找不到即抛错。
遍历历史 Release 的分支仅在 `allowPrerelease` 为真时启用，默认关闭。

> **不变量：最新 Release 必须包含 `latest.yml`，否则桌面端自动更新静默失效。**

若采用 `_web` / `_desktop` 后缀，发一次 `_web` 就会让 `_web` 成为"最新 Release"而其中没有
`latest.yml`，必须在每次发布时额外携带该文件——多一条容易漏掉的不变量。

而本项目的桌面端与 API **同仓库、同提交构建**，一起发布天然保证版本一致；分开发反而会产生
「客户端 1.2.0 连后端 1.1.0」的版本偏移。故采用单 tag 全发。

「只部署网页端」这类例外需求，改用独立的 `workflow_dispatch` 入口，**不占用 tag 语义**。

### 0.2 附带的硬门禁

- tag 格式校验：`^v\d+\.\d+\.\d+$`，拼错即拦；
- **tag 版本号必须严格大于已发布的最高版本**，否则报错。
  理由：桌面端只接受更高版本，打一个旧号会静默永久失效；
- Release 必须包含 `latest.yml` 且其中 `version` 与 tag 一致。

---

## 1. 目标

把 QuickLaunch 从「在 GitHub runner 上验证制品」升级为**真实上线**，并接上 Stripe 订阅支付：

1. CI/CD 增加真正的部署环节：main 分支绿灯后，自动把镜像部署到 `luty-server`，通过 Traefik 走 HTTPS 对外服务。
2. Stripe 订阅：免费版限 10 条待办，订阅后解锁无限；用 Stripe 托管的 Checkout 收钱，用 webhook 确认。

---

## 2. 服务器现状（实测，非推断）

| 项 | 实测值 | 对方案的影响 |
|---|---|---|
| 主机 | `VM-0-8-ubuntu`，Ubuntu 24.04 LTS，x86_64 | CI 构建的是 amd64 镜像，**可直接 pull** |
| 配置 | 4 vCPU / 3.6 GB 内存 / 40 GB 磁盘（探测时为 26G 已用；用户已扩容） | 本应用镜像约 110 MB，Postgres 数据量极小，**余量充足** |
| Docker | 27.5.1，Compose v2.32.4 | 无需安装任何东西 |
| 用户 | `ubuntu`，**属于 docker 组**（可直接跑 docker），`sudo -n` 免密可用 | 部署不需要 sudo |
| 现有容器 | `traefik:v3.2`（占 80/443）、`homepage`（5005）、`9router`（20128） | **80/443 已被占用，不能自己监听** |
| Docker 网络 | 存在 `traefik-net` | 应用加入该网络，由 Traefik 反代 |
| Traefik 配置 | `exposedByDefault: false`，network `traefik-net`，file provider 读 `~/traefik/dynamic/dynamic.yml` | **只有显式打 label 的容器才会被暴露**，符合我的需求 |
| TLS | Let's Encrypt，tlsChallenge（TLS-ALPN-01），证书存 `~/traefik/acme.json` | 新子域名自动签发证书，无需手动运维 |
| 域名 | `luty.tech` → 122.152.219.10（apex 走 homepage，`router.luty.tech` 走 9router） | 新增 `quicklaunch` 子域名，互不影响 |
| DNS | ✅ **`quicklaunch.luty.tech` → 122.152.219.10 已生效**（NS 在 DNSPod） | 前置条件已满足，可进入阶段 0 |
| 防火墙 | `ufw` inactive；22/80/443 外部可达 | 入站由云厂商安全组控制 |
| 已有的部署模式 | `~/9router-webhook.py` + systemd `9router-webhook.service`，端口 9876，HMAC 验签 | **当前 inactive，且 9876 未监听** |
| `~/.ssh/authorized_keys` | 1 条公钥（用户本机） | 部署专用公钥需追加；由用户操作或授权我代做，**不覆盖原条目** |

### 为什么不复用已有的 webhook 模式

9router 那套是「GitHub Actions 发 POST → 服务器跑脚本」。我不采用，理由三条：

1. 它是**为 9router 写死的**（`9router-auto-update.sh` 里必然包含该项目的路径与容器名），改它等于动你正在跑的生产服务；
2. webhook 服务当前是停的，重新启用它会顺带影响 9router 的更新链路；
3. webhook 方案在**公网暴露一个能触发部署的端口**，而 SSH 方案只需要 22（本来就开着），攻击面更小。

结论：**用 GitHub Actions 直接 SSH 到服务器执行部署**，与 9router 完全隔离，互不影响。

---

## 3. 方案 A：部署环节

### 3.1 总体流程

```
推送 tag v1.2.0
   │
   ├─ verify-backend / verify-web
   ├─ 版本门禁：tag 格式合法 + 严格大于已发布最高版本
   ├─ docker: 构建并推送 ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>
   ├─ deploy-local: 在 runner 上起栈 + 冒烟（快速门禁，不变）
   ├─ desktop / android(可选): 打包，版本号取自 tag
   ├─ release: 发 Release（含 latest.yml，version 与 tag 一致）
   │
   └─ deploy-server（新增）
         ├─ SSH 到 luty-server（部署专用密钥）
         ├─ docker compose pull app migrate
         ├─ 迁移先跑：compose up --exit-code-from migrate migrate
         ├─ 起应用：up -d --wait app，等容器 healthy
         └─ 公网验收：python scripts/smoke.py --base-url https://quicklaunch.luty.tech
```

**关键点：部署后的验收不是「容器起来了」，而是「从公网用真实 HTTPS 打通全链路」**——
复用已有的 `scripts/smoke.py`（17 项断言，含越权检查），把 base-url 换成线上域名。

### 3.2 服务器上的目录与文件

```
~/quicklaunch/                 # 新增，与 9router/traefik/homepage 并列，互不干扰
  ├── compose.yaml             # 从仓库同步（或首次 scp，之后由 CI 覆盖）
  ├── .env                     # chmod 600：JWT_SECRET / POSTGRES_PASSWORD / STRIPE_* 
  └── （数据在 docker volume quicklaunch_db-data 里，不在这个目录）
```

**与 9router 的隔离**：独立目录、独立 compose project name（`quicklaunch`）、独立 volume、独立容器名。

### 3.3 接入 Traefik 的方式（不占用任何新端口）

给 `app` 服务加 labels（写进服务器的 compose 覆盖文件，不改仓库里的通用 compose）：

```yaml
services:
  app:
    networks: [default, traefik-net]
    labels:
      - traefik.enable=true
      - traefik.docker.network=traefik-net
      - traefik.http.routers.quicklaunch.rule=Host(`quicklaunch.luty.tech`)
      - traefik.http.routers.quicklaunch.entrypoints=websecure
      - traefik.http.routers.quicklaunch.tls.certresolver=letsencrypt
      - traefik.http.services.quicklaunch.loadbalancer.server.port=8000
networks:
  traefik-net:
    external: true
```

同时**移除** `8000:8000` 的端口映射（不需要对外暴露，只让 Traefik 从内部网络访问）。

好处：`COOKIE_SECURE=true` 可以真正开启（有 HTTPS 了），会话 Cookie 才安全。

### 3.4 密钥与 secret 的流转

| Secret | 存放位置 | 谁写入 |
|---|---|---|
| `JWT_SECRET` | 服务器 `~/quicklaunch/.env`（600） | 首次部署时生成并写入，之后不复用 GitHub |
| `POSTGRES_PASSWORD` | 同上 | 同上 |
| `STRIPE_SECRET_KEY` | 服务器 `.env` + GitHub Secret（供 CI 冒烟测试用） | 你提供 |
| `STRIPE_WEBHOOK_SECRET` | 服务器 `.env` | 你从 Stripe 后台取得 |
| `DEPLOY_SSH_KEY` | GitHub Secret（仓库级） | 你把我生成的**公钥**加到服务器，**私钥**贴进 GitHub Secret |
| `DEPLOY_HOST` / `DEPLOY_USER` | GitHub Variable | 122.152.219.10 / ubuntu |

**我会坚持的几条**：
- 密钥**只**存在服务器 `.env`（600）和 GitHub Secrets，**绝不进仓库、不进日志、不进回复**；
- SSH 严格校验主机指纹（`known_hosts` 固定服务器公钥），防止中间人；
- 部署用的 SSH 密钥**只用于这个仓库**，可随时在服务器上删掉那一行 `authorized_keys` 撤销；
- 不在部署日志里 `echo` 任何 `.env` 内容（用 `docker compose config` 时要过滤）。

### 3.5 部署之后如何回滚

镜像 tag 是 `sha-<commit>`，所以回滚 = 把 `.env` 里的 `APP_IMAGE` 换成上一个 tag，重跑一次 `up -d`。
我会额外提供 `~/quicklaunch/rollback.sh`（读参数指定 tag），并在 README 写明步骤。
**不自动回滚**（与前文一致：自动回滚会掩盖问题）。

---

## 4. 方案 B：Stripe 订阅支付

### 4.1 产品定义

- 免费版：最多 **10 条待办**
- 订阅后（Pro）：无限条
- 收款方式：**Stripe 托管 Checkout 页面**（卡号永远不进我们的前端和服务器，PCI 负担最小）
- 取消/换卡：走 **Stripe 客户门户**（Billing Portal），我不写取消逻辑

### 4.2 技术前提（已实测核实）

| 项 | 实测值 | 说明 |
|---|---|---|
| `stripe-python` | **15.6.1**（已装到本地 venv 验证过） | 会钉死这个版本 |
| SDK 固定发送的 API 版本 | **`2026-08-26.dahlia`** | 事件字段结构由它决定 |
| `Webhook.construct_event` | 存在，**默认容差 300 秒** | CI 与服务器时钟需大致准确，否则验签失败 |
| ⚠️ 待核实 | 订阅周期字段可能在 `items.data[0].current_period_end` | **拿到密钥后我会用一次真实 API 调用确认，不猜** |

### 4.3 数据模型（新增一个迁移）

`users` 表新增：

| 字段 | 用途 |
|---|---|
| `plan` | `free` / `pro` |
| `stripe_customer_id` | 唯一索引，关联 Stripe 客户 |
| `stripe_subscription_id` | 当前订阅 |
| `subscription_status` | `active` / `trialing` / `past_due` / `canceled` …（原样存 Stripe 的值） |
| `current_period_end` | 用于前端展示「有效期至」 |

新增 `billing_events` 表：

| 字段 | 用途 |
|---|---|
| `event_id`（主键） | **幂等键**。Stripe 会重复投递同一事件；没有这张表就会重复开通 |
| `event_type` / `received_at` / `payload_digest` | 审计与排障 |

### 4.4 接口

| 方法与路径 | 作用 | 认证 |
|---|---|---|
| `GET /api/billing/me` | 当前套餐、到期时间、剩余额度 | 需登录 |
| `POST /api/billing/checkout` | 创建订阅 Checkout 会话，返回托管页 URL | 需登录 |
| `POST /api/billing/portal` | 返回客户门户 URL（取消/换卡） | 需登录 |
| `POST /api/billing/webhook` | 验签 → 幂等 → 更新订阅状态 | **无 Cookie 认证，靠签名** |

`POST /api/todos` 增加额度检查：免费用户超过 10 条返回 **`402 Payment Required`**，
错误体沿用既有格式 `{"error": {"code": "quota_exceeded", "message": ...}}`。

### 4.5 只处理这 5 个事件

| 事件 | 处理 |
|---|---|
| `checkout.session.completed` | 记录 customer/subscription 关联 |
| `customer.subscription.created` | 开通 Pro |
| `customer.subscription.updated` | 同步状态与到期时间（续费、升降级、宽限期） |
| `customer.subscription.deleted` | 降回 free |
| `invoice.paid` | 续费成功，刷新到期时间 |

`invoice.payment_failed` **只记录日志、不改权限**——避免过度设计，也避免误伤用户。

**验签与幂等是硬要求**：
- 缺少 `Stripe-Signature` → `400`
- 验签失败 → `400`
- 时钟超出容差 → `400`（并记录日志便于排查）
- 同一个 `event_id` 第二次到达 → 直接 `200` 返回，不重复处理

### 4.6 前端改动

- 待办列表上方显示额度：`3 / 10 条（免费版）`，Pro 显示 `无限`
- 达到上限时，「添加」按钮旁出现「升级解锁无限」按钮 → 调 `/api/billing/checkout` → `window.location = url`
- 已订阅时显示「管理订阅」→ 客户门户
- 支付返回后回到应用，前端轮询 `/api/billing/me` 直到状态更新（webhook 是异步的，不能假设同步到达）

### 4.7 测试策略（关键：现有 45 个测试必须保持离线）

| 层 | 做法 |
|---|---|
| 单元测试 | 注入**假的支付网关**（接口抽象），断言额度逻辑、状态机、幂等 |
| 验签测试 | 用真实 SDK 的 `construct_event` 签一个 payload；**篡改签名必须被拒**、过期时间戳必须被拒 |
| 端到端 | 新增 `scripts/fake_stripe.py`：本地桩服务，模拟创建会话、支付成功、投递已签名 webhook；CI 里跑通「下单 → 付款 → 开通 Pro → 第 11 条待办能建」 |
| 未配置密钥时 | `/api/billing/*` 返回 **503**，明确失败，**绝不静默假装成功** |
| 线上验收 | 部署后用 Stripe **测试模式**真实走一遍：真 Checkout 页面 + 真 webhook 到达 |

---

## 5. 会改到哪些文件

| 文件 | 改动 |
|---|---|
| `.github/workflows/pipeline.yml` | 新增 `deploy-server` job（SSH 部署 + 线上冒烟） |
| `backend/app/config.py` | 新增 Stripe 与额度配置项 |
| `backend/app/db/models.py` | `users` 加支付字段；新增 `billing_events` |
| `backend/migrations/versions/0002_billing.py` | 新迁移（CI 会验证可升级 + 可回滚） |
| `backend/app/billing/`（新） | `gateway.py`（Stripe 抽象）、`service.py`（状态机与幂等） |
| `backend/app/routers/billing.py`（新） | 4 个接口 |
| `backend/app/routers/todos.py` | 额度检查 |
| `backend/requirements.txt` | `stripe==15.6.1` |
| `backend/tests/` | 单元 + 集成 + 验签/幂等测试 |
| `web/src/` | 额度展示、升级按钮、订阅管理、支付返回处理 |
| `scripts/fake_stripe.py`、`scripts/smoke.py` | 桩服务；冒烟扩展支付链路 |
| `compose.yaml` | 新增 Stripe 相关环境变量占位 |
| `README.md` / `report/REPORT.md` | 补充部署与支付章节 |

**不会改到**：`~/9router*`、`~/traefik/*`、`~/homepage`、`~/litellm` 以及任何其他项目。

---

## 6. 验收标准

- [ ] `https://quicklaunch.luty.tech` 返回 200，且 `/api/health` 的 `database` 为 `up`
- [ ] Traefik 自动签发 Let's Encrypt 证书（`https` 无警告）
- [ ] CD 部署后**从公网**跑 `smoke.py` 全绿（17 项 + 新增支付项）
- [ ] 未签名的 webhook 请求被拒（`400`）；重放同一 `event_id` 不产生第二次开通
- [ ] 免费用户第 11 条待办返回 `402`；测试模式付款后立即解锁
- [ ] 回滚演练：切回上一个 `sha-` tag 后服务恢复正常
- [ ] 服务器重启后容器自动恢复（`restart: unless-stopped`），且数据不丢
- [ ] 仓库中**不存在任何密钥**（`git grep` 检查常见密钥前缀）

---

## 7. 需要你决定或操作的事

### 7.1 必须你来做（我做不了）

1. ~~加 DNS A 记录~~ ✅ **已完成**（`quicklaunch.luty.tech` → `122.152.219.10`，已验证解析）
2. **部署侧的 GitHub 配置**（只能你操作）：
   - Secret `DEPLOY_SSH_KEY`：部署专用**私钥**（待我生成公钥给你之后）
   - Variable `DEPLOY_HOST` = `122.152.219.10`
   - Variable `DEPLOY_USER` = `ubuntu`
3. **把部署公钥加进服务器** `~/.ssh/authorized_keys`（**不覆盖原有条目**）：
   由你执行，或你授权我代做（我会先把完整命令给你看）
4. **Stripe 侧配置**：
   - 建测试账号，保持 **Test mode**
   - 建 1 个 Product + 2 个 Price（Plus 月付 / Pro 月付），把两个 `price_...` 给我
   - 在 Billing → Customer portal 里**启用客户门户**（否则「管理订阅」按钮会报错）
   - 服务器跑起来后再注册 webhook 端点 `https://quicklaunch.luty.tech/api/billing/webhook`，
     取得 `whsec_...`
5. **提供 Stripe 测试密钥**：`sk_test_...`（不给我也能全部实现 + 用桩验证，只是真实 Checkout 那一下要你点）

### 7.2 已确认的决策点

| # | 问题 | 结论 |
|---|---|---|
| 1 | 发版触发 | ✅ **只认 tag**，`v1.2.0` 单 tag 发全套 |
| 2 | SSH 公钥追加 | ⏳ 待我生成公钥后由你操作或授权我代做 |
| 3 | 订阅周期 | ✅ 先只做月付 |
| 4 | 档位与额度 | ✅ Free 10 / Plus 200 / Pro 无限 |
| 5 | 部署目录 | ✅ `~/quicklaunch/` |
| 6 | 部署时短暂中断 | ✅ 接受（单副本无法真零停机） |
| 7 | 部署密钥是否收紧为单命令 | ⏳ 待定（见 §3.4） |

---

## 8. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 证书签发失败 | 域名不通 | 阶段 0 先用占位容器验证 Traefik 能签发，**再**做支付 |
| **tag 版本号未递增** | 桌面端**静默**永不更新 | 硬门禁：tag 版本必须严格大于已发布最高版本，否则管线报错 |
| **最新 Release 缺 `latest.yml`** | 同上，静默失效 | 单 tag 全发从机制上消除该风险；release job 仍校验文件存在且版本一致 |
| Stripe 事件字段与预期不符 | 订阅状态写错 | 拿到密钥后先用真实 API 调一次并打印结构，**按实际字段写代码** |
| webhook 未到达（网络/配置） | 用户付了钱没解锁 | 提供 `/api/billing/sync` 兜底：用户点「我已完成支付」时主动向 Stripe 查一次真实订阅状态 |
| 部署密钥泄露 | 服务器被入侵 | 专用密钥（不与你个人密钥共用）+ 可选 `command=` 收紧为单命令 + 随时删 `authorized_keys` 一行撤销 |
| 服务器磁盘 | 构建失败 | 只 pull 镜像不在服务器构建；部署脚本清理**本项目**旧 tag，保留最近 3 个 |
| 误改到其他项目 | **严重** | 所有操作限定在 `~/quicklaunch/`；不动 `9router`/`traefik`/`homepage`；每次改动前先 `ls` 确认路径 |
| 密钥泄漏进日志 | **严重** | 部署脚本不打印 `.env`；仓库加密钥扫描检查 |

---

## 9. 实施顺序（已获批准，按序执行）

分阶段，每阶段结束都可验证、可停下来：

### ✅ 阶段 0｜连通性 —— **已完成（2026-09-12）**

在 `~/quicklaunch/` 下用独立项目 `ql-phase0` 起了 nginx 占位容器，挂 Traefik label，
验证后销毁（`docker compose down`，未用 `-v`）。实测结果：

| 验收项 | 结果 |
|---|---|
| 证书主体 / 签发者 | `CN = quicklaunch.luty.tech` / **Let's Encrypt**（`CN = YR2`） |
| 有效期 | `Sep 12 08:34:24` → `Dec 11 08:34:23 2026 GMT` |
| SAN | `DNS:quicklaunch.luty.tech` |
| acme.json 证书数 | 3 → **4**（确认是新签发，非复用泛域名） |
| 外网访问 | **HTTP 200**，页面内容正确 |
| 其他服务未受影响 | `luty.tech` 200、`router.luty.tech` 307、`traefik.luty.tech` 401（与改动前一致） |

同时验证了服务器 compose（v2.32.4）**支持 `!reset` 覆盖语法**：
`docker compose -f compose.yaml -f compose.server.yaml config` 合并后
`app.ports = None`、app 同时接入 `default` 与 `traefik-net`、6 条 Traefik label 生效。

### ⚠️ 阶段 1｜遇到环境约束：服务器拉不动 GHCR（待决策）

`deploy.sh` 在「拉取镜像」这一步挂死。实测服务器出网吞吐：

| 目标 | 实测速度 | 结论 |
|---|---|---|
| **ghcr.io** | **0.00 Mbps** | 几乎不通，大镜像拉不动 |
| **github.com release 资产** | **0.00 Mbps** | 不通 |
| github.com 首页 / codeload | 0.37 Mbps | 极慢但可用 |
| **docker hub** | **0.00 Mbps** | 不通，且 `/etc/docker/daemon.json` 不存在（无镜像加速） |
| pypi | 0.19 Mbps | 很慢 |
| npm registry | 17.69 Mbps | 正常 |
| **git clone 本仓库（浅克隆）** | **7 秒完成** | 可用 |

其余已完成项（不再受此约束影响）：

- 服务器侧文件已就位：`compose.yaml`、`compose.server.yaml`、`deploy.sh`、`.env`(600)
- `.env` 中 `JWT_SECRET`(64 字符)/`POSTGRES_PASSWORD` 由**服务器本地生成**，未经过 AI、未进 GitHub
- 部署专用密钥已在 `authorized_keys` 中，且 CI 侧 Secret 指纹已用临时 workflow 验证通过
  （`SHA256:S/hiR65Izc3surRmZYzrQBSRq/ixEGHhYVs86L43boA`），该 workflow 用后已删除
- `deploy.sh` 的行为：先起 db 等 healthy → **迁移独立成步、失败即停** → 起 app 等 healthy →
  清理旧镜像（保留最近 N 个）→ **绝不覆盖 `.env`、绝不 `down -v`**

**可行方案（三选一，均已评估）**：

| 方案 | 做法 | 代价 | 我的建议 |
|---|---|---|---|
| **A. 配置 Docker 镜像加速** | 在 `/etc/docker/daemon.json` 加 `https://mirror.ccs.tencentyun.com`（已实测返回 200），重启 docker | **重启 docker 会短暂影响 9router/homepage/traefik**；且加速器只解决 Docker Hub，GHCR 未必受益 | 需你授权；能解决"从源码构建"路线 |
| **B. 服务器本地构建镜像** | 服务器 `git clone`（7 秒）后 `docker build` | 构建要拉 `node:22-alpine` 与 `python:3.12-slim`，而 Docker Hub 不通 → **仍被 A 阻塞** | 与 A 组合才可行 |
| **C. 走腾讯云容器镜像服务 TCR** | 服务器从 `ccr.ccs.tencentyun.com` 拉（内网快），CI 推过去 | 需要你在腾讯云开通 TCR（个人版免费）、创建命名空间与仓库、给出访问凭据；CI 侧要加一个 push 目标 | 长期最干净，但需要你开通 |

**不采用的方案**：从本机 `docker save` 后 scp 传镜像——每层 110 MB，跨境上行同样不可靠，
且每次发版都要经过我的机器，不适合作为 CI 路径。

### ✅ 阶段 1｜真实部署 —— **已完成（2026-09-12）**

**线上地址：https://quicklaunch.luty.tech（HTTP 200，Let's Encrypt 证书有效）**

最终链路（tag 驱动，已实测跑通）：

```
推送 v1.1.0
  ├─ versioning：解析版本 + 门禁（必须严格大于已发布最高版本）
  ├─ verify-backend / verify-web
  ├─ docker：构建镜像推 GHCR
  ├─ smoke-image：在 runner 上冒烟该镜像
  ├─ desktop：打包（版本取自 tag）
  ├─ deploy-server：SSH 同步文件 → 服务器构建并上线 → 公网冒烟 17/17
  ├─ release：发布 v1.1.0（含 latest.yml）
  └─ desktop-self-test：真跑打包产物 + 读真实更新源
```

实测证据（run [#34689001392](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34689001392)）：

| 项 | 结果 |
|---|---|
| Release | `v1.1.0`（非 draft），含安装包 + zip + blockmap + **latest.yml** |
| 服务器运行版本 | `quicklaunch:0970e6d`，**与 tag 指向的提交一致** |
| 容器 | app healthy、db healthy（数据卷未重建，数据保留） |
| 公网冒烟 | **17/17 通过**（含注册/登录/增删改查/越权隔离） |
| 其他服务 | 未受影响 |

#### 阶段 1 中遇到并解决的环境问题（都已实测定位）

| 现象 | 根因 | 处理 |
|---|---|---|
| `docker pull` 挂死 12 分钟 | 服务器到 **ghcr.io 只有 74 B/s**（`ghcr.io` 解析到境外 IP） | 改为**服务器本地构建**：源码从 GitHub clone 只要 7 秒 |
| 构建时 `pip install` 214 秒后失败 | 服务器到 **pypi.org 仅 0.52 Mbps** | Dockerfile 加 `ARG PIP_INDEX_URL`（默认官方源），服务器构建传阿里云镜像（实测 4.38 Mbps）；npm 官方源有 21.9 Mbps，无需替换 |
| 版本门禁拦掉了合法的 v1.1.0（两次） | ① 内联 shell 里的 `gh` CLI 静默失败；② `versioning` job 没 checkout 仓库 | 抽成 `scripts/check_version.py`（可在本地测试，已用真实发布列表验证） |
| 冒烟首次报 4.68 秒 | 首次请求含 DNS+TCP+TLS 冷启动；稳态 42-123 ms | 部署验收用 `--health-budget-ms 8000` |

#### 服务器侧现状

```
~/quicklaunch/
  ├── compose.yaml            # 由 CI 每次部署同步（版本化）
  ├── compose.server.yaml     # Traefik 接入覆盖文件
  ├── deploy.sh               # 部署脚本（CI 调用，也可人工执行）
  ├── .env                    # 600，密钥仅存在于此，CI 从不覆盖
  └── src/                    # 由 deploy.sh 拉取的源码（用于本地构建）
```

服务器上另有 `authorized_keys.bak.*` 两个备份，以及本次部署引用的 Docker 卷 `quicklaunch_db-data`。

**回滚**：`~/quicklaunch/deploy.sh <上一个 rev>`，或把 `.env` 的 `APP_IMAGE` 改回旧 tag 后
`docker compose -f compose.yaml -f compose.server.yaml up -d app`。

### 后续阶段 —— 全部完成

1. ✅ **阶段 1（续）**：服务器拉不动 GHCR/Docker Hub，改为**服务器本地构建**（源码浅克隆 7 秒 +
   构建期传 `PIP_INDEX_URL`）→ 部署上线 → 加 `deploy-server` job（tag 触发）+ tag 门禁 + 回滚演练。
2. ✅ **阶段 2｜支付后端**：模型 + 迁移（`0002_billing`）+ 网关抽象（真实 SDK / HTTP / 内存替身
   共用同一份验签）+ 4 个接口 + 单元/验签/幂等测试，全部离线可跑。
3. ✅ **阶段 3｜支付前端**：额度展示、升级按钮、客户门户入口、支付返回后的轮询兜底。
4. ✅ **阶段 4｜端到端**：`fake_stripe.py` 进 CI，每次构建都跑「下单 → 付款 → 签名 webhook →
   开通 → 第 11 条待办能建 → 取消 → 掉回 Free 且内容保留」（CI 实测 28/28）。
   真实 Checkout 等你给测试密钥（§10）。
5. ✅ **阶段 5｜文档**：README 与报告补上部署/支付章节与实测数据。

**过程中额外修掉的支付缺陷（都是 CI 真跑出来的，见报告 §1.1）**：事件形状少一层 `data.object`、
两边密钥字面量不一致、签名失败被返回 500（Stripe 会永远重投）。

---

## 10. 当前阻塞项与下一步

**已全部确认并落地**：域名、DNS、发版方式（tag 单发全套）、档位额度、部署目录、部署方式（SSH）、
订阅周期（月付）、部署密钥、`DEPLOY_*` 配置。

**剩余待办只有一项**：

| # | 待办 | 谁 | 说明 |
|---|---|---|---|
| 1 | Stripe：建 Product + 2 个 Price（Plus/Pro 月付），启用客户门户，给出 `price_*`、`sk_test_...`（或 `sk_live_...`），并注册 webhook 端点取得 `whsec_...` | 用户 | 填进服务器 `~/quicklaunch/.env` 后重启容器即生效，**代码不用改** |

未提供之前，线上 `/api/billing/*` 返回 503（被冒烟测试验证过的合法状态），页面显示
「本服务器未配置支付，额度上限仍然生效」而不是报错——免费档照常可用。
