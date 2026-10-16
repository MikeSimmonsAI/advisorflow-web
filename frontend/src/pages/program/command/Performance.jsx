/* SCI LIVE PERFORMANCE (Oct 15 redesign) - what EvoSys Pro is accomplishing.
     PerformanceHome  every metric card opens its exact records
     MetricDrill      the records behind a number, with date / location /
                      channel / touch / status filters (count == list)
     Journey          one family's complete recorded history
   Reads /program/command/analytics, /analytics/records, /analytics/journey.
   Read-only: nothing on these screens sends, enrolls or approves. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../api/client'

export const RANGES = [['today', 'Today'], ['7d', '7 Days'], ['30d', '30 Days'], ['all', 'All Time'], ['custom', 'Custom']]
const CT = { timeZone: 'America/Chicago' }
const fmtDay = iso => new Date(iso + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })
const fmtWhen = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  const today = new Date().toLocaleDateString([], CT) === d.toLocaleDateString([], CT)
  return (today ? 'Today ' : d.toLocaleDateString([], { ...CT, month: 'short', day: 'numeric' }) + ' ')
    + d.toLocaleTimeString([], { ...CT, hour: 'numeric', minute: '2-digit' })
}
const n = v => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
const pct = v => (v === null || v === undefined ? '—' : `${v}%`)
const RESULT = { delivered: 'Delivered', opened: 'Opened', failed: 'Failed', pending: 'Awaiting result' }
const CHANNEL = { sms: 'Text', email: 'Email', voicemail: 'Voicemail' }

export function rangeQuery(range, loc) {
  const p = new URLSearchParams({ range: range.preset })
  if (range.preset === 'custom') { if (range.start) p.set('start', range.start); if (range.end) p.set('end', range.end) }
  if (loc) p.set('location_id', loc)
  return p
}

export function RangeBar({ range, setRange, loc, setLoc, locations }) {
  const today = new Date().toLocaleDateString('en-CA', CT)
  return (
    <div className="pf-filters">
      <div className="pf-seg" role="tablist" aria-label="Date range">
        {RANGES.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={range.preset === k} className={range.preset === k ? 'on' : ''}
                  onClick={() => setRange(k === 'custom' ? { preset: 'custom', start: range.start || today, end: range.end || today } : { preset: k })}>{l}</button>))}
      </div>
      {range.preset === 'custom' && (
        <div className="pf-dates">
          <label>From<input type="date" value={range.start || ''} max={today} onChange={e => setRange({ ...range, start: e.target.value })} /></label>
          <label>To<input type="date" value={range.end || ''} max={today} onChange={e => setRange({ ...range, end: e.target.value })} /></label>
        </div>)}
      {setLoc && (
        <select className="pf-select" value={loc} onChange={e => setLoc(e.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {(locations || []).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>)}
    </div>
  )
}

/* ── Performance Home ─────────────────────────────────────────────────────── */
const HERO = [
  ['families_contacted', 'Families contacted', 'unique families reached out to'],
  ['new_families', 'New families', 'first touch in this period'],
  ['followup_families', 'Existing families', 'followed up (2nd+ touch)'],
  ['attempts', 'Outreach attempts', 'texts + emails tried'],
  ['delivered', 'Delivered', 'confirmed by carrier / mail'],
  ['replies', 'Replies', 'genuine family replies'],
  ['offers', 'Times offered', 'two real calendar times'],
  ['appointments', 'Appointments', 'confirmed on the calendar'],
]
const SECOND = [
  ['failed', 'Failed'], ['pending', 'Awaiting result'], ['ai_messages', 'AI replies sent'],
  ['staff_messages', 'Staff replies sent'], ['opt_outs', 'Opt-outs'], ['repliers', 'Families who replied'],
]

