// 品牌资源复制的测试：这块很小，但坏了的表现是"安装包突然换成默认图标"——
// 不会报错、不会变红，只是产品看起来不像你的产品。所以给它三条断言。

import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { assetFiles, stageBranding } from './stage-branding.mjs'

function scratch() {
  return mkdtempSync(join(tmpdir(), 'ql-branding-'))
}

test('目录为空时返回空列表，不抛异常', () => {
  const source = join(scratch(), 'branding')
  assert.deepEqual(assetFiles(source), [])
  assert.deepEqual(stageBranding({ source, target: join(scratch(), 'build') }), [])
})

test('只挑资源文件，忽略说明文档', () => {
  const root = scratch()
  const source = join(root, 'branding')
  mkdirSync(source)
  writeFileSync(join(source, 'README.md'), '不是资源')
  writeFileSync(join(source, 'icon.ico'), 'ico')
  writeFileSync(join(source, 'icon.png'), 'png')

  assert.deepEqual(assetFiles(source), ['icon.ico', 'icon.png'])
})

test('复制到目标目录，且目录会被创建', () => {
  const root = scratch()
  const source = join(root, 'branding')
  const target = join(root, 'desktop', 'build')
  mkdirSync(source)
  writeFileSync(join(source, 'icon.ico'), 'ico-bytes')

  const copied = stageBranding({ source, target })

  assert.deepEqual(copied, ['icon.ico'])
  assert.ok(existsSync(join(target, 'icon.ico')), '资源应当被复制到 buildResources')
  assert.deepEqual(readdirSync(target), ['icon.ico'])
})
