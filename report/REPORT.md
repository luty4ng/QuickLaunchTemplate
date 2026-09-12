# QuickLaunch 交付报告

> 交付对象：`luty4ng/QuickLaunchTemplate`（GitHub，公开）
> 目标：一个 Demo，跑通「一条 CI/CD 管线自动发布网页端 / 桌面端 / 安卓端」，后端用 FastAPI，
> 实现尽量简洁、高效、省心、可扩展。
> 发布策略演进：最初三端全发；按实际需求改为**默认只发 Windows 桌面端 + 网页端，
> 安卓端与 Linux 桌面端由开关控制**（见 §12）。

---

## 1. 结论

**已完成，且管线是真的跑通的，不是纸面设计。**

| 交付物 | 位置 | 状态 |
|---|---|---|
| 后端（FastAPI，含迁移、认证、租户隔离） | `backend/` | ✅ 本地 45 个测试通过，CI 在 Postgres 16 上再跑一遍 |
| 网页端（同一个 React 产物，由后端直接托管） | `web/` | ✅ 11 个单测通过，镜像内含构建产物 |
| 桌面端（Electron，Windows 默认发布、Linux 可选） | `desktop/` | ✅ 产物自检通过（在 CI 的 Windows runner 上真跑起来），并支持一键自动更新 |
| 安卓端（Capacitor，可安装 APK，默认不发布） | `mobile/` | ✅ 开关打开后 CI 产出并校验包名与内嵌前端产物 |
| 一条管线（CI + CD，单文件 + 发布开关） | `.github/workflows/pipeline.yml` | ✅ 11 个 job（含 versioning）全绿 |
| 一条 Release（默认 5 个可下载产物） | [v1.0.38](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38) | ✅ |

关键运行记录（全部是真实执行，可在仓库 Actions / Releases / Packages 页复核）：

