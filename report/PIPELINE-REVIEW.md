# 模板配置与发版梳理（含精简方案）

> 起因：一次 Release 里挂着 7 项（`latest.yml`、免安装 zip、安装包、blockmap、静态网页 zip，
> 外加 GitHub 自动附的源码包两个）。**目标形态：一次发版只出安装包。**
> 本文先回答"这些文件各自是什么、能不能砍"，再给出分步精简方案，最后把整个模板的
> 可配置项收成一张表——想改什么，改哪里。

---

## 0. 结论

**能砍到"只有安装包"吗？几乎——但必须留下 `latest.yml`（和 blockmap）。**

| 文件 | 大小 | 必需？ | 原因 |
|---|---|---|---|
| `QuickLaunch-Setup-<v>-x64.exe` | 107 MB | **必需** | 用户安装和更新装的就是它 |
| `latest.yml` | 359 B | **必需** | 已装客户端靠它判断有没有新版本；**最新 Release 里没有它，自动更新就静默失效**（踩过） |
| `*.exe.blockmap` | 115 KB | **强烈建议保留** | 有它只下载变化的块；没有它每次更新全量 107 MB |
| `QuickLaunch-<v>-win.zip` | 146 MB | **可以砍** | 免安装版，更新链路上没人用它 |
| `web-<sha>.zip` | 72.5 KB | **可以砍**（或改成开关） | 只有"网页端单独托管到别处"时才用得上；镜像里本来就带前端 |
| `Source code (zip/tar.gz)` | — | **砍不掉** | GitHub 给每个 Release 自动附的源码包，它们不是 asset，无法单独移除 |

所以推荐的目标形态是 **3 个文件**：安装包 + `latest.yml` + blockmap。三项里唯一"多出来"的
blockmap 是 115 KB，换来的是更新体积从 107 MB 降到几 MB——建议留着。

顺便说清楚一件事：**免安装 zip 是打包时就多花时间、发布时又多占 146 MB 的**，
因为 `desktop/package.json` 里 `build.win.target` 同时配了 `nsis` 和 `zip`。
砍它同时省打包时间和下载页噪音，是本次精简里收益最大的一步。

---

## 1. 现在一次发版都经过了什么（12 个 job）

耗时取自 tag `v1.2.1` 的真实运行（整条 294 秒）：

| 环节 | 做什么 | 实测 | 建议 |
|---|---|---|---|
| `changes` | 路径过滤、发布开关、项目标识漂移检查 | 5 s | 保留（它还负责"命名漏改就红"） |
| `versioning` | 从 tag 取版本 + 版本必须递增的门禁 | 6 s | 保留 |
| `verify-backend` | ruff + 迁移可升可回滚（postgres 16）+ 94 个测试 | 45 s | 保留 |
| `verify-web` | lint + typecheck + 20 个测试 + 生产构建 | 21 s | 保留 |
| `docker` | 构建镜像并推 GHCR | 48 s | 保留（对外分发用得上），但见 §3 第 3 步 |
| `smoke-image` | 在 runner 上真起一套栈冒烟镜像（含支付链路 28 项） | 52 s | **保留**：这是部署前唯一验证镜像的地方 |
| `desktop` (windows) | 打 NSIS 安装包 + 免安装 zip | 197 s | 保留，但产物只留安装包（§3 第 1 步） |
| `desktop` (linux) | AppImage / deb | 默认关 | 已经是开关 |
| `android` | APK | 默认关 | 已经是开关 |
| `deploy-server` | SSH 同步文件 → 服务器构建 → 备份 → 迁移 → 上线 → **公网冒烟门禁** | 102 s | **保留**：CD 的核心，公网冒烟已经救过一次场 |
| `release` | 汇总产物、发 Release（含 tag） | 33 s | 保留，产物清单精简 |
| `desktop-self-test` | 真跑打包后的应用、读**真实更新源**并报告结论 | 23 s | **保留**：这是"更新路径被验证过"的唯一证据 |
| `auto-release` | 每小时心跳，判断是否到点自动发版 | ~15 s | 保留（默认关） |

一句话：**流程长是因为覆盖了"三端 + 真实部署 + 自动更新"，不是因为步骤冗余。**
真正该减的是**产物**（下载页上挂着什么），其次是"默认开启哪些目标"，而不是这些环节本身。

