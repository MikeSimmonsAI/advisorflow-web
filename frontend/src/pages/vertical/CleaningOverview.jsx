/**
 * COMMERCIAL CLEANING — CLIENT DASHBOARD.
 *
 * The approved client-portal design, rendered from THIS workspace's real
 * records. Not a second dashboard engine: every panel below reads an endpoint
 * the platform already serves, through the same authorized scope every other
 * screen uses, so what a client sees here and what they see on the screen it
 * links to can never disagree — and a client can never be shown a record the
 * Prospects page would not show them.
 *
 * ── Sources. Every number on this page names one. ──────────────────────────
 *   GET /workspace-views                which configured screens exist here
 *   GET /workspace-views/prospects      total + Not Yet Worked / Walkthrough
 *                                       Set / Proposal Sent / Contract Won
 *   GET /workspace-views/follow-up      total + Replied / Awaiting Reply
 *   GET /workspace-views/walkthroughs   total + Upcoming / Confirmed /
 *                                       Awaiting Confirmation / Completed /
 *                                       Awaiting Outcome / Cancelled
 *   GET /pipeline/stats                 by_stage, for the interested count
 *   GET /activity/today                 what was actually sent today
 *   GET /leads/?page=1&page_size=8      the recently-worked table
 *
 * ── The rule this page is built on ────────────────────────────────────────
 * NO NUMBER WITHOUT A SOURCE, AND NO SHAPE WITHOUT A NUMBER. The approved
 * concept shows 631 prospects, 93% coverage and 17 walkthroughs. Those are
 * sample data for a demo account and not one of them is a fact about any real
 * workspace, so none of them is drawn. A metric this schema cannot compute
 * renders its card in full with an honest "not yet available" body — the
 * layout is the design's, the content is the truth — and a funnel with
 * nothing in it renders its frame and says so rather than inventing a shape.
 *
 * ── The one number this page refuses to estimate ──────────────────────────
 * WALKTHROUGHS COMPLETED comes from `pipeline_conversations`, where a person
 * recorded that the visit happened. It is never inferred from a booked time
 * having passed: `booking_links` carries no attendance, and a dashboard that
 * promoted yesterday's bookings to attendances would hand a cleaning company
 * a perfect show rate on walkthroughs nobody turned up to. Visits whose time
 * has gone by with no outcome recorded are counted separately, and named as
 * the work queue they are.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getWorkspaceContext } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { useTerminology } from '../../terminology'
import './CleaningOverview.css'

const VIEW_PROSPECTS = 'prospects'
const VIEW_FOLLOW_UP = 'follow-up'
const VIEW_WALKTHROUGHS = 'walkthroughs'

// The pipeline stages that mean a prospect said yes to a conversation. Read
// from `pipeline_conversations.stage`, which is where a person put them.
const INTERESTED_STAGES = ['interested', 'walkthrough_offered']

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

export default function CleaningOverview() {
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
  const [prospects, setProspects] = useState(null)
  const [followUp, setFollowUp] = useState(null)
  const [walkthroughs, setWalkthroughs] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [activity, setActivity] = useState(null)
  const [recent, setRecent] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')

  // THE WORKSPACE AND THE IDENTITY ARE DEPENDENCIES, for the reason the
  // platform Overview records: switching customer does not remount this
  // component, so without them the previous customer's numbers stay on screen
  // after a switch — which in a portal one client logs into to see their own
  // account is not a cosmetic bug.
  const identityKey = `${user?.role || ''}|${user?.organization_id || ''}`
  const workspaceKey = getWorkspaceContext() || ''

  useEffect(() => {
    let live = true
    let unexpected = 0

    // A REFUSAL IS NOT AN ERROR. 402/403/404 mean the server is working and
    // this workspace does not have the module; the panel is simply absent, as
    // it is from the rail. Anything else is a real fault and is said once, in
    // one sentence, with the detail left in the console for whoever looks.
    const attempt = (label, promise, fallback) =>
      promise.catch(e => {
        const status = e?.status ?? e?.response?.status
        if (!(status === 402 || status === 403 || status === 404)) {
          unexpected += 1
          // eslint-disable-next-line no-console
          console.warn('[cleaning-overview] %s failed:', label, e?.message || e)
        }
        return fallback
      })
    const skip = (fallback) => Promise.resolve(fallback)
    const view = (key) => attempt(key, api.get(`/workspace-views/${key}?limit=6`), null)

    Promise.all([
      attempt('views', api.get('/workspace-views', { skipRedirect: true }), null),
      view(VIEW_PROSPECTS),
      view(VIEW_FOLLOW_UP),
      view(VIEW_WALKTHROUGHS),
      attempt('pipeline', api.get('/pipeline/stats'), null),
      attempt('activity', api.get('/activity/today?limit=200'), null),
      hasLeads ? attempt('recent prospects', api.get('/leads/?page=1&page_size=8'), null)
               : skip(null),
    ]).then(([v, pr, fu, wt, pl, ac, rl]) => {
      if (!live) return
      setViews(Array.isArray(v?.views) ? v.views : [])
      setProspects(pr)
      setFollowUp(fu)
      setWalkthroughs(wt)
      setPipeline(pl)
      setActivity(ac)
      setRecent(Array.isArray(rl?.items) ? rl.items : [])
      setLoadError(unexpected > 0 ? 'Some account data is unavailable.' : '')
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
  const orgName = terminology.orgName || branding?.brand_name || 'this account'

  // ── THE FIVE COUNTERS ────────────────────────────────────────────────────
  //
  // Each one carries where it came from, because a tile whose provenance is
  // not obvious is the tile somebody eventually invents a number for.
  const sourced = prospects?.total ?? null
  const notYetWorked = statValue(prospects, 'Not Yet Worked')
  // WORKED IS A SUBTRACTION, NOT A TOUCH COUNT. The platform records which
  // stage a prospect is in, not who moved it, so "worked" here means exactly
  // "no longer sitting at New Prospect" — which is what the subtitle says.
  const worked = (sourced === null || notYetWorked === null)
    ? null
    : Math.max(0, sourced - notYetWorked)
  const coverage = (worked === null || !sourced) ? null : Math.round((worked / sourced) * 100)

  const followUpTotal = followUp?.total ?? null

  const byStage = pipeline && typeof pipeline.by_stage === 'object' ? pipeline.by_stage : null
  const interested = byStage
    ? INTERESTED_STAGES.reduce((sum, s) => sum + Number(byStage[s] || 0), 0)
    : null

  const wtConfirmed = statValue(walkthroughs, 'Confirmed')
  const wtAwaitingConfirmation = statValue(walkthroughs, 'Awaiting Confirmation')
  const wtUpcoming = statValue(walkthroughs, 'Upcoming')
  const wtCompleted = statValue(walkthroughs, 'Completed')
  const wtAwaitingOutcome = statValue(walkthroughs, 'Awaiting Outcome')
  const wtCancelled = statValue(walkthroughs, 'Cancelled')
  // BOOKED IS THE TWO STATUSES THAT MEAN A TIME EXISTS, and nothing else. A
  // booking link that was sent and never acted on is not a walkthrough; the
  // server's own filter already excludes it, and this adds the two counters
  // rather than reading `total`, which includes cancelled ones.
  const booked = (wtConfirmed === null && wtAwaitingConfirmation === null)
    ? null
    : Number(wtConfirmed || 0) + Number(wtAwaitingConfirmation || 0)

  const kpis = [
    {
      key: 'sourced',
      label: 'Prospects Sourced',
      tag: hasView(VIEW_PROSPECTS) ? 'On the account' : null,
      value: num(sourced),
      sub: prospects
        ? 'Businesses on this account right now, excluding dead and do-not-contact.'
        : 'This screen is not configured for this account.',
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null,
      tone: 'blue',
    },
    {
      key: 'worked',
      label: 'Prospects Worked',
      tag: coverage === null ? null : `${coverage}% of the list`,
      value: num(worked),
      sub: 'Moved past New Prospect by somebody on the account. The platform '
         + 'records the stage, not who moved it.',
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null,
      tone: 'cyan',
    },
    {
      key: 'follow-up',
      label: 'Follow-Ups',
      tag: hasView(VIEW_FOLLOW_UP) ? 'Active queue' : null,
      value: num(followUpTotal),
      sub: followUp
        ? `${num(statValue(followUp, 'Replied') ?? 0)} replied · ${num(statValue(followUp, 'Quiet 7+ Days') ?? 0)} quiet 7+ days`
        : 'This screen is not configured for this account.',
      to: hasView(VIEW_FOLLOW_UP) ? `/view/${VIEW_FOLLOW_UP}` : null,
      tone: 'amber',
    },
    {
      key: 'interested',
      label: 'Interested',
      tag: interested === null ? 'Not yet available' : 'In the pipeline',
      value: interested === null ? null : num(interested),
      sub: interested === null
        ? 'This counts prospects a person moved to Interested on the pipeline '
        + 'board. The board is not answering for this account right now.'
        : 'Prospects a person moved to Interested or Walkthrough Offered.',
      to: interested === null ? null : '/pipeline',
      tone: 'green',
    },
    {
      key: 'walkthroughs',
      label: 'Walkthroughs Booked',
      tag: hasView(VIEW_WALKTHROUGHS) ? 'Time on the calendar' : null,
      value: num(booked),
      sub: walkthroughs
        ? `${num(wtUpcoming ?? 0)} still upcoming. A booking link that was sent and not acted on is not counted.`
        : 'This screen is not configured for this account.',
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null,
      tone: 'blue',
    },
  ]

  // ── THE WORKFLOW, AS FIVE REAL NUMBERS ───────────────────────────────────
  //
  // Widths are proportions of the widest stage, so the shape is the account's
  // own and not a decorative taper. A stage the platform cannot answer is
  // carried through as null and drawn as a gap rather than a zero, because
  // "we cannot count this" and "this is empty" are different facts.
  const funnel = [
    { key: 'sourced', label: 'Sourced', value: sourced,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null },
    { key: 'worked', label: 'Worked', value: worked,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null },
    { key: 'follow-up', label: 'Follow-Up', value: followUpTotal,
      to: hasView(VIEW_FOLLOW_UP) ? `/view/${VIEW_FOLLOW_UP}` : null },
    { key: 'interested', label: 'Interested', value: interested,
      to: interested === null ? null : '/pipeline' },
    { key: 'booked', label: 'Booked', value: booked,
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null },
  ]
  const funnelPeak = funnel.reduce(
    (max, s) => (s.value === null ? max : Math.max(max, s.value)), 0)
  const funnelHasAnything = funnel.some(s => (s.value ?? 0) > 0)

  // ── WALKTHROUGH OUTCOMES ─────────────────────────────────────────────────
  //
  // The corrected truth, on the front page, because it is the number this
  // business judges the whole service by. Each row is a counter the server
  // computed through the same authorized query as the screen it links to.
  const outcomes = [
    { key: 'upcoming', label: 'Upcoming', value: wtUpcoming, tone: 'blue',
      note: 'A time on the calendar, still ahead.' },
    { key: 'confirmed', label: 'Confirmed by the prospect', value: wtConfirmed, tone: 'cyan',
      note: 'They confirmed the time.' },
    { key: 'awaiting-confirmation', label: 'Awaiting confirmation', value: wtAwaitingConfirmation, tone: 'amber',
      note: 'Booked, not yet confirmed.' },
    { key: 'completed', label: 'Completed', value: wtCompleted, tone: 'green',
      note: 'Somebody recorded that the visit happened.' },
    { key: 'awaiting-outcome', label: 'Awaiting an outcome', value: wtAwaitingOutcome, tone: 'amber',
      note: 'The time has passed and nobody has said what happened yet.' },
    { key: 'cancelled', label: 'Cancelled', value: wtCancelled, tone: 'red',
      note: 'Booked, then called off.' },
  ]

  // ── TODAY'S OUTREACH ─────────────────────────────────────────────────────
  //
  // `GET /activity/today` answers the day boundary on the SERVER, in the
  // business's own timezone, over every row rather than the first page of
  // them. The older screen fetched 300 rows from a 30-day endpoint and
  // filtered them in the browser, which undercounted silently.
  const sentToday = activity?.total ?? null
  const leadsContacted = activity?.leads_contacted ?? null
  const contactedTwice = activity?.contacted_more_than_once ?? null
  const activityItems = Array.isArray(activity?.items) ? activity.items : []

  const tierLabel = useMemo(() => {
    const map = new Map((terminology.tiers || []).map(t => [t.value, t.label]))
    return (value) => (value ? (map.get(value) || String(value).replace(/_/g, ' ')) : '—')
  }, [terminology.tiers])

  const leadName = (l) =>
    [l.first_name, l.last_name].filter(Boolean).join(' ') || l.company
    || l.phone || l.email || 'Unnamed'

  return (
    <div className="co">
      {/* ── HEADER ─────────────────────────────────────────────────────── */}
      <header className="co-header">
        <div className="co-header-title">
          <h1>Client Dashboard</h1>
          <p>{orgName} — prospects, outreach, follow-up and booked walkthroughs.</p>
        </div>
        <div className="co-header-actions">
          <form className="co-search" onSubmit={runSearch}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search a business, contact, phone or email"
              aria-label="Search prospects"
            />
          </form>
          {hasView(VIEW_WALKTHROUGHS) && (
            <button type="button" className="co-btn co-btn--primary"
                    onClick={() => go(`/view/${VIEW_WALKTHROUGHS}`)}>
              Walkthroughs
            </button>
          )}
          <div className="co-avatar" title={user?.full_name || 'Signed in'}>
            {initials(user?.full_name)}
          </div>
        </div>
      </header>

      {loadError && <div className="co-notice">{loadError}</div>}

      {/* ── GREETING ───────────────────────────────────────────────────── */}
      <section className="co-greeting">
        <h2>{greeting(now)}, {firstName}.</h2>
        <p>Here is where {orgName} stands today.</p>
      </section>

      {/* ── KPI ROW ────────────────────────────────────────────────────── */}
      <section className="co-kpis">
        {kpis.map(kpi => (
          <article key={kpi.key}
                   className={`co-kpi co-kpi--${kpi.tone}${kpi.to ? ' co-kpi--link' : ''}`}
                   onClick={kpi.to ? () => go(kpi.to) : undefined}
                   role={kpi.to ? 'button' : undefined}
                   tabIndex={kpi.to ? 0 : undefined}
                   onKeyDown={kpi.to ? (e => { if (e.key === 'Enter') go(kpi.to) }) : undefined}>
            <div className="co-kpi-top">
              <span className="co-kpi-label">{kpi.label}</span>
              {kpi.tag && <span className="co-kpi-tag">{kpi.tag}</span>}
            </div>
            <div className={`co-kpi-value${kpi.value === null ? ' co-kpi-value--none' : ''}`}>
              {loading ? '·' : (kpi.value === null ? 'Not yet available' : kpi.value)}
            </div>
            <div className="co-kpi-sub">{kpi.sub}</div>
          </article>
        ))}
      </section>

      {/* ── WORKFLOW ───────────────────────────────────────────────────── */}
      <section className="co-card">
        <div className="co-card-head">
          <h3>Your Workflow</h3>
          <span className="co-card-note">Sourced → worked → follow-up → interested → booked</span>
        </div>
        {loading ? (
          <div className="co-empty">Loading…</div>
        ) : !funnelHasAnything ? (
          <div className="co-empty">
            <strong>Nothing has moved through the workflow yet.</strong>
            <span>These bars are counted from this account&apos;s own records.
                  They fill in as prospects are sourced, worked and booked.</span>
          </div>
        ) : (
          <ol className="co-funnel">
            {funnel.map((stage, i) => (
              <li key={stage.key}
                  className={`co-funnel-step${stage.to ? ' co-funnel-step--link' : ''}`}
                  onClick={stage.to ? () => go(stage.to) : undefined}
                  role={stage.to ? 'button' : undefined}
                  tabIndex={stage.to ? 0 : undefined}
                  onKeyDown={stage.to ? (e => { if (e.key === 'Enter') go(stage.to) }) : undefined}>
                <span className="co-funnel-rank">{i + 1}</span>
                <span className="co-funnel-label">{stage.label}</span>
                <span className="co-funnel-track">
                  {stage.value === null ? (
                    <span className="co-funnel-gap">not counted</span>
                  ) : (
                    <span className="co-funnel-fill"
                          style={{ width: funnelPeak ? `${(stage.value / funnelPeak) * 100}%` : '0%' }} />
                  )}
                </span>
                <span className="co-funnel-value">
                  {stage.value === null ? '—' : num(stage.value)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* ── WALKTHROUGH OUTCOMES + TODAY'S OUTREACH ────────────────────── */}
      <div className="co-grid co-grid--split">
        <section className="co-card">
          <div className="co-card-head">
            <h3>Walkthroughs</h3>
            {hasView(VIEW_WALKTHROUGHS) && (
              <button type="button" className="co-link"
                      onClick={() => go(`/view/${VIEW_WALKTHROUGHS}`)}>
                Open →
              </button>
            )}
          </div>
          {loading ? (
            <div className="co-empty">Loading…</div>
          ) : !walkthroughs ? (
            <div className="co-empty">
              <strong>The walkthroughs screen is not configured for this account.</strong>
            </div>
          ) : (
            <>
              <ul className="co-outcomes">
                {outcomes.map(row => (
                  <li key={row.key} className={`co-outcome co-outcome--${row.tone}`}>
                    <span className="co-outcome-dot" aria-hidden="true" />
                    <div className="co-outcome-body">
                      <span className="co-outcome-label">{row.label}</span>
                      <span className="co-outcome-note">{row.note}</span>
                    </div>
                    <span className="co-outcome-value">{num(row.value ?? 0)}</span>
                  </li>
                ))}
              </ul>
              <p className="co-footnote">
                Completed is what somebody recorded on the pipeline, never what
                the calendar implies. A visit whose time has passed with no
                outcome recorded is counted as awaiting one.
              </p>
            </>
          )}
        </section>

        <section className="co-card">
          <div className="co-card-head">
            <h3>Today&apos;s Outreach</h3>
            <button type="button" className="co-link" onClick={() => go('/activity')}>
              View activity →
            </button>
          </div>
          {loading ? (
            <div className="co-empty">Loading…</div>
          ) : !activity ? (
            <div className="co-empty">
              <strong>Activity is unavailable for this account right now.</strong>
            </div>
          ) : sentToday === 0 ? (
            <div className="co-empty">
              <strong>Nothing has been sent today.</strong>
              <span>This counts every message this account sent today, on the
                    business&apos;s own clock rather than your browser&apos;s.</span>
            </div>
          ) : (
            <>
              <div className="co-mini">
                <div className="co-mini-cell">
                  <span className="co-mini-value">{num(sentToday)}</span>
                  <span className="co-mini-label">Sent today</span>
                </div>
                <div className="co-mini-cell">
                  <span className="co-mini-value">{num(leadsContacted)}</span>
                  <span className="co-mini-label">Businesses contacted</span>
                </div>
                <div className="co-mini-cell">
                  <span className="co-mini-value">{num(contactedTwice)}</span>
                  <span className="co-mini-label">Contacted more than once</span>
                </div>
              </div>
              <ul className="co-feed">
                {activityItems.slice(0, 5).map(item => (
                  <li key={item.id} className="co-feed-row"
                      onClick={() => go('/leads/' + item.lead_id)}>
                    <span className={`co-feed-channel co-feed-channel--${item.channel}`}>
                      {item.channel}
                    </span>
                    <span className="co-feed-name">{item.lead_name || 'Unnamed'}</span>
                    <span className="co-feed-preview">{item.preview || '—'}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>

      {/* ── RECENTLY WORKED PROSPECTS ──────────────────────────────────── */}
      <section className="co-card">
        <div className="co-card-head">
          <h3>Recently Worked Prospects</h3>
          {hasView(VIEW_PROSPECTS) && (
            <button type="button" className="co-link"
                    onClick={() => go(`/view/${VIEW_PROSPECTS}`)}>
              View all →
            </button>
          )}
        </div>
        {!hasLeads ? (
          <div className="co-empty">
            <strong>The prospect module is not enabled for this account.</strong>
          </div>
        ) : loading ? (
          <div className="co-empty">Loading…</div>
        ) : recent.length === 0 ? (
          <div className="co-empty">
            <strong>No prospects on this account yet.</strong>
            <span>Sourced businesses and website enquiries appear here the
                  moment they arrive.</span>
          </div>
        ) : (
          <div className="co-tablewrap">
            <table className="co-table">
              <thead>
                <tr><th>Business</th><th>Stage</th><th>Source list</th><th>Status</th></tr>
              </thead>
              <tbody>
                {recent.slice(0, 6).map(l => (
                  <tr key={l.id} onClick={() => go('/leads/' + l.id)}>
                    <td><strong>{leadName(l)}</strong></td>
                    <td className="co-td-soft">{tierLabel(l.tier)}</td>
                    <td className="co-td-soft">
                      {l.import_list_name || l.source_file || '—'}
                    </td>
                    <td>
                      <span className={`co-pill co-pill--${l.status || 'off'}`}>
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
    </div>
  )
}
