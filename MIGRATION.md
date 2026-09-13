# 一键迁移：把这份模板变成你的项目

> 目标读者：拿到这个仓库、想把它变成自己项目的人（也是三个月后的你自己）。
> 原则：**能脚本化的都脚本化，剩下的人工步骤必须写清楚"为什么必须人工"。**
> 相关文档：`report/PIPELINE-REVIEW.md`（发版产物与配置全表）、`report/PIPELINE-MAP.md`（管线地图）、
> `report/AUTOMATION.md`（人工/自动化边界）。

---

## 0. 这份模板当前的分层（迁移前先看懂）

```
project.env              项目身份：名字、slug、仓库、域名、包名（唯一事实来源，无密钥）
branding/                品牌资源：图标、安装器位图（**迁移时整个换掉**）
backend/app/
  config.py deps.py main.py schemas.py security.py   骨架：设置、依赖、应用工厂、契约、密码/JWT
  db/                                                骨架：引擎、会话、Base、（users 表）
  routers/auth|health|updates                        骨架：认证、健康、桌面端更新源
  features/todos/                                    示例业务 1：待办 + 配额
  features/billing/                                  示例业务 2：Stripe 订阅
web/src/                 前端（骨架 + 示例业务的组件；分层进行中，见 §4）
desktop/ mobile/         Electron 外壳 / Capacitor 配置
deploy/                  compose 覆盖文件、deploy.sh、（bootstrap-server.sh）
scripts/                 smoke.py（部署门禁）、project_env.py、make_feed.py、check_version.py …
.github/workflows/       一条流水线
```

边界是**单向**的：功能包可以导入骨架，骨架**不许**导入功能包。这条规则由
`backend/tests/unit/test_layering.py` 用 AST 守着——不是靠人记得。

---

## 1. 三条命令改名（脚本化）

```bash
# 1) 一次性改掉域名/仓库/镜像名/包名，并自动跑一遍自检
python scripts/project_env.py bootstrap \
  --repo <owner>/<repo> --domain <新域名> --name "<显示名>" --slug <小写短名> --write

# 2) 自检（CI 每次推送也会跑同一条）
python scripts/project_env.py check

# 3) 看一眼解析结果：镜像名、目录名、更新源地址都由它派生
python scripts/project_env.py show
```

`bootstrap` 默认是 dry run，`--write` 才落盘。它同时会替换 release 说明里的产品名、
`desktop/package.json` 的 `publish.owner/repo`、`mobile/capacitor.config.json` 的包名等
十余处——**漏改的后果并不均等**：漏 `deploy.sh` 的仓库地址是部署失败（还算好），
漏 electron-builder 的 `publish.owner` 是桌面端自动更新静默失效。

---

## 2. 服务器一键准备（脚本化）

```bash
# 在目标服务器上（仓库同步过去之后）：
bash deploy/bootstrap-server.sh --check                    # 只体检，不改任何东西
sudo bash deploy/bootstrap-server.sh --install-docker      # 装 docker + compose 插件
bash deploy/bootstrap-server.sh                            # 建网络、建目录、生成 .env
bash deploy/bootstrap-server.sh --with-traefik --acme-email you@example.com   # 裸机才需要
```

它会做：装/检查 docker 与 compose 插件 → 建外部网络 `traefik-net` → 建 `~/<slug>/` →
**生成 `.env`**（JWT_SECRET、POSTGRES_PASSWORD 在服务器本地生成，绝不过 CI）→ 打印剩余人工步骤。
**绝不覆盖已存在的 `.env`**，重复执行不会改动任何东西。

> 注意：`--with-traefik` 只在"这台机器上还没有 Traefik"时才用；如果 80/443 已被占用，
> 脚本会拒绝继续，而不是去抢端口。已有 Traefik 的机器只要它带一个名为 `letsencrypt`
> 的证书 resolver 和 `traefik-net` 网络即可（`deploy/compose.server.yaml` 就是这么引用的）。

---

## 3. 剩下的四件人工事（必须人工，且理由明确）

| # | 做什么 | 为什么必须人工 |
|---|---|---|
| 1 | **DNS**：把域名 A 记录指向服务器 | 一次性；用 API 自动化要长期维护一组 DNS 凭据，不划算 |
| 2 | **部署公钥**：把公钥追加进服务器 `~/.ssh/authorized_keys` | 私钥不该经过 CI 或任何第三方——这一步的"麻烦"就是它的安全边界 |
| 3 | **GitHub**：Secret `DEPLOY_SSH_KEY`，Variables `DEPLOY_HOST` / `DEPLOY_USER` | 同上：值由人给 |
| 4 | **（可选）Stripe**：建 Product + 两个 Price、开客户门户，把 `sk_*` / `price_*` / `whsec_*` 写进服务器 `.env` | 密钥只能人工写入；不配也能跑（`/api/billing/*` 返回 503，界面有明确提示） |

