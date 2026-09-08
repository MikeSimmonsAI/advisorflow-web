/**
 * God Mode — CUSTOMER ORGANISATIONS, the platform-wide lifecycle view.
 *
 * WHAT THIS SCREEN USED TO BE, AND WHY IT WAS CHANGED
 *
 * It showed PACKAGE, IMPLEMENTATION and SOURCE, and the first two were "—" on
 * most rows while the third said "created outside the pipeline". Those were not
 * three problems: `package` was read off the implementation, `implementation`
 * was the implementation, and `source` was literally `implementation is not
 * None`. One missing row, three dashes, always together.
 *
 * The dashes are gone because the screen now reads the relationships that were
 * always there — `Implementation` has carried the opportunity, the proposal and
 * its version, the salesperson, the brand and the pricing snapshot since
 * Checkpoint 6 — and because where there is genuinely nothing to read, it says
 * so in words instead of a dash.
 *
 * STATUS LEADS, because the first question about a customer list is which of
 * them are still customers. That was previously unanswerable: `is_active` is a
 * boolean and could not tell "paused this week" from "left us in March".
 *
 * WIDTH IS RATIONED. Setup / MRR / term / RCV / TCV do not all belong in a
 * table — the row carries what distinguishes customers from each other, and
 * Customer 360 carries the rest.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { enterCustomer } from './god/enterCustomer'
import { Panel, Empty, money, when, errText } from './god/GodOpsShared'
import { StatusPill } from './god/Customer360'
import './god/GodOps.css'

/** What this customer pays, in one cell, without lying about month-to-month. */
function Money({ c }) {
  if (!c || !c.complete) {
    return <span className="go-badge warn">INCOMPLETE</span>
  }
  const cur = c.currency || 'USD'
  return (
    <div style={{ lineHeight: 1.45 }}>
      {c.setup !== null && c.setup !== undefined
        ? <div>{money(c.setup, cur)} setup</div> : null}
      {c.mrr !== null && c.mrr !== undefined
        ? <div>{money(c.mrr, cur)}/mo</div> : null}
      <div style={{ fontSize: 11, color: 'var(--go-dim)' }}>
        {c.structure === 'fixed_term'
          ? c.term_months + ' mo · ' + money(c.total_contract_value, cur) + ' TCV'
          : c.structure === 'month_to_month'
            ? 'Month-to-month · no fixed TCV'
            : c.structure === 'one_time_only' ? 'One-time' : ''}
      </div>
    </div>
  )
}