---

## 2. 三个"看起来能省、其实别省"的地方

1. **`smoke-image`（52 s）**：它在 runner 上真的起容器、跑迁移、跑 28 项冒烟。
   没有它，"镜像能不能起来"就只有部署到线上才知道。省 52 秒，换"坏镜像先上线"。
2. **`desktop-self-test`（23 s）**：它启动打包后的 exe、读**真实 Release** 里的 `latest.yml`，
   报告"有没有更新"。桌面端自动更新出问题时的表现是**静默**的——用户再也收不到新版本，
   而没有任何报错。这 23 秒是唯一能提前发现它的地方。
3. **`deploy-server` 里的公网冒烟**：不是"部署完顺便测一下"，而是**部署的验收标准**。
   上一次发版就是这样被抓的：容器 healthy、部署"成功"，但 Traefik 路由规则写错，
   公网 404——只有打真实地址才看得出来。

---

## 3. 精简方案：三步，每步都能单独验证

每一步都是"改一处 → push main 看 CI → 打 tag 复验 Release 内容"，互不依赖，可随时停。

### 第 1 步（推荐先做）：默认只出安装包，免安装 zip 变成开关

* 改 `desktop/package.json`：`build.win.target` 只留 `nsis`（去掉 `zip`）。
* 想保留"偶尔打个免安装包"的能力，就在 `desktop/package.json` 加一个脚本
  `"dist:win:portable": "npm run stage && electron-builder --win zip"`，
  由仓库变量 `PUBLISH_PORTABLE` 决定 `desktop` job 用哪个脚本（矩阵里的 `script` 字段换一下即可）。
* 影响面：`desktop` job 少产一个 146 MB 文件；Release 少一项；更新链路**完全不受影响**
  （`latest.yml` 里本来只登记安装包，这一点已核对过 v1.2.2 的实际内容）。
* 预期效果：Release 从 5 个产物变 4 个，打包时间略降。

### 第 2 步：静态网页包改成开关（默认关）

* `release` job 里 `assemble the release bundle` 那一步在打包 `web-<sha>.zip`，
  以及 `files:` 里的 `dist/*.zip`。加一个开关（`PUBLISH_WEB_ZIP`，默认 false）：
  关掉时那一步直接跳过。
* 影响面：只有"把网页端部署到别处"的场景需要它；镜像里始终带着编译好的前端，
  线上不依赖这个 zip。
* 说明：它只有 72 KB，砍掉主要是**减少下载页上的困惑**，不是省时间。

### 第 3 步（可选）：GHCR 推送按需

* 线上是**服务器本地构建**（那台机器拉 ghcr.io 只有 74 B/s，拉不动），
  所以 GHCR 镜像目前只有"对外分发/留档"的意义。
* 想更简：把 `docker` job 的 push 做成开关，或改成只在 tag 时推送、分支推送只构建。
* 建议：先留着。它是"这个模板能把镜像发到 registry"的证明，砍掉这条，
  模板教给别人的东西就少了一块。

### 做完之后的样子

```
一次 tag v1.2.3 →
  Release 里 3 个文件：QuickLaunch-Setup-1.2.3-x64.exe + latest.yml + *.blockmap
  （外加 GitHub 自动附的 Source code 两个，删不掉）
  线上仍旧自动部署 + 公网冒烟；桌面端仍旧一键自动更新
```

---

## 4. 配置总览：想改什么，改哪里

### 4.1 仓库级（GitHub Variables / Secrets）

| 名字 | 作用 | 当前值 | 改法 |
|---|---|---|---|
| `DEPLOY_HOST` / `DEPLOY_USER` | 部署目标 | `122.152.219.10` / `ubuntu` | repo variables |
| `DEPLOY_SSH_KEY`（Secret） | 部署私钥 | 服务器生成 | repo secrets |
| `AUTO_RELEASE_ENABLED` | 定时发版总开关 | `true` | repo variables |
| `AUTO_RELEASE_HOUR` / `_TZ` | 发版时间 | `2` / `Asia/Shanghai` | repo variables |
| `AUTO_RELEASE_BUMP` | 版本递增方式 | `patch` | repo variables |
| `PUBLISH_ANDROID` / `PUBLISH_LINUX` | 是否构建 APK / Linux 包 | 未设置（关） | repo variables |
| `QL_API_BASE` | 打包进客户端/APK 的默认后端地址 | 未设置（=同源） | repo variables |

