// Stamp the desktop package version before packaging.
//
// electron-updater only offers an update when the version published in the
// release is strictly newer than the one installed, so shipping every build as
// "1.0.0" (as the demo did at first) means installed clients never update. CI
// derives the version from the run number and this writes it into package.json
// for electron-builder to bake into the app and into latest.yml.
//
//   node scripts/stamp-version.mjs 1.0.42
//   node scripts/stamp-version.mjs 0.0.42 --note "older on purpose, to test the update path"
//
// The difference between the two is the whole point of the flag: a normal build
// must be newer than what users have, a test build must be older than the
// published release so the feed has something newer to offer.
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SEMVER = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/

const here = dirname(fileURLToPath(import.meta.url))
const manifestPath = resolve(here, '..', 'package.json')

const version = process.argv[2]
if (!version) {
  console.error('usage: node scripts/stamp-version.mjs <semver>')
  process.exit(2)
}
if (!SEMVER.test(version)) {
  console.error(`refusing to stamp "${version}": not a semver version`)
  process.exit(2)
}

const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
const previous = manifest.version
manifest.version = version
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')

console.log(`stamped desktop version ${previous} -> ${version}`)
