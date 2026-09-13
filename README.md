# QuickLaunch

一个 FastAPI 后端 + 三端客户端（**网页端 / 桌面端 / 安卓端**），由一条 GitHub Actions
流水线完成构建、测试、容器化、发布与**上线部署**。

本仓库是一个**交付演示**：业务（待办清单）故意做到最小，让评审注意力全部落在管线上。
这里没有 mock——每个 job 都真实执行，验收证据就是运行历史。线上跑在
**https://quicklaunch.luty.tech**。

```
                        ┌─────────────────────────────────┐
   git push ──────────▶ │  .github/workflows/pipeline.yml  │
                        └────────────────┬────────────────┘
                                         │
         ┌───────────────────────────────┼────────────────────────────────┐
         │                               │                                │
    ┌────▼─────┐                  ┌──────▼──────┐                 ┌───────▼───────┐
    │ verify-  │                  │  verify-web │                 │   changes     │
    │ backend  │                  │ lint/types/ │                 │ 路径过滤 +    │
    │ +postgres│                  │ unit/build  │                 │ 发布开关      │
    └────┬─────┘                  └──────┬──────┘                 └───────────────┘
         │                               │
         └───────────┬───────────────────┘
                     │
            ┌────────▼───────┐      ┌──────────────────┐   ┌────────────────────┐
            │ docker: 构建    │      │ desktop: Electron│   │ android: Gradle    │
            │ + 推送 GHCR     │      │ Windows（默认）   │   │ 默认关闭，需开关    │
            │                │      │ Linux（可选）     │   │                    │
            └────────┬───────┘      └────────┬─────────┘   └─────────┬──────────┘
                     │                       │                       │
            ┌────────▼───────┐               │                       │
            │ smoke-image:   │               │                       │
            │ 真实起栈冒烟    │               │                       │
            │ + 回滚演练      │               │                       │
            └────────┬───────┘               │                       │
                     └───────────┬───────────┴───────────────────────┘
                                 │  仅 tag 触发以下两步
                       ┌─────────▼──────────┐   ┌────────────────────────┐
                       │ deploy: 服务器上线  │   │ release: 汇总本次产物   │
                       │ + 公网冒烟门禁      │   │ （桌面端更新源在此）     │
                       └────────────────────┘   └────────────────────────┘
```

## 发版：一切由 tag 驱动

推送分支**只验证，不发布、不部署**；只有推送 `v<semver>` 形式的 tag 才会发布产物并把新版本
部署到线上服务器。这样「合并代码」和「让用户拿到新版本」是两件分开的事。

| 触发方式 | 会发生什么 |
|---|---|
| `git push` 到任意分支 | 验证 + 构建镜像 + 在 runner 上起真实栈冒烟。不发布、不部署 |
| `git push origin v1.2.0` | 上面全部 + 打包客户端 + 发布 Release + 部署到线上 + 公网冒烟 |
| 到点自动发版（默认关闭） | 每天配置的时间（默认 02:00）自动判断"有新版可发吗"，有就自动发+部署 |
| 手动触发 | `version`/`draft` 可演练「有更新可用」；`deploy_ref` 只部署不发版；安卓/Linux 开关 |

**版本门禁**：要发布的版本必须严格大于已发布的最高版本（`scripts/check_version.py`），否则流水线在
`versioning` 这一步就停下。桌面端只接受比当前更高的版本，所以重复或回退的版本号没有意义。

### 定时自动发版（可配置，默认关闭）

```bash
gh variable set AUTO_RELEASE_ENABLED --body true      # 总开关
gh variable set AUTO_RELEASE_HOUR    --body 2         # 几点发（默认凌晨 2 点）
gh variable set AUTO_RELEASE_TZ      --body Asia/Shanghai
gh variable set AUTO_RELEASE_BUMP    --body patch     # patch / minor / major
```

四道门全过才会发：开关打开、到了配置的小时、main 上有新提交、**该提交的 CI 已经绿了**。
它派发的是同一条流水线（带版本号），所以自动发版与手动 tag 走完全相同的发布路径。
想先看它会不会发：`gh workflow run pipeline -f auto_release=true`（默认 dry run，不会真发）。

## 项目标识只写一处（迁移到新项目时）

域名、仓库名、镜像名、包名不再散落在 workflow / compose / 脚本 / `package.json` 里，
而是集中在 `project.env`；能读环境变量的地方从它派生，读不到的地方由 CI 每次校验一致性：

