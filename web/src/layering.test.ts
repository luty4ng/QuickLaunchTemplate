import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { describe, expect, it } from 'vitest'

import { FEATURES } from './features'

/**
 * The skeleton / business boundary, kept honest by reading the source.
 *
 * Same rule as `backend/tests/unit/test_layering.py`, for the same reason: the
 * template is meant to be migrated by swapping the business, so "no skeleton file
 * imports a feature" has to be a failing test rather than a convention. The
 * registry (`src/features/index.ts`) is the single allowed link, and a feature
 * may import its own files but never a sibling feature's.
 */

const SRC = join(process.cwd(), 'src')
const FEATURES_DIR = join(SRC, 'features')
const REGISTRY = join(FEATURES_DIR, 'index.ts')

/** `from 'x'`, `export ... from 'x'`, `import 'x'`, `import('x')`. */
const IMPORT = /(?:from\s*|import\s*\(?\s*)['"]([^'"]+)['"]/g

const posix = (path: string) => path.split(sep).join('/')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : []
  })
}

function featuresOnDisk(): string[] {
  return readdirSync(FEATURES_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
}

describe('skeleton / feature boundary', () => {
  it('gives every feature folder a FEATURE export and registers it', async () => {
    const names = featuresOnDisk()
    expect(names.length).toBeGreaterThan(0)

    for (const name of names) {
      const module = (await import(`./features/${name}/index.ts`)) as {
        FEATURE?: { id?: string; Panel?: unknown }
      }
      expect(module.FEATURE, `${name} must export FEATURE`).toBeTruthy()
      expect(typeof module.FEATURE?.Panel, `${name}: FEATURE.Panel must be a component`).toBe('function')
      expect(
        FEATURES.some((feature) => feature.id === module.FEATURE?.id),
        `${name} is missing from FEATURES in src/features/index.ts`,
      ).toBe(true)
    }
  })

  it('registers each feature exactly once', () => {
    expect(new Set(FEATURES.map((feature) => feature.id)).size).toBe(FEATURES.length)
  })

  it('lets only the registry name a feature folder', () => {
    const names = featuresOnDisk()
    const offenders: string[] = []

    for (const file of sourceFiles(SRC)) {
      const self = posix(relative(SRC, file))
      for (const [, specifier] of readFileSync(file, 'utf8').matchAll(IMPORT)) {
        if (!specifier?.startsWith('.')) continue
        const target = posix(relative(SRC, resolve(dirname(file), specifier)))
        const owner = names.find(
          (name) => target === `features/${name}` || target.startsWith(`features/${name}/`),
        )
        if (!owner || file === REGISTRY) continue
        // A feature reading its own files is the point; a sibling is not.
        if (!self.startsWith(`features/${owner}/`)) offenders.push(`${self} -> ${specifier}`)
      }
    }

    expect(offenders).toEqual([])
  })
})
