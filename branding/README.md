# branding/ —— 品牌与业务资源的唯一去处

模板迁移到新项目时，**这个目录整个换掉**：图标、安装器位图、以及将来任何"看起来像你的产品"的东西，
都放在这里，不要散落到 `web/`、`desktop/`、`mobile/` 里各放一份。

名字（`PROJECT_NAME` / `PROJECT_SLUG` / `APP_DOMAIN` / 包名）不在这里——它们在仓库根的
`project.env`，由 `scripts/project_env.py check` 保证全仓库一致。这个目录只管**二进制资源**。

## 放什么

| 文件 | 用在哪 | 要求 |
|---|---|---|
| `icon.ico` | Windows 安装包、开始菜单、任务栏、`Setup.exe` 的图标 | 多尺寸 ICO，**必须包含 256×256**（否则 electron-builder 会报错） |
| `icon.png` | Linux 包（AppImage/deb）与桌面条目 | 512×512 或更大，方形 |
| `installerIcon.ico`（可选） | 安装器自身的图标，缺省时用 `icon.ico` | 同 `icon.ico` |
| `installerHeader.bmp`（可选） | NSIS 安装器顶部横幅 | 150×57 BMP |
| `installerSidebar.bmp`（可选） | NSIS 安装器侧边栏 | 164×314 BMP |

放进来就会生效：打包前 `desktop/scripts/stage-branding.mjs` 会把本目录里的资源文件复制到
`desktop/build/`（electron-builder 的 `buildResources` 目录）。**目录为空也没关系**——
脚本会打印一行说明并继续，此时用 electron-builder 的默认图标（当前就是这种状态）。

## 还没接进来的

* **安卓图标**：`mobile/android/` 原生工程由 CI 在构建时生成，图标需要在那一步注入
  （`capacitor-assets` 或直接在 CI 里覆盖 `res/mipmap-*`）。目前 APK 用的是 Capacitor 默认图标，
  且安卓端默认关闭——要做的话在 `.github/workflows/pipeline.yml` 的 `android` job 里加一步。
* **网页端 favicon**：目前 `web/index.html` 内联了一个极小图标；要换成品牌图标，
  把文件放这里，再在构建时复制到 `web/public/`（同样建议走一个 stage 脚本，而不是手工放两份）。
* **桌面端窗口图标**：`desktop/main.js` 目前用打包进资源的默认图标；接进来的话同样从这里取。

## 为什么单独一个目录

因为"这是谁的产品"这三件事（名字、域名、图标）在迁移时是**同时**要换的，而它们的落点天然分散：
名字在 `project.env`、域名在 `project.env`、图标在打包配置里。把前两者收进 `project.env`、
把第三者收进这里，迁移就只剩两处要看——`MIGRATION.md` 里也是这么写的。
