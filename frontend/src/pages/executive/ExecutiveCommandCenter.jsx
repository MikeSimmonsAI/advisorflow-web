/**
 * ExecutiveCommandCenter - how is my business doing, and what needs me today.
 *
 * ===========================================================================
 * THE TWO QUESTIONS, IN THAT ORDER
 * ===========================================================================
 *
 * An executive opening this page is asking two things and only two:
 *
 *   1. Is the business working?          -> the headline band
 *   2. What needs me?                    -> the exception list
 *
 * So the page is those two things, in that order, and nothing else competes.
 * There is no activity feed, no per-lead queue, no operational control. Those
 * belong to the people doing the work; an executive who needed them would be
 * doing somebody else's job.
 *
 * ===========================================================================
 * EVERY HEADLINE OPENS, AND EVERY HEADLINE IS HONEST
 * ===========================================================================
 *
 * The totals are summed by the server FROM the per-organization rows Portfolio
 * shows, so clicking a figure and counting the rows behind it always agrees.
 * That is why the tiles navigate rather than just sit there.
 *
 * A figure the platform cannot compute renders as WORDS, never as zero. The
 * clearest case is revenue: a customer whose plan does not resolve cannot be
 * priced, and folding them in at $0 would report a business earning nothing
 * rather than a figure nobody can produce. So MRR is always shown WITH its
 * coverage - "across 3 of 5 customers" - and the two unpriced ones are named
 * rather than quietly averaged away.
 *
 * Rates work the same way. Nought replies from nought sends is not a nought
 * percent response rate, and a screen that says 0% is reporting a failure that
 * did not happen.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { Health, Kpi, money, num, pct, plural } from './ExecUI'

export default function ExecutiveCommandCenter() {
  const nav = useNavigate()
  const [state, setState] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    api.get('/executive/portfolio')
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({
        loading: false, data: null,
        error: 'Could not load your portfolio. Please try again.' }))
  }, [])

  const { loading, data, error } = state

  if (loading) return <p className="ex-muted">Reading your portfolio…</p>
  if (error || !data) return <div className="ex-err">{error || 'No data.'}</div>

  const s = data.summary
  const win = s.recent_window_days
  const orgs = s.organizations

  // A brand with no customers yet is a real, common state - and it is not an
  // error, an empty table, or a wall of zeroes.
  if (!orgs) {
    return (
      <>
        <div className="ex-head">
          <div>
            <p className="ex-eyebrow">{data.platform_name}</p>
            <h1 className="ex-h1">Command Center</h1>
          </div>
        </div>
        <div className="ex-card">
          <div className="ex-blank">
            <b>No customer organizations yet.</b>
            <p>
              This portfolio has nothing in it so far. As deals close and
              customers are provisioned onto {data.platform_name}, they appear
              here and everything on this page starts reporting.
            </p>
          </div>
        </div>
      </>
    )
  }

  const goPortfolio = (f) => nav('/executive/organizations' + (f ? '?filter=' + f : ''))

  return (
    <>
      <div className="ex-head">
        <div>
          <p className="ex-eyebrow">{data.platform_name}</p>
          <h1 className="ex-h1">Command Center</h1>
          <p className="ex-sub">
            {num(orgs)} {plural(orgs, 'organization', 'organizations')} in your
            portfolio. Every figure here is the sum of those organizations —
            click one to see the businesses behind it.
          </p>
        </div>
      </div>

      {/* -- IS THE BUSINESS WORKING ----------------------------------------
          Leads first, because everything downstream depends on whether the
          book is being worked; then what that produced. */}
      <div className="ex-section">
        <h2>The business <span>· last {win} days unless stated</span></h2>
        <div className="ex-kpis">
          <Kpi label="Organizations" value={num(orgs)}
               sub={s.active_organizations === orgs
                 ? 'all active'
                 : `${num(orgs - s.active_organizations)} suspended`}
               onClick={() => goPortfolio()} />

          <Kpi label="Leads worked" value={num(s.leads_worked_recently)}
               sub={`of ${num(s.leads_total)} in the book · ${num(s.leads_added_recently)} added`}
               onClick={() => goPortfolio('low_activity')}
               title="Leads contacted at least once in the window" />

          <Kpi label="Never contacted" value={num(s.leads_never_touched)}
               alarm={s.leads_never_touched > 0}
               sub={s.leads_never_touched
                 ? 'leads bought and not yet worked'
                 : 'every lead has been touched'}
               onClick={() => goPortfolio('needs_attention')} />

          <Kpi label="Appointments" value={num(s.appointments_total)}
               sub={`${num(s.appointments_last_7_days)} booked in the last 7 days · ${num(s.appointments_upcoming)} still ahead`}
               lead />
        </div>
      </div>

      {/* -- IS IT CONVERTING ------------------------------------------------
          Both rates are null when nothing has been sent, and both say so in
          words. A denominator of nought is not a nought percent rate. */}
      <div className="ex-section">
        <h2>Conversion</h2>
        <div className="ex-kpis">
          <Kpi label="Response rate" value={pct(s.response_rate)}
               absent="Nothing sent yet"
               sub={s.leads_sent
                 ? `${num(s.replies_total)} ${plural(s.replies_total, 'reply', 'replies')} from ${num(s.leads_sent)} contacted`
                 : 'no messages have gone out'} />

          <Kpi label="Booking rate" value={pct(s.conversion_rate)}
               absent="Nothing sent yet"
               sub={s.leads_sent
                 ? 'contacted leads that became appointments'
                 : 'no messages have gone out'} />

          <Kpi label="Unread replies" value={num(s.replies_unreviewed)}
               alarm={s.replies_unreviewed > 0}
               sub={s.replies_unreviewed
                 ? 'families waiting on somebody'
                 : 'every reply has been read'}
               onClick={() => goPortfolio('needs_attention')} />

          <Kpi label="Held prospects" value={num(s.held_leads)}
               sub={s.held_leads
                 ? 'inbound leads blocked by plan limits — an upgrade conversation'
                 : 'no plan limits are being hit'}
               onClick={() => goPortfolio('billing_issue')} />
        </div>
      </div>

      {/* -- WHERE IS THE REVENUE -------------------------------------------
          MRR IS NEVER SHOWN WITHOUT ITS COVERAGE. See the file header: a bare
          total silently claims the unpriced customers earn nothing. */}
      <div className="ex-section">
        <h2>Revenue</h2>
        <div className="ex-kpis">
          <Kpi label="Recurring revenue" value={money(s.mrr)}
               absent="Not priced yet"
               lead
               sub={s.mrr === null
                 ? 'no customer has a plan this brand can price'
                 : s.mrr_unpriced_organizations
                   ? `across ${num(s.mrr_priced_organizations)} of ${num(orgs)} customers · ${num(s.mrr_unpriced_organizations)} cannot be priced`
                   : `across all ${num(orgs)} customers`}
               onClick={() => nav('/executive/revenue')} />

          <Kpi label="Implementations open" value={num(s.implementations_open)}
               sub={s.implementations_open
                 ? 'customers not yet fully live'
                 : 'nothing in flight'}
               onClick={() => goPortfolio('implementation')} />

          <Kpi label="Needs attention" value={num(s.needs_attention)}
               alarm={s.needs_attention > 0}
               sub={s.needs_attention
                 ? `${plural(s.needs_attention, 'organization has', 'organizations have')} a standing issue`
                 : 'nothing outstanding across the portfolio'}
               onClick={() => goPortfolio('needs_attention')} />

          <Kpi label="At risk or inactive"
               value={num((s.health.at_risk || 0) + (s.health.inactive || 0))}
               sub={`${num(s.health.healthy || 0)} healthy · ${num(s.health.watch || 0)} slowing · ${num(s.health.onboarding || 0)} onboarding`}
               onClick={() => goPortfolio('inactive')} />
        </div>
      </div>

      {/* -- WHAT NEEDS ME TODAY --------------------------------------------- */}
      <div className="ex-section">
        <h2>
          What needs you
          {data.attention_total > data.attention.length
            ? <span>· showing {data.attention.length} of {data.attention_total}</span>
            : null}
        </h2>

        {!data.attention.length ? (
          <div className="ex-allclear">
            <div>
              <b>Nothing outstanding.</b>
              <p>
                No organization in this portfolio is dormant, sitting on
                unworked leads, missing a payment method or leaving families
                unanswered. This is checked against every organization each
                time the page loads.
              </p>
            </div>
          </div>
        ) : (
          <div className="ex-exc">
            {data.attention.map(o => {
              const worst = o.items.some(i => i.severity === 'action_required')
                ? 'action_required' : 'attention'
              return (
                <div className={'ex-exc-row sv-' + worst} key={o.id}>
                  <div className="ex-exc-bar" />
                  <div className="ex-exc-body">
                    <div className="ex-exc-t">
                      <b>{o.name}</b>
                      <Health health={o.health} label={o.health_label} />
                    </div>
                    <p className="ex-exc-why">{o.reason}</p>
                    <ul className="ex-exc-items">
                      {o.items.map(i => (
                        <li key={i.key} className={'sv-' + i.severity}>{i.text}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="ex-exc-side">
                    <button className="ex-btn ex-small"
                            onClick={() => nav(`/executive/organizations/${o.id}/view`)}>
                      Open
                    </button>
                  </div>
                </div>
              )
            })}
            {data.attention_total > data.attention.length ? (
              <button className="ex-btn" onClick={() => goPortfolio('needs_attention')}>
                See all {data.attention_total} organizations needing attention
              </button>
            ) : null}
          </div>
        )}
      </div>
    </>
  )
}
