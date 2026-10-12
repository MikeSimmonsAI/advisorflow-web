/* The field visits for one property, on the office's property page: each
 * appointment with its outcome, and Visit notes (interview, repairs, photos,
 * offers) one tap away. Field logins get their own card instead. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Panel } from '../ds/ds'
import { ApptCard } from './WholesaleAppointments'
import './appts.css'

export default function FieldVisitsPanel({ propertyId, refreshKey }) {
  const [items, setItems] = useState(null)
  const [reps, setReps] = useState([])
  useEffect(() => {
    api.get(`/wholesale/appointments?when=all&property=${encodeURIComponent(propertyId)}`)
      .then((r) => setItems(r.items || [])).catch(() => setItems([]))
  }, [propertyId, refreshKey])
  useEffect(() => { api.get('/wholesale/appointments/reps').then((r) => setReps(r.items || [])).catch(() => setReps([])) }, [])
  if (!items || !items.length) return null
  const replace = (a) => setItems((xs) => xs.map((x) => (x.id === a.id ? a : x)))
  return (
    <Panel title="Field visits" count={items.length} hint="Appointments at this property, with the rep's visit notes.">
      <ul className="ap-list">{items.map((a) => <ApptCard key={a.id} a={a} isField={false} reps={reps} onChanged={replace} />)}</ul>
    </Panel>
  )
}
