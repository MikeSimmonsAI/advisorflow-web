import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Availability.css'

const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Slot times from 06:00 to 21:00 in 15-min increments (generous range for settings)
const ALL_TIMES = []
for (let h = 6; h <= 21; h++) {
  for (let m = 0; m < 60; m += 15) {
    ALL_TIMES.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
  }
}
ALL_TIMES.push('21:00')

const SLOT_TIMES = [
  '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
  '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
  '15:00', '15:30', '16:00', '16:30', '17:00',
]

const TIMEZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Phoenix', 'America/Los_Angeles', 'America/Anchorage',
  'Pacific/Honolulu', 'America/Puerto_Rico',
]

function fmtTime(t) {
  if (!t) return ''
  const [h, m] = t.split(':').map(Number)
  const ampm = h >= 12 ? 'PM' : 'AM'
  const hr = h % 12 || 12
  return `${hr}:${m.toString().padStart(2, '0')} ${ampm}`
}

function fmtDateTime(iso) {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleString([], {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtBlock(b) {
  if (b.block_type === 'date_range') {
    return `${b.start_date} to ${b.end_date}${b.reason ? ` (${b.reason})` : ''}`
  }
  if (b.block_type === 'slot') {
    return `${b.block_date} at ${fmtTime(b.block_time)}${b.reason ? ` (${b.reason})` : ''}`
  }
  if (b.block_type === 'recurring') {
    const day = b.recur_day_of_week !== null ? DAY_NAMES[b.recur_day_of_week] : 'Every day'
    const after = b.recur_after_time ? ` after ${fmtTime(b.recur_after_time)}` : ''
    const before = b.recur_before_time ? ` before ${fmtTime(b.recur_before_time)}` : ''
    return `${day}${after}${before}${b.reason ? ` (${b.reason})` : ''}`
  }
  return 'Unknown block'
}

// ── Mini Calendar ─────────────────────────────────────────────────────────────
function MiniCalendar({ events, blocks, onDayClick }) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth())

  const firstDay = new Date(year, month, 1)
  // 0=Sun in JS, we want 0=Mon
  const startDow = (firstDay.getDay() + 6) % 7
  const daysInMonth = new Date(year, month + 1, 0).getDate()

  // Build a Set of date strings that have appointments: "YYYY-MM-DD"
  const apptDates = new Set(
    (events || []).map(e => e.booked_time.slice(0, 10))
  )
  // Build a Set of blocked dates (date_range and slot blocks)
  const blockedDates = new Set()
  ;(blocks || []).forEach(b => {
    if (b.block_type === 'date_range' && b.start_date && b.end_date) {
      const s = new Date(b.start_date)
      const e = new Date(b.end_date)
      for (let d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
        blockedDates.add(d.toISOString().slice(0, 10))
      }
    }
    if (b.block_type === 'slot' && b.block_date) {
      blockedDates.add(b.block_date)
    }
  })

  const prevMonth = () => {
    if (month === 0) { setMonth(11); setYear(y => y - 1) }
    else setMonth(m => m - 1)
  }
  const nextMonth = () => {
    if (month === 11) { setMonth(0); setYear(y => y + 1) }
    else setMonth(m => m + 1)
  }

  const cells = []
  for (let i = 0; i < startDow; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)

  const monthName = firstDay.toLocaleString('default', { month: 'long' })
  const todayStr = today.toISOString().slice(0, 10)

  return (
    <div className="mini-cal">
      <div className="mini-cal-nav">
        <button className="mini-cal-nav-btn" onClick={prevMonth}>‹</button>
        <span className="mini-cal-month">{monthName} {year}</span>
        <button className="mini-cal-nav-btn" onClick={nextMonth}>›</button>
      </div>
      <div className="mini-cal-grid">
        {DAY_SHORT.map(d => (
          <div key={d} className="mini-cal-dow">{d}</div>
        ))}
        {cells.map((day, i) => {
          if (!day) return <div key={`empty-${i}`} />
          const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
          const isToday = dateStr === todayStr
          const hasAppt = apptDates.has(dateStr)
          const isBlocked = blockedDates.has(dateStr)
          const isPast = dateStr < todayStr
          return (
            <button
              key={day}
              className={[
                'mini-cal-day',
                isToday ? 'mini-cal-day--today' : '',
                hasAppt ? 'mini-cal-day--appt' : '',
                isBlocked ? 'mini-cal-day--blocked' : '',
                isPast ? 'mini-cal-day--past' : '',
              ].filter(Boolean).join(' ')}
              onClick={() => !isPast && onDayClick && onDayClick(dateStr)}
              title={hasAppt ? 'Has appointment' : isBlocked ? 'Blocked' : 'Click to block this day'}
            >
              {day}
              {hasAppt && <span className="mini-cal-dot mini-cal-dot--appt" />}
              {isBlocked && <span className="mini-cal-dot mini-cal-dot--blocked" />}
            </button>
          )
        })}
      </div>
      <div className="mini-cal-legend">
        <span><span className="mini-cal-dot mini-cal-dot--appt" /> Appointment</span>
        <span><span className="mini-cal-dot mini-cal-dot--blocked" /> Blocked</span>
      </div>
    </div>
  )
}

export default function Availability() {
  const user = getCurrentUser()
  const isAdmin = user?.role === 'org_admin' || user?.role === 'super_admin' || user?.role === 'god_admin'
  const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'god_admin'
  const navigate = useNavigate()

  // ── Team picker (admin-only feature) ─────────────────────────────────────
  const [team, setTeam] = useState([])
  const [selectedAdvisor, setSelectedAdvisor] = useState(null)

  // ── Availability data ─────────────────────────────────────────────────────
  const [blocks, setBlocks] = useState([])
  const [upcoming, setUpcoming] = useState([])
  const [calEvents, setCalEvents] = useState([])
  const [openSlots, setOpenSlots] = useState(null)
  const [loading, setLoading] = useState(true)

  // ── Booking settings state ────────────────────────────────────────────────
  const [bsDuration, setBsDuration] = useState(30)
  const [bsBuffer, setBsBuffer] = useState(0)
  const [bsMaxPerDay, setBsMaxPerDay] = useState(8)
  const [bsStartTime, setBsStartTime] = useState('09:00')
  const [bsEndTime, setBsEndTime] = useState('17:00')
  const [bsDays, setBsDays] = useState([0, 1, 2, 3, 4]) // Mon-Fri
  const [bsTimezone, setBsTimezone] = useState('America/Chicago')
  const [bsConfirmMsg, setBsConfirmMsg] = useState('')
  const [bsSaving, setBsSaving] = useState(false)
  const [bsSuccess, setBsSuccess] = useState('')
  const [bsError, setBsError] = useState('')

  // ── UI state ─────────────────────────────────────────────────────────────
  const [mainTab, setMainTab] = useState('calendar') // 'calendar' | 'block' | 'settings'
  const [blockTab, setBlockTab] = useState('vacation')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  // Block forms
  const [vacStart, setVacStart] = useState('')
  const [vacEnd, setVacEnd] = useState('')
  const [vacReason, setVacReason] = useState('')
  const [vacCancel, setVacCancel] = useState(false)

  const [slotDate, setSlotDate] = useState('')
  const [slotTime, setSlotTime] = useState('09:00')
  const [slotReason, setSlotReason] = useState('')

  const [recurDay, setRecurDay] = useState('')
  const [recurAfter, setRecurAfter] = useState('')
  const [recurBefore, setRecurBefore] = useState('')
  const [recurReason, setRecurReason] = useState('')

  // ── Load team list (admins see all advisors) ──────────────────────────────
  useEffect(() => {
    if (isAdmin) {
      api.get('/admin/users').then(users => {
        const advisors = (users || []).filter(u => u.is_active !== false)
        setTeam(advisors)
        const me = advisors.find(u => u.id === user?.id)
        setSelectedAdvisor(me || advisors[0] || null)
      }).catch(() => {
        setSelectedAdvisor({ id: user?.id, full_name: user?.full_name || 'You' })
      })
    } else {
      setSelectedAdvisor({ id: user?.id, full_name: user?.full_name || 'You' })
    }
  }, [])

  // ── Load profile (booking settings) for the current user ─────────────────
  useEffect(() => {
    api.get('/settings/profile').then(p => {
      setBsDuration(p.appt_duration_minutes ?? 30)
      setBsBuffer(p.buffer_minutes ?? 0)
      setBsMaxPerDay(p.max_bookings_per_day ?? 8)
      setBsStartTime(p.available_start_time ?? '09:00')
      setBsEndTime(p.available_end_time ?? '17:00')
      const days = (p.available_days ?? '0,1,2,3,4')
        .split(',').filter(Boolean).map(Number)
      setBsDays(days)
      setBsTimezone(p.booking_timezone ?? 'America/Chicago')
      setBsConfirmMsg(p.booking_confirmation_message ?? '')
    }).catch(() => {})
  }, [])

  // ── Load availability data when selected advisor changes ─────────────────
  const load = useCallback(() => {
    if (!selectedAdvisor) return
    setLoading(true)
    const adv = selectedAdvisor
    const advisorParam = adv.id !== user?.id ? `?advisor_id=${adv.id}` : ''
    Promise.all([
      api.get(`/availability/blocks${advisorParam}`).catch(() => []),
      api.get(`/availability/upcoming${advisorParam}`).catch(() => []),
      api.get(`/availability/slots/${adv.id}`).catch(() => null),
      // Calendar events endpoint only for own schedule
      adv.id === user?.id ? api.get('/calendar/events?days_ahead=60').catch(() => []) : Promise.resolve([]),
    ]).then(([blocksData, upcomingData, slotsData, eventsData]) => {
      setBlocks(blocksData || [])
      setUpcoming(upcomingData || [])
      setOpenSlots(slotsData?.slots?.length ?? null)
      setCalEvents(eventsData || [])
      setLoading(false)
    })
  }, [selectedAdvisor?.id, user?.id])

  useEffect(() => { if (selectedAdvisor) load() }, [selectedAdvisor?.id])

  async function handleDeleteBlock(id) {
    if (!window.confirm('Remove this availability block?')) return
    try {
      await api.delete(`/availability/block/${id}`)
      setBlocks(blocks.filter(b => b.id !== id))
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSaveVacation() {
    if (!vacStart || !vacEnd) { setError('Start and end date required'); return }
    setSaving(true); setError(''); setSuccess('')
    const advisorParam = selectedAdvisor && selectedAdvisor.id !== user?.id ? `?advisor_id=${selectedAdvisor.id}` : ''
    try {
      await api.post(`/availability/block/date-range${advisorParam}`, {
        start_date: vacStart, end_date: vacEnd,
        reason: vacReason || null, cancel_existing: vacCancel,
      })
      setSuccess(vacCancel ? 'Vacation blocked and existing bookings cancelled.' : 'Vacation blocked.')
      setVacStart(''); setVacEnd(''); setVacReason(''); setVacCancel(false)
      load()
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  async function handleSaveSlot() {
    if (!slotDate || !slotTime) { setError('Date and time required'); return }
    setSaving(true); setError(''); setSuccess('')
    const advisorParam = selectedAdvisor && selectedAdvisor.id !== user?.id ? `?advisor_id=${selectedAdvisor.id}` : ''
    try {
      await api.post(`/availability/block/slot${advisorParam}`, {
        block_date: slotDate, block_time: slotTime, reason: slotReason || null,
      })
      setSuccess('Slot blocked.')
      setSlotDate(''); setSlotTime('09:00'); setSlotReason('')
      load()
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  async function handleSaveRecurring() {
    if (!recurAfter && !recurBefore) { setError('Set at least one time boundary'); return }
    setSaving(true); setError(''); setSuccess('')
    const advisorParam = selectedAdvisor && selectedAdvisor.id !== user?.id ? `?advisor_id=${selectedAdvisor.id}` : ''
    try {
      await api.post(`/availability/block/recurring${advisorParam}`, {
        recur_day_of_week: recurDay !== '' ? parseInt(recurDay) : null,
        recur_after_time: recurAfter || null,
        recur_before_time: recurBefore || null,
        reason: recurReason || null,
      })
      setSuccess('Recurring block saved.')
      setRecurDay(''); setRecurAfter(''); setRecurBefore(''); setRecurReason('')
      load()
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  async function handleSaveBookingSettings() {
    setBsSaving(true); setBsSuccess(''); setBsError('')
    try {
      await api.patch('/settings/booking-settings', {
        appt_duration_minutes: bsDuration,
        buffer_minutes: bsBuffer,
        max_bookings_per_day: bsMaxPerDay,
        available_start_time: bsStartTime,
        available_end_time: bsEndTime,
        available_days: bsDays.join(','),
        booking_timezone: bsTimezone,
        booking_confirmation_message: bsConfirmMsg || null,
      })
      setBsSuccess('Booking settings saved.')
      setTimeout(() => setBsSuccess(''), 3000)
    } catch (e) { setBsError(e.message) } finally { setBsSaving(false) }
  }

  function toggleDay(idx) {
    setBsDays(prev =>
      prev.includes(idx) ? prev.filter(d => d !== idx) : [...prev, idx].sort()
    )
  }

  // Calendar day click: pre-fill the block-slot form and switch tab
  function handleCalDayClick(dateStr) {
    setSlotDate(dateStr)
    setMainTab('block')
    setBlockTab('slot')
  }

  return (
    <div className="availability-page">

      {/* HEADER */}
      <div className="availability-header">
        <div>
          <h1 className="availability-title">Availability</h1>
          <p className="availability-subtitle">
            Manage your schedule, booking settings, and blocked time.
          </p>
        </div>
        {/* ADVISOR SELECTOR — admins only */}
        {isAdmin && team.length > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <label style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
              Viewing advisor:
            </label>
            <select
              className="filter-select"
              value={selectedAdvisor?.id || ''}
              onChange={e => {
                const adv = team.find(u => u.id === e.target.value)
                if (adv) setSelectedAdvisor(adv)
              }}
              style={{ minWidth: 200 }}
            >
              {isSuperAdmin ? (
                Object.entries(
                  team.reduce((acc, u) => {
                    const org = u.organization_name || 'Unknown Org'
                    if (!acc[org]) acc[org] = []
                    acc[org].push(u)
                    return acc
                  }, {})
                ).sort(([a], [b]) => a.localeCompare(b)).map(([orgName, orgUsers]) => (
                  <optgroup key={orgName} label={orgName}>
                    {orgUsers.map(u => (
                      <option key={u.id} value={u.id}>
                        {u.full_name}{u.id === user?.id ? ' (you)' : ''}
                      </option>
                    ))}
                  </optgroup>
                ))
              ) : (
                team.map(u => (
                  <option key={u.id} value={u.id}>
                    {u.full_name}{u.id === user?.id ? ' (you)' : ''}
                  </option>
                ))
              )}
            </select>
          </div>
        )}
      </div>

      {/* STATS ROW */}
      <div className="av-stats-row">
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(47,182,255,0.12)' }}>
            <span style={{ fontSize: 20 }}>🕐</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#2fb6ff' }}>
              {loading ? '--' : (openSlots ?? '--')}
            </strong>
            <span className="av-stat-label">Open slots · next 14 days</span>
          </div>
        </div>
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(30,240,168,0.12)' }}>
            <span style={{ fontSize: 20 }}>📅</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#1ef0a8' }}>
              {loading ? '--' : upcoming.length}
            </strong>
            <span className="av-stat-label">Upcoming appointments</span>
          </div>
        </div>
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(167,139,250,0.12)' }}>
            <span style={{ fontSize: 20 }}>⏱</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#a78bfa', fontSize: 14 }}>
              {fmtTime(bsStartTime)} – {fmtTime(bsEndTime)}
            </strong>
            <span className="av-stat-label">Working hours · {bsDays.length}d/wk</span>
          </div>
        </div>
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(248,113,113,0.12)' }}>
            <span style={{ fontSize: 20 }}>🚫</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#f87171' }}>
              {loading ? '--' : blocks.length}
            </strong>
            <span className="av-stat-label">Active time blocks</span>
          </div>
        </div>
      </div>

      {/* MAIN TABS */}
      <div className="av-main-tabs">
        {[
          { key: 'calendar', label: '📅 Calendar' },
          { key: 'block', label: '🚫 Block Time' },
          { key: 'settings', label: '⚙️ Booking Settings' },
        ].map(t => (
          <button
            key={t.key}
            className={`av-main-tab ${mainTab === t.key ? 'av-main-tab--active' : ''}`}
            onClick={() => setMainTab(t.key)}
          >{t.label}</button>
        ))}
      </div>

      {/* ── CALENDAR TAB ─────────────────────────────────────────────────── */}
      {mainTab === 'calendar' && (
        <div className="av-calendar-tab">
          <div className="av-cal-grid">
            {/* Mini calendar */}
            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">Schedule</h2>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Click a day to block it</span>
              </div>
              <MiniCalendar
                events={calEvents}
                blocks={blocks}
                onDayClick={handleCalDayClick}
              />
            </section>

            {/* Upcoming appointments */}
            <section className="panel av-upcoming-panel">
              <div className="panel-header">
                <h2 className="panel-title">
                  Upcoming appointments
                  {selectedAdvisor && selectedAdvisor.id !== user?.id && (
                    <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 8 }}>
                      — {selectedAdvisor.full_name}
                    </span>
                  )}
                </h2>
                <span className="panel-count">{upcoming.length}</span>
              </div>
              {loading ? (
                <div className="empty-state">Loading...</div>
              ) : upcoming.length === 0 ? (
                <div className="av-empty-upcoming">
                  <span style={{ fontSize: 28, opacity: 0.4 }}>📅</span>
                  <span>No upcoming appointments booked yet.</span>
                </div>
              ) : (
                <div className="av-upcoming-list">
                  {upcoming.map(appt => (
                    <div key={appt.id} className="av-upcoming-row">
                      <div className="av-upcoming-time-block">
                        <span className="av-upcoming-datetime">{fmtDateTime(appt.booked_time)}</span>
                      </div>
                      <div className="av-upcoming-lead-info">
                        <span className="av-upcoming-name">{appt.lead_name}</span>
                        {appt.lead_phone && <span className="av-upcoming-phone">{appt.lead_phone}</span>}
                      </div>
                      {appt.lead_id && (
                        <button className="btn btn--secondary" style={{ fontSize: 12, padding: '4px 12px', flexShrink: 0 }}
                          onClick={() => navigate(`/leads/${appt.lead_id}`)}>
                          View lead
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          {/* Active blocks (below calendar grid) */}
          <section className="panel" style={{ marginTop: 16 }}>
            <div className="panel-header">
              <h2 className="panel-title">Active blocks</h2>
              <span className="panel-count">{blocks.length}</span>
            </div>
            {loading ? (
              <div className="empty-state">Loading...</div>
            ) : blocks.length === 0 ? (
              <div className="av-empty-upcoming" style={{ padding: '24px 0' }}>
                <span style={{ fontSize: 28, opacity: 0.4 }}>✅</span>
                <span>No blocks set — fully open for bookings.</span>
              </div>
            ) : (
              <div className="availability-block-list">
                {blocks.map(b => (
                  <div key={b.id} className={`availability-block-item availability-block-item--${b.block_type}`}>
                    <div className="availability-block-icon">
                      {b.block_type === 'date_range' ? '🏖️' : b.block_type === 'slot' ? '🕐' : '🔄'}
                    </div>
                    <div className="availability-block-info">
                      <span className="availability-block-type">{b.block_type.replace('_', ' ')}</span>
                      <span className="availability-block-detail">{fmtBlock(b)}</span>
                      {b.cancel_existing && <span className="availability-block-tag">Bookings cancelled</span>}
                    </div>
                    <button className="availability-block-delete" onClick={() => handleDeleteBlock(b.id)} title="Remove block">
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* ── BLOCK TIME TAB ───────────────────────────────────────────────── */}
      {mainTab === 'block' && (
        <div className="availability-grid">
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Block time
                {selectedAdvisor && selectedAdvisor.id !== user?.id && (
                  <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 8 }}>
                    for {selectedAdvisor.full_name}
                  </span>
                )}
              </h2>
            </div>
            <div className="availability-tabs">
              {[
                { key: 'vacation', label: 'Vacation / Days Off' },
                { key: 'slot', label: 'Specific Slot' },
                { key: 'recurring', label: 'Recurring' },
              ].map(t => (
                <button key={t.key}
                  className={`availability-tab ${blockTab === t.key ? 'availability-tab--active' : ''}`}
                  onClick={() => setBlockTab(t.key)}
                >{t.label}</button>
              ))}
            </div>

            {error && <div className="compose-error" style={{ margin: '12px 0' }}>{error}</div>}
            {success && <div className="availability-success">{success}</div>}

            {blockTab === 'vacation' && (
              <div className="availability-form">
                <div className="availability-field-row">
                  <div className="availability-field">
                    <label>START DATE</label>
                    <input type="date" value={vacStart} onChange={e => setVacStart(e.target.value)} className="compose-subject" />
                  </div>
                  <div className="availability-field">
                    <label>END DATE</label>
                    <input type="date" value={vacEnd} onChange={e => setVacEnd(e.target.value)} className="compose-subject" />
                  </div>
                </div>
                <div className="availability-field">
                  <label>REASON (OPTIONAL)</label>
                  <input type="text" value={vacReason} onChange={e => setVacReason(e.target.value)}
                    placeholder="e.g. Training week" className="compose-subject" />
                </div>
                <label className="compose-checkbox" style={{ marginBottom: 8 }}>
                  <input type="checkbox" checked={vacCancel} onChange={e => setVacCancel(e.target.checked)} />
                  Cancel existing bookings in this range and notify leads via SMS
                </label>
                <button className="btn btn--primary" onClick={handleSaveVacation} disabled={saving}>
                  {saving ? 'Saving...' : 'Block dates'}
                </button>
              </div>
            )}

            {blockTab === 'slot' && (
              <div className="availability-form">
                <div className="availability-field-row">
                  <div className="availability-field">
                    <label>DATE</label>
                    <input type="date" value={slotDate} onChange={e => setSlotDate(e.target.value)} className="compose-subject" />
                  </div>
                  <div className="availability-field">
                    <label>TIME</label>
                    <select value={slotTime} onChange={e => setSlotTime(e.target.value)} className="filter-select">
                      {SLOT_TIMES.map(t => <option key={t} value={t}>{fmtTime(t)}</option>)}
                    </select>
                  </div>
                </div>
                <div className="availability-field">
                  <label>REASON (OPTIONAL)</label>
                  <input type="text" value={slotReason} onChange={e => setSlotReason(e.target.value)}
                    placeholder="e.g. Team meeting" className="compose-subject" />
                </div>
                <button className="btn btn--primary" onClick={handleSaveSlot} disabled={saving}>
                  {saving ? 'Saving...' : 'Block slot'}
                </button>
              </div>
            )}

            {blockTab === 'recurring' && (
              <div className="availability-form">
                <div className="availability-field">
                  <label>DAY OF WEEK (LEAVE BLANK FOR EVERY DAY)</label>
                  <select value={recurDay} onChange={e => setRecurDay(e.target.value)} className="filter-select">
                    <option value="">Every day</option>
                    {DAY_NAMES.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                </div>
                <div className="availability-field-row">
                  <div className="availability-field">
                    <label>BLOCK SLOTS AFTER</label>
                    <select value={recurAfter} onChange={e => setRecurAfter(e.target.value)} className="filter-select">
                      <option value="">No limit</option>
                      {SLOT_TIMES.map(t => <option key={t} value={t}>{fmtTime(t)}</option>)}
                    </select>
                  </div>
                  <div className="availability-field">
                    <label>BLOCK SLOTS BEFORE</label>
                    <select value={recurBefore} onChange={e => setRecurBefore(e.target.value)} className="filter-select">
                      <option value="">No limit</option>
                      {SLOT_TIMES.map(t => <option key={t} value={t}>{fmtTime(t)}</option>)}
                    </select>
                  </div>
                </div>
                <div className="availability-field">
                  <label>REASON (OPTIONAL)</label>
                  <input type="text" value={recurReason} onChange={e => setRecurReason(e.target.value)}
                    placeholder="e.g. No late afternoon slots" className="compose-subject" />
                </div>
                <button className="btn btn--primary" onClick={handleSaveRecurring} disabled={saving}>
                  {saving ? 'Saving...' : 'Save recurring block'}
                </button>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Active blocks</h2>
              <span className="panel-count">{blocks.length}</span>
            </div>
            {loading ? (
              <div className="empty-state">Loading...</div>
            ) : blocks.length === 0 ? (
              <div className="av-empty-upcoming" style={{ padding: '24px 0' }}>
                <span style={{ fontSize: 28, opacity: 0.4 }}>✅</span>
                <span>No blocks set — fully open for bookings.</span>
              </div>
            ) : (
              <div className="availability-block-list">
                {blocks.map(b => (
                  <div key={b.id} className={`availability-block-item availability-block-item--${b.block_type}`}>
                    <div className="availability-block-icon">
                      {b.block_type === 'date_range' ? '🏖️' : b.block_type === 'slot' ? '🕐' : '🔄'}
                    </div>
                    <div className="availability-block-info">
                      <span className="availability-block-type">{b.block_type.replace('_', ' ')}</span>
                      <span className="availability-block-detail">{fmtBlock(b)}</span>
                      {b.cancel_existing && <span className="availability-block-tag">Bookings cancelled</span>}
                    </div>
                    <button className="availability-block-delete" onClick={() => handleDeleteBlock(b.id)} title="Remove block">
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* ── BOOKING SETTINGS TAB ─────────────────────────────────────────── */}
      {mainTab === 'settings' && (
        <section className="panel av-booking-settings">
          <div className="panel-header">
            <h2 className="panel-title">Booking Settings</h2>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>
              Controls how leads see and book your calendar.
            </p>
          </div>

          {bsError && <div className="compose-error" style={{ marginBottom: 16 }}>{bsError}</div>}
          {bsSuccess && <div className="availability-success">{bsSuccess}</div>}

          <div className="bs-grid">
            {/* Working days */}
            <div className="bs-section">
              <label className="bs-label">WORKING DAYS</label>
              <div className="bs-days-row">
                {DAY_SHORT.map((d, i) => (
                  <button
                    key={i}
                    className={`bs-day-btn ${bsDays.includes(i) ? 'bs-day-btn--active' : ''}`}
                    onClick={() => toggleDay(i)}
                  >{d}</button>
                ))}
              </div>
            </div>

            {/* Working hours */}
            <div className="bs-section">
              <label className="bs-label">WORKING HOURS</label>
              <div className="availability-field-row">
                <div className="availability-field">
                  <label style={{ fontSize: 11 }}>FROM</label>
                  <select value={bsStartTime} onChange={e => setBsStartTime(e.target.value)} className="filter-select">
                    {ALL_TIMES.map(t => <option key={t} value={t}>{fmtTime(t)}</option>)}
                  </select>
                </div>
                <div className="availability-field">
                  <label style={{ fontSize: 11 }}>TO</label>
                  <select value={bsEndTime} onChange={e => setBsEndTime(e.target.value)} className="filter-select">
                    {ALL_TIMES.map(t => <option key={t} value={t}>{fmtTime(t)}</option>)}
                  </select>
                </div>
              </div>
            </div>

            {/* Duration & buffer */}
            <div className="bs-section">
              <label className="bs-label">APPOINTMENT DURATION</label>
              <select value={bsDuration} onChange={e => setBsDuration(Number(e.target.value))} className="filter-select">
                {[15, 30, 45, 60, 90, 120].map(v => (
                  <option key={v} value={v}>{v} min</option>
                ))}
              </select>
            </div>

            <div className="bs-section">
              <label className="bs-label">BUFFER BETWEEN MEETINGS</label>
              <select value={bsBuffer} onChange={e => setBsBuffer(Number(e.target.value))} className="filter-select">
                {[0, 5, 10, 15, 20, 30, 45, 60].map(v => (
                  <option key={v} value={v}>{v === 0 ? 'None' : `${v} min`}</option>
                ))}
              </select>
            </div>

            {/* Max per day & timezone */}
            <div className="bs-section">
              <label className="bs-label">MAX BOOKINGS PER DAY</label>
              <select value={bsMaxPerDay} onChange={e => setBsMaxPerDay(Number(e.target.value))} className="filter-select">
                {[1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20].map(v => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </div>

            <div className="bs-section">
              <label className="bs-label">TIMEZONE</label>
              <select value={bsTimezone} onChange={e => setBsTimezone(e.target.value)} className="filter-select">
                {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
              </select>
            </div>
          </div>

          {/* Confirmation message — full width */}
          <div className="bs-section" style={{ marginTop: 8 }}>
            <label className="bs-label">CONFIRMATION MESSAGE <span style={{ fontWeight: 400, textTransform: 'none', fontSize: 11 }}>(shown to lead after booking)</span></label>
            <textarea
              className="compose-subject"
              rows={3}
              value={bsConfirmMsg}
              onChange={e => setBsConfirmMsg(e.target.value)}
              placeholder="e.g. Thank you! I look forward to speaking with you. Call my cell at (555) 000-0000 if you need to reschedule."
              style={{ resize: 'vertical', fontFamily: 'inherit' }}
            />
          </div>

          <div style={{ marginTop: 20 }}>
            <button className="btn btn--primary" onClick={handleSaveBookingSettings} disabled={bsSaving}>
              {bsSaving ? 'Saving...' : 'Save booking settings'}
            </button>
          </div>
        </section>
      )}

    </div>
  )
}
