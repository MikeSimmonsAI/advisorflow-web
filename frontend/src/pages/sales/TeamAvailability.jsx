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
import { Card, Chip, ErrorBar, Empty, wallDateTime } from './parts'

const START_HOUR = 8
const END_HOUR = 19
const PX_PER_HOUR = 52

/* ─── CSS custom-property theme tokens ─────────────────────────────────────── */
const TA_STYLE = `
.ta-root {
  --ta-header-bg:     #f7f9fb;
  --ta-header-border: #dfe6eb;
  --ta-col-border:    #e1e7ec;
  --ta-hour-line:     #eef2f5;
  --ta-col-bg:        #ffffff;
  --ta-col-stripe:    #eef2f5;
  --ta-subtle-text:   #8999a5;
  --ta-free-bg:       rgba(85,199,154,.13);
  --ta-free-accent:   #55c79a;
  --ta-busy-bg:       #eef2f6;
  --ta-busy-text:     #68798a;
  --ta-busy-border:   #b6c3ce;
  --ta-own-bg:        #e8f7f5;
  --ta-own-text:      #155e57;
  --ta-own-border:    #1A9B8E;
  --ta-legend-border: #e6ebef;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-appearance="light"]) .ta-root {
    --ta-header-bg:     #1e2428;
    --ta-header-border: #2d3840;
    --ta-col-border:    #2d3840;
    --ta-hour-line:     #242c33;
    --ta-col-bg:        #1a2127;
    --ta-col-stripe:    #1f272e;
    --ta-subtle-text:   #6b7f8e;
    --ta-free-bg:       rgba(85,199,154,.15);
    --ta-busy-bg:       #232b33;
    --ta-busy-text:     #8999a5;
    --ta-busy-border:   #3d4e5a;
    --ta-own-bg:        #1a312e;
    --ta-own-text:      #3dc9a4;
    --ta-own-border:    #1A9B8E;
    --ta-legend-border: #2d3840;
  }
}
[data-appearance="dark"] .ta-root {
  --ta-header-bg:     #1e2428;
  --ta-header-border: #2d3840;
  --ta-col-border:    #2d3840;
  --ta-hour-line:     #242c33;
  --ta-col-bg:        #1a2127;
  --ta-col-stripe:    #1f272e;
  --ta-subtle-text:   #6b7f8e;
  --ta-free-bg:       rgba(85,199,154,.15);
  --ta-busy-bg:       #232b33;
  --ta-busy-text:     #8999a5;
  --ta-busy-border:   #3d4e5a;
  --ta-own-bg:        #1a312e;
  --ta-own-text:      #3dc9a4;
  --ta-own-border:    #1A9B8E;
  --ta-legend-border: #2d3840;
}
`

function isoDate(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10)
}

/** The API sends naive UTC with no suffix; adding 'Z' makes Intl timeZone
 *  conversion correct rather than off by the viewer's offset. */
function asUtc(iso) {
  const s = String(iso)
  return new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z')
}

/** Where a UTC instant sits on the grid, in that member's own timezone. */
function offsetPx(iso, tz) {
  const d = asUtc(iso)
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
  }).format(asUtc(iso))
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
      {/* inject theme tokens */}
      <style>{TA_STYLE}</style>

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
                {wallDateTime(booked.starts_at_local || booked.starts_at)} ·{' '}
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
        <div className="ta-root">
          <Card title={'TEAM DAY VIEW · ' + data.brand_sales_org.name}
                sub={'Each column is that person\'s own working day'} bodyless>
            <div style={{ overflowX: 'auto' }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: `70px repeat(${data.members.length}, minmax(150px, 1fr))`,
                minWidth: 70 + data.members.length * 150,
              }}>
                {/* header */}
                <div style={{
                  padding: 10, fontSize: 9, fontWeight: 800,
                  color: 'var(--ta-subtle-text)',
                  borderBottom: '1px solid var(--ta-header-border)',
                  background: 'var(--ta-header-bg)',
                }}>TIME</div>
                {data.members.map(m => (
                  <div key={m.user_id} style={{
                    padding: 10, fontSize: 10, fontWeight: 800,
                    borderLeft: '1px solid var(--ta-col-border)',
                    borderBottom: '1px solid var(--ta-header-border)',
                    background: 'var(--ta-header-bg)',
                  }}>
                    {m.full_name}
                    <div style={{ fontSize: 8, fontWeight: 400, color: 'var(--ta-subtle-text)', marginTop: 3 }}>
                      {m.timezone}{m.accepts_bookings ? '' : ' · not bookable'}
                    </div>
                  </div>
                ))}

                {/* time gutter */}
                <div>
                  {hours.map(h => (
                    <div key={h} style={{
                      height: PX_PER_HOUR,
                      borderBottom: '1px solid var(--ta-hour-line)',
                      padding: '4px 8px', fontSize: 8,
                      color: 'var(--ta-subtle-text)',
                    }}>
                      {h % 12 === 0 ? 12 : h % 12}{h < 12 ? ' AM' : ' PM'}
                    </div>
                  ))}
                </div>

                {/* one column per person */}
                {data.members.map(m => (
                  <div key={m.user_id} style={{
                    position: 'relative', height: gridHeight,
                    borderLeft: '1px solid var(--ta-col-border)',
                    background: `repeating-linear-gradient(to bottom,
                      var(--ta-col-bg) 0,
                      var(--ta-col-bg) ${PX_PER_HOUR - 1}px,
                      var(--ta-hour-line) ${PX_PER_HOUR}px)`,
                  }}>
                    {/* free time blocks */}
                    {m.free.map((f, i) => {
                      const top = offsetPx(f.starts_at, m.timezone)
                      const bottom = offsetPx(f.ends_at, m.timezone)
                      if (bottom <= 0 || top >= gridHeight) return null
                      return (
                        <div key={'f' + i} style={{
                          position: 'absolute', left: 4, right: 4,
                          top: Math.max(0, top),
                          height: Math.min(gridHeight, bottom) - Math.max(0, top),
                          background: 'var(--ta-free-bg)',
                          borderLeft: '2px solid var(--ta-free-accent)',
                          borderRadius: 4,
                        }} />
                      )
                    })}

                    {/* meeting blocks */}
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
                          background: opaque ? 'var(--ta-busy-bg)' : 'var(--ta-own-bg)',
                          color: opaque ? 'var(--ta-busy-text)' : 'var(--ta-own-text)',
                          borderLeft: '3px solid ' + (opaque ? 'var(--ta-busy-border)' : 'var(--ta-own-border)'),
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

            <div className="sw-card-b sw-flex" style={{
              gap: 14, borderTop: '1px solid var(--ta-legend-border)',
            }}>
              <span className="sw-flex" style={{ gap: 6 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 3,
                  background: 'var(--ta-free-bg)',
                  borderLeft: '2px solid var(--ta-free-accent)',
                }} />
                <span className="sw-subtle">Available</span>
              </span>
              <span className="sw-flex" style={{ gap: 6 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 3,
                  background: 'var(--ta-own-bg)',
                  borderLeft: '3px solid var(--ta-own-border)',
                }} />
                <span className="sw-subtle">Your meeting</span>
              </span>
              <span className="sw-flex" style={{ gap: 6 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 3,
                  background: 'var(--ta-busy-bg)',
                  borderLeft: '3px solid var(--ta-busy-border)',
                }} />
                <span className="sw-subtle">Busy — details not shown to you</span>
              </span>
            </div>
          </Card>
        </div>
      )}
    </SalesShell>
  )
}
