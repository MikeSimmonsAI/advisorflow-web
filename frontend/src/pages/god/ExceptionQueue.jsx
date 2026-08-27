/**
 * OWNER ACTION QUEUE — what needs a decision from the platform owner.
 *
 * This file is the former ExceptionQueue, widened to the approved design. It
 * kept its name and its export so there is still exactly ONE queue in the
 * product; the Command Center's alert count and this list are the same
 * function called once.
 *
 * ── THE RULE THAT HAS NOT CHANGED ──────────────────────────────────────────
 * EVERY ROW IS DERIVED FROM REAL DATA. Nothing here is seeded, sampled or
 * padded. If a condition is not present in the payloads, the row does not
 * render, and if none are present the queue says so rather than showing filler.
 *
 * ── Inputs (all already loaded by the Command Center) ──────────────────────
 *   orgs        GET /god/orgs                        health, activity, counts
 *   customers   GET /god/ops/customer-organizations  package, implementation
 *   billingRows GET /billing/all                     payment-method coverage
 *
 * Each row answers: what · where · why it matters · how old · what to do, and
 * carries a drilldown that goes to the affected resource.
 */
import { T, fmt, lastActivityLabel, daysAgo } from './godTheme'

const PSEUDO = 'org-god-platform'

const SEV_COLOR = { critical: T.red, high: T.amber, medium: T.blue, info: '#5d7697' }
const SEV_ORDER = { critical: 0, high: 1, medium: 2, info: 3 }

function ageLabel(iso) {
  const d = daysAgo(iso)
  if (d === null) return null
  if (d === 0) return 'today'
  if (d === 1) return '1 day'
  if (d < 60) return d + ' days'
  return Math.floor(d / 30) + ' months'
}

/**
 * Pure. Exported so the executive summary's alert count and this list cannot
 * drift apart, and so the derivation is testable without a browser.
 */