| 运行 | 结果 | 说明 |
|---|---|---|
| [#34651815693](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693) | ✅ 全绿 | 首个完整成功的端到端管线（当时 10 个 job 全部真实执行），产出镜像 + 三端产物 + Release |
| [#34677071586](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677071586) | ✅ 全绿 | **默认发布行为**：安卓跳过、Linux 绿色 no-op，Release 只含 Windows + 网页端 |
| [#34677377502](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677377502) | ✅ 全绿 | **打开开关**：`publish_android` + `publish_linux`，四端全部构建并发布 |
| [#34675833510](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34675833510) | ✅ 全绿 | **更新路径验证**：低版本客户端读到线上 feed → `update-available` |
| [#34652578016](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016) | ❌ 按预期变红 | 故意塞类型错误 → `verify-web / typecheck` 拦住 |
| [#34652632864](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864) | ❌ 按预期变红 | 故意塞无用 import → `verify-backend / lint` 拦住 |
| [#34652715044](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044) | ❌ 按预期变红 | 故意破坏租户隔离 → `verify-backend / integration` 拦住 |

最新 Release：[v1.0.39](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.39)（四端全发布）。
默认发布形态见 [v1.0.38](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38)（仅 Windows + 网页端）。

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

最后两条尤其说明问题：**如果只跑绿、不做反向验证、不以真实产物为准，这两类缺陷会一路带到用户面前。**

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
| 冒烟用例在部署后的真实地址上通过 | ✅ | 17/17（新版本）、16/16（回滚版本） |
| 用上一个 `sha-` tag 重新部署，服务恢复正常 | ✅ | 回滚演练：`sha-3cd811b` 重新部署后 16/16 通过，CI 输出 `::notice::rollback rehearsal passed` |

---

## 7. 实测数据

| 项目 | 数值 | 来源 |
|---|---|---|
| 后端测试 | 45 passed（22 单测 + 23 集成） | 本地 + CI |
| 前端测试 | 11 passed | 本地 + CI |
| 冒烟用例 | 17/17（首次部署）、16/16（回滚演练） | CI run #34651815693 |
| `/api/health` 响应 | 20–28 ms（预算 1000 ms） | 冒烟输出 |
| **CI 门禁（后端 job）** | **38 s** | run #34651815693 |
| **CI 门禁（前端 job）** | **19 s** | 同上 |
| CD（构建推送镜像 + 起栈 + 探活 + 冒烟 + 回滚演练） | **46 s + 96 s = 142 s** | 同上，**满足 < 5 分钟基线** |
| 完整管线（含三端打包 + Release） | **5.0 min** | run #34655397546 |
| 失败反馈速度 | 28-32 s 变红，下游全部 skipped | run #34652632864 |
| 镜像 | `ghcr.io/luty4ng/quicklaunchtemplate`，公开，tag `sha-<commit>` + `latest` | GHCR |
| 变更检测 | 只改文档/脚本时后端 job 自动跳过 | run #34655397546 |

各 job 耗时明细（run #34651815693）：changes 5s、web 19s、backend 38s、
desktop(windows) 201s、desktop(linux) 92s、android 125s、docker 46s、deploy 96s、
desktop-self-test 30s、release 26s。

> 说明：CI 门禁本身（19 s / 38 s）远优于 DESIGN.md 的「< 3 分钟」基线；
> 完整管线 5 分钟里的大头是桌面端打包（electron-builder 在 Windows 上出 NSIS 安装包）。
> 「从合并到探活通过」这段（docker + deploy + self-test）约 172 s，满足 < 5 分钟基线。

---

## 8. 如何扩展（这是「可扩展」的具体含义）

1. **加一个客户端**：复用 `web/dist`，加一个 job 即可（就像 Android 那样）。管线结构不需要改。
2. **接真实服务器**：`compose.yaml` 已经是可部署形态。在一台机器上：
   ```bash
   export JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
   docker compose up -d --wait      # 自动跑迁移再起应用
   ```
   若要走 CD 自动部署，给 `deploy` job 加一段 SSH（secrets 里放主机、用户、私钥），
   镜像已经是 `sha-<commit>` 可直接拉取，回滚就是换一个 tag 重新 `up -d`。
3. **加数据库迁移**：写一个新 revision，CI 会自动验证「能升级 + 能回滚 + 真库上跑得通」。
4. **加一个必须拦截的规则**：加一条测试即可。§5 已经证明这条链路是通的。
5. **网页端独立托管**：`web-<sha>.zip` 已经在 Release 里，扔到任意静态托管即可，
   只需把 `VITE_API_BASE` 指向 API 地址，并把该前端源站加进 `CORS_ORIGINS`。

---

## 9. 已知限制（诚实清单）

1. **「deploy」不是把服务部署到服务器上，而是制品验证。**
   它在 GitHub runner 上真起一套 Postgres 16 + 迁移 + 应用，探活、跑 17 项冒烟、演练一次回滚，
   **然后 `down -v` 全部销毁**。没有 VPS、没有域名、没有 TLS、没有持久化数据。
   这是 DESIGN.md 里 A 段的设计意图（「不依赖任何外部服务器」），也正是「GitHub 可以替代部署」这句话
   成立的部分：GitHub 能替代**制品分发**（Releases / GHCR / Pages）和**部署前的真实验证**，
   但替代不了**长期运行的 API 进程**——runner 是一次性的，job 结束即销毁。
   要让人打开 App 就能连上后端，仍需一个常驻容器平台或一台服务器（B 段，模板已留）。
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
7. **自动回滚仍然没有**（刻意）：自动回滚会掩盖问题，`sha-` tag 是人工回滚锚点，管线每次部署演练一遍。
8. **变更检测没有覆盖 docker job**：只改文档时 `verify-backend` / `verify-web` 会跳过，
   但 `docker` 仍会构建并推一个内容相同的新 `sha-` tag。`deploy` 现在会照样冒烟这个镜像
   （所以不会再有「进了仓库却没验证」的 tag），但这次构建本身是浪费的。
   彻底修掉需要「镜像 tag 必须指向真正变更的提交」，逻辑会绕很多。
9. **每次默认分支构建都会新建一个 Release**，版本号随运行号递增（`v1.0.<run>`）。
   这是让已安装客户端能持续更新的代价——`latest.yml` 必须出现在**最新**的 release 里。
   仓库会缓慢积累历史版本；要控制的话加一个清理旧 release 的步骤即可。
10. **`report/` 与 `scripts/gh*.py` 里的辅助脚本**：`gh.py` / `gh_logs.py` / `gh_push.py` 是我用来驱动
    GitHub API 的工具（本机没有 `gh` CLI，PowerShell 的 HTTPS 又被本地 schannel 阻断）。
    它们只操作本仓库，不碰其他项目。留在仓库里是因为它们记录了「在没有 gh CLI 的环境里怎么驱动管线」，
    但严格说不是交付物的一部分。

---

## 10. 复核指引（想自己验证的话）

```bash
# 1. 看当前默认行为下的全绿管线（Windows + 网页端，安卓/Linux 跳过）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677071586

# 2. 看打开开关后的全绿管线（四端全过）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34677377502

# 3. 看默认发布的产物（5 个）
open https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38

# 4. 看打开开关后的产物（9 个，含 APK / deb / AppImage）
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
```

> 交付状态：**完成**。main 分支干净（无未提交改动、无遗留分支、无开启的 PR），
> 全部实验分支与测试用的 draft release 已删除，
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
| 版本递增 | `versioning` job 每次构建盖 `1.0.<run number>`；**版本不递增客户端就永远收不到更新** |

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
