// 把 branding/ 里的品牌资源复制到 desktop/build/（electron-builder 的 buildResources）。
//
// 为什么要这一步，而不是直接把 buildResources 指向 ../branding：
// electron-builder 的资源路径按"项目目录内"解析，指到项目外在不同目标（NSIS/AppImage/deb）
// 上行为并不一致；复制一次是确定的，而且能顺手忽略 README 之类的非资源文件。
//
// 目录为空是**正常状态**：模板默认不带图标，electron-builder 会用它自己的默认图标。
// 所以这里只打印一行说明，绝不失败——为了一个可选资源让整条打包链路变红不值得。

import { cpSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const brandingDir = join(here, '..', '..', 'branding')
const buildDir = join(here, '..', 'build')

/** 资源文件后缀；README/说明文档不算资源。 */
const ASSET_SUFFIXES = ['.ico', '.png', '.bmp', '.icns', '.jpg', '.jpeg', '.svg']

export function assetFiles(directory) {
  if (!existsSync(directory)) return []
  return readdirSync(directory)
    .filter((name) => ASSET_SUFFIXES.some((suffix) => name.toLowerCase().endsWith(suffix)))
    .filter((name) => statSync(join(directory, name)).isFile())
    .sort()
}

export function stageBranding({ source = brandingDir, target = buildDir } = {}) {
  const assets = assetFiles(source)
  if (assets.length === 0) {
    console.log(`branding: ${source} 里没有资源文件，使用 electron-builder 的默认图标`)
    return []
  }
  mkdirSync(target, { recursive: true })
  for (const name of assets) {
    cpSync(join(source, name), join(target, name))
  }
  console.log(`branding: 复制 ${assets.length} 个资源到 ${target}: ${assets.join(', ')}`)
  return assets
}

// 直接运行（`node scripts/stage-branding.mjs`）时执行；被测试 import 时不执行。
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  stageBranding()
}