export function PerformanceHome({ range, setRange, loc, setLoc, openMetric }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const seq = useRef(0)
  const q = rangeQuery(range, loc).toString()
  useEffect(() => {
    const k = ++seq.current
    const go = () => api.get(`/program/command/analytics?${q}`)
      .then(r => { if (k === seq.current) { setD(r); setErr('') } })
      .catch(e => { if (k === seq.current) setErr(e.message || 'Could not load performance.') })
    go()
    const t = setInterval(() => { if (document.visibilityState === 'visible') go() }, 60000)
    return () => clearInterval(t)
  }, [q])
  const m = d?.metrics || {}
  const open = (metric, extra) => openMetric({ metric, ...(extra || {}) })
  const loading = !d || d.window?.preset !== range.preset
  return <>
    <RangeBar range={range} setRange={setRange} loc={loc} setLoc={setLoc} locations={d?.locations_all} />
    {err && <p className="cmd-err">{err}</p>}
    {!d ? <p className="cmd-muted">Loading performance…</p> : <div className={`pf-home ${loading ? 'pf-stale' : ''}`}>
      <p className="pf-period">{d.window.label}{d.window.start_date && d.window.preset !== 'today' ? ` · ${fmtDay(d.window.start_date)} – ${fmtDay(d.window.end_date)}` : ''}
        {' '}· Central Time · real families only (tests, demos and opt-outs never counted as results)</p>
      <div className="pf-kpis">
        {HERO.map(([k, l, s]) => (
          <button key={k} className={`pf-kpi ${k === 'appointments' ? 'goal' : ''}`} onClick={() => open(k)}>
            <span className="pf-kpi-l">{l}</span>
            <b>{n(m[k]?.value)}</b>
            <small>{s}</small>
            <i>{n(m[k]?.all_time)} all time ›</i>
          </button>))}
      </div>
      <div className="pf-chips">
        {SECOND.map(([k, l]) => (
          <button key={k} className={k === 'failed' && m[k]?.value ? 'bad' : ''} onClick={() => open(k)}>
            <b>{n(m[k]?.value)}</b>{l}</button>))}
        <button onClick={() => open('families_contacted')}><b>{d.rates.avg_touches_per_family ?? '—'}</b>Avg touches / family
          <small>{d.rates.avg_touches_per_family_all_time ?? '—'} all time</small></button>
      </div>

      <div className="pf-grid">
        <section className="cmd-card pf-card">
          <h3>Conversion funnel <small>{d.window.label}</small></h3>
          <Funnel steps={d.funnel} open={open} />
          <div className="pf-rates">
            <span>Delivery rate <b>{pct(d.rates.delivery_rate)}</b></span>
            <span>Reply rate <b>{pct(d.rates.reply_rate)}</b></span>
            <span>Booked / contacted <b>{pct(d.rates.booking_rate)}</b></span>
          </div>
        </section>

        <section className="cmd-card pf-card">
          <h3>Activity trend <small>{d.trend.step_days === 7 ? 'by week' : 'by day'}</small></h3>
          <Trend t={d.trend} />
        </section>
      </div>

      <section className="cmd-card pf-card">
        <h3>Touch analytics <small>tap any number for the records</small></h3>
        <TouchTable rows={d.touches} open={open} />
        <p className="pf-note">A reply, offer or appointment counts under the last touch the family received before it.
          New families are those whose 1st touch was in this period; existing families got a 2nd or later touch.</p>
      </section>

      <div className="pf-grid">
        <section className="cmd-card pf-card">
          <h3>Outreach engine</h3>
          <p className={`pf-batch ${d.daily_batch.on ? 'ok' : 'warn'}`}>{d.daily_batch.on
            ? `Automatic enrollment ON — ${d.daily_batch.count} new families each sending day from ${d.daily_batch.start}, one every ${d.daily_batch.spacing_minutes} min.`
            : <>Automatic enrollment is OFF — no new families are being added. <a href="/program?tab=send">Turn it on ›</a></>}</p>
          <div className="pf-snap">
            {[['loaded', 'Families loaded'], ['not_contacted', 'Not yet contacted'], ['in_outreach', 'In active outreach']].map(([k, l]) => (
              <button key={k} onClick={() => open(k)}><b>{n(m[k]?.value)}</b><span>{l}</span></button>))}
          </div>
          {d.failure_reasons.length > 0 && <>
            <h4>Why attempts failed</h4>
            {d.failure_reasons.map(r => (
              <button key={r.reason} className="pf-row" onClick={() => open('failed')}><span>{r.reason}</span><b>{r.count}</b></button>))}
          </>}
        </section>

        <section className="cmd-card pf-card pf-proj">
          <h3>Projection <em>ESTIMATE · NOT A RESULT</em></h3>
          <Projection p={d.projections} />
        </section>
      </div>

      <section className="cmd-card pf-card">
        <h3>Location performance <small>{d.window.label}</small></h3>
        <LocationTable rows={d.locations} open={open} setLoc={setLoc} />
      </section>
      <p className="cmd-foot">Every number opens the exact records behind it. Updated {fmtWhen(d.updated_at)}.</p>
    </div>}
  </>
}

