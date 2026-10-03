/**
 * SALES PIPELINE — command center for the acting workspace.
 *
 * ── Sources. Every figure on this page is one of these responses. ─────────
 *   GET  /pipeline/board                  Sales Board tab (pages/pipeline/SalesBoard.jsx):
 *                                     leads by the org's configured stages
 *   GET  /pipeline/summary?days=      KPIs, stage cards, funnel, activity,
 *                                     launch context choices (lead_types)
 *   GET  /pipeline/conversations?paged&limit&offset&stage=   All Conversations
 *   GET  /pipeline/appointments?days=     Appointments (booking links)
 *   GET  /pipeline/flagged                Flagged (needs a human)
 *   GET  /leads/?search&page_size         Launch: lead picker
 *   POST /pipeline/launch                 Launch (explicit, confirmed; the
 *                                         server runs every send gate)
 *   POST /pipeline/approve/{id}           Flagged: approve (and optionally send)
 *   POST /pipeline/dismiss/{id}           Flagged: dismiss without sending
 *
 * A null figure renders "Not yet available". A trend renders only when the
 * server computed one from the previous period; no trend is ever invented.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useTerminology } from '../terminology'
import { useWorkspaceAuthority } from '../auth/workspaceAuthority'
import '../styles/shared.css'
import './Pipeline.css'
import SalesBoard from './pipeline/SalesBoard'
import { asUtc } from './sales/calendarTime'

const NOT_AVAILABLE = 'Not yet available'

// Presentation for the stage keys the server reports; unknown keys still render.
const STAGE_TONE = {
  outreach_sent: 'blue', replied: 'violet', ai_responding: 'amber', flagged: 'red',
  booking_sent: 'orange', booked: 'green', confirmed: 'teal', kept: 'teal', sale: 'gold',
  completed: 'slate', stopped: 'slate', dnc: 'red',
}
// GET /pipeline/flagged returns at most FLAGGED_CAP rows; at the cap the real
// number is higher, so it reads "200+" rather than exactly 200.
const FLAGGED_CAP = 200
const flaggedLabel = n => (n >= FLAGGED_CAP ? `${FLAGGED_CAP}+` : `${n}`)

const FLOW_STAGES = ['outreach_sent', 'replied', 'ai_responding', 'flagged', 'booking_sent', 'booked', 'confirmed', 'kept']
const EXIT_STAGES = ['completed', 'stopped', 'dnc', 'sale']

// Used only if the summary (which carries the vertical's own list) fails.
const FALLBACK_LEAD_TYPES = [
  { value: 'new_inquiry', label: 'New Inquiry' },
  { value: 'referral', label: 'Referral' },
  { value: 'web_lead', label: 'Web Lead' },
  { value: 'general', label: 'General Outreach' },
]

const TONE_OPTIONS = [
  { key: 'cold', label: 'Cold', desc: 'Soft intro, low pressure' },
  { key: 'warm', label: 'Warm', desc: 'Friendly, suggest a meeting' },
  { key: 'hot', label: 'Hot', desc: 'Direct, ask for the appointment' },
  { key: 'urgent', label: 'Urgent', desc: 'Brief, time-sensitive' },
]

const RANGES = [7, 30, 90]

const ICONS = {
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  reply: 'M9 17l-5-5 5-5M4 12h11a5 5 0 0 1 5 5v2',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  alert: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  check: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01l-3-3',
  funnel: 'M22 3H2l8 9.46V19l4 2v-8.54z',
  rocket: 'M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09zM12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
}
function Icon({ name, size = 18 }) {
  return (
    <svg className="pl-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[name] || ''} /></svg>
  )
}

function isNum(v) { return typeof v === 'number' && Number.isFinite(v) }
function fmtNum(v) { return isNum(v) ? v.toLocaleString('en-US') : NOT_AVAILABLE }
function fmtPct(v) { return isNum(v) ? `${v}%` : NOT_AVAILABLE }
function humanize(v) {
  return String(v || '').split(/[_\-\s]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(' ')
}
function ago(iso) {
  if (!iso) return ''
  // API timestamps are naive UTC; read as local they land hours in the future.
  const ms = Date.now() - (asUtc(iso) || new Date(NaN)).getTime()
  if (Number.isNaN(ms)) return ''
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} day${d === 1 ? '' : 's'} ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function errText(e) { return e?.message || 'Request failed' }
function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

function Trend({ pct, points }) {
  const v = isNum(points) ? points : pct
  if (!isNum(v)) return null
  const up = v > 0
  const flat = v === 0
  const unit = isNum(points) ? ' pts' : '%'
  return (
    <span className={`pl-trend ${flat ? '' : up ? 'is-up' : 'is-down'}`}>
      {flat ? '±0' : `${up ? '↑' : '↓'} ${Math.abs(v)}`}{unit} vs previous period
    </span>
  )
}

/* ═════════════════════════════════════════════════════════════════════ */

