/* DISPOSITION - engagement, offers side by side, follow-ups, closing checklist, analytics.
 * Server: GET /wholesale/deals/{id}/disposition-summary, GET /wholesale/disposition/analytics.
 * Read-only. Choosing a buyer, recording replies and closing stay in the existing
 * buyer board and closing workspace below.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney } from './wsShared'

const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const avg = (o, unit = 'days') => (o && o.value != null ? `${o.value} ${unit} (n=${o.n})` : 'not enough data')

function Analytics() {
  const [a, setA] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.get('/wholesale/disposition/analytics').then(setA).catch((e) => setErr(e.message)) }, [])
  if (err) return <div className="ws-error">{err}</div>
  if (!a) return <p className="ws-muted">Loading…</p>
  return (
    <>
      <div className="uw-metrics">
        {[['Deals marketed', a.deals_marketed], ['Deal sheets sent', a.deal_sheets_sent], ['Reply rate', pct(a.reply_rate.value)],
          ['Offers', a.offers], ['Deals closed', a.deals_closed], ['Fees collected', fmtMoney(a.fees_collected)]].map(([k, v]) => (
          <div key={k} className="uw-metric"><div className="uw-metric__label">{k}</div><div className="uw-metric__value">{v}</div></div>))}
      </div>
      <dl className="uw-dl uw-dl--wide">
        <div><dt>Sent to first offer</dt><dd>{avg(a.days_sent_to_first_offer)}</dd></div>
        <div><dt>Contract to buyer chosen</dt><dd>{avg(a.days_contract_to_buyer)}</dd></div>
        <div><dt>Buyer chosen to closing</dt><dd>{avg(a.days_buyer_to_close)}</dd></div>
        <div><dt>Average assignment fee</dt><dd>{a.average_assignment_fee.value != null ? `${fmtMoney(a.average_assignment_fee.value)} (n=${a.average_assignment_fee.n})` : 'not enough data'}</dd></div>
      </dl>
      <p className="ws-muted uw-small">Last {a.window_days} days, real deals only. {a.note}</p>
    </>
  )
}

export function DispositionPanel({ dealId, refreshKey }) {
  const [s, setS] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let live = true
    api.get(`/wholesale/deals/${dealId}/disposition-summary`).then((r) => { if (live) setS(r) }).catch((e) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [dealId, refreshKey])
  if (err) return <div className="panel ws-panel"><div className="ws-error">{err}</div></div>
  if (!s) return <div className="panel ws-panel">Loading disposition…</div>
  const ck = s.closing_checklist
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title"><span>Disposition</span>
        <span className="ws-muted uw-small">Closing checklist {ck.done}/{ck.total}{ck.next ? ` · next: ${ck.next}` : ''}</span></div>

      {s.readiness ? (
        <div className={s.readiness.ready ? 'ws-muted uw-small' : 'uw-missing'} style={{ marginBottom: 8 }}>
          <b>{s.readiness.ready ? 'Ready to market' : 'Not ready to market yet'}</b> ·{' '}
          {s.readiness.checks.map((c) => `${c.ok ? '✓' : '○'} ${c.check}`).join(' · ')} · {s.readiness.eligible_buyers} eligible buyer(s)
          {s.readiness.ineligible_buyers.length ? <div className="uw-small">Not eligible: {s.readiness.ineligible_buyers.slice(0, 5).map((x) => `${x.buyer} (${x.why})`).join('; ')}</div> : null}
        </div>
      ) : null}
      <div className="uw-flow" aria-label="Engagement">
        {s.funnel.map((f) => (
          <span key={f.key} className="uw-step" style={{ cursor: 'default' }} title={f.note || ''}>
            <span className="uw-step__icon" aria-hidden="true">{f.count}</span>{f.label}</span>))}
      </div>

      {s.follow_ups.length ? (
        <div className="uw-missing" style={{ marginBottom: 10 }}><b>Follow up ({s.follow_ups.length})</b>
          <ul>{s.follow_ups.map((x, i) => <li key={i}>{x.buyer ? <b>{x.buyer}: </b> : null}{x.why}</li>)}</ul></div>
      ) : <p className="ws-muted uw-small">No follow-ups due.</p>}

      {s.offers.length ? (
        <>
          <h4 style={{ margin: '8px 0 4px' }}>Offers side by side</h4>
          {s.highlights.length ? <ul className="uw-history">{s.highlights.map((h) => (
            <li key={h.what}><span className="ws-muted">{h.what}:</span> <b>{h.buyer}</b>{typeof h.value === 'number' && h.what !== 'Offers with verified proof of funds' ? ` · ${fmtMoney(h.value)}` : typeof h.value === 'string' ? ` · ${h.value}` : ''}</li>))}</ul> : null}
          <div className="uw-table-wrap"><table className="uw-table">
            <thead><tr><th>Buyer</th><th className="ws-num">Offer</th><th className="ws-num">To you after contract</th><th>Funds</th><th>Proof of funds</th><th>Proposed close</th><th>Buyer tier</th></tr></thead>
            <tbody>{s.offers.map((o) => (
              <tr key={o.outreach_id}><td>{o.buyer}{o.selected ? <span className="ws-pill is-ok" style={{ marginLeft: 4 }}>chosen</span> : null}</td>
                <td className="ws-num">{fmtMoney(o.offer)}</td><td className="ws-num">{o.net != null ? fmtMoney(o.net) : '—'}</td>
                <td>{(o.financing || '—').replace('_', ' ')}</td>
                <td><span className={`ws-pill ${o.pof_status === 'verified' ? 'is-ok' : 'is-warn'}`}>{o.pof_status.replace('_', ' ')}</span></td>
                <td>{o.target_close_date || (o.typical_close_days ? `usually ${o.typical_close_days} days` : '—')}</td>
                <td className="uw-small">{o.tier || '—'}</td></tr>))}</tbody>
          </table></div>
          <p className="ws-muted uw-small">Facts only - choose the buyer on the Buyer matching board.</p>
        </>
      ) : null}

      <details className="uw-steps"><summary>Closing checklist</summary>
        <ul className="uw-history" style={{ listStyle: 'none', paddingLeft: 0 }}>{ck.steps.map((x) => (
          <li key={x.label}>{x.done ? '✓' : '○'} {x.label}</li>))}</ul></details>
      <details className="uw-steps"><summary>Disposition numbers across all deals</summary><Analytics /></details>
    </div>
  )
}
