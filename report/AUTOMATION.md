# 部署自动化边界：哪些该自动、哪些该留给人、为什么

> 适用对象：本模板，以及从它派生出去的项目。
> 相关文件：`project.env`（项目标识）、`scripts/project_env.py`（迁移与漂移检查）、
> `scripts/auto_release.py`（定时发版决策）、`deploy/deploy.sh`（服务器侧部署）。

---

## 0. 结论

一次发版里**只剩下一个动作必须由人做：决定打这个 tag**。其余要么已经自动化，
要么是**刻意保留人工**——因为那几件事自动化之后只会更差，不是更省心。

| 分类 | 环节 | 为什么 |
|---|---|---|
| 已自动化 | 验证、构建、发布、部署、公网冒烟、回滚演练、**数据库备份**、**项目标识漂移检查** | 确定性、可重复、失败可回滚 |
| 新自动化 | **定时发版**（默认每天 02:00，可配置）、**迁移清单**（`project.env` + `bootstrap`） | 前者省掉"记得发版"，后者省掉"改名漏一处" |
| 保留人工 | 打 tag、写服务器 `.env` 的密钥、回滚决策、给 GitHub 写 Secret | 涉及对外承诺与凭证；自动化会把风险放大而不是缩小 |
| 建议做但没做 | 异地备份、预发环境、依赖升级 PR、可用性探针 | 已在 §7 列为缺口 |

判断标准就一条：**这一步失败时，是"重跑一次就好"，还是"说不清、收不回、或者把密钥
多交出去一份"？** 前者自动化，后者留给人。

---

## 1. 一次性接入新项目