### 4.2 项目标识（`project.env`，唯一事实来源，非密钥）

| 键 | 作用 | 当前值 |
|---|---|---|
| `PROJECT_NAME` | 展示名、安装包名、Release 标题 | `QuickLaunch` |
| `PROJECT_SLUG` | 镜像名、compose 项目名、服务器目录 `~/<slug>` | `quicklaunch` |
| `REPO_OWNER` / `REPO_NAME` | 仓库（桌面端 `publish.owner` 也用它） | `luty4ng` / `QuickLaunchTemplate` |
| `APP_DOMAIN` | Traefik Host + 公网冒烟地址 | `quicklaunch.luty.tech` |
| `DESKTOP_APP_ID` / `MOBILE_APP_ID` | 客户端包名 | `dev.quicklaunch.desktop` / `.app` |

改完跑 `python scripts/project_env.py check`；迁移新项目用 `bootstrap`（见 `report/AUTOMATION.md`）。

### 4.3 服务器 `~/quicklaunch/.env`（**密钥只在这里**，CI 从不覆盖）

| 键 | 说明 |
|---|---|
| `JWT_SECRET` | ≥32 字符，服务器本地生成 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | 数据库账号（本地生成） |
| `APP_IMAGE` | 当前版本镜像 tag（deploy.sh 每次只改这一行） |
| `STRIPE_*`（可选） | `SECRET_KEY` / `WEBHOOK_SECRET` / `PRICE_PLUS` / `PRICE_PRO`；不配则 `/api/billing/*` 返回 503 |

### 4.4 应用自身（`backend/app/config.py`，可用环境变量覆盖）

| 键 | 默认 | 说明 |
|---|---|---|
| `FREE_TODO_LIMIT` / `PLUS_TODO_LIMIT` | 10 / 200 | 档位额度（Pro 不限） |
| `PASSWORD_MIN_LENGTH` | 8 | 注册密码最短长度 |
| `JWT_TTL_SECONDS` | 7 天 | 会话有效期 |
| `BCRYPT_ROUNDS` | 12 | 密码哈希成本 |
| `COOKIE_SECURE` / `CORS_ORIGINS` | false / 空 | 分开部署时才需要 |
| `AUTO_MIGRATE` | true（本地）| **生产是流水线里独立一步**，不靠这个 |

### 4.5 手动触发的输入（`workflow_dispatch`）

| 输入 | 用途 |
|---|---|
| `version` | 用指定版本号发布（配合 `draft=true` 演练"有更新可用"） |
| `deploy_ref` | **只部署不发版**（如 `main`） |
| `auto_release` / `auto_release_dry_run` | 立即跑一次自动发版判断；默认 dry run |
| `draft` | 发成草稿（不进更新源） |
| `publish_android` / `publish_linux` | 只对本次运行打开安卓 / Linux |

---

## 5. 精简之后，"模板"教给别人的是什么

砍掉两个 zip 之后，这个模板仍然完整演示了四件事：

1. **一条流水线管三端 + 服务端**：同一份前端产物复用到网页/桌面/安卓；
2. **真实部署**：SSH → 服务器本地构建 → 备份 → 迁移（独立成步）→ 上线 → **公网冒烟门禁**；
3. **桌面端自动更新**：`latest.yml` 在最新 Release 里，且"更新路径"每次发版都被真跑验证；
4. **可配置的自动化边界**：什么时候发（定时/手动）、发什么（开关）、标识在哪（`project.env`）。

产物从 5 个减到 3 个不会削弱任何一条，反而让"一次发版到底产生了什么"更容易看懂。

---

## 6. 下一步

第 1 步（去掉免安装 zip）改动最小、收益最直接，建议先做。做之前先确认一件事：
**你还想要"解压即用"的免安装版吗？**

* 想要 → 那就把它做成开关（默认关），需要时打开；
* 不想要 → 直接从 `desktop/package.json` 的 `win.target` 里删掉，CI 与文档同步改。
