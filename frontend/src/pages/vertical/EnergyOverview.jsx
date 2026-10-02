/**
 * RETAIL ENERGY — OPERATIONS OVERVIEW.
 *
 * The approved back-office design, rendered from THIS workspace's real
 * records. Not a second dashboard engine: every panel below reads an
 * endpoint the platform already serves, through the same authorized scope
 * every other screen uses, so what an operator sees here and what they see
 * on the screen it links to can never disagree.
 *
 * ── Sources. Every number on this page names one. ──────────────────────────
 *   GET /leads/daily-briefing          new leads (24h), replies needing a
 *                                      decision, follow-ups due today
 *   GET /workspace-views               which configured screens exist here
 *   GET /workspace-views/rate-requests total + New / In Review / Options
 *                                      Sent / Unassigned counters, and the
 *                                      most recent rate-request rows
 *   GET /workspace-views/renewals      total + Renewal Due / Under Contract
 *   GET /energy-ops/moves?per_page=1  Move Concierge open count (summary.open)
 *   GET /leads/sparklines?days=7       daily new leads and daily bookings
 *   GET /leads/?page=1&page_size=8     the recent-leads list
 *   GET /leads/workspace-summary       lead sources over the last 30 days
 *   GET /intake/contacts/summary       the customer snapshot
 *   GET /launch/me                     launch progress and the real status of
 *                                      every integration the launch tracks
 *
 * ── The rule this page is built on ────────────────────────────────────────
 * NO NUMBER WITHOUT A SOURCE, AND NO SHAPE WITHOUT A NUMBER. The approved
 * concept shows sample counts and an "all core services online" banner.
 * None of those is a fact about this workspace until the server says so, so
 * none of them is drawn from the concept. A metric this schema cannot yet
 * compute renders its card in full with an honest "not yet available" body —
 * the layout is the design's, the content is the truth.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getWorkspaceContext } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { useTerminology } from '../../terminology'
import './EnergyOverview.css'

const VIEW_RATE_REQUESTS = 'rate-requests'
const VIEW_RENEWALS = 'renewals'

// A refused call (the module or permission is absent) is marked, not
// swallowed, so a card can tell "you may not see this" from "nothing yet".
const FORBIDDEN = 'forbidden'

function num(n) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US')
}

function isNum(n) {
  return typeof n === 'number' && Number.isFinite(n)
}

function pct(part, whole) {
  if (!isNum(part) || !isNum(whole) || whole <= 0) return null
  return Math.round((part / whole) * 100)
}

function statValue(payload, label) {
  if (!payload || !Array.isArray(payload.stats)) return null
  const hit = payload.stats.find(s => s.label === label)
  return hit ? hit.value : null
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase()
}

function humanize(value) {
  const s = String(value || '').replace(/[_-]+/g, ' ').trim()
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—'
}

// The server writes naive UTC ISO strings; read them as UTC.
function parseIso(iso) {
  if (!iso) return null
  const s = String(iso)
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

function ago(iso) {
  const d = parseIso(iso)
  if (!d) return ''
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return days < 30 ? `${days}d ago` : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// ── icons ──────────────────────────────────────────────────────────────────
// Stroke icons in one weight, so the page reads as one set.
const ICONS = {
  users: <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>,
  user: <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="13" y2="17" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></>,
  refresh: <><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></>,
  zap: <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />,
  truck: <><rect x="1" y="3" width="15" height="13" /><polygon points="16 8 20 8 23 11 23 16 16 16 16 8" /><circle cx="5.5" cy="18.5" r="2.5" /><circle cx="18.5" cy="18.5" r="2.5" /></>,
  clock: <><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></>,
  more: <><circle cx="5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="19" cy="12" r="1.2" /></>,
  globe: <><circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></>,
  phone: <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />,
  mail: <><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" /></>,
  alert: <><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></>,
  check: <polyline points="20 6 9 17 4 12" />,
  plug: <><path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0z" /><path d="M12 18v4" /></>,
}

function Icon({ name, size = 18 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name] || ICONS.more}
    </svg>
  )
}

// No clock here: the app shell's top bar (components/Layout.jsx LiveClock)
// already shows the time, and a second one in the page header duplicated it.

function sourceIcon(source) {
  const s = String(source || '').toLowerCase()
  if (s.includes('web') || s.includes('site') || s.includes('form')) return 'globe'
  if (s.includes('compare') || s.includes('rate')) return 'zap'
  if (s.includes('move') || s.includes('concierge')) return 'truck'
  if (s.includes('referr') || s.includes('partner')) return 'users'
  if (s.includes('import') || s.includes('csv') || s.includes('upload')) return 'file'
  if (s.includes('phone') || s.includes('call')) return 'phone'
  return 'user'
}

// Integration statuses as the launch programme records them
// (app/models/launch_delivery_models.py). `connected` and `verified` are the
// only two that mean something answered.
const INTEGRATION_STATE = {
  connected: { tone: 'green', label: 'Connected' },
  verified: { tone: 'green', label: 'Connected' },
  blocked: { tone: 'red', label: 'Blocked' },
  not_applicable: { tone: 'muted', label: 'Not needed' },
  credentials_received: { tone: 'amber', label: 'Credentials received' },
  configuring: { tone: 'amber', label: 'Configuring' },
  testing: { tone: 'amber', label: 'Testing' },
}
function integrationState(status) {
  return INTEGRATION_STATE[status] || { tone: 'amber', label: 'Not connected' }
}

export default function EnergyOverview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const authority = useWorkspaceAuthority()
  const terminology = useTerminology()
  const hasLeads = authority.isFeatureEnabled('leads')

  const [views, setViews] = useState([])
  const [briefing, setBriefing] = useState(null)
  const [rateRequests, setRateRequests] = useState(null)
  const [renewals, setRenewals] = useState(null)
  const [concierge, setConcierge] = useState(null)
  // Real operating queues + move requests (app/routers/energy_ops_router.py).
  const [opsQueues, setOpsQueues] = useState(null)
  const [moves, setMoves] = useState(null)
  const [spark, setSpark] = useState(null)
  const [recent, setRecent] = useState([])
  const [leadSummary, setLeadSummary] = useState(null)
  const [contactSummary, setContactSummary] = useState(null)
  const [launch, setLaunch] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')

  // THE WORKSPACE AND THE IDENTITY ARE DEPENDENCIES, for the reason the
  // platform Overview records: switching customer does not remount this
  // component, so without them the previous workspace's numbers stay on
  // screen after a switch.
  const identityKey = `${user?.role || ''}|${user?.organization_id || ''}`
  const workspaceKey = getWorkspaceContext() || ''

  useEffect(() => {
    let live = true
    let unexpected = 0

    // A REFUSAL IS NOT AN ERROR. 402/403 mean the server is working and this
    // workspace does not have the module; the panel is simply absent, as it
    // is from the rail. Anything else is a real fault and is said once, in
    // one sentence, with the detail left in the console for whoever looks.
    const attempt = (label, promise, fallback, forbidden = fallback) =>
      promise.catch(e => {
        const status = e?.status ?? e?.response?.status
        if (status === 403) return forbidden
        if (!(status === 402 || status === 404)) {
          unexpected += 1
          // eslint-disable-next-line no-console
          console.warn('[energy-overview] %s failed:', label, e?.message || e)
        }
        return fallback
      })
    const skip = (fallback) => Promise.resolve(fallback)
    const view = (key) => attempt(key, api.get(`/workspace-views/${key}?limit=6`), null)

    Promise.all([
      attempt('views', api.get('/workspace-views', { skipRedirect: true }), null),
      hasLeads ? attempt('briefing', api.get('/leads/daily-briefing'), null) : skip(null),
      view(VIEW_RATE_REQUESTS),
      view(VIEW_RENEWALS),
      // Move Concierge counts come from /energy-ops/moves below; there is no
      // move-concierge workspace view, so asking for one only produced a 404.
      skip(null),
      hasLeads ? attempt('sparklines', api.get('/leads/sparklines?days=7'), null) : skip(null),
      hasLeads ? attempt('recent leads', api.get('/leads/?page=1&page_size=8'), null) : skip(null),
      hasLeads ? attempt('lead summary', api.get('/leads/workspace-summary'), null) : skip(null),
      attempt('contact summary', api.get('/intake/contacts/summary', { skipRedirect: true }), null, FORBIDDEN),
      attempt('launch', api.get('/launch/me', { skipRedirect: true }), null),
      hasLeads ? attempt('ops queues', api.get('/energy-ops/queues'), null) : skip(null),
      hasLeads ? attempt('moves', api.get('/energy-ops/moves?per_page=1'), null) : skip(null),
    ]).then(([v, b, rr, rn, mc, sp, rl, ls, cs, lc, oq, mv]) => {
      if (!live) return
      setViews(Array.isArray(v?.views) ? v.views : [])
      setBriefing(b)
      setRateRequests(rr)
      setRenewals(rn)
      setConcierge(mc)
      setSpark(sp)
      setRecent(Array.isArray(rl?.items) ? rl.items : [])
      setLeadSummary(ls)
      setContactSummary(cs)
      setLaunch(lc?.implementation ? lc : null)
      setOpsQueues(oq)
      setMoves(mv)
      setLoadError(unexpected > 0 ? 'Some workspace data is unavailable.' : '')
      setLoading(false)
    })
    return () => { live = false }
  }, [identityKey, workspaceKey, hasLeads])

  const hasView = (key) => views.some(v => v.key === key)
  const enrollStats = opsQueues?.enrollments || null
  const queueCount = (key) => {
    const q = opsQueues?.queues?.find(x => x.key === key)
    return q && q.available ? q.count : null
  }
  const go = (path) => navigate(path)
  function runSearch(e) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/leads?q=${encodeURIComponent(q)}` : '/leads')
  }

  // ── THE FOUR COUNTERS ────────────────────────────────────────────────────
  //
  // ENROLMENTS THIS MONTH is the one the schema cannot answer yet: nothing
  // records WHEN an account reached a signed contract, only that it is at
  // that tier now, so a month-to-date figure would have to be guessed. It
  // renders as the design's card with an honest body instead.

  // "NOT CONFIGURED" IS A CLAIM, AND IT IS FALSE WHILE THE ANSWER IS STILL IN
  // FLIGHT. Each card reads its own payload, which is null both before the
  // request returns and when the workspace genuinely has no such screen.
  // Saying the second during the first tells the reader their workspace is
  // misconfigured for a second and a half, every time they open the page.
  const missing = (payload, sentence) =>
    (payload ? null : (loading ? 'Reading this workspace…' : sentence))

  const kpis = [
    {
      key: 'new-leads',
      label: 'New Leads',
      tag: 'Last 24h',
      icon: 'users',
      value: hasLeads ? num(briefing?.leads_imported_last_24h ?? null) : '—',
      sub: hasLeads ? 'Arrived from the website, a referral or an advisor.'
                    : 'The lead module is not enabled for this workspace.',
      to: hasLeads ? '/leads' : null,
      tone: 'blue',
    },
    {
      key: 'rate-requests',
      label: 'Open Rate Requests',
      tag: hasView(VIEW_RATE_REQUESTS) ? 'Live' : null,
      icon: 'file',
      value: num(rateRequests?.total ?? null),
      sub: missing(rateRequests, 'This screen is not configured for this workspace.')
        || `${num(statValue(rateRequests, 'Options Sent') ?? 0)} waiting on the customer · ${num(statValue(rateRequests, 'Unassigned') ?? 0)} unassigned`,
      to: hasLeads ? '/rate-requests' : null,
      tone: 'violet',
    },
    {
      // Counted from Lead.enrolled_at, which the Enroll action records
      // (GET /energy-ops/queues -> enrollments). Customers enrolled before
      // that date was recorded are reported as "date not recorded", never
      // counted into a month.
      key: 'enrollments',
      label: 'Enrollments This Month',
      tag: enrollStats ? 'Enroll action' : 'Not available',
      icon: 'calendar',
      value: enrollStats ? num(enrollStats.this_month) : null,
      sub: enrollStats
        ? (enrollStats.date_not_recorded
            ? `${num(enrollStats.date_not_recorded)} earlier customer${enrollStats.date_not_recorded === 1 ? '' : 's'}: enrollment date not recorded.`
            : 'Recorded when a rate request is enrolled.')
        : 'Enrollment counts are not available for this workspace.',
      to: enrollStats ? '/energy/renewals?queue=customers' : null,
      tone: enrollStats ? 'green' : 'muted',
    },
    {
      key: 'renewals',
      label: 'Renewal Watch',
      tag: opsQueues ? 'Contract end date' : null,
      icon: 'refresh',
      value: num(queueCount('renewal_window')),
      sub: missing(opsQueues, 'Renewals are not available for this workspace.')
        || `${num(queueCount('customers') ?? 0)} enrolled · ${num(opsQueues.renewal_date_missing ?? 0)} with no end date on file.`,
      to: opsQueues ? '/energy/renewals?queue=renewal_window' : null,
      tone: 'green',
    },
  ]

  // ── NEEDS ATTENTION ──────────────────────────────────────────────────────
  //
  // A fixed priority queue, in the design's order, each row counted from a
  // named source. A row whose source this workspace does not have shows a
  // dash, never a zero: "none" and "cannot tell" are different answers.
  const newRates = statValue(rateRequests, 'New')
  const reviewRates = statValue(rateRequests, 'In Review')
  const incompleteRates = rateRequests
    ? (newRates ?? 0) + (reviewRates ?? 0)
    : null
  const followUpsDue = opsQueues ? ((queueCount('follow_up_due') ?? 0) + (queueCount('overdue') ?? 0)) : null
  const repliesWaiting = briefing ? (briefing.replies_needing_attention ?? 0) : null
  const conciergeActive = moves ? (moves.summary?.open ?? 0) : null
  const renewalsUpcoming = opsQueues ? queueCount('renewal_window') : null

  const attention = [
    {
      key: 'rates', tone: 'red', icon: 'file',
      title: 'Incomplete rate requests',
      sub: rateRequests ? 'New or in review — options not yet sent.' : 'Rate requests are not configured here.',
      count: incompleteRates, to: '/rate-requests',
    },
    {
      key: 'followups', tone: 'amber', icon: 'user',
      title: 'Customers needing follow-up',
      sub: opsQueues ? 'Follow-up tasks due today or overdue.' : 'Follow-ups are not available here.',
      count: followUpsDue, to: '/energy/follow-up?queue=overdue',
    },
    {
      key: 'concierge', tone: 'blue', icon: 'truck',
      title: 'Move Concierge pending',
      sub: moves ? 'Move requests still open.' : 'Move Concierge is not available here.',
      count: conciergeActive, to: '/energy/move-concierge?status=open',
    },
    {
      key: 'renewals', tone: 'violet', icon: 'refresh',
      title: 'Renewals coming up',
      sub: opsQueues ? 'Contract end date within the renewal window.' : 'Renewals are not available here.',
      count: renewalsUpcoming, to: '/energy/renewals?queue=renewal_window',
    },
    {
      key: 'other', tone: 'muted', icon: 'more',
      title: 'Other items',
      sub: briefing ? 'Customer replies waiting on a decision.' : 'Replies are not available here.',
      count: repliesWaiting, to: '/replies?needs_attention=true',
    },
  ]
  const attentionTotal = attention.reduce((s, r) => s + (r.count || 0), 0)

  // ── LEAD → ENROLMENT ACTIVITY ────────────────────────────────────────────
  //
  // Server-computed daily counts (UTC days), oldest to newest, zeros
  // included. The second series is BOOKED APPOINTMENTS — that is what the
  // endpoint counts — and it is labelled as such. Rate requests and
  // enrollments have no daily history in this schema, so they are named in
  // the legend as not yet tracked rather than drawn as flat zeros.
  const series = useMemo(() => {
    const leadsIn = Array.isArray(spark?.leads_imported) ? spark.leads_imported : []
    const booked = Array.isArray(spark?.bookings) ? spark.bookings : []
    const days = Math.max(leadsIn.length, booked.length)
    if (!days) return { days: [], peak: 0, total: 0, ticks: [] }
    const now = new Date()
    const todayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
    const out = []
    let peak = 0
    let total = 0
    for (let i = 0; i < days; i += 1) {
      const d = new Date(todayUtc - (days - 1 - i) * 86400000)
      const leads = Number(leadsIn[i] || 0)
      const booked_ = Number(booked[i] || 0)
      peak = Math.max(peak, leads, booked_)
      total += leads + booked_
      out.push({
        label: d.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' }),
        leads, booked: booked_,
      })
    }
    // A readable axis: four steps, rounded up to a whole step.
    const step = Math.max(1, Math.ceil(peak / 4))
    const top = step * 4
    const ticks = [4, 3, 2, 1, 0].map(k => k * step)
    return { days: out, peak: top, total, ticks }
  }, [spark])

  // ── TOP LEAD SOURCES (30 days) ───────────────────────────────────────────
  const sources = useMemo(() => {
    const list = Array.isArray(leadSummary?.sources_30d) ? leadSummary.sources_30d : null
    if (!list) return null
    const rows = list
      .map(r => ({ source: r?.source, count: Number(r?.count) || 0 }))
      .sort((a, b) => b.count - a.count)
    const total = rows.reduce((s, r) => s + r.count, 0)
    return { rows: rows.slice(0, 6), total }
  }, [leadSummary])

  // ── CUSTOMER SNAPSHOT ────────────────────────────────────────────────────
  const snapshotHidden = contactSummary === FORBIDDEN
  const snap = contactSummary && contactSummary !== FORBIDDEN ? contactSummary : null
  const totalContacts = isNum(snap?.contacts) ? snap.contacts : null
  const snapStats = snap ? [
    {
      key: 'email', icon: 'mail', tone: 'blue', value: snap.email_ready,
      label: 'Email ready',
      extra: pct(snap.email_ready, totalContacts),
    },
    {
      key: 'phones', icon: 'phone', tone: 'blue', value: snap.valid_phones,
      label: 'Valid phones',
      note: isNum(snap.mobile) ? `${num(snap.mobile)} mobile` : null,
    },
    {
      key: 'enrich', icon: 'alert', tone: 'amber', value: snap.needs_enrichment,
      label: 'Need enrichment',
      extra: pct(snap.needs_enrichment, totalContacts),
    },
    {
      key: 'previous', icon: 'user', tone: 'amber', value: snap.previous_customers,
      label: 'Previous customers',
      extra: pct(snap.previous_customers, totalContacts),
    },
  ] : []

  // ── SYSTEM READINESS ─────────────────────────────────────────────────────
  //
  // Read from the launch programme's own integration rows. "All core
  // services online" is a claim, so it is made only when every integration
  // that applies here is actually connected or verified.
  const integrations = Array.isArray(launch?.requirements?.integrations)
    ? launch.requirements.integrations : []
  const applicable = integrations.filter(i => i.status !== 'not_applicable')
  const online = applicable.filter(i => i.status === 'connected' || i.status === 'verified')
  const blocked = applicable.filter(i => i.status === 'blocked')
  const allOnline = applicable.length > 0 && online.length === applicable.length
  const launchPct = launch?.overview?.overall_pct
  const launchSteps = Array.isArray(launch?.overview?.steps) ? launch.overview.steps : []

  const tierLabel = useMemo(() => {
    const map = new Map((terminology.tiers || []).map(t => [t.value, t.label]))
    return (value) => (value ? (map.get(value) || humanize(value)) : '—')
  }, [terminology.tiers])

  const leadName = (l) =>
    [l.first_name, l.last_name].filter(Boolean).join(' ') || l.phone || l.email || 'Unnamed'

  const rateItems = Array.isArray(rateRequests?.items) ? rateRequests.items : []

  return (
    <div className="eo eo-ov">
      {/* ── TOP BAR ────────────────────────────────────────────────────── */}
      <header className="eo-header">
        <div className="eo-header-actions">
          <form className="eo-search" onSubmit={runSearch}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search customers, leads, phone or email"
              aria-label="Search customers and leads"
            />
          </form>
          {hasLeads && (
            <button type="button" className="eo-btn" onClick={() => go('/leads')}>
              + New Lead
            </button>
          )}
          {hasView(VIEW_RATE_REQUESTS) && (
            <button type="button" className="eo-btn eo-btn--primary"
                    onClick={() => go(`/view/${VIEW_RATE_REQUESTS}`)}>
              New Rate Request
            </button>
          )}
          <div className="eo-avatar" title={user?.full_name || 'Signed in'}>
            {initials(user?.full_name)}
          </div>
        </div>
      </header>

      {/* ── HERO ───────────────────────────────────────────────────────── */}
      <section className="eo-hero">
        <h1>Operations Overview</h1>
        <p>Today&apos;s leads, customers, enrollments and items needing attention.</p>
      </section>

      {loadError && <div className="eo-notice">{loadError}</div>}

      {/* ── KPI ROW ────────────────────────────────────────────────────── */}
      <section className="eo-kpis">
        {kpis.map(kpi => (
          <article key={kpi.key}
                   className={`eo-kpi eo-kpi--${kpi.tone}${kpi.to ? ' eo-kpi--link' : ''}`}
                   onClick={kpi.to ? () => go(kpi.to) : undefined}
                   role={kpi.to ? 'button' : undefined}
                   tabIndex={kpi.to ? 0 : undefined}
                   onKeyDown={kpi.to ? (e => { if (e.key === 'Enter') go(kpi.to) }) : undefined}>
            <span className="eo-kpi-icon"><Icon name={kpi.icon} size={22} /></span>
            <div className="eo-kpi-body">
              <div className="eo-kpi-top">
                <span className="eo-kpi-label">{kpi.label}</span>
                {kpi.tag && <span className="eo-kpi-tag">{kpi.tag}</span>}
              </div>
              <div className={`eo-kpi-value${kpi.value === null ? ' eo-kpi-value--none' : ''}`}>
                {loading ? '·' : (kpi.value === null ? 'Not yet available' : kpi.value)}
              </div>
              <div className="eo-kpi-sub">{kpi.sub}</div>
            </div>
          </article>
        ))}
      </section>

      {/* ── NEEDS ATTENTION + ACTIVITY ─────────────────────────────────── */}
      <div className="eo-grid eo-grid--split">
        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Needs Attention</h3>
            <span className="eo-card-note">Priority queue</span>
          </div>
          {loading ? (
            <div className="eo-empty">Loading…</div>
          ) : (
            <>
              <ul className="eo-attn" role="none">
                {attention.map(item => {
                  const actionable = item.count > 0
                  return (
                    <li key={item.key}
                        className={`eo-attn-row eo-attn-row--${item.tone}${actionable ? ' eo-attn-row--link' : ''}`}
                        onClick={actionable ? () => go(item.to) : undefined}
                        role={actionable ? 'button' : undefined}
                        tabIndex={actionable ? 0 : undefined}
                        onKeyDown={actionable ? (e => { if (e.key === 'Enter') go(item.to) }) : undefined}>
                      <span className="eo-attn-icon"><Icon name={item.icon} size={16} /></span>
                      <div className="eo-attn-body">
                        <span className="eo-attn-title">{item.title}</span>
                        <span className="eo-attn-sub">{item.sub}</span>
                      </div>
                      <span className={`eo-attn-count${actionable ? ' eo-attn-count--hot' : ''}`}>
                        {item.count === null ? '—' : num(item.count)}
                      </span>
                    </li>
                  )
                })}
              </ul>
              {attentionTotal === 0 && (
                <p className="eo-attn-foot">Nothing is waiting on a person right now.</p>
              )}
            </>
          )}
        </section>

        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Lead → Enrollment Activity</h3>
            <span className="eo-card-note">Last 7 days</span>
          </div>
          {!hasLeads ? (
            <div className="eo-empty">
              <strong>The lead module is not enabled for this workspace.</strong>
            </div>
          ) : loading ? (
            <div className="eo-empty">Loading…</div>
          ) : series.days.length === 0 ? (
            <div className="eo-empty">
              <strong>No activity history yet.</strong>
              <span>This chart draws real daily counts. It fills in as leads
                    arrive and appointments are booked.</span>
            </div>
          ) : (
            <>
              <div className="eo-chartwrap">
                <div className="eo-chart-axis" aria-hidden="true">
                  {series.ticks.map(t => <span key={t}>{t}</span>)}
                </div>
                <div className="eo-chart-plot">
                  <div className="eo-chart-grid" aria-hidden="true">
                    {series.ticks.map(t => <i key={t} />)}
                  </div>
                  <div className="eo-chart" role="img"
                       aria-label={`Daily new leads and booked appointments over the last ${series.days.length} days`}>
                    {series.days.map((d, i) => (
                      <div className="eo-chart-day" key={i}>
                        <div className="eo-chart-bars">
                          <span className="eo-bar eo-bar--leads"
                                style={{ height: `${(d.leads / series.peak) * 100}%` }}
                                title={`${d.leads} new lead${d.leads === 1 ? '' : 's'}`}>
                            <em>{d.leads}</em>
                          </span>
                          <span className="eo-bar eo-bar--booked"
                                style={{ height: `${(d.booked / series.peak) * 100}%` }}
                                title={`${d.booked} appointment${d.booked === 1 ? '' : 's'} booked`}>
                            <em>{d.booked}</em>
                          </span>
                        </div>
                        <span className="eo-chart-label">{d.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="eo-legend">
                <span><i className="eo-key eo-key--leads" /> New leads</span>
                <span><i className="eo-key eo-key--booked" /> Appointments booked</span>
                <span className="eo-legend-off"><i className="eo-key eo-key--off" /> Rate requests · Enrollments — not yet tracked by day</span>
              </div>
              {series.total === 0 && (
                <p className="eo-legend-note">Nothing recorded in this window — every day is a real zero.</p>
              )}
            </>
          )}
        </section>
      </div>

      {/* ── RECENT LEADS / RATE REQUESTS / ENROLLMENTS ─────────────────── */}
      <div className="eo-grid eo-grid--three">
        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Recent Leads</h3>
            {hasLeads && (
              <button type="button" className="eo-link" onClick={() => go('/leads')}>
                View all →
              </button>
            )}
          </div>
          {!hasLeads ? (
            <div className="eo-empty">
              <strong>The lead module is not enabled for this workspace.</strong>
            </div>
          ) : loading ? (
            <div className="eo-empty">Loading…</div>
          ) : recent.length === 0 ? (
            <div className="eo-empty">
              <span className="eo-empty-icon"><Icon name="users" size={30} /></span>
              <strong>No leads yet.</strong>
              <span>Enquiries from the website, referrals and imports appear
                    here the moment they arrive.</span>
              <button type="button" className="eo-btn eo-btn--ghost" onClick={() => go('/leads')}>
                + Add lead
              </button>
            </div>
          ) : (
            <ul className="eo-list" role="none">
              {recent.slice(0, 5).map(l => (
                <li key={l.id} className="eo-list-row" role="button" tabIndex={0}
                    onClick={() => go('/leads/' + l.id)}
                    onKeyDown={e => { if (e.key === 'Enter') go('/leads/' + l.id) }}>
                  <div className="eo-list-main">
                    <strong>{leadName(l)}</strong>
                    <span>
                      {tierLabel(l.tier)}
                      {(l.import_list_name || l.source_file || l.source)
                        ? ` · ${l.import_list_name || l.source_file || humanize(l.source)}` : ''}
                    </span>
                  </div>
                  <div className="eo-list-side">
                    <span className={`eo-pill eo-pill--${l.status || 'off'}`}>
                      {String(l.status || '—').replace(/_/g, ' ')}
                    </span>
                    {l.created_at && <span className="eo-list-when">{ago(l.created_at)}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Recent Rate Requests</h3>
            {hasView(VIEW_RATE_REQUESTS) && (
              <button type="button" className="eo-link" onClick={() => go(`/view/${VIEW_RATE_REQUESTS}`)}>
                View all →
              </button>
            )}
          </div>
          {loading ? (
            <div className="eo-empty">Loading…</div>
          ) : !rateRequests ? (
            <div className="eo-empty">
              <strong>Rate requests are not configured for this workspace.</strong>
            </div>
          ) : rateItems.length === 0 ? (
            <div className="eo-empty">
              <span className="eo-empty-icon"><Icon name="file" size={30} /></span>
              <strong>No rate requests yet.</strong>
              <span>Rate comparison requests from your website or team
                    show up here.</span>
              {hasView(VIEW_RATE_REQUESTS) && (
                <button type="button" className="eo-btn eo-btn--ghost"
                        onClick={() => go(`/view/${VIEW_RATE_REQUESTS}`)}>
                  + New rate request
                </button>
              )}
            </div>
          ) : (
            <ul className="eo-list" role="none">
              {rateItems.slice(0, 5).map(r => {
                const v = r.values || {}
                const detail = [v.location, v.source_detail].filter(Boolean).join(' · ')
                const target = r.lead_id ? `/leads/${r.lead_id}` : `/view/${VIEW_RATE_REQUESTS}`
                return (
                  <li key={r.id} className="eo-list-row" role="button" tabIndex={0}
                      onClick={() => go(target)}
                      onKeyDown={e => { if (e.key === 'Enter') go(target) }}>
                    <div className="eo-list-main">
                      <strong>{v.name || '(no name)'}</strong>
                      <span>{detail || (v.owner ? `Owner: ${v.owner}` : 'Unassigned')}</span>
                    </div>
                    <div className="eo-list-side">
                      <span className="eo-pill">{tierLabel(v.tier)}</span>
                      {v.updated_at && <span className="eo-list-when">{ago(v.updated_at)}</span>}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Recent Enrollments</h3>
            <span className="eo-card-note">{enrollStats ? 'Recorded by Enroll' : 'Not available'}</span>
          </div>
          {!enrollStats ? (
            <div className="eo-empty">
              <span className="eo-empty-icon"><Icon name="calendar" size={30} /></span>
              <strong>Not yet available.</strong>
              <span>Enrollment records are not available for this workspace.</span>
            </div>
          ) : (enrollStats.recent || []).length === 0 ? (
            <div className="eo-empty">
              <span className="eo-empty-icon"><Icon name="calendar" size={30} /></span>
              <strong>No dated enrollments yet.</strong>
              <span>Enrolling a rate request records its date here.
                    {enrollStats.date_not_recorded ? ` ${num(enrollStats.date_not_recorded)} earlier customer(s) have no enrollment date on file.` : ''}</span>
            </div>
          ) : (
            <ul className="eo-list" role="none">
              {enrollStats.recent.map(r => (
                <li key={r.id} className="eo-list-row" role="button" tabIndex={0}
                    onClick={() => go(r.link)}
                    onKeyDown={e => { if (e.key === 'Enter') go(r.link) }}>
                  <div className="eo-list-main">
                    <strong>{r.name}</strong>
                    <span>{r.current_supplier || (r.owner ? `Owner: ${r.owner}` : 'Unassigned')}</span>
                  </div>
                  <div className="eo-list-side">
                    <span className="eo-pill">Enrolled</span>
                    {r.enrolled_at && <span className="eo-list-when">{ago(r.enrolled_at)}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* ── SOURCES / SNAPSHOT / READINESS ─────────────────────────────── */}
      <div className={`eo-grid ${snapshotHidden ? 'eo-grid--two' : 'eo-grid--three'}`}>
        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Top Lead Sources</h3>
            <span className="eo-card-note">Last 30 days</span>
          </div>
          {!hasLeads ? (
            <div className="eo-empty">
              <strong>The lead module is not enabled for this workspace.</strong>
            </div>
          ) : loading ? (
            <div className="eo-empty">Loading…</div>
          ) : !sources ? (
            <div className="eo-empty">
              <strong>Not yet available.</strong>
              <span>Lead source totals could not be read for this workspace.</span>
            </div>
          ) : sources.rows.length === 0 ? (
            <div className="eo-empty">
              <strong>No leads in the last 30 days.</strong>
            </div>
          ) : (
            <ul className="eo-sources">
              {sources.rows.map(r => {
                const share = sources.total > 0 ? Math.round((r.count / sources.total) * 100) : 0
                return (
                  <li key={r.source || 'unknown'} className="eo-source">
                    <span className="eo-source-icon"><Icon name={sourceIcon(r.source)} size={15} /></span>
                    <span className="eo-source-name">{r.source ? humanize(r.source) : 'Unknown'}</span>
                    <span className="eo-source-bar"><span style={{ width: `${share}%` }} /></span>
                    <span className="eo-source-count">{num(r.count)}</span>
                    <span className="eo-source-pct">({share}%)</span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {!snapshotHidden && (
          <section className="eo-card">
            <div className="eo-card-head">
              <h3>Customer Snapshot</h3>
              {snap && (
                <button type="button" className="eo-link" onClick={() => go('/contacts')}>
                  Browse contacts →
                </button>
              )}
            </div>
            {loading ? (
              <div className="eo-empty">Loading…</div>
            ) : !snap ? (
              <div className="eo-empty">
                <strong>Not yet available.</strong>
                <span>The contact summary could not be read for this workspace.</span>
              </div>
            ) : (
              <div className="eo-snap">
                <div className="eo-snap-total">
                  <span className="eo-snap-total-label">Total contacts</span>
                  <span className={`eo-snap-total-value${totalContacts === null ? ' eo-snap-total-value--none' : ''}`}>
                    {totalContacts === null ? 'Not yet available' : num(totalContacts)}
                  </span>
                </div>
                <ul className="eo-snap-stats">
                  {snapStats.map(s => (
                    <li key={s.key} className={`eo-snap-stat eo-snap-stat--${s.tone}`}>
                      <span className="eo-snap-icon"><Icon name={s.icon} size={17} /></span>
                      <div>
                        <strong className={isNum(s.value) ? '' : 'eo-snap-none'}>
                          {isNum(s.value) ? num(s.value) : 'Not yet available'}
                        </strong>
                        <span>
                          {s.label}
                          {isNum(s.value) && s.extra !== null && s.extra !== undefined ? ` (${s.extra}%)` : ''}
                          {isNum(s.value) && s.note ? ` (${s.note})` : ''}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        <section className="eo-card">
          <div className="eo-card-head">
            <h3>System Readiness</h3>
            {launch && (
              <button type="button" className="eo-link" onClick={() => go('/launch')}>
                Launch Center →
              </button>
            )}
          </div>
          {loading ? (
            <div className="eo-empty">Loading…</div>
          ) : integrations.length === 0 ? (
            <div className="eo-empty">
              <strong>Not yet available.</strong>
              <span>Integration status appears here once the launch programme
                    lists the services this workspace uses.</span>
            </div>
          ) : (
            <>
              <div className={`eo-ready-banner eo-ready-banner--${allOnline ? 'green' : blocked.length ? 'red' : 'amber'}`}>
                <span className="eo-ready-banner-icon">
                  <Icon name={allOnline ? 'check' : 'alert'} size={16} />
                </span>
                <div>
                  <strong>
                    {allOnline
                      ? 'All core services online'
                      : `${online.length} of ${applicable.length} services connected`}
                  </strong>
                  <span>
                    {allOnline
                      ? 'Every listed integration is connected.'
                      : blocked.length
                        ? `${blocked.length} ${blocked.length === 1 ? 'is' : 'are'} blocked and need${blocked.length === 1 ? 's' : ''} attention.`
                        : 'The rest are still being set up.'}
                  </span>
                </div>
              </div>
              <ul className="eo-ready">
                {integrations.map(i => {
                  const st = integrationState(i.status)
                  return (
                    <li key={i.key} className={`eo-ready-row eo-ready-row--${st.tone}`}>
                      <span className="eo-ready-dot" aria-hidden="true" />
                      <span className="eo-ready-name">
                        {i.label || humanize(i.key)}
                        {!i.required && i.status !== 'not_applicable' && <em> · optional</em>}
                      </span>
                      <span className="eo-ready-state">{st.label}</span>
                    </li>
                  )
                })}
              </ul>
              {isNum(launchPct) && (
                <div className="eo-launch">
                  <div className="eo-launch-bar" role="img"
                       aria-label={`Launch readiness ${launchPct} percent`}>
                    <span style={{ width: `${Math.max(0, Math.min(100, launchPct))}%` }} />
                  </div>
                  <span className="eo-launch-meta">
                    Launch {launchPct}% · {launch.overview?.complete_steps ?? 0} of{' '}
                    {launch.overview?.total_steps ?? launchSteps.length} steps complete
                  </span>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