然后是**打一个 tag**（如 `v0.1.0`）——剩下的验证、构建、发布、部署、公网冒烟全自动。

---

## 4. 换成你自己的业务（这一步是"改代码"，但只需要改一个目录）

### 后端

```bash
rm -rf backend/app/features/todos backend/app/features/billing
mkdir -p backend/app/features/<你的功能>/
# 最小形态：
#   __init__.py   FEATURE = Feature(name="<你的功能>", router=router)
#   router.py     router = APIRouter(prefix="/<你的前缀>", tags=["<你的功能>"])
#   models.py     Base 子类（alembic 通过 app.features 的导入看到它）
#   schemas.py    请求/响应契约
#   settings.py   这个功能的环境变量（可选）
```

然后在 `backend/app/features/__init__.py` 的 `FEATURES` 里换掉那两项。
**不需要动 `main.py`**——它只调用 `features.load(app)`。

数据库迁移照样写：`alembic revision --autogenerate -m "..."`，
流水线会验证"能升级 + 能回滚 + 在 postgres 16 上跑得通"。

### 前端

结构与后端一一对应：

```
web/src/
  api/core.ts        骨架：request 封装、错误映射、会话、更新桥、API 基址
  components/        骨架 UI：AuthPanel（登录）、UpdateBanner（桌面更新条）
  features/
    index.ts         注册表——骨架与业务之间唯一的接口（按顺序即面板顺序）
    types.ts         Feature / FeatureProps 契约（onError、onUnauthorized）
    bus.ts           跨功能失效通知（业务之间不互相 import）
    todos/           api.ts + TodoList.tsx + Panel.tsx + index.ts
    billing/         api.ts + PlanPanel.tsx + Panel.tsx + index.ts
  App.tsx            壳层：会话、健康徽标、错误行、渲染注册表
```

换业务的动作只有两步：删掉 `todos/`、`billing/` 两个目录，注册表里换掉那两行
（新功能只要导出 `FEATURE = { id, Panel }`，面板自己管状态、自己调自己的 `api.ts`）。
`src/layering.test.ts` 扫描源码，骨架文件或兄弟功能 import 具体功能就会变红；
`src/App.test.tsx` 真实渲染壳层，断言"注册表里有什么就渲染什么"——两个测试都不认识示例业务。

### 演示数据与文案

* `scripts/smoke.py` 里有示例业务的断言（待办增删改查、配额、计费）——换成你的接口与断言；
  健康检查、注册登录、租户隔离、更新源那几段是骨架的，可以留着。
* `README.md` / `report/*` 是 QuickLaunch 的溯源材料，迁移时重写。

---

## 5. 验收清单（照着做，每步都有可验证的输出）

| 步骤 | 验收命令 | 期望 |
|---|---|---|
| 改名 | `python scripts/project_env.py check` | `project identity agrees with project.env (8 files checked)` |
| 后端骨架完好 | `cd backend && pytest tests -q` | 全绿；含 `tests/unit/test_layering.py` |
| 迁移可逆 | `alembic upgrade head && alembic downgrade base && alembic upgrade head` | 三步都成功 |
| 前端 | `cd web && npm run lint && npm run typecheck && npm test && npm run build` | 全绿；含 `src/layering.test.ts` 分层守卫与 `src/App.test.tsx` 渲染冒烟 |
| 服务器 | `bash deploy/bootstrap-server.sh --check` | 全部 ✓ |
| 首次发版 | `git tag -a v0.1.0 -m "first" && git push origin v0.1.0` | 流水线全绿：Release 只有安装包、更新源公网可读且 Range=206、公网冒烟通过 |
| 更新源 | `curl -s https://<域名>/updates/latest.yml` | `version: <你的版本>` |

---

## 6. 还不完美的地方（诚实清单）

1. **`users` 表上还留着 billing 的列**（`plan`、`stripe_*`）：拆表要先做一次数据迁移，
   单独做。骨架不读这些列，所以删掉 features/ 之后它们只是几列没人碰的字段。
2. **`smoke.py` 的业务断言没有分离**：目前"骨架检查"与"示例业务检查"在同一个文件里，
   迁移时要手工挑。
3. **`bootstrap-server.sh` 尚未在裸机上实测**：只在一台已经准备好的服务器上验证过
   幂等性（重复执行为空操作）与 `--check`。
4. **安卓图标未接入** `branding/`：原生工程由 CI 生成，注入图标要再加一步。
5. **前端品牌字样还是字面量**：`web/src/App.tsx` 的 `QuickLaunch`、`web/index.html` 的
   `<title>`、`web/package.json` 的 `name` 都不在 `project_env.py check` 的 8 个文件里，
   改名时容易漏（后果只是显示名不对，不影响运行）。最省事的补法是把这三个文件纳入
   `CHECKED_FILES`，或让 Vite 用 `VITE_APP_NAME` 注入。
