import type { ReactElement } from 'react'

/**
 * The contract between the app shell and a business feature.
 *
 * The shell knows nothing about what a feature does: it renders `Panel` for a
 * signed-in user and hands over two ways to report back. Everything else - its
 * state, its endpoints, its components - stays inside the feature folder.
 */
export type FeatureProps = {
  /** Show (or clear) the app-level error line. */
  onError: (message: string | null) => void
  /** The session died mid-flight; the shell drops back to the sign-in panel. */
  onUnauthorized: () => void
}

export type Feature = {
  /** Stable key: also the React key and the panel's DOM order in the shell. */
  id: string
  Panel: (props: FeatureProps) => ReactElement | null
}
