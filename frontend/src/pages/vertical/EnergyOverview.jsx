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
 *                                      Sent / Unassigned counters
 *   GET /workspace-views/renewals      total + Renewal Due / Under Contract
 *   GET /workspace-views/move-concierge total + Active / Unassigned
 *   GET /leads/sparklines?days=7       daily new leads and daily bookings
 *   GET /leads/?page=1&page_size=8     the recent-leads table
 *   GET /launch/me                     real launch readiness
 *
 * ── The rule this page is built on ────────────────────────────────────────
 * NO NUMBER WITHOUT A SOURCE, AND NO SHAPE WITHOUT A NUMBER. The approved
 * concept shows sample counts, an upward trend and a progress bar at a fixed
 * percentage. None of those is a fact about this workspace, so none of them
 * is drawn. A metric this schema cannot yet compute renders its card in full
 * with an honest "not yet available" body — the layout is the design's, the
 * content is the truth — and a chart with no history renders its frame and
 * says so rather than inventing bars.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getWorkspaceContext } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { useTerminology } from '../../terminology'
import './EnergyOverview.css'

const VIEW_RATE_REQUESTS = 'rate-requests'
const VIEW_RENEWALS = 'renewals'
const VIEW_CONCIERGE = 'move-concierge'

function num(n) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US')
}

function statValue(payload, label) {
  if (!payload || !Array.isArray(payload.stats)) return null
  const hit = payload.stats.find(s => s.label === label)
  return hit ? hit.value : null
}

function greeting(date) {
  const h = date.getHours()
  if (h < 12) return 'Good morning'
  return h < 17 ? 'Good afternoon' : 'Good evening'
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase()
}

