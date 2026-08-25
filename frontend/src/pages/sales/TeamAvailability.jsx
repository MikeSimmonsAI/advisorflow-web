/**
 * Team Availability — who is free, who is busy, and the shared time finder.
 *
 * A column per team member on a 9am-6pm grid. A rep can see that a colleague is
 * occupied (they need that to book) but only sees the TITLE of meetings they
 * are on themselves; everything else reads "Busy". The server enforces that —
 * it sends the literal string "Busy" rather than a title this viewer may not
 * see, so the privacy rule cannot be undone in the browser.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import FindTeamTime from './FindTeamTime'
import { Card, Chip, ErrorBar, Empty } from './parts'

const START_HOUR = 8
const END_HOUR = 19
const PX_PER_HOUR = 52

function isoDate(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10)
}

/** Where a UTC instant sits on the grid, in that member's own timezone. */
function offsetPx(iso, tz) {
  const d = new Date(iso)
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour: 'numeric', minute: 'numeric', hour12: false,
  }).formatToParts(d)
  const h = Number(parts.find(p => p.type === 'hour')?.value || 0)
  const m = Number(parts.find(p => p.type === 'minute')?.value || 0)
  return ((h + m / 60) - START_HOUR) * PX_PER_HOUR
}

function timeLabel(iso, tz) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: tz, hour: 'numeric', minute: '2-digit',
  }).format(new Date(iso))
}

