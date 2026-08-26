/**
 * My Availability — the inputs to the shared scheduling engine.
 *
 * What a salesperson sets here directly determines which openings the Find Team
 * Time finder will offer for them, so the page says so rather than presenting
 * itself as a settings screen nobody connects to anything.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, ErrorBar, dateTime } from './parts'
import CalendarConnections from './CalendarConnections'
import VideoStatus from './VideoStatus'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

// A deliberately short list of common zones plus whatever the profile already
// holds — an unknown IANA name is rejected by the API rather than silently
// defaulting someone's whole calendar to Central.
const ZONES = [
  'America/Chicago', 'America/New_York', 'America/Denver', 'America/Los_Angeles',
  'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu', 'UTC',
  'Europe/London', 'Europe/Madrid',
]

function hhmm(minutes) {
  const h = Math.floor(minutes / 60), m = minutes % 60
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0')
}
function toMinutes(v) {
  const [h, m] = (v || '00:00').split(':').map(Number)
  return h * 60 + (m || 0)
}

export default function MyAvailability() {
  const [data, setData] = useState(null)
  const [days, setDays] = useState({})
  const [lunch, setLunch] = useState({})
  const [meta, setMeta] = useState({})
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [off, setOff] = useState({ label: '', starts_at: '', ends_at: '' })

  const load = useCallback(async () => {
    setError(null)
    try {
      const p = await api.get('/sales/availability/me')
      setData(p)
      const d = {}
      DAYS.forEach((_, i) => {
        const w = p.windows.find(x => x.day_of_week === i)
        d[i] = w ? { on: true, start: hhmm(w.start_minute), end: hhmm(w.end_minute) }
                 : { on: false, start: '09:00', end: '17:00' }
      })
      setDays(d)
      const l = {}
      DAYS.forEach((_, i) => {
        const b = p.recurring_blocks.find(x => x.day_of_week === i)
        l[i] = b ? { on: true, start: hhmm(b.start_minute), end: hhmm(b.end_minute),
                     label: b.label || 'Lunch' }
                 : { on: false, start: '12:00', end: '13:00', label: 'Lunch' }
      })
      setLunch(l)
      setMeta({
        timezone: p.timezone,
        buffer_before_minutes: p.buffer_before_minutes,
        buffer_after_minutes: p.buffer_after_minutes,
        min_notice_minutes: p.min_notice_minutes,
        booking_horizon_days: p.booking_horizon_days,
        accepts_bookings: p.accepts_bookings,
      })
    } catch (e) { setError(e.message || 'Could not load your availability.') }
  }, [])

  useEffect(() => { load() }, [load])

  async function save() {
    setSaving(true); setError(null); setSaved(false)
    try {
      const windows = []
      const blocks = []
      DAYS.forEach((_, i) => {
        if (days[i]?.on) {
          windows.push({ day_of_week: i, start_minute: toMinutes(days[i].start),
                         end_minute: toMinutes(days[i].end) })
        }
        if (lunch[i]?.on) {
          blocks.push({ day_of_week: i, label: lunch[i].label || 'Blocked',
                        start_minute: toMinutes(lunch[i].start),
                        end_minute: toMinutes(lunch[i].end) })
        }
      })
      const p = await api.put('/sales/availability/me', {
        ...meta, windows, recurring_blocks: blocks,
      })
      setData(p); setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) { setError(e.message || 'Save failed.') }
    finally { setSaving(false) }
  }

  async function addTimeOff() {
    if (!off.starts_at || !off.ends_at) return
    setSaving(true); setError(null)
    try {
      await api.post('/sales/availability/time-off', {
        label: off.label || 'Time off',
        starts_at: new Date(off.starts_at).toISOString(),
        ends_at: new Date(off.ends_at).toISOString(),
      })
      setOff({ label: '', starts_at: '', ends_at: '' })
      await load()
    } catch (e) { setError(e.message || 'Could not save time off.') }
    finally { setSaving(false) }
  }

  async function removeTimeOff(id) {
    try { await api.delete('/sales/availability/time-off/' + id); await load() }
    catch (e) { setError(e.message) }
  }

  const zones = [...new Set([...(meta.timezone ? [meta.timezone] : []), ...ZONES])]

  return (
    <SalesShell
      title="My Availability"
      subtitle="These settings decide which openings the team scheduler will offer for you."
      actions={
        <>
          {saved && <Chip tone="green">Saved</Chip>}
          <button className="sw-btn sw-primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save availability'}
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />
      {!data && <div className="sw-subtle">Loading…</div>}

      {data && (
        <div className="sw-grid2">
          <div>
            <Card title="WORKING DAYS &amp; HOURS"
                  sub="Local to your timezone — daylight saving is handled for you">
              {DAYS.map((name, i) => (
                <div key={name} className="sw-flex" style={{ gap: 10, marginBottom: 8 }}>
                  <label className="sw-flex" style={{ width: 118, gap: 7, fontSize: 11 }}>
                    <input type="checkbox" checked={!!days[i]?.on}
                           onChange={e => setDays({ ...days, [i]: { ...days[i], on: e.target.checked } })} />
                    {name}
                  </label>
                  <input className="sw-input" type="time" style={{ width: 120 }}
                         disabled={!days[i]?.on} value={days[i]?.start || '09:00'}
                         onChange={e => setDays({ ...days, [i]: { ...days[i], start: e.target.value } })} />
                  <span className="sw-subtle">to</span>
                  <input className="sw-input" type="time" style={{ width: 120 }}
                         disabled={!days[i]?.on} value={days[i]?.end || '17:00'}
                         onChange={e => setDays({ ...days, [i]: { ...days[i], end: e.target.value } })} />
                </div>
              ))}
            </Card>

            <div className="sw-mt">
              <Card title="RECURRING BLOCKS" sub="Lunch, a standing internal meeting — carved out every week">
                {DAYS.map((name, i) => (
                  <div key={name} className="sw-flex" style={{ gap: 10, marginBottom: 8 }}>
                    <label className="sw-flex" style={{ width: 118, gap: 7, fontSize: 11 }}>
                      <input type="checkbox" checked={!!lunch[i]?.on}
                             onChange={e => setLunch({ ...lunch, [i]: { ...lunch[i], on: e.target.checked } })} />
                      {name}
                    </label>
                    <input className="sw-input" style={{ width: 110 }} placeholder="Lunch"
                           disabled={!lunch[i]?.on} value={lunch[i]?.label || ''}
                           onChange={e => setLunch({ ...lunch, [i]: { ...lunch[i], label: e.target.value } })} />
                    <input className="sw-input" type="time" style={{ width: 110 }}
                           disabled={!lunch[i]?.on} value={lunch[i]?.start || '12:00'}
                           onChange={e => setLunch({ ...lunch, [i]: { ...lunch[i], start: e.target.value } })} />
                    <span className="sw-subtle">to</span>
                    <input className="sw-input" type="time" style={{ width: 110 }}
                           disabled={!lunch[i]?.on} value={lunch[i]?.end || '13:00'}
                           onChange={e => setLunch({ ...lunch, [i]: { ...lunch[i], end: e.target.value } })} />
                  </div>
                ))}
              </Card>
            </div>
          </div>

          <div>
            {/* First in this column deliberately. A connected calendar changes
                what every setting below it actually means — external
                commitments become part of the availability these rules shape. */}
            <CalendarConnections />

            {/* Beside the calendar connections because they answer adjacent
                questions: one is "will this meeting reach my calendar", the
                other "will it have a video link". */}
            <div className="sw-mt">
              <VideoStatus />
            </div>

            <div className="sw-mt" />
            <Card title="BOOKING RULES">
              <div className="sw-field">
                <label>TIMEZONE</label>
                <select className="sw-select" value={meta.timezone || ''}
                        onChange={e => setMeta({ ...meta, timezone: e.target.value })}>
                  {zones.map(z => <option key={z} value={z}>{z}</option>)}
                </select>
                <div className="sw-subtle" style={{ marginTop: 6 }}>
                  Yours alone. Teammates in other zones keep theirs, and the finder
                  works across all of them.
                </div>
              </div>
              <div className="sw-grid-even">
                <div className="sw-field">
                  <label>BUFFER BEFORE (MIN)</label>
                  <input className="sw-input" type="number" min="0" max="240"
                         value={meta.buffer_before_minutes ?? 0}
                         onChange={e => setMeta({ ...meta, buffer_before_minutes: Number(e.target.value) })} />
                </div>
                <div className="sw-field">
                  <label>BUFFER AFTER (MIN)</label>
                  <input className="sw-input" type="number" min="0" max="240"
                         value={meta.buffer_after_minutes ?? 0}
                         onChange={e => setMeta({ ...meta, buffer_after_minutes: Number(e.target.value) })} />
                </div>
                <div className="sw-field">
                  <label>MINIMUM NOTICE (MIN)</label>
                  <input className="sw-input" type="number" min="0"
                         value={meta.min_notice_minutes ?? 0}
                         onChange={e => setMeta({ ...meta, min_notice_minutes: Number(e.target.value) })} />
                </div>
                <div className="sw-field">
                  <label>BOOKING HORIZON (DAYS)</label>
                  <input className="sw-input" type="number" min="1" max="365"
                         value={meta.booking_horizon_days ?? 60}
                         onChange={e => setMeta({ ...meta, booking_horizon_days: Number(e.target.value) })} />
                </div>
              </div>
              <div className="sw-field">
                <label className="sw-flex" style={{ gap: 7 }}>
                  <input type="checkbox" checked={meta.accepts_bookings !== false}
                         onChange={e => setMeta({ ...meta, accepts_bookings: e.target.checked })} />
                  <span style={{ fontSize: 11, fontWeight: 400 }}>
                    Available to be booked
                  </span>
                </label>
                <div className="sw-subtle" style={{ marginTop: 6 }}>
                  Unticking removes you from every shared search until you tick it again.
                </div>
              </div>
            </Card>

            <div className="sw-mt">
              <Card title="TIME OFF" sub="Dated absences, on top of your weekly pattern">
                {data.time_off.length === 0 && (
                  <div className="sw-subtle" style={{ marginBottom: 10 }}>None scheduled.</div>
                )}
                {data.time_off.map(t => (
                  <div key={t.id} className="sw-flex sw-between"
                       style={{ padding: '8px 0', borderBottom: '1px solid #eef2f5' }}>
                    <div>
                      <b style={{ fontSize: 11 }}>{t.label}</b>
                      <div className="sw-subtle">
                        {dateTime(t.starts_at)} → {dateTime(t.ends_at)}
                      </div>
                    </div>
                    <button className="sw-tiny" onClick={() => removeTimeOff(t.id)}>Remove</button>
                  </div>
                ))}
                <div className="sw-field">
                  <label>LABEL</label>
                  <input className="sw-input" value={off.label} placeholder="PTO, conference…"
                         onChange={e => setOff({ ...off, label: e.target.value })} />
                </div>
                <div className="sw-grid-even">
                  <div className="sw-field">
                    <label>FROM</label>
                    <input className="sw-input" type="datetime-local" value={off.starts_at}
                           onChange={e => setOff({ ...off, starts_at: e.target.value })} />
                  </div>
                  <div className="sw-field">
                    <label>TO</label>
                    <input className="sw-input" type="datetime-local" value={off.ends_at}
                           onChange={e => setOff({ ...off, ends_at: e.target.value })} />
                  </div>
                </div>
                <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
                  <button className="sw-btn" onClick={addTimeOff}
                          disabled={saving || !off.starts_at || !off.ends_at}>Add time off</button>
                </div>
              </Card>
            </div>
          </div>
        </div>
      )}
    </SalesShell>
  )
}
