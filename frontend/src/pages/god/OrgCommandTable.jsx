/**
 * ORGANIZATION COMMAND TABLE — the owner's operating table.
 *
 * ONE implementation, mounted twice: on the Command Center and on
 * /god/organizations. It replaced the hierarchy tree on one screen and a
 * separate hand-rolled table on the other, which is why those two used to
 * disagree about what an organization's state was.
 *
 * ── Every column has a named source ───────────────────────────────────────
 *   Organization / Users / Leads / Package / Implementation / Brand
 *        GET /god/ops/customer-organizations
 *   Msgs 30d / Last activity / Health / Advisors
 *        GET /god/orgs           (health_score is computed server-side)
 *   Billing
 *        GET /billing/all        (payment method + Stripe billing_status)
 *
 * ── Every action goes somewhere that exists ───────────────────────────────
 *   Enter        POST /god/platform/context/customer/{id}  (shared helper)
 *   Open         /god/customers/{id}
 *   Users        /god/customers/{id}?tab=people
 *   Implementation  /god/implementations/{id}
 *   Deal         /sales/opportunities/{opportunity_id}
 *   Activity     /god/audit?organization_id={id}
 *   Suspend / Reactivate   POST /god/orgs/{id}/suspend | /reactivate
 *
 * An action that has no target for a given row is NOT RENDERED. There are no
 * greyed-out buttons here that would do nothing if the grey were removed.
 *
 * PERMISSIONS ARE THE SERVER'S. Hiding a button is a courtesy; every endpoint
 * above is behind require_god. Nothing in this file decides what Mike may do.
 */
import { useMemo, useState } from 'react'
import { T, fmt, healthColor, lastActivityLabel } from './godTheme'
import { StatusBadge, orgStateBadge, NoSource } from './StatusBadge'

const PSEUDO = 'org-god-platform'

const STATE_FILTERS = ['all', 'active', 'suspended', 'attention', 'onboarding', 'unpriced']

function Pill({ tone, children, title }) {
  return <span className={'gm-pill ' + tone} title={title}>{children}</span>
}

function billingCell(bill) {
  if (!bill) return <NoSource>—</NoSource>
  if (!bill.stripe_customer_id) {
    return <Pill tone="red" title="No Stripe customer — this organization cannot be charged">NO METHOD</Pill>
  }
  const s = (bill.billing_status || '').toLowerCase()
  if (s === 'past_due') return <Pill tone="red">PAST DUE</Pill>
  if (s === 'canceled') return <Pill tone="off">CANCELED</Pill>
  if (s === 'trialing') return <Pill tone="gold">TRIALING</Pill>
  if (s === 'active') return <Pill tone="teal">ACTIVE</Pill>
  return <Pill tone="blue">{(bill.billing_status || 'method on file').toUpperCase()}</Pill>
}

function implCell(impl) {
  if (!impl) return <Pill tone="off" title="No implementation record — created outside the sales pipeline">OFF-PIPELINE</Pill>
  if (impl.status === 'blocked') return <Pill tone="red">BLOCKED</Pill>
  if (impl.is_live) return <Pill tone="teal">LIVE</Pill>
  return <Pill tone="gold">{String(impl.status || 'in progress').replace(/_/g, ' ').toUpperCase()}</Pill>
}

