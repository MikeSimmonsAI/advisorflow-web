/**
 * CUSTOMER WORKSPACE — OVERVIEW.
 *
 * Visual target: the approved customer app redesign (Aug 27 2026). This is not
 * a prettier dashboard; it is meant to answer one question on arrival —
 * WHAT NEEDS TO HAPPEN NEXT — and to be one click from doing it.
 *
 * ── Sources. Every number on this page names one. ──────────────────────────
 *   GET /leads/?page=1&page_size=1            total lead count (envelope.total)
 *   GET /leads/?status=dnc&page_size=1        suppression count
 *   GET /leads/status-funnel                  new · sent · replied · hot · booked
 *   GET /leads/daily-briefing                 callbacks, imports, bookings
 *   GET /sms/replies?needs_attention=true     the hot reply queue itself
 *   GET /leads/?page=1&page_size=40           the "needs action" table
 *   GET /activity/sent?limit=8&days=7         recent outbound activity
 *   GET /outcomes/summary                     recorded outcomes / sales
 *   GET /pipeline/forecast                    AI alerts, folded into the queue
 *   GET /admin/dashboard/metrics              team performance  (admins only)
 *
 * ── Two rules ─────────────────────────────────────────────────────────────
 * 1. NO METRIC WITHOUT A SOURCE. The approved mockup shows "avg first touch"
 *    and a "no contact" pipeline stage. Neither exists in this schema, so
 *    neither is here. A number invented to fill a tile is worse than a gap.
 * 2. EVERY TILE GOES SOMEWHERE. A KPI that cannot be drilled into is a
 *    decorative number block. Each one below carries a route, and the Leads
 *    page reads those filters out of the URL.
 *
 * DNC is fetched as its own count rather than read off the funnel: the funnel
 * endpoint returns five stages and `dnc` is not one of them, so the previous
 * version of this page read funnelCount('dnc') and displayed 0 forever.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getBranding, getWorkspaceContext } from '../api/client'
import { useObservationMode } from '../context/ObservationContext'
import './Overview.css'

// ── Industry-aware labels ─────────────────────────────────────────────────────
const INDUSTRY_LABELS = {
  funeral:      { appointments: 'Arrangements', bookingRate: 'Arrangement rate', bookedSub: 'Booked arrangements', projectedBookings: 'Projected arrangements', confirmLabel: 'arrangements confirmed', weeklyLabel: 'arrangements this week', recordedVisits: 'Recorded visits' },
  fiber:        { appointments: 'Installs',     bookingRate: 'Install rate',     bookedSub: 'Scheduled installs',  projectedBookings: 'Projected installs',     confirmLabel: 'installs confirmed',     weeklyLabel: 'installs this week',     recordedVisits: 'Completed installs' },
  solar:        { appointments: 'Assessments',  bookingRate: 'Assessment rate',  bookedSub: 'Scheduled assessments', projectedBookings: 'Projected assessments', confirmLabel: 'assessments confirmed', weeklyLabel: 'assessments this week', recordedVisits: 'Completed assessments' },
  roofing:      { appointments: 'Inspections',  bookingRate: 'Inspection rate',  bookedSub: 'Scheduled inspections', projectedBookings: 'Projected inspections', confirmLabel: 'inspections confirmed', weeklyLabel: 'inspections this week', recordedVisits: 'Completed inspections' },
  insurance:    { appointments: 'Consultations', bookingRate: 'Consultation rate', bookedSub: 'Booked consultations', projectedBookings: 'Projected consultations', confirmLabel: 'consultations confirmed', weeklyLabel: 'consultations this week', recordedVisits: 'Completed consultations' },
  real_estate:  { appointments: 'Showings',     bookingRate: 'Showing rate',     bookedSub: 'Scheduled showings',  projectedBookings: 'Projected showings',    confirmLabel: 'showings confirmed',     weeklyLabel: 'showings this week',     recordedVisits: 'Completed showings' },
  home_services:{ appointments: 'Appointments', bookingRate: 'Booking rate',     bookedSub: 'Booked appointments', projectedBookings: 'Projected bookings',    confirmLabel: 'appointments confirmed', weeklyLabel: 'bookings this week',     recordedVisits: 'Completed appointments' },
  sales:        { appointments: 'Demos',        bookingRate: 'Demo rate',        bookedSub: 'Scheduled demos',     projectedBookings: 'Projected demos',       confirmLabel: 'demos confirmed',        weeklyLabel: 'demos this week',        recordedVisits: 'Completed demos' },
}
const DEFAULT_LABELS = INDUSTRY_LABELS.funeral

const STAGE_TONE = {
  new: 'var(--signal-amber)', sent: 'var(--signal-blue)', replied: 'var(--signal-blue)',
  hot: 'var(--signal-red)', booked: 'var(--signal-green)',
}

const STATUS_PILL = {
  new: 'gold', sent: 'blue', replied: 'blue', hot: 'red',
  booked: 'teal', dnc: 'off',
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '—'
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase()
}

function ago(iso) {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return null
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return m + 'm'
  const h = Math.floor(m / 60)
  if (h < 24) return h + 'h'
  const d = Math.floor(h / 24)
  return d + 'd'
}

function num(n) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US')
}

export default function Overview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const observationMode = useObservationMode()

  const branding = getBranding()
  const enabledFeatures = branding?.enabled_features ?? null
  const isEnabled = (key) => !enabledFeatures || enabledFeatures.includes(key)
  const industry = branding?.industry || 'funeral'
  const IL = INDUSTRY_LABELS[industry] || DEFAULT_LABELS

  // Team performance reads an admin endpoint. An advisor asking for it gets a
  // 403, so it is not requested for them and the panel says why rather than
  // rendering an empty box that looks like "your team did nothing".
  const isManager = ['org_admin', 'super_admin', 'god_admin'].includes(user?.role)

  const [totalLeads, setTotalLeads] = useState(null)
  const [dncCount, setDncCount] = useState(null)
  const [funnel, setFunnel] = useState([])
  const [briefing, setBriefing] = useState(null)
  const [replies, setReplies] = useState([])
  const [recentLeads, setRecentLeads] = useState([])
  const [activity, setActivity] = useState([])
  const [outcomes, setOutcomes] = useState(null)
  const [forecast, setForecast] = useState(null)
  const [team, setTeam] = useState(null)
  const [teamError, setTeamError] = useState('')
  const [loading, setLoading] = useState(true)
  const [now, setNow] = useState(new Date())
  const [query, setQuery] = useState('')

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  // A REFUSED REQUEST IS NOT AN EMPTY PIPELINE.
  //
  // Every call below used to end in `.catch(() => null)` or `.catch(() => [])`,
  // which turned "the server refused me" into "you have no leads" - silently,
  // and in two different shapes. A failed `/leads/` left totalLeads null and
  // rendered Total leads as an em-dash; a failed status-funnel left an empty
  // array and rendered every stage as 0. That is exactly the screen an advisor
  // reported: one widget blank, the rest confidently zero, and nothing anywhere
  // saying a request had failed.
  //
  // The rule this restores is the one already applied to CONNECTED/HEALTHY
  // elsewhere, pointed the other way: do not display "0 leads" unless the
  // backend actually said 0.
  const [loadError, setLoadError] = useState('')

  // THE WORKSPACE AND THE IDENTITY ARE DEPENDENCIES.
  //
  // The dependency array was [isManager]. For an ADVISOR that value is false
  // before the user hydrates and false after, so the effect never re-ran: a
  // first burst that failed stayed failed until a full page reload. For a
  // manager it flips false->true and silently refetches, which is why this
  // reproduced for advisors and not for admins looking at the same build.
  //
  // Switching workspace does not remount this component either - React
  // reconciles the same element in the same position - so without the
  // workspace in the deps the dashboard kept showing the previous workspace's
  // numbers after a switch.
  const identityKey = `${user?.role || ''}|${user?.organization_id || ''}`
  const workspaceKey = getWorkspaceContext() || ''

  useEffect(() => {
    let live = true
    const failures = []
    // Records WHY a call failed instead of discarding it. The fallback value
    // keeps the existing render shape; the recorded message is what makes the
    // failure visible.
    const attempt = (label, promise, fallback) =>
      promise.catch(e => {
        failures.push(`${label}: ${e?.message || 'request failed'}`)
        return fallback
      })

    const calls = [
      attempt('leads', api.get('/leads/?page=1&page_size=1'), null),
      attempt('suppression', api.get('/leads/?status=dnc&page=1&page_size=1'), null),
      attempt('status funnel', api.get('/leads/status-funnel'), []),
      attempt('daily briefing', api.get('/leads/daily-briefing'), null),
      attempt('replies', api.get('/sms/replies?needs_attention=true'), []),
      attempt('lead list', api.get('/leads/?page=1&page_size=40'), null),
      attempt('activity', api.get('/activity/sent?limit=8&days=7'), []),
      attempt('outcomes', api.get('/outcomes/summary'), null),
      attempt('forecast', api.get('/pipeline/forecast'), null),
      isManager
        ? api.get('/admin/dashboard/metrics').catch(e => ({ __err: e?.message || 'unavailable' }))
        : Promise.resolve(null),
    ]
    Promise.all(calls).then(([t, dnc, fn, br, rp, rl, ac, oc, fc, tm]) => {
      if (!live) return
      setTotalLeads(t?.total ?? null)
      setDncCount(dnc?.total ?? null)
      setFunnel(Array.isArray(fn) ? fn : [])
      setBriefing(br)
      setReplies(Array.isArray(rp) ? rp : [])
      setRecentLeads(Array.isArray(rl?.items) ? rl.items : [])
      setActivity(Array.isArray(ac) ? ac : [])
      setOutcomes(oc)
      setForecast(fc)
      if (tm && tm.__err) { setTeamError(tm.__err); setTeam(null) } else { setTeam(tm) }
      setLoadError(failures.join(' · '))
      setLoading(false)
    })
    return () => { live = false }
  }, [isManager, identityKey, workspaceKey])

  // ── derived ───────────────────────────────────────────────────────────────
  const stage = (s) => funnel.find(x => x.status === s)?.count ?? 0
  const newLeads = stage('new')
  const sentLeads = stage('sent')
  const bookedLeads = stage('booked')
  const hotReplies = replies.length
  const replyRate = sentLeads > 0 ? Math.round((hotReplies / sentLeads) * 100) : null
  const bookingRate = sentLeads > 0 ? Math.round((bookedLeads / sentLeads) * 100) : null

  const advisorNames = useMemo(() => {
    const m = {}
    ;(team?.advisors || []).forEach(a => { m[a.advisor_id] = a.advisor_name })
    return m
  }, [team])

  const greeting = now.getHours() < 12 ? 'Good morning'
    : now.getHours() < 17 ? 'Good afternoon' : 'Good evening'
  const firstName = user?.full_name?.split(' ')[0] || 'there'

  function go(path) { navigate(path) }

  function runSearch(e) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/leads?q=${encodeURIComponent(q)}` : '/leads')
  }

  // ── WHAT NEEDS ATTENTION ─────────────────────────────────────────────────
  // Real counts only. A condition at zero is not shown: an empty queue is the
  // honest answer to "what needs to happen next", not four rows saying none.
  const attention = []
  if (hotReplies > 0) attention.push({
    key: 'hot', tone: 'var(--signal-red)',
    title: `${hotReplies} hot ${hotReplies === 1 ? 'reply needs' : 'replies need'} a human response`,
    sub: 'High-intent contacts waiting on qualification or scheduling.',
    cta: 'Review', to: '/replies?needs_attention=true',
  })
  if (briefing?.cadence_touches_due_today > 0) attention.push({
    key: 'cadence', tone: 'var(--signal-amber)',
    title: `${briefing.cadence_touches_due_today} cadence ${briefing.cadence_touches_due_today === 1 ? 'touch is' : 'touches are'} due today`,
    sub: 'Scheduled follow-ups that have reached their send time.',
    cta: 'Open', to: '/cadence',
  })
  if (newLeads > 0) attention.push({
    key: 'new', tone: 'var(--signal-blue)',
    title: `${num(newLeads)} ${newLeads === 1 ? 'lead has' : 'leads have'} never been contacted`,
    sub: briefing?.leads_imported_last_24h
      ? `${num(briefing.leads_imported_last_24h)} of them arrived in the last 24 hours.`
      : 'Still sitting at status "new".',
    cta: 'Filter', to: '/leads?status=new',
  })
  if (briefing?.certified_appointments_waiting > 0) attention.push({
    key: 'appts', tone: 'var(--signal-green)',
    title: `${briefing.certified_appointments_waiting} ${IL.confirmLabel}`,
    sub: 'Booked or confirmed, with no outcome recorded yet.',
    cta: 'View', to: '/workqueue',
  })
  // The AI forecast already produces actionable alerts with their own routes.
  // Folding them in here keeps that feature rather than dropping it, and keeps
  // one place to look for "what next".
  ;(forecast?.alerts || []).forEach((a, i) => attention.push({
    key: 'fc' + i,
    tone: a.type === 'urgent' ? 'var(--signal-red)' : 'var(--signal-purple)',
    title: a.message, sub: 'From the pipeline forecast.',
    cta: a.action || 'Open', to: a.path || '/pipeline',
  }))

  const kpis = [
    { label: 'Total leads', value: num(totalLeads), color: 'var(--signal-blue)',
      trend: briefing?.leads_imported_last_24h != null
        ? `+${num(briefing.leads_imported_last_24h)} in 24h` : 'all lists', to: '/leads' },
    { label: 'New / unworked', value: num(newLeads), color: 'var(--signal-amber)',
      trend: newLeads > 0 ? 'needs attention' : 'nothing waiting', to: '/leads?status=new' },
    { label: 'Hot replies', value: num(hotReplies), color: 'var(--signal-red)',
      trend: hotReplies > 0 ? 'awaiting a decision' : 'inbox clear',
      to: '/replies?needs_attention=true' },
    { label: IL.appointments, value: num(bookedLeads), color: 'var(--signal-green)',
      // Label first, count second. "1 recorded visits" is what you get from
      // gluing a count onto a plural noun, and the industry labels are plural
      // by nature ("Arrangements", "Installs") so there is no singular to pick.
      trend: outcomes?.total_appointments != null
        ? `${IL.recordedVisits}: ${num(outcomes.total_appointments)}` : IL.bookedSub,
      to: '/leads?status=booked' },
    { label: 'Reply rate', value: replyRate === null ? '—' : replyRate + '%',
      color: 'var(--signal-purple)',
      trend: sentLeads > 0 ? `of ${num(sentLeads)} contacted` : 'nothing sent yet',
      to: '/reports', managerOnly: true },
    { label: IL.bookingRate, value: bookingRate === null ? '—' : bookingRate + '%',
      color: 'var(--signal-green)',
      trend: sentLeads > 0 ? `of ${num(sentLeads)} contacted` : 'nothing sent yet',
      to: '/reports', managerOnly: true },
    { label: 'Callbacks & touches', value: num(briefing?.cadence_touches_due_today),
      color: 'var(--signal-amber)', trend: 'due today', to: '/cadence' },
    { label: 'DNC / opted out', value: num(dncCount), color: 'var(--text-secondary)',
      trend: 'suppression active', to: '/leads?status=dnc' },
  ]

  return (
    <div className="ov-page">

      {/* ── top bar ── */}
      <div className="ov-topbar">
        <form className="ov-search" onSubmit={runSearch}>
          <span className="ov-search-icon" aria-hidden="true">🔍</span>
          <input
            value={query} onChange={e => setQuery(e.target.value)}
            placeholder="Search leads by name, phone or email…"
            aria-label="Search leads"
          />
        </form>
        <div className="ov-top-actions">
          {!observationMode && (
            <button className="ov-btn" onClick={() => go('/leads?import=1')}>Import leads</button>
          )}
          {!observationMode && isEnabled('campaigns') && isManager && (
            <button className="ov-btn" onClick={() => go('/campaigns')}>New campaign</button>
          )}
          <button className="ov-btn ov-btn--primary" onClick={() => go('/workqueue')}>
            ⚡ View urgent work
          </button>
        </div>
      </div>

      {/* THE FAILURE IS SAID OUT LOUD, ABOVE THE NUMBERS IT INVALIDATES.
          Placed here rather than inside one widget because a failed request
          poisons several tiles at once, and an advisor reading "0 leads" needs
          to know the figure is not an answer before they act on it. */}
      {!loading && loadError && (
        <div className="ov-load-error" role="alert" style={{
          margin: '0 0 16px', padding: '11px 14px', borderRadius: 10,
          background: 'rgba(240,80,80,0.12)',
          border: '1px solid rgba(240,80,80,0.35)', fontSize: 13.5,
        }}>
          <strong>Some of this dashboard could not load.</strong>{' '}
          The numbers below are incomplete — this is not an empty pipeline.
          <div style={{ marginTop: 5, opacity: 0.75, fontSize: 12.5 }}>
            {loadError}
          </div>
        </div>
      )}

      {/* ── hero ── */}
      <div className="ov-hero">
        <div>
          <div className="ov-greeting">{greeting}, {firstName}.</div>
          <div className="ov-sub">
            {loading ? 'Loading your workspace…'
              : attention.length
                ? `${attention.length} thing${attention.length === 1 ? '' : 's'} need your attention right now.`
                : 'Nothing is waiting on you. Here is what moved.'}
          </div>
        </div>
        <div className="ov-datebox">
          <div className="ov-clock">
            {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
          <div className="ov-date">
            {now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}
          </div>
        </div>
      </div>

      {/* ── quick row ── */}
      {!loading && (
        <div className="ov-quick-row">
          <button className="ov-quick" onClick={() => go('/replies?needs_attention=true')}>
            <strong>{num(hotReplies)}</strong> replies awaiting review
          </button>
          <button className="ov-quick" onClick={() => go('/cadence')}>
            <strong>{num(briefing?.cadence_touches_due_today ?? 0)}</strong> touches due today
          </button>
          <button className="ov-quick" onClick={() => go('/leads?status=new')}>
            <strong>{num(newLeads)}</strong> leads never contacted
          </button>
          <button className="ov-quick" onClick={() => go('/leads?status=booked')}>
            <strong>{num(briefing?.bookings_last_7_days ?? 0)}</strong> {IL.weeklyLabel}
          </button>
        </div>
      )}

      {/* ── KPI cards ── */}
      <div className="ov-kpis">
        {kpis.filter(k => !k.managerOnly || isManager).map(k => (
          <button key={k.label} className="ov-kpi" onClick={() => go(k.to)}
                  title={'Open ' + k.to}>
            <span className="ov-kpi-label">{k.label}</span>
            <span className="ov-kpi-value" style={{ color: k.color }}>
              {loading ? '·' : k.value}
            </span>
            <span className="ov-kpi-trend">{k.trend}</span>
          </button>
        ))}
      </div>

      {/* ── attention + hot replies ── */}
      <div className="ov-grid">
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">What needs attention now</h2>
            <span className="panel-count">{loading ? '' : attention.length}</span>
          </div>
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : attention.length === 0 ? (
            <div className="empty-state">
              Nothing is waiting on a person right now — no unanswered hot replies,
              no overdue touches, no untouched leads.
            </div>
          ) : (
            <div className="ov-queue">
              {attention.map(a => (
                <button key={a.key} className="ov-queue-item" onClick={() => go(a.to)}>
                  <span className="ov-queue-dot" style={{ background: a.tone }} />
                  <span>
                    <span className="ov-queue-title">{a.title}</span>
                    <span className="ov-queue-sub">{a.sub}</span>
                  </span>
                  <span className="ov-queue-go">{a.cta} →</span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Hot replies</h2>
            <span className="panel-count">{replies.length}</span>
          </div>
          {loading ? <div className="empty-state">Loading…</div>
            : replies.length === 0 ? (
              <div className="empty-state">No replies are waiting on a decision.</div>
            ) : (
              <>
                {replies.slice(0, 5).map(r => (
                  <button key={r.id} className="ov-reply"
                          onClick={() => r.lead_id ? go(`/leads/${r.lead_id}`) : go('/replies')}
                          title="Open the lead's conversation">
                    <span className="ov-reply-avatar">{initials(r.lead_name)}</span>
                    <span style={{ minWidth: 0 }}>
                      <span className="ov-reply-name">
                        {r.lead_name}{ago(r.received_at) ? ` · ${ago(r.received_at)} ago` : ''}
                      </span>
                      <span className="ov-reply-meta">
                        {(r.source || 'sms').toUpperCase()}
                        {r.classification ? ` · ${String(r.classification).replace(/_/g, ' ')}` : ''}
                        {r.is_hot ? ' · hot' : ''}
                        {r.reviewed_at ? ' · reviewed' : ' · unreviewed'}
                      </span>
                      <p className="ov-reply-body">
                        {String(r.body || '').slice(0, 160)}
                        {String(r.body || '').length > 160 ? '…' : ''}
                      </p>
                    </span>
                  </button>
                ))}
                {replies.length > 5 && (
                  <button className="ov-btn" style={{ marginTop: 10 }}
                          onClick={() => go('/replies?needs_attention=true')}>
                    See all {replies.length} →
                  </button>
                )}
              </>
            )}
        </section>
      </div>

      {/* ── lead flow ── */}
      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Lead flow</h2>
          <span className="panel-count">{loading ? '' : num(totalLeads)}</span>
        </div>
        {loading ? <div className="empty-state">Loading…</div>
          : funnel.length === 0 ? (
            <div className="empty-state">
              No pipeline data yet. Import leads to start populating these stages.
            </div>
          ) : (
            <div className="ov-pipeline">
              {funnel.map(s => (
                <button key={s.status} className="ov-stage"
                        onClick={() => go('/leads?status=' + s.status)}>
                  <span className="ov-stage-label">{s.label}</span>
                  <span className="ov-stage-value" style={{ color: STAGE_TONE[s.status] }}>
                    {num(s.count)}
                  </span>
                  <span className="ov-stage-sub">
                    {totalLeads ? Math.round((s.count / totalLeads) * 1000) / 10 + '% of all leads' : ''}
                  </span>
                </button>
              ))}
              <button className="ov-stage" onClick={() => go('/leads?status=dnc')}>
                <span className="ov-stage-label">DNC</span>
                <span className="ov-stage-value" style={{ color: 'var(--text-secondary)' }}>
                  {num(dncCount)}
                </span>
                <span className="ov-stage-sub">suppressed</span>
              </button>
            </div>
          )}
        <p className="ov-note">
          These are the real lead statuses this organization uses. Stages the schema does not
          record are not shown.
        </p>
      </section>

      {/* ── leads needing action + activity ── */}
      <div className="ov-grid">
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Leads needing action</h2>
            <button className="ov-btn" onClick={() => go('/leads')}>Open Leads →</button>
          </div>
          {loading ? <div className="empty-state">Loading…</div>
            : recentLeads.length === 0 ? (
              <div className="empty-state">No leads yet. Import a list to get started.</div>
            ) : (
              <div className="ov-tablewrap">
                <table className="ov-table">
                  <thead>
                    <tr>
                      <th>Lead</th><th>Source</th><th>Status</th>
                      <th>Owner</th><th>Last touch</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentLeads
                      .filter(l => ['new', 'replied', 'hot'].includes(l.status))
                      .slice(0, 8)
                      .map(l => (
                        <tr key={l.id} onClick={() => go('/leads/' + l.id)}>
                          <td>
                            <strong>
                              {[l.first_name, l.last_name].filter(Boolean).join(' ')
                                || l.phone || l.email || 'Unnamed lead'}
                            </strong>
                          </td>
                          <td style={{ color: 'var(--text-secondary)' }}>
                            {l.import_list_name || l.source_file || '—'}
                          </td>
                          <td>
                            <span className={'ov-pill ov-pill--' + (STATUS_PILL[l.status] || 'off')}>
                              {String(l.status || '—').toUpperCase()}
                            </span>
                          </td>
                          <td style={{ color: 'var(--text-secondary)' }}>
                            {l.assigned_to_id
                              ? (advisorNames[l.assigned_to_id]
                                  || (l.assigned_to_id === user?.id ? 'You' : 'Assigned'))
                              : 'Unassigned'}
                          </td>
                          <td style={{ color: 'var(--text-secondary)' }}>
                            {ago(l.last_messaged_at) ? ago(l.last_messaged_at) + ' ago' : 'never'}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {recentLeads.filter(l => ['new', 'replied', 'hot'].includes(l.status)).length === 0 && (
                  <div className="empty-state">
                    Nothing in the most recent 40 leads is waiting on a touch.
                  </div>
                )}
              </div>
            )}
          <p className="ov-note">
            Owner names come from the organization's advisor metrics, which only an admin can
            read — an advisor sees only their own leads here, so the owner is always them.
          </p>
        </section>

        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Recent activity</h2>
            <button className="ov-btn" onClick={() => go('/activity')}>All activity →</button>
          </div>
          {loading ? <div className="empty-state">Loading…</div>
            : activity.length === 0 ? (
              <div className="empty-state">Nothing has been sent in the last 7 days.</div>
            ) : activity.slice(0, 8).map(a => (
              <button key={a.channel + a.id} className="ov-activity"
                      onClick={() => a.lead_id ? go('/leads/' + a.lead_id) : go('/activity')}>
                <span className="ov-activity-dot" style={{
                  background: a.delivery_status === 'failed' || a.delivery_status === 'undelivered'
                    ? 'var(--signal-red)'
                    : a.delivery_status === 'delivered' ? 'var(--signal-green)'
                    : a.channel === 'email' ? 'var(--signal-purple)' : 'var(--signal-blue)',
                }} />
                <span style={{ minWidth: 0 }}>
                  <span className="ov-activity-title">
                    {a.channel === 'email' ? 'Email' : 'SMS'} to {a.lead_name}
                  </span>
                  <span className="ov-activity-sub">
                    {ago(a.sent_at) ? ago(a.sent_at) + ' ago' : '—'}
                    {' · '}{a.delivery_status || 'pending'}
                    {a.subject ? ` · ${a.subject}` : ''}
                  </span>
                </span>
              </button>
            ))}
        </section>
      </div>

      {/* ── team performance ── */}
      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Team performance</h2>
          {team?.totals && (
            <span className="panel-count">{num(team.advisors?.length ?? 0)}</span>
          )}
        </div>
        {!isManager ? (
          <div className="empty-state">
            Team performance is an organization-admin view. Your own numbers are in Reports.
          </div>
        ) : teamError ? (
          <div className="empty-state">Team metrics are unavailable: {teamError}</div>
        ) : loading ? (
          <div className="empty-state">Loading…</div>
        ) : !team?.advisors?.length ? (
          <div className="empty-state">
            No advisors are set up in this organization yet, so there is no per-person
            output to report.
          </div>
        ) : (
          <>
            <div className="ov-team">
              {team.advisors.slice(0, 8).map(a => (
                <button key={a.advisor_id} className="ov-person"
                        onClick={() => go('/users/' + a.advisor_id)}
                        title="Open this advisor">
                  <span className="ov-person-name">{a.advisor_name || 'Unnamed advisor'}</span>
                  <span className="ov-person-role">
                    Advisor · {a.reply_rate}% reply · {a.booking_rate}% booked
                  </span>
                  <div className="ov-person-metrics">
                    <div><strong>{num(a.leads_owned)}</strong><small>leads</small></div>
                    <div><strong>{num(a.messages_sent)}</strong><small>sent</small></div>
                    <div><strong>{num(a.booked_leads)}</strong><small>booked</small></div>
                  </div>
                </button>
              ))}
            </div>
            {team.totals && (
              <p className="ov-note">
                Organization total: {num(team.totals.leads_owned)} leads owned ·{' '}
                {num(team.totals.messages_sent)} messages sent ·{' '}
                {num(team.totals.replies)} replies ·{' '}
                {num(team.totals.booked_leads)} booked ·{' '}
                {team.totals.reply_rate}% reply rate. These are counts from the database, not
                estimates.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  )
}