```bash
python scripts/project_env.py show      # 解析后的全部值
python scripts/project_env.py check     # 有没有漂移（CI 每次都跑）
python scripts/project_env.py bootstrap --repo owner/name --domain x.y --slug z --write
```

漏改的后果并不均等——漏改部署脚本里的仓库地址会直接失败（还算好），
漏改 electron-builder 的 `publish.owner` 会让**桌面端自动更新静默失效**。
所以 `check` 把这类不一致变成一次红色的 CI，而不是几个月后"用户怎么收不到更新"。
剩下必须人做的四件事，`bootstrap` 会在最后打印出来。

## 实际发布什么

**一次 Release 只有一个文件：安装包。** 更新源（`latest.yml`）不再作为 Release 附件发布，
它由应用自己提供（`GET /updates/latest.yml`）；网页端和 API 走容器镜像。
GitHub 还会自动附上两个源码包，那两项删不掉。

| 目标 | 产物 | 默认 | 由谁产出 |
|---|---|---|---|
| Windows 桌面端 | `QuickLaunch-Setup-<version>-x64.exe`（安装包；更新时下载的也是它） | **发布** | `desktop` job |
| 桌面端更新源 | 应用自己发的 `/updates/latest.yml`（**不是** Release 附件） | **发布** | `update-feed` job |
| 网页端 + API | `ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>`（一个镜像同时提供两者） | **发布** | `docker` job |
| Linux 桌面端 | `*.AppImage`、`*.deb` | 关闭 | `desktop` job |
| 安卓端 | `app-debug.apk`（可直接安装，debug 签名） | 关闭 | `android` job |

> **一次 tag = 全套验证 + 明确的产物清单。** 路径过滤器只用于分支推送省时间；打 tag 时它被显式
> 绕过（`changes` job 里的 `scope` 一步），否则一个只改文档的 tag 会「发布一个没跑过测试、
> 也没冒烟过镜像的版本」。产物则相反，用的是**白名单**而不是通配符：
> 新出现一个构建输出，不会自动变成用户的下载项。

### 如何打开可选目标

开关是**仓库变量**，改一次持续生效：

```bash
gh variable set PUBLISH_ANDROID --body true     # 构建并发布 APK
gh variable set PUBLISH_LINUX   --body true     # 构建并发布 AppImage/deb
gh variable set PUBLISH_ANDROID --body false    # 再关掉
```

只想对**某一次运行**生效、不动设置：

```bash
gh workflow run pipeline --ref main -f publish_android=true
```

运行摘要与 Release 说明都会写明这次发布了哪几端。
Linux 与安卓默认关闭，是因为本项目只服务 Windows 客户端和网页端：
每次推送都构建一个 APK，既花掉约 1 分钟 runner 时间，又往下载页塞一个没人装的产物。

## 桌面端自动更新

已安装的 Windows 客户端启动时会检查更新源，后台下载新安装包，用户点**一次**
「Restart and update」即完成升级。

**更新源由应用自己提供**：`GET /updates/latest.yml`。客户端里那个地址写在
`desktop/app-config.json`（`project_env.py check` 保证它与 `project.env` 的域名一致）。

**块文件（`.blockmap`）也由应用提供**，这不是随手一放：electron-updater 把块文件地址当成
「安装包地址 + `.blockmap`」，只有同源才能做**增量更新**（只下载变化的块，而不是每次都下 110 MB）。
所以 feed 里的安装包地址指向本域名下的 `/updates/…`，而那个地址**返回 302 跳到 GitHub 的
Release 附件**——安装包不必搬到服务器上（实测从 CI 往这台服务器传 110 MB 只有约 28 KB/s，
一次发版要一小时），客户端会带着 Range 请求跟着跳转，增量照样成立。

为什么更新源不放 Release 里：那样下载页就会多出 `latest.yml` 与 `blockmap` 两个没人会点的文件；
现在一次 Release 只有一个安装包，而客户端要的东西一样没少。发布流程是：
`desktop` job 打包 → `release` job 发布安装包 → `update-feed` job 把 feed 与块文件写到服务器，
并**在公网地址上逐项验证**：feed 版本号正确、安装包（经 302 跳转后）**支持 Range 请求**、
块文件存在且同样支持 Range。任何一项不过，这次发布就判失败。

代价：客户端更新的流量走 GitHub（不是你的服务器）；服务器只存 feed 与块文件（几百 KB 级）。

更新路径是**被验证过的**，不是假设：`desktop-self-test` 在 Windows runner 上真启动打包后的应用，
让它读**线上真实 feed**，并报告自己得出的结论。要专门触发「有更新」分支，用一个比线上更低的版本发布：

