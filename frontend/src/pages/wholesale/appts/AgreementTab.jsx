/* THE PURCHASE AGREEMENT, SENT FROM THE PHONE (build list #15).
 * The price is the rep's standing offer (inside the limit or approved); the
 * company's signer is an admin, never the rep. The seller gets the document
 * to e-sign, with the Texas notice it already carries. */
import { useState } from 'react'
import { api } from '../../../api/client'
import { Alert } from '../ds/ds'
import { tomorrowCentral } from './apptTime'
import './appts.css'

const money = (n) => (n || n === 0 ? '$' + Number(n).toLocaleString() : '-')

export default function AgreementTab({ apptId, sellerGuess }) {
  const [f, setF] = useState({ seller_name: sellerGuess || '', seller_email: '', earnest_money: '', option_days: '10',
    title_company: '', closing_date: '' })
  const [prev, setPrev] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const on = (k) => (e) => { setF((x) => ({ ...x, [k]: e.target.value })); setPrev(null) }

  async function run(action) {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.post(`/wholesale/appointments/${apptId}/agreement`, { ...f, action })
      setPrev(r)
      if (action === 'send') setMsg(r.message || (r.sent ? 'Sent for signature.' : 'Not sent.'))
    } catch (e) { setErr(e.message || 'That did not work.') } finally { setBusy(false) }
  }

  return (
    <div className="of-wrap">
      <p className="evo-small evo-muted" style={{ margin: 0 }}>
        The price comes from your offer on the Offer tab. Your company's signature comes from the office, not you.</p>
      <div className="ap-form">
        <label className="ap-lab ap-lab--full">Seller's full legal name (as on the deed)
          <input className="ap-input" value={f.seller_name} onChange={on('seller_name')} placeholder="Everyone on the deed" /></label>
        <label className="ap-lab ap-lab--full">Seller's email (they sign there)
          <input className="ap-input" type="email" value={f.seller_email} onChange={on('seller_email')} /></label>
        <label className="ap-lab">Earnest money
          <input className="ap-input" inputMode="decimal" value={f.earnest_money} onChange={on('earnest_money')} placeholder="$" /></label>
        <label className="ap-lab">Option period (days)
          <input className="ap-input" inputMode="numeric" value={f.option_days} onChange={on('option_days')} /></label>
        <label className="ap-lab">Title company
          <input className="ap-input" value={f.title_company} onChange={on('title_company')} /></label>
        <label className="ap-lab">Closing date
          <input className="ap-input" type="date" min={tomorrowCentral()} value={f.closing_date} onChange={on('closing_date')} /></label>
      </div>
      <div className="ap-actions">
        <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={() => run('preview')}>{busy ? 'Working…' : 'Check it'}</button>
        <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !prev || prev.missing.length > 0} onClick={() => run('send')}>Send to the seller to sign</button>
      </div>
      {prev ? (
        <div className="ag-sum">
          <div>Price <strong>{money(prev.price)}</strong> · Signs for your company: <strong>{prev.company_signer}</strong></div>
          {prev.missing.length ? <div className="of-over">Still needed: {prev.missing.join(', ')}</div> : <div className="evo-small">Everything needed is filled in.</div>}
          <iframe title="Purchase agreement" className="ag-doc" sandbox="" srcDoc={prev.html} />
        </div>
      ) : null}
      <Alert kind="ok">{msg}</Alert>
      <Alert>{err}</Alert>
    </div>
  )
}
