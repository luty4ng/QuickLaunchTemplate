import type { Feature } from './types'
import { FEATURE as billing } from './billing'
import { FEATURE as todos } from './todos'

/**
 * The only place the skeleton learns what the business is.
 *
 * Migrating to another product means dropping a folder in here, adding one line,
 * and deleting the example features. Nothing else in `src/` outside
 * `src/features/**` may import a feature directly - `src/layering.test.ts` and
 * the `no-restricted-imports` rule in `eslint.config.js` both enforce that.
 *
 * Order is the order the panels appear in the shell.
 */
export const FEATURES: readonly Feature[] = [billing, todos]