```bash
gh workflow run pipeline --ref main -f version=0.0.1 -f draft=true
```

这样会打包出一个比线上版本更旧的客户端（发成 draft，所以下载页不会出现它，也不会覆盖 feed），
自检必须报告 `update-available`。

**安装那一步是 CI 无法演练的**（它会在 runner 上装软件并重启），
所以值得在真机上点一次确认。产物未做代码签名，Windows 会弹一次 SmartScreen 提示。

## 线上部署（CD）

```
浏览器 ──▶ Traefik（服务器上已有，独占 80/443，Let's Encrypt 自动证书）
              └──▶ app:8000（FastAPI + 编译好的 SPA，一个容器）
                       └──▶ db:5432（postgres:16，named volume，数据不随部署重建）
```

tag 触发后，`deploy-server` job 通过 SSH 做三件事：同步 `compose*.yaml` 与 `deploy.sh`
→ 在服务器上执行 `deploy.sh <rev> 3` → 用 `scripts/smoke.py` 打**公网地址**验证。
最后一步才是门禁：部署只在公网真的能用时才算成功。

`deploy.sh` 的顺序是刻意设计的：

```
起 db → 等 healthy → 迁移（独立一步，失败即停）→ 起 app → 等 healthy → 清理旧镜像（保留最近 3 个）
```

**绝不覆盖 `.env`**（服务器密钥只存在于服务器），**绝不 `docker compose down -v`**（那会删数据）。
回滚是 `~/quicklaunch/deploy.sh <上一个 rev>`，或把 `.env` 里的 `APP_IMAGE` 改回旧 tag；
CI 每次部署都会**演练一遍回滚**，证明这条路真的通。

服务器的网络环境决定了镜像必须**在服务器上构建**，而不是从 registry 拉：

| 目标 | 服务器实测速度 | 结论 |
|---|---|---|
| ghcr.io | 74 B/s | 拉不动镜像 |
| Docker Hub | 0.00 Mbps | 不通（也就拉不到 `node`/`python` 基础镜像的加速） |
| pypi.org | 0.52 Mbps | 构建时 `pip install` 会超时 → 传 `PIP_INDEX_URL`（阿里云，实测 4.38 Mbps） |
| github.com（浅克隆源码） | 7 秒 | 可用，所以「服务器本地构建」成立 |
| github.com（`git fetch` 增量） | **会周期性被掐断** | 因此取源码有三次尝试：重试 fetch → 浅克隆 → codeload tarball |
| npm registry | 21.9 Mbps | 正常，无需换源 |

> 取源码那三次尝试不是过度设计：第一次真实部署就是死在
> `GnuTLS recv error (-110): The TLS connection was non-properly terminated`，
> 而那时 codeload 的 tarball 是通的。现在 `deploy.sh` 会把用的是哪一种打进日志（CI 日志可见）。

部署所需的仓库配置（都已就位，换仓库时需要重建）：

| 类型 | 名称 | 用途 |
|---|---|---|
| Secret | `DEPLOY_SSH_KEY` | 部署专用 Ed25519 私钥（服务器生成，未经过任何第三方） |
| Variable | `DEPLOY_HOST` / `DEPLOY_USER` | 服务器地址与登录用户 |

> 该密钥只被授权做部署这一件事：公钥在服务器 `authorized_keys` 里，且 CI 侧只用于
> `deploy.sh` 这一条命令。

## 订阅与额度（Stripe）

三档订阅，**只在额度上不同**——不做功能阉割，也就没有「付了钱还是不能用」的中间态：

| 档次 | 待办条数上限 | 说明 |
|---|---|---|
| Free | 10 | 默认档 |
| Plus | 200 | |
| Pro | 不限 | |

额度只在**创建**时检查；读、改、删永远不拦。所以降级（或取消订阅）之后，已经建好的内容
不会被隐藏，也不会被删掉，只是不能再往上加。

| 接口 | 作用 |
|---|---|
| `GET /api/billing/me` | 当前档位、用量、上限，以及本部署是否配置了支付 |
| `POST /api/billing/checkout` | 用 `{"plan": "plus"}` 换一个支付页地址 |
| `POST /api/billing/portal` | 打开 Stripe 客户门户（改卡、取消、看发票） |
| `POST /api/billing/sync` | 主动与支付方核对一次（webhook 迟到时的兜底按钮） |
| `POST /api/billing/webhook` | **唯一能授予档位的入口**（不出现在 OpenAPI 里） |

