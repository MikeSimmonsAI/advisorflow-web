/* OWNER PERMISSION FROM THE PHONE (build list #15). The rep makes the
 * owner's personal permission link and texts it from their OWN phone (the
 * system sends nothing). Permission exists only when the owner ticks the box
 * on that page. */
import { useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Panel } from '../ds/ds'
import './appts.css'

export default function PermissionLink({ propertyId, phone, owner }) {
  const [p, setP] = useState(phone || '')
  const [name, setName] = useState(owner || '')
  const [link, setLink] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [copied, setCopied] = useState(false)
  async function make() {
    setBusy(true); setErr(null)
    try { setLink(await api.post(`/wholesale/evosense/properties/${propertyId}/permission-link`, { phone: p, owner_name: name || null })) }
    catch (e) { setErr(e.message || 'Could not make the link.') } finally { setBusy(false) }
  }
  const digits = p.replace(/[^\d+]/g, '')
  const body = link ? encodeURIComponent(link.text || link.url) : ''
  return (
    <Panel title="Owner permission" hint="So the office can follow up by text and phone.">
      {!link ? (
        <>
          <div className="ap-form">
            <label className="ap-lab">Owner's cell<input className="ap-input" inputMode="tel" value={p} onChange={(e) => setP(e.target.value)} /></label>
            <label className="ap-lab">Owner's name<input className="ap-input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          </div>
          <button type="button" className="evo-btn evo-btn--secondary" style={{ marginTop: 10 }} disabled={busy || digits.length < 10} onClick={make}>
            {busy ? 'Making…' : 'Make their permission link'}</button>
        </>
      ) : (
        <div className="of-wrap">
          <p className="evo-small" style={{ margin: 0 }}>Send it from your phone while you are with them. Permission counts only when they tap yes.</p>
          <div className="ap-actions">
            <a className="evo-btn evo-btn--primary" href={`sms:${digits}${/iPhone|iPad/.test(navigator.userAgent) ? '&' : '?'}body=${body}`}>Text it to {digits}</a>
            {navigator.share ? <button type="button" className="evo-btn evo-btn--ghost" onClick={() => navigator.share({ text: link.text, url: link.url }).catch(() => null)}>Share…</button> : null}
            <button type="button" className="evo-btn evo-btn--ghost" onClick={() => { navigator.clipboard?.writeText(link.text || link.url); setCopied(true) }}>{copied ? 'Copied' : 'Copy'}</button>
          </div>
        </div>
      )}
      <Alert>{err}</Alert>
    </Panel>
  )
}
