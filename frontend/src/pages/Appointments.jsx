// APPOINTMENTS & VISITS — the Family Service Center's booking view (Oct 2026).
//
// Every row is a booking link from GET /pipeline/appointments (booked,
// confirmed and cancelled, plus `pending` links nobody has picked a time on
// yet), in the same scope as every other lead screen: an advisor sees their
// own families, a manager the workspace, test records never. "Outcomes
// needed" is the My Work count of past appointments with no outcome recorded.
// The stages are the states these records really carry; nothing is invented.
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import '../styles/sciWorkspace.css'

const DAY_MS = 86400000

function parseWhen(v) {
  if (!v) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + 'Z')
  return Number.isNaN(d.getTime()) ? null : d
}
function startOfWeek(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const dow = (x.getDay() + 6) % 7          // Monday first
  return new Date(x.getTime() - dow * DAY_MS)
}
const fmtDay = d => d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
const fmtTime = d => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const fmtFull = d => d ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'

const STAGE = {
  pending: { label: 'Link sent', tone: 'amber' },
  booked: { label: 'Booked', tone: 'blue' },
  confirmed: { label: 'Confirmed', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: '' },
}

function stageOf(a) {
  if (a.status === 'pending') return a.expired ? { label: 'Link expired', tone: 'red' } : STAGE.pending
  if (a.status === 'cancelled') return STAGE.cancelled
  if (a.status === 'confirmed' || a.confirmed_at) return STAGE.confirmed
  return STAGE.booked
}