export default function EnergyOverview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const authority = useWorkspaceAuthority()
  const terminology = useTerminology()
  const branding = authority.branding
  const hasLeads = authority.isFeatureEnabled('leads')

  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  const [views, setViews] = useState([])
  const [briefing, setBriefing] = useState(null)
  const [rateRequests, setRateRequests] = useState(null)
  const [renewals, setRenewals] = useState(null)
  const [concierge, setConcierge] = useState(null)
  const [spark, setSpark] = useState(null)
  const [recent, setRecent] = useState([])
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
    const attempt = (label, promise, fallback) =>
      promise.catch(e => {
        const status = e?.status ?? e?.response?.status
        if (!(status === 402 || status === 403 || status === 404)) {
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
      view(VIEW_CONCIERGE),
      hasLeads ? attempt('sparklines', api.get('/leads/sparklines?days=7'), null) : skip(null),
      hasLeads ? attempt('recent leads', api.get('/leads/?page=1&page_size=8'), null) : skip(null),
      attempt('launch', api.get('/launch/me', { skipRedirect: true }), null),
    ]).then(([v, b, rr, rn, mc, sp, rl, lc]) => {
      if (!live) return
      setViews(Array.isArray(v?.views) ? v.views : [])
      setBriefing(b)
      setRateRequests(rr)
      setRenewals(rn)
      setConcierge(mc)
      setSpark(sp)
      setRecent(Array.isArray(rl?.items) ? rl.items : [])
      setLaunch(lc)
      setLoadError(unexpected > 0 ? 'Some workspace data is unavailable.' : '')
      setLoading(false)
    })
    return () => { live = false }
  }, [identityKey, workspaceKey, hasLeads])

  const hasView = (key) => views.some(v => v.key === key)
  const go = (path) => navigate(path)
  function runSearch(e) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/leads?q=${encodeURIComponent(q)}` : '/leads')
  }

  const firstName = (user?.full_name || '').trim().split(/\s+/)[0] || 'there'
  const orgName = terminology.orgName || branding?.brand_name || 'this workspace'

  // ── THE FOUR COUNTERS ────────────────────────────────────────────────────
  //
  // Each one carries where it came from, because a tile whose provenance is
  // not obvious is the tile somebody eventually invents a number for.
  //
  // ENROLMENTS THIS MONTH is the one the schema cannot answer yet: nothing
  // records WHEN an account reached a signed contract, only that it is at
  // that tier now, so a month-to-date figure would have to be guessed. It
  // renders as the design's card with an honest body instead.
  const renewalDue = statValue(renewals, 'Renewal Due')
  const underContract = statValue(renewals, 'Under Contract')
  const kpis = [
    {
      key: 'new-leads',
      label: 'New Leads',
      tag: 'Last 24h',
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
      value: num(rateRequests?.total ?? null),
      sub: rateRequests
        ? `${num(statValue(rateRequests, 'Options Sent') ?? 0)} waiting on the customer · ${num(statValue(rateRequests, 'Unassigned') ?? 0)} unassigned`
        : 'This screen is not configured for this workspace.',
      to: hasView(VIEW_RATE_REQUESTS) ? `/view/${VIEW_RATE_REQUESTS}` : null,
      tone: 'cyan',
    },
    {
      key: 'enrollments',
      label: 'Enrollments This Month',
      tag: 'Not yet tracked',
      value: null,
      sub: 'Contracts record the tier an account is at, not the date it got '
         + 'there, so a month-to-date figure cannot be counted yet.',
      to: null,
      tone: 'muted',
    },
    {
      key: 'renewals',
      label: 'Renewal Watch',
      tag: hasView(VIEW_RENEWALS) ? 'Renewal window' : null,
      value: num(renewalDue ?? null),
      sub: renewals
        ? `${num(underContract ?? 0)} under contract in total.`
        : 'This screen is not configured for this workspace.',
      to: hasView(VIEW_RENEWALS) ? `/view/${VIEW_RENEWALS}` : null,
      tone: 'amber',
    },
  ]

  // ── NEEDS ATTENTION ──────────────────────────────────────────────────────
  //
  // Real conditions only, and a condition at zero is not listed. Four rows
  // reading "0 of these" is not a priority queue; an empty queue is the
  // honest answer to "what needs a person right now".
  const attention = []
  const repliesWaiting = briefing?.replies_needing_attention ?? 0
  const followUpsDue = briefing?.cadence_touches_due_today ?? 0
  const unassignedRates = statValue(rateRequests, 'Unassigned') ?? 0
  const conciergeActive = statValue(concierge, 'Active') ?? 0
  if (repliesWaiting > 0) attention.push({
    key: 'replies', tone: 'blue', icon: 'mail',
    title: `${repliesWaiting} customer ${repliesWaiting === 1 ? 'reply needs' : 'replies need'} a decision`,
    sub: 'Answered a plan comparison or a question and is waiting on you.',
    to: '/replies?needs_attention=true', cta: 'Open',
  })
  if (unassignedRates > 0) attention.push({
    key: 'rates', tone: 'cyan', icon: 'zap',
    title: `${unassignedRates} rate ${unassignedRates === 1 ? 'request has' : 'requests have'} no owner`,
    sub: 'Nobody is working these, so nothing will happen to them.',
    to: `/view/${VIEW_RATE_REQUESTS}`, cta: 'Assign',
  })
  if (followUpsDue > 0) attention.push({
    key: 'followups', tone: 'amber', icon: 'clock',
    title: `${followUpsDue} follow-${followUpsDue === 1 ? 'up is' : 'ups are'} due today`,
    sub: 'Scheduled touches that have reached their date.',
    to: '/workqueue', cta: 'Work queue',
  })
  if (conciergeActive > 0) attention.push({
    key: 'concierge', tone: 'blue', icon: 'truck',
    title: `${conciergeActive} Move Concierge ${conciergeActive === 1 ? 'handoff' : 'handoffs'} in progress`,
    sub: 'Energy plus the rest of the move, still open.',
    to: `/view/${VIEW_CONCIERGE}`, cta: 'Open',
  })
  if (renewalDue > 0) attention.push({
    key: 'renewals', tone: 'amber', icon: 'refresh',
    title: `${renewalDue} ${renewalDue === 1 ? 'account is' : 'accounts are'} inside the renewal window`,
    sub: 'Reached before the contract date rather than after it.',
    to: `/view/${VIEW_RENEWALS}`, cta: 'Review',
  })

  // ── LEAD → ENROLMENT ACTIVITY ────────────────────────────────────────────
  //
  // Server-computed daily counts, oldest to newest, zeros included. The
  // endpoint returns real history or nothing; this draws whichever it got and
  // never fills a gap. A week with no movement renders the frame and says so,
  // because a flat row of bars at zero reads as a broken chart.
  const series = useMemo(() => {
    const leadsIn = Array.isArray(spark?.leads_imported) ? spark.leads_imported : []
    const booked = Array.isArray(spark?.bookings) ? spark.bookings : []
    const days = Math.max(leadsIn.length, booked.length)
    if (!days) return { days: [], peak: 0, total: 0 }
    const today = new Date()
    const out = []
    let peak = 0
    let total = 0
    for (let i = 0; i < days; i += 1) {
      const d = new Date(today)
      d.setDate(today.getDate() - (days - 1 - i))
      const leads = Number(leadsIn[i] || 0)
      const enrolled = Number(booked[i] || 0)
      peak = Math.max(peak, leads, enrolled)
      total += leads + enrolled
      out.push({ label: d.toLocaleDateString(undefined, { weekday: 'short' }), leads, enrolled })
    }
    return { days: out, peak, total }
  }, [spark])

  // ── LAUNCH READINESS ─────────────────────────────────────────────────────
  //
  // THE LAUNCH EXPERIENCE IS THE SOURCE OF TRUTH AND IS NOT REBUILT HERE.
  // `overview.overall_pct` is computed once on the server and handed to both
  // the ring on the Launch screen and this bar, which is exactly why it is
  // read rather than recalculated: two components each averaging their own
  // copy is how a screen ends up showing 60% beside 62%.
  const launchPct = launch?.overview?.overall_pct
  const launchSteps = Array.isArray(launch?.overview?.steps) ? launch.overview.steps : []
  const launchStatus = launch?.implementation?.status || null

  const tierLabel = useMemo(() => {
    const map = new Map((terminology.tiers || []).map(t => [t.value, t.label]))
    return (value) => (value ? (map.get(value) || String(value).replace(/_/g, ' ')) : '—')
  }, [terminology.tiers])

  const leadName = (l) =>
    [l.first_name, l.last_name].filter(Boolean).join(' ') || l.phone || l.email || 'Unnamed'

  return (
    <div className="eo">
      {/* ── HEADER ─────────────────────────────────────────────────────── */}
      <header className="eo-header">
        <div className="eo-header-title">
          <h1>Operations Overview</h1>
          <p>Today&apos;s leads, customers, enrollments and items needing attention.</p>
        </div>
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

      {loadError && <div className="eo-notice">{loadError}</div>}

      {/* ── GREETING ───────────────────────────────────────────────────── */}
      <section className="eo-greeting">
        <h2>{greeting(now)}, {firstName}.</h2>
        <p>Here is what needs attention across {orgName} today.</p>
      </section>

      {/* ── KPI ROW ────────────────────────────────────────────────────── */}
      <section className="eo-kpis">
        {kpis.map(kpi => (
          <article key={kpi.key}
                   className={`eo-kpi eo-kpi--${kpi.tone}${kpi.to ? ' eo-kpi--link' : ''}`}
                   onClick={kpi.to ? () => go(kpi.to) : undefined}
                   role={kpi.to ? 'button' : undefined}
                   tabIndex={kpi.to ? 0 : undefined}
                   onKeyDown={kpi.to ? (e => { if (e.key === 'Enter') go(kpi.to) }) : undefined}>
            <div className="eo-kpi-top">
              <span className="eo-kpi-label">{kpi.label}</span>
              {kpi.tag && <span className="eo-kpi-tag">{kpi.tag}</span>}
            </div>
            <div className={`eo-kpi-value${kpi.value === null ? ' eo-kpi-value--none' : ''}`}>
              {loading ? '·' : (kpi.value === null ? 'Not yet available' : kpi.value)}
            </div>
            <div className="eo-kpi-sub">{kpi.sub}</div>
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
          ) : attention.length === 0 ? (
            <div className="eo-empty">
              <strong>Nothing is waiting on a person right now.</strong>
              <span>No unanswered replies, no rate request without an owner,
                    no follow-up past its date and nothing inside the renewal window.</span>
            </div>
          ) : (
            <ul className="eo-attn">
              {attention.map(item => (
                <li key={item.key} className={`eo-attn-row eo-attn-row--${item.tone}`}>
                  <span className="eo-attn-dot" aria-hidden="true" />
                  <div className="eo-attn-body">
                    <span className="eo-attn-title">{item.title}</span>
                    <span className="eo-attn-sub">{item.sub}</span>
                  </div>
                  <button type="button" className="eo-attn-cta" onClick={() => go(item.to)}>
                    {item.cta} →
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="eo-card">
          <div className="eo-card-head">
            <h3>Lead → Enrollment Activity</h3>
            <span className="eo-card-note">Last 7 days</span>
          </div>
          {loading ? (
            <div className="eo-empty">Loading…</div>
          ) : series.days.length === 0 ? (
            <div className="eo-empty">
              <strong>No activity history yet.</strong>
              <span>This chart draws real daily counts. It fills in as leads
                    arrive and enrollments are booked.</span>
            </div>
          ) : (
            <>
              <div className="eo-chart" role="img"
                   aria-label={`Daily new leads and enrollments over the last ${series.days.length} days`}>
                {series.days.map((d, i) => (
                  <div className="eo-chart-day" key={i}>
                    <div className="eo-chart-bars">
                      <span className="eo-bar eo-bar--leads"
                            style={{ height: series.peak ? `${(d.leads / series.peak) * 100}%` : '0%' }}
                            title={`${d.leads} new lead${d.leads === 1 ? '' : 's'}`} />
                      <span className="eo-bar eo-bar--enrolled"
                            style={{ height: series.peak ? `${(d.enrolled / series.peak) * 100}%` : '0%' }}
                            title={`${d.enrolled} enrollment${d.enrolled === 1 ? '' : 's'}`} />
                    </div>
                    <span className="eo-chart-label">{d.label}</span>
                  </div>
                ))}
              </div>
              <div className="eo-legend">
                <span><i className="eo-key eo-key--leads" /> New leads</span>
                <span><i className="eo-key eo-key--enrolled" /> Enrollments</span>
                {series.total === 0 && (
                  <span className="eo-legend-note">
                    Nothing recorded in this window — every day is a real zero.
                  </span>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {/* ── RECENT LEADS + LAUNCH READINESS ────────────────────────────── */}
      <div className="eo-grid eo-grid--split">
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
              <strong>No leads yet.</strong>
              <span>Enquiries from the website, referrals and imports appear
                    here the moment they arrive.</span>
            </div>
          ) : (
            <div className="eo-tablewrap">
              <table className="eo-table">
                <thead>
                  <tr><th>Customer</th><th>Need</th><th>Source</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {recent.slice(0, 6).map(l => (
                    <tr key={l.id} onClick={() => go('/leads/' + l.id)}>
                      <td><strong>{leadName(l)}</strong></td>
                      <td className="eo-td-soft">{tierLabel(l.tier)}</td>
                      <td className="eo-td-soft">
                        {l.import_list_name || l.source_file || '—'}
                      </td>
                      <td>
                        <span className={`eo-pill eo-pill--${l.status || 'off'}`}>
                          {String(l.status || '—').replace(/_/g, ' ')}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="eo-card eo-card--launch">
          <div className="eo-card-head">
            <h3>Launch Readiness</h3>
            {launch && (
              <button type="button" className="eo-link eo-link--invert"
                      onClick={() => go('/launch')}>
                Launch Center →
              </button>
            )}
          </div>
          {loading ? (
            <div className="eo-empty eo-empty--invert">Loading…</div>
          ) : !launch ? (
            <div className="eo-empty eo-empty--invert">
              <strong>No launch is open for this workspace.</strong>
              <span>Readiness appears here while an implementation is running.</span>
            </div>
          ) : (
            <>
              <p className="eo-launch-lede">
                Progress is read from the Launch Center, not recalculated here,
                so this bar and that screen can never disagree.
              </p>
              <div className="eo-launch-bar" role="img"
                   aria-label={`Launch readiness ${launchPct ?? 0} percent`}>
                <span style={{ width: `${Math.max(0, Math.min(100, launchPct ?? 0))}%` }} />
              </div>
              <div className="eo-launch-meta">
                <span className="eo-launch-pct">{launchPct ?? 0}%</span>
                <span>
                  {launch.overview?.complete_steps ?? 0} of{' '}
                  {launch.overview?.total_steps ?? launchSteps.length} steps complete
                  {launchStatus ? ` · ${String(launchStatus).replace(/_/g, ' ')}` : ''}
                </span>
              </div>
              {launchSteps.length > 0 && (
                <div className="eo-launch-steps">
                  {launchSteps.slice(0, 4).map(s => (
                    <div key={s.key} className="eo-launch-step">
                      <span className="eo-launch-step-name">{s.label}</span>
                      <span className="eo-launch-step-state">
                        {s.pct >= 100 ? 'Complete' : s.pct > 0 ? `${s.pct}%` : 'Not started'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