export function buildExceptions({ orgs = [], customers = [], billingRows = null }) {
  const out = []
  const real = (orgs || []).filter(o => o.id !== PSEUDO)
  const byId = {}
  real.forEach(o => { byId[o.id] = o })
  const cust = (customers || []).filter(c => c.organization_id !== PSEUDO)

  const push = (x) => out.push(x)
  const orgLabel = (o) => (o && o.name) || 'Unknown organization'

  // ── billing: nothing can be charged ──────────────────────────────────────
  const rows = (billingRows || []).filter(o => o.id !== PSEUDO)
  const withPaymentMethod = rows.filter(o => !!o.stripe_customer_id).length
  if (Array.isArray(billingRows) && rows.length && withPaymentMethod === 0) {
    push({
      id: 'billing-offline', sev: 'critical',
      org_name: 'Platform-wide',
      title: 'Billing has never run',
      detail: `Not one of ${rows.length} billable organizations has a payment method, `
            + 'and there are no invoice or payment tables for charges to be written to. '
            + 'No revenue can be collected or measured in this state.',
      action: 'Review organizations', to: '/god/organizations',
    })
  }

  // ── per-customer conditions, from the customer-organizations payload ─────
  cust.forEach(c => {
    const o = byId[c.organization_id]
    const bill = rows.find(b => b.id === c.organization_id)
    const age = ageLabel(c.created_at)

    // implementation blocked — the owner is the escalation path
    if (c.implementation && c.implementation.status === 'blocked') {
      push({
        id: 'impl-blocked-' + c.organization_id, sev: 'critical',
        org_name: c.name, age,
        title: `${c.name}'s implementation is blocked`,
        detail: 'Onboarding cannot proceed without an owner decision. Open the '
              + 'implementation to see which milestone is holding it.',
        action: 'Open implementation', to: '/god/implementations/' + c.implementation.id,
      })
    }

    // customer missing a package — revenue cannot be forecast
    if (!c.package) {
      push({
        id: 'no-package-' + c.organization_id, sev: 'high',
        org_name: c.name, age,
        title: `${c.name} has no package assigned`,
        detail: 'Plan field reads "' + (c.plan || 'none') + '". Until a package is '
              + 'assigned there is no agreed price for this customer and nothing '
              + 'to forecast or invoice against.',
        action: 'Assign package', to: '/god/customers/' + c.organization_id,
      })
    }

    // billing exception — real, from the org's own Stripe columns
    if (bill && !bill.stripe_customer_id && c.is_active) {
      push({
        id: 'no-pm-' + c.organization_id, sev: 'high',
        org_name: c.name, age,
        title: `${c.name} has no payment method`,
        detail: 'Billing status reads "' + (bill.billing_status || 'none') + '". '
              + 'This customer is live and cannot be charged.',
        action: 'Open customer', to: '/god/customers/' + c.organization_id,
      })
    } else if (bill && bill.billing_status === 'past_due') {
      push({
        id: 'past-due-' + c.organization_id, sev: 'high',
        org_name: c.name, age,
        title: `${c.name} is past due`,
        detail: 'Stripe reports this subscription as past_due.',
        action: 'Open customer', to: '/god/customers/' + c.organization_id,
      })
    }

    // incomplete onboarding — provisioned but never launched
    if (c.implementation && !c.implementation.is_live
        && c.implementation.status !== 'blocked') {
      push({
        id: 'impl-open-' + c.organization_id, sev: 'medium',
        org_name: c.name, age,
        title: `${c.name} is still onboarding`,
        detail: 'Implementation status is "'
              + String(c.implementation.status || '').replace(/_/g, ' ')
              + '". It has not been marked live, so the customer is not yet in '
              + 'normal operation.',
        action: 'Open implementation', to: '/god/implementations/' + c.implementation.id,
      })
    }

    // configuration gap — an org with no platform sits outside every scope
    if (!c.platform) {
      push({
        id: 'no-platform-' + c.organization_id, sev: 'high',
        org_name: c.name, age,
        title: `${c.name} belongs to no brand`,
        detail: 'An organization with no platform sits outside every scoping '
              + 'decision in the system, including the customer list of whoever '
              + 'is meant to own it.',
        action: 'Open customer', to: '/god/customers/' + c.organization_id,
      })
    }

    // user / access issue — nobody can actually use it
    if (c.is_active && (c.user_count || 0) === 0) {
      push({
        id: 'no-users-' + c.organization_id, sev: 'high',
        org_name: c.name, age,
        title: `${c.name} is active with no user accounts`,
        detail: 'Nobody can sign in to this organization. Either it was activated '
              + 'before its people were added, or the invitations were never issued.',
        action: 'Add people', to: '/god/customers/' + c.organization_id,
      })
    }

    // configuration gap — never came through the sales pipeline
    if (!c.provisioned_from_sale && c.is_active) {
      push({
        id: 'off-pipeline-' + c.organization_id, sev: 'info',
        org_name: c.name, age,
        title: `${c.name} was created outside the pipeline`,
        detail: 'There is no implementation record, so this customer has no '
              + 'onboarding checklist, no launch gate and no linked opportunity.',
        action: 'Open customer', to: '/god/customers/' + c.organization_id,
      })
    }

    // suspended
    if (!c.is_active) {
      push({
        id: 'suspended-' + c.organization_id, sev: 'info',
        org_name: c.name, age,
        title: `${c.name} is suspended`,
        detail: 'Users cannot sign in. This is reversible from the organization table.',
        action: 'Open customer', to: '/god/customers/' + c.organization_id,
      })
    }

    // communication failure — holding leads and sending nothing
    if (o && (o.lead_count || 0) > 100 && (o.messages_30d || 0) === 0 && c.is_active) {
      push({
        id: 'silent-' + c.organization_id, sev: 'high',
        org_name: c.name, age: ageLabel(o.last_activity) || age,
        title: `${c.name} holds ${fmt(o.lead_count)} leads and sent nothing in 30 days`,
        detail: `Last activity ${lastActivityLabel(o.last_activity)}. Health score `
              + `${o.health_score}/100. A tenant this size going quiet is the `
              + 'clearest churn signal available.',
        action: 'Enter organization', org: o,
      })
    }

    // dormant — provisioned, never onboarded
    if (o && !o.lead_count && !o.messages_30d && !o.last_activity && c.is_active) {
      push({
        id: 'dormant-' + c.organization_id, sev: 'medium',
        org_name: c.name, age,
        title: `${c.name} has never been used`,
        detail: `${o.user_count || 0} user account(s) · 0 leads · 0 messages · no `
              + 'recorded activity since it was created.',
        action: 'Enter organization', org: o,
      })
    }
  })

  // ── trials with no expiry: a schema fact, stated once, not per customer ──
  const trials = real.filter(o => (o.plan || 'trial') === 'trial')
  if (trials.length) {
    push({
      id: 'trials-no-expiry', sev: 'medium',
      org_name: `${trials.length} organization${trials.length > 1 ? 's' : ''}`,
      title: `${trials.length} organization${trials.length > 1 ? 's sit' : ' sits'} on trial with no expiry date`,
      detail: trials.map(o => o.name).join(' · ')
            + '. Nothing in the schema records when a trial starts or ends, so no '
            + 'trial can convert or lapse on its own.',
      action: 'Review organizations', to: '/god/organizations',
    })
  }

  // Orgs the customer payload did not cover still get their health surfaced
  // rather than silently disappearing.
  const covered = new Set(cust.map(c => c.organization_id))
  real.filter(o => !covered.has(o.id) && o.health_score < 60).forEach(o => {
    push({
      id: 'health-' + o.id, sev: 'high', org_name: orgLabel(o),
      age: ageLabel(o.last_activity),
      title: `${orgLabel(o)} has a critical health score (${o.health_score}/100)`,
      detail: `Last activity ${lastActivityLabel(o.last_activity)} · `
            + `${fmt(o.lead_count)} leads · ${fmt(o.messages_30d)} messages in 30 days.`,
      action: 'Enter organization', org: o,
    })
  })

  return out.sort((a, b) => (SEV_ORDER[a.sev] - SEV_ORDER[b.sev]) || 0)
}