| 环节 | 现状 | 建议 | 理由 |
|---|---|---|---|
| DNS 解析到服务器 | 人工 | 保留人工 | 一辈子做一次；用 DNSPod API 自动化反而要维护密钥 |
| 服务器准备（Docker、`~/<slug>/`、Traefik 网络） | 人工 | 可自动化（`bootstrap.sh`，未做） | 步骤确定，但一台机器只做一次，收益低 |
| 生成 `.env`（`JWT_SECRET`/`POSTGRES_PASSWORD`） | 人工，在服务器上 `openssl rand` | 可自动化（脚本生成，**仍在服务器上跑**） | 关键不是"谁敲命令"，而是**密钥不经过 CI、不经过任何第三方** |
| 部署密钥：服务器生成 → 公钥进 `authorized_keys` → 私钥存 GitHub Secret | 人工 | **保留人工** | 私钥一旦由流水线代传，就等于多了一份可被流水线读取的副本；这一步的"麻烦"正是它的安全边界 |
| GitHub Variables / Secrets（`DEPLOY_HOST`、`DEPLOY_USER`、发布开关） | 人工一条命令 | 半自动 | 值由人给，写入用 `gh variable set` 即可 |
| Stripe：Product + Price + 客户门户 + webhook 密钥 | 人工 | 可自动化 | 建 Product/Price/webhook 都有 API，且创建 webhook endpoint 时**直接返回签名密钥**（[Stripe 文档](https://docs.stripe.com/api/webhook_endpoints/create.md)）；但至少要有一次人给的 API key，所以是"半自动" |
| **改项目标识（域名/仓库/镜像/包名）** | 人工，10+ 处 | **已自动化**：`project.env` + `check` + `bootstrap` | 见 §3，这是最容易漏、漏了又最难发现的一类 |

---

## 2. 每次发版

| 环节 | 谁做 |
|---|---|
| 决定"这个版本要不要发"与版本号 | **人**（`git tag -a v1.2.2 && git push origin v1.2.2`） |
| 或：到点自动发 | **流水线**（§4，默认关闭） |
| 验证（后端 94 / 前端 20 / 迁移可升可回滚 / 镜像冒烟 28 项） | 流水线 |
| 构建、发布 Release、部署到服务器、公网冒烟门禁 | 流水线 |

**打 tag 保留人工是刻意的**：它是"要不要惊动线上用户"的决定，而桌面端会自动更新，
发出去就收不回。把这一步也自动化，等于让"合并代码"和"用户拿到新版本"重新粘在一起——
那正是当初把发版改成 tag 驱动时要分开的两件事。

---

## 3. 项目标识：一个文件 + 一道检查

改名要动的地方散在十几处，**故障方式还不一样**：

| 位置 | 漏改的后果 |
|---|---|
| `deploy/deploy.sh` 的仓库地址 | 部署直接失败（还算好的） |
| `deploy/compose.server.yaml` 的域名 | 证书签给旧域名 / 路由不生效 |
| `pipeline.yml` 的冒烟地址 | 门禁打的是旧站点——**看起来绿，其实没验** |
| `desktop/package.json` 的 `publish.owner` | **桌面端自动更新静默失效**，直到用户再也收不到版本 |
| `compose.yaml` 的项目名 | 与旧项目抢容器名/数据卷名 |

所以：

```
project.env                  ← 唯一事实来源（无密钥，可提交）
scripts/project_env.py check ← CI 每次推送都跑：谁漂移了就红
scripts/project_env.py bootstrap --repo owner/name --domain x.y --slug z --write
                             ← 一次性改完所有联动处，然后自动跑一次 check
```

能读环境变量的地方（`deploy.sh`、workflow 的 shell 步骤、两个 compose 文件、Traefik label）
都改成从 `project.env` 派生；读不到的地方（`package.json`、`capacitor.config.json`、
workflow 的 `env:` 块）保持写死，但**由 `check` 保证与 `project.env` 一致**——
这是刻意的取舍：与其为了"全动态"引入构建期改文件的魔法，不如让不一致直接报错。

**这条边界踩过一次**：第一版把 Traefik 的 label 键也写成了 `${COMPOSE_PROJECT_NAME:-...}`，
以为 compose 会插值——它只插值 label 的**值**，键会原样保留。于是路由名变成了字面量
`${COMPOSE_PROJECT_NAME:-quicklaunch}`；同一版里我又把规则写成 `Host(\`a\`, \`a\`)`，
而 Traefik v3 的 `Host()` **只接受一个参数**，两条叠加的结果是路由根本没建起来、公网 404。
值得注意的是**它是被门禁抓到的**：部署本身成功（容器 healthy），是部署后的公网冒烟
把这次发布判成失败。修法：label 键用字面量、由 `check` 校验它等于 slug；
`Host()` 只留一个参数。

`bootstrap` 默认是 dry run，`--write` 才落盘，落盘后立刻自检；它还会打印剩下需要人做的 4 件事。

---

## 4. 定时自动发版（可配置）

### 4.1 为什么是"每小时醒一次"

GitHub Actions 的 `schedule` **不能读仓库变量**（cron 表达式在 YAML 里写死）。
所以实现方式是：每小时醒一次，由 `scripts/auto_release.py` 判断"现在是不是你配置的那个小时"。
好处是**时间可以随时改，不用动代码**。

### 4.2 配置（全部是仓库变量，默认全关）

| 变量 | 默认 | 含义 |
|---|---|---|
| `AUTO_RELEASE_ENABLED` | `false` | 总开关。不开就永远不自动发版 |
| `AUTO_RELEASE_HOUR` | `2` | 几点发（24 小时制，按下面的时区） |
| `AUTO_RELEASE_TZ` | `Asia/Shanghai` | 时区 |
| `AUTO_RELEASE_BUMP` | `patch` | 版本怎么涨：`patch` / `minor` / `major` |

```bash
gh variable set AUTO_RELEASE_ENABLED --body true
gh variable set AUTO_RELEASE_HOUR    --body 2
gh variable set AUTO_RELEASE_TZ      --body Asia/Shanghai
gh variable set AUTO_RELEASE_BUMP    --body patch
```

### 4.3 四道门（全部通过才会发）

1. 开关是 `true`；
2. 当前小时（配置的时区）等于配置的小时；
3. main 上有**新提交**（最新 Release 指向的提交 ≠ 当前 HEAD）——没动就不发，不会刷版本号；
4. 该提交的**推送流水线已经绿了**——绝不发布一个 CI 还没验完的提交；
   还没跑完就等下一个整点，这正是"每小时醒一次"的用处。

通过后它会**派发同一条流水线**（带版本号 + `deploy_ref=main`），由 `release` job 建 tag。
也就是说：自动发版和手动发版走的是**同一条路径**，不存在第二套逻辑要维护。

### 4.4 想先看它会不会发、又不想真发

```bash
gh workflow run pipeline --ref main -f auto_release=true            # 默认 dry run
gh workflow run pipeline --ref main -f auto_release=true -f auto_release_dry_run=false   # 真发
```

dry run 会把四道门的判断逐条打印出来（包括"最新 Release 是哪个、这个提交的 CI 绿没绿"）。

两点差异值得知道：

* **手动触发的运行不受"配置的小时"限制**——那个小时是给无人值守的心跳用的；
  人明确点了一次，就按人的意思办（脚本会打印 `hour gate: skipped`）。
* 手动触发的**默认是 dry run**：点一下不会真的发版，要真发得显式关掉它。

---

## 5. 运行期

| 环节 | 谁做 | 说明 |
|---|---|---|
| 往服务器 `.env` 加/换密钥 | **人** | 密钥不进 CI 是设计，不是没来得及做 |
| 回滚 | **人** | 刻意不自动回滚：自动回滚会把"部署失败"变成"悄悄退回旧版本"，问题被掩盖。命令是 `APP_IMAGE` 换上一版镜像，或 `deploy.sh <上一个 rev>` |
| 数据库备份 | **流水线**（本次新增） | 部署时、**迁移之前**自动 `pg_dump`，保留最近 7 份；备份失败就中止部署 |
| 依赖升级 / 可用性告警 | 未做 | 见 §7 |

备份这一条值得展开：整条部署路径上，**代码能回滚、镜像能切回，只有数据是不可恢复的**。
所以它被放在迁移之前、失败即停——宁可这次不部署，也不要带着一条不可回滚的迁移往前走。

---

## 6. 迁移到新项目：实际要做的

```bash
# 1) 一次性改名（dry run 先看会改哪些文件）
python scripts/project_env.py bootstrap \
  --repo <owner>/<name> --domain <new.domain> \
  --name "<显示名>" --slug <小写短名> --write

# 2) 自检（CI 里也会跑，漂移就红）
python scripts/project_env.py check
```

然后剩下 4 件必须人做的事（`bootstrap` 也会把它们打印出来）：

1. **DNS**：把新域名解析到服务器；
2. **服务器**：建 `~/<slug>/`、写 `.env`（`JWT_SECRET`、`POSTGRES_PASSWORD`、`APP_IMAGE`）、
   把部署公钥放进 `~/.ssh/authorized_keys`；
3. **GitHub**：Secret `DEPLOY_SSH_KEY`，Variables `DEPLOY_HOST` / `DEPLOY_USER`；
4. 推一个 tag（如 `v0.1.0`）——剩下的验证、发布、部署、冒烟都是流水线的事。

---

## 7. 还没做的（诚实清单）

| 缺口 | 影响 | 成本 |
|---|---|---|
| 备份是**同机**的 | 挡得住"迁移写坏数据"，挡不住"整台机器没了" | 加一个对象存储上传 ~1 小时 |
| 没有预发环境 | 坏版本先上线，冒烟失败时用户可能已经看到 | 同机第二个 compose 项目 + 子域名 ~1 小时 |
| 自动发版只按"main 动了"判断 | 纯文档提交也会发一个版本 | 约定提交信息（如 `[skip release]`）或按路径过滤 ~30 分钟 |
| 没有可用性探针 | 线上挂了要靠人发现 | 外部 uptime 或定时 workflow ~15 分钟 |
| 没有 Dependabot | 依赖不会自动更新 | 开一个开关 |
| 部署密钥未加 `command=` | 密钥泄漏等于拿到 shell（虽然它已只用于部署） | ~1 小时，需要同步改 CI 的同步方式 |