export default function TeamAvailability() {
  const [day, setDay] = useState(isoDate(new Date()))
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [finding, setFinding] = useState(false)
  const [booked, setBooked] = useState(null)

  const load = useCallback(async (d) => {
    setLoading(true); setError(null)
    try { setData(await api.get('/sales/availability/team?day=' + d)) }
    catch (e) { setError(e.message || 'Could not load team availability.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load(day) }, [load, day])

  const hours = []
  for (let h = START_HOUR; h < END_HOUR; h++) hours.push(h)
  const gridHeight = (END_HOUR - START_HOUR) * PX_PER_HOUR

  function shift(n) {
    const d = new Date(day + 'T12:00:00')
    d.setDate(d.getDate() + n)
    setDay(isoDate(d))
  }

  return (
    <SalesShell
      title="Team Availability"
      subtitle="Who is free, who is busy, and the first time everyone can meet."
      actions={
        <>
          <button className="sw-btn" onClick={() => shift(-1)}>←</button>
          <input className="sw-input" type="date" style={{ width: 160 }}
                 value={day} onChange={e => setDay(e.target.value)} />
          <button className="sw-btn" onClick={() => shift(1)}>→</button>
          <button className="sw-btn sw-primary" onClick={() => setFinding(true)}>
            Find Team Time
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={() => load(day)} />

      {finding && (
        <FindTeamTime
          onClose={() => setFinding(false)}
          onBooked={a => { setFinding(false); setBooked(a); load(day) }}
        />
      )}

      {booked && (
        <div className="sw-card" style={{ marginBottom: 16 }}>
          <div className="sw-card-b sw-flex sw-between">
            <div>
              <Chip tone="green">Booked</Chip>
              <b style={{ marginLeft: 8, fontSize: 12 }}>{booked.title}</b>
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                {new Date(booked.starts_at).toLocaleString()} ·{' '}
                {booked.participants.map(p => p.full_name).join(', ')}
              </div>
            </div>
            <button className="sw-tiny" onClick={() => setBooked(null)}>Dismiss</button>
          </div>
        </div>
      )}

      {loading && !data && <div className="sw-subtle">Loading…</div>}

      {data && data.members.length === 0 && (
        <div className="sw-card">
          <Empty title="No team members">
            Nobody holds an active membership in this brand sales organization yet.
          </Empty>
        </div>
      )}

      {data && data.members.length > 0 && (
        <Card title={'TEAM DAY VIEW · ' + data.brand_sales_org.name}
              sub={'Each column is that person’s own working day'} bodyless>
          <div style={{ overflowX: 'auto' }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: `70px repeat(${data.members.length}, minmax(150px, 1fr))`,
              minWidth: 70 + data.members.length * 150,
            }}>
              {/* header */}
              <div style={{ padding: 10, fontSize: 9, fontWeight: 800, color: '#8999a5',
                            borderBottom: '1px solid #dfe6eb', background: '#f7f9fb' }}>TIME</div>
              {data.members.map(m => (
                <div key={m.user_id} style={{
                  padding: 10, fontSize: 10, fontWeight: 800,
                  borderLeft: '1px solid #e1e7ec', borderBottom: '1px solid #dfe6eb',
                  background: '#f7f9fb',
                }}>
                  {m.full_name}
                  <div style={{ fontSize: 8, fontWeight: 400, color: '#8999a5', marginTop: 3 }}>
                    {m.timezone}{m.accepts_bookings ? '' : ' · not bookable'}
                  </div>
                </div>
              ))}

              {/* time gutter */}
              <div>
                {hours.map(h => (
                  <div key={h} style={{
                    height: PX_PER_HOUR, borderBottom: '1px solid #eef2f5',
                    padding: '4px 8px', fontSize: 8, color: '#8c9ba7',
                  }}>
                    {h % 12 === 0 ? 12 : h % 12}{h < 12 ? ' AM' : ' PM'}
                  </div>
                ))}
              </div>

              {/* one column per person */}
              {data.members.map(m => (
                <div key={m.user_id} style={{
                  position: 'relative', height: gridHeight,
                  borderLeft: '1px solid #e4eaee',
                  background: `repeating-linear-gradient(to bottom,#fff 0,#fff ${PX_PER_HOUR - 1}px,#eef2f5 ${PX_PER_HOUR}px)`,
                }}>
                  {/* free time, drawn in their own timezone */}
                  {m.free.map((f, i) => {
                    const top = offsetPx(f.starts_at, m.timezone)
                    const bottom = offsetPx(f.ends_at, m.timezone)
                    if (bottom <= 0 || top >= gridHeight) return null
                    return (
                      <div key={'f' + i} style={{
                        position: 'absolute', left: 4, right: 4,
                        top: Math.max(0, top),
                        height: Math.min(gridHeight, bottom) - Math.max(0, top),
                        background: 'rgba(85,199,154,.13)',
                        borderLeft: '2px solid #55c79a', borderRadius: 4,
                      }} />
                    )
                  })}
                  {/* meetings */}
                  {m.busy.map((b, i) => {
                    const top = offsetPx(b.starts_at, m.timezone)
                    const bottom = offsetPx(b.ends_at, m.timezone)
                    if (bottom <= 0 || top >= gridHeight) return null
                    const opaque = b.title === 'Busy'
                    return (
                      <div key={'b' + i} title={b.title} style={{
                        position: 'absolute', left: 6, right: 6,
                        top: Math.max(0, top),
                        height: Math.max(20, Math.min(gridHeight, bottom) - Math.max(0, top)),
                        borderRadius: 7, padding: '5px 7px', fontSize: 9, overflow: 'hidden',
                        background: opaque ? '#eef2f6' : '#e8f7f5',
                        color: opaque ? '#68798a' : '#155e57',
                        borderLeft: '3px solid ' + (opaque ? '#b6c3ce' : '#1A9B8E'),
                      }}>
                        <b style={{ display: 'block', fontSize: 9 }}>{b.title}</b>
                        <span style={{ fontSize: 8 }}>
                          {timeLabel(b.starts_at, m.timezone)}
                          {b.confirmation_status ? ' · ' + b.confirmation_status : ''}
                        </span>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>

          <div className="sw-card-b sw-flex" style={{ gap: 14, borderTop: '1px solid #e6ebef' }}>
            <span className="sw-flex" style={{ gap: 6 }}>
              <span style={{ width: 12, height: 12, borderRadius: 3,
                             background: 'rgba(85,199,154,.3)', borderLeft: '2px solid #55c79a' }} />
              <span className="sw-subtle">Available</span>
            </span>
            <span className="sw-flex" style={{ gap: 6 }}>
              <span style={{ width: 12, height: 12, borderRadius: 3, background: '#e8f7f5',
                             borderLeft: '3px solid #1A9B8E' }} />
              <span className="sw-subtle">Your meeting</span>
            </span>
            <span className="sw-flex" style={{ gap: 6 }}>
              <span style={{ width: 12, height: 12, borderRadius: 3, background: '#eef2f6',
                             borderLeft: '3px solid #b6c3ce' }} />
              <span className="sw-subtle">Busy — details not shown to you</span>
            </span>
          </div>
        </Card>
      )}
    </SalesShell>
  )
}
