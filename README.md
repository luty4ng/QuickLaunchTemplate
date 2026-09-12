# QuickLaunch

一个 FastAPI 后端 + 三端客户端（**网页端 / 桌面端 / 安卓端**），由一条 GitHub Actions
流水线完成构建、测试、容器化与发布。

本仓库是一个**交付演示**：业务（待办清单）故意做到最小，让评审注意力全部落在管线上。
这里没有 mock——每个 job 都真实执行，验收证据就是运行历史。

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
            │ deploy: compose│               │                       │
            │ 探活 + 冒烟     │               │                       │
            │ + 回滚演练      │               │                       │
            └────────┬───────┘               │                       │
                     └───────────┬───────────┴───────────────────────┘
                                 │
                       ┌─────────▼──────────┐
                       │ release: 打 tag     │
                       │ 汇总本次发布的产物   │
                       └────────────────────┘
```

## 实际发布什么

默认分支每次推送**发布两个目标**，另外两个是**可选**的。

| 目标 | 产物 | 默认 | 由谁产出 |
|---|---|---|---|
| Windows 桌面端 | `QuickLaunch-Setup-<version>-x64.exe`、`QuickLaunch-<version>-win.zip` | **发布** | `desktop` job |
| 桌面端更新源 | `latest.yml` + `*.blockmap`（已安装客户端读取的更新清单） | **发布** | `desktop` job |
| 网页端 + API | `ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>`（一个镜像同时提供两者） | **发布** | `docker` job |
| 网页端（静态包） | `web-<sha>.zip` | **发布** | `release` job |
| Linux 桌面端 | `*.AppImage`、`*.deb`、`latest-linux.yml` | 关闭 | `desktop` job |
| 安卓端 | `app-debug.apk`（可直接安装，debug 签名） | 关闭 | `android` job |

默认分支每次构建都会发布一个 tag 为 **`v<version>`** 的 Release，版本号取自运行号
（`1.0.<run number>`）。**版本号必须递增**：桌面客户端只接受比当前版本**更高**的更新。

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
「Restart and update」即完成升级。它读取最新 Release 里的 `latest.yml`——
该文件由 electron-builder 生成，由 `release` job 发布。

更新路径是**被验证过的**，不是假设：`desktop-self-test` 在 Windows runner 上真启动打包后的应用，
让它读**真实发布的更新源**，并报告自己得出的结论。要专门触发「有更新」分支，用一个比线上更低的版本发布：

```bash
gh workflow run pipeline --ref main -f version=0.0.1 -f draft=true
```

这样会打包出一个比线上版本更旧的客户端（发成 draft，所以下载页不会出现它），
自检必须报告 `update-available`。

**安装那一步是 CI 无法演练的**（它会在 runner 上装软件并重启），
所以值得在真机上点一次确认。产物未做代码签名，Windows 会弹一次 SmartScreen 提示。

## 为什么网页端和后端共用一个镜像

FastAPI 进程同时提供 `/api/*` 和编译好的 SPA。一个产物、一个端口、一个健康检查；
而且因为浏览器始终只与自己的源通信，**没有 CORS，也没有跨站 Cookie 问题**。
桌面端与安卓端复用同一份前端 bundle，所以 UI 只写一遍。

## 仓库结构

```
backend/      FastAPI 应用、alembic 迁移、pytest 测试
  app/          config、security（bcrypt+JWT）、deps、schemas、routers
  migrations/   版本化 schema；`alembic upgrade head` 是流水线里独立的一步
  tests/unit/   纯逻辑，不连数据库
  tests/integration/  真实 HTTP + 真实数据库（本地 sqlite，CI 里 postgres）
web/          Vite + React + TypeScript SPA（唯一的前端源码）
desktop/      Electron 外壳 + electron-builder 打包（含自动更新）
mobile/       Capacitor 配置；android/ 原生工程由 CI 生成，不入库
scripts/      smoke.py（部署后门禁）、verify_apk.py、gh*.py（驱动管线的工具）
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

## 关键设计取舍

- **迁移是流水线里独立的一步**，绝不写进应用启动命令。否则多副本同时启动会并发跑迁移。
- **CD 门禁是冒烟测试，不是健康检查。** `/api/health` 只能证明数据库连得上；
  `scripts/smoke.py` 会注册用户、建/读/改/删待办、确认登出真的让会话失效，
  并在真实部署上验证租户隔离。
- **租户隔离在 SQL 里强制**，而不是查出来再补一次归属校验：每条语句都带
  `user_id = 调用者`。访问他人资源返回 404 而非 403，不泄露资源是否存在。
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

全要素全绿的一次是 [#34651815693](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693)
（10 个 job，5.0 分钟）；最近的默认发布是
[v1.0.38](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/v1.0.38)（5 个产物：
安装包、免安装 zip、blockmap、`latest.yml`、静态网页包）。

## 文档

- `DESIGN.md` —— 本项目最初的设计草案（v1，Next.js 时期；已被实现取代，保留以溯源）。
- `report/REPORT.md` —— 交付报告：做了什么、管线如何运作、实测耗时、真实运行证据、
  与 DESIGN.md 的逐条对照、已知限制、以及自动更新与发布开关的说明。