function Funnel({ steps, open }) {
  const top = Math.max(1, steps[0]?.value || 0)
  return <div className="pf-funnel">{steps.map((s, i) => (
    <button key={s.key} onClick={() => open(s.key)} title={`${s.label}: ${s.value}`}>
      <span className="pf-fl">{s.label}</span>
      <span className="pf-fbar"><span style={{ width: `${Math.max(s.value ? 2 : 0, (100 * s.value) / top)}%` }} /></span>
      <b>{n(s.value)}</b>
      {i > 0 && <small>{steps[i - 1].value ? `${Math.round((100 * s.value) / steps[i - 1].value)}%` : '—'}</small>}
    </button>))}</div>
}

const SERIES = [['attempts', 'Attempts'], ['delivered', 'Delivered'], ['new_families', 'New families'], ['replies', 'Replies'], ['appointments', 'Appointments']]
function Trend({ t }) {
  const [s, setS] = useState('attempts')
  const [hover, setHover] = useState(null)
  const vals = t.series[s] || []
  const max = Math.max(1, ...vals)
  const W = 600, H = 150
  const bw = vals.length ? W / vals.length : 0
  const total = vals.reduce((a, b) => a + b, 0)
  return <>
    <div className="pf-tabs">{SERIES.map(([k, l]) => <button key={k} className={s === k ? 'on' : ''} onClick={() => setS(k)}>{l}</button>)}</div>
    <div className="pf-chart" onMouseLeave={() => setHover(null)}>
      <div className="pf-plot">
        <span className="pf-ymax">{max}</span>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
             aria-label={`${SERIES.find(x => x[0] === s)[1]} per ${t.step_days === 7 ? 'week' : 'day'}`}>
          {[0.5, 1].map(f => <line key={f} x1={0} x2={W} y1={H - f * (H - 10)} y2={H - f * (H - 10)} className="pf-grid-l" vectorEffect="non-scaling-stroke" />)}
          <line x1={0} x2={W} y1={H} y2={H} className="pf-base" vectorEffect="non-scaling-stroke" />
          {vals.map((v, i) => {
            const h = (v / max) * (H - 10)
            const x = i * bw + Math.max(1, bw * 0.15)
            const w = Math.max(2, bw * 0.7)
            return <g key={i} onMouseEnter={() => setHover(i)} onClick={() => setHover(i)}>
              <rect x={i * bw} y={0} width={bw} height={H} fill="transparent" />
              {v > 0 && <rect x={x} y={H - h} width={w} height={h} className={`pf-bar ${hover === i ? 'hi' : ''}`} />}
            </g>
          })}
        </svg>
      </div>
      <div className="pf-xlab">{t.labels.length ? <><span>{fmtDay(t.labels[0])}</span>
        {t.labels.length > 2 && <span>{fmtDay(t.labels[Math.floor(t.labels.length / 2)])}</span>}
        {t.labels.length > 1 && <span>{fmtDay(t.labels[t.labels.length - 1])}</span>}</> : null}</div>
      <p className="pf-tip">{hover !== null
        ? <><b>{n(vals[hover])}</b> {SERIES.find(x => x[0] === s)[1].toLowerCase()} · {t.step_days === 7 ? 'week of ' : ''}{fmtDay(t.labels[hover])}</>
        : <><b>{n(total)}</b> {SERIES.find(x => x[0] === s)[1].toLowerCase()} in the chart · tap a bar for its {t.step_days === 7 ? 'week' : 'day'}</>}</p>
    </div>
  </>
}

