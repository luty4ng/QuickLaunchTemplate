# 管线地图：13 个 job 分别在什么时候跑

> 这张图是给"看着乱"准备的。`.github/workflows/pipeline.yml` 里有 13 个 job、
> 4 种触发方式、5 个开关，但**任一次运行真正执行的通常只有 6–8 个**——
> 其余是"默认关掉的目标"与"只在发布时才走的路"。
> 相关：`report/PIPELINE-REVIEW.md`（发版产物与精简）、`report/AUTOMATION.md`（人工/自动化边界）。

---

## 1. 四种触发方式各跑什么

| 触发 | 会真正执行的 job | 结果 |
|---|---|---|
| **推分支**（`git push`） | `changes` `versioning` `verify-backend` `verify-web` `docker` `smoke-image` `desktop` | 验证 + 构建镜像 + 在 runner 上冒烟镜像 + 打包桌面端。**不发布、不部署** |
| **推 tag**（`git push origin v1.2.8`） | 上面全部 + `release` `update-feed` `deploy-server` `desktop-self-test` | 发布 Release（只有安装包）、发布更新源、部署上线并打公网地址冒烟、真跑打包后的客户端 |
| **每小时心跳**（`schedule`） | `auto-release` | 判断"是不是到了配置的发版时间、有没有新提交、那次提交的 CI 绿没绿"，都满足才派发一次 tag 式发布 |
| **手动 `workflow_dispatch`** | 按输入决定：给 `deploy_ref` 就只部署；给 `version` 就发版；给 `auto_release` 就跑发版判断（默认 dry run） | 用来演练与救火 |

## 2. 逐个 job：它是干什么的，什么时候不跑

| job | 目的 | 什么时候不跑 |
|---|---|---|
| `changes` | 路径过滤、发布开关、**项目标识漂移检查**（`project_env.py check`） | 从不跳过（它决定别人跳不跳） |
| `versioning` | 从 tag/输入取版本；tag 必须严格大于已发布最高版本 | 从不跳过 |
| `verify-backend` | ruff + 迁移可升可回滚（postgres 16）+ 全部后端测试 | 本次提交没碰后端/脚本/工作流 |
| `verify-web` | lint + typecheck + 前端测试 + 生产构建 | 本次提交没碰前端/桌面/移动端/工作流 |
| `docker` | 构建镜像并推 GHCR | pull_request，或上游失败 |
| `smoke-image` | 在 runner 上真起一套栈冒烟镜像（含支付链路） | 没有"进镜像"的改动（`deployable=false`）且不是在手动触发 |
| `desktop` | 打包 Windows 安装包（Linux 腿默认关，跑成绿色 no-op） | pull_request |
| `release` | 发布 Release：**只附安装包** | 不是 tag，也没给 `version` 输入 |
| `update-feed` | 把 `latest.yml` + `.blockmap` 写到服务器，并**在公网验证**安装包可下、Range=206、块文件在 | 不是发布（分支推送 / draft 演练） |
| `deploy-server` | SSH 部署到服务器 → 公网冒烟门禁 | 不是 tag，也没给 `deploy_ref` |
| `desktop-self-test` | 真启动打包后的应用，读**线上真实 feed** 并自报结论 | `release` 没成功（分支推送就是这种） |
| `android` | 打 APK | 开关没开（默认跳过） |
| `auto-release` | 定时发版决策 | 不是 schedule，也没手动传 `auto_release` |

## 3. 为什么图上看起来"多余"

* **`android / build apk`、`desktop / package (linux)`**：默认关闭的目标。它们仍然出现在图里，
  GitHub 没有"按开关隐藏 job"这回事——关掉时一个是 `skipped`，一个是**绿色 no-op**
  （刻意的：一条永远红的 `skipped` 腿会让每次默认发版看起来半残）。
* **`auto / decide whether to release`**：独立节点，因为它只由定时心跳或手动触发，
  和主链没有依赖关系。
* **右侧两个节点（`update-feed`、`desktop-self-test`）**：都要等 `release` 完成——
  一个负责把更新源发出去，一个负责真跑客户端读它。它们不能合并：一个是 Linux runner、
  一个是 Windows runner，而且需要不同的权限（前者要部署密钥，后者不要）。
* **`deploy-server` 不等 `desktop`**：部署只需要镜像和验证，不需要等 Windows 打包完，
  所以它在图上会先完成、然后右边还在跑。这是刻意的，不是漏了依赖。

## 4. 如果还想更清爽：三个可选方案（各有代价）

| 方案 | 效果 | 代价 |
|---|---|---|
| **A. 拆成两个 workflow 文件**：`pipeline.yml`（分支推送：changes/versioning/verify/docker/smoke-image）+ `release.yml`（tag 与手动：desktop/release/update-feed/self-test/deploy） | 平时推送看到的图只有 6 个节点，发布时才出现另一半 | 早期刻意选择的"单文件"被推翻；`release.yml` 要么重复一遍 verify、要么用 `workflow_run` 串联（后者更容易出错，当初就是为此选了单文件） |
| **B. 去掉"绿色 no-op"腿**，让关掉的目标显示成 `skipped` | 图里少一个"其实什么都没做"的节点 | 每次默认发版的图会带一个灰色 skipped 腿，观感更像半残——当初特意反过来做的 |
| **C. 把 `desktop`（Windows 打包）挪到发布流程里**，分支推送不再打包 | 分支推送从 ~13 分钟降到 ~2 分钟 | 分支推送就再也验证不到打包链路；打包问题要到发版时才发现 |

我的建议：**先什么都不改**。图的复杂度来自"这个模板确实覆盖了三端 + 真实部署 + 自动更新"，
不是来自冗余步骤；上面三个方案都是拿"某类问题更晚被发现"换"图更好看"。
真要动，A 的收益最大——但它值得单独做一次、单独验证一次。
