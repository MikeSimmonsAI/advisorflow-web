/* MY COMPENSATION — what one salesperson has projected, earned, on hold,
 * payable and been paid.
 *
 * SCOPED BY THE TOKEN, NOT BY THIS FILE. The endpoint takes no payee
 * parameter; it reads the caller's own id from their token. There is nothing
 * to tamper with in the request, which is what makes this safe rather than
 * merely tidy — a filter a client could change is not a boundary.
 *
 * NOTHING HERE IS ADMINISTRATIVE. There is no rate, no plan, no way to mark
 * anything paid. A rep can see what they are owed and why; they cannot change
 * what anybody earns.
 *
 * THE FIVE FIGURES ARE NOT INTERCHANGEABLE and the card refuses to blur them:
 * projected is a forecast from open deals and is owed to nobody; earned means
 * a customer payment was actually collected; on hold means the holdback has
 * not elapsed; payable means it has; paid means the money went out.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import SalesShell from './SalesShell'

function usd(n) {
  if (n === null || n === undefined) return '—'
  return '$' + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

function day(v) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

const VIEW_TONE = {
  on_hold: { bg: 'rgba(245,185,66,.14)', fg: '#8a6100', label: 'ON HOLD' },
  payable_now: { bg: 'rgba(63,185,80,.14)', fg: '#1c6b2b', label: 'PAYABLE' },
  paid: { bg: 'rgba(120,130,145,.14)', fg: '#4a5563', label: 'PAID' },
  void: { bg: 'rgba(248,81,73,.14)', fg: '#9a2820', label: 'VOID' },
}

function Pill({ view }) {
  const t = VIEW_TONE[view] || VIEW_TONE.paid
  return (
    <span style={{ background: t.bg, color: t.fg, fontSize: 10, fontWeight: 800,
                   letterSpacing: '.06em', padding: '2px 8px', borderRadius: 20 }}>
      {t.label}
    </span>
  )
}

function Stat({ label, value, sub, muted }) {
  return (
    <div className="sw-card" style={{ flex: '1 1 150px', minWidth: 150 }}>
      <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                    color: '#5c6b7a' }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4,
                    color: muted ? '#8496a4' : '#0f2338' }}>
        {value}
      </div>
      {sub ? <div className="sw-subtle" style={{ marginTop: 2 }}>{sub}</div> : null}
    </div>
  )
}

export default function MyCompensation() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/sales/compensation/me')
      .then(r => { setD(r); setErr('') })
      .catch(e => setErr(e?.message || 'Could not load your compensation.'))
  }, [])

  const s = d?.summary
  const p = d?.projected

  return (
    <SalesShell title="My Compensation"
                subtitle="What you have earned, what is on hold, and what has been paid.">
      {err ? <div className="sw-notbuilt"><b>COULD NOT LOAD</b><p>{err}</p></div> : null}
      {!d && !err ? <div className="sw-subtle">Loading…</div> : null}

      {d ? (
        <>
          <div className="sw-flex" style={{ gap: 12, flexWrap: 'wrap' }}>
            <Stat label="PROJECTED" value={usd(p.amount)}
                  sub={p.deal_count + ' open ' + (p.deal_count === 1 ? 'deal' : 'deals')}
                  muted />
            <Stat label="EARNED" value={usd(s.earned_total)}
                  sub={s.earned_count + ' ' + (s.earned_count === 1 ? 'entry' : 'entries')} />
            <Stat label="ON HOLD" value={usd(s.on_hold.amount)}
                  sub="holdback not elapsed" />
            <Stat label="PAYABLE" value={usd(s.payable_now.amount)}
                  sub="ready to be paid" />
            <Stat label="PAID" value={usd(s.paid.amount)} />
          </div>

          {/* PROJECTED IS A FORECAST. Said plainly, next to the number, because
              the difference between "expect" and "owed" is the whole point. */}
          <div className="sw-card sw-mt">
            <div className="sw-subtle">
              <b>Projected is not owed to you.</b> It is what your open deals would
              pay if they closed on today's terms. Commission is earned only when
              the customer payment actually lands, and becomes payable after your
              plan's holdback.
              {p.unconfigured_deals > 0 ? (
                <> {p.unconfigured_deals}{' '}
                  {p.unconfigured_deals === 1 ? 'deal is' : 'deals are'} on a package
                  with no commission rate configured yet — those are excluded rather
                  than counted as zero.</>
              ) : null}
              {p.pending_approval_deals > 0 ? (
                <> {usd(p.pending_approval_amount)} sits on{' '}
                  {p.pending_approval_deals}{' '}
                  {p.pending_approval_deals === 1 ? 'deal' : 'deals'} still waiting on
                  pricing approval, and is not included above.</>
              ) : null}
            </div>
          </div>

          <div className="sw-card sw-mt">
            <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                          color: '#16324f', marginBottom: 10 }}>
              MY COMMISSION ENTRIES ({d.entries.length})
            </div>
            {!d.entries.length ? (
              <div className="sw-subtle">
                Nothing yet. An entry appears here when a customer payment on one of
                your won deals is actually collected — not when the deal is marked
                Won.
              </div>
            ) : (
              <div className="sw-tablewrap">
                <table className="sw-table" style={{ minWidth: 720 }}>
                  <thead>
                    <tr>
                      <th>Deal</th><th>Customer</th><th>Type</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                      <th>Collected</th><th>Payable</th><th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.entries.map(e => (
                      <tr key={e.id}>
                        <td>{e.deal_name || '—'}</td>
                        <td>{e.customer_name || <span className="sw-subtle">not provisioned</span>}</td>
                        <td>{e.compensation_type}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>
                          {usd(e.amount)}
                        </td>
                        <td>{day(e.collected_at)}</td>
                        <td>
                          {day(e.payable_at)}
                          {e.view === 'on_hold' && e.days_until_payable > 0 ? (
                            <div style={{ fontSize: 10, color: '#8a6100' }}>
                              in {e.days_until_payable}d
                            </div>
                          ) : null}
                        </td>
                        <td><Pill view={e.view} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : null}
    </SalesShell>
  )
}
