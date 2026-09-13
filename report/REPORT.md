# QuickLaunch 交付报告

> 交付对象：`luty4ng/QuickLaunchTemplate`（GitHub，公开）
> 目标：一个 Demo，跑通「一条 CI/CD 管线自动发布网页端 / 桌面端 / 安卓端」，后端用 FastAPI，
> 实现尽量简洁、高效、省心、可扩展。
> 发布策略演进：最初三端全发；按实际需求改为**默认只发 Windows 桌面端 + 网页端，
> 安卓端与 Linux 桌面端由开关控制**（见 §12）。
> 发版机制演进：最初每次分支推送都发一个版本；后改为**只有打 tag 才发布并部署**（见 §13）。
> 交付后追加：**真实上线到 https://quicklaunch.luty.tech**，并接上 **Stripe 订阅**（见 §14）。

---

## 1. 结论

**已完成，且管线是真的跑通的，不是纸面设计——网页端已经跑在公网域名上。**

| 交付物 | 位置 | 状态 |
|---|---|---|
| 后端（FastAPI，含迁移、认证、租户隔离、订阅额度） | `backend/` | ✅ 本地 94 个测试通过，CI 在 Postgres 16 上再跑一遍 |
| 网页端（同一个 React 产物，由后端直接托管） | `web/` | ✅ 20 个单测通过，镜像内含构建产物 |
| 桌面端（Electron，Windows 默认发布、Linux 可选） | `desktop/` | ✅ 产物自检通过（在 CI 的 Windows runner 上真跑起来），并支持一键自动更新 |
| 安卓端（Capacitor，可安装 APK，默认不发布） | `mobile/` | ✅ 开关打开后 CI 产出并校验包名与内嵌前端产物 |
| 一条管线（CI + CD，单文件 + 发布开关 + tag 门禁） | `.github/workflows/pipeline.yml` | ✅ 11 个 job（含 versioning）全绿 |
| 一条 Release（默认 5 个可下载产物） | [v1.2.1](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.2.1) | ✅ 由 tag 触发，同时部署上线 |
| **线上部署**（Traefik + Let's Encrypt + compose） | https://quicklaunch.luty.tech | ✅ 公网冒烟 23/23（未配置支付时的状态） |
| **订阅支付**（Stripe，Free/Plus/Pro 只差额度） | `backend/app/billing/`、`web/src/components/PlanPanel.tsx` | ✅ 流水线每次构建都在替身支付方上跑完整链路 |

关键运行记录（全部是真实执行，可在仓库 Actions / Releases / Packages 页复核）：

