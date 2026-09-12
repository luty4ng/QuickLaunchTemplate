import { useState } from 'react'

import { ApiError, api, type BillingMe } from '../api'

/**
 * Plan and quota, plus the upgrade path.
 *
 * Two rules shape this component:
 *
 *  * **Prices are not hardcoded.** The plans and their limits come from the
 *    server, so changing a tier is not a front-end release.
 *  * **After paying, nothing is assumed.** Stripe confirms a subscription by
 *    webhook, which arrives after the browser returns, so the success case polls
 *    rather than declaring victory - and offers "already paid? check again" for
 *    the case where the webhook was lost.
 */
export function PlanPanel({
  billing,
  onRefresh,
}: {
  billing: BillingMe | null
  onRefresh: () => Promise<void>
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  if (!billing) return null

  const { quota, plan, plans } = billing
  const describe = (cause: unknown) =>
    cause instanceof ApiError ? cause.message : 'Could not reach the server.'

  const go = async (action: () => Promise<{ url: string }>, label: string) => {
    setBusy(label)
    setError(null)
    try {
      const { url } = await action()
      // Handing off to the provider's own page: card details never touch this app.
      window.location.assign(url)
    } catch (cause) {
      setError(describe(cause))
      setBusy(null)
    }
  }

  const checkAgain = async () => {
    setChecking(true)
    setError(null)
    try {
      await api.syncBilling()
      await onRefresh()
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setChecking(false)
    }
  }

  const used = `${quota.used} / ${quota.limit ?? '∞'}`
  const full = !quota.can_create

  return (
    <div className="plan">
      <div className="plan-row">
        <span>
          <strong>{plan === 'free' ? 'Free' : plan === 'plus' ? 'Plus' : 'Pro'}</strong>{' '}
          <span className="muted">
            {quota.limit === null ? `${quota.used} 条 · 无限额` : `${used} 条`}
          </span>
        </span>

        {billing.has_customer ? (
          <button
            className="secondary"
            disabled={busy !== null}
            onClick={() => void go(() => api.openPortal(), 'portal')}
          >
            {busy === 'portal' ? '打开中…' : '管理订阅'}
          </button>
        ) : null}
      </div>

      {billing.current_period_end && (
        <p className="muted" style={{ margin: 0 }}>
          {billing.cancel_at_period_end ? '到期后取消：' : '有效期至：'}
          {new Date(billing.current_period_end).toLocaleDateString()}
          {billing.status && billing.status !== 'active' ? ` · ${billing.status}` : ''}
        </p>
      )}

      {full && billing.billing_enabled && (
        <div className="plan-row">
          {plans
            .filter((candidate) => candidate.available && candidate.id !== plan)
            .map((candidate) => (
              <button
                key={candidate.id}
                disabled={busy !== null}
                onClick={() => void go(() => api.startCheckout(candidate.id), candidate.id)}
              >
                {busy === candidate.id
                  ? '跳转中…'
                  : `升级到 ${candidate.name}${candidate.limit === null ? '（无限）' : `（${candidate.limit} 条）`}`}
              </button>
            ))}
        </div>
      )}

      {!billing.billing_enabled && (
        <p className="muted" style={{ margin: 0 }}>
          本服务器未配置支付，额度上限仍然生效。
        </p>
      )}

      {quota.limit !== null && !full && quota.remaining !== null && quota.remaining <= 3 && (
        <p className="muted" style={{ margin: 0 }}>
          还可添加 {quota.remaining} 条。
        </p>
      )}

      {error && <p className="error">{error}</p>}

      {billing.billing_enabled && !billing.has_customer && (
        <button className="ghost" disabled={checking} onClick={() => void checkAgain()}>
          {checking ? '查询中…' : '已付款但未解锁？点这里重新核对'}
        </button>
      )}
    </div>
  )
}
