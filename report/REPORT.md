# QuickLaunch 交付报告

> 交付对象：`luty4ng/QuickLaunchTemplate`（GitHub，公开）
> 目标：一个 Demo，跑通「一条 CI/CD 管线自动发布网页端 / 桌面端 / 安卓端」，后端用 FastAPI，
> 实现尽量简洁、高效、省心、可扩展。

---

## 1. 结论

**已完成，且管线是真的跑通的，不是纸面设计。**

| 交付物 | 位置 | 状态 |
|---|---|---|
| 后端（FastAPI，含迁移、认证、租户隔离） | `backend/` | ✅ 本地 45 个测试通过，CI 在 Postgres 16 上再跑一遍 |
| 网页端（同一个 React 产物，由后端直接托管） | `web/` | ✅ 11 个单测通过，镜像内含构建产物 |
| 桌面端（Electron，Windows/Linux 安装包） | `desktop/` | ✅ 产物自检通过（在 CI 的 Windows runner 上真跑起来） |
| 安卓端（Capacitor，可安装 APK） | `mobile/` | ✅ CI 产出并校验包名与内嵌前端产物 |
| 一条管线（CI + CD，单文件） | `.github/workflows/pipeline.yml` | ✅ 10 个 job 全绿（run #34651815693） |
| 一条 Release（6 个可下载产物） | [build-12](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/build-12) | ✅ |

关键运行记录（全部是真实执行，可在仓库 Actions / Releases / Packages 页复核）：

| 运行 | 结果 | 说明 |
|---|---|---|
| [#34651815693](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693) | ✅ 全绿 | 首个完整成功的端到端管线，产出镜像 + 三端产物 + Release |
| [#34652578016](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016) | ❌ 按预期变红 | 故意塞类型错误 → `verify-web / typecheck` 拦住 |
| [#34652632864](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864) | ❌ 按预期变红 | 故意塞无用 import → `verify-backend / lint` 拦住 |
| [#34652715044](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044) | ❌ 按预期变红 | 故意破坏租户隔离 → `verify-backend / integration` 拦住 |

> 「一条永远绿的管线，和不存在的管线可信度一样。」——所以上面三条反向验证是本次交付的重点之一。

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

- `desktop` 是**按宿主 OS 拆开的 matrix**：Windows runner 出 `.exe`（NSIS 安装包 + 便携版 + zip），
  Linux runner 出 `.AppImage` + `.deb`。跨平台出 Windows 安装包需要 Wine，而 Linux runner 上没有，
  会以 `spawn wine ENOENT` 失败——「哪个系统就出哪个系统的安装包」这个约束反而更简单可靠。
- `desktop-self-test` 在 windows-latest 上真跑打包产物（见 §3.4）。
- `android`：`cap add android` → `cap sync` → `gradlew assembleDebug` → 校验 APK。

### 4.6 汇总：`release`

只在默认分支成功时执行：下载全部 artifact，打成 Release（tag 为 `build-<run number>`），
当前是 [build-12](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/build-12)，包含：

| 产物 | 大小 |
|---|---|
| `QuickLaunch-Setup-1.0.0-x64.exe`（Windows 安装包） | 111.4 MB |
| `QuickLaunch-Portable-1.0.0-x64.exe`（Windows 便携版） | 111.2 MB |
| `QuickLaunch-1.0.0-win.zip`（Windows 免安装解压即用） | 153.1 MB |
| `QuickLaunch-1.0.0-x86_64.AppImage`（Linux） | 125.0 MB |
| `QuickLaunch-1.0.0-amd64.deb`（Debian/Ubuntu） | 98.8 MB |
| `app-debug.apk`（安卓，debug 签名可直接安装） | 4.2 MB |
| `web-5df3e84.zip`（静态前端包） | 0.1 MB |

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
| 效率基线：CI < 3 分钟、CD < 5 分钟 | 见 §7 实测 |

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
| 完整管线（含三端打包 + Release） | **5.0 min** | 同上 |
| 失败反馈速度 | 后端 lint 28 s 变红，下游全部 skipped | run #34652632864 |
| 镜像 | `ghcr.io/luty4ng/quicklaunchtemplate`，公开，tag `sha-<commit>` + `latest` | GHCR |

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

1. **本机无法验证安卓构建**：本机既无 Android SDK，Gradle 下载也被网络环境阻断（Java 连
   `services.gradle.org` 超时，而 Python/git 可达——是本地 Java 出网问题，不是仓库问题）。
   安卓端的证据来自 CI 的实际构建 + APK 校验。
2. **本机无法验证 Docker**：本机没有 Docker/Postgres，镜像构建、起栈、探活、迁移全部在 CI 上验证。
3. **APK 是 debug 签名**：可直接安装，但不适合上架。要做 release 签名只需在 `android/app/build.gradle`
   加 signingConfig 并把 keystore 放进 secrets——刻意没做，因为那会给 Demo 引入真实的密钥管理负担。
4. **桌面端未做代码签名**：Windows 会弹 SmartScreen 提示。签名需要付费证书，Demo 不做。
5. **未做自动回滚**：这是刻意的。自动回滚会掩盖问题；`sha-` tag 是人工回滚锚点，
   而且管线每次部署都演练一遍回滚。
6. **单架构镜像**：只出 amd64（arm64 需要模拟，会显著拖慢反馈速度）。
7. **`report/` 与 `scripts/gh*.py` 里的辅助脚本**：`gh.py` / `gh_logs.py` / `gh_push.py` 是我用来驱动
   GitHub API 的工具（本机没有 `gh` CLI，PowerShell 的 HTTPS 又被本地 schannel 阻断）。
   它们只操作本仓库，不碰其他项目。留在仓库里是因为它们记录了「在没有 gh CLI 的环境里怎么驱动管线」，
   但严格说不是交付物的一部分。

---

## 10. 复核指引（想自己验证的话）

```bash
# 1. 看最后一次全绿的管线（10 个 job）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693

# 2. 看三端产物
open https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/build-12

# 3. 看门禁能变红（三条反向验证，均已关闭 PR 但运行记录永久保留）
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016   # 类型错误
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864   # lint 错误
open https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044   # 越权

# 4. 本机跑一遍
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt
cd web && npm ci && npm run build && cd ..
cd backend && DATABASE_URL=sqlite+aiosqlite:///./data/dev.db \
  JWT_SECRET=local-dev-secret-that-is-long-enough \
  WEB_DIST=../web/dist ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
python scripts/smoke.py --base-url http://127.0.0.1:8000
```
