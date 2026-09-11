# QuickLaunch Demo —— 方案文档（v1 草案）

> 目的：**验证一条完整的 CI/CD 管线**。业务故意做到最简，评审时注意力应全部落在管线上。
> 状态：草案，代码未开工。
> 证据标注：**【实测】**= 本机跑过命令得到的结果；**【推断】**= 基于经验，尚未验证。

---

## 1. 结论与范围

**技术选型：Next.js 16 全栈单体 + PostgreSQL + Prisma + Docker + GitHub Actions。**

**业务：极简待办清单。** 注册、登录、看自己的待办、增删改、勾选完成。没有团队、没有角色、没有分享、没有通知。

**为什么是这套栈**
- 全栈单体 = 一个产物、一个镜像、一条流水线。CI 链短、反馈快，"验证管线"时每一步失败都能一眼定位到原因
- 每一环都是行业里被广泛使用、且招聘市场上能对上的东西：TypeScript、React、PostgreSQL、Prisma、Docker、GitHub Actions
- Prisma 的 `migrate deploy` 是一等公民，能在流水线里真实验证"部署阶段跑数据库迁移"这个最容易翻车的环节

**为什么不用其他常见组合（各一句代价）**
- NestJS + React 前后端分离：两套产物、两个镜像、两条部署链，Demo 阶段翻倍的是维护面而不是覆盖度
- Spring Boot + Vue：同样行业认可，但 JVM 构建在 CI 里更慢，验证管线时反馈周期变长
- SQLite：省掉 CI 里的数据库 service container，但本地/CI/生产三方数据库不同源，恰好掩盖了"迁移在流水线里跑不通"这个最该被验证的点

---

## 2. 版本矩阵

| 组件 | 版本 | 说明 |
|---|---|---|
| Node.js | 22.x（本机 22.20.0） | 【实测】当前 LTS 线 |
| Next.js | 16.3.5 | 【实测】镜像上的 latest |
| React / React DOM | 19.3.0 | 【实测】 |
| TypeScript | 7.0.2 | 【实测】新大版本，配置方式可能和 5.x 有差异 |
| ESLint | 10.10.0 + `eslint-config-next` 16.3.5 | 【实测】flat config；Next 16 起 `next lint` 已移除，直接用 ESLint CLI |
| Vitest | 5.0.0 | 【实测】 |
| Prisma / @prisma/client | **7.10.0（两边必须同版本）** | 【实测】CLI 的 latest 是 `8.0.0-rc.13`，属**预发布**，必须显式锁 7.x 避开 |
| Zod | 4.6.2 | 【实测】请求体校验 |
| bcryptjs | 3.0.3 | 【实测】纯 JS 实现，无需编译工具链，CI 里不会因为 node-gyp 挂掉 |
| jose | 6.2.12 | 【实测】签发/校验会话 JWT |
| PostgreSQL | 16 | 本地、CI、生产三方同版本 |

**风险提示**：上表是"镜像上存在这些版本"，**不等于这套组合能装得上、构建得过**【推断】。TypeScript 7 与 ESLint 10 都是新大版本，实际配置方式可能与既有文档不同。开工第一步就是装依赖跑通 lint + typecheck，装不通就地降级到上一个稳定大版本。

---

## 3. 数据模型

三张表砍到两张 —— **不做服务端 session 表**：会话状态放进 httpOnly Cookie（JWT），少一张表、少一次查询。代价是无法主动踢人下线，Demo 阶段接受。

```
User
  id            String   @id @default(cuid())
  email         String   @unique          // 存库前统一转小写
  passwordHash  String                    // bcrypt，永不返回给客户端
  createdAt     DateTime @default(now())

  todos         Todo[]

Todo
  id            String   @id @default(cuid())
  userId        String                    // FK → User.id, onDelete: Cascade
  title         String
  done          Boolean  @default(false)
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt

  user          User     @relation(...)

@@index([userId, createdAt(sort: Desc)])
```

**索引理由**：`User.email` 唯一索引承担登录查询；`Todo(userId, createdAt desc)` 复合索引精确对应唯一的高频查询"取某人的待办按时间倒序"，别的索引一律不加。

**数据隔离铁律**（这是本 Demo 唯一有技术含量的约束）
1. 所有 Todo 读写必须带 `where: { userId }`，禁止先 `findUnique({ id })` 再回头补归属校验
2. 访问他人的 Todo 返回 **404 而不是 403** —— 不泄露资源是否存在
3. 这条规则由集成测试兜底：双用户交叉访问用例（见 §6）

---

## 4. 接口清单

统一前缀 `/api`。请求体全部经 Zod 校验，校验失败统一返回 `400 + {error: {code, message}}`。