export default function ExceptionQueue({ items, onAction, loading, busyId }) {
  if (loading) {
    return <div className="gm-card gm-empty">Evaluating platform state…</div>
  }
  if (!items || !items.length) {
    return (
      <div className="gm-card gm-empty" style={{ color: T.teal }}>
        Nothing needs an owner decision. Every organization is inside normal
        operating bounds.
      </div>
    )
  }
  return (
    <div className="gm-card" style={{ padding: 0, overflow: 'hidden' }}>
      <div className="gm-q">
        {items.map(x => {
          const busy = !!busyId && x.org && busyId === x.org.id
          return (
            <div key={x.id} className="gm-q-item">
              <i style={{
                background: SEV_COLOR[x.sev],
                boxShadow: x.sev === 'critical' ? `0 0 10px ${SEV_COLOR[x.sev]}` : 'none',
              }} />
              <div style={{ minWidth: 0 }}>
                <b className="gm-q-title">{x.title}</b>
                <small className="gm-q-detail">{x.detail}</small>
              </div>
              <div className="gm-q-meta">
                <span className="gm-q-sev" style={{ color: SEV_COLOR[x.sev] }}>
                  {x.sev.toUpperCase()}
                </span>
                {x.age ? <span className="gm-q-age">open {x.age}</span> : null}
                <button
                  className="gm-act gm-primary"
                  disabled={busy}
                  onClick={() => onAction && onAction(x)}
                >
                  {busy ? 'OPENING…' : x.action + ' →'}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
