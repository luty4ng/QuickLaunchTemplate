// Version comparison for the update check.
//
// Kept in its own module with no Electron imports so it can be unit tested: the
// "is this build older than what the release feed offers?" decision is the part
// that silently breaks, and it is worth pinning down.
//
// Only the subset the updater needs: numeric dotted versions with an optional
// pre-release suffix, compared by SemVer rules (1.0.10 > 1.0.9, and a
// pre-release ranks below its release: 1.0.1-beta.1 < 1.0.1).

/** Numeric version plus optional pre-release, or null when unparseable. */
function parse(value) {
  const text = String(value ?? '')
    .trim()
    .replace(/^v/, '')
  if (!/^\d+(\.\d+)*([.-][0-9A-Za-z.-]+)?$/.test(text)) return null

  const [core, ...rest] = text.split('-')
  const numbers = core.split('.').map((part) => Number.parseInt(part, 10))
  while (numbers.length < 3) numbers.push(0)
  return { numbers, prerelease: rest.join('-') || null }
}

function comparePrerelease(left, right) {
  if (left === right) return 0
  if (left === null) return 1 // a release outranks any pre-release
  if (right === null) return -1
  const leftParts = left.split('.')
  const rightParts = right.split('.')
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
    const a = leftParts[index]
    const b = rightParts[index]
    if (a === undefined) return -1
    if (b === undefined) return 1
    const aNumber = Number.parseInt(a, 10)
    const bNumber = Number.parseInt(b, 10)
    const bothNumeric = String(aNumber) === a && String(bNumber) === b
    if (bothNumeric) {
      if (aNumber !== bNumber) return aNumber < bNumber ? -1 : 1
    } else if (a !== b) {
      return a < b ? -1 : 1
    }
  }
  return 0
}

/** Returns -1, 0 or 1. Returns null when either side is not a version. */
function compareVersions(left, right) {
  const a = parse(left)
  const b = parse(right)
  if (!a || !b) return null
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) return a.numbers[index] < b.numbers[index] ? -1 : 1
  }
  return comparePrerelease(a.prerelease, b.prerelease)
}

/**
 * True only when `candidate` is a valid version that is strictly newer than
 * `current`. An unparseable value on either side answers false: refusing to
 * update is recoverable, updating from a garbage feed is not.
 */
function isNewer(candidate, current) {
  return compareVersions(candidate, current) === 1
}

module.exports = { compareVersions, isNewer, parse }