支付这件事上最容易出错的地方，按同样的顺序处理：

1. **验签**在授予任何东西之前完成，而且**拒绝未来时间戳**——签名里带着时间戳，不检查的话
   抓到一个请求就能把 `t` 改大无限重放（`stripe-python` 15.6.1 只拒太旧的，所以自己加了一道）。
2. **幂等**用事件 id 做主键（`billing_events` 表）：Stripe 会重投，重投不能重复授予。
3. **档位只从服务端价格表映射**：前端只发 `{"plan": "plus"}`，**从不发 price id**，
   否则改一个请求体就能用 1 元的价格买到 Pro。
4. `past_due` **保留**访问权限：卡过期不该立刻断服务，那是 Stripe 的重试窗口。

没配置 `STRIPE_*` 时，`/api/billing/*` 返回 **503**——明确失败，绝不静默假装成功；
冒烟测试会检查这一点（所以「不接支付」是一个被验证过的合法部署状态）。
需要的配置项：`STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET`、`STRIPE_PRICE_PLUS`、
`STRIPE_PRICE_PRO`，以及可选的 `STRIPE_API_BASE`（把请求指向别处，测试用）。

### 不用 Stripe 账号也能验证

`scripts/fake_stripe.py` 是一个**只用标准库**实现的 Stripe 替身，提供
`/v1/checkout/sessions`、`/v1/billing_portal/sessions`、`GET /v1/subscriptions/{id}`，
外加三个控制端点（`__control/pay`、`__control/cancel`、`__control/reset`）——因为真付款的是人。
它拒绝以 `--env production` 启动。

流水线每次构建都跑完整条支付链路：**下单 → 付款 → 投递已签名 webhook → 档位变成 Plus →
第 11 条待办建得出来 → 取消 → 掉回 Free 且内容还在**。本地同样可以跑：

