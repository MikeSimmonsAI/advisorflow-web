/**
 * ExecutivePortfolio - every organization, worst first, with the reason.
 *
 * ===========================================================================
 * HEALTH HAS TO SAY WHY
 * ===========================================================================
 *
 * A portfolio screen that grades customers with a number nobody can explain is
 * not a management tool - it is something to argue with. Every row here
 * carries the sentence behind its verdict:
 *
 *     Restland          Healthy    Working the book - last activity today
 *     WUPA              At risk    Quiet for 47 days. This is usually the
 *                                  first sign of a customer drifting away
 *     Fiber Cartel      Watch      No payment method on file
 *
 * The reason comes from the server, from the same rows the Command Center's
 * totals are summed from, so a headline and this table can never disagree
 * about how many organizations are in trouble.
 *
 * ===========================================================================
 * WORST FIRST, AND EVERY FILTER IS REAL
 * ===========================================================================
 *
 * The default order is by severity, then by how many exceptions an
 * organization is carrying, then by size. An executive opens this page to find
 * trouble, so trouble is at the top and does not have to be sorted for.
 *
 * Each filter is a predicate the data can actually answer, and each tab shows
 * its count before it is clicked - so "Billing (0)" is a usable answer rather
 * than a click that turns out to be wasted. Zero-count tabs stay visible and
 * go quiet rather than disappearing, because a filter bar that changes shape
 * as data changes is disorienting.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { Health, Val, money, num, pct, plural, since } from './ExecUI'

const FILTER_LABELS = {
  all: 'All',
  healthy: 'Healthy',
  needs_attention: 'Needs attention',
  inactive: 'Inactive',
  implementation: 'Implementing',
  billing_issue: 'Billing',
  low_activity: 'Low activity',
  high_growth: 'Growing',
}

export default function ExecutivePortfolio() {
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const filter = params.get('filter') || 'all'
  const [state, setState] = useState({ loading: true, data: null, error: null })

  const load = useCallback((key) => {
    setState(s => ({ ...s, loading: true }))
    api.get('/executive/portfolio/health?filter=' + encodeURIComponent(key))
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({
        loading: false, data: null,
        error: 'Could not load your portfolio. Please try again.' }))
  }, [])

  useEffect(() => { load(filter) }, [load, filter])

  const { loading, data, error } = state

  function choose(key) {
    // THE FILTER LIVES IN THE URL, so a view an executive is looking at can be
    // shared, bookmarked and survive a refresh. State that only exists in
    // memory quietly resets every time somebody reloads.
    if (key === 'all') { params.delete('filter') } else { params.set('filter', key) }
    setParams(params, { replace: true })
  }

  if (error) return <div className="ex-err">{error}</div>
  if (!data) return <p className="ex-muted">Reading your portfolio…</p>

  const win = data.recent_window_days

  return (
    <>
      <div className="ex-head">
        <div>
          <p className="ex-eyebrow">{data.platform_name}</p>
          <h1 className="ex-h1">Organizations</h1>
          <p className="ex-sub">
            Every customer in your portfolio, worst first. The health column
            says what it means, not just what colour it is.
          </p>
        </div>
      </div>

      <div className="ex-tabs">
        {data.filters.map(f => (
          <button key={f.key}
                  className={'ex-tab' + (f.key === data.filter ? ' on' : '')
                    + (f.count === 0 && f.key !== data.filter ? ' ex-empty' : '')}
                  onClick={() => choose(f.key)}>
            {FILTER_LABELS[f.key] || f.key}
            <span className="ex-c">{f.count}</span>
          </button>
        ))}
      </div>

      {loading ? <p className="ex-muted">Loading…</p> : null}

      {!data.organizations.length ? (
        <div className="ex-card">
          <div className="ex-blank">
            <b>
              {data.total === 0
                ? 'No customer organizations yet.'
                : 'No organizations match this filter.'}
            </b>
            <p>
              {data.total === 0
                ? 'As customers are provisioned onto this brand they appear here.'
                : `All ${num(data.total)} organizations in this portfolio fall outside "${FILTER_LABELS[data.filter] || data.filter}". Nothing is missing — nothing matches.`}
            </p>
          </div>
        </div>
      ) : (
        <div className="ex-tablewrap">
          <table className="ex-table">
            <thead>
              <tr>
                <th>Organization</th>
                <th>Health</th>
                <th>Package</th>
                <th className="ex-num">MRR</th>
                <th className="ex-num">Users</th>
                <th className="ex-num">Leads</th>
                <th className="ex-num">Worked · {win}d</th>
                <th className="ex-num">Appointments</th>
                <th className="ex-num">Response</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {data.organizations.map(o => (
                <tr key={o.id}>
                  <td>
                    <button className="ex-orgbtn"
                            onClick={() => nav(`/executive/organizations/${o.id}/view`)}>
                      {o.name}
                    </button>
                    <span className="ex-why">{o.reason}</span>
                    {/* THE EXCEPTIONS, ON THE ROW. An executive scanning this
                        table should not have to open an organization to learn
                        that it has 1,584 unworked leads. */}
                    {o.attention.map(a => (
                      <span key={a.key} className={'ex-flag sv-' + a.severity}>
                        {a.text}
                      </span>
                    ))}
                  </td>
                  <td><Health health={o.health} label={o.health_label} /></td>
                  <td><Val value={o.package} absent="no package" /></td>
                  {/* NULL MRR IS WORDS, NEVER $0. A customer we cannot price
                      and a customer paying nothing are opposite facts. */}
                  <td className="ex-num"><Val value={money(o.mrr)} absent="not priced" /></td>
                  <td className="ex-num">{num(o.active_users)}</td>
                  <td className="ex-num">{num(o.leads_total)}</td>
                  <td className="ex-num">{num(o.leads_worked_recently)}</td>
                  <td className="ex-num">
                    {num(o.appointments_total)}
                    {o.appointments_last_7_days
                      ? <span className="ex-why">+{o.appointments_last_7_days} this week</span>
                      : null}
                  </td>
                  <td className="ex-num">
                    <Val value={pct(o.response_rate)} absent="—" />
                  </td>
                  <td><Val value={since(o.last_activity)} absent="never" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="ex-muted" style={{ marginTop: 14 }}>
        Showing {num(data.shown)} of {num(data.total)}{' '}
        {plural(data.total, 'organization', 'organizations')}. Health is
        measured from real outbound messages, replies and bookings — never from
        whether an account exists.
      </p>
    </>
  )
}
