/* THE OFFER, AT THE KITCHEN TABLE (build list #14, #20).
 * The rep enters ARV and repairs and sees ONE number: the most they may
 * offer. Your investor % and fee never reach this screen - the server works
 * the formula out. An offer above the limit is sent to you to approve. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Tag } from '../ds/ds'
import './appts.css'

const money = (n) => (n || n === 0 ? '$' + Number(n).toLocaleString() : '-')
const STATUS = { within: ['Within your limit', 'live'], pending: ['Waiting for approval', 'info'], approved: ['Approved', 'live'], declined: ['Declined', 'danger'] }

export default function OfferTab({ apptId, repairsTotal }) {
  const [arv, setArv] = useState('')
  const [repairs, setRepairs] = useState(repairsTotal ? String(repairsTotal) : '')
  const [quote, setQuote] = useState(null)
  const [offer, setOffer] = useState('')
  const [note, setNote] = useState('')
  const [list, setList] = useState([])
  const [arvOnFile, setArvOnFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)

  const load = () => api.get(`/wholesale/appointments/${apptId}/offers`).then((r) => {
    setList(r.items || [])
    if (r.arv_on_file) { setArvOnFile(r.arv_on_file); setArv((cur) => cur || String(r.arv_on_file)) }
  }).catch(() => null)
  useEffect(() => { load() }, [apptId])
  useEffect(() => {
    setQuote(null)
    if (!arv) return undefined
    const t = setTimeout(() => {
      api.post(`/wholesale/appointments/${apptId}/offer-quote`, { arv, repairs: repairs || '0' })
        .then(setQuote).catch((e) => setErr(e.message || 'Could not work it out.'))
    }, 400)
    return () => clearTimeout(t)
  }, [arv, repairs, apptId])

  const n = Number(String(offer).replace(/[$,]/g, '')) || 0
  const over = quote && quote.max_offer != null && n > quote.max_offer
  async function record() {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const o = await api.post(`/wholesale/appointments/${apptId}/offers`, { arv, repairs: repairs || '0', offer, note })
      setMsg(o.status === 'pending' ? 'Sent for approval. You will get a notification with the answer.' : 'Offer recorded.')
      setOffer(''); setNote(''); load()
    } catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }

  return (
    <div className="of-wrap">
      <div className="ap-form">
        <label className="ap-lab">After-repair value (ARV)
          <input className="ap-input" inputMode="decimal" placeholder="$ from the office or comps" value={arv} onChange={(e) => setArv(e.target.value)} />
          {arvOnFile ? <span className="evo-muted evo-small" style={{ fontWeight: 400 }}>{money(arvOnFile)} on file from verified sold comps</span> : null}</label>
        <label className="ap-lab">Repairs
          <input className="ap-input" inputMode="decimal" placeholder="$ from your walkthrough" value={repairs} onChange={(e) => setRepairs(e.target.value)} /></label>
      </div>
      <div className="of-max" aria-live="polite">
        <span>Most you can offer</span>
        <strong>{quote ? (quote.max_offer != null ? money(quote.max_offer) : 'Ask the office') : '-'}</strong>
        {quote && quote.mao != null ? <span className="evo-muted evo-small">Formula max {money(quote.mao)} · limit {quote.offer_limit_pct}%</span> : null}
      </div>
      <div className="ap-form">
        <label className="ap-lab">Your offer to the seller
          <input className="ap-input" inputMode="decimal" placeholder="$" value={offer} onChange={(e) => setOffer(e.target.value)} /></label>
        <label className="ap-lab">Note (optional)
          <input className="ap-input" placeholder="Why this number" value={note} onChange={(e) => setNote(e.target.value)} /></label>
      </div>
      {over ? <p className="of-over">That is {money(n - quote.max_offer)} over your limit. It will be sent for approval before you can promise it.</p> : null}
      <button type="button" className={`evo-btn ${over ? 'evo-btn--secondary' : 'evo-btn--primary'}`} disabled={busy || !arv || !offer} onClick={record}>
        {busy ? 'Saving…' : over ? 'Ask for approval' : 'Record offer'}</button>
      <Alert kind="ok">{msg}</Alert>
      <Alert>{err}</Alert>
      {list.length ? (
        <ul className="of-list">
          {list.map((o) => {
            const [l, tone] = STATUS[o.status] || [o.status, null]
            return (
              <li key={o.id}>
                <strong>{money(o.offer)}</strong> <Tag kind={tone || undefined}>{l}</Tag>
                <span className="evo-muted evo-small"> ARV {money(o.arv)} · repairs {money(o.repairs)} · {o.requested_by_name}</span>
                {o.decision_note ? <div className="evo-small">{o.decided_by_name}: {o.decision_note}</div> : null}
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}