export default function Pipeline() {
  const navigate = useNavigate()
  const terminology = useTerminology()
  const { isManager } = useWorkspaceAuthority()
  const [searchParams] = useSearchParams()
  const [tab, setTab] = useState(() => searchParams.get('tab') || 'board')
  const [days, setDays] = useState(30)
  const [summary, setSummary] = useState(null)
  const [summaryErr, setSummaryErr] = useState(null)
  const [flagged, setFlagged] = useState([])
  const [reloadKey, setReloadKey] = useState(0)
  const [stageFilter, setStageFilter] = useState('')
  const reload = useCallback(() => setReloadKey(k => k + 1), [])

  useEffect(() => {
    let alive = true
    setSummaryErr(null)
    api.get(`/pipeline/summary?days=${days}`)
      .then(d => { if (alive) setSummary(d || null) })
      .catch(e => { if (alive) { setSummary(null); setSummaryErr(errText(e)) } })
    return () => { alive = false }
  }, [days, reloadKey])

  useEffect(() => {
    api.get('/pipeline/flagged').then(d => setFlagged(Array.isArray(d) ? d : [])).catch(() => setFlagged([]))
  }, [reloadKey])

  // The vertical's own launch contexts plus THIS organization's tiers.
  const audienceTypes = useMemo(() => {
    const base = (summary?.lead_types?.length ? summary.lead_types : FALLBACK_LEAD_TYPES)
    const seen = new Set(base.map(t => t.value))
    return base.concat((terminology.tiers || []).filter(t => t && t.value && !seen.has(t.value))
      .map(t => ({ value: t.value, label: t.label })))
  }, [summary, terminology.tiers])

  const stageLabel = useCallback(key => (summary?.stages || []).find(s => s.key === key)?.label || humanize(key), [summary])

  const openStage = key => { setStageFilter(key); setTab('conversations') }

  const TABS = [
    { key: 'board', label: 'Sales Board' },
    { key: 'overview', label: 'Conversations Overview' },
    { key: 'conversations', label: 'All Conversations' },
    { key: 'appointments', label: 'Appointments' },
    { key: 'flagged', label: `Flagged${flagged.length ? ` (${flaggedLabel(flagged.length)})` : ''}` },
    { key: 'launch', label: 'Launch' },
  ]

  return (
    <div className="pl">
      <header className="pl-header">
        <div className="pl-title">
          <h1>Sales Pipeline</h1>
          <p>Every lead by stage — owner, time in stage, next task, appointment and outcome — plus the AI conversation pipeline.</p>
        </div>
        <div className="pl-header-actions">
          <label className="pl-range">
            <Icon name="calendar" size={16} />
            <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Date range">
              {RANGES.map(d => <option key={d} value={d}>Last {d} days</option>)}
            </select>
          </label>
          <button type="button" className="pl-btn pl-btn--primary" onClick={() => setTab('launch')}>
            <Icon name="rocket" size={16} /> Launch Pipeline
          </button>
        </div>
      </header>

      <nav className="pl-tabs" role="tablist">
        {TABS.map(t => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`pl-tab${tab === t.key ? ' is-active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </nav>

      {tab === 'board' && <SalesBoard />}
      {tab === 'overview' && (
        <Overview summary={summary} err={summaryErr} days={days} flaggedCount={flagged.length}
          onStage={openStage} onTab={setTab} navigate={navigate} isManager={isManager} />
      )}
      {tab === 'conversations' && (
        <Conversations stageFilter={stageFilter} setStageFilter={setStageFilter} stages={summary?.stages || []}
          stageLabel={stageLabel} navigate={navigate} reloadKey={reloadKey} />
      )}
      {tab === 'appointments' && <Appointments days={days} navigate={navigate} />}
      {tab === 'flagged' && <Flagged flagged={flagged} reload={reload} />}
      {tab === 'launch' && <Launch audienceTypes={audienceTypes} onLaunched={reload} />}
    </div>
  )
}

/* ── overview ─────────────────────────────────────────────────────────── */

function Overview({ summary, err, days, flaggedCount, onStage, onTab, navigate, isManager }) {
  if (err) {
    return <div className="pl-banner pl-banner--error">Pipeline summary could not be loaded: {err}</div>
  }
  if (!summary) return <div className="pl-panel pl-muted">Loading pipeline…</div>
  const k = summary.kpis || {}
  const cards = [
    { key: 'active', label: 'Active Conversations', icon: 'chat', tone: 'blue', value: fmtNum(k.active_conversations?.value), raw: k.active_conversations?.value, onClick: () => onTab('conversations') },
    { key: 'reply', label: 'Reply Rate', icon: 'reply', tone: 'violet', value: fmtPct(k.reply_rate?.value), raw: k.reply_rate?.value, trendPoints: k.reply_rate?.trend_points, sub: `Conversations started in the last ${days} days` },
    { key: 'await', label: 'Awaiting Booking', icon: 'clock', tone: 'orange', value: fmtNum(k.awaiting_booking?.value), raw: k.awaiting_booking?.value, onClick: () => onStage('booking_sent') },
    { key: 'human', label: 'Needs Human', icon: 'alert', tone: 'red', value: fmtNum(k.needs_human?.value), raw: k.needs_human?.value, onClick: () => onTab('flagged') },
    { key: 'confirmed', label: 'Confirmed Appointments', icon: 'check', tone: 'green', value: fmtNum(k.confirmed_appointments?.value), raw: k.confirmed_appointments?.value, trendPct: k.confirmed_appointments?.trend_pct, sub: isNum(k.upcoming_appointments?.value) ? `${k.upcoming_appointments.value} upcoming` : null, onClick: () => onTab('appointments') },
    { key: 'conv', label: 'Conversion Rate', icon: 'funnel', tone: 'teal', value: fmtPct(k.conversion_rate?.value), raw: k.conversion_rate?.value, trendPoints: k.conversion_rate?.trend_points, sub: 'Started → booked' },
  ]
  const stages = summary.stages || []
  const flow = FLOW_STAGES.map(key => stages.find(s => s.key === key)).filter(Boolean)
  const extras = stages.filter(s => !FLOW_STAGES.includes(s.key) && !EXIT_STAGES.includes(s.key))
  const exits = EXIT_STAGES.map(key => stages.find(s => s.key === key)).filter(s => s && (s.tracked || s.count))
  const funnel = (summary.funnel || [])
  const empty = summary.total_conversations === 0

  return (
    <div className="pl-overview">
      <section className="pl-kpis" aria-label="Pipeline KPIs">
        {cards.map(c => {
          const Tag = c.onClick ? 'button' : 'div'
          return (
            <Tag key={c.key} type={c.onClick ? 'button' : undefined} className="pl-kpi" onClick={c.onClick}>
              <span className={`pl-kpi-icon pl-tone-${c.tone}`}><Icon name={c.icon} /></span>
              <span className="pl-kpi-text">
                <span className="pl-kpi-label">{c.label}</span>
                <span className={`pl-kpi-value${isNum(c.raw) ? '' : ' is-na'}`}>{c.value}</span>
                <Trend pct={c.trendPct} points={c.trendPoints} />
                {c.sub && <span className="pl-kpi-sub">{c.sub}</span>}
              </span>
            </Tag>
          )
        })}
      </section>

      {empty && (
        <div className="pl-banner">
          No pipeline conversations in this workspace yet. Launch a pipeline to start one — every message still passes consent, DNC, quiet-hours and suppression checks.
          <button type="button" className="pl-btn pl-btn--outline" onClick={() => onTab('launch')}>Go to Launch</button>
        </div>
      )}

      <div className="pl-grid">
        <section className="pl-panel pl-stages-panel">
          <div className="pl-panel-head">
            <div>
              <h2>Pipeline Stages</h2>
              <p className="pl-muted">Where every conversation sits right now. Select a stage to see its conversations.</p>
            </div>
          </div>
          <div className="pl-stages">
            {[...flow, ...extras].map((s, i) => (
              <button key={s.key} type="button" className={`pl-stage pl-stage--${STAGE_TONE[s.key] || 'slate'}${s.tracked ? '' : ' is-untracked'}`}
                onClick={() => s.tracked && onStage(s.key)} disabled={!s.tracked}
                title={s.tracked ? `Show ${s.label} conversations` : (s.note || 'Not recorded by the pipeline yet')}>
                <span className="pl-stage-head"><span className="pl-stage-num">{i + 1}</span>{s.label}</span>
                <span className={`pl-stage-count${isNum(s.count) ? '' : ' is-na'}`}>{isNum(s.count) ? s.count.toLocaleString('en-US') : NOT_AVAILABLE}</span>
                {s.tracked && isNum(s.avg_age_days) && <span className="pl-stage-meta">avg {s.avg_age_days}d in stage</span>}
                {s.tracked && Object.keys(s.by_channel || {}).length > 0 && (
                  <span className="pl-stage-channels">
                    {Object.entries(s.by_channel).map(([ch, n]) => <span key={ch}><span>{humanize(ch)}</span><strong>{n}</strong></span>)}
                  </span>
                )}
                {!s.tracked && <span className="pl-stage-meta">{s.note || 'Not recorded yet'}</span>}
              </button>
            ))}
          </div>
          {exits.length > 0 && (
            <div className="pl-exits">
              {exits.map(s => (
                <button key={s.key} type="button" className="pl-exit" onClick={() => onStage(s.key)}>
                  {s.label} <strong>{fmtNum(s.count)}</strong>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="pl-panel pl-actions-panel">
          <div className="pl-panel-head"><h2>Quick Actions</h2></div>
          <div className="pl-quick">
            <button type="button" className="pl-btn pl-btn--primary" onClick={() => onTab('launch')}><Icon name="rocket" size={16} /> Launch New Pipeline</button>
            {isManager && <button type="button" className="pl-btn" onClick={() => navigate('/imports/new')}><Icon name="upload" size={16} /> Import Leads</button>}
            <button type="button" className="pl-btn" onClick={() => onTab('conversations')}><Icon name="list" size={16} /> View All Conversations</button>
            <button type="button" className="pl-btn" onClick={() => onTab('flagged')}><Icon name="alert" size={16} /> Review Flagged{flaggedCount ? ` (${flaggedLabel(flaggedCount)})` : ''}</button>
            {isManager && <button type="button" className="pl-btn" onClick={() => navigate('/templates')}><Icon name="file" size={16} /> Manage Templates</button>}
          </div>
        </section>
      </div>

      <div className="pl-grid pl-grid--lower">
        <section className="pl-panel">
          <div className="pl-panel-head"><h2>Recent Activity</h2></div>
          {(summary.recent_activity || []).length === 0 ? (
            <p className="pl-muted">No recorded pipeline activity yet.</p>
          ) : (
            <ul className="pl-activity">
              {summary.recent_activity.map((a, i) => (
                <li key={i}>
                  <button type="button" className="pl-activity-row" disabled={!a.lead_id}
                    onClick={() => a.lead_id && navigate(`/leads/${a.lead_id}`)}>
                    <span className={`pl-activity-icon ${a.kind === 'reply' ? 'pl-tone-violet' : 'pl-tone-blue'}`}>
                      <Icon name={a.kind === 'reply' ? 'reply' : 'check'} size={16} />
                    </span>
                    <span className="pl-activity-text">
                      <strong>{a.title}</strong>
                      <span className="pl-muted">{[a.lead_name, a.detail].filter(Boolean).join(' · ') || '—'}</span>
                    </span>
                    <span className="pl-activity-time pl-muted">{ago(a.at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="pl-panel">
          <div className="pl-panel-head">
            <div>
              <h2>Pipeline Conversion</h2>
              <p className="pl-muted">Conversations started in the last {days} days, and how far they got.</p>
            </div>
          </div>
          {funnel.length === 0 || !isNum(funnel[0]?.count) || funnel[0].count === 0 ? (
            <p className="pl-muted">No conversations started in this period.</p>
          ) : (
            <ol className="pl-funnel">
              {funnel.map((f, i) => (
                <li key={f.key}>
                  <span className="pl-funnel-label">{f.label}</span>
                  <span className="pl-funnel-bar">
                    <span className={`pl-funnel-fill pl-funnel-fill--${i}`} style={{ width: isNum(f.pct_of_started) ? `${Math.max(2, f.pct_of_started)}%` : '0%' }} />
                  </span>
                  <span className="pl-funnel-num">
                    {isNum(f.count) ? <>{f.count.toLocaleString('en-US')} <span className="pl-muted">({f.pct_of_started}%)</span></> : <span className="pl-muted">{NOT_AVAILABLE}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      <p className="pl-note">
        Stage counts are each conversation's current stage. Rates and trends compare conversations started in this period with the previous {days} days. “{NOT_AVAILABLE}” means the platform does not record that figure yet.
      </p>
    </div>
  )
}

/* ── all conversations ────────────────────────────────────────────────── */

function Conversations({ stageFilter, setStageFilter, stages, stageLabel, navigate, reloadKey }) {
  const PAGE = 50
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  useEffect(() => { setPage(1) }, [stageFilter])
  useEffect(() => {
    let alive = true
    setLoading(true)
    setErr(null)
    const p = new URLSearchParams({ paged: 'true', limit: String(PAGE), offset: String((page - 1) * PAGE) })
    if (stageFilter) p.set('stage', stageFilter)
    api.get(`/pipeline/conversations?${p.toString()}`)
      .then(d => {
        if (!alive) return
        const items = Array.isArray(d) ? d : (d?.items || [])
        setRows(items)
        setTotal(Array.isArray(d) ? items.length : (d?.total ?? items.length))
      })
      .catch(e => { if (alive) { setRows([]); setTotal(0); setErr(errText(e)) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [stageFilter, page, reloadKey])
  const pages = Math.max(1, Math.ceil(total / PAGE))

  return (
    <section className="pl-panel">
      <div className="pl-panel-head">
        <h2>All conversations</h2>
        <div className="pl-inline">
          <select className="pl-select" value={stageFilter} onChange={e => setStageFilter(e.target.value)} aria-label="Stage">
            <option value="">All stages</option>
            {stages.filter(s => s.tracked).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <span className="pl-count">{total.toLocaleString('en-US')}</span>
        </div>
      </div>
      {err && <div className="pl-banner pl-banner--error">{err}</div>}
      {loading && rows.length === 0 ? <p className="pl-muted">Loading…</p> : rows.length === 0 ? (
        <p className="pl-muted">No conversations{stageFilter ? ` at ${stageLabel(stageFilter)}` : ''}. Launch a pipeline to start one.</p>
      ) : (
        <div className="pl-tablewrap">
          <table className="pl-table">
            <thead><tr><th>Lead</th><th>Stage</th><th>Context</th><th>Channel</th><th>Sent</th><th>Replies</th><th>AI sent</th><th>Last activity</th></tr></thead>
            <tbody>
              {rows.map(c => (
                <tr key={c.pipeline_id} tabIndex={0} onClick={() => navigate(`/leads/${c.lead_id}`)}
                  onKeyDown={e => { if (e.key === 'Enter') navigate(`/leads/${c.lead_id}`) }}>
                  <td><div className="pl-strong">{c.lead_name}</div><div className="pl-muted pl-small">{c.lead_phone || ''}</div></td>
                  <td><span className={`pl-chip pl-chip--${STAGE_TONE[c.stage] || 'slate'}`}>{stageLabel(c.stage)}</span>{c.flagged && <span className="pl-chip pl-chip--red">Flagged</span>}</td>
                  <td className="pl-small">{c.lead_type ? humanize(c.lead_type) : '—'}</td>
                  <td className="pl-small">{humanize(c.channel) || '—'}</td>
                  <td>{c.messages_sent ?? 0}</td>
                  <td>{c.replies_received ?? 0}</td>
                  <td>{c.ai_responses_sent ?? 0}</td>
                  <td className="pl-small pl-muted">{ago(c.last_inbound_at || c.last_outbound_at) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > 0 && (
        <div className="pl-pager">
          <span className="pl-muted">Showing {(page - 1) * PAGE + 1}–{Math.min(page * PAGE, total)} of {total.toLocaleString('en-US')}</span>
          <div className="pl-inline">
            <button type="button" className="pl-btn" disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}>Previous</button>
            <span className="pl-small pl-muted">Page {page} of {pages}</span>
            <button type="button" className="pl-btn" disabled={page >= pages || loading} onClick={() => setPage(p => p + 1)}>Next</button>
          </div>
        </div>
      )}
    </section>
  )
}

/* ── appointments ─────────────────────────────────────────────────────── */

function Appointments({ days, navigate }) {
  const [rows, setRows] = useState(null)
  const [totals, setTotals] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let alive = true
    setErr(null)
    api.get(`/pipeline/appointments?days=${days}`)
      .then(d => { if (alive) { setRows(d?.items || []); setTotals(d?.totals || null) } })
      .catch(e => { if (alive) { setRows([]); setErr(errText(e)) } })
    return () => { alive = false }
  }, [days])
  const upcoming = (rows || []).filter(r => r.upcoming)
  const past = (rows || []).filter(r => !r.upcoming).reverse()
  // The server caps each list; its totals are the real counts.
  const upTotal = totals?.upcoming ?? upcoming.length
  const pastTotal = totals?.past ?? past.length
  const shown = (n, total) => (total > n ? `${n} of ${total}` : `${total}`)
  const table = list => (
    <div className="pl-tablewrap">
      <table className="pl-table">
        <thead><tr><th>When</th><th>Lead</th><th>Type</th><th>Status</th><th>Advisor</th></tr></thead>
        <tbody>
          {list.map(a => (
            <tr key={a.id} tabIndex={0} onClick={() => navigate(`/leads/${a.lead_id}`)}
              onKeyDown={e => { if (e.key === 'Enter') navigate(`/leads/${a.lead_id}`) }}>
              <td className="pl-nowrap">{fmtDateTime(a.booked_time)}</td>
              <td className="pl-strong">{a.lead_name || '—'}</td>
              <td className="pl-small">{a.appointment_type || '—'}</td>
              <td><span className={`pl-chip pl-chip--${a.status === 'confirmed' ? 'green' : a.status === 'cancelled' ? 'slate' : 'blue'}`}>{humanize(a.status)}</span></td>
              <td className="pl-small">{a.advisor_name || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  return (
    <section className="pl-panel">
      <div className="pl-panel-head"><h2>Appointments</h2><span className="pl-count">{upTotal + pastTotal}</span></div>
      {err && <div className="pl-banner pl-banner--error">{err}</div>}
      {rows === null ? <p className="pl-muted">Loading…</p> : rows.length === 0 ? (
        <p className="pl-muted">No booked appointments upcoming or in the last {days} days.</p>
      ) : (
        <>
          <h3 className="pl-h3">Upcoming ({shown(upcoming.length, upTotal)})</h3>
          {upcoming.length ? table(upcoming) : <p className="pl-muted">Nothing upcoming.</p>}
          <h3 className="pl-h3">Last {days} days ({shown(past.length, pastTotal)})</h3>
          {past.length ? table(past) : <p className="pl-muted">None in this period.</p>}
        </>
      )}
    </section>
  )
}

/* ── flagged (unchanged behaviour) ────────────────────────────────────── */

function Flagged({ flagged, reload }) {
  const [editing, setEditing] = useState(null)
  const [edited, setEdited] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function approve(id, message, send) {
    setBusy(true)
    setErr(null)
    try {
      await api.post(`/pipeline/approve/${id}`, { pipeline_id: id, message, send })
      setEditing(null)
      reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function dismiss(id) {
    setErr(null)
    try { await api.post(`/pipeline/dismiss/${id}`, {}); reload() } catch (e) { setErr(errText(e)) }
  }

  return (
    <section className="pl-panel">
      <div className="pl-panel-head">
        <h2>Flagged conversations — needs your review</h2>
        <span className="pl-count">{flaggedLabel(flagged.length)}</span>
      </div>
      {err && <div className="pl-banner pl-banner--error">{err}</div>}
      {flagged.length === 0 ? <p className="pl-muted">No flagged conversations right now.</p> : (
        <div className="pl-flagged">
          {flagged.map(item => (
            <article key={item.pipeline_id} className="pl-flag">
              <div className="pl-flag-head">
                <div>
                  <div className="pl-strong">{item.lead_name}</div>
                  <div className="pl-muted pl-small">{[item.lead_tier && humanize(item.lead_tier), item.lead_phone, `${item.messages_sent ?? 0} sent, ${item.replies_received ?? 0} replied`].filter(Boolean).join(' · ')}</div>
                </div>
                {item.flag_reason && <span className="pl-chip pl-chip--red">{item.flag_reason}</span>}
              </div>
              <div className="pl-small pl-muted">Their reply</div>
              <blockquote className="pl-quote">{item.flagged_reply || '—'}</blockquote>
              {editing === item.pipeline_id ? (
                <>
                  <div className="pl-small pl-muted">Edit response before sending</div>
                  <textarea className="pl-textarea" rows={3} value={edited} onChange={e => setEdited(e.target.value)} />
                  <div className="pl-inline">
                    <button type="button" className="pl-btn pl-btn--primary" disabled={busy || !edited.trim()} onClick={() => approve(item.pipeline_id, edited, true)}>{busy ? 'Sending…' : 'Send this response'}</button>
                    <button type="button" className="pl-btn" disabled={busy} onClick={() => approve(item.pipeline_id, edited, false)}>Save without sending</button>
                    <button type="button" className="pl-btn pl-btn--ghost" onClick={() => setEditing(null)}>Cancel</button>
                  </div>
                </>
              ) : (
                <>
                  <div className="pl-small pl-muted">AI suggested response</div>
                  <blockquote className="pl-quote pl-quote--ai">{item.suggested_response || '—'}</blockquote>
                  <div className="pl-inline">
                    <button type="button" className="pl-btn pl-btn--primary" disabled={busy || !item.suggested_response} onClick={() => approve(item.pipeline_id, item.suggested_response, true)}>Approve &amp; send</button>
                    <button type="button" className="pl-btn" onClick={() => { setEditing(item.pipeline_id); setEdited(item.suggested_response || '') }}>Edit before sending</button>
                    <button type="button" className="pl-btn pl-btn--ghost" onClick={() => dismiss(item.pipeline_id)}>Dismiss — I'll handle it</button>
                  </div>
                </>
              )}
            </article>
          ))}
        </div>
      )}
      <p className="pl-note">Sending goes through the same consent, DNC/STOP, quiet-hours and suppression checks as every other message.</p>
    </section>
  )
}

/* ── launch (existing flow and gates) ─────────────────────────────────── */

function Launch({ audienceTypes, onLaunched }) {
  const [leads, setLeads] = useState([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const q = useDebounced(search)
  const [selected, setSelected] = useState(new Set())
  const [leadType, setLeadType] = useState('general')
  const [tone, setTone] = useState('warm')
  const [aiDirection, setAiDirection] = useState('')
  const [channel, setChannel] = useState('sms')
  const [autoRespond, setAutoRespond] = useState(true)
  const [launching, setLaunching] = useState(false)
  const [result, setResult] = useState(null)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    if (!audienceTypes.some(t => t.value === leadType) && audienceTypes.length) setLeadType(audienceTypes[0].value)
  }, [audienceTypes, leadType])

  useEffect(() => {
    let alive = true
    const p = new URLSearchParams({ page: '1', page_size: '200' })
    if (q.trim()) p.set('search', q.trim().slice(0, 120))
    api.get(`/leads/?${p.toString()}`)
      .then(d => {
        if (!alive) return
        const items = Array.isArray(d) ? d : (d?.items || [])
        setLeads(items)
        setTotal(Array.isArray(d) ? items.length : (d?.total ?? items.length))
      })
      .catch(() => { if (alive) { setLeads([]); setTotal(0) } })
    return () => { alive = false }
  }, [q])

  const eligible = leads.filter(l => l.status !== 'dnc' && !l.is_duplicate)

  async function launch() {
    setConfirming(false)
    setLaunching(true)
    setResult(null)
    try {
      const r = await api.post('/pipeline/launch', {
        lead_ids: Array.from(selected), lead_type: leadType, tone, ai_direction: aiDirection, channel, auto_respond: autoRespond,
      })
      setResult(r)
      setSelected(new Set())
      onLaunched()
    } catch (e) { setResult({ error: errText(e) }) } finally { setLaunching(false) }
  }

  return (
    <div className="pl-launch">
      <section className="pl-panel">
        <div className="pl-panel-head"><h2>Launch AI pipeline</h2></div>
        <div className="pl-form">
          <label>Launch context
            <select className="pl-select" value={leadType} onChange={e => setLeadType(e.target.value)}>
              {audienceTypes.map(lt => <option key={lt.value} value={lt.value}>{lt.label}</option>)}
            </select>
            <span className="pl-help">Tells the AI what kind of conversation this is</span>
          </label>
          <label>Channel
            <select className="pl-select" value={channel} onChange={e => setChannel(e.target.value)}>
              <option value="sms">SMS text message</option>
              <option value="email">Email</option>
              <option value="both">Both — SMS + Email</option>
            </select>
          </label>
          <div className="pl-field">Tone
            <div className="pl-tones">
              {TONE_OPTIONS.map(t => (
                <button key={t.key} type="button" className={`pl-tone${tone === t.key ? ' is-active' : ''}`} onClick={() => setTone(t.key)} title={t.desc}>{t.label}</button>
              ))}
            </div>
          </div>
          <label>AI direction
            <input className="pl-input" value={aiDirection} onChange={e => setAiDirection(e.target.value)} maxLength={500}
              placeholder="e.g. Ask whether they'd like a comparison of fixed-rate options" />
          </label>
        </div>
        <label className="pl-check">
          <input type="checkbox" checked={autoRespond} onChange={e => setAutoRespond(e.target.checked)} />
          <span>Auto-respond when confidence ≥ 85% — flag for review when below</span>
        </label>
      </section>

      <section className="pl-panel">
        <div className="pl-panel-head">
          <h2>Select leads</h2>
          <div className="pl-inline">
            <label className="pl-search"><Icon name="search" size={16} />
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name, phone, email" aria-label="Search leads" />
            </label>
            <span className="pl-count">{selected.size} selected</span>
          </div>
        </div>
        <div className="pl-tablewrap pl-picker">
          <table className="pl-table">
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="Select all shown"
                  checked={eligible.length > 0 && eligible.every(l => selected.has(l.id))}
                  onChange={e => {
                    const next = new Set(selected)
                    eligible.forEach(l => (e.target.checked ? next.add(l.id) : next.delete(l.id)))
                    setSelected(next)
                  }} /></th>
                <th>Name</th><th>Phone</th><th>Stage</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {eligible.length === 0 ? (
                <tr><td colSpan="5" className="pl-muted">No leads available to launch{q ? ' for this search' : ''}.</td></tr>
              ) : eligible.map(l => (
                <tr key={l.id}>
                  <td><input type="checkbox" checked={selected.has(l.id)} aria-label={`Select ${l.first_name || ''} ${l.last_name || ''}`}
                    onChange={e => { const next = new Set(selected); e.target.checked ? next.add(l.id) : next.delete(l.id); setSelected(next) }} /></td>
                  <td className="pl-strong">{`${l.first_name || ''} ${l.last_name || ''}`.trim() || '—'}</td>
                  <td className="pl-small">{l.phone || '—'}</td>
                  <td className="pl-small">{l.tier ? humanize(l.tier) : '—'}</td>
                  <td className="pl-small">{humanize(l.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {total > leads.length && <p className="pl-note">Showing {leads.length} of {total.toLocaleString('en-US')} leads — search to narrow.</p>}
        <div className="pl-inline pl-launch-foot">
          <button type="button" className="pl-btn pl-btn--primary" disabled={launching || selected.size === 0} onClick={() => setConfirming(true)}>
            <Icon name="rocket" size={16} /> {launching ? 'Launching…' : `Launch pipeline for ${selected.size} lead${selected.size === 1 ? '' : 's'}`}
          </button>
          {result && (
            <span className={result.error ? 'pl-error' : 'pl-ok'}>
              {result.error || `Launched ${result.launched ?? 0} · Skipped ${result.skipped ?? 0} · Errors ${result.errors ?? 0}`}
            </span>
          )}
        </div>
        {confirming && (
          <div className="pl-confirm" role="alertdialog" aria-label="Confirm launch">
            <p><strong>Start outreach to {selected.size} lead{selected.size === 1 ? '' : 's'}?</strong> The first message goes out by {channel === 'both' ? 'SMS and email' : channel.toUpperCase()}. DNC, duplicate and over-capacity leads are skipped, and every message still passes the server's consent, STOP, quiet-hours and suppression checks.</p>
            <div className="pl-inline">
              <button type="button" className="pl-btn pl-btn--primary" onClick={launch}>Yes, launch</button>
              <button type="button" className="pl-btn" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
