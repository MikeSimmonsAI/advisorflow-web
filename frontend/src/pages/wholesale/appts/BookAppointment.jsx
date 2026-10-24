/* BOOK A FIELD APPOINTMENT - one drawer used from the property page, the
 * Callback Center and the Appointments page. The person picks the day, the
 * time (Central), how long, and which rep goes. Server: POST /wholesale/appointments. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Drawer } from '../ds/ds'
import { centralToIso, LENGTHS, tomorrowCentral } from './apptTime'
import './appts.css'

export default function BookAppointment({ open, onClose, onBooked, link = {}, address = '', title = '', what = '' }) {
  const [reps, setReps] = useState(null)
  const [day, setDay] = useState(tomorrowCentral())
  const [time, setTime] = useState('10:00')
  const [minutes, setMinutes] = useState(60)
  const [rep, setRep] = useState('auto')
  const [addr, setAddr] = useState(address || '')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!open) return
    setAddr(address || ''); setErr(null)
    api.get('/wholesale/appointments/reps').then((r) => setReps(r.items || [])).catch(() => setReps([]))
  }, [open, address])

  async function save() {
    setBusy(true); setErr(null)
    try {
      const body = {
        ...link, starts_at: centralToIso(day, time), duration_minutes: Number(minutes),
        address: addr.trim() || null, title: title || null, notes: notes.trim() || null,
      }
      // 'auto': no assigned_to_id, so the server picks by the workspace rules
      // (the deal's owner, then the default person, then the least-busy rep).
      if (rep !== 'auto') body.assigned_to_id = rep || null
      const a = await api.post('/wholesale/appointments', body)
      setRep('auto')
      setNotes('')
      if (onBooked) onBooked(a)
      if (onClose) onClose()
    } catch (e) {
      setErr(e.message || 'That did not save.')
    } finally { setBusy(false) }
  }

  const ready = day && time && (addr.trim() || link.evosense_property_id || link.property_id)
  return (
    <Drawer open={open} onClose={onClose} title="Book an appointment" sub={what || addr || 'Field visit'}
            footer={<>
              <button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !ready} onClick={save}>
                {busy ? 'Booking…' : 'Book it'}</button>
            </>}>
      <div className="ap-form">
        <label className="ap-lab">Day
          <input type="date" className="ap-input" value={day} onChange={(e) => setDay(e.target.value)} /></label>
        <label className="ap-lab">Time (Central)
          <input type="time" className="ap-input" step="900" value={time} onChange={(e) => setTime(e.target.value)} /></label>
        <label className="ap-lab">How long
          <select className="ap-input" value={minutes} onChange={(e) => setMinutes(e.target.value)}>
            {LENGTHS.map(([m, l]) => <option key={m} value={m}>{l}</option>)}
          </select></label>
        <label className="ap-lab">Who goes
          <select className="ap-input" value={rep} onChange={(e) => setRep(e.target.value)}>
            <option value="auto">Assign automatically</option>
            <option value="">Leave unassigned</option>
            {(reps || []).map((r) => <option key={r.user_id} value={r.user_id}>
              {r.name}{r.role_label ? ` - ${r.role_label}` : ''}</option>)}
          </select></label>
        <label className="ap-lab ap-lab--full">Address
          <input className="ap-input" value={addr} onChange={(e) => setAddr(e.target.value)}
                 placeholder="Street, city, TX zip" autoComplete="off" /></label>
        <label className="ap-lab ap-lab--full">Notes for the rep
          <textarea className="ap-input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                    placeholder="Gate code, who will be there, what they want for it…" /></label>
      </div>
      {reps && !reps.length ? <p className="evo-muted evo-small">Nobody to assign yet - add people on Wholesale &gt; Team. You can still book it and assign later.</p> : null}
      <Alert>{err}</Alert>
    </Drawer>
  )
}
