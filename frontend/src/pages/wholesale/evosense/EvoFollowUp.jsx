/* CALL-BACKS for this property's owner. Each one shows in the Callback Center
 * (Due now / Upcoming / Overdue) beside deal callbacks, so nothing promised
 * to an owner depends on somebody remembering it. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText, fmtWhen } from '../wsShared'
import { Panel } from '../ds/ds'

function tomorrowAt10() {
  const d = new Date(Date.now() + 86400000)
  d.setHours(10, 0, 0, 0)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T10:00`
}

export default function EvoFollowUp({ propertyId, items, onChange }) {
  const [when, setWhen] = useState(tomorrowAt10())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function run(fn) {
    setBusy(true); setErr('')
    try { await fn(); if (onChange) onChange() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const schedule = (e) => {
    e.preventDefault()
    run(async () => {
      await api.post(`/wholesale/evosense/properties/${propertyId}/follow-up`,
        { due_at: new Date(when).toISOString(), note })
      setNote('')
    })
  }
  const done = (id, kind) => run(() => api.post(`/wholesale/evosense/follow-ups/${id}/${kind}`, {}))

  return (
    <Panel title="Call-backs" id="followups" count={(items || []).length}>
      {(items || []).map((it) => (
        <div key={it.id} className="evo-small" style={{ padding: '8px 0', borderBottom: '1px solid var(--evo-border, #e5e7eb)' }}>
          <div><strong>Due {fmtWhen(it.due_at)}</strong>{it.assigned_to_name ? ` · ${it.assigned_to_name}` : ''}</div>
          {it.notes ? <div className="evo-muted">{it.notes}</div> : null}
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy} onClick={() => done(it.followup_id, 'complete')}>Done</button>
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => done(it.followup_id, 'cancel')}>Cancel</button>
          </div>
        </div>
      ))}
      <form onSubmit={schedule} style={{ display: 'grid', gap: 8, marginTop: 10 }}>
        <label className="evo-field__label" htmlFor="fu-when">Call the owner back on</label>
        <input id="fu-when" type="datetime-local" className="settings-input" value={when} onChange={(e) => setWhen(e.target.value)} />
        <input className="settings-input" placeholder="Why / what to ask (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <div><button type="submit" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy || !when}>Schedule call-back</button></div>
      </form>
      {err ? <p className="evo-small" style={{ color: 'var(--evo-danger-ink)' }}>{err}</p> : null}
      <p className="evo-muted evo-small" style={{ marginBottom: 0 }}>All call-backs are listed in the <Link to="/wholesale/callbacks">Callback Center</Link>.</p>
    </Panel>
  )
}
