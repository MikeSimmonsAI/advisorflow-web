/**
 * Performance - conversion, touches, trend, booking. GET /roofing-ops/dashboard.
 * Every percentage is printed with the count it divides by. An empty range is
 * an empty state, never zeros dressed up as results.
 */
import { useMemo, useState } from 'react'
import { useRoofing } from './RoofingShell'
import { Empty, ErrorNote, Loading, Panel, SampleTag, n, pct, useApi } from './ui'
import './roofing-ops.css'

const PRESETS = [{ k: '7', label: '7 days' }, { k: '30', label: '30 days' }, { k: '90', label: '90 days' }, { k: 'custom', label: 'Custom' }]

function isoDaysAgo(d) {
  const t = new Date(Date.now() - (d - 1) * 86400000)
  return t.toISOString().slice(0, 10)
}

export default function Performance() {
  const { status } = useRoofing()
  const [preset, setPreset] = useState('30')
  const [start, setStart] = useState(isoDaysAgo(30))
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10))
  const [owner, setOwner] = useState('')
  const range = preset === 'custom' ? { start, end } : { start: isoDaysAgo(Number(preset)), end: new Date().toISOString().slice(0, 10) }
  const qs = new URLSearchParams({ ...range, ...(owner ? { owner } : {}) }).toString()
  const { data, error, loading, reload } = useApi(`/roofing-ops/dashboard?${qs}`)

  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Performance</h1>
          <p className="rf-lede">How your existing leads are turning into conversations and booked inspections.</p>
        </div>
        <div className="rf-filters">
          <div className="rf-tabs" role="tablist" aria-label="Date range">
            {PRESETS.map((p) => (
              <button key={p.k} className="rf-tab" role="tab" aria-selected={preset === p.k} onClick={() => setPreset(p.k)}>{p.label}</button>
            ))}
          </div>
          {preset === 'custom' && (
            <>
              <input className="rf-input" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} aria-label="Start date" />
              <input className="rf-input" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} aria-label="End date" />
            </>
          )}
          {status.can_manage && (
            <select className="rf-select" value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Salesperson">
              <option value="">Whole company</option>
              <option value="me">My leads</option>
              {(status.team || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          )}
        </div>
      </div>

      {error && <ErrorNote onRetry={reload}>{error}</ErrorNote>}
      {!data && loading && <Loading label="Calculating" />}
      {data && <Body d={data} />}
    </div>
  )
}

function Body({ d }) {
  return (
    <div className="rf-stack">
      <p className="rf-pop">
        {d.sample_data && <SampleTag />}
        <span>{d.population}. Grouped by {d.range.grain}.</span>
      </p>
      {!d.has_data ? (
        <Panel>
          <Empty icon="chart" title="No homeowners were imported in this date range">
            Pick a longer range, or import your existing leads from Leads &amp; Data. Results appear here as soon as follow-up starts.
          </Empty>
        </Panel>
      ) : (
        <>
          <Panel pad={false}>
            <div className="rf-kpis">
              {d.kpis.map((k) => (
                <div className="rf-kpi" key={k.key} title={k.hint || ''}>
                  <div className="rf-kpi__label">{k.label}</div>
                  <div className="rf-kpi__value">{n(k.value)}</div>
                  {k.denominator !== undefined
                    ? <div className="rf-kpi__pct"><b>{pct(k.pct)}</b><br />{n(k.value)} of {n(k.denominator)} {k.denominator_label}</div>
                    : <div className="rf-kpi__pct">{k.hint}</div>}
                </div>
              ))}
            </div>
          </Panel>
          <div className="rf-grid-2">
            <Funnel funnel={d.funnel} />
            <Touches rows={d.by_touch} />
          </div>
          <Trend trend={d.trend} grain={d.range.grain} />
          <div className="rf-grid-2">
            <Booking b={d.booking} />
            <FollowUp f={d.follow_up} stages={d.stage_now} />
          </div>
        </>
      )}
    </div>
  )
}

