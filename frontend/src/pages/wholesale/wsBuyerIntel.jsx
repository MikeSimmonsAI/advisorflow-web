/* CASH BUYER INTELLIGENCE - who to send this deal to first, and why.
 * Server: GET /wholesale/deals/{id}/buyer-intel (read-only).
 * Sending still happens below, through the existing matched-buyers list and its
 * Send button; this panel never sends anything and never buys a contact lookup.
 */
import { Fragment, useEffect, useState } from 'react'
import { api } from '../../api/client'

const TIER_CLASS = { VERIFIED_ACTIVE_BUYER: 'is-ok', POTENTIAL_BUYER: 'is-warn', UNVERIFIED_CONTACT: '' }
const sign = (n) => (n > 0 ? `+${n}` : `${n}`)

export function BuyerIntelPanel({ dealId, refreshKey }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)
  const [filter, setFilter] = useState('all')
  useEffect(() => {
    let live = true
    api.get(`/wholesale/deals/${dealId}/buyer-intel`)
      .then((r) => { if (live) setData(r) }).catch((e) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [dealId, refreshKey])
  if (err) return <div className="panel ws-panel"><div className="ws-error">{err}</div></div>
  if (!data) return <div className="panel ws-panel">Loading buyer intelligence…</div>
  const rows = data.buyers.filter((b) => filter === 'all' || b.tier.tier === filter)
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Who to send this deal to first</span>
        <span className="ws-muted uw-small">Price used: {data.price_note}</span>
      </div>
      <div className="uw-row" style={{ marginTop: 0 }}>
        {[['all', `All (${data.buyers.length})`],
          ['VERIFIED_ACTIVE_BUYER', `Verified active (${data.counts.VERIFIED_ACTIVE_BUYER})`],
          ['POTENTIAL_BUYER', `Potential (${data.counts.POTENTIAL_BUYER})`],
          ['UNVERIFIED_CONTACT', `Unverified (${data.counts.UNVERIFIED_CONTACT})`]].map(([k, l]) => (
          <button key={k} type="button" className={`btn btn--sm ${filter === k ? 'btn--primary' : 'btn--secondary'}`} onClick={() => setFilter(k)}>{l}</button>
        ))}
      </div>
      <p className="ws-muted uw-small">
        Verified active = proof they buy (an offer, a closing, or a recent deed to them in county records) <b>and</b> a contact that works
        (a reply, current proof of funds, or cash checked by your team). A "past deals" number someone typed is shown but never counted.
      </p>
      {!rows.length ? <p className="ws-muted">No buyers in this group.</p> : (
        <div className="uw-table-wrap"><table className="uw-table">
          <thead><tr><th>Buyer</th><th>Tier</th><th className="ws-num">Fit</th><th className="ws-num">Priority</th><th>Contact</th><th /></tr></thead>
          <tbody>{rows.map((b) => (
            <Fragment key={b.buyer_id}>
              <tr className={b.disqualified ? 'is-quiet' : ''}>
                <td>{b.name}{b.disqualified ? <div className="ws-muted uw-small">Not a fit: {b.disqualified_reason}</div> : null}</td>
                <td><span className={`ws-pill ${TIER_CLASS[b.tier.tier]}`}>{b.tier.label}</span></td>
                <td className="ws-num">{b.fit_score}</td>
                <td className="ws-num"><b>{b.priority}</b></td>
                <td className="uw-small">{[b.has_phone && 'phone', b.has_email && 'email'].filter(Boolean).join(' · ') || <span className="ws-muted">none on file</span>}</td>
                <td><button type="button" className="btn btn--secondary btn--sm" onClick={() => setOpen(open === b.buyer_id ? null : b.buyer_id)}
                            aria-expanded={open === b.buyer_id}>{open === b.buyer_id ? 'Hide' : 'Why'}</button></td>
              </tr>
              {open === b.buyer_id ? (
                <tr><td colSpan={6}>
                  <div className="uw-cases" style={{ marginTop: 0 }}>
                    <div className="uw-case"><div className="uw-case__head">Priority, point by point</div>
                      <ul className="uw-history">{b.explain.map((x, i) => <li key={i}><b>{sign(x.points)}</b> {x.why}</li>)}</ul></div>
                    <div className="uw-case"><div className="uw-case__head">Evidence</div>
                      <ul className="uw-history">
                        {b.tier.buys.map((x) => <li key={x}>Buys: {x}</li>)}
                        {b.tier.contact.map((x) => <li key={x}>Contact: {x}</li>)}
                        {b.tier.missing.map((x) => <li key={x} className="ws-muted">Missing: {x}</li>)}
                        {b.tier.claimed_note ? <li className="ws-muted">{b.tier.claimed_note}</li> : null}
                      </ul></div>
                    <div className="uw-case"><div className="uw-case__head">Buy-box fit</div>
                      <ul className="uw-history">{(b.fit_factors || []).map((f, i) => (
                        <li key={i}>{f.matched === true ? '✓' : f.matched === false ? '✗' : '–'} {f.detail || f.dimension}</li>))}</ul></div>
                  </div>
                </td></tr>
              ) : null}
            </Fragment>
          ))}</tbody>
        </table></div>
      )}
    </div>
  )
}
