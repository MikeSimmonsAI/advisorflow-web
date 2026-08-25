/**
 * RevenueMetrics — Revenue & Accounts band.
 *
 * ── What GET /billing/all actually returns (verified Aug 25 2026) ──
 *   { orgs: [{ id, name, slug, plan, billing_status, stripe_customer_id,
 *              stripe_subscription_id, stripe_plan_interval, is_active }] }
 *
 * It returns NO monetary figures, and it succeeds even with no Stripe key
 * because it only reads columns off `organizations`. So a 200 from it does NOT
 * mean billing works.
 *
 * MRR / Collected / Past Due therefore have NO source and always render
 * "no source" until the invoices + payments tables exist. Do not wire them to
 * anything that cannot actually produce a currency amount.
 *
 * Payment-method coverage IS real — it is stripe_customer_id being non-null.
 */
import { T } from './godTheme'
import { NoSource } from './StatusBadge'

function Metric({ label, value, note, noteTone, tone = '', onClick, title }) {
  const clickable = typeof onClick === 'function'
  return (
    <div
      className={`gm-card gm-metric ${tone} ${clickable ? 'gm-click' : ''}`}
      onClick={onClick} title={title}
      role={clickable ? 'button' : undefined} tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } } : undefined}
    >
      <label style={{ fontSize: 8, letterSpacing: '.14em', color: '#57739b', display: 'block', fontWeight: 700 }}>
        {label}
      </label>
      {typeof value === 'string' || typeof value === 'number'
        ? <strong style={{ display: 'block', fontSize: 30, letterSpacing: '-.045em', color: '#fff', marginTop: 10, lineHeight: 1 }}>{value}</strong>
        : <span style={{ display: 'block', fontSize: 13, marginTop: 14, lineHeight: 1 }}>{value}</span>}
      {note && <small style={{ display: 'block', fontSize: 9, marginTop: 8, color: noteTone || '#56708f' }}>{note}</small>}
    </div>
  )
}

/** Derives the real, currently-knowable billing facts. Exported so the exception
 *  queue and the metrics band cannot drift apart. */
export function billingFacts({ orgs = [], billingRows = null }) {
  const real = orgs.filter(o => o.id !== 'org-god-platform')
  const rows = (billingRows || []).filter(o => o.id !== 'org-god-platform')
  const withPaymentMethod = rows.filter(o => !!o.stripe_customer_id).length
  return {
    billable: real.length,
    onTrial:  real.filter(o => (o.plan || 'trial') === 'trial').length,
    unpriced: real.filter(o => !o.plan || o.plan === 'trial').length,
    withPaymentMethod,
    // No invoice model exists, so billing cannot be "running" regardless of Stripe.
    billingConfigured: withPaymentMethod > 0,
    reachedBillingApi: Array.isArray(billingRows),
  }
}

export default function RevenueMetrics({ orgs, billingRows, loading, onDrill }) {
  const f = billingFacts({ orgs, billingRows })
  const pmNote = !f.reachedBillingApi
    ? 'billing API unreachable'
    : `${f.withPaymentMethod} with payment method`

  return (
    <div style={{ display: 'grid', gap: 12, marginBottom: 12, gridTemplateColumns: 'repeat(auto-fit,minmax(165px,1fr))' }}>
      <Metric label="MRR" tone="gm-pend" value={<NoSource />}
              note="needs invoices table" title="No monetary source exists yet. /billing/all returns no amounts." />
      <Metric label="COLLECTED · 30D" tone="gm-pend" value={<NoSource />}
              note="needs payments table" />
      <Metric label="PAST DUE" tone="gm-pend" value={<NoSource />}
              note="needs invoices table" title="Requires invoice due dates, which are not stored anywhere." />
      <Metric label="BILLABLE ORGS" tone={f.billingConfigured ? '' : 'gm-crit'}
              value={loading ? '·' : f.billable}
              note={pmNote} noteTone={f.withPaymentMethod === 0 ? T.red : undefined}
              onClick={() => onDrill?.('orgs')}
              title="Every organization except the god platform org" />
      <Metric label="ON TRIAL" tone="gm-warn" value={loading ? '·' : f.onTrial}
              note="no expiry date stored" noteTone={T.amber}
              onClick={() => onDrill?.('trial')}
              title="organizations.plan = 'trial'. There is no trial_ends_at column." />
      <Metric label="UNPRICED" tone="gm-warn" value={loading ? '·' : f.unpriced}
              note="no package assigned" noteTone={T.amber}
              onClick={() => onDrill?.('trial')} />
    </div>
  )
}