export default function GodCustomers() {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [platform, setPlatform] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [entering, setEntering] = useState(null)

  const load = useCallback(() => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (platform) p.set('platform_id', platform)
    if (showArchived) p.set('include_archived', 'true')
    const qs = p.toString()
    api.get('/god/customer-360/customers' + (qs ? '?' + qs : ''))
      .then(r => { setD(r); setErr('') })
      .catch(e => setErr(errText(e)))
  }, [status, platform, showArchived])

  useEffect(() => { load() }, [load])

  const rows = (d?.customers || []).filter(o =>
    !q.trim() || (o.name || '').toLowerCase().includes(q.trim().toLowerCase()))

  async function handleEnter(e, orgId, orgName) {
    e.stopPropagation()
    setErr(''); setEntering(orgId)
    try {
      await enterCustomer(orgId, orgName)
      nav('/god/customer-app')
    } catch (ex) {
      setErr(errText(ex)); setEntering(null)
    }
  }

  const counts = d?.status_counts || {}

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god')}>← Command Center</button>
          <h1 style={{ marginTop: 8 }}>Customer organisations</h1>
          <p>Every tenant on every brand, with what they bought, who sold it and
             where they are in their life with us. Each one is isolated from the
             others and from the brand-sales tree.</p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      {/* Counts by status, which is the question this screen exists to answer
          and which a boolean could never express. */}
      {d ? (
        <div className="go-kpis">
          {(d.vocabulary?.statuses || []).map(s => (
            <div className="go-kpi" key={s.key}>
              <div className="k">{s.label}</div>
              <div className="v">{counts[s.key] || 0}</div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="go-filters">
        <input value={q} onChange={e => setQ(e.target.value)}
               placeholder="Search by name…" />
        <select value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {(d?.vocabulary?.statuses || []).map(s => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
        <select value={platform} onChange={e => setPlatform(e.target.value)}>
          <option value="">All brands</option>
          {(d?.platforms || []).map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <label className="go-check" style={{ alignItems: 'center', fontSize: 12 }}>
          <input type="checkbox" checked={showArchived}
                 onChange={e => setShowArchived(e.target.checked)} />
          <span>Include archived</span>
        </label>
      </div>

      <Panel title="Customers" count={d ? rows.length : null}>
        {!d ? <Empty>Loading…</Empty>
          : !rows.length ? (
            <Empty>
              No customer organisations match.
              {!showArchived && (counts.archived || 0) > 0
                ? ' ' + counts.archived + ' archived customer' +
                  (counts.archived === 1 ? ' is' : 's are') +
                  ' hidden — tick “Include archived” to see them.'
                : ''}
            </Empty>
          ) : (
          <table className="go-table">
            <thead>
              <tr>
                <th>Customer</th><th>Brand</th><th>Status</th><th>Package</th>
                <th className="num">Commercials</th><th>Implementation</th>
                <th>Sold by</th><th>Since</th>
                <th className="num">Users</th><th className="num">Leads</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(o => (
                <tr key={o.organization_id} className="clickable"
                    onClick={() => nav('/god/customers/' + o.organization_id + '/360')}>
                  <td data-label="Customer">
                    {o.name}
                    <div style={{ fontSize: 11, color: 'var(--go-dim)', marginTop: 2 }}>
                      {o.source === 'pipeline'
                        ? 'From pipeline'
                        : 'Created outside pipeline'}
                    </div>
                  </td>
                  <td data-label="Brand">
                    {o.platform ? o.platform.name
                      : <span className="go-badge warn">none</span>}
                  </td>
                  <td data-label="Status">
                    <StatusPill status={o.lifecycle_status}
                                label={o.lifecycle_status_label} />
                    {!o.workspace_active && o.lifecycle_status === 'active' ? (
                      <div style={{ fontSize: 10, color: 'var(--go-dim)', marginTop: 3 }}>
                        workspace suspended
                      </div>
                    ) : null}
                  </td>
                  <td data-label="Package">
                    {o.package ? o.package.name
                      : <span style={{ color: 'var(--go-dim)', fontStyle: 'italic' }}>
                          none recorded
                        </span>}
                  </td>
                  <td data-label="Commercials" className="num">
                    <Money c={o.commercials} />
                  </td>
                  <td data-label="Implementation">
                    {o.implementation
                      ? <span className={'go-badge ' + (o.implementation.is_live ? 'live' : '')}>
                          {(o.implementation.status || '').replace(/_/g, ' ')}
                        </span>
                      : <span style={{ color: 'var(--go-dim)', fontStyle: 'italic',
                                       fontSize: 11 }}>
                          no record
                        </span>}
                  </td>
                  <td data-label="Sold by">
                    {o.sold_by_name || <span style={{ color: 'var(--go-dim)' }}>—</span>}
                  </td>
                  <td data-label="Since">{when(o.customer_since)}</td>
                  <td data-label="Users" className="num">{o.user_count}</td>
                  <td data-label="Leads" className="num">{o.lead_count}</td>
                  {/* TWO ACTIONS, TWO DESTINATIONS, AND THE LABELS SAY WHICH.
                      "Enter" said nothing about what was being entered — the
                      commercial record or the customer's actual product — and
                      the two buttons sat side by side looking interchangeable.
                      360 opens the commercial history; Workspace enters this
                      customer's live operating environment as them. */}
                  <td data-label="" onClick={e => e.stopPropagation()}>
                    <div className="go-actions">
                      <button className="go-btn sm ghost"
                              title={'Commercial history for ' + o.name}
                              onClick={() => nav('/god/customers/' +
                                                 o.organization_id + '/360')}>
                        360
                      </button>
                      <button className="go-btn sm"
                              title={'Open ' + o.name + "'s workspace as them"}
                              disabled={entering === o.organization_id}
                              onClick={e => handleEnter(e, o.organization_id, o.name)}>
                        {entering === o.organization_id ? 'Entering…' : 'Workspace'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
            Commercials are the figures recorded when each deal was won, not
            today's catalogue. A customer with no implementation record has no
            provable originating deal — reconstructing one by name would invent
            it, so those read INCOMPLETE rather than being filled in.
          </p>
        </div>
      </Panel>
    </div>
  )
}
