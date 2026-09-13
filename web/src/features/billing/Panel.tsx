import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../api/core'
import { useDataChanged } from '../bus'
import { PlanPanel } from './PlanPanel'
import { billingApi, type BillingMe } from './api'

const POLL_ATTEMPTS = 5
const POLL_INTERVAL_MS = 1500

/**
 * Billing is optional, so this panel fails quietly: a server without payment
 * configured answers 503 and the rest of the app keeps working. The one thing it
 * does react to is the success redirect - the plan is granted by a webhook that
 * may land after the browser returns, so it re-checks instead of assuming.
 */
export function BillingPanel() {
  const [billing, setBilling] = useState<BillingMe | null>(null)
  const dataVersion = useDataChanged()

  const refresh = useCallback(async () => {
    try {
      setBilling(await billingApi.me())
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 401)) setBilling(null)
    }
  }, [])

  useEffect(() => {
    void (async () => {
      await refresh()
    })()
  }, [refresh, dataVersion])

  useEffect(() => {
    if (!window.location.search.includes('billing=success')) return
    let cancelled = false
    void (async () => {
      for (let attempt = 0; attempt < POLL_ATTEMPTS && !cancelled; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
        if (cancelled) return
        await refresh()
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refresh])

  if (!billing) return null
  return (
    <div className="card">
      <PlanPanel billing={billing} onRefresh={refresh} />
    </div>
  )
}
