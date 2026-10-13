/* Appointments: booked visits for the program's families (GET /program/appointments),
   grouped by day, filtered by the location chosen at the top. Click one to open
   the family's record. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, FilterChips, Loading, PageHead, Panel, errText, num } from './ui'

const WHEN = [['upcoming', 'Upcoming'], ['past', 'Past'], ['all', 'All']]
const STATUS = { booked: ['Booked', 'ok'], confirmed: ['Confirmed', 'ok'], cancelled: ['Cancelled', 'warn'], completed: ['Completed', ''] }

const dayKey = iso => new Date(iso).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
const timeOf = iso => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

export default function Appointments({ locationId, openPerson }) {
  const [when, setWhen] = useState('upcoming')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    setData(null); setErr('')
    const p = new URLSearchParams({ when })
    if (locationId) p.set('location_id', locationId)
    api.get(`/program/appointments?${p}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(errText(e)); setData({ items: [] }) } })
    return () => { alive = false }
  }, [when, locationId])

  const groups = []
  for (const a of data?.items || []) {
    const k = dayKey(a.when)
    const g = groups.find(x => x.k === k)
    if (g) g.items.push(a); else groups.push({ k, items: [a] })
  }

  return (
    <>
      <PageHead title="Appointments" sub="Visits families booked. Times show in your own time zone." />
      <ErrorLine text={err} />
      <Panel title="Booked visits" labelledBy="sci-appts"
        aside={<span className="sci-micro sci-muted">{data ? `${num(data.items.length)} shown` : 'Loading…'}</span>}>
        <div style={{ marginBottom: 12 }}><FilterChips items={WHEN} value={when} onChange={setWhen} label="Which appointments" /></div>
        {!data ? <Loading /> : !groups.length ? (
          <Empty icon="calendar" title={when === 'upcoming' ? 'No upcoming visits' : 'No visits here'}>
            When a family books from their link, the visit shows up here.
          </Empty>
        ) : groups.map(g => (
          <div key={g.k} style={{ marginBottom: 16 }}>
            <h3 className="sci-h3" style={{ margin: '4px 0 8px' }}>{g.k}</h3>
            <div className="sci-tablewrap">
              <table className="sci-table compact">
                <tbody>
                  {g.items.map(a => (
                    <tr key={a.id} style={{ cursor: 'pointer' }} onClick={() => openPerson(a.lead_id)}>
                      <td style={{ width: 90, whiteSpace: 'nowrap' }}><b>{timeOf(a.when)}</b></td>
                      <td className="sci-cell-main">
                        <button type="button" className="sci-namebtn" onClick={e => { e.stopPropagation(); openPerson(a.lead_id) }}>{a.name}</button>
                        <small>{[a.phone, a.email].filter(Boolean).join(' · ')}</small>
                      </td>
                      <td className="sci-small">{a.location || '—'}<span className="sci-micro sci-muted" style={{ display: 'block' }}>{a.label || 'Visit'}{a.minutes ? ` · ${a.minutes} min` : ''}</span></td>
                      <td style={{ textAlign: 'right' }}><Chip tone={(STATUS[a.status] || [])[1] || ''} plain>{(STATUS[a.status] || [a.status])[0]}</Chip></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Panel>
    </>
  )
}
