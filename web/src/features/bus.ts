import { useEffect, useState } from 'react'

/**
 * Cross-feature invalidation, deliberately business-agnostic.
 *
 * Features must not import each other, but they do have to react to each other:
 * creating a todo moves the quota that the billing panel displays. A feature
 * calls `publishDataChanged()` after a successful write; any feature that shows
 * derived data passes `useDataChanged()` into its effect dependencies and
 * re-reads. Topics would be clearer for a large app and are not worth the
 * vocabulary here - one extra GET is cheaper than a naming scheme.
 */
const listeners = new Set<() => void>()

/** Announce that server data changed. Safe to call with nobody listening. */
export function publishDataChanged(): void {
  for (const listener of [...listeners]) listener()
}

/** A counter that increments on every announcement; use it as an effect key. */
export function useDataChanged(): number {
  const [version, setVersion] = useState(0)

  useEffect(() => {
    const listener = () => setVersion((current) => current + 1)
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }, [])

  return version
}
