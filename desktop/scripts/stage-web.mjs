// Stages the built web bundle into desktop/web-dist so electron-builder can
// ship it as an extra resource. Fails loudly rather than packaging an empty app.
import { cp, rm, access } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = resolve(here, '..', '..', 'web', 'dist')
const target = resolve(here, '..', 'web-dist')

try {
  await access(resolve(source, 'index.html'))
} catch {
  console.error(`No web build at ${source}. Run "npm run build" in web/ first.`)
  process.exit(1)
}

await rm(target, { recursive: true, force: true })
await cp(source, target, { recursive: true })
console.log(`staged ${source} -> ${target}`)
