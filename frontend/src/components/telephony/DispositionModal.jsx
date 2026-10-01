// Record what happened on a call: outcome, notes, and optionally a callback.
// POST /calls/{id}/disposition. A callback becomes a Wholesale seller callback
// for a wholesale seller and a follow-up task otherwise - the server decides.
import { useState } from 'react'
import { api } from '../../api/client'
import './telephony.css'

export const OUTCOMES = [
  ['connected', 'Connected - spoke with them'],
  ['interested', 'Interested'],
  ['callback_requested', 'Asked for a call back'],
  ['appointment_set', 'Appointment set'],
  ['not_interested', 'Not interested'],
  ['left_voicemail', 'Left a voicemail myself'],
  ['no_answer', 'No answer'],
  ['busy', 'Busy'],
  ['wrong_number', 'Wrong number'],
  ['other', 'Other'],
]

export default function DispositionModal({ callId, initialOutcome, onClose, onSaved }) {
  const [outcome, setOutcome] = useState(initialOutcome || 'connected')
  const [notes, setNotes] = useState('')
  const [when, setWhen] = useState('')
  const [followUp, setFollowUp] = useState(false)
  const [followTitle, setFollowTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function save(e) {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      const res = await api.post(`/calls/${callId}/disposition`, {
        outcome, notes: notes.trim() || null,
        callback_at: when ? new Date(when).toISOString() : null,
        follow_up: followUp && !when,
        follow_up_title: followUp && !when ? (followTitle.trim() || null) : null,
      })
      onSaved && onSaved(res)
      onClose && onClose()
    } catch (e2) {
      setErr(e2.message || 'Could not save the outcome.')
    } finally { setBusy(false) }
  }

  return (
    <div className="tel-modal-back" role="dialog" aria-modal="true" aria-label="Call outcome"
         onClick={e => { if (e.target === e.currentTarget && !busy) onClose && onClose() }}>
      <form className="tel-modal" onSubmit={save}>
        <h3>How did the call go?</h3>
        <label className="tel-field">Outcome
          <select value={outcome} onChange={e => setOutcome(e.target.value)}>
            {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="tel-field">Notes
          <textarea rows={3} maxLength={5000} value={notes} onChange={e => setNotes(e.target.value)}
                    placeholder="What was said, what was agreed" />
        </label>
        <label className="tel-field">Schedule a callback (optional)
          <input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} />
        </label>
        {!when && (
          <label className="tel-check">
            <input type="checkbox" checked={followUp} onChange={e => setFollowUp(e.target.checked)} />
            Create a follow-up task
          </label>
        )}
        {!when && followUp && (
          <label className="tel-field">Task title (optional)
            <input type="text" maxLength={300} value={followTitle} onChange={e => setFollowTitle(e.target.value)}
                   placeholder="Follow up with this contact" />
          </label>
        )}
        {err && <div className="tel-box tel-box--error">{err}</div>}
        <div className="tel-modal-foot">
          <button type="button" className="tel-btn tel-btn--ghost" disabled={busy} onClick={onClose}>Later</button>
          <button type="submit" className="tel-btn tel-btn--primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save outcome'}
          </button>
        </div>
      </form>
    </div>
  )
}
