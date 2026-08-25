/**
 * ExceptionQueue — the owner decision queue.
 *
 * Every item is DERIVED FROM REAL DATA returned by /god/stats, /god/orgs and the
 * success/failure of /billing/all. Nothing here is hardcoded. If a condition is
 * not present in the data, the row does not render — and if none are present the
 * queue shows an honest empty state rather than filler.
 *
 * Each item answers: what happened · where · why it matters · what you can do.
 */
import { T, fmt, lastActivityLabel } from './godTheme'
import { Dot } from './StatusBadge'

const SEV = { critical: T.red, high: T.amber, info: T.blue }

/** Pure function so the same logic can be unit-tested or reused by a future alerts API. */
export function buildExceptions({ orgs = [], billingRows = null }) {
  const out = []
  const real = orgs.filter(o => o.id !== 'org-god-platform')

  // Real signal, not an assumption: an org can only be billed once it has a
  // Stripe customer. /billing/all reports that per org and succeeds without
  // Stripe, so a 200 from it proves nothing on its own.
  const rows = (billingRows || []).filter(o => o.id !== 'org-god-platform')
  const withPaymentMethod = rows.filter(o => !!o.stripe_customer_id).length
  if (Array.isArray(billingRows) && withPaymentMethod === 0) {
    out.push({
      id: 'billing-offline', sev: 'critical', title: 'Billing has never run',
      detail: `Not one of ${real.length} billable organizations has a Stripe customer, `
            + 'and there are no invoice or payment tables for charges to be written to. '
            + 'No revenue can be collected or measured in this state.',
      action: 'Set up billing', to: '/god/billing',
    })
  }

  const neverUsedEnterprise = real.filter(
    o => o.plan === 'enterprise' && !o.lead_count && !o.messages_30d && !o.last_activity
  )
  neverUsedEnterprise.forEach(o => out.push({
    id: 'ent-unused-' + o.id, sev: 'high',
    title: `${o.name} is on enterprise and has never been used`,
    detail: `${o.user_count} user(s) · 0 leads · 0 messages · no recorded activity. `
          + 'Either the highest-value account on the platform or a mis-set plan field.',
    action: 'Open account', org: o,
  }))

  const trials = real.filter(o => (o.plan || 'trial') === 'trial')
  if (trials.length) out.push({
    id: 'trials-no-expiry', sev: 'high',
    title: `${trials.length} organization${trials.length > 1 ? 's sit' : ' sits'} on trial with no expiry`,
    detail: trials.map(o => o.name).join(' · ')
          + '. Nothing in the schema records when a trial starts or ends, so no trial can convert or lapse on its own.',
    action: 'Assign packages', to: '/god/billing',
  })

  const idle = real.filter(o => o.lead_count > 500 && (o.messages_30d || 0) === 0)
  idle.forEach(o => out.push({
    id: 'idle-' + o.id, sev: 'high',
    title: `${o.name} holds ${fmt(o.lead_count)} leads and sent nothing in 30 days`,
    detail: `Last activity ${lastActivityLabel(o.last_activity)}. Health score ${o.health_score}/100. `
          + 'A tenant this size going quiet is the clearest churn signal available.',
    action: 'Enter org', org: o,
  }))

  const dormant = real.filter(o => !o.lead_count && !o.messages_30d && o.plan !== 'enterprise')
  if (dormant.length) out.push({
    id: 'dormant', sev: 'info',
    title: `${dormant.length} organization${dormant.length > 1 ? 's have' : ' has'} no data at all`,
    detail: dormant.map(o => o.name).join(' · ') + '. Provisioned but never onboarded.',
    action: 'Review', to: '/god/organizations',
  })

  const suspended = real.filter(o => !o.is_active)
  if (suspended.length) out.push({
    id: 'suspended', sev: 'info',
    title: `${suspended.length} organization${suspended.length > 1 ? 's are' : ' is'} suspended`,
    detail: suspended.map(o => o.name).join(' · '),
    action: 'Review', to: '/god/organizations',
  })

  const order = { critical: 0, high: 1, info: 2 }
  return out.sort((a, b) => order[a.sev] - order[b.sev])
}

export default function ExceptionQueue({ items, onAction, loading }) {
  if (loading) {
    return <div className="gm-card" style={{ padding: 26, textAlign: 'center', color: T.ghost, fontSize: 11 }}>
      Evaluating platform state…
    </div>
  }
  if (!items.length) {
    return <div className="gm-card" style={{ padding: 26, textAlign: 'center', color: T.teal, fontSize: 11 }}>
      No exceptions open. Every organization is inside normal operating bounds.
    </div>
  }
  return (
    <div className="gm-card" style={{ padding: 0, overflow: 'hidden' }}>
      {items.map(x => (
        <div key={x.id} className="gm-ex">
          <Dot color={SEV[x.sev]} glow={x.sev === 'critical'} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <b style={{ display: 'block', color: '#f1f7ff', fontSize: 12, fontWeight: 600 }}>{x.title}</b>
            <small style={{ display: 'block', color: '#5e7796', fontSize: 10, marginTop: 4, lineHeight: 1.55 }}>
              {x.detail}
            </small>
          </div>
          <button className="gm-btn" onClick={() => onAction?.(x)}>{x.action} →</button>
        </div>
      ))}
    </div>
  )
}
