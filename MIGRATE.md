# 一键迁移

> **结论先给：一次迁移 = 4 条命令 + 4 件只能由人做的事。**
> 这份文档是"照着敲就行"的操作版；每条命令**为什么**这么做、边界在哪，在
> [`MIGRATION.md`](MIGRATION.md)（穷尽式清单）。
> 如果你是 Agent，直接跳到 **[§7 快速迁移区块](#7-快速迁移区块agent-专用)**——那一节自包含，可以整段丢给别的 Agent 执行。

---

## 0. 全景

| # | 做什么 | 一条命令 | 谁做 | 完成标志 |
|---|---|---|---|---|
| 1 | 有一个属于你的仓库 | GitHub **Use this template**（或只推 `main`） | 人 | 新仓库里能看到这份代码，且**没有历史 tag** |
| 2 | 改名（12 处一起改） | `python scripts/project_env.py bootstrap … --write` | 脚本 | `check` 打印 `12 files checked` |
| 3 | 服务器准备 | `bash deploy/bootstrap-server.sh` | 脚本 + 人 | `--check` 全 ✓ |
| 4 | 换业务 | `rm -rf` 5 个目录 + 改 2 处注册表 | 人/Agent | `python scripts/rehearse_migration.py` 通过 |
| 5 | 首次发版 | `git tag -a v0.1.0 && git push origin v0.1.0` | 人 | Release 只有安装包 + 公网冒烟 `SMOKE OK` |

必须人工的 4 件（**不是懒，是安全边界**）：DNS 解析、部署公钥、GitHub Secret/Variables、Stripe 密钥。

---

## 1. 先有一个属于你的仓库

**推荐：GitHub 上点 "Use this template"**（或 New repository 时选这个模板）。得到的是一个
干净的仓库：一条初始提交、**没有历史 tag**——正是下面想要的状态。

想保留完整提交历史，就只推 `main`：

```bash
git clone --bare https://github.com/<你>/<模板仓库>.git template.git
cd template.git
git push https://github.com/<你>/<新仓库>.git main:main   # 只推 main
cd .. && rm -rf template.git
git clone https://github.com/<你>/<新仓库>.git && cd <新仓库>
```

> ⚠️ **不要用 `git push --mirror`。** 流水线的触发条件是
> `on.push.tags: ['v*']`——**tag 一推就等于发版 + 部署**。镜像会把模板的历史 tag
> （`v1.2.2` … `v1.2.7`）一起带进新仓库，于是在你的新项目里凭空触发 6 次发版流水线，
> 每次都会去发布一个不属于你的版本、并尝试部署。已经带过去了就删掉：
> `git tag -l 'v*' | xargs -n1 git push origin --delete`（或直接在 GitHub 的 Tags 页面删）。
>
> （不建议 fork：fork 会一直挂着上游关系。）

> ⚠️ **仓库要公开。** `deploy/deploy.sh` 在服务器上取源码时走 `git clone` / `codeload tarball`，
> **不带任何凭据**——私有仓库会让三种取源码方式全部失败。要私有仓库，就得自己在服务器上
> 配好凭据（deploy key / token），模板没有内置这一步。

## 2. 改名：一条命令改 12 处

```bash
# 先干跑，看清要改什么（不加 --write 不会落盘）
python scripts/project_env.py bootstrap \
  --repo <owner>/<name> --domain <新域名> \
  --name "<显示名>" --slug <小写短名>

# 确认无误再落盘，并自动跑一遍自检
python scripts/project_env.py bootstrap \
  --repo <owner>/<name> --domain <新域名> \
  --name "<显示名>" --slug <小写短名> --write
```

它一次改掉：`project.env` + 12 个不能插值的文件（workflow 的镜像名、compose、`.env.example`、
Traefik 路由名、`deploy.sh`、`desktop/package.json` 的 `publish.owner/repo`、
`desktop/app-config.json` 的更新源地址、Capacitor 包名、`web/index.html` 的 `<title>`、
`web/package.json` + `package-lock.json` 的 name、`web/src/App.tsx` 的 `APP_NAME`）。

期望输出（**这就是"改名成功"的证据**）：

```
project identity agrees with project.env (12 files checked)
```

漏改的后果并不均等：漏 `deploy.sh` 的仓库地址是部署直接失败（还算好），
漏 electron-builder 的 `publish.owner` 是桌面端**自动更新静默失效**，
漏前端的只是线上还挂着旧名字（不影响运行，所以最容易一直没人发现）——所以这 12 个文件每一个都有校验。

## 3. 服务器：一条命令 + 4 件人工事

```bash
# 1) 只体检：不碰任何东西。新机器上应当逐条报 ✗ 并退出码 1（不会误报通过）
bash deploy/bootstrap-server.sh --check

# 2) 准备：只补缺的东西，重复执行是空操作
bash deploy/bootstrap-server.sh

# 只在需要时追加（正常一台机器最多用到其中一条）：
#   sudo bash deploy/bootstrap-server.sh --install-docker     # 缺 docker / compose 插件
#   bash deploy/bootstrap-server.sh --with-traefik --acme-email you@example.com   # 裸机，且没有 Traefik
```

它会：检查/安装 docker 与 compose 插件 → 建外部网络 `traefik-net` → 建 `~/<slug>/` →
**在服务器本地生成 `.env`**（`JWT_SECRET`、`POSTGRES_PASSWORD` 用 `openssl rand` 现生成，
绝不过 CI）→ `chmod 600` → 打印下面这 4 件人工事。**已存在的 `.env` 绝不被覆盖。**

1. **部署公钥**：把 CI 用的公钥追加进服务器 `~/.ssh/authorized_keys`
   （`echo '<公钥>' >> ~/.ssh/authorized_keys`）。私钥一旦由脚本或流水线代传，就多了一份能被读取的副本。
2. **GitHub**：`gh secret set DEPLOY_SSH_KEY < <私钥文件>`；
   `gh variable set DEPLOY_HOST --body <公网地址>`；`gh variable set DEPLOY_USER --body ubuntu`。
3. **DNS**：把新域名 A 记录指向服务器；云安全组**同时放行 80 与 443**
   （Let's Encrypt 的 HTTP-01 校验要从公网访问 80，否则证书签不下来）。
4. **（可选）接支付**：把 `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` /
   `STRIPE_PRICE_PLUS` / `STRIPE_PRICE_PRO` 写进服务器 `~/<slug>/.env`，再部署一次。
   不配也能跑：`/api/billing/*` 返回 503，界面会写明"本服务器未配置支付"。

## 4. 换业务：删 5 个目录 + 改 2 处注册表

```bash
rm -rf backend/app/features/todos backend/app/features/billing
rm -rf backend/tests/unit/features backend/tests/integration/features
rm -rf web/src/features/todos web/src/features/billing
```

然后只改两处注册表：

* `backend/app/features/__init__.py`：去掉 `from app.features import billing, todos`，把
  `FEATURES = (todos.FEATURE, billing.FEATURE)` 换成你的功能包。
* `web/src/features/index.ts`：去掉两行 import，把 `FEATURES = [billing, todos]` 换成你的。

新功能的最小形态：

```
后端 backend/app/features/<你>/     __init__.py  FEATURE = Feature(name="<你>", router=router)
                                    router.py    router = APIRouter(prefix="/<前缀>", tags=["<你>"])
                                    models.py    Base 子类（alembic 靠导入看到它）
                                    schemas.py   请求/响应契约
前端 web/src/features/<你>/          index.ts     export const FEATURE = { id, Panel }
                                    Panel.tsx    自己管状态、自己调自己的 api.ts
```

**不需要动** `main.py`、`App.tsx`、`conftest.py`、任何骨架文件——它们不认识具体业务。

验证（这条命令就是"我删干净了没有"的机器答案）：

```bash
python scripts/rehearse_migration.py            # 完整预演：后端 + 前端
python scripts/rehearse_migration.py --keep     # 保留副本，可以直接当新项目的起点
```

期望：`迁移预演通过：零业务状态下骨架自洽（这就是迁移的起点）`

## 5. 首次发版

```bash
git tag -a v0.1.0 -m "first release"
git push origin v0.1.0
```

**一个 tag 发全套**：验证 → 构建镜像 → 发布 Release（只含安装包）→ 部署到服务器 →
写桌面更新源 → 公网冒烟。分支推送只验证，不发布、不部署。

自动发版（可选）：默认**关**。要开就设 `AUTO_RELEASE_ENABLED=true`，配合
`AUTO_RELEASE_HOUR=2`（默认凌晨 2 点）、`AUTO_RELEASE_TZ=Asia/Shanghai`、`AUTO_RELEASE_BUMP=patch`。
它每小时醒一次，只在配置的那个小时里、且 main 有新的**推送 run 全绿**的提交时才发。

## 6. 验收：每条都有"看得见的输出"

| 步骤 | 命令 | 期望输出 |
|---|---|---|
| 改名 | `python scripts/project_env.py check` | `project identity agrees with project.env (12 files checked)` |
| 删干净了没有 | `python scripts/rehearse_migration.py` | `迁移预演通过：零业务状态下骨架自洽` |
| 后端 | `cd backend && ruff check . && ruff check ../scripts && pytest tests/unit -q && pytest tests/integration -q` | 全绿（**含** `tests/unit/test_layering.py`） |
| 迁移可逆 | `cd backend && alembic upgrade head && alembic downgrade base && alembic upgrade head` | 三步都成功 |
| 前端 | `cd web && npm run lint && npm run typecheck && npm test && npm run build` | 全绿 |
| 服务器 | `bash deploy/bootstrap-server.sh --check` | 全部 ✓（没准备的机器上应逐条 ✗ 且退出码 1） |
| 首次发版 | `git tag -a v0.1.0 -m "first" && git push origin v0.1.0` | 流水线全绿；Release 只有安装包 |
| 部署后门禁 | `python scripts/smoke.py --base-url https://<域名>` | `SMOKE OK`（业务断言还没换完时加 `--skip-business`） |
| 更新源 | `curl -s https://<域名>/updates/latest.yml` | `version: <你的版本>` |

---

## 7. 快速迁移区块（Agent 专用）

> **给执行迁移的 Agent：** 本节自包含。只按本节执行，不要自由发挥；
> 遇到 §7.5 列出的情形就停下来问人，不要自己决定。

### 7.1 你的任务

把一个已经跑通过的模板仓库，变成用户自己的项目：改掉项目标识、清掉示例业务、
准备好服务器、让所有门禁变绿，最后（在用户同意后）用第一个 tag 把它发上线。

**完成 ≠ 代码能跑**。完成 = §7.6 的 DoD 每一条都有命令输出作证。

### 7.2 硬约束（违反即失败）

1. **不要手工改那 12 个文件**——用 `scripts/project_env.py bootstrap --write`，手工改必漏。
2. **不要写死任何密钥**。密钥只存在于服务器 `~/<slug>/.env` 和 GitHub Secrets；不要打印、不要提交、不要写进日志或回复。
3. **不要自己造 DNS、Stripe、GitHub 凭据**——那 4 件是人工事，向用户索取或让用户自己做。
4. **不要在没有用户明确同意时**：执行 `rm -rf`（删示例业务）、`git push` 任何 tag、在服务器上跑 `apply` 或任何 `sudo` 命令。
5. **不要动与本次迁移无关的项目/目录/服务**；服务器上只碰 `~/<slug>/`、`traefik-net` 和本项目容器。
6. **保持骨架不动**：`app/` 下除 `features/`、`web/src` 下除 `features/`、`App.tsx`、`main.py`、`tests/conftest.py` 都不该因为换业务而被修改。
7. 每步都要跑该步的验证命令，**报告真实输出**；失败就修，不要跳过。

### 7.3 开工前一次性问清（缺任何一项都不要开始）

| 要什么 | 例子 | 用途 |
|---|---|---|
| 新仓库 `owner/name` | `acme/AcmeApp` | `bootstrap --repo` |
| 显示名 + 小写短名 | `Acme Launch` / `acmeapp` | `--name` / `--slug`（slug 决定镜像名、目录名、Traefik 路由名） |
| 域名 | `app.acme.com` | `--domain`（Traefik Host + 公网冒烟目标） |
| 仓库公开还是私有 | 公开（推荐） | 私有会让 `deploy.sh` 取源码失败（§1 的警告） |
| 服务器 SSH 别名/地址、用户名 | `acme-server` / `ubuntu` | 部署；有没有现成 Traefik 占着 80/443 |
| 是否允许 sudo 装 docker、是否允许脚本装 Traefik | 允许 / 已有 | `--install-docker` / `--with-traefik` |
| 部署公钥怎么给 | 用户生成或你生成后让用户贴 | `DEPLOY_SSH_KEY` + `authorized_keys` |
| 是否接 Stripe | 是 / 否 | 4 个 key 写进服务器 `.env` |

### 7.4 执行序列

每一步都给出「命令 → 期望 → 不满足时怎么办」。

**S0 前置检查**
```bash
git status --short          # 期望：空（有未提交改动先问用户怎么处理）
git tag -l 'v*'             # 期望：空。非空说明这个仓库带着模板的历史 tag——见 §1 的警告，先问用户
python scripts/project_env.py show   # 看清当前项目标识
```

**S1 改名**
```bash
python scripts/project_env.py bootstrap --repo <owner>/<name> --domain <域名> --name "<显示名>" --slug <短名>
#   → 期望：打印"files that would change"清单 + "(dry run)"。把要改的文件列表给用户看一眼。
python scripts/project_env.py bootstrap --repo <owner>/<name> --domain <域名> --name "<显示名>" --slug <短名> --write
python scripts/project_env.py check
#   → 期望：project identity agrees with project.env (12 files checked)
#   → 不是这句：逐条按提示改（提示会指出文件与行号），改完再 check
```

**S2 换业务**（先拿到用户对 `rm -rf` 的明确同意）
```bash
rm -rf backend/app/features/todos backend/app/features/billing
rm -rf backend/tests/unit/features backend/tests/integration/features
rm -rf web/src/features/todos web/src/features/billing
# 然后编辑两个注册表（backend/app/features/__init__.py、web/src/features/index.ts）
python scripts/rehearse_migration.py
#   → 期望：迁移预演通过：零业务状态下骨架自洽
#   → 若失败：报错会指出是后端还是前端、哪个测试红的。**先修骨架的用法，不要改骨架测试来绕过**
```
用户自己的功能包按 §4 的最小形态添加；加完再跑一次预演与本地全量测试。

**S3 本地全量验证**
```bash
cd backend && ruff check . && ruff format --check . && ruff check ../scripts \
  && pytest tests/unit -q && pytest tests/integration -q && cd ..
cd web && npm run lint && npm run typecheck && npm test && npm run build && cd ..
```
→ 期望：全绿。**ruff 那两条最容易漏**（CI 会跑 `../scripts`），本地漏跑会让 CI 红。

**S4 提交推送（不要推 tag）**
```bash
git add -A && git commit -m "<中文说明这次迁移做了什么>" && git push origin main
```
然后把这次推送的 CI run 结果拿到手（全绿才算过）。

**S5 服务器**
```bash
ssh <别名> "pwd && whoami && hostname && ls -la ~/"
bash deploy/bootstrap-server.sh --check        # 体检；把 ✗ 列表原样贴给用户
# 用户确认后（apply 需要 sudo 的先问）：
bash deploy/bootstrap-server.sh [--install-docker] [--with-traefik --acme-email <邮箱>]
```
→ 期望：`--check` 全 ✓；apply 结束时会打印 4 件人工事——**把那 4 件原样转达给用户**，不要代做。

**S6 首次发版**（**必须用户明确同意**：tag = 真发布 + 真部署）
```bash
git tag -a v0.1.0 -m "first release" && git push origin v0.1.0
```
→ 期望：Release 里**只有安装包**；服务器部署成功；公网冒烟通过。

**S7 验收并汇报**
```bash
python scripts/smoke.py --base-url https://<域名>          # 业务断言没换完就加 --skip-business
curl -s https://<域名>/updates/latest.yml                   # version: <版本>
curl -s https://<域名>/api/health                           # {"status":"ok",...}
```
汇报时给出：命令 + 真实输出 + 未通过项与原因。不要用"应该没问题"这类话。

### 7.5 停下来问人的情形

* 要 `rm -rf` 任何东西、要 `push` tag、要在服务器上 `sudo` 或跑 apply；
* `git tag -l 'v*'` 非空（新仓库里带着模板的历史 tag，会误触发发版）；
* 用户没给齐 §7.3 里的信息（尤其域名、slug、仓库公开性）；
* `project_env.py check` 报的漂移指向你不认识的第三个项目（说明仓库里混进了别的东西）；
* 预演失败且原因在**骨架**而不是业务（这是模板 bug，值得单独修，不要就地绕）；
* 涉及密钥、凭据、DNS、支付服务商；
* 线上部署后冒烟失败——**不要自动回滚**，把失败项报给用户（`sha-` 镜像 tag 是人工回滚锚点）。

### 7.6 完成的定义（DoD）

- [ ] `python scripts/project_env.py check` → `12 files checked` 且无漂移
- [ ] `python scripts/rehearse_migration.py` → 通过
- [ ] `cd backend && ruff check . && ruff check ../scripts && pytest tests/unit -q && pytest tests/integration -q` → 全绿
- [ ] `cd web && npm run lint && npm run typecheck && npm test && npm run build` → 全绿
- [ ] 推送后的 CI run **全绿**
- [ ] `bash deploy/bootstrap-server.sh --check` → 全 ✓
- [ ] 首个 tag 的 Release：**只有安装包**（没有 `latest.yml`、没有 `.blockmap`）
- [ ] `python scripts/smoke.py --base-url https://<域名>` → `SMOKE OK`
- [ ] `curl -s https://<域名>/updates/latest.yml` → 版本号正确
- [ ] 上面每一条都附上**真实输出**，未通过项写明原因

### 7.7 实测过的坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 用 `--mirror` 搬仓库 | 新仓库里凭空多出几次发版/部署 run | tag 就是发版触发器；删掉 `v*` 历史 tag，或改用 "Use this template" |
| 仓库是私有的 | `deploy.sh` 三种取源码方式全失败 | 把仓库设为公开，或在服务器上自己配凭据 |
| 忘了 ruff 的 `../scripts` | CI `backend` job 红，本地却全绿 | S3 里那两条 ruff 命令一起跑 |
| 手工改了 `<title>`/`APP_NAME` 没改 `project.env` | `project_env.py check` 报漂移 | 一律用 `bootstrap --write` |
| `smoke.py` 的业务断言没换完 | 公网冒烟红在 `/api/todos` 相关项 | 过渡期加 `--skip-business`，换完再摘掉 |
| 80/443 已被别的 Traefik 占用 | `--with-traefik` 直接中止 | 不要抢端口；复用现有 Traefik（它需要 `traefik-net` 和名为 `letsencrypt` 的 resolver） |
| 想改 Traefik 路由名 | 路由名是**字面量**（label 的 key 不能插值） | 交给 `project_env.py`（`check` 会守住 slug 与路由名一致） |
| 自动发版"没反应" | 心跳每小时醒，但只在配置的那个小时发 | 这是设计；要看结果就把 `AUTO_RELEASE_HOUR` 设成当前小时并等下一次心跳 |

---

**配套文档**：[`MIGRATION.md`](MIGRATION.md)（逐条理由 + 诚实清单）、
[`DESIGN.md`](DESIGN.md)（架构与取舍）、[`report/AUTOMATION.md`](report/AUTOMATION.md)（哪些自动化、哪些必须人工及原因）。
