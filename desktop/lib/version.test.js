// Unit tests for the update version comparison.
//
//   node --test desktop/lib
//
// The cases that matter are the ones that silently stop delivering updates: a
// two-digit patch (1.0.10 vs 1.0.9), a pre-release being offered to a stable
// install, and a feed that is behind the running build.

const test = require('node:test')
const assert = require('node:assert/strict')

const { compareVersions, isNewer } = require('./version')

test('equal versions compare as equal', () => {
  assert.equal(compareVersions('1.2.3', '1.2.3'), 0)
  assert.equal(compareVersions('v1.2.3', '1.2.3'), 0)
  assert.equal(compareVersions('1.2.3-0', '1.2.3-0'), 0)
})

test('patch, minor and major bumps are detected', () => {
  assert.ok(isNewer('1.0.2', '1.0.1'))
  assert.ok(isNewer('1.1.0', '1.0.9'))
  assert.ok(isNewer('2.0.0', '1.99.99'))
})

test('two-digit numbers compare numerically, not as strings', () => {
  assert.ok(isNewer('1.0.10', '1.0.9'), '1.0.10 must be newer than 1.0.9')
  assert.ok(!isNewer('1.0.9', '1.0.10'))
  assert.ok(isNewer('1.0.100', '1.0.99'))
})

test('a stable release outranks its pre-release', () => {
  assert.ok(isNewer('1.0.1', '1.0.1-beta.1'))
  assert.ok(!isNewer('1.0.1-beta.1', '1.0.1'))
  assert.ok(isNewer('1.0.1-beta.2', '1.0.1-beta.1'))
})

test('a feed that is behind the running build is not an update', () => {
  assert.ok(!isNewer('1.0.0', '1.0.27'))
  assert.ok(!isNewer('1.0.27', '1.0.27'))
})

test('missing or malformed segments do not crash or invent updates', () => {
  assert.equal(compareVersions('1.0', '1.0.0'), 0)
  assert.ok(isNewer('1.1', '1.0.9'))
  // Anything unparseable answers "no update": refusing to update is recoverable,
  // updating from a garbage feed is not.
  assert.equal(compareVersions('nonsense', '1.0.0'), null)
  assert.ok(!isNewer('nonsense', '1.0.0'))
  assert.ok(!isNewer('1.0.1', 'nonsense'))
  assert.ok(!isNewer('', '1.0.0'))
  assert.ok(!isNewer(null, '1.0.0'))
})
