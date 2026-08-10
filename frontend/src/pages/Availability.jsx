import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Availability.css'

const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

const SLOT_TIMES = [
  '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
  '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
  '15:00', '15:30', '16:00', '16:30', '17:00',
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

export default function Availability() {
  const user = getCurrentUser()
  const isSuperAdmin = user?.role === 'super_admin'
  const navigate = useNavigate()

  // ── Team picker ──────────────────────────────────────────────────────────
  const [team, setTeam] = useState([])
  const [selectedAdvisor, setSelectedAdvisor] = useState(null) // {id, full_name}

  // ── Data ─────────────────────────────────────────────────────────────────
  const [blocks, setBlocks] = useState([])
  const [upcoming, setUpcoming] = useState([])
  const [openSlots, setOpenSlots] = useState(null)
  const [loading, setLoading] = useState(true)

  // ── UI state ─────────────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('vacation')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  // Vacation form
  const [vacStart, setVacStart] = useState('')
  const [vacEnd, setVacEnd] = useState('')
  const [vacReason, setVacReason] = useState('')
  const [vacCancel, setVacCancel] = useState(false)

  // Slot form
  const [slotDate, setSlotDate] = useState('')
  const [slotTime, setSlotTime] = useState('09:00')
  const [slotReason, setSlotReason] = useState('')

  // Recurring form
  const [recurDay, setRecurDay] = useState('')
  const [recurAfter, setRecurAfter] = useState('')
  const [recurBefore, setRecurBefore] = useState('')
  const [recurReason, setRecurReason] = useState('')

  // ── Load team list on mount ──────────────────────────────────────────────
  useEffect(() => {
    api.get('/admin/users').then(users => {
      const advisors = (users || []).filter(u => u.is_active !== false)
      setTeam(advisors)
      // Default to current user if they're in the list, else first advisor
      const me = advisors.find(u => u.id === user?.id)
      setSelectedAdvisor(me || advisors[0] || null)
    }).catch(() => {
      // Fallback: treat current user as the only advisor
      setSelectedAdvisor({ id: user?.id, full_name: user?.full_name || 'You' })
    })
  }, [])

  // ── Helper: advisor_id query param ───────────────────────────────────────
  const aqp = (extra = '') => {
    if (!selectedAdvisor || selectedAdvisor.id === user?.id) return extra
    const sep = extra.includes('?') ? '&' : '?'
    return `${extra}${sep}advisor_id=${selectedAdvisor.id}`
  }

  // ── Load availability data whenever selected advisor changes ────────────
  function load() {
    if (!selectedAdvisor) return
    setLoading(true)
    const adv = selectedAdvisor
    const advisorParam = adv.id !== user?.id ? `?advisor_id=${adv.id}` : ''
    Promise.all([
      api.get(`/availability/blocks${advisorParam}`).catch(() => []),
      api.get(`/availability/upcoming${advisorParam}`).catch(() => []),
      api.get(`/availability/slots/${adv.id}`).catch(() => null),
    ]).then(([blocksData, upcomingData, slotsData]) => {
      setBlocks(blocksData || [])
      setUpcoming(upcomingData || [])
      setOpenSlots(slotsData?.slots?.length ?? null)
      setLoading(false)
    })
  }

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
    const adv = selectedAdvisor
    const advisorParam = adv && adv.id !== user?.id ? `?advisor_id=${adv.id}` : ''
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
    const adv = selectedAdvisor
    const advisorParam = adv && adv.id !== user?.id ? `?advisor_id=${adv.id}` : ''
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
    const adv = selectedAdvisor
    const advisorParam = adv && adv.id !== user?.id ? `?advisor_id=${adv.id}` : ''
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

  return (
    <div className="availability-page">

      {/* HEADER */}
      <div className="availability-header">
        <div>
          <h1 className="availability-title">Availability</h1>
          <p className="availability-subtitle">
            Set working hours and block time for each advisor. Leads book only during open slots (9 AM–5 PM minus any blocks).
          </p>
        </div>
        {/* ADVISOR SELECTOR */}
        {team.length > 1 && (
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
            <span style={{ fontSize: 20 }}>&#128338;</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#2fb6ff' }}>
              {loading ? '--' : (openSlots ?? '--')}
            </strong>
            <span className="av-stat-label">Open slots next 14 days</span>
          </div>
        </div>
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(30,240,168,0.12)' }}>
            <span style={{ fontSize: 20 }}>&#128197;</span>
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
            <span style={{ fontSize: 20 }}>&#128336;</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#a78bfa', fontSize: 14 }}>
              9:00 AM – 5:00 PM
            </strong>
            <span className="av-stat-label">Working hours, 7 days</span>
          </div>
        </div>
        <div className="av-stat-card">
          <div className="av-stat-icon-wrap" style={{ background: 'rgba(248,113,113,0.12)' }}>
            <span style={{ fontSize: 20 }}>&#128683;</span>
          </div>
          <div className="av-stat-body">
            <strong className="av-stat-value" style={{ color: '#f87171' }}>
              {loading ? '--' : blocks.length}
            </strong>
            <span className="av-stat-label">Active time blocks</span>
          </div>
        </div>
      </div>

      {/* UPCOMING APPOINTMENTS */}
      <section className="panel av-upcoming-panel">
        <div className="panel-header">
          <h2 className="panel-title">
            &#128197; Upcoming appointments
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
            <span style={{ fontSize: 28, opacity: 0.4 }}>&#128197;</span>
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

      {/* BLOCK TIME + ACTIVE BLOCKS */}
      <div className="availability-grid">

        {/* BLOCK TIME */}
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
              { key: 'vacation',  label: 'Vacation / Days Off' },
              { key: 'slot',      label: 'Specific Slot' },
              { key: 'recurring', label: 'Recurring' },
            ].map(t => (
              <button key={t.key}
                className={`availability-tab ${activeTab === t.key ? 'availability-tab--active' : ''}`}
                onClick={() => setActiveTab(t.key)}
              >{t.label}</button>
            ))}
          </div>

          {error && <div className="compose-error" style={{ margin: '12px 0' }}>{error}</div>}
          {success && <div className="availability-success">{success}</div>}

          {activeTab === 'vacation' && (
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

          {activeTab === 'slot' && (
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

          {activeTab === 'recurring' && (
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

        {/* ACTIVE BLOCKS */}
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Active blocks</h2>
            <span className="panel-count">{blocks.length}</span>
          </div>
          {loading ? (
            <div className="empty-state">Loading...</div>
          ) : blocks.length === 0 ? (
            <div className="av-empty-upcoming" style={{ padding: '24px 0' }}>
              <span style={{ fontSize: 28, opacity: 0.4 }}>&#9989;</span>
              <span>No blocks set — fully open for bookings.</span>
            </div>
          ) : (
            <div className="availability-block-list">
              {blocks.map(b => (
                <div key={b.id} className={`availability-block-item availability-block-item--${b.block_type}`}>
                  <div className="availability-block-icon">
                    {b.block_type === 'date_range' ? '&#127958;&#65039;' : b.block_type === 'slot' ? '&#128336;' : '&#128260;'}
                  </div>
                  <div className="availability-block-info">
                    <span className="availability-block-type">{b.block_type.replace('_', ' ')}</span>
                    <span className="availability-block-detail">{fmtBlock(b)}</span>
                    {b.cancel_existing && <span className="availability-block-tag">Bookings cancelled</span>}
                  </div>
                  <button className="availability-block-delete" onClick={() => handleDeleteBlock(b.id)} title="Remove block">
                    &#10005;
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

      </div>
    </div>
  )
}