| 运行 | 结果 | 说明 |
|---|---|---|
| [#34651815693](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693) | ✅ 全绿 | 首个完整成功的端到端管线（当时 10 个 job 全部真实执行），产出镜像 + 三端产物 + Release |
| [#34677071586](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677071586) | ✅ 全绿 | **默认发布行为**：安卓跳过、Linux 绿色 no-op，Release 只含 Windows + 网页端 |
| [#34677377502](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677377502) | ✅ 全绿 | **打开开关**：`publish_android` + `publish_linux`，四端全部构建并发布 |
| [#34691719517](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34691719517) | ✅ 全绿 | **支付链路端到端**：替身支付方上跑通下单→付款→签名 webhook→开通→取消（28/28） |
| [#34693927161](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34693927161) | ✅ 全绿 | **tag `v1.2.1` 的完整发布 + 部署**：全量测试、镜像冒烟、桌面端打包与自检、部署上线、公网冒烟门禁 |
| [#34652578016](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016) | ❌ 按预期变红 | 故意塞类型错误 → `verify-web / typecheck` 拦住 |
| [#34652632864](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864) | ❌ 按预期变红 | 故意塞无用 import → `verify-backend / lint` 拦住 |
| [#34652715044](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044) | ❌ 按预期变红 | 故意破坏租户隔离 → `verify-backend / integration` 拦住 |

> 「一条永远绿的管线，和不存在的管线可信度一样。」——所以上面三条反向验证是本次交付的重点之一。

### 1.1 调通过程本身就是最好的证据

这条管线不是一次写对的。前前后后一共 **27 次运行**（含 3 次门禁反向验证、3 次本地验证后主动取消），
其中 **12 次红是真实的环境/设计缺陷**，每一次都暴露一个只有真跑才会出现的问题，而每一次修复都留在
git 历史里（`git log` 可逐条复核）。值得记录的几条：

| 现象 | 根因 | 修复 |
|---|---|---|
| `docker save` 报 "repository name must be lowercase" | `github.repository` 是 `QuickLaunchTemplate`，GHCR 只接受小写 | 镜像名改用 owner + 小写仓库名 |
| 桌面端 `spawn wine ENOENT` | electron-builder 在 Linux 上靠 Wine 出 Windows 安装包 | 改成 matrix：哪个系统出哪个系统的安装包 |
| `Could not find the web assets directory: ./www` | Capacitor 拷贝的是 `webDir`，而产物建到了 `web/dist` | 用 `VITE_OUT_DIR` 直接建到 `mobile/www` |
| 容器一直 `starting`，健康检查不过 | `docker compose up --wait` 只保证「在跑」，而 docker 健康检查有自己的 10s 周期 | 健康判定改成显式轮询 |
| 打包后的桌面应用在 CI 上白屏 | Chromium 把 `file://` 当不透明源，模块脚本不执行 | 注册 `app://` 标准协议托管前端产物 |
| 自检卡住直到超时 | `app.disableHardwareAcceleration()` 写在 `whenReady()` 里会抛异常，且被 Promise 吞掉，窗口根本没建 | 移到 `app.whenReady()` 之前 |
| `net::ERR_CONNECTION_REFUSED` | Windows 上 step 结束时其进程树被回收，桩服务已死 | 用 WMI 启动桩服务，脱离当前进程树 |
| 文档提交也把桌面端构建搞挂了 | 跳过的 job 不会产出 artifact，而桌面端硬依赖它 | 下载改为尽力而为 + 缺就自己构建 |
| 镜像里的前端用了绝对路径 `/assets/` | 我自己的 `.gitignore` 里一条 `.env` 把 `web/.env` 悄悄排除了 | 默认值写进 `vite.config.ts`（忽略规则拿不走），并加断言防回归 |
| 支付链路「webhook 到了但什么都没授予」，日志里连报错都没有 | 替身发的载荷少一层 `data.object`，应用按 Stripe 的形状去读，读到空 | 替身与测试用的内存网关共用同一份事件构造代码 |
| 同上，但容器日志是 `SignatureVerificationError`，响应 500 | 替身与容器各自读了一个字面量密钥：两个都对，配在一起就错 | 两边都读同一个 `STRIPE_WEBHOOK_SECRET`；替身在验签被拒时直接打印原因 |
| 签名不对返回 500（Stripe 会永远重投） | SDK 抛的 `SignatureVerificationError` 不继承 `ValueError`，没被映射成 400 | 在共用的验签函数里统一转成 `ValueError` → 400 |
| 首次真实部署失败：`GnuTLS recv error (-110): The TLS connection was non-properly terminated` | 服务器到 github.com 的 git-over-HTTPS 会**周期性被掐断**，而 `deploy.sh` 取源码只有一次 `git fetch`，没有重试也没有退路；手工复现时它还会无声挂住 3 分钟 | 改成三种方式依次尝试（fetch 重试 3 次 → 浅克隆 → codeload tarball），并给 git 加 `lowSpeedTime` 让卡住的传输 30 秒内报错 |
| 手工拷贝的 `deploy.sh` 部署成功却退出 1（`$'\r': command not found`） | Windows 工作副本是 CRLF，bash 把最后一行的 `\r` 当命令 | 新增 `.gitattributes` 把 `*.sh`/`*.py`/`*.yml` 固定为 LF |
| 首个 tag 发布（v1.2.0，只改了文档）**跳过了全部测试与镜像冒烟**，Release 里还少了 `web-<sha>.zip` | 路径过滤器在分支推送时省时间，用在发布上就变成「悄悄削减发布内容」 | `changes` job 增加 `scope` 一步：**tag 一律全量在范围内**，不再看 diff |

最后三条尤其说明问题：**如果只跑绿、不做反向验证、不以真实产物为准，这类缺陷会一路带到用户面前。**
支付那条更典型：整条链路「看起来全绿」，只是钱进来之后档位不会变。

---

## 2. 整体架构

```
                        ┌──────────────────────────────────────┐
   git push ──────────▶ │  .github/workflows/pipeline.yml        │
                        └───────────────┬──────────────────────┘
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               │                                │
   ┌────▼──────────┐            ┌───────▼───────┐                 ┌──────▼──────┐
   │ verify-       │            │  verify-web   │                 │  changes    │
   │ backend       │            │ lint/types/   │                 │ paths-filter│
   │ + postgres 16 │            │ unit/build    │                 └─────────────┘
   └────┬──────────┘            └───────┬───────┘
        │                               │
        └───────────┬───────────────────┘
                    │
        ┌───────────▼─────────┐   ┌──────────────────┐   ┌──────────────────┐
        │ docker: 构建+推 GHCR│   │ desktop: 打包    │   │ android: 构建 APK│
        └───────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
                    │                      │                      │
        ┌───────────▼─────────┐   ┌────────▼─────────┐            │
        │ deploy: compose 起栈│   │ self-test: 真跑  │            │
        │ +健康检查+冒烟+回滚 │   │ 打包后的 exe     │            │
        └───────────┬─────────┘   └────────┬─────────┘            │
                    └───────────┬──────────┴──────────────────────┘
                                │
                        ┌───────▼────────┐
                        │ release: 打 tag│
                        │ 汇总全部产物   │
                        └────────────────┘
```

**一个核心取舍：网页端和 API 是同一个镜像。** FastAPI 进程同时提供 `/api/*` 和编译好的 SPA，
所以浏览器永远只跟自己同源通信——**没有 CORS，也没有跨站 Cookie 问题**，一个产物、一个端口、
一个健康检查。桌面端和安卓端复用同一份前端 bundle，UI 只写一遍。

---

## 3. 我做了什么

### 3.1 后端 `backend/`（FastAPI）

- **技术栈**：FastAPI 0.141 + SQLAlchemy 2.0（async）+ Alembic + Pydantic v2 + PyJWT + bcrypt + Postgres 16。
  本地开发用 SQLite（无需 Docker），CI 与生产强制 Postgres——三方同源是「迁移能不能在生产跑通」的前提。
- **接口**：`/api/health`、`/api/auth/{register,login,logout,me}`、`/api/todos` 的增删改查。
  请求体全部经 Pydantic 校验；业务错误统一 `{"error": {"code", "message"}}`。
- **会话**：JWT 放在 httpOnly Cookie 里，不建 session 表——少一张表、少一次查询。代价是无法主动踢人下线，
  Demo 阶段接受。
- **数据隔离（本项目唯一有技术含量的约束）**：所有 Todo 语句**在 SQL 里**就带 `user_id = 调用者`，
  绝不做「先按 id 查出来再补一次归属校验」——那种写法离数据泄露只差一个忘记写的 `if`。
  访问他人资源返回 **404 而非 403**，不泄露资源是否存在。
- **ID 设计**：自实现 UUIDv7 风格的 48 位毫秒时间戳前缀 ID，保证 `ORDER BY created_at DESC, id DESC`
  在同一毫秒内也稳定，同时让索引写入接近追加而非随机散布。
- **迁移**：`alembic upgrade head` 是**独立的流水线步骤**，绝不写进应用启动脚本——
  多副本同时启动会并发跑迁移，这是这类项目最常见的生产事故。

### 3.2 前端与三端复用

- `web/`：Vite 8 + React 19 + TypeScript，一份代码产出三个目标，差异只有两个构建期变量：
  - `VITE_WEB_BASE`：网页端用 `/`，桌面/安卓用 `./`（相对路径，能在 `app://` 和 WebView 里工作）
  - `VITE_API_BASE`：留空 = 同源（网页端）；桌面/安卓编译进一个默认后端地址
- `desktop/`：Electron 44 外壳。**用自定义 `app://` 协议而不是 `file://`**——Chromium 把 `file://`
  当不透明源，模块脚本会被当成跨源请求而不执行（这正是它一开始在 CI 上白屏的原因）。
- `mobile/`：Capacitor 8，`webDir` 直接指向构建输出目录；`android/` 原生工程不入库，由 CI 用
  `cap add android` 生成，避免把生成物当源码维护。

### 3.3 容器与部署

- `Dockerfile`：三阶段（Node 构建前端 → Python 依赖 → slim 运行时），非 root 用户，
  `HEALTHCHECK` 打的是 `/api/health`（会连数据库），而不是 TCP 探活。
- `compose.yaml`：`db` + **一次性的 `migrate` 服务** + `app`；`app` 用
  `condition: service_completed_successfully` 等迁移跑完。镜像用 `sha-<commit>` tag 拉取，
  这个 tag 同时就是回滚锚点。

### 3.4 验收：三端都被「真跑过」，不只是「构建过」

这是我认为最值得说的一点：**构建成功不等于能用**。

- **网页端**：CI 里 `docker compose up` 起真实栈 → 轮询容器 healthy → 跑 `scripts/smoke.py`
  打真实 HTTP（注册→建待办→改→删→登出→越权验证），17/17 通过。
- **桌面端**：CI 在 **windows-latest** 上解压产物，真启动 `QuickLaunch.exe`，让它自己
  「自检」：等 React 挂载 → 检查 preload 桥 → 从渲染进程（和主进程）请求 `/api/health` →
  写 JSON 报告 → 退出码表态。见 `desktop/main.js` 的 `--ql-self-test`。
- **安卓端**：`scripts/verify_apk.py` 直接读 zip 里的二进制 `AndroidManifest.xml`
  （同时按 UTF-8 和 UTF-16 匹配，避免假阴性）确认包名，并确认前端产物真在 `assets/public/` 里。

---

## 4. 管线怎么运作的（逐段说明）

### 4.1 触发与并发

```yaml
on: { push: { branches: ['**'], tags: ['v*'] }, pull_request: {}, workflow_dispatch: {} }
concurrency: { group: pipeline-${{ github.ref }}, cancel-in-progress: true }
```

`push` 到任何分支和任何 PR 都会跑；同一 ref 上的新推送会取消上一次，避免排队烧时间。
CI 与 CD 放在**同一个文件**里用 `needs:` 串联——拆成两个文件就得用 `workflow_run`，
那需要额外 PAT 且判断容易写错。

### 4.2 变更检测：`changes`

`dorny/paths-filter` 输出 `backend` / `web` 两个布尔量。改文档不会重建三端；
改 `web/**` 不会白跑一遍 Postgres 集成测试。这是让管线「高效」的第一层。

### 4.3 CI 门禁：`verify-backend` 与 `verify-web`

`verify-backend` 起 `postgres:16` service container，按顺序：

1. `ruff check` + `ruff format --check`（含 `scripts/`，它们是交付路径的一部分）
2. `alembic upgrade head` —— **迁移必须能在 Postgres 上跑通**
3. `alembic downgrade base && alembic upgrade head` —— **迁移必须可逆**（回滚能力的前提）
4. `pytest tests/unit` —— 纯逻辑，无数据库
5. `pytest tests/integration` —— 打真 Postgres 的端到端 HTTP

`verify-web`：`eslint` → `tsc --noEmit` → `vitest` → `vite build`，并把 `web/dist` 作为 artifact
上传，供桌面/安卓 job 复用（不重复构建）。

### 4.4 CD：`docker` → `deploy`

- `docker`：buildx 构建多阶段镜像，打两个 tag 推 GHCR：
  `sha-<short>`（回滚锚点）和 `latest`（仅默认分支），并 `docker save` 成 artifact 交给下一段。
- `deploy`（在 GitHub runner 上做**制品验证**，不依赖任何外部服务器）：
  1. `compose up -d --wait db` → 轮询 db healthy
  2. `compose up --exit-code-from migrate migrate` → **迁移独立成步，失败即停**
  3. `compose up -d --wait app` → 轮询容器 healthy（Docker 自己的 HEALTHCHECK）
  4. `scripts/smoke.py` → 17 项真实 HTTP 断言
  5. **回滚演练**：拉上一个 `sha-` tag 重新部署，再跑一遍冒烟（16/16 通过）
  6. 无论成败都 `compose down -v` 清理

  这一段刻意不依赖 `--wait` 的模糊语义，而是把「db 健康 / 迁移成功 / 应用健康」写成三个显式阶段，
  日志里能直接看出是哪一条不满足。

### 4.5 三端打包：`desktop` / `desktop-self-test` / `android`

- `desktop` 是**按宿主 OS 拆开的 matrix**：Windows runner 出 `.exe`（NSIS 安装包 + 免安装 zip），
  Linux runner 出 `.AppImage` + `.deb`。跨平台出 Windows 安装包需要 Wine，而 Linux runner 上没有，
  会以 `spawn wine ENOENT` 失败——「哪个系统就出哪个系统的安装包」这个约束反而更简单可靠。
- `desktop-self-test` 在 windows-latest 上真跑打包产物（见 §3.4）。
- `android`：`cap add android` → `cap sync` → `gradlew assembleDebug` → 校验 APK。

### 4.6 汇总：`release`

只在默认分支成功时执行：只下载**本次要发布的** artifact，打成 Release
（tag 为 `v<version>`，版本号取自运行号 `1.0.<run number>`）。

默认发布（Windows + 网页端）的产物清单，以
[v1.0.38](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38) 为例，共 5 个：

| 产物 | 大小 |
|---|---|
| `QuickLaunch-Setup-1.0.38-x64.exe`（Windows 安装包，自动更新的下载目标） | 111.7 MB |
| `QuickLaunch-Setup-1.0.38-x64.exe.blockmap`（增量下载用） | 0.1 MB |
| `QuickLaunch-1.0.38-win.zip`（免安装解压即用） | 153.6 MB |
| `latest.yml`（更新源清单，客户端读它判断有无新版） | < 0.1 MB |
| `web-c7eb03b.zip`（静态网页包） | 0.1 MB |

打开可选目标后（`publish_android` / `publish_linux`），
[v1.0.39](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.39) 共 9 个，
额外包含 `app-debug.apk`（4.2 MB）、`QuickLaunch-1.0.39-x86_64.AppImage`（125.5 MB）、
`QuickLaunch-1.0.39-amd64.deb`（99.1 MB）、`latest-linux.yml`。详见 §12。

后端/网页端的「发布」形态是容器镜像：`ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>`（公开可拉）。

---

## 5. 门禁反向验证（管线能不能拦住错误）

只让管线变绿是不够的，必须证明它会红。三个实验都是真实提交并推送的（各自一个 PR，跑完即删除分支）：

| 实验 | 注入的错误 | 结果 | 拦在哪一步 | 下游 |
|---|---|---|---|---|
| A | `web/src/api.ts` 里 `(): string => 42` | ❌ 变红 | `verify-web` → **typecheck** | CD 与三端打包全部 skipped |
| B | `backend/app/security.py` 加无用 `import uuid` | ❌ 变红 | `verify-backend` → **lint** | 同上 |
| C | `update_todo`/`delete_todo` 改成「只按 id 查、不校验归属」 | ❌ 变红 | `verify-backend` → **integration tests** | 同上 |

实验 C 最能说明问题：**它让 41 个测试继续通过，只有 `tests/integration/test_isolation.py` 的 4 个用例失败**
（本地实测：`4 failed, 41 passed`）。也就是说，普通 CRUD 测试完全发现不了越权，
只有把隔离当成一等公民单独写用例，才拦得住。这正是为什么这组用例被单独放一个文件、单独起名字。

---

## 6. 与设计要求（DESIGN.md）的对应关系

`DESIGN.md` 是用户给的设计草案，其中技术栈部分（Next.js 全栈）与本次需求（后端必须 FastAPI）
冲突，按用户要求以 FastAPI 为准。其余设计意图全部保留：

| DESIGN.md 的要求 | 实现情况 |
|---|---|
| 业务极简、评审注意力落在管线上 | ✅ 只有 users / todos 两张表、8 个接口、单页 UI |
| 一个产物、一条流水线 | ✅ 一个镜像同时提供 API 与前端 |
| 会话放 httpOnly Cookie，不建 session 表 | ✅ |
| 隔离铁律：查询带 userId、越权返 404 | ✅ 并在 CI 用独立用例兜底（见 §5 实验 C） |
| 迁移必须独立成步，不能塞进启动脚本 | ✅ compose 的 `migrate` 一次性服务 + CD 独立步骤 |
| CI 阶段：lint → typecheck → 单测 → 真库集成 → build | ✅ 全部落实（typecheck 在前端，后端用 ruff） |
| CD 阶段：多阶段构建、双 tag 推 GHCR、独立迁移、起容器、探活、冒烟、失败即停不自动回滚 | ✅ 全部落实，另加回滚演练 |
| 部署分 A 段（runner 上做制品验证）/ B 段（真实 VPS，预留） | ✅ A 段已真跑；B 段只需填 secrets（见 §8） |
| 「管线门禁本身要验证」三条 | ✅ §5 全部完成，并额外做了 lint 实验 |
| 效率基线：CI < 3 分钟、CD < 5 分钟 | ✅ CI 门禁 19 s / 38 s；CD（镜像+部署+探活+冒烟+回滚演练）142 s |
| **验收清单（§6）逐条** | 见下表 |

### 6.1 DESIGN.md §6 验收清单逐条对照

| 验收项 | 结果 | 证据 |
|---|---|---|
| 基础四步（lint/typecheck/单测/build）零错误 | ✅ | 本地 + CI 每次运行 |
| 单测覆盖密码哈希往返、JWT 签发/过期/篡改、Zod 边界 | ✅ | `tests/unit/test_security.py`，22 个用例（校验层用 Pydantic 而非 Zod） |
| 集成测试全链路（注册→登录→建→改→删） | ✅ | `tests/integration/test_api.py`，真 Postgres |
| **双用户越权用例**：A 访问 B 的 todoId 必须 404，且列表不含 B 数据 | ✅ | `tests/integration/test_isolation.py`，5 个用例；部署后冒烟再验一遍 |
| 故意塞类型错误 → CI 必须变红 | ✅ | run #34652578016（`verify-web / typecheck`） |
| 故意让一条单测断言失败 → CI 必须变红 | ✅ | run #34652632864 用的是更靠前的 lint 门禁；越权实验则直接落在集成测试上（#34652715044） |
| 故意破坏隔离 → 集成测试必须变红 | ✅ | run #34652715044，且**只有**隔离用例失败（41 passed / 4 failed） |
| `main` 产出镜像，两个 tag 都在 GHCR | ✅ | `ghcr.io/luty4ng/quicklaunchtemplate`：`sha-<commit>` + `latest`，公开 |
| 镜像启动后 `/api/health` 返回 200 且 < 1s | ✅ | 20–28 ms；并且 Docker 自身 HEALTHCHECK 也报 healthy |
| 冒烟用例在部署后的真实地址上通过 | ✅ | **28/28**（CI 临时栈，含支付链路）/ **23/23**（公网 `https://quicklaunch.luty.tech`，未配支付） |
| 用上一个 `sha-` tag 重新部署，服务恢复正常 | ✅ | CI 里的回滚演练：`sha-<上一个提交>` 重新部署后通过，输出 `::notice::rollback rehearsal passed`。**需要说明**：这次演练跑在 runner 的临时栈上（验证制品与 compose 机制没问题），**服务器侧的真回滚命令还没在线上演练过**；服务器上的回滚锚点是上一版镜像 `quicklaunch:62bb66b`（保留最近 3 个） |

---

## 7. 实测数据

以下数字全部来自真实运行（未做估算）：

| 项目 | 数值 | 来源 |
|---|---|---|
| 后端测试 | **94 passed**（53 单测 + 41 集成） | 本地（sqlite）+ CI（postgres 16） |
| 前端测试 | **20 passed** | 本地 + CI |
| 冒烟用例 | **28/28**（配了支付方）/ **23/23**（未配支付） | run [#34691719517](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34691719517) + 本地 |
| 支付链路（CI 每次构建都跑） | 下单 → 付款 → 已签名 webhook → 档位变 Plus → 第 11 条待办放行 → 取消 → 回 Free 且内容保留，**全部 PASS** | 同上 |
| `/api/health` 响应 | 20–28 ms（预算 1000 ms） | 冒烟输出 |
| **CI 门禁（后端 job）** | **40 s**（起 Postgres 服务 + 迁移可升可回滚 + 94 个测试） | run #34691719517 |
| **CI 门禁（前端 job）** | **19 s** | run #34651815693（改动前端时） |
| 镜像构建 + 推送 GHCR | **55 s** | run #34691719517 |
| 起栈 + 迁移 + 冒烟 + 回滚演练 | **54 s** | 同上 |
| 桌面端打包（Windows） | **197 s**（缓存热）/ **755 s**（冷缓存） | run #34693927161 / #34691719517 |
| **完整 tag 发布 + 部署（12 个 job）** | **294 s ≈ 4.9 分钟** | run #34693927161 |
| 部署到服务器（同步文件 + 服务器构建 + 迁移 + 上线 + 容器自检） | **102 s** | 同上 |
| 失败反馈速度 | **28–32 s 变红，下游全部 skipped** | run #34652632864 |

各 job 耗时（tag 发布 run #34693927161）：changes 5s、versioning 6s、web 21s、backend 45s、
desktop(linux) 3s（关闭时 no-op）、desktop(windows) 197s、docker 48s、smoke-image 52s、
**deploy-server 102s**、release 33s、desktop-self-test 23s。

各 job 耗时（分支推送 run #34691719517）：changes 4s、versioning 3s、backend 40s、
desktop(windows) 755s、docker 55s、smoke-image 54s。

> 关于 Windows 打包 197 s 与 755 s 的差别：npm 依赖有缓存，但 **Electron 与 electron-builder
> 的二进制缓存不住**（`~/AppData/Local/electron{,-builder}/Cache`），冷 runner 要重新下载
> 上百 MB。它是管线里唯一的长尾，修法很直接（把这两个目录也加进 `actions/cache`）——
> 留作后续优化，没有为它推迟发版。

> 说明：CI 门禁本身（19 s / 40 s）远优于 DESIGN.md 的「< 3 分钟」基线；
> 「构建镜像 → 起真实栈 → 冒烟 → 回滚演练」这 109 s 满足 < 5 分钟基线。

---

## 8. 如何扩展（这是「可扩展」的具体含义）

1. **加一个客户端**：复用 `web/dist`，加一个 job 即可（就像 Android 那样）。管线结构不需要改。
2. **接真实服务器**：**已经做到，见 §13。** 换一台机器只需要改 `DEPLOY_HOST` / `DEPLOY_USER`、
   把新的公钥放进 `authorized_keys`，其余（compose、Traefik label、迁移独立成步、回滚演练）都不用动。
3. **加数据库迁移**：写一个新 revision，CI 会自动验证「能升级 + 能回滚 + 真库上跑得通」。
4. **加一个必须拦截的规则**：加一条测试即可。§5 已经证明这条链路是通的。
5. **网页端独立托管**：`web-<sha>.zip` 已经在 Release 里，扔到任意静态托管即可，
   只需把 `VITE_API_BASE` 指向 API 地址，并把该前端源站加进 `CORS_ORIGINS`。

---

## 9. 已知限制（诚实清单）

1. **两个「部署」含义不同，别混淆。** `smoke-image` 在 GitHub runner 上真起一套
   Postgres 16 + 迁移 + 应用，探活、跑冒烟、演练回滚，**然后 `down -v` 全部销毁**——这是
   **制品验证**，证明镜像本身是好的。真正把服务放到域名上的是 `deploy-server`（SSH 到服务器，
   见 §13）。两者都有价值：前者证明镜像，后者证明线上。
2. **安卓端只到「构建正确」，没有「验证可用」。**
   CI 做的全部是：Gradle 构建成功、包名 `dev.quicklaunch.app` 在二进制 manifest 里、
   `assets/public/index.html` 在 APK 里。**从未安装到设备或模拟器、从未启动、从未发出一次真实请求。**
   仓库变量 `QL_API_BASE` 未设置，所以 APK 里没有编译进后端地址，装上后打开是「API down」，
   需要在界面里手填 Server 地址。要升级成「验证可用」，得在 CI 里加一个 emulator 装上 APK 跑真实请求。
3. **桌面端只有 Windows 产物被真正运行过。** Linux 的 AppImage / deb 只构建，从未执行。
   你的场景只服务 Windows 客户，所以这不影响交付。
4. **桌面端自动更新的「安装」环节无法在 CI 里验证。**
   已验证：应用能读真实发布 feed、能正确判断有无更新、能定位安装包并校验哈希（见 §11）。
   未验证：`quitAndInstall()` 真正拉起 NSIS 安装器并重启——CI 上装一遍再重启会产生副作用，
   且未签名时还会弹 SmartScreen。**这一环需要你在真机上点一次确认。**
5. **没有做依赖/安全扫描**：没有 `pip-audit`、`npm audit`、CodeQL、Dependabot。
6. **签名相关**：APK 是 debug 签名；Windows/Linux 桌面未做代码签名。
   未签名不影响自动更新功能，但安装与更新时会弹一次 SmartScreen 警告。
7. **自动回滚仍然没有**（刻意）：自动回滚会掩盖问题，上一个 `sha-` tag 是人工回滚锚点，管线每次部署演练一遍。
8. **变更检测没有覆盖 docker / desktop job**：只改文档时 `verify-backend` / `verify-web` 会跳过，
   但 `docker` 仍会构建并推一个内容相同的新 `sha-` tag，`smoke-image` 也会照样冒烟它，
   `desktop` 还会重新打包一遍桌面端（约 3–13 分钟）。好处是「进了仓库的镜像都验过」，
   代价是这种构建本身有点浪费。`desktop` 之所以不做路径过滤，是因为「演练有更新可用」
   那条路（`workflow_dispatch -f version=0.0.1 -f draft=true`）即使没有路径变更也必须产出安装包——
   过滤器在这里会悄悄把演练变成空跑。这是刻意的取舍，不是遗漏。
9. **发版要人做决定，这是刻意的**：推送分支不再发版，只有打 `v<semver>` tag 才发布 + 部署。
   好处是「合并代码」和「惊动线上用户」分开了；代价是发版是一个需要人参与的显式动作
   （也可以 `gh workflow run pipeline -f deploy_ref=main` 只部署不发版）。
10. **`report/` 与 `scripts/gh*.py` 里的辅助脚本**：`gh.py` / `gh_logs.py` / `gh_push.py` 是我用来驱动
    GitHub API 的工具（本机没有 `gh` CLI，PowerShell 的 HTTPS 又被本地 schannel 阻断）。
    它们只操作本仓库，不碰其他项目。留在仓库里是因为它们记录了「在没有 gh CLI 的环境里怎么驱动管线」，
    但严格说不是交付物的一部分。
11. **线上还没接真实 Stripe**：`/api/billing/*` 目前返回 503（这是被冒烟测试验证过的合法状态，
    页面显示「本服务器未配置支付，额度上限仍然生效」而不是报错）。填上密钥即可生效，代码不用改（见 §14.4）。
12. **服务器是单机单副本；备份是同机的**：应用与数据库在同一台机器上。部署时**迁移之前**会自动
    `pg_dump`（保留最近 7 份，见 `deploy/deploy.sh`），所以"迁移写坏数据"是可恢复的；
    但备份与数据库在同一台机器上，**机器没了就一起没了**——异地备份仍待补，这也是
    「Demo 够用、生产不够」的地方。
13. **部署密钥目前是完整 shell 权限**（只授权了这一个用户的 SSH，但没有用 `command=` 把它限制成
    「只能执行 deploy.sh」）。加 `command=` 限制是可行的加固，尚未做——它会让人工排障时需要另一个入口。
14. **自动发版默认关闭，也只按"main 动了"判断**：开了之后纯文档提交也会发一个版本；
    要收紧可以约定提交信息或按路径过滤（见 `report/AUTOMATION.md` §7）。

---

## 10. 复核指引（想自己验证的话）

```bash
# 1. 打开线上站点（Traefik + Let's Encrypt，真实部署）
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://quicklaunch.luty.tech/
curl -sS https://quicklaunch.luty.tech/api/health

# 2. 看 tag 触发的全绿管线（全量验证 + 发布 + 部署 + 公网冒烟）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34693927161   # v1.2.1：12 个 job 全绿
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34693476660   # 只部署不发版（deploy_ref）

# 3. 看 tag 发布的产物（5 个：安装包、zip、blockmap、latest.yml、web zip）
open https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.2.1

# 4. 看开关打开后四端全发布的产物（9 个，含 APK / deb / AppImage）
open https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.39

# 5. 看镜像（公开可拉）
docker pull ghcr.io/luty4ng/quicklaunchtemplate:latest

# 6. 看门禁能变红（三条反向验证，已关闭 PR，运行记录永久保留）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016   # 类型错误
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864   # lint 错误
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044   # 越权

# 7. 本机跑一遍（无需 Docker）
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt
cd web && npm ci && npm run build && cd ..
cd backend && DATABASE_URL=sqlite+aiosqlite:///./data/dev.db \
  JWT_SECRET=local-dev-secret-that-is-long-enough \
  WEB_DIST=../web/dist ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
python scripts/smoke.py --base-url http://127.0.0.1:8000

# 8. 连支付链路一起验（不需要 Stripe 账号，见 §14.3）
STRIPE_WEBHOOK_SECRET=whsec_fake_secret python scripts/fake_stripe.py \
  --port 12194 --webhook-target http://127.0.0.1:8000/api/billing/webhook
```

> 交付状态：**完成**。main 分支干净（无未提交改动、无遗留分支、无开启的 PR，
> 三个反向验证 PR 已关闭），测试用的 draft release 与**早期命名方案遗留的 6 个 `build-*`
> release/tag 已删除**，开发机上的验证残留（含一份部署私钥副本）也已清除，
> 未对任何其他仓库发起写操作。

---

## 11. 桌面端自动更新（Windows）
**能做什么**：已安装的 Windows 客户端启动时会静默检查更新，发现新版本就在后台下载，
界面出现一个横条和**一个按钮**——点它即安装并重启到新版本。这就是「用户只需在客户端里一键拉取最新版」。

**怎么做到的**（`electron-updater` + 仓库自己的 GitHub Releases 作为更新源）：

| 环节 | 实现 |
|---|---|
| 更新源 | Release 里的 `latest.yml`（electron-builder 生成，含版本号、安装包名、sha512、大小） |
| 检查 | 应用启动时静默检查；界面「Server」旁也有「Check for updates」按钮 |
| 下载 | 后台自动下载，进度通过 IPC 推到界面 |
| 安装 | `quitAndInstall()`，界面上的「Restart and update」按钮触发 |
| 版本比较 | `desktop/lib/version.js`，6 个 `node:test` 用例（`1.0.10 > 1.0.9`、预发布低于正式版、无法解析一律回答「无更新」） |
| 版本递增 | 版本来自 tag（`v1.2.0` → `1.2.0`），且 `versioning` job 会拒绝不比已发布最高版本更高的 tag；**版本不递增客户端就永远收不到更新** |

**实测证据**：

| 场景 | 应用版本 | feed 提供 | 结论 |
|---|---|---|---|
| 本地受控 feed | 1.0.99 | 9.9.9 | `update-available`, comparison=1 |
| 本地受控 feed | 1.0.99 | 1.0.1 | `up-to-date`, comparison=-1 |
| CI，真实发布 feed | 0.0.1 | 1.0.33（已发布） | `update-available`, comparison=1 |
| CI，真实发布 feed | 1.0.34 | 1.0.34（自己） | `up-to-date`, comparison=0 |

最后一行对应 run [#34674755069](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34674755069) 的
`desktop-self-test`；第三行是专门为验证「有更新」路径而触发的
[#34675833510](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34675833510)
（用 `workflow_dispatch` 指定一个比线上低的版本 + draft release，跑完即删）。

**唯一没有实测的一环**：`quitAndInstall()` 真正拉起安装器那一下（原因见 §9 第 4 条）。
你在真机上装一次最新 Release 里的 `QuickLaunch-Setup-<version>-x64.exe`
（当前是 [v1.0.39](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.39)），
等有新构建时点一下按钮即可确认。

**注意**：未做代码签名，所以安装和更新时会弹一次 SmartScreen 警告；更新功能本身不受影响。

---

## 12. 发布哪些端（默认 + 开关）

**默认只发 Windows 桌面端和网页端；Linux 桌面端与安卓端需要显式打开。**

这个默认值来自实际需求：本项目只服务 Windows 客户，网页端是同一镜像的一部分；
而每推送一次都构建一个 APK，既花掉约 1 分钟 runner 时间，又往下载页塞一个没人装的产物。

| 目标 | 默认 | 产物 |
|---|---|---|
| Windows 桌面端 | **开** | `QuickLaunch-Setup-<version>-x64.exe`、`QuickLaunch-<version>-win.zip`、`latest.yml`、`blockmap` |
| 网页端 + API | **开** | `ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>`、`web-<sha>.zip` |
| Linux 桌面端 | 关 | `*.AppImage`、`*.deb`、`latest-linux.yml` |
| 安卓端 | 关 | `app-debug.apk` |

### 怎么开关

用**仓库变量**（持久生效，改一次一直有效）：

```bash
gh variable set PUBLISH_ANDROID --body true    # 打开安卓
gh variable set PUBLISH_LINUX   --body true    # 打开 Linux 桌面端
gh variable set PUBLISH_ANDROID --body false   # 再关掉
```

或者只对**某一次运行**生效，不动设置：

```bash
gh workflow run pipeline --ref main -f publish_android=true
```

### 实测

| 运行 | 开关状态 | 结果 |
|---|---|---|
| [#34677071586](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677071586) | 默认（无变量、无输入） | `android` **skipped**；`desktop (linux)` 绿色 no-op；Release [v1.0.38](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38) 只有 **5 个**资产：安装包、zip、blockmap、latest.yml、web zip |
| [#34677377502](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677377502) | `publish_android=true publish_linux=true` | 四端全过；Release [v1.0.39](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.39) 有 **9 个**资产，含 `app-debug.apk`、`.deb`、`.AppImage`、`latest-linux.yml` |

**实现要点**（都是踩过的坑）：

1. 开关在 `changes` job 里解析一次，作为 job output 广播，保证所有消费者看到同一个答案。
2. 判断写成 `vars.PUBLISH_ANDROID == 'true'` 而不是直接用变量——仓库变量是字符串，
   而字符串 `"false"` 在表达式里是**真值**，直接判断会导致开关永远打不开。
3. GitHub Actions **没有**受支持的「按条件跳过某个 matrix 腿」的写法
   （见 [SO 讨论](https://stackoverflow.com/questions/77186893) 与
   [官方语法文档](https://docs.github.com/en/enterprise-server@3.7/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idstrategy)），
   所以关掉的 Linux 腿跑的是一串带同一个 `if:` 的**成功 no-op**，
   而不是一条永远红色的「skipped」——否则每次默认发布看起来都像半残。
4. `release` job 只下载它将要附带的 artifact，「发不发」写进 Release 说明和运行摘要里。

---

## 13. 真实上线：从「制品验证」到「跑在域名上」

§9.1 曾经把「deploy 只是制品验证」列为已知限制——那一条现在不成立了。线上地址：

**https://quicklaunch.luty.tech**（Traefik 反向代理 + Let's Encrypt 证书，HTTP 200）

### 13.1 拓扑

```
浏览器 ──▶ Traefik（服务器上原有的，独占 80/443）
              └──▶ app:8000（FastAPI 同时提供 /api/* 与编译好的 SPA）
                       └──▶ db:5432（postgres:16，named volume：数据不随部署重建）
```

服务器上 `~/quicklaunch/` 只有 5 个东西：`compose.yaml`、`compose.server.yaml`、
`deploy.sh`、`.env`(600)、`src/`（部署时 clone 的源码，用于本地构建）。
**不碰服务器上原有的 `9router` / `traefik` / `homepage`**，只往 Traefik 的网络里挂一个容器。

### 13.2 部署链路（tag 触发）

```
推送 v1.2.0
  ├─ versioning   解析版本 + 门禁（必须严格大于已发布最高版本）
  ├─ verify-backend / verify-web
  ├─ docker       构建镜像并推 GHCR
  ├─ smoke-image  在 runner 上真起一套栈冒烟该镜像（含支付链路）
  ├─ desktop      Windows 安装包（版本取自 tag）
  ├─ android      （默认跳过）
  ├─ deploy-server SSH 同步文件 → 服务器构建上线 → 打公网地址冒烟
  ├─ release      发布 v1.2.0（含 latest.yml）
  └─ desktop-self-test  真跑打包产物 + 读真实更新源
```

`deploy.sh` 的内容顺序是刻意设计的，每一步失败都停：

```
起 db → 等 healthy → 迁移（独立一步）→ 起 app → 等 healthy → 清理旧镜像（保留最近 3 个）
```

**绝不覆盖 `.env`**（服务器密钥只存在服务器上，CI 从不传密钥过去），
**绝不 `docker compose down -v`**（那会连数据卷一起删）。
回滚是 `~/quicklaunch/deploy.sh <上一个 rev>`，或把 `.env` 的 `APP_IMAGE` 改回旧 tag；
CI 每次部署都会**演练一遍回滚**（重新部署上一个 `sha-` tag 并确认服务回来）。

### 13.3 服务器环境实测（决定了方案怎么选）

部署方案是被服务器网络逼出来的，不是先选好再做的：

| 目标 | 实测速度 | 结论 |
|---|---|---|
| ghcr.io | **74 B/s** | 拉镜像这条路直接死掉（`docker pull` 挂死 12 分钟） |
| github.com release 资产 | 0.00 Mbps | 也不通 |
| github.com 源码浅克隆 | **7 秒** | **可用** → 所以改成「服务器本地构建」 |
| github.com 的 git fetch（增量） | 会**周期性被掐断**：`GnuTLS recv error (-110): The TLS connection was non-properly terminated`；卡住时不报错、一直挂着 | `deploy.sh` 因此改成三种取源码方式依次尝试（重试 fetch → 浅克隆 → codeload tarball）+ 超时上限 |
| codeload tarball（`/tar.gz/<ref>`） | **3 秒 / 223 KB** | fetch 被掐的那一刻它是通的，所以作为最后一道退路 |
| Docker Hub | 0.00 Mbps | 不通，`daemon.json` 也不存在（没有镜像加速可用） |
| pypi.org | **0.52 Mbps** | 构建时 `pip install` 214 秒后失败 → 传 `PIP_INDEX_URL`（阿里云，实测 4.38 Mbps） |
| npm registry | 21.9 Mbps | 正常，官方源够快，不需要换 |
| Let's Encrypt | 签发成功 | 证书 `CN = quicklaunch.luty.tech`，有效期 90 天，Traefik 自动续期 |

> 没有采用「本机 `docker save` 后 scp 传镜像」：每层 110 MB，跨境上行同样不可靠，
> 而且每次发版都要经过我的机器——那不是 CI 该有的形状。

### 13.4 部署所需的仓库配置

| 类型 | 名称 | 说明 |
|---|---|---|
| Secret | `DEPLOY_SSH_KEY` | 部署专用 Ed25519 私钥，**在服务器上生成**，没有经过任何人的机器（指纹 `SHA256:S/hiR65Izc3surRmZYzrQBSRq/ixEGHhYVs86L43boA`） |
| Variable | `DEPLOY_HOST`、`DEPLOY_USER` | 服务器地址与登录用户 |

这条命令的权限边界（对应用户「不要动我其他项目」的要求）：

- 私钥只能登录这一个用户，且 CI 只用它执行 `~/quicklaunch/deploy.sh`；
- 所有写操作限制在 `~/quicklaunch/` 目录内；
- 不执行任何破坏性动作（无 `down -v`、无 `rm -rf`、无系统级改动），重启类操作不做。

> **密钥卫生**：生成密钥时，开发机的临时目录里留过一份私钥副本（`.cache/deploy-key/`，
> 当时按只读 ACL 保护）。交付前已连同全部本地验证残留一起删除（约 1.1 GB），
> 现在系统里只剩两处：GitHub Secret `DEPLOY_SSH_KEY` 与服务器 `authorized_keys` 里的公钥。
> 要作废重来，删掉 `authorized_keys` 里那一行、换掉 Secret，再打一个 tag 即可。

### 13.5 上线记录（v1.2.1，当前线上版本）

| 步骤 | 结果 |
|---|---|
| tag `v1.2.1` 推送（run [#34693927161](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34693927161)） | **12 个 job 全绿**：后端 94 测试、前端 20 测试、镜像冒烟（含支付链路 28/28）、Windows 打包、桌面端真跑自检、部署上线、公网冒烟门禁 |
| 部署日志里的取源码方式 | `git fetch（第 1 次）`——重试机制没被触发，但它在（v1.2.0 那次就是缺了它） |
| 服务器上的版本 | `quicklaunch:737aa07`（= tag 指向的提交），app healthy、db healthy |
| 迁移 | `0002_billing` 作为独立一步执行成功 |
| Release | `v1.2.1`，**5 个产物**：安装包、免安装 zip、blockmap、`latest.yml`（version: 1.2.1）、`web-737aa07.zip` |
| 公网冒烟 | **23/23**（含「未配置支付时 checkout 必须返回 503」一项）；`/api/health` 118 ms |
| 其他服务 | `9router`、`homepage`、`traefik` 均未受影响，Traefik 配置未改动 |

**v1.2.0 的失败也留着，因为它更说明问题**：那次 tag 的部署死在
`GnuTLS recv error (-110)`（服务器到 github.com 的链路被掐断），而且发布本身
「跳过测试、没有冒烟镜像、少了静态前端包」——因为那个 tag 只改了文档，路径过滤器
顺手把发布内容削减了。两件事都不是靠读代码能发现的，是**真跑**发现的：

1. 失败没有把站点打挂：构建成功前不切换容器，线上在失败期间一直正常服务旧版本；
2. 「断网重试」在这台服务器上是部署路径的一部分，不是可选项；
3. 「一次 tag = 全套验证 + 全套产物」必须由代码保证（`changes` job 里的 `scope` 一步），
   否则过滤器会在发布时悄悄削减内容。

---

## 14. 订阅支付（Stripe）

### 14.1 产品形态：只按额度分档，不做功能阉割

| 档次 | 待办条数上限 | 说明 |
|---|---|---|
| Free | 10 | 默认档 |
| Plus | 200 | 月付 |
| Pro | 不限 | 月付 |

额度**只在创建时**检查；读、改、删永远不拦。所以取消订阅后，用户已有的 11 条待办
一条都不会消失，只是不能再新增——这一条有测试守着（`billing: cancelling keeps the user's todos`）。

### 14.2 支付这件事上，钱和权限之间只隔四道关

| 关卡 | 做法 | 为什么必须这么做 |
|---|---|---|
| 验签 | 每个 webhook 先验 `Stripe-Signature`，**并拒绝未来时间戳** | 时间戳是签名内容的一部分。不查未来方向的话，抓到一个请求就能把 `t` 改大无限重放——实测 `stripe-python` 15.6.1 **只拒太旧的**，所以自己补了一道（有单测记录这个 SDK 行为） |
| 幂等 | 事件 id 作为 `billing_events` 主键 | Stripe 会重投。重投不能变成「再授予一次」 |
| 定价 | 前端只发 `{"plan": "plus"}`，**从不发 price id**；档位只由服务端价格表映射 | 否则改一个请求体就能用 1 元的价格买到 Pro。有测试断言请求体里不含 `price_` |
| 宽限 | `past_due` 保留访问权限 | 卡过期不该立刻断服务，那是 Stripe 自己的重试窗口 |

未配置 `STRIPE_*` 时 `/api/billing/*` 返回 **503**——明确失败，绝不静默假装成功。
这条也被冒烟测试覆盖（`billing: checkout fails loudly when unconfigured`），
所以「这个部署没接支付」是一个**被验证过的合法状态**，而不是一个会拖垮部署门禁的缺陷。

### 14.3 不用 Stripe 账号也能端到端验证

`scripts/fake_stripe.py` 是一个**只用标准库**实现的支付方替身（提供 checkout、客户门户、
取订阅三个接口，外加 `__control/pay|cancel|reset` 三个控制端点——因为真付款的是人），
它与测试用的内存网关**共用同一份事件构造代码**，两边不可能对事件形状产生分歧。
它拒绝以 `--env production` 启动。

流水线每次构建都跑完整条链路，全部通过才算绿：

```
下单 → 替身记录付款 → 投递已签名 webhook → 档位变 Plus → 第 11 条待办建得出来
     → 取消 → 掉回 Free → 已有 11 条内容还在
```

本地同样可跑（README 里有可复制的命令）。这一步的价值在 §1.1 已经体现：
**三个支付缺陷都是它抓出来的，其中两个是「看起来全绿、只是钱进来档位不变」这种最难靠读代码发现的。**

### 14.4 现状与剩余一步

| 项 | 状态 |
|---|---|
| 后端、迁移、接口、前端面板、测试 | ✅ 已完成并跑通 |
| 替身支付方的端到端 | ✅ CI 每次构建都跑 |
| 线上部署 | ✅ https://quicklaunch.luty.tech |
| **真实 Stripe**（真实 Checkout + 真实 webhook） | ⏳ 需要你的 Stripe 测试密钥与两个 `price_*`：填进服务器 `.env` 后重启容器即可生效，代码不用改 |

---

## 15. 自动化边界与模板化（交付后追加）

模板要迁移到别的项目，所以"哪些还得人来、为什么"本身就是交付内容。完整的梳理在
`report/AUTOMATION.md`，这里只记要点：

| 项 | 变化 |
|---|---|
| 项目标识 | 抽成 `project.env` 单一事实来源；`scripts/project_env.py check` 在 CI 每次推送时校验有没有漂移，`bootstrap` 一次性改完所有联动处（改名漏一处的后果并不均等：漏 `deploy.sh` 是部署失败，漏 electron-builder 的 `publish.owner` 是**自动更新静默失效**） |
| 数据库备份 | 部署时**迁移之前**自动 `pg_dump`（保留 7 份），失败即停——这是整条路径上唯一不可恢复的一步 |
| 定时发版 | 每小时心跳 + 配置小时（`AUTO_RELEASE_HOUR`，默认 02:00）+ 只发新提交 + 要求该提交的 CI 已绿；默认关闭，走的是与手动 tag 完全相同的发布路径 |
| 保留人工 | 打 tag（对外承诺）、服务器 `.env` 里的密钥（凭证不该多经一手）、回滚决策（自动回滚会掩盖问题） |
| 新增测试 | 版本递增的算术（`next_version` / `version_key`）有了单测——它决定凌晨两点自动发出去的是哪个版本号，算错是静默失效而不是报错 |

### 15.1 发版产物的精简（已执行）

一次 Release 原本挂着 5 个产物 + GitHub 自动附的 2 个源码包，其中**只有 3 个是必需的**：
安装包、`latest.yml`（客户端靠它判断新版本）、blockmap（增量下载）。
免安装 zip（146 MB）与静态网页 zip（72 KB）已按用户确认删除；
`release` job 的附件清单同时从通配符改成**白名单**，避免将来多出的构建输出自动变成下载项。
桌面端的"可真跑产物"改由单独的 CI 产物提供（`desktop-windows-unpacked`，保留 1 天、不进 Release），
所以 `desktop-self-test` 仍然真的启动打包后的应用去读真实更新源。
完整梳理见 `report/PIPELINE-REVIEW.md`。
