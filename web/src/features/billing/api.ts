import { request } from '../../api/core'

export type Quota = {
  plan: string
  /** null means unlimited. */
  limit: number | null
  used: number
  remaining: number | null
  can_create: boolean
}

export type Plan = {
  id: 'plus' | 'pro'
  name: string
  limit: number | null
  price_id: string
  available: boolean
}

export type BillingMe = {
  plan: string
  quota: Quota
  status: string | null
  current_period_end: string | null
  cancel_at_period_end: boolean
  has_customer: boolean
  plans: Plan[]
  billing_enabled: boolean
}

export const billingApi = {
  me: () => request<BillingMe>('/api/billing/me'),
  /** The plan name, not a price: what it costs is decided by the server. */
  checkout: (plan: Plan['id']) =>
    request<{ url: string }>('/api/billing/checkout', {
      method: 'POST',
      body: JSON.stringify({ plan }),
    }),
  portal: () => request<{ url: string }>('/api/billing/portal', { method: 'POST' }),
  /** Reconcile with the provider, for when a webhook never arrived. */
  sync: () =>
    request<{ plan: string; status: string | null; changed: boolean }>('/api/billing/sync', {
      method: 'POST',
    }),
}
