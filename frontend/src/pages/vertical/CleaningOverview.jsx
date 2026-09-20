/**
 * COMMERCIAL CLEANING — CLIENT DASHBOARD.
 *
 * THE APPROVED CLIENT-PORTAL STRUCTURE (SS2), RENDERED FROM THIS WORKSPACE'S
 * REAL RECORDS (SS3). The two are not in tension: SS2 decides what goes where
 * and what it is called — eyebrow, account name, five counters, Your Workflow,
 * Recently Worked Prospects, Recent VA Activity — and every value inside that
 * frame is read from an endpoint the platform already serves, through the same
 * authorized scope every other screen uses. So what a client sees here and
 * what they see on the screen it links to can never disagree, and a client can
 * never be shown a record the Prospects page would not show them.
 *
 * ── Sources. Every number on this page names one. ──────────────────────────
 *   GET /workspace-views                which configured screens exist here
 *   GET /workspace-views/prospects      total + Not Yet Worked / Walkthrough
 *                                       Set / Proposal Sent / Contract Won,
 *                                       and the rows themselves, which the
 *                                       server returns updated_at DESC —
 *                                       which is what "recently worked" means
 *   GET /workspace-views/follow-up      total + Replied / Awaiting Reply
 *   GET /workspace-views/walkthroughs   total + Upcoming / Confirmed /
 *                                       Awaiting Confirmation / Completed /
 *                                       Awaiting Outcome / Cancelled
 *   GET /pipeline/stats                 by_stage, for the interested count
 *   GET /activity/today                 what was actually sent today, by whom
 *
 * ── The rule this page is built on ────────────────────────────────────────
 * NO NUMBER WITHOUT A SOURCE, AND NO SHAPE WITHOUT A NUMBER. The approved
 * concept shows a demo company's name, 631 prospects, 93% coverage and 17
 * walkthroughs. Those are demo content and not one of them is a fact about any
 * real workspace, so none of them is drawn — the account name in the header is
 * the ACTIVE workspace's own, never a literal. A metric this schema cannot
 * compute renders its card in full with an honest "not yet available" body,
 * and a workflow with nothing in it renders its frame and says so rather than
 * inventing a shape.
 *
 * ── The one number this page refuses to estimate ──────────────────────────
 * WALKTHROUGHS COMPLETED comes from `pipeline_conversations`, where a person
 * recorded that the visit happened. It is never inferred from a booked time
 * having passed: `booking_links` carries no attendance, and a dashboard that
 * promoted yesterday's bookings to attendances would hand a cleaning company
 * a perfect show rate on walkthroughs nobody turned up to. Visits whose time
 * has gone by with no outcome recorded are counted separately, and named as
 * the work queue they are.
 *
 * ── Scope ─────────────────────────────────────────────────────────────────
 * Every request on this page is answered through `lead_scope`, which resolves
 * the ACTIVE workspace — not the caller's privileges. A platform administrator
 * standing inside a customer's workspace reads that customer's account and
 * nothing else, and there is no banner here offering otherwise. Leaving or
 * switching the workspace is a deliberate act taken elsewhere.
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

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase()
}

function ago(iso) {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return null
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

export default function CleaningOverview() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const authority = useWorkspaceAuthority()
  const terminology = useTerminology()
  const branding = authority.branding
  const hasLeads = authority.isFeatureEnabled('leads')

  const [views, setViews] = useState([])
  const [prospects, setProspects] = useState(null)
  const [followUp, setFollowUp] = useState(null)
  const [walkthroughs, setWalkthroughs] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [activity, setActivity] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')

  // THE WORKSPACE AND THE IDENTITY ARE DEPENDENCIES, for the reason the
  // platform Overview records: switching customer does not remount this
  // component, so without them the previous customer's numbers stay on screen
  // after a switch — which in a portal one client logs into to see their own
  // account is not a cosmetic bug. `organization_id` is in the key as well,
  // because a god administrator entering a workspace changes the active
  // organization without changing either of the other two.
  const identityKey = `${user?.role || ''}|${user?.organization_id || ''}`
  const workspaceKey = getWorkspaceContext() || ''
  const orgKey = branding?.organization_id || ''

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
    const view = (key, limit) =>
      attempt(key, api.get(`/workspace-views/${key}?limit=${limit}`), null)

    Promise.all([
      attempt('views', api.get('/workspace-views', { skipRedirect: true }), null),
      // Six rows, because the Recently Worked panel below reads them. The
      // server returns leads updated_at DESC, so this is the same list the
      // Prospects screen opens on — not a second definition of "recent".
      view(VIEW_PROSPECTS, 6),
      view(VIEW_FOLLOW_UP, 1),
      view(VIEW_WALKTHROUGHS, 1),
      attempt('pipeline', api.get('/pipeline/stats'), null),
      attempt('activity', api.get('/activity/today?limit=200'), null),
    ]).then(([v, pr, fu, wt, pl, ac]) => {
      if (!live) return
      setViews(Array.isArray(v?.views) ? v.views : [])
      setProspects(pr)
      setFollowUp(fu)
      setWalkthroughs(wt)
      setPipeline(pl)
      setActivity(ac)
      setLoadError(unexpected > 0 ? 'Some account data is unavailable.' : '')
      setLoading(false)
    })
    return () => { live = false }
  }, [identityKey, workspaceKey, orgKey])

  const hasView = (key) => views.some(v => v.key === key)
  const go = (path) => navigate(path)
  function runSearch(e) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/leads?q=${encodeURIComponent(q)}` : '/leads')
  }

  // THE ACCOUNT NAME IS READ, NEVER WRITTEN. The approved concept carries a
  // demo customer's name in this position; this is whichever organization the
  // workspace actually resolved to, and it changes when the workspace does.
  const orgName = terminology.orgName || branding?.brand_name || 'This account'

  // ── THE FIVE COUNTERS, IN THE APPROVED ORDER ─────────────────────────────
  //
  // Each one carries where it came from, because a tile whose provenance is
  // not obvious is the tile somebody eventually invents a number for.
  const sourced = prospects?.total ?? null
  const notYetWorked = statValue(prospects, 'Not Yet Worked')
  // VA WORKED IS A SUBTRACTION, NOT A TOUCH COUNT. The platform records which
  // stage a prospect is in, not who moved it, so this means exactly "no longer
  // sitting at New Prospect" — which is what the note under it says.
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

  // "NOT CONFIGURED" IS A CLAIM, AND IT IS FALSE WHILE THE ANSWER IS STILL IN
  // FLIGHT. Each of these cards reads its own payload, which is null both
  // before the request returns and when the workspace genuinely has no such
  // screen. Saying the second thing during the first is how a dashboard tells
  // a client their account is misconfigured for a second and a half.
  const missing = (payload, sentence) =>
    (payload ? null : (loading ? 'Reading this account…' : sentence))

  const kpis = [
    {
      key: 'sourced',
      label: 'Prospects Sourced',
      value: num(sourced),
      note: missing(prospects, 'Screen not configured for this account')
        || 'on the account now',
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null,
      tone: 'blue',
      title: 'Businesses on this account, excluding dead and do-not-contact.',
    },
    {
      key: 'worked',
      label: 'VA Worked',
      value: num(worked),
      note: coverage === null ? 'past New Prospect' : `${coverage}% of the list`,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null,
      tone: 'cyan',
      title: 'Moved past New Prospect by somebody on the account. The platform '
           + 'records the stage, not who moved it.',
    },
    {
      key: 'follow-up',
      label: 'Follow-Ups',
      value: num(followUpTotal),
      note: missing(followUp, 'Screen not configured for this account')
        || `${num(statValue(followUp, 'Replied') ?? 0)} replied · ${num(statValue(followUp, 'Quiet 7+ Days') ?? 0)} quiet 7+ days`,
      to: hasView(VIEW_FOLLOW_UP) ? `/view/${VIEW_FOLLOW_UP}` : null,
      tone: 'amber',
      title: 'Conversations that are alive and owed a next action.',
    },
    {
      key: 'interested',
      label: 'Interested',
      value: interested === null ? null : num(interested),
      note: (loading && interested === null)
        ? 'Reading this account…'
        : (interested === null
            ? 'the pipeline board is not answering for this account right now'
            : 'moved to Interested by a person'),
      to: interested === null ? null : '/pipeline',
      tone: 'green',
      title: 'Prospects a person moved to Interested or Walkthrough Offered on '
           + 'the pipeline board.',
    },
    {
      key: 'walkthroughs',
      label: 'Walkthroughs',
      value: num(booked),
      note: missing(walkthroughs, 'Screen not configured for this account')
        || `booked · ${num(wtUpcoming ?? 0)} still upcoming`,
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null,
      tone: 'blue',
      title: 'A time on the calendar. A booking link that was sent and never '
           + 'acted on is not counted.',
    },
  ]

  // ── YOUR WORKFLOW ────────────────────────────────────────────────────────
  //
  // The five stages of the approved concept, named the way it names them, each
  // one a real count. The bar under each number is a proportion of the widest
  // stage, so the shape is the account's own and not a decorative taper. A
  // stage the platform cannot answer is carried through as null and drawn as a
  // gap rather than a zero, because "we cannot count this" and "this is empty"
  // are different facts.
  const workflow = [
    { key: 'sourced', label: 'Sourced', value: sourced,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null },
    { key: 'worked', label: 'VA Worked', value: worked,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null },
    { key: 'follow-up', label: 'Follow-Up', value: followUpTotal,
      to: hasView(VIEW_FOLLOW_UP) ? `/view/${VIEW_FOLLOW_UP}` : null },
    { key: 'interested', label: 'Interested', value: interested,
      to: interested === null ? null : '/pipeline' },
    { key: 'booked', label: 'Booked', value: booked,
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null },
  ]
  const workflowPeak = workflow.reduce(
    (max, s) => (s.value === null ? max : Math.max(max, s.value)), 0)
  const workflowHasAnything = workflow.some(s => (s.value ?? 0) > 0)

  // ── RECENTLY WORKED PROSPECTS ────────────────────────────────────────────
  //
  // The rows the Prospects screen itself would show first. `render()` orders
  // leads `updated_at DESC`, so "recently worked" is not a second definition
  // invented here — it is the same list, through the same authorized query,
  // six rows deep. The subtitle under each business is what the record says:
  // its stage, who owns it, and when it last moved. Nothing is narrated.
  const prospectRows = Array.isArray(prospects?.items) ? prospects.items : []

  // ── RECENT VA ACTIVITY ───────────────────────────────────────────────────
  //
  // WHO DID THE WORK, FROM THE ROWS THAT RECORD IT. `sent_by_name` is the
  // person who pressed send — kept apart on the server from the advisor the
  // prospect hears from, which is a different question and a different name.
  // Each line is a count of that person's own sends today and how many
  // distinct businesses they reached. There is no "worked this morning"
  // because the schema records sends, not shifts.
  const sentToday = activity?.total ?? null
  const leadsContacted = activity?.leads_contacted ?? null
  const contactedTwice = activity?.contacted_more_than_once ?? null
  const activityItems = Array.isArray(activity?.items) ? activity.items : []

  const byPerson = useMemo(() => {
    const acc = new Map()
    for (const item of activityItems) {
      const name = item.sent_by_name || 'Automation'
      if (!acc.has(name)) acc.set(name, { name, sends: 0, leads: new Set() })
      const row = acc.get(name)
      row.sends += 1
      if (item.lead_id) row.leads.add(item.lead_id)
    }
    return [...acc.values()]
      .map(r => ({ name: r.name, sends: r.sends, leads: r.leads.size }))
      .sort((a, b) => b.leads - a.leads || b.sends - a.sends)
  }, [activityItems])

  // ── WALKTHROUGH OUTCOMES ─────────────────────────────────────────────────
  //
  // The corrected truth, kept on the front page, because it is the number this
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

  const tierLabel = useMemo(() => {
    const map = new Map((terminology.tiers || []).map(t => [t.value, t.label]))
    return (value) => (value ? (map.get(value) || String(value).replace(/_/g, ' ')) : null)
  }, [terminology.tiers])

  // What a prospect row says about itself, in the record's own words.
  const prospectLine = (values) => {
    const parts = [
      tierLabel(values?.tier),
      values?.location || null,
      values?.owner ? `owned by ${values.owner}` : null,
      ago(values?.updated_at),
    ].filter(Boolean)
    return parts.length ? parts.join(' · ') : 'No stage recorded yet.'
  }

  return (
    <div className="co">
      {/* ── HEADER ─────────────────────────────────────────────────────── */}
      <header className="co-header">
        <div className="co-header-title">
          <div className="co-eyebrow">Client Dashboard</div>
          <h1>{orgName}</h1>
          {/* The approved concept prints the operator's product name here.
              It is a brand, and a brand is a row — the rail's wordmark
              already carries whichever one this workspace belongs to, so
              repeating it as a literal would be the same hard-coding the
              account name above was corrected for. */}
          <p>Your account at a glance — prospects, outreach, follow-up and
             booked walkthroughs.</p>
        </div>
        <div className="co-header-actions">
          {/* THE SCOPE, STATED ONCE, WHERE A BANNER USED TO CLAIM THE
              OPPOSITE. Everything below is this account and only this
              account, whoever is signed in. */}
          <span className="co-scope" title="Every number on this page is this organization's own.">
            This account only
          </span>
          {hasLeads && (
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
          )}
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

      {/* ── THE FIVE COUNTERS ──────────────────────────────────────────── */}
      <section className="co-kpis">
        {kpis.map(kpi => (
          <article key={kpi.key}
                   className={`co-kpi co-kpi--${kpi.tone}${kpi.to ? ' co-kpi--link' : ''}`}
                   title={kpi.title}
                   onClick={kpi.to ? () => go(kpi.to) : undefined}
                   role={kpi.to ? 'button' : undefined}
                   tabIndex={kpi.to ? 0 : undefined}
                   onKeyDown={kpi.to ? (e => { if (e.key === 'Enter') go(kpi.to) }) : undefined}>
            <div className={`co-kpi-value${kpi.value === null ? ' co-kpi-value--none' : ''}`}>
              {loading ? '·' : (kpi.value === null ? 'Not yet available' : kpi.value)}
            </div>
            <div className="co-kpi-label">{kpi.label}</div>
            <div className="co-kpi-note">{kpi.note}</div>
          </article>
        ))}
      </section>

      {/* ── YOUR WORKFLOW ──────────────────────────────────────────────── */}
      <section className="co-card">
        <div className="co-card-head">
          <h3>Your Workflow</h3>
          <span className="co-card-note">counted from this account&apos;s own records</span>
        </div>
        {loading ? (
          <div className="co-empty">Loading…</div>
        ) : !workflowHasAnything ? (
          <div className="co-empty">
            <strong>Nothing has moved through the workflow yet.</strong>
            <span>These stages fill in as prospects are sourced, worked and booked.</span>
          </div>
        ) : (
          <ol className="co-workflow">
            {workflow.map((stage, i) => (
              <li key={stage.key}
                  className={`co-stage${stage.to ? ' co-stage--link' : ''}`}
                  onClick={stage.to ? () => go(stage.to) : undefined}
                  role={stage.to ? 'button' : undefined}
                  tabIndex={stage.to ? 0 : undefined}
                  onKeyDown={stage.to ? (e => { if (e.key === 'Enter') go(stage.to) }) : undefined}>
                <small className="co-stage-label">{i + 1} · {stage.label}</small>
                <b className="co-stage-value">
                  {stage.value === null ? '—' : num(stage.value)}
                </b>
                <span className="co-stage-track">
                  {stage.value === null ? (
                    <span className="co-stage-gap">not counted</span>
                  ) : (
                    <span className="co-stage-fill"
                          style={{ width: workflowPeak ? `${(stage.value / workflowPeak) * 100}%` : '0%' }} />
                  )}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* ── RECENTLY WORKED PROSPECTS + RECENT VA ACTIVITY ─────────────── */}
      <div className="co-grid co-grid--split">
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
          {loading ? (
            <div className="co-empty">Loading…</div>
          ) : !prospects ? (
            <div className="co-empty">
              <strong>The prospects screen is not configured for this account.</strong>
            </div>
          ) : prospectRows.length === 0 ? (
            <div className="co-empty">
              <strong>No prospects on this account yet.</strong>
              <span>Sourced businesses and website enquiries appear here the
                    moment they arrive.</span>
            </div>
          ) : (
            <ul className="co-rows">
              {prospectRows.map(row => (
                <li key={row.id} className="co-row co-row--link"
                    onClick={() => go('/leads/' + row.lead_id)}
                    role="button" tabIndex={0}
                    onKeyDown={e => { if (e.key === 'Enter') go('/leads/' + row.lead_id) }}>
                  <b>{row.values?.name || '(no name)'}</b>
                  <span>{prospectLine(row.values)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="co-card">
          <div className="co-card-head">
            <h3>Recent VA Activity</h3>
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
          ) : byPerson.length === 0 ? (
            <div className="co-empty">
              <strong>Nothing has been sent today.</strong>
              <span>This counts every message this account sent today, on the
                    business&apos;s own clock rather than your browser&apos;s.
                    Calls a VA logged without a message are not sends and are
                    not counted here.</span>
            </div>
          ) : (
            <>
              <ul className="co-rows">
                {byPerson.slice(0, 5).map(person => (
                  <li key={person.name} className="co-row">
                    <b>{num(person.leads)} {person.leads === 1 ? 'prospect' : 'prospects'} contacted today</b>
                    <span>{person.name} · {num(person.sends)} {person.sends === 1 ? 'message' : 'messages'}</span>
                  </li>
                ))}
              </ul>
              <p className="co-footnote">
                Counted from the messages this account sent today. &ldquo;Automation&rdquo;
                is a send no person pressed.
              </p>
            </>
          )}
        </section>
      </div>

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
              <span>The day boundary is the business&apos;s own, answered by the
                    server rather than read off your browser&apos;s clock.</span>
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
                  <li key={`${item.channel}-${item.id}`} className="co-feed-row"
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
    </div>
  )
}
