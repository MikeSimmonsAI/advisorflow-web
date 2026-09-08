/* MY COMPENSATION — what one salesperson has projected, earned, on hold,
 * payable and been paid.
 *
 * SCOPED BY THE TOKEN, NOT BY THIS FILE. The endpoint takes no payee
 * parameter; it reads the caller's own id from their token. There is nothing
 * to tamper with in the request, which is what makes this safe rather than
 * merely tidy — a filter a client could change is not a boundary.
 *
 * NOTHING HERE IS ADMINISTRATIVE. There is no rate, no plan, no way to mark
 * anything paid, and no other person's name. A rep can see what they are owed
 * and why; they cannot change what anybody earns or learn what anybody else
 * makes. The attention panel below deliberately omits the "you cannot settle
 * this" item the manager screen shows — telling a salesperson they lack a
 * power they were never meant to have is noise, not information.
 *
 * THE SAME VISUAL SYSTEM AS THE COMMAND CENTER, ON PURPOSE. A rep and their
 * manager looking at the same five figures should not have to translate
 * between two layouts to agree on a number. Projected is dashed and set
 * apart; payable carries the accent; on hold is amber. One vocabulary.
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

function plural(n, one, many) { return n === 1 ? one : many }

const VIEW_TONE = {
  on_hold: { cls: 'sw-chip sw-amber', label: 'ON HOLD' },
  payable_now: { cls: 'sw-chip sw-green', label: 'PAYABLE' },
  paid: { cls: 'sw-chip', label: 'PAID' },
  void: { cls: 'sw-chip sw-red', label: 'VOID' },
}

function Pill({ view }) {
  const t = VIEW_TONE[view] || VIEW_TONE.paid
  return <span className={t.cls}>{t.label}</span>
}

function Tile({ label, value, sub, tone, lead, forecast }) {
  const cls = ['sw-tile', 'is-static']
  if (tone) cls.push('t-' + tone)
  if (lead) cls.push('is-lead')
  if (forecast) cls.push('is-forecast')
  return (
    <div className={cls.join(' ')}>
      <span className="sw-tile-label">{label}</span>
      <div className="sw-tile-value">{value}</div>
      {sub ? <div className="sw-tile-sub">{sub}</div> : null}
    </div>
  )
}

function Sev({ severity, label }) {
  return <span className={'sw-sev sv-' + (severity || 'no_data')}>{label}</span>
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
  const att = d?.attention

  return (
    <SalesShell title="My Compensation"
                subtitle="What you have earned, what is on hold, and what has been paid.">
      {err ? <div className="sw-err">{err}</div> : null}
      {!d && !err ? <div className="sw-muted">Loading…</div> : null}

      {d ? (
        <>
          {/* THE FORECAST, HELD APART. The difference between "expect" and
              "owed" is the whole point of this screen, so it is drawn rather
              than only written. */}
          <div className="sw-cc-forecast">
            <Tile forecast label="Projected" value={usd(p.amount)}
                  sub={p.deal_count + ' open ' + plural(p.deal_count, 'deal', 'deals')} />
            <p className="sw-cc-forecast-note">
              <span>
                <b>Projected is not owed to you.</b> It is what your open deals
                would pay if they closed on today's terms. Commission is earned
                only when the customer payment actually lands, and becomes
                payable after your plan's holdback.
                {p.unconfigured_deals > 0 ? (
                  <> {p.unconfigured_deals}{' '}
                    {plural(p.unconfigured_deals, 'deal is', 'deals are')} on a
                    package with no commission rate configured yet — those are
                    excluded rather than counted as zero.</>
                ) : null}
                {p.pending_approval_deals > 0 ? (
                  <> {usd(p.pending_approval_amount)} sits on{' '}
                    {p.pending_approval_deals}{' '}
                    {plural(p.pending_approval_deals, 'deal', 'deals')} still
                    waiting on pricing approval, and is not included above.</>
                ) : null}
              </span>
            </p>
          </div>

          <div className="sw-tiles">
            <Tile tone="earned" label="Earned" value={usd(s.earned_total)}
                  sub={s.earned_count + ' ' + plural(s.earned_count, 'entry', 'entries')
                       + ' · lifetime'} />
            <Tile tone="hold" label="On hold" value={usd(s.on_hold.amount)}
                  sub={s.on_hold.count + ' · holdback not elapsed'} />
            <Tile tone="payable" lead label="Payable" value={usd(s.payable_now.amount)}
                  sub={s.payable_now.count + ' ready to be paid'} />
            <Tile tone="paid" label="Paid" value={usd(s.paid.amount)}
                  sub={s.paid.count + ' settled'} />
          </div>

          {/* WHEN YOUR HOLDBACKS ELAPSE. Same source as the manager screen —
              real entries with stored payable dates, no pipeline forecast. */}
          {s.upcoming?.length ? (
            <div className="sw-card sw-mt">
              <div className="sw-card-h">
                <div>
                  <h3>BECOMING PAYABLE</h3>
                  <small>
                    Commission you have already earned, by the date its holdback
                    elapses. Open deals are not counted here.
                  </small>
                </div>
              </div>
              <div className="sw-card-b" style={{ paddingTop: 0 }}>
                <div className="sw-becoming">
                  {s.upcoming.map(u => (
                    <div className="sw-becoming-cell" key={u.window_days}>
                      <span>Next {u.window_days} days</span>
                      <b>{usd(u.amount)}</b>
                      <small>{u.count} {plural(u.count, 'entry', 'entries')}</small>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}

          {/* WORTH KNOWING — the rep's own version of the manager panel.
              Server-computed and narrowed to this person's rows by the token.
              Every item is informational: there is no administrative action a
              salesperson can take from here, so no item carries a button that
              would fail if they pressed it. */}
          {att?.items?.length ? (
            <div className="sw-card sw-mt">
              <div className="sw-card-h">
                <div>
                  <h3>WORTH KNOWING</h3>
                  <small>
                    Things affecting your numbers. Counted from your own
                    entries — not reminders.
                  </small>
                </div>
                <span style={{ marginLeft: 'auto' }}>
                  <Sev severity={att.overall} label={att.overall_label} />
                </span>
              </div>
              <div className="sw-card-b" style={{ paddingTop: 0 }}>
                <div className="sw-att">
                  {att.items.map(it => (
                    <div className={'sw-att-row sv-' + it.severity} key={it.key}>
                      <div className="sw-att-bar" />
                      <div className="sw-att-body">
                        <div className="sw-att-t">
                          <Sev severity={it.severity} label={it.severity_label} />
                          <b>{it.title}</b>
                        </div>
                        <p className="sw-att-d">{it.detail}</p>
                      </div>
                      <div className="sw-att-side" />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}

          <div className="sw-card sw-mt">
            <div className="sw-card-h">
              <div>
                <h3>MY COMMISSION ENTRIES ({d.entries.length})</h3>
                <small>Every entry, and the customer payment that created it.</small>
              </div>
            </div>
            <div className="sw-card-b">
              {!d.entries.length ? (
                <div className="sw-blank">
                  <b>Nothing yet.</b>
                  <p>
                    An entry appears here when a customer payment on one of your
                    won deals is actually collected — not when the deal is
                    marked Won. Until then your work shows up in Projected above.
                  </p>
                </div>
              ) : (
                <div className="sw-tablewrap">
                  <table className="sw-table" style={{ minWidth: 720 }}>
                    <thead>
                      <tr>
                        <th>Deal</th><th>Customer</th><th>Type</th>
                        <th className="sw-num">Amount</th>
                        <th>Collected</th><th>Payable</th><th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.entries.map(e => (
                        <tr key={e.id}>
                          <td>{e.deal_name || '—'}</td>
                          <td>{e.customer_name
                            || <span className="sw-muted">not provisioned</span>}</td>
                          <td>{e.compensation_type}</td>
                          <td className="sw-num"><b>{usd(e.amount)}</b></td>
                          <td>{day(e.collected_at)}</td>
                          <td>
                            {day(e.payable_at)}
                            {e.view === 'on_hold' && e.days_until_payable > 0 ? (
                              <span className="sw-cap">in {e.days_until_payable}d</span>
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
          </div>
        </>
      ) : null}
    </SalesShell>
  )
}
