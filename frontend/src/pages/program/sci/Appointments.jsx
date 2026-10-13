/* Appointments: booked visits for the program's families (GET /program/appointments),
   grouped by day, filtered by the location chosen at the top. Click one to open
   the family's record. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, FilterChips, Kpi, Loading, PageHead, Panel, errText, num, when as ago } from './ui'

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
      <BookingActivity openPerson={openPerson} />
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


/* BOOKING PAGE ACTIVITY: how many families opened their booking link, looked
   at times and booked - and WHEN (hour of day, weekday, Central time), so the
   morning-vs-afternoon question has an answer. GET /program/booking-activity. */
const EVENT = { opened: 'Opened booking page', day_viewed: 'Looked at times', booked: 'Booked' }
const hourLabel = h => `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? 'a' : 'p'}`

function Bars({ rows, labelKey, fmt }) {
  const max = Math.max(1, ...rows.map(r => r.opened))
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 120, padding: '8px 0 0' }}>
      {rows.map(r => (
        <div key={r[labelKey]} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}
          title={`${fmt(r[labelKey])}: ${r.opened} opened, ${r.booked} booked`}>
          <div style={{ width: '100%', height: 90, display: 'flex', alignItems: 'flex-end', gap: 2 }}>
            <div style={{ flex: 1, height: `${(r.opened / max) * 100}%`, minHeight: r.opened ? 3 : 0, background: '#2f6f73', borderRadius: '3px 3px 0 0' }} />
            <div style={{ flex: 1, height: `${(r.booked / max) * 100}%`, minHeight: r.booked ? 3 : 0, background: '#c9a24a', borderRadius: '3px 3px 0 0' }} />
          </div>
          <span className="sci-micro sci-muted" style={{ fontSize: 10.5 }}>{fmt(r[labelKey])}</span>
        </div>
      ))}
    </div>
  )
}

function BookingActivity({ openPerson }) {
  const [d, setD] = useState(null)
  const [days, setDays] = useState(30)
  useEffect(() => { setD(null); api.get(`/program/booking-activity?days=${days}`).then(setD).catch(() => setD({ error: true })) }, [days])
  if (!d) return <Panel title="Booking page activity"><Loading /></Panel>
  if (d.error) return null
  return (
    <Panel title="Booking page activity" labelledBy="sci-book-act"
      aside={<FilterChips items={[[7, '7 days'], [30, '30 days'], [90, '90 days']]} value={days} onChange={setDays} label="Period" />}>
      <section className="sci-kpis four" aria-label="Booking summary" style={{ marginBottom: 12 }}>
        <Kpi icon="users" tone="blue" value={d.opened_people} label="Families opened the page" note={`${num(d.open_events)} visits in all`} />
        <Kpi icon="clock" tone="slate" value={d.looked_at_times_people} label="Looked at times" />
        <Kpi icon="check" tone="green" value={d.booked_people} label="Booked" />
        <Kpi icon="bolt" tone="gold" value={d.booking_rate == null ? '—' : `${d.booking_rate}%`} label="Opened → booked" />
      </section>
      <div className="sci-grid-even">
        <div className="sci-card">
          <h3 className="sci-h3" style={{ marginBottom: 0 }}>By time of day <span className="sci-micro sci-muted">(Central)</span></h3>
          <Bars rows={d.by_hour} labelKey="hour" fmt={hourLabel} />
        </div>
        <div className="sci-card">
          <h3 className="sci-h3" style={{ marginBottom: 0 }}>By day of week</h3>
          <Bars rows={d.by_weekday} labelKey="day" fmt={x => x} />
        </div>
      </div>
      <p className="sci-micro sci-muted" style={{ margin: '8px 0 12px' }}>
        <span style={{ display: 'inline-block', width: 10, height: 10, background: '#2f6f73', borderRadius: 2, marginRight: 4 }} />Opened
        <span style={{ display: 'inline-block', width: 10, height: 10, background: '#c9a24a', borderRadius: 2, margin: '0 4px 0 12px' }} />Booked
      </p>
      {d.recent.length === 0 ? <Empty icon="calendar" title="No activity yet">When a family opens their booking link it shows up here.</Empty> : (
        <div className="sci-rows">
          {d.recent.slice(0, 15).map((r, i) => (
            <div className="sci-kv" key={i} style={{ cursor: r.lead_id ? 'pointer' : 'default' }} onClick={() => r.lead_id && openPerson && openPerson(r.lead_id)}>
              <span><b>{r.name}</b> · {EVENT[r.event] || r.event}{r.event === 'day_viewed' && r.detail ? ` (${r.detail})` : ''}</span>
              <span className="sci-muted">{ago(r.at)}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}
