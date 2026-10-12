/* Offers above a rep's limit, waiting for you (build list #20). Shown at the
 * top of Appointments to admins only; everyone else never sees it. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { Alert, Panel } from '../ds/ds'
import './appts.css'

const money = (n) => (n || n === 0 ? '$' + Number(n).toLocaleString() : '-')

function Row({ o, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  async function decide(approve) {
    setBusy(true); setErr(null)
    try { await api.post(`/wholesale/appointments/offers/${o.id}/decide`, { approve, note }); onDone(o.id) }
    catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }
  return (
    <li className="ap-card">
      <div className="ap-card__top">
        <span className="ap-time">{money(o.offer)}</span>
        <span className="ap-title">{o.address || 'Property'}</span>
      </div>
      <div className="ap-meta">
        <span>{o.requested_by_name}</span>
        <span>Their limit {money(o.max_offer)}</span>
        <span>Formula max {money(o.mao)}</span>
        <span>ARV {money(o.arv)} · repairs {money(o.repairs)}</span>
      </div>
      {o.note ? <p className="ap-notes">{o.note}</p> : null}
      <input className="ap-input" placeholder="Note back to the rep (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note to rep" />
      <div className="ap-actions">
        <button type="button" className="evo-btn evo-btn--success evo-btn--sm" disabled={busy} onClick={() => decide(true)}>Approve</button>
        <button type="button" className="evo-btn evo-btn--danger evo-btn--sm" disabled={busy} onClick={() => decide(false)}>Decline</button>
        {o.evosense_property_id ? <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/evosense/property/${o.evosense_property_id}`}>Open property</Link> : null}
      </div>
      <Alert>{err}</Alert>
    </li>
  )
}

export default function PendingOffers() {
  const [items, setItems] = useState(null)
  useEffect(() => {
    api.get('/wholesale/appointments/offers/pending').then((r) => setItems(r.items || [])).catch(() => setItems(null))
  }, [])
  if (!items || !items.length) return null
  return (
    <Panel title="Offers waiting for you" count={items.length} hint="A rep wants to offer more than their limit.">
      <ul className="ap-list">{items.map((o) => <Row key={o.id} o={o} onDone={(id) => setItems((xs) => xs.filter((x) => x.id !== id))} />)}</ul>
    </Panel>
  )
}