function Funnel({ funnel }) {
  const top = Math.max(1, ...funnel.map((s) => s.count))
  const COLORS = ['#2f6fed', '#3d7cf0', '#4b89f3', '#12925e', '#f26a1b', '#d9580c', '#6d4fd8', '#5a3fc0', '#12925e']
  return (
    <Panel title="Lead conversion funnel" sub="Homeowners who ever reached each stage. Bars are shares of everyone imported.">
      <div className="rf-funnel">
        {funnel.map((s, i) => (
          <div className="rf-funnel__row" key={s.key}>
            <span className="rf-funnel__name">{s.label}</span>
            <div className="rf-funnel__track" aria-hidden="true">
              <div className="rf-funnel__bar" style={{ width: `${(s.count / top) * 100}%`, background: COLORS[i] }}>{s.count > 0 ? n(s.count) : ''}</div>
            </div>
            <span className="rf-funnel__nums">
              <b>{pct(s.pct_of_imported)}</b> of imported
              {s.pct_of_previous !== null && <><br />{pct(s.pct_of_previous)} of previous step</>}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

function Touches({ rows }) {
  const best = useMemo(() => {
    const ok = rows.filter((r) => r.received > 0 && r.reply_rate !== null)
    if (!ok.length) return null
    return ok.reduce((a, b) => (b.reply_rate > a.reply_rate ? b : a))
  }, [rows])
  return (
    <Panel title="Response by touch" sub="A reply is credited to the most recent text or email before it.">
      <div className="rf-touches">
        {rows.map((r) => (
          <div key={r.touch} className={`rf-touch ${best && best.touch === r.touch ? 'rf-touch--best' : ''}`}>
            <div className="rf-touch__name">{r.touch === '4+' ? '4th touch and later' : `Touch ${r.touch}`}</div>
            <div className="rf-touch__rate">{pct(r.reply_rate)}</div>
            <div className="rf-touch__sub">{n(r.replied)} of {n(r.received)} replied</div>
            <div className="rf-touch__sub">{pct(r.opt_out_rate)} opted out ({n(r.opted_out)})</div>
            <div className="rf-touch__bar" aria-hidden="true"><span style={{ width: `${r.reply_rate || 0}%` }} /></div>
          </div>
        ))}
      </div>
      <p className="rf-callout">
        {best
          ? <>Best performer: <b>{best.touch === '4+' ? 'the 4th touch and later' : `touch ${best.touch}`}</b> — {pct(best.reply_rate)} of the {n(best.received)} homeowners who received it replied.</>
          : 'No touch has gone out in this range yet, so there is nothing to compare.'}
      </p>
    </Panel>
  )
}

const SERIES = [
  { key: 'sent', label: 'Texts & emails sent', color: '#2f6fed' },
  { key: 'replies', label: 'Replies', color: '#12925e' },
  { key: 'booked', label: 'Inspections booked', color: '#f26a1b' },
]

function Trend({ trend, grain }) {
  const W = 900, H = 220, L = 34, R = 10, T = 12, B = 26
  const max = Math.max(1, ...trend.flatMap((p) => SERIES.map((s) => p[s.key])))
  const x = (i) => L + (trend.length <= 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (trend.length - 1))
  const y = (v) => T + (H - T - B) * (1 - v / max)
  const ticks = [0, Math.round(max / 2), max]
  const every = Math.max(1, Math.ceil(trend.length / 8))
  const total = trend.reduce((a, p) => a + p.sent + p.replies + p.booked, 0)
  return (
    <Panel title={`Engagement by ${grain}`} sub="Activity on these homeowners, by the day it happened."
      actions={<div className="rf-legend">{SERIES.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div>}>
      {total === 0 ? (
        <Empty icon="chart" title="No activity in this range yet" />
      ) : (
        <svg className="rf-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Sent, replies and inspections booked per ${grain}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#e1e7ef" />
              <text x={L - 6} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          {trend.map((p, i) => (i % every === 0 ? (
            <text key={p.period} x={x(i)} y={H - 6} textAnchor="middle">
              {new Date(p.period + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </text>
          ) : null))}
          {SERIES.map((s) => (
            <g key={s.key}>
              <polyline fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"
                points={trend.map((p, i) => `${x(i)},${y(p[s.key])}`).join(' ')} />
              {trend.map((p, i) => (p[s.key] > 0 ? (
                <circle key={i} cx={x(i)} cy={y(p[s.key])} r="3" fill={s.color}>
                  <title>{`${p.period}: ${p[s.key]} ${s.label.toLowerCase()}`}</title>
                </circle>
              ) : null))}
            </g>
          ))}
        </svg>
      )}
    </Panel>
  )
}

function Booking({ b }) {
  return (
    <Panel title="Inspection booking conversion" sub="From an engaged homeowner to an inspection on the calendar.">
      <div className="rf-steps">
        <div className="rf-step">
          <span className="rf-step__label">Offered times<span>{n(b.offered)} of {n(b.engaged)} engaged homeowners</span></span>
          <span className="rf-step__val">{pct(b.offered_of_engaged)}</span>
        </div>
        <div className="rf-step">
          <span className="rf-step__label">Booked after an offer<span>{n(b.booked)} of {n(b.offered)} offered</span></span>
          <span className="rf-step__val">{pct(b.booked_of_offered)}</span>
        </div>
        <div className="rf-step">
          <span className="rf-step__label">Engaged to booked<span>{n(b.booked)} of {n(b.engaged)} engaged homeowners</span></span>
          <span className="rf-step__val">{pct(b.booked_of_engaged)}</span>
        </div>
      </div>
    </Panel>
  )
}

function FollowUp({ f, stages }) {
  return (
    <Panel title="Follow-up performance" sub="What the outreach cost in touches, and where homeowners stand now.">
      <div className="rf-steps">
        <div className="rf-step"><span className="rf-step__label">Touches sent<span>Texts and emails that actually left</span></span><span className="rf-step__val">{n(f.touches_sent)}</span></div>
        <div className="rf-step"><span className="rf-step__label">Average touches<span>Per contacted homeowner</span></span><span className="rf-step__val">{f.avg_touches_per_contacted ?? '—'}</span></div>
        <div className="rf-step"><span className="rf-step__label">Opt-out rate<span>Of contacted homeowners</span></span><span className="rf-step__val">{pct(f.opt_out_rate)}</span></div>
        <div className="rf-step"><span className="rf-step__label">Closed lost<span>Marked lost by your team</span></span><span className="rf-step__val">{n(f.lost)}</span></div>
      </div>
      <div className="rf-chips" style={{ marginTop: 14 }}>
        {stages.filter((s) => s.count > 0).map((s) => <span key={s.key} className="rf-chip">{s.label} <b>{n(s.count)}</b></span>)}
      </div>
    </Panel>
  )
}