| 方法 | 路径 | 作用 | 请求体 | 成功 | 失败 |
|---|---|---|---|---|---|
| GET | `/api/health` | 健康检查（CD 探活用） | — | `200 {status:"ok"}` | `503`（DB 连不上） |
| POST | `/api/auth/register` | 注册 | `{email, password}` | `201` + `Set-Cookie` | `400` 校验失败 / `409` 邮箱已存在 |
| POST | `/api/auth/login` | 登录 | `{email, password}` | `200` + `Set-Cookie` | `400` / `401` 凭据错误 |
| POST | `/api/auth/logout` | 登出 | — | `204` + 清 Cookie | — |
| GET | `/api/todos` | 列出当前用户的待办 | — | `200 Todo[]` | `401` 未登录 |
| POST | `/api/todos` | 新建 | `{title}` | `201 Todo` | `400` / `401` |
| PATCH | `/api/todos/:id` | 改标题 / 勾选完成 | `{title?, done?}` | `200 Todo` | `400` / `401` / `404` |
| DELETE | `/api/todos/:id` | 删除 | — | `204` | `401` / `404` |

**响应约定**
- 任何响应体**永不包含** `passwordHash`
- 登录失败不区分"邮箱不存在"和"密码错误"，统一 `401`（防用户枚举）
- 密码最小 8 位；bcrypt cost 在测试环境调低以加速 CI，生产用默认值

**页面**
- `/` —— 未登录显示注册/登录表单，登录后显示待办列表（单页完成，不做路由分叉）
- `/api/health` 之外不需要额外运维接口

---

## 5. CI/CD 阶段定义

**放在同一个 workflow 文件里的两个 job，用 `needs` 表达依赖。** 不用 `workflow_run` 串联两个文件 —— 跨 workflow 的依赖判断更容易写错，还需要额外的 PAT。简单最优先。

### CI job（触发：`pull_request` + `push` 到任意分支）

| # | 步骤 | 验证什么 | 失败意味着 |
|---|---|---|---|
| 1 | `actions/checkout` + `setup-node@22` + `npm ci` | lockfile 可复现安装 | 依赖被意外改动，或 lockfile 与 package.json 不一致 |
| 2 | `npm run lint` | 代码规范 | — |
| 3 | `npm run typecheck`（`tsc --noEmit`） | 类型正确 | — |
| 4 | `npm run test:unit` | 纯函数逻辑：密码哈希/校验、JWT 签发校验、Zod schema | 与数据库无关，**本机也能跑** |
| 5 | `npm run test:integration` | 真 Postgres 上的端到端 API 行为 | **只能 CI 跑**（本机无 Docker/Postgres） |
| 6 | `npm run build` | 生产构建能过 | 构建期报错（Server/Client 边界、环境变量缺失等） |

步骤 5 的数据库用 GitHub Actions 的 **service container**（`postgres:16`），与生产同版本。执行顺序：`prisma migrate deploy` → 灌测试数据 → 跑用例。

### CD job（触发：`push` 到 `main` 或手动 `workflow_dispatch`，`needs: [ci]`）

| # | 步骤 | 验证什么 |
|---|---|---|
| 1 | `docker/build-push-action` 多阶段构建 | Dockerfile 正确、镜像能构建 |
| 2 | 打两个 tag：`sha-<short>` 与 `latest`，推 GHCR | 用内置 `GITHUB_TOKEN`，不需要额外 secret；`sha-` tag 是回滚锚点 |
| 3 | `prisma migrate deploy` | **独立一步，失败即停** |
| 4 | 起容器（compose，带 Postgres） | 镜像真能跑起来 |
| 5 | 轮询 `/api/health` 直到 200 | 应用真的活着，不是"进程还在" |
| 6 | 冒烟：注册 → 登录 → 建待办 → 查列表 → 删除 | 制品功能可用，不是只有健康检查糊弄过去 |
| 7 | 失败即停，不自动回滚 | 自动回滚会掩盖问题；回滚由人执行（见下） |

**为什么迁移必须独立成步**：多副本同时启动会并发跑迁移。把 `migrate deploy` 塞进应用启动脚本，是本类项目最常见的生产事故来源。

**部署目标**：本机没有 Docker【实测】，所以 CD 的"部署"这一环设计成两段：
- **A 段（默认启用，现在就能真跑）**：把容器跑在 GitHub runner 上做**制品验证** —— 构建、启动、探活、冒烟。不依赖任何外部服务器
- **B 段（模板预留，默认不触发）**：真实部署到 VPS（SSH + compose），放在 `environment: production` 后面，需要配置 secrets。你哪天要给自己的服务器上线，填上 secrets 即可

