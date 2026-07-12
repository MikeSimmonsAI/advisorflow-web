import { useEffect, useState } from 'react'
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

function fmtBlock(b) {
  if (b.block_type === 'date_range') {
    return `${b.start_date} → ${b.end_date}${b.reason ? ` (${b.reason})` : ''}`
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
  const [blocks, setBlocks] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('vacation') // vacation | slot | recurring
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

  function load() {
    setLoading(true)
    api.get('/availability/blocks')
      .then(setBlocks)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleDeleteBlock(id) {
    if (!confirm('Remove this availability block?')) return
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
    try {
      await api.post('/availability/block/date-range', {
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
    try {
      await api.post('/availability/block/slot', {
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
    try {
      await api.post('/availability/block/recurring', {
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
      <div className="availability-header">
        <h1 className="availability-title">📅 Availability</h1>
        <p className="availability-subtitle">
          Block dates, times, or recurring slots. Leads won't be able to book during blocked times.
        </p>
      </div>

      <div className="availability-grid">
        <div className="availability-left">
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Block time</h2>
            </div>
            <div className="availability-tabs">
              {[
                { key: 'vacation', label: '🏖️ Vacation / Days Off' },
                { key: 'slot', label: '🕐 Specific Slot' },
                { key: 'recurring', label: '🔁 Recurring' },
              ].map(t => (
                <button
                  key={t.key}
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
                    <label>Start date</label>
                    <input type="date" value={vacStart} onChange={e => setVacStart(e.target.value)} className="compose-subject" />
                  </div>
                  <div className="availability-field">
                    <label>End date</label>
                    <input type="date" value={vacEnd} onChange={e => setVacEnd(e.target.value)} className="compose-subject" />
                  </div>
                </div>
                <div className="availability-field">
                  <label>Reason (optional)</label>
                  <input type="text" value={vacReason} onChange={e => setVacReason(e.target.value)}
                    placeholder="e.g. Family vacation" className="compose-subject" />
                </div>
                <label className="compose-checkbox" style={{ marginBottom: 16 }}>
                  <input type="checkbox" checked={vacCancel} onChange={e => setVacCancel(e.target.checked)} />
                  Cancel existing bookings in this range and notify leads via SMS
                </label>
                <button className="btn btn--primary" onClick={handleSaveVacation} disabled={saving}>
                  {saving ? 'Saving…' : 'Block dates'}
                </button>
              </div>
            )}

            {activeTab === 'slot' && (
              <div className="availability-form">
                <div className="availability-field-row">
                  <div className="availability-field">
                    <label>Date</label>
                    <input type="date" value={slotDate} onChange={e => setSlotDate(e.target.value)} className="compose-subject" />
                  </div>
                  <div className="availability-field">
                    <label>Time</label>
                    <select value={slotTime} onChange={e => setSlotTime(e.target.value)} className="filter-select">
                      {SLOT_TIMES.map(t => (
                        <option key={t} value={t}>{fmtTime(t)}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="availability-field">
                  <label>Reason (optional)</label>
                  <input type="text" value={slotReason} onChange={e => setSlotReason(e.target.value)}
                    placeholder="e.g. Team meeting" className="compose-subject" />
                </div>
                <button className="btn btn--primary" onClick={handleSaveSlot} disabled={saving}>
                  {saving ? 'Saving…' : 'Block slot'}
                </button>
              </div>
            )}

            {activeTab === 'recurring' && (
              <div className="availability-form">
                <div className="availability-field">
                  <label>Day of week (leave blank for every day)</label>
                  <select value={recurDay} onChange={e => setRecurDay(e.target.value)} className="filter-select">
                    <option value="">Every day</option>
                    {DAY_NAMES.map((d, i) => (
                      <option key={i} value={i}>{d}</option>
                    ))}
                  </select>
                </div>
                <div className="availability-field-row">
                  <div className="availability-field">
                    <label>Block slots after</label>
                    <select value={recurAfter} onChange={e => setRecurAfter(e.target.value)} className="filter-select">
                      <option value="">No limit</option>
                      {SLOT_TIMES.map(t => (
                        <option key={t} value={t}>{fmtTime(t)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="availability-field">
                    <label>Block slots before</label>
                    <select value={recurBefore} onChange={e => setRecurBefore(e.target.value)} className="filter-select">
                      <option value="">No limit</option>
                      {SLOT_TIMES.map(t => (
                        <option key={t} value={t}>{fmtTime(t)}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="availability-field">
                  <label>Reason (optional)</label>
                  <input type="text" value={recurReason} onChange={e => setRecurReason(e.target.value)}
                    placeholder="e.g. No late afternoon slots" className="compose-subject" />
                </div>
                <button className="btn btn--primary" onClick={handleSaveRecurring} disabled={saving}>
                  {saving ? 'Saving…' : 'Save recurring block'}
                </button>
              </div>
            )}
          </section>
        </div>

        <div className="availability-right">
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Active blocks</h2>
              <span className="panel-count">{blocks.length}</span>
            </div>
            {loading ? (
              <div className="empty-state">Loading…</div>
            ) : blocks.length === 0 ? (
              <div className="empty-state">No blocks set. You're open for bookings.</div>
            ) : (
              <div className="availability-block-list">
                {blocks.map(b => (
                  <div key={b.id} className={`availability-block-item availability-block-item--${b.block_type}`}>
                    <div className="availability-block-icon">
                      {b.block_type === 'date_range' ? '🏖️' : b.block_type === 'slot' ? '🕐' : '🔁'}
                    </div>
                    <div className="availability-block-info">
                      <span className="availability-block-type">{b.block_type.replace('_', ' ')}</span>
                      <span className="availability-block-detail">{fmtBlock(b)}</span>
                      {b.cancel_existing && (
                        <span className="availability-block-tag">Bookings cancelled</span>
                      )}
                    </div>
                    <button
                      className="availability-block-delete"
                      onClick={() => handleDeleteBlock(b.id)}
                      title="Remove block"
                    >×</button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
