/* What happened at a visit (#405): Completed / No-show / Cancelled / Follow-up
   needed. One tap saves it (POST /program/appointments/{id}/outcome). Optional:
   every visit gets one survey 24 hours after it ends automatically; No-show
   and Cancelled stop it. Used on Appointments and on a family's record. */
import { useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, errText } from './ui'

export const OUTCOMES = [
  ['completed', 'Completed', 'ok'],
  ['no_show', 'No-show', 'warn'],
  ['cancelled', 'Cancelled', 'warn'],
  ['follow_up_needed', 'Follow-up needed', 'info'],
]
const TONE = Object.fromEntries(OUTCOMES.map(([k, , t]) => [k, t]))

const btn = (on, tone) => ({
  minHeight: 44, minWidth: 44, padding: '8px 14px', borderRadius: 10, fontSize: 14, fontWeight: 700,
  border: `2px solid ${on ? (tone === 'ok' ? '#2f7a4f' : tone === 'warn' ? '#b7791f' : '#2f6f73') : '#d5e0e7'}`,
  background: on ? (tone === 'ok' ? '#e7f5ec' : tone === 'warn' ? '#fdf3e2' : '#e6f2f2') : '#fff',
  color: '#173247', cursor: 'pointer',
})

export default function VisitOutcome({ appt, onSaved }) {
  const [busy, setBusy] = useState('')
  const inFlight = useRef(false)              // blocks a second tap before React re-renders
  const [msg, setMsg] = useState(null)        // {tone, text}
  const [saved, setSaved] = useState(null)    // the last server answer

  // Fresh data from the server (a reload, another person's change) wins over
  // this component's last answer.
  useEffect(() => { setSaved(null) }, [appt.attendance, appt.survey_note, appt.can_record])

  const current = saved?.attendance ?? appt.attendance
  const note = saved?.survey_note ?? appt.survey_note

  if (!appt.can_record) {
    return current ? (
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Chip tone={TONE[current] || ''} plain>{(OUTCOMES.find(o => o[0] === current) || [])[1] || current}</Chip>
        {note && <span className="sci-micro sci-muted">{note}</span>}
      </div>
    ) : (appt.started === false ? <span className="sci-micro sci-muted">You can record the outcome once the visit starts.</span> : null)
  }

  async function choose(value, e) {
    e.stopPropagation()
    if (busy || inFlight.current) return                    // no double submit
    if (value === 'completed' && !appt.ended) {
      setMsg({ tone: 'bad', text: 'You can mark it Completed once the visit is over.' })
      return
    }
    inFlight.current = true
    setBusy(value); setMsg(null)
    try {
      const r = await api.post(`/program/appointments/${appt.id}/outcome`, { attendance: value, location_id: appt.location_id || null })
      setSaved(r)
      setMsg({ tone: 'ok', text: r.unchanged ? `Already saved as ${r.label}.` : `Saved: ${r.label}.` })
      onSaved && onSaved(r)
    } catch (err) {
      setMsg({ tone: 'bad', text: `Not saved: ${errText(err)}` })
    } finally {
      inFlight.current = false
      setBusy('')
    }
  }

  return (
    <div onClick={e => e.stopPropagation()} style={{ marginTop: 6 }}>
      <div role="group" aria-label={`What happened at ${appt.name || 'this'} visit`}
        style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(128px, 1fr))', gap: 6,
                 maxWidth: 'min(640px, calc(100vw - 84px))' }}>
        {OUTCOMES.map(([k, label, tone]) => {
          const on = current === k
          const blocked = k === 'completed' && !appt.ended
          return (
            <button key={k} type="button" aria-pressed={on} disabled={!!busy || blocked}
              title={blocked ? 'Available once the visit is over' : undefined}
              style={{ ...btn(on, tone), opacity: blocked ? 0.5 : 1 }} onClick={e => choose(k, e)}>
              {busy === k ? 'Saving…' : on ? `✓ ${label}` : label}
            </button>
          )
        })}
      </div>
      <div aria-live="polite" style={{ minHeight: 18, marginTop: 4 }}>
        {msg ? <span className="sci-micro" style={{ color: msg.tone === 'ok' ? '#2f7a4f' : '#b42318', fontWeight: 600 }}>{msg.text}</span> : null}
        {note && (!msg || msg.tone === 'ok') ? <span className="sci-micro sci-muted"> {note}</span> : null}
      </div>
    </div>
  )
}