/** Joins the three payloads into one row per organization. */
export function buildRows({ orgs = [], customers = [], billingRows = null }) {
  const enrich = {}
  ;(orgs || []).filter(o => o.id !== PSEUDO).forEach(o => { enrich[o.id] = o })
  const bills = {}
  ;(billingRows || []).forEach(b => { bills[b.id] = b })

  const rows = (customers || [])
    .filter(c => c.organization_id !== PSEUDO)
    .map(c => {
      const o = enrich[c.organization_id] || {}
      return {
        id: c.organization_id,
        name: c.name,
        slug: c.slug,
        platform_name: c.platform ? c.platform.name : null,
        platform_id: c.platform ? c.platform.id : null,
        package_name: c.package ? c.package.name : null,
        plan: c.plan || o.plan || null,
        industry: c.industry,
        is_active: c.is_active,
        user_count: c.user_count,
        lead_count: c.lead_count,
        implementation: c.implementation,
        provisioned_from_sale: c.provisioned_from_sale,
        created_at: c.created_at,
        // from /god/orgs
        messages_30d: o.messages_30d,
        advisor_count: o.advisor_count,
        last_activity: o.last_activity,
        health_score: o.health_score,
        // the enriched record is what Enter and the state badge need
        _org: Object.keys(o).length ? o : { id: c.organization_id, name: c.name, is_active: c.is_active },
        _bill: bills[c.organization_id] || null,
      }
    })

  // An organization present in /god/orgs but not in customer-organizations is
  // reported rather than dropped — a row that exists in one list and not the
  // other is exactly the kind of thing an owner needs to see.
  const seen = new Set(rows.map(r => r.id))
  Object.values(enrich).filter(o => !seen.has(o.id)).forEach(o => {
    rows.push({
      id: o.id, name: o.name, slug: o.slug, platform_name: null, platform_id: o.platform_id,
      package_name: null, plan: o.plan, is_active: o.is_active,
      user_count: o.user_count, lead_count: o.lead_count, implementation: null,
      provisioned_from_sale: false, created_at: o.created_at,
      messages_30d: o.messages_30d, advisor_count: o.advisor_count,
      last_activity: o.last_activity, health_score: o.health_score,
      _org: o, _bill: bills[o.id] || null, _orphan: true,
    })
  })

  return rows
}