export default function Appointments() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [outcomes, setOutcomes] = useState(null)
  const [error, setError] = useState('')
  const [view, setView] = useState('week')
  const [week, setWeek] = useState(() => startOfWeek(new Date()))
  const [stage, setStage] = useState('all')

  function load() {
    setError(''); setData(null)
    api.get('/pipeline/appointments', { params: { days: 30, include_pending: true } })
      .then(setData).catch(e => setError(e.message || 'Could not load appointments.'))
    api.get('/workqueue/today')
      .then(d => setOutcomes(typeof d?.totals?.outcomes_needed === 'number' ? d.totals.outcomes_needed : (d?.outcomes_needed || []).length))
      .catch(() => setOutcomes(null))
  }
  useEffect(() => { load() }, [])

  const booked = useMemo(() => (data?.items || []).map(a => ({ ...a, when: parseWhen(a.booked_time) })), [data])
  const pending = data?.pending || []
  const upcoming = booked.filter(a => a.upcoming && a.status !== 'cancelled')
  const awaiting = pending.filter(p => !p.expired)
  const confirmedUp = upcoming.filter(a => stageOf(a) === STAGE.confirmed)

  const days = Array.from({ length: 7 }, (_, i) => new Date(week.getTime() + i * DAY_MS))
  const inDay = d => booked.filter(a => a.when && a.when >= d && a.when < new Date(d.getTime() + DAY_MS))
    .sort((x, y) => x.when - y.when)

  const listRows = [
    ...pending.map(p => ({ ...p, when: null, sortAt: parseWhen(p.link_sent_at) })),
    ...booked.map(a => ({ ...a, sortAt: a.when })),
  ].filter(r => {
    const s = stageOf(r)
    if (stage === 'all') return true
    if (stage === 'upcoming') return r.upcoming && r.status !== 'cancelled'
    if (stage === 'pending') return r.status === 'pending'
    if (stage === 'past') return r.when && !r.upcoming && r.status !== 'cancelled'
    return s.label.toLowerCase() === stage
  }).sort((a, b) => (b.sortAt || 0) - (a.sortAt || 0))

  const open = r => navigate(`/leads/${r.lead_id}?tab=overview`)
  const kpis = [
    { label: 'Upcoming appointments', value: data ? data.totals?.upcoming ?? upcoming.length : null, sub: 'Booked or confirmed', go: () => { setView('list'); setStage('upcoming') } },
    { label: 'Awaiting a time', value: data ? awaiting.length : null, sub: 'Link sent, no time picked (30 days)', go: () => { setView('list'); setStage('pending') } },
    { label: 'Confirmed', value: data ? confirmedUp.length : null, sub: 'Upcoming and confirmed', go: () => { setView('list'); setStage('confirmed') } },
    { label: 'Outcomes needed', value: outcomes, sub: 'Past visits with no outcome', go: () => navigate('/workqueue') },
  ]

  return (
    <div className="sci-ws appt-page">
      <header className="appt-head">
        <div>
          <p className="appt-eyebrow">Family Service Center</p>
          <h1 className="page-title" style={{ margin: 0 }}>Appointments &amp; visits</h1>
          <p className="page-subtitle">Bookings, confirmations and visits for the families in your scope.</p>
        </div>
        <div className="wq-tabs" role="tablist" aria-label="Appointment view">
          <button type="button" role="tab" aria-selected={view === 'week'} className={view === 'week' ? 'on' : ''} onClick={() => setView('week')}>Week</button>
          <button type="button" role="tab" aria-selected={view === 'list'} className={view === 'list' ? 'on' : ''} onClick={() => setView('list')}>List</button>
        </div>
      </header>

      <div className="appt-kpis">
        {kpis.map(k => (
          <button type="button" key={k.label} className="panel appt-kpi" onClick={k.go}>
            <span className="appt-kpi-label">{k.label}</span>
            <strong className="appt-kpi-value">{k.value == null ? '—' : k.value}</strong>
            <span className="appt-kpi-sub">{k.sub}</span>
          </button>
        ))}
      </div>

      {error && <div className="act-error" role="alert">{error} <button type="button" className="btn btn--secondary" onClick={load}>Retry</button></div>}
      {!data && !error && <div className="empty-state" role="status">Loading appointments…</div>}

      {data && view === 'week' && (
        <section className="panel appt-week" aria-label="Week view">
          <div className="appt-week-head">
            <h2 className="panel-title">{days[0].toLocaleDateString([], { month: 'long', year: 'numeric' })} · week of {fmtDay(days[0])}</h2>
            <div className="appt-week-nav">
              <button type="button" className="btn btn--secondary" onClick={() => setWeek(new Date(week.getTime() - 7 * DAY_MS))}>‹ Previous</button>
              <button type="button" className="btn btn--secondary" onClick={() => setWeek(startOfWeek(new Date()))}>This week</button>
              <button type="button" className="btn btn--secondary" onClick={() => setWeek(new Date(week.getTime() + 7 * DAY_MS))}>Next ›</button>
            </div>
          </div>
          <div className="appt-days">
            {days.map(d => {
              const list = inDay(d)
              const today = new Date().toDateString() === d.toDateString()
              return (
                <div key={d.toISOString()} className={`appt-day${today ? ' appt-day--today' : ''}`}>
                  <div className="appt-day-label">{fmtDay(d)}</div>
                  {list.length === 0 && <div className="appt-none">—</div>}
                  {list.map(a => {
                    const s = stageOf(a)
                    return (
                      <button type="button" key={a.id} className={`appt-slot appt-slot--${s.tone || 'neutral'}`} onClick={() => open(a)}>
                        <strong>{fmtTime(a.when)}</strong>
                        <span>{a.lead_name || 'Unnamed contact'}</span>
                        <span className="appt-slot-sub">{a.appointment_type || 'Appointment'} · {s.label}</span>
                      </button>
                    )
                  })}
                </div>
              )
            })}
          </div>
          {data.truncated && <p className="act-muted">Busy period: only the nearest 300 on each side are shown.</p>}
        </section>
      )}

      {data && view === 'list' && (
        <section className="panel appt-list" aria-label="Appointment list">
          <div className="appt-week-head">
            <div className="wq-tabs" role="group" aria-label="Stage">
              {[['all', 'All'], ['pending', 'Link sent'], ['upcoming', 'Upcoming'], ['confirmed', 'Confirmed'], ['past', 'Past'], ['cancelled', 'Cancelled']].map(([k, l]) => (
                <button key={k} type="button" className={stage === k ? 'on' : ''} aria-pressed={stage === k} onClick={() => setStage(k)}>{l}</button>
              ))}
            </div>
          </div>
          {listRows.length === 0 ? <div className="empty-state">Nothing in this stage.</div> : (
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table appt-table">
                <thead><tr><th scope="col">Family / contact</th><th scope="col">Stage</th><th scope="col">When</th><th scope="col">Type</th><th scope="col">Advisor</th><th scope="col">Action</th></tr></thead>
                <tbody>
                  {listRows.map(r => {
                    const s = stageOf(r)
                    return (
                      <tr key={`${r.status}-${r.id}`}>
                        <td><strong>{r.lead_name || 'Unnamed contact'}</strong></td>
                        <td><span className={`sci-chip ${s.tone ? `sci-chip--${s.tone}` : ''}`} style={{ marginLeft: 0 }}>{s.label}</span></td>
                        <td className="appt-nowrap">{r.status === 'pending' ? `Link sent ${fmtFull(parseWhen(r.link_sent_at))}` : fmtFull(r.when)}</td>
                        <td>{r.appointment_type || '—'}</td>
                        <td>{r.advisor_name || '—'}</td>
                        <td><button type="button" className="btn btn--secondary" onClick={() => open(r)}>Open</button></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