```bash
# 1) 支付方替身（它和 API 读同一个 STRIPE_WEBHOOK_SECRET，别给成两个值）
STRIPE_WEBHOOK_SECRET=whsec_fake_secret python scripts/fake_stripe.py \
  --port 12194 --webhook-target http://127.0.0.1:8000/api/billing/webhook

# 2) API（见上面的本地运行，另外带上这几个变量）
#    STRIPE_SECRET_KEY=sk_test_fake  STRIPE_WEBHOOK_SECRET=whsec_fake_secret
#    STRIPE_PRICE_PLUS=price_fake_plus  STRIPE_PRICE_PRO=price_fake_pro
#    STRIPE_API_BASE=http://127.0.0.1:12194

# 3) 与部署门禁同一套检查
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

两边只要有一个字不一样，webhook 就会 400 `invalid_signature`——替身会直接把原因打在日志里。

## 为什么网页端和后端共用一个镜像

FastAPI 进程同时提供 `/api/*` 和编译好的 SPA。一个产物、一个端口、一个健康检查；
而且因为浏览器始终只与自己的源通信，**没有 CORS，也没有跨站 Cookie 问题**。
桌面端与安卓端复用同一份前端 bundle，所以 UI 只写一遍。

## 仓库结构

```
backend/      FastAPI 应用、alembic 迁移、pytest 测试
  app/          config、security（bcrypt+JWT）、deps、schemas、routers
  app/billing/  支付网关抽象（真实 Stripe / HTTP / 内存替身）、验签、幂等、档位映射
  migrations/   版本化 schema；`alembic upgrade head` 是流水线里独立的一步
  tests/unit/   纯逻辑，不连数据库（含真实 SDK 的验签测试）
  tests/integration/  真实 HTTP + 真实数据库（本地 sqlite，CI 里 postgres）
web/          Vite + React + TypeScript SPA（唯一的前端源码）
desktop/      Electron 外壳 + electron-builder 打包（含自动更新）
mobile/       Capacitor 配置；android/ 原生工程由 CI 生成，不入库
deploy/       compose.server.yaml（Traefik 接入）、deploy.sh（服务器侧部署脚本）
scripts/      smoke.py（部署门禁）、fake_stripe.py（支付方替身）、verify_apk.py、gh*.py
Dockerfile    多阶段：构建前端 -> 装 Python 依赖 -> slim 非 root 运行时
compose.yaml  db + 一次性迁移服务 + app
```

## 本地运行

```bash
# API + 网页端，用 sqlite，不需要 Docker
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt
cd web && npm ci && npm run build && cd ..
cd backend && DATABASE_URL=sqlite+aiosqlite:///./data/dev.db \
  JWT_SECRET=local-dev-secret-that-is-long-enough \
  WEB_DIST=../web/dist ../.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 然后用与流水线相同的方式验证它真的可用
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

完整栈（含 Postgres，与生产一致的跑法）：

```bash
cp .env.example .env      # 然后设置 JWT_SECRET
docker compose up -d --wait
```

想连支付链路一起验，再按上面「不用 Stripe 账号也能验证」起一个替身即可——
`smoke.py` 会自己识别出对面是替身还是真实 Stripe。

## 关键设计取舍

- **发版由 tag 决定，不由分支决定。** 分支推送只验证；`v1.2.0` 这样的 tag 才发布并部署。
  否则「合并一个 commit」就会惊动线上用户和桌面端自动更新。
- **迁移是流水线里独立的一步**，绝不写进应用启动命令。否则多副本同时启动会并发跑迁移。
- **CD 门禁是冒烟测试，不是健康检查。** `/api/health` 只能证明数据库连得上；
  `scripts/smoke.py` 会注册用户、建/读/改/删待办、确认登出真的让会话失效，
  在多租户间验证越权返回 404，并把支付链路（或「本部署没接支付」这件事）一并验掉。
- **租户隔离在 SQL 里强制**，而不是查出来再补一次归属校验：每条语句都带
  `user_id = 调用者`。访问他人资源返回 404 而非 403，不泄露资源是否存在。
- **档位只由验签通过的 webhook 授予**，而且价格表在服务端。前端能发的只有一个档位名。
- **失败即停，不自动回滚。** 自动回滚会掩盖问题；上一个 `sha-` tag 是人工回滚锚点，
  而且管线每次部署都演练一遍回滚。

## 验证

一条不能变红的管线不值得信任，所以推了三个故意弄坏的 PR，跑完即回滚：

| 注入的错误 | 被谁拦住 | 运行 |
|---|---|---|
| 网页端 `(): string => 42` | `verify-web` → typecheck | [#34652578016](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016) |
| 后端一个未使用的 import | `verify-backend` → lint | [#34652632864](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864) |
| 去掉待办接口的租户隔离 | `verify-backend` → 集成测试 | [#34652715044](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044) |

三次 PR 全部被拦住，下游 job（镜像、桌面端、安卓端、release）一个都没跑。

第三个最有意思：**45 个测试里仍有 41 个通过**，只有
`tests/integration/test_isolation.py` 里的 4 个跨租户用例发现了问题——
这就是它们单独成一个文件、单独命名的原因。

支付链路同样被流水线当场抓过两个 bug，都不是靠人看代码看出来的：

| 现象 | 根因 | 修法 |
|---|---|---|
| 冒烟里「webhook 授予了档位」一直失败，日志里既没报错也没授予 | 替身发的载荷少了一层 `data.object`，每个 webhook 都「到达但匹配不到用户」 | 与测试用的内存网关共用一份事件构造代码 |
| 同上，但容器日志里是 `SignatureVerificationError` | 替身和容器各自读了一个字面量密钥，两个都对、配在一起就错 | 两边都读同一个 `STRIPE_WEBHOOK_SECRET` |
| 签名不对时返回 500 | SDK 抛的 `SignatureVerificationError` 不是 `ValueError`，没被映射成 400 | 在共用的验签函数里统一转成 400（否则 Stripe 会永远重投） |

线上地址 **https://quicklaunch.luty.tech**（Traefik + Let's Encrypt），公网冒烟
**23/23**（未配置支付时的状态）/ **28/28**（配置了支付方）。

## 文档

- `DESIGN.md` —— 本项目最初的设计草案（v1，Next.js 时期；已被实现取代，保留以溯源）。
- `report/REPORT.md` —— 交付报告：做了什么、管线如何运作、实测耗时、真实运行证据、
  与 DESIGN.md 的逐条对照、已知限制、以及自动更新与发布开关的说明。
- `report/PLAN-deploy-and-stripe.md` —— 上线部署与订阅支付的实施方案（含决策记录与实测数据）。
- `report/AUTOMATION.md` —— 自动化边界：哪些环节已自动、哪些刻意留给人、为什么，
  以及定时发版与迁移新项目的操作方式。
- `report/PIPELINE-REVIEW.md` —— 配置与发版梳理：一次 Release 里每个文件是干什么的、
  哪些能砍（默认只保留安装包 + `latest.yml` + blockmap）、以及精简的三步方案。