export default function OrgCommandTable({
  orgs, customers, billingRows, loading,
  onEnter, onSuspend, onGo, busyId, dense = false, initialFilter = null,
}) {
  const [q, setQ] = useState('')
  // `initialFilter` lets a link arrive already filtered (God Tools → Billing
  // Review is /god/organizations?filter=unpriced). An unrecognised value is
  // ignored rather than producing an empty table with no visible reason.
  const [state, setState] = useState(
    STATE_FILTERS.includes(initialFilter) ? initialFilter : 'all'
  )
  const [group, setGroup] = useState(!dense)

  const rows = useMemo(
    () => buildRows({ orgs, customers, billingRows }),
    [orgs, customers, billingRows]
  )

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return rows.filter(r => {
      if (needle && !(
        (r.name || '').toLowerCase().includes(needle) ||
        (r.slug || '').toLowerCase().includes(needle) ||
        (r.platform_name || '').toLowerCase().includes(needle) ||
        (r.id || '').toLowerCase().includes(needle)
      )) return false
      if (state === 'active') return r.is_active
      if (state === 'suspended') return !r.is_active
      if (state === 'attention') return (r.health_score ?? 100) < 80
      if (state === 'onboarding') return !!r.implementation && !r.implementation.is_live
      if (state === 'unpriced') return !r.package_name
      return true
    })
  }, [rows, q, state])

  const groups = useMemo(() => {
    if (!group) return [{ key: '__all', label: null, rows: filtered }]
    const m = new Map()
    filtered.forEach(r => {
      const k = r.platform_name || 'Unassigned to any brand'
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(r)
    })
    return [...m.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .map(([label, rs]) => ({ key: label, label, rows: rs }))
  }, [filtered, group])

  return (
    <div>
      <div className="gm-filters">
        <input
          className="gm-input"
          style={{ flex: '1 1 220px', maxWidth: 300 }}
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search name, brand, slug or id…"
        />
        <div className="gm-seg">
          {STATE_FILTERS.map(s => (
            <button key={s} className={state === s ? 'on' : ''} onClick={() => setState(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="gm-seg">
          <button className={group ? 'on' : ''} onClick={() => setGroup(g => !g)}>
            {group ? 'GROUPED BY BRAND' : 'FLAT LIST'}
          </button>
        </div>
        <span style={{ color: T.dim, fontSize: 10, marginLeft: 'auto' }}>
          {loading ? 'loading…' : `${filtered.length} of ${rows.length}`}
        </span>
      </div>

      <div className="gm-card" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="gm-tablewrap">
          <table className="gm-table">
            <thead>
              <tr>
                <th>ORGANIZATION</th>
                <th>BRAND</th>
                <th>PACKAGE</th>
                <th className="gm-num">USERS</th>
                <th className="gm-num">LEADS</th>
                <th className="gm-num">MSG 30D</th>
                <th>LAST ACTIVITY</th>
                <th className="gm-num">HEALTH</th>
                <th>BILLING</th>
                <th>ONBOARDING</th>
                <th>STATE</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={12} className="gm-empty">Loading organizations…</td></tr>
              )}
              {!loading && filtered.length === 0 && (
                <tr><td colSpan={12} className="gm-empty">
                  No organization matches this filter.
                </td></tr>
              )}
              {!loading && groups.map(g => (
                <RowGroup
                  key={g.key} group={g} showHeader={group}
                  onEnter={onEnter} onSuspend={onSuspend} onGo={onGo} busyId={busyId}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function RowGroup({ group, showHeader, onEnter, onSuspend, onGo, busyId }) {
  const [open, setOpen] = useState(true)
  return (
    <>
      {showHeader && group.label ? (
        <tr className="gm-group">
          <td colSpan={12}>
            <button className="gm-groupbtn" onClick={() => setOpen(o => !o)}>
              <span style={{ color: '#4d668a', fontSize: 9 }}>{open ? '▾' : '▸'}</span>
              {group.label}
              <StatusBadge tone={group.rows.length ? 'blue' : 'off'}>
                {group.rows.length} ORG{group.rows.length === 1 ? '' : 'S'}
              </StatusBadge>
            </button>
          </td>
        </tr>
      ) : null}
      {open && group.rows.map(r => (
        <Row key={r.id} r={r} onEnter={onEnter} onSuspend={onSuspend}
             onGo={onGo} busy={busyId === r.id} />
      ))}
    </>
  )
}

function Row({ r, onEnter, onSuspend, onGo, busy }) {
  const impl = r.implementation
  return (
    <tr>
      <td>
        <div className="gm-orgname">{r.name}</div>
        <div className="gm-orgsub">
          {r.slug || r.id.slice(0, 8)}
          {r.industry ? ' · ' + r.industry : ''}
          {r._orphan ? ' · not in the customer list' : ''}
        </div>
      </td>
      <td>{r.platform_name || <Pill tone="gold">NO BRAND</Pill>}</td>
      <td>
        {r.package_name
          ? <Pill tone="purple">{r.package_name}</Pill>
          : <Pill tone="gold" title="No package assigned — nothing to invoice against">
              {(r.plan || 'UNPRICED').toUpperCase()}
            </Pill>}
      </td>
      <td className="gm-num">{fmt(r.user_count)}</td>
      <td className="gm-num">{fmt(r.lead_count)}</td>
      <td className="gm-num" style={{
        color: (r.messages_30d || 0) === 0 && (r.lead_count || 0) > 0 ? T.amber : undefined,
      }}>{fmt(r.messages_30d)}</td>
      <td style={{ whiteSpace: 'nowrap', color: T.dim }}>{lastActivityLabel(r.last_activity)}</td>
      <td className="gm-num" style={{ color: healthColor(r.health_score), fontWeight: 700 }}>
        {r.health_score ?? '—'}
      </td>
      <td>{billingCell(r._bill)}</td>
      <td>{implCell(impl)}</td>
      <td>{orgStateBadge(r._org)}</td>
      <td>
        <div className="gm-acts">
          <button className="gm-act gm-primary" disabled={busy}
                  onClick={() => onEnter && onEnter(r._org)}
                  title="Assume this organization's context. Audited. Creates no membership.">
            {busy ? '…' : 'ENTER'}
          </button>
          <button className="gm-act" onClick={() => onGo('/god/customers/' + r.id)}>
            OPEN
          </button>
          <button className="gm-act" onClick={() => onGo('/god/customers/' + r.id + '?tab=people')}>
            USERS
          </button>
          {impl ? (
            <button className="gm-act" onClick={() => onGo('/god/implementations/' + impl.id)}>
              IMPL
            </button>
          ) : null}
          {impl && impl.opportunity_id ? (
            <button className="gm-act"
                    onClick={() => onGo('/sales/opportunities/' + impl.opportunity_id)}>
              DEAL
            </button>
          ) : null}
          <button className="gm-act"
                  onClick={() => onGo('/god/audit?organization_id=' + r.id)}>
            ACTIVITY
          </button>
          <button className={'gm-act ' + (r.is_active ? 'gm-danger' : '')}
                  onClick={() => onSuspend && onSuspend(r._org, r.is_active ? 'suspend' : 'reactivate')}>
            {r.is_active ? 'SUSPEND' : 'REACTIVATE'}
          </button>
        </div>
      </td>
    </tr>
  )
}
