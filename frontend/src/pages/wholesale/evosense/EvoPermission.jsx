/* PERMISSION TO FOLLOW UP. The caller creates a personal link for the number
 * the owner gave on the call and sends it (email, or one text from her own
 * phone). Creating the link grants nothing: permission exists only once the
 * OWNER ticks the box on that page. Until then the AI may not follow up. */
import { useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Panel, Tag } from '../ds/ds'

const STATE = {
  on_file: ['Permission on file', 'live', 'The owner confirmed on their link. Follow-up may continue for that number.'],
  link_sent: ['Link sent - waiting on owner', undefined, 'Nothing is permitted until the owner ticks the box. Follow up by phone if they haven\'t confirmed in 2 days.'],
  opted_out: ['Opted out', 'danger', 'The owner withdrew permission. No automated follow-up.'],
  not_yet: ['Not yet', undefined, 'Ask on the call, then send the owner their link.'],
}

export default function EvoPermission({ propertyId, permission, ownerName, defaultPhone, onChange }) {
  const [phone, setPhone] = useState(defaultPhone || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [made, setMade] = useState(null)
  const [copied, setCopied] = useState('')
  const st = STATE[(permission && permission.state) || 'not_yet'] || STATE.not_yet

  async function create(e) {
    e.preventDefault()
    setBusy(true); setErr(''); setMade(null)
    try {
      setMade(await api.post(`/wholesale/evosense/properties/${propertyId}/permission-link`,
        { phone, owner_name: ownerName || null }))
      if (onChange) onChange()
    } catch (x) { setErr(errText(x)) } finally { setBusy(false) }
  }
  async function copy(what, text) {
    try { await navigator.clipboard.writeText(text); setCopied(what) } catch { setCopied('') }
  }

  return (
    <Panel title="Permission to follow up" id="permission">
      <div style={{ marginBottom: 8 }}><Tag kind={st[1]}>{st[0]}</Tag></div>
      <p className="evo-muted evo-small" style={{ marginTop: 0 }}>{st[2]}</p>
      {(permission && permission.phones || []).map((x) => (
        <div key={x.phone_last4} className="evo-small evo-muted">Number ending {x.phone_last4}: {x.state.replace('_', ' ')}</div>
      ))}
      <form onSubmit={create} style={{ display: 'grid', gap: 8, marginTop: 12 }}>
        <label className="evo-field__label" htmlFor="perm-phone">Owner's mobile number (the one they gave you)</label>
        <input id="perm-phone" className="settings-input" value={phone} inputMode="tel"
               onChange={(e) => setPhone(e.target.value)} placeholder="214-555-0142" />
        <div><button type="submit" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy || phone.replace(/\D/g, '').length < 10}>
          {busy ? 'Creating…' : 'Create permission link'}</button></div>
      </form>
      {err ? <p className="evo-small" style={{ color: 'var(--evo-danger-ink)' }}>{err}</p> : null}
      {made ? (
        <div style={{ marginTop: 12, display: 'grid', gap: 8 }} className="evo-small">
          <div><strong>Send this to the owner</strong> (email, or one text from your phone):</div>
          <textarea className="evo-textarea" rows={4} readOnly value={made.text} />
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={() => copy('msg', made.text)}>
              {copied === 'msg' ? 'Copied' : 'Copy message'}</button>
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => copy('url', made.url)}>
              {copied === 'url' ? 'Copied' : 'Copy link only'}</button>
            <a className="evo-btn evo-btn--ghost evo-btn--sm"
               href={`mailto:?subject=${encodeURIComponent(made.email_subject)}&body=${encodeURIComponent(made.email_body)}`}>
              Open in email</a>
          </div>
          <div className="evo-muted">The link works for 14 days. Sending a new link retires the old one.</div>
        </div>
      ) : null}
    </Panel>
  )
}
