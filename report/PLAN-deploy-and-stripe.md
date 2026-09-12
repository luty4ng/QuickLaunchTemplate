# QuickLaunch 上线方案（部署到个人服务器 + Stripe 订阅支付）

> 状态：**待审核**。审核通过后再动手。本文不含任何密钥。
> 编写依据：对 `luty-server` 的实际只读探测 + 仓库当前代码（commit `9f2a338`）。

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
| 配置 | 4 vCPU / 3.6 GB 内存 / 40 GB 磁盘（已用 68%，剩 ~13 GB） | 本应用镜像约 110 MB，Postgres 数据量极小，**余量充足** |
| Docker | 27.5.1，Compose v2.32.4 | 无需安装任何东西 |
| 用户 | `ubuntu`，**属于 docker 组**（可直接跑 docker），`sudo -n` 免密可用 | 部署不需要 sudo |
| 现有容器 | `traefik:v3.2`（占 80/443）、`homepage`（5005）、`9router`（20128） | **80/443 已被占用，不能自己监听** |
| Docker 网络 | 存在 `traefik-net` | 应用加入该网络，由 Traefik 反代 |
| Traefik 配置 | `exposedByDefault: false`，network `traefik-net`，file provider 读 `~/traefik/dynamic/dynamic.yml` | **只有显式打 label 的容器才会被暴露**，符合我的需求 |
| TLS | Let's Encrypt，`tlsChallenge`，证书存 `~/traefik/acme.json` | 新子域名自动签发证书，无需手动运维 |
| 域名 | `luty.tech` → 122.152.219.10（`luty.tech` 走 homepage，`router.luty.tech` 走 9router） | 见下方「需要你做的事」 |
| DNS | **`quicklaunch.luty.tech` 目前无解析** | **必须先加 A 记录**，否则 Let's Encrypt 签不出证书 |
| 防火墙 | `ufw` inactive；22 端口可外部 SSH | 入站由云厂商安全组控制 |
| 已有的部署模式 | `~/9router-webhook.py` + systemd `9router-webhook.service`，端口 9876，HMAC 验签 | **当前 inactive，且 9876 未监听** |
| `~/.ssh/authorized_keys` | 1 条公钥 | 部署用的新公钥需要追加进去（我会先给你公钥，由你决定是否加） |

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
push to main
   │
   ├─ verify-backend / verify-web（不变）
   ├─ docker: 构建并推送 ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>
   ├─ deploy-local: 在 runner 上起栈 + 冒烟（不变，仍然是最快的门禁）
   │
   └─ deploy-server（新增，需要 manual approval 或直接自动？见 §7 待确认）
         ├─ SSH 到 luty-server（新密钥，只用于部署）
         ├─ docker compose pull app migrate
         ├─ docker compose up -d --wait db / migrate / app
         ├─ 等容器 healthy
         └─ 从公网 HTTPS 地址跑一次冒烟：python scripts/smoke.py --base-url https://quicklaunch.luty.tech
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

1. **加 DNS A 记录**：`quicklaunch.luty.tech` → `122.152.219.10`（没有它签不出证书）
2. **决定子域名**：默认用 `quicklaunch.luty.tech`，你想换名字就告诉我
3. **Stripe 侧配置**：
   - 建测试账号，保持 **Test mode**
   - 建 1 个 Product + 1 个 Price（建议先只做月付），把 `price_...` 给我
   - 部署后再注册 webhook 端点 `https://quicklaunch.luty.tech/api/billing/webhook`，
     把 `whsec_...` 给我（**注意：不能提前注册，因为域名还没解析**）
4. **提供 Stripe 测试密钥**：`sk_test_...`（不给我也能全部实现 + 用桩验证，只是真实 Checkout 那一下要你点）

### 7.2 需要你确认的决策点

| # | 问题 | 我的建议 |
|---|---|---|
| 1 | 部署是**自动**（main 绿灯即上）还是**手动触发**（workflow_dispatch）？ | **先手动**。上线初期手动更安全，稳定后改自动（改一行 if） |
| 2 | 是否允许我给你一个**新的 SSH 公钥**，由你追加到服务器 `authorized_keys`？ | 是。私钥只进 GitHub Secret；撤销只需删掉那一行 |
| 3 | 订阅周期 | **先只做月付**，年付以后加一个 `price_id` 即可 |
| 4 | 免费额度 10 条 | 认可就用 10 |
| 5 | 部署目录 `~/quicklaunch/` | 认可就用这个路径 |
| 6 | 部署时是否允许**短暂中断**（重建容器几秒）？ | 单副本无法真正零停机；Demo 阶段建议接受。要零停机就得双副本 + 滚动更新，复杂度不值当 |

---

## 8. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 证书签发失败 | 域名不通 | 先只加 DNS + 一个最小容器验证 Traefik 能签发，**再**做支付 |
| Stripe 事件字段与预期不符 | 订阅状态写错 | 拿到密钥后先用真实 API 调一次并打印结构，**按实际字段写代码** |
| webhook 未到达（网络/配置） | 用户付了钱没解锁 | 提供 `/api/billing/sync` 兜底：用户点「我已完成支付」时主动向 Stripe 查一次真实订阅状态 |
| 服务器磁盘 | 构建失败 | 只 pull 镜像不在服务器构建；`docker image prune` 由部署脚本按需清理**本项目**的旧 tag |
| 误改到其他项目 | **严重** | 所有操作限定在 `~/quicklaunch/`；不动 `9router`/`traefik`/`homepage`；每次改动前先 `ls` 确认路径 |
| 密钥泄漏进日志 | **严重** | 部署脚本不打印 `.env`；`set -x` 避开密钥行；仓库加 secret 扫描检查 |

---

## 9. 实施顺序（审核通过后）

分阶段，每阶段结束都可验证、可停下来：

1. **阶段 0｜连通性**：加 DNS → 部署一个占位容器 → 确认 Traefik 签发证书、域名返回 200。
   *这一步不碰支付，先证明网络链路通。*
2. **阶段 1｜真实部署**：`deploy-server` job + 服务器目录 + `.env` + 公网冒烟。跑通后 main 绿灯即可上线。
3. **阶段 2｜支付后端**：模型 + 迁移 + 网关抽象 + 4 个接口 + 单元/验签/幂等测试（全部离线可跑）。
4. **阶段 3｜支付前端**：额度展示、升级按钮、门户入口、支付返回轮询。
5. **阶段 4｜端到端**：`fake_stripe.py` 进 CI；拿到你的测试密钥后走一次真实 Checkout + 真实 webhook。
6. **阶段 5｜文档**：README 与报告补部署/支付章节，记录实测数据。

---

## 10. 待确认清单（回我这几项即可开工）

- [ ] 域名：沿用 `quicklaunch.luty.tech`，还是换一个？
- [ ] DNS A 记录已加 / 由我提示你加
- [ ] 部署触发方式：先手动（建议）还是直接自动
- [ ] 是否同意我用新 SSH 公钥部署（我会先把公钥给你看）
- [ ] Stripe：`price_id` + 是否提供 `sk_test_...`（给了就能验真实 API，不给就先用桩）
- [ ] 订阅周期：月付（建议）还是月付+年付
- [ ] 免费额度：10 条
