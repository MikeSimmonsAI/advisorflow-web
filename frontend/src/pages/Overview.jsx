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
import { api, getCurrentUser, getWorkspaceContext } from '../api/client'
import { useObservationMode } from '../context/ObservationContext'
import { useWorkspaceAuthority } from '../auth/workspaceAuthority'
import './Overview.css'

// ── Industry-aware labels ─────────────────────────────────────────────────────
//
// THIS USED TO BE A SECOND INDUSTRY REGISTRY, AND IT ANSWERED "FUNERAL" FOR
// EVERY TENANT ON THE PLATFORM. Seventy hand-written strings in a map keyed by
// industries the backend has never heard of (`solar`, `sales`) and missing ones
// it has (`energy`, `dental`, `generic`), read as:
//
//     const industry = branding?.industry || 'funeral'
//     const IL = INDUSTRY_LABELS[industry] || DEFAULT_LABELS   // = funeral
//
// `branding` never carried an `industry` key — `fetchAndStoreBranding` builds
// an eight-field object and that is not one of them — so `industry` was
// ALWAYS undefined and the funeral map was not a fallback, it was the only
// path. An energy customer's dashboard said ARRANGEMENTS, ARRANGEMENT RATE and
// RECORDED VISITS because nothing had ever asked what business they are in.
//
// The platform's one industry registry answers that, and `src/terminology.js`
// fetches it. The seven labels are derived from a single noun per business
// type, so there is nothing here to drift.
import { metricLabels, useTerminology } from '../terminology'

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

  // ONE AUTHORITY, THE SAME ONE THE SIDEBAR AND THE ROUTES USE.
  //
  // This page used to read `branding.enabled_features` itself and apply the
  // answer to exactly one button, while deciding "is this person a manager"
  // from `user.role` — the global row value the rest of the app had already
  // stopped trusting. See auth/workspaceAuthority.js for what that cost.
  const authority = useWorkspaceAuthority()
  const { isFeatureEnabled: isEnabled, isManager } = authority
  const branding = authority.branding
  // THIS ORGANIZATION'S OWN WORDS, resolved by the server from its configured
  // business type. Neutral English until the answer arrives, never a vertical's.
  const terminology = useTerminology()
  const IL = useMemo(
    () => metricLabels(terminology.vocabulary?.appointments),
    [terminology.vocabulary?.appointments])

  // Team performance reads an admin endpoint. An advisor asking for it gets a
  // 403, so it is not requested for them and the panel says why rather than
  // rendering an empty box that looks like "your team did nothing".
  // (`isManager` now comes from the workspace role above, not `user.role`.)

  // WHAT THIS DASHBOARD IS ALLOWED TO BE ABOUT.
  //
  // Six of the ten calls below are lead-only, as are the search box, the
  // import button, four KPI cards, two of the quick-row tiles, Lead flow and
  // Leads needing action. Resolved once, here, so the request list and the
  // render agree by construction rather than by both remembering to check.
  const hasLeads = isEnabled('leads')
  const hasImports = isEnabled('imports')
  const hasCadences = isEnabled('cadences')
  const hasCompliance = isEnabled('compliance')

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
    let unexpected = 0

    // A REFUSAL IS NOT AN ERROR, AND NEITHER IS EVER SHOWN VERBATIM.
    //
    // This used to push `e.message` — the backend's own `detail` string — into
    // a banner the customer reads. An advisor in a workspace without `leads`
    // was shown, six times over:
    //
    //   "This organization is not enabled for 'leads' (Lead management and the
    //    leads list). An operator can enable it in the customer's Features
    //    settings."
    //
    // That sentence is addressed to an operator. It names an internal feature
    // key, describes a console the reader cannot open, and tells somebody who
    // bought a plan without a module that their software is broken. It is also
    // the wrong count: six refusals of one module read as six failures.
    //
    // 402 and 403 are the server working correctly. They are not counted, not
    // shown, and not described — the module is simply absent from the page, as
    // it is from the sidebar. Anything else is a genuine fault, and the
    // customer is told that much in one sentence with no detail in it. The
    // detail still goes to the console for whoever is looking.
    const attempt = (label, promise, fallback) =>
      promise.catch(e => {
        const status = e?.status ?? e?.response?.status
        const entitlement = status === 402 || status === 403
        if (!entitlement) {
          unexpected += 1
          // eslint-disable-next-line no-console
          console.warn('[overview] %s failed:', label, e?.message || e)
        }
        return fallback
      })

    // NOT REQUESTED AT ALL WHEN THE MODULE IS OFF.
    //
    // Firing a request you know will be refused is six round trips spent to
    // learn something already in hand, and every one of them is a log line
    // that looks like an incident. `skip` keeps the destructuring positional
    // so the shape of this list stays readable against the render below.
    const skip = (fallback) => Promise.resolve(fallback)

    const calls = [
      hasLeads ? attempt('leads', api.get('/leads/?page=1&page_size=1'), null) : skip(null),
      hasLeads && hasCompliance
        ? attempt('suppression', api.get('/leads/?status=dnc&page=1&page_size=1'), null)
        : skip(null),
      hasLeads ? attempt('status funnel', api.get('/leads/status-funnel'), []) : skip([]),
      hasLeads ? attempt('daily briefing', api.get('/leads/daily-briefing'), null) : skip(null),
      attempt('replies', api.get('/sms/replies?needs_attention=true'), []),
      hasLeads ? attempt('lead list', api.get('/leads/?page=1&page_size=40'), null) : skip(null),
      attempt('activity', api.get('/activity/sent?limit=8&days=7'), []),
      attempt('outcomes', api.get('/outcomes/summary'), null),
      hasLeads ? attempt('forecast', api.get('/pipeline/forecast'), null) : skip(null),
      isManager && isEnabled('master_dashboard')
        ? attempt('team', api.get('/admin/dashboard/metrics'), null)
        : skip(null),
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
      setTeam(tm)
      setTeamError('')
      // ONE SENTENCE, NO DETAIL, AND ONLY FOR A REAL FAULT. The count is not
      // shown either: "3 of 10 calls failed" is an engineer's framing of a
      // problem the reader cannot act on.
      setLoadError(unexpected > 0 ? 'Some workspace data is unavailable.' : '')
      setLoading(false)
    })
    return () => { live = false }
  }, [isManager, identityKey, workspaceKey,
      hasLeads, hasImports, hasCadences, hasCompliance])

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

  // EVERY TILE DECLARES THE MODULE IT IS ABOUT.
  //
  // Eight tiles, six of them lead-derived, and not one of them said so. In a
  // workspace without `leads` the six rendered anyway: "Total leads —", "New /
  // unworked —", four more, each a door onto a route that refuses and each a
  // dash that reads as "you have none" rather than "we did not ask". `feature`
  // is filtered below, next to the existing managerOnly filter, so the two
  // reasons a tile can be absent are stated in the same place.
  const kpis = [
    { label: `Total ${terminology.vocabulary?.leads || 'leads'}`, value: num(totalLeads),
      feature: 'leads', color: 'var(--signal-blue)',
      trend: briefing?.leads_imported_last_24h != null
        ? `+${num(briefing.leads_imported_last_24h)} in 24h` : 'all lists', to: '/leads' },
    { label: 'New / unworked', value: num(newLeads), color: 'var(--signal-amber)',
      feature: 'leads',
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
      feature: 'leads', to: '/leads?status=booked' },
    { label: 'Reply rate', value: replyRate === null ? '—' : replyRate + '%',
      color: 'var(--signal-purple)',
      trend: sentLeads > 0 ? `of ${num(sentLeads)} contacted` : 'nothing sent yet',
      to: '/reports', managerOnly: true, feature: 'reports' },
    { label: IL.bookingRate, value: bookingRate === null ? '—' : bookingRate + '%',
      color: 'var(--signal-green)',
      trend: sentLeads > 0 ? `of ${num(sentLeads)} contacted` : 'nothing sent yet',
      to: '/reports', managerOnly: true, feature: 'reports' },
    { label: 'Callbacks & touches', value: num(briefing?.cadence_touches_due_today),
      color: 'var(--signal-amber)', trend: 'due today', to: '/cadence',
      feature: 'cadences' },
    { label: 'DNC / opted out', value: num(dncCount), color: 'var(--text-secondary)',
      trend: 'suppression active', to: '/leads?status=dnc',
      // Two modules: the suppression list is a compliance feature, and this
      // particular view of it is a filter over the lead list.
      feature: 'compliance', alsoFeature: 'leads' },
  ]

  return (
    <div className="ov-page">

      {/* ── top bar ── */}
      <div className="ov-topbar">
        {/* THE SEARCH BOX SEARCHES LEADS. Without the module there is nothing
            behind it: submitting went to /leads, which the router refuses, so
            it was a text field whose only outcome was a denial. */}
        {hasLeads ? (
          <form className="ov-search" onSubmit={runSearch}>
            <span className="ov-search-icon" aria-hidden="true">🔍</span>
            <input
              value={query} onChange={e => setQuery(e.target.value)}
              placeholder={`Search ${terminology.vocabulary?.leads || 'leads'} by name, phone or email…`}
              aria-label={`Search ${terminology.vocabulary?.leads || 'leads'}`}
            />
          </form>
        ) : <div className="ov-search-spacer" />}
        <div className="ov-top-actions">
          {/* IMPORT NEEDS BOTH: somewhere to put the records and the right to
              stage them. It was shown unconditionally. */}
          {!observationMode && hasLeads && hasImports && (
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

      {/* SAID ONCE, CALMLY, AND ONLY WHEN SOMETHING ACTUALLY BROKE.
          A module this workspace does not have is not a failure and does not
          appear here — it is absent from the page, the way it is absent from
          the sidebar. What remains is a genuine fault, and the reader is told
          that much and nothing they cannot act on. The status colour is amber
          rather than red for the same reason: the page around it is still
          correct. */}
      {!loading && loadError && (
        <div className="ov-load-error" role="status" style={{
          margin: '0 0 16px', padding: '11px 14px', borderRadius: 10,
          background: 'rgba(240,180,60,0.10)',
          border: '1px solid rgba(240,180,60,0.30)', fontSize: 13.5,
        }}>
          {loadError}
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
      {/* Replies is its own module and keeps working — this workspace has it,
          and its hot replies are real work whatever else is switched off. The
          other three are lead- and cadence-derived and go with them. */}
      {!loading && (
        <div className="ov-quick-row">
          <button className="ov-quick" onClick={() => go('/replies?needs_attention=true')}>
            <strong>{num(hotReplies)}</strong> replies awaiting review
          </button>
          {hasCadences && (
            <button className="ov-quick" onClick={() => go('/cadence')}>
              <strong>{num(briefing?.cadence_touches_due_today ?? 0)}</strong> touches due today
            </button>
          )}
          {hasLeads && (
            <button className="ov-quick" onClick={() => go('/leads?status=new')}>
              <strong>{num(newLeads)}</strong> {terminology.vocabulary?.leads || 'leads'} never contacted
            </button>
          )}
          {hasLeads && (
            <button className="ov-quick" onClick={() => go('/leads?status=booked')}>
              <strong>{num(briefing?.bookings_last_7_days ?? 0)}</strong> {IL.weeklyLabel}
            </button>
          )}
        </div>
      )}

      {/* ── KPI cards ── */}
      <div className="ov-kpis">
        {kpis
          .filter(k => !k.managerOnly || isManager)
          .filter(k => isEnabled(k.feature) && isEnabled(k.alsoFeature))
          .map(k => (
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
      {hasLeads && (
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
      )}

      {/* ── leads needing action + activity ── */}
      <div className="ov-grid">
        {hasLeads && (
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
        )}

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
      {isManager && isEnabled('master_dashboard') && (
      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Team performance</h2>
          {team?.totals && (
            <span className="panel-count">{num(team.advisors?.length ?? 0)}</span>
          )}
        </div>
        {/* The !isManager branch is unreachable now that the whole section is
            behind `isManager` — an advisor is not shown an admin view and then
            told it is an admin view. `teamError` likewise: the request is only
            made when both the role and the module allow it, and a failure is
            counted with the rest rather than described here. */}
        {loading ? (
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
      )}
    </div>
  )
}