**回滚方式**：`docker pull ghcr.io/<owner>/<repo>:sha-<上一个绿色提交>` 后重启容器。因为第 3 步的迁移在部署前跑，回滚前要确认该提交之后没有不可逆迁移（本 Demo 全是加表加列，安全）。

---

## 6. 验收标准（可判定）

**基础四步（本机可验证）**
- [ ] `npm ci && npm run lint && npm run typecheck && npm run test:unit && npm run build` 全部零错误
- [ ] 单测覆盖到：密码哈希往返、JWT 签发/过期/篡改、Zod 边界（空标题、超长标题、非法邮箱）

**仅 CI 可验证的两步**
- [ ] 集成测试通过：注册 → 登录 → 建待办 → 改状态 → 删除 全链路
- [ ] **双用户越权用例通过**：用户 A 的 token 访问用户 B 的 `todoId`，必须拿到 `404`；A 的列表里不含 B 的任何数据

**管线门禁本身要验证（关键！这三条验证的是管线，不是应用）**
- [ ] 故意在源码里塞一个类型错误 → CI 必须变红
- [ ] 故意让一条单测断言失败 → CI 必须变红
- [ ] 故意把隔离逻辑改成"能查到别人的待办" → 集成测试必须变红

> 只让管线"跑绿"是不够的。一条永远绿的管线和不存在的管线，可信度是一样的。必须证明它有拦住错误的能力。

**CD 门禁**
- [ ] `main` 分支产出镜像，两个 tag 都在 GHCR 里
- [ ] 镜像启动后 `/api/health` 返回 `200`，且响应时间 < 1s
- [ ] 冒烟用例在部署后的真实地址上通过
- [ ] 用上一个 `sha-` tag 重新部署，服务恢复正常

**效率基线**
- [ ] CI 从 push 到出结论 < 3 分钟
- [ ] CD 从合并到探活通过 < 5 分钟

---

## 7. 目录结构（规划）

```
.
├── .github/workflows/pipeline.yml   # CI + CD 两个 job 同文件
├── prisma/schema.prisma             # 数据模型
├── prisma/migrations/               # 迁移文件（入版本库）
├── src/app/                         # 页面 + API 路由
├── src/lib/                         # 认证、校验、db client（纯逻辑，便于单测）
├── tests/unit/                      # 不碰数据库
├── tests/integration/               # 打真 Postgres
├── Dockerfile                       # 多阶段、非 root、HEALTHCHECK
├── docker-compose.yml               # 本地/部署用，postgres + app
├── .env.example                     # 变量清单（真 .env 不进版本库）
└── DESIGN.md                        # 本文档
```

---

## 8. 已知限制与风险

**环境事实【实测】**
- 本机：Node 22.20.0、npm 10.9.3、pnpm 11.8.0、git 2.42.0
- **没有 Docker、没有 Postgres、没有 gh CLI，WSL 无发行版**
- npm 走腾讯云镜像可用；github.com 用 git 可达（PowerShell/curl 报 SSL 是本地 schannel 问题，不是网络不通）

**由此产生的限制**
1. 本机只能验证 lint / typecheck / 单测 / build 四步。集成测试与镜像构建**必须在 GitHub Actions 或装了 Docker 的机器上跑**
2. npm 默认缓存目录 `C:\Users\Luty\AppData\Local\npm-cache` 被文件沙箱拒绝写入（`EPERM`）。装依赖必须加 `--cache .npm-cache`（仓库内目录）。**这是硬约束，不是偏好**

**技术风险【推断】**
- Next 16 / TS 7 / ESLint 10 / Vitest 5 都属新大版本，组合可安装性与配置写法未经实测。缓解：开工第一步先装依赖跑通 lint + typecheck，失败就降级到上一稳定大版本，并更新本文档的版本矩阵
- Prisma 7 的 client 生成器配置在近几个大版本有过变动，schema 写法以 `prisma validate` 的实际报错为准，不照抄旧文档

**待清理**
- 仓库里现有 `.npm-cache/`（装依赖要用的缓存，保留并加进 `.gitignore`）和 `debug.log`（130 字节的 crashpad 噪声，可删）

---

## 9. 下一步

按依赖顺序：
1. 装依赖 + 跑通 lint/typecheck（**先验证版本组合可安装**，这是最大的不确定项）
2. 数据模型 + 迁移
3. 认证与待办接口 + 最小页面
4. 单测 + 集成测试
5. Dockerfile + compose + `.env.example`
6. `pipeline.yml`（CI job → CD job）
7. 本机跑四步验收；把仓库推上 GitHub，跑管线并执行 §6 的"故意弄红"三条验证