const TCOLS = [['families', 'Families', 'families_contacted'], ['attempts', 'Attempts', 'attempts'], ['delivered', 'Delivered', 'delivered'],
  ['failed', 'Failed', 'failed'], ['replies', 'Replies', 'replies'], ['reply_rate', 'Reply rate', null],
  ['offers', 'Offered', 'offers'], ['appointments', 'Appts', 'appointments']]
function TouchTable({ rows, open }) {
  const label = t => (t === '4+' ? '4th+ touch' : `${t}${{ 1: 'st', 2: 'nd', 3: 'rd' }[t]} touch`)
  return <div className="pf-tablewrap"><table className="pf-table">
    <thead><tr><th>Touch</th>{TCOLS.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
    <tbody>{rows.map(r => (
      <tr key={r.touch}><th>{label(r.touch)}</th>
        {TCOLS.map(([k, , metric]) => <td key={k}>{metric
          ? <button onClick={() => open(metric, { touch: r.touch })} className={k === 'failed' && r[k] ? 'bad' : ''}>{n(r[k])}</button>
          : pct(r[k])}</td>)}</tr>))}</tbody>
  </table></div>
}

function LocationTable({ rows, open, setLoc }) {
  const [all, setAll] = useState(false)
  if (!rows.length) return <p className="cmd-muted">No location has families loaded.</p>
  const shown = all ? rows : rows.slice(0, 8)
  return <>
    <div className="pf-tablewrap"><table className="pf-table pf-loc">
      <thead><tr><th>Location</th><th>Loaded</th><th>Contacted</th><th>Attempts</th><th>Delivered</th><th>Replies</th><th>Reply rate</th><th>Appts</th></tr></thead>
      <tbody>{shown.map(r => {
        const f = { location_id: r.location_id }
        return <tr key={r.location_id}>
          <th><button className="pf-locname" onClick={() => setLoc(r.location_id)} title="Show this location only">{r.location}</button></th>
          <td><button onClick={() => open('loaded', f)}>{n(r.loaded)}</button></td>
          <td><button onClick={() => open('families_contacted', f)}>{n(r.families)}</button></td>
          <td><button onClick={() => open('attempts', f)}>{n(r.attempts)}</button></td>
          <td><button onClick={() => open('delivered', f)}>{n(r.delivered)}</button></td>
          <td><button onClick={() => open('replies', f)}>{n(r.replies)}</button></td>
          <td>{pct(r.reply_rate)}</td>
          <td><button onClick={() => open('appointments', f)}>{n(r.appointments)}</button></td>
        </tr>
      })}</tbody>
    </table></div>
    {rows.length > 8 && <button className="pf-more" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${rows.length} locations`}</button>}
  </>
}

function Projection({ p }) {
  return <>
    <p className="pf-note">{p.note}</p>
    <dl className="pf-dl">
      <dt>Families not yet contacted</dt><dd>{n(p.remaining_families)}</dd>
      <dt>Automatic enrollment pace</dt><dd>{p.daily_batch_per_day ? `${p.daily_batch_per_day} / sending day` : 'Off'}</dd>
      <dt>Sending days to reach everyone</dt><dd>{p.sending_days_to_reach_all ?? '— (enrollment off)'}</dd>
      <dt>Reply rate so far</dt><dd>{pct(p.expected_reply_rate_pct)} <small>of {n(p.based_on_families)} families</small></dd>
      <dt>Estimated further replies</dt><dd>{p.expected_additional_repliers === null ? '—' : `≈ ${n(p.expected_additional_repliers)} families`}</dd>
      <dt>Estimated further appointments</dt><dd>{p.expected_additional_appointments === null ? '—' : `≈ ${p.expected_additional_appointments}`}</dd>
    </dl>
  </>
}

/* ── Metric drill-down ────────────────────────────────────────────────────── */
const STATUS = [['', 'Any result'], ['delivered', 'Delivered'], ['opened', 'Opened (email)'], ['failed', 'Failed'], ['pending', 'Awaiting result']]
export function MetricDrill({ spec, range: range0, loc: loc0, onBack, openFamily }) {
  const [range, setRange] = useState(range0)
  const [loc, setLoc] = useState(spec.location_id ?? loc0 ?? '')
  const [touch, setTouch] = useState(spec.touch || '')
  const [channel, setChannel] = useState(spec.channel || '')
  const [status, setStatus] = useState(spec.status || '')
  const [r, setR] = useState(null)
  const [err, setErr] = useState('')
  const [shown, setShown] = useState(50)
  const seq = useRef(0)
  const q = useMemo(() => {
    const p = rangeQuery(range, loc)
    p.set('metric', spec.metric)
    if (touch) p.set('touch', touch)
    if (channel) p.set('channel', channel)
    if (status) p.set('status', status)
    return p.toString()
  }, [range, loc, touch, channel, status, spec.metric])
  useEffect(() => {
    const k = ++seq.current
    setShown(50)
    api.get(`/program/command/analytics/records?${q}`)
      .then(x => { if (k === seq.current) { setR(x); setErr('') } })
      .catch(e => { if (k === seq.current) setErr(e.message || 'Could not load the records.') })
  }, [q])
  useEffect(() => { window.scrollTo(0, 0) }, [])
  const attemptish = ['attempt', 'message'].includes(r?.row_kind)
  return (
    <div className="cmd cmd-wide pf-drill">
      <header className="cmd-fhead">
        <button className="cmd-back" onClick={onBack} aria-label="Back"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M15 5l-7 7 7 7" /></svg></button>
        <div><h1>{r?.label || 'Records'}</h1><p className="cmd-sub">{r?.help}</p></div>
      </header>
      {r && !r.snapshot && <RangeBar range={range} setRange={setRange} />}
      <div className="pf-dfilters">
        <select className="pf-select" value={loc} onChange={e => setLoc(e.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {(r?.locations_all || []).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        {r && !r.snapshot && <>
          <select className="pf-select" value={channel} onChange={e => setChannel(e.target.value)} aria-label="Channel">
            <option value="">Text + email</option><option value="sms">Text</option><option value="email">Email</option>
            {r.row_kind === 'reply' && <option value="voicemail">Voicemail</option>}
          </select>
          <div className="pf-seg small" role="tablist" aria-label="Touch">
            {[['', 'Any touch'], ['1', '1st'], ['2', '2nd'], ['3', '3rd'], ['4+', '4th+']].map(([k, l]) => (
              <button key={k} className={touch === k ? 'on' : ''} onClick={() => setTouch(k)}>{l}</button>))}
          </div>
          {attemptish && <select className="pf-select" value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
            {STATUS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}
        </>}
      </div>
      {err && <p className="cmd-err">{err}</p>}
      {!r ? <p className="cmd-muted">Loading…</p> : <>
        <div className="pf-count"><b>{n(r.count)}</b> {r.count === 1 ? 'record' : 'records'}
          {r.row_kind !== 'family' && <> · {n(r.families)} {r.families === 1 ? 'family' : 'families'}</>}
          <span>{r.snapshot ? 'Right now' : r.window.label}</span></div>
        {!r.items.length ? <p className="cmd-card cmd-muted pad">No records match these filters.</p>
          : <div className="pf-records">{r.items.slice(0, shown).map(i => <Record key={`${i.kind}-${i.id || i.lead_id}`} i={i} open={openFamily} />)}</div>}
        {r.items.length > shown && <button className="pf-more" onClick={() => setShown(shown + 100)}>Show {Math.min(100, r.items.length - shown)} more of {n(r.items.length)}</button>}
        {r.truncated && <p className="cmd-muted small">Showing the newest {n(r.items.length)}. Narrow the filters to see older records.</p>}
      </>}
    </div>
  )
}

function Record({ i, open }) {
  const res = i.result && (RESULT[i.result] || i.result.replace(/_/g, ' '))
  return (
    <article className="cmd-card pf-rec">
      <button className="pf-rec-main" onClick={() => open(i.lead_id, 'journey')}>
        <span className="pf-rec-top"><b>{i.name}</b><small>{fmtWhen(i.at)}</small></span>
        <span className="pf-rec-tags">
          {i.touch ? <em className="tag">Touch {i.touch}</em> : null}
          {i.channel && <em className={`tag ${i.channel}`}>{CHANNEL[i.channel] || i.channel}</em>}
          {res && i.kind !== 'reply' && <em className={`tag r-${i.result}`}>{res}</em>}
          {i.kind === 'family' && i.count ? <em className="tag">{i.count} {i.count_label}</em> : null}
        </span>
        <small className="pf-rec-loc">{i.location || 'Location not set'}</small>
        {i.text && <span className="pf-rec-text">{i.text}</span>}
        {i.error && <span className="pf-rec-err">Reason: {i.error}</span>}
        {i.appt_at && <span className="pf-rec-ok">Appointment {fmtWhen(i.appt_at)}</span>}
        {i.next_touch_at && <small className="pf-rec-loc">Next touch due {fmtWhen(i.next_touch_at)} (sent during sending hours)</small>}
      </button>
      <div className="pf-rec-links">
        <button onClick={() => open(i.lead_id, 'journey')}>Journey ›</button>
        <button onClick={() => open(i.lead_id, 'conversation')}>Conversation ›</button>
        <a href={`/leads/${i.lead_id}`}>Family record ›</a>
      </div>
    </article>
  )
}

/* ── Family journey ───────────────────────────────────────────────────────── */
const KIND = { imported: 'Imported', approved: 'Approved', hold: 'On hold', cadence: 'Schedule', touch: 'Outreach',
  skipped: 'Not sent', message: 'Message', reply: 'Reply', opt_out: 'Opt-out', ai: 'AI', staff: 'Staff', offer: 'Offer', handling: 'Handling', booked: 'Appointment' }
export function Journey({ id }) {
  const [j, setJ] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get(`/program/command/analytics/journey/${id}`).then(setJ).catch(e => setErr(e.message || 'Could not load the journey.'))
  }, [id])
  if (err) return <p className="cmd-err">{err}</p>
  if (!j) return <p className="cmd-muted">Loading the journey…</p>
  return (
    <section className="pf-journey" aria-label="Family journey">
      <div className="pf-jstats">
        <div><span>Stage</span><b>{j.stage}</b></div>
        <div><span>Touches sent</span><b>{j.touches_sent}</b></div>
        <div><span>Replies</span><b>{j.replies}</b></div>
        <div><span>Next touch due</span><b>{j.next_touch_at ? fmtWhen(j.next_touch_at) : '—'}</b></div>
        <div><span>Handling</span><b>{j.handling === 'human' ? 'Human' : 'AI'}</b></div>
      </div>
      <ol className="pf-timeline">
        {[...j.events].reverse().map((e, k) => (
          <li key={k} className={`k-${e.kind} ${e.result === 'failed' ? 'fail' : ''}`}>
            <span className="pf-tdot" aria-hidden="true" />
            <div>
              <p className="pf-thead"><b>{e.title}</b><small>{fmtWhen(e.at)}</small></p>
              <p className="pf-ttags"><em className="tag">{KIND[e.kind] || e.kind}</em>
                {e.channel && <em className={`tag ${e.channel}`}>{CHANNEL[e.channel] || e.channel}</em>}
                {e.result && ['delivered', 'opened', 'failed', 'pending'].includes(e.result) && <em className={`tag r-${e.result}`}>{RESULT[e.result]}</em>}</p>
              {e.detail && <p className="pf-tdetail">{e.detail}</p>}
              {e.error && <p className="pf-rec-err">Reason: {e.error}</p>}
            </div>
          </li>))}
        {!j.events.length && <li><div><p className="cmd-muted">Nothing recorded yet.</p></div></li>}
      </ol>
    </section>
  )
}
