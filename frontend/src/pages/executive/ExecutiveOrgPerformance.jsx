/**
 * ExecutiveOrgPerformance - one organization, seen by an executive.
 *
 * ===========================================================================
 * THIS IS NOT THE CUSTOMER DASHBOARD WITH THE BUTTONS HIDDEN
 * ===========================================================================
 *
 * The page this replaces was the customer workspace homepage with a red
 * "READ ONLY" banner nailed to the top. It showed "What needs attention now",
 * hot replies to answer, and a lead-flow queue - which are the right screens
 * for the person who is going to pick up the phone, and the wrong ones for a
 * regional executive who is not.
 *
 * An executive opening one organization is deciding something:
 *
 *     Is this business performing?
 *     Compared to what?
 *     What is going wrong here?
 *     What did we sell them, and are we being paid?
 *
 * So this page is OUTCOMES, COMPARISON and EXCEPTIONS. There is deliberately
 * no per-lead list, no reply to answer, no control that operates the
 * customer's business - not because they are hidden, but because they were
 * never the executive's job. Hiding buttons and calling the result an
 * executive view is what produced the page this replaces.
 *
 * ===========================================================================
 * COMPARISON IS THE THING A SINGLE ORGANIZATION CANNOT TELL YOU
 * ===========================================================================
 *
 * "23% response rate" means nothing on its own. Against a portfolio median of
 * 31% it means something specific and actionable. Every rate here is shown
 * against the median of the rest of the portfolio, and when there is nothing
 * to compare against - a portfolio of one - the comparison is simply absent
 * rather than invented.
 *
 * ===========================================================================
 * SWITCHING WITHOUT LEAVING
 * ===========================================================================
 *
 * The organization picker moves between authorized organizations directly. It
 * is built from durable ids the server sent with this page - never from a name
 * match, and never by routing back out through the owner shell. An executive
 * comparing three customers should not have to leave their own layer to do it.
 *
 * READ-ONLY IS STATED ONCE, QUIETLY. The old blood-red banner shouted a
 * restriction at somebody who was never trying to edit anything. The context
 * chip says what this view is; the absence of controls says the rest.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import {
  Delta, Health, Kpi, Val, day, money, num, pct, plural, since,
} from './ExecUI'

export default function ExecutiveOrgPerformance() {
  const { orgId } = useParams()
  const nav = useNavigate()
  const [state, setState] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    if (!orgId) return
    setState({ loading: true, data: null, error: null })
    api.get(`/executive/organizations/${orgId}/performance`)
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(err => setState({
        loading: false, data: null,
        error: err?.status === 404
          ? 'That organization is not in your portfolio.'
          : err?.status === 403
            ? 'You do not have executive access to this organization.'
            : 'Could not load this organization. Please try again.' }))
  }, [orgId])

  const { loading, data, error } = state

  if (loading) return <p className="ex-muted">Loading organization…</p>

  if (error || !data) {
    return (
      <>
        <div className="ex-err">{error || 'Organization not found.'}</div>
        <button className="ex-btn" onClick={() => nav('/executive/organizations')}>
          Back to portfolio
        </button>
      </>
    )
  }

  const o = data.organization
  const c = data.comparison
  const impl = o.implementation
  const deal = o.originating_deal
  const worked = o.leads_total
    ? Math.round((o.leads_worked_recently / o.leads_total) * 100) : null

  return (
    <>
      {/* -- WHO, AND IN WHAT CONTEXT -------------------------------------- */}
      <div className="ex-orghead">
        <div className="ex-orghead-top">
          <div style={{ minWidth: 0 }}>
            <p className="ex-eyebrow">{data.platform_name} · Portfolio</p>
            <h1>{o.name}</h1>
            <div style={{ display: 'flex', gap: 10, marginTop: 12,
                          flexWrap: 'wrap', alignItems: 'center' }}>
              <Health health={o.health} label={o.health_label} />
              <span className="ex-context">Viewing performance · read only</span>
            </div>
            <p className="ex-sub" style={{ marginTop: 10 }}>{o.reason}</p>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {/* DURABLE IDS ONLY. Nothing here resolves an organization by
                name, and the list is the portfolio the server authorized. */}
            {data.portfolio.length > 1 ? (
              <select className="ex-select" aria-label="Switch organization"
                      value={o.id}
                      onChange={e => nav(`/executive/organizations/${e.target.value}/view`)}>
                {data.portfolio.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            ) : null}
            <button className="ex-btn" onClick={() => nav('/executive/organizations')}>
              Back to portfolio
            </button>
          </div>
        </div>

        <div className="ex-orgmeta">
          <div>
            <span>Package</span>
            <b className={o.package ? '' : 'ex-none'}>
              <Val value={o.package} absent="none assigned" />
            </b>
          </div>
          <div>
            <span>Monthly</span>
            <b className={o.mrr === null ? 'ex-none' : ''}>
              <Val value={money(o.mrr)} absent="not priced" />
            </b>
          </div>
          <div>
            <span>Status</span>
            <b>{o.is_active ? 'Active' : 'Suspended'}</b>
          </div>
          <div>
            <span>Customer since</span>
            <b className={o.provisioned_at ? '' : 'ex-none'}>
              <Val value={day(o.provisioned_at)} absent="not recorded" />
            </b>
          </div>
          <div>
            <span>Last activity</span>
            <b className={o.last_activity ? '' : 'ex-none'}>
              <Val value={since(o.last_activity)} absent="never" />
            </b>
          </div>
          <div>
            <span>People</span>
            <b>{num(o.active_users)} active</b>
          </div>
        </div>
      </div>

      {/* -- WHAT NEEDS DOING HERE ----------------------------------------- */}
      <div className="ex-section">
        <h2>Attention</h2>
        {!o.attention.length ? (
          <div className="ex-allclear">
            <div>
              <b>Nothing outstanding.</b>
              <p>
                This organization is active, working its leads, answering
                families and billable. Checked against the same conditions
                every organization in the portfolio is checked against.
              </p>
            </div>
          </div>
        ) : (
          <div className="ex-exc">
            {o.attention.map(a => (
              <div className={'ex-exc-row sv-' + a.severity} key={a.key}>
                <div className="ex-exc-bar" />
                <div className="ex-exc-body">
                  <div className="ex-exc-t"><b>{a.text}</b></div>
                </div>
                <div className="ex-exc-side" />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* -- IS THE BOOK BEING WORKED --------------------------------------
          The question behind every other number on this page. A customer with
          thousands of leads and nobody touching them is not underperforming;
          they have not started. */}
      <div className="ex-section">
        <h2>Lead performance</h2>
        <div className="ex-kpis">
          <Kpi label="Leads in the book" value={num(o.leads_total)}
               sub={`${num(o.leads_added_recently)} added recently`} />
          <Kpi label="Worked recently" value={num(o.leads_worked_recently)}
               lead
               sub={worked === null ? 'no leads yet' : `${worked}% of the book`} />
          <Kpi label="Never contacted" value={num(o.leads_never_touched)}
               alarm={o.leads_never_touched > 0}
               sub={o.leads_never_touched
                 ? 'bought and not yet worked'
                 : 'every lead has been touched'} />
          <Kpi label="Contacted" value={num(o.leads_sent)}
               sub="leads that have received a message" />
        </div>
      </div>

      {/* -- WHAT THAT PRODUCED, AND AGAINST WHAT --------------------------- */}
      <div className="ex-section">
        <h2>
          Outcomes
          {c ? <span>· compared with {num(c.peer_count)} other{' '}
            {plural(c.peer_count, 'organization', 'organizations')}</span> : null}
        </h2>
        <div className="ex-kpis">
          <div className="ex-kpi">
            <span className="ex-k">Response rate</span>
            {o.response_rate === null
              ? <span className="ex-v ex-none">Nothing sent yet</span>
              : <span className="ex-v">{pct(o.response_rate)}</span>}
            <span className="ex-s">
              {o.leads_sent
                ? `${num(o.replies_total)} ${plural(o.replies_total, 'reply', 'replies')} from ${num(o.leads_sent)} contacted`
                : 'no messages have gone out'}
              {c ? (
                <span className="ex-compare">
                  <Delta value={o.response_rate} median={c.median_response_rate} />
                </span>
              ) : null}
            </span>
          </div>

          <div className="ex-kpi">
            <span className="ex-k">Booking rate</span>
            {o.conversion_rate === null
              ? <span className="ex-v ex-none">Nothing sent yet</span>
              : <span className="ex-v">{pct(o.conversion_rate)}</span>}
            <span className="ex-s">
              {num(o.leads_booked)} {plural(o.leads_booked, 'lead', 'leads')} became
              an appointment
              {c ? (
                <span className="ex-compare">
                  <Delta value={o.conversion_rate} median={c.median_conversion_rate} />
                </span>
              ) : null}
            </span>
          </div>

          <div className="ex-kpi">
            <span className="ex-k">Appointments</span>
            <span className="ex-v">{num(o.appointments_total)}</span>
            <span className="ex-s">
              {num(o.appointments_last_7_days)} in the last 7 days ·{' '}
              {num(o.appointments_upcoming)} still ahead
              {c ? (
                <span className="ex-compare">
                  <Delta value={o.appointments_total} median={c.median_appointments}
                         format={num} />
                </span>
              ) : null}
            </span>
          </div>

          <Kpi label="Unread replies" value={num(o.replies_unreviewed)}
               alarm={o.replies_unreviewed > 0}
               sub={o.replies_unreviewed
                 ? 'families waiting on somebody here'
                 : 'every reply has been read'} />
        </div>
      </div>

      {/* -- THE COMMERCIAL PICTURE ---------------------------------------- */}
      <div className="ex-section">
        <h2>Commercial</h2>
        <div className="ex-card">
          <div className="ex-card-b">
            <div className="ex-orgmeta" style={{ marginTop: 0, paddingTop: 0,
                                                 borderTop: 0 }}>
              <div>
                <span>Sold by</span>
                <b className={deal?.sold_by ? '' : 'ex-none'}>
                  <Val value={deal?.sold_by} absent="not sold through the pipeline" />
                </b>
              </div>
              <div>
                <span>Deal value</span>
                <b className={deal?.value ? '' : 'ex-none'}>
                  <Val value={money(deal?.value)} absent="no deal on record" />
                </b>
              </div>
              <div>
                <span>Payment method</span>
                <b className={o.has_payment_method ? '' : 'ex-none'}>
                  {o.has_payment_method ? 'On file' : 'None on file'}
                </b>
              </div>
              <div>
                <span>Implementation</span>
                <b className={impl ? '' : 'ex-none'}>
                  <Val value={impl?.label} absent="none recorded" />
                </b>
              </div>
              <div>
                <span>Held prospects</span>
                <b className={o.held_leads ? '' : 'ex-none'}>
                  {o.held_leads ? num(o.held_leads) : 'none'}
                </b>
              </div>
            </div>

            {/* A CUSTOMER WITH NO COMMERCIAL RECORD IS SAID SO, not filled in.
                An invented salesperson would eventually be paid a commission. */}
            {!deal ? (
              <p className="ex-sub" style={{ marginTop: 16, marginBottom: 0 }}>
                This customer was created outside the sales pipeline, so there
                is no deal, no value and no salesperson on record. That is a
                gap in the commercial history rather than a problem with the
                account.
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </>
  )
}
