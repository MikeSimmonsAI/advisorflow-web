/**
 * COMMERCIAL CLEANING — CLIENT REPORTS.
 *
 * WHAT THIS REPLACES, AND WHY. A cleaning company opening Reports inside their
 * own account got the platform's generic reporting screen: a banner reading
 * "God View — All Organizations", an AI auto-response total of 117, a pipeline
 * of 40 and 3 flagged for review — on an account with no leads and no
 * messages. Every one of those numbers belonged to other customers. The
 * scoping defect behind it is fixed on the server (lead_scope.god_sees_all_orgs);
 * this page is the other half of the answer, because even correctly scoped,
 * "AI auto-sent" and "close rate" are not what a commercial cleaning business
 * asks its reports.
 *
 * ── Sources. Every number on this page names one. ──────────────────────────
 *   GET /workspace-views                 which configured screens exist here
 *   GET /workspace-views/prospects       total + Not Yet Worked / Walkthrough
 *                                        Set / Proposal Sent / Contract Won
 *   GET /workspace-views/follow-up       total + Replied / Awaiting Reply /
 *                                        Quiet 7+ Days
 *   GET /workspace-views/walkthroughs    total + Upcoming / Confirmed /
 *                                        Awaiting Confirmation / Completed /
 *                                        Awaiting Outcome / Cancelled
 *   GET /pipeline/stats                  by_stage, the account's own board
 *   GET /activity/today                  what this account sent today
 *   GET /admin/dashboard/metrics         per-person activity for this account
 *
 * ── What is deliberately NOT here ─────────────────────────────────────────
 * `/outcomes/summary` counts `lead_outcomes` rows — a deathcare record this
 * trade never creates — and would put a "Sales" tile and a close rate built
 * from an empty table onto a cleaning company's report. A metric that exists
 * in the platform is not a reason to show it to a customer it does not
 * describe. The outcomes this business has are walkthrough outcomes, and they
 * are on the page under that name.
 *
 * ── The rule this page is built on ────────────────────────────────────────
 * NO NUMBER WITHOUT A SOURCE, AND NO RATE WITHOUT A DENOMINATOR. A percentage
 * over zero is not 0%, it is not a percentage at all, and it renders as "—".
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getWorkspaceContext } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { useTerminology } from '../../terminology'
import { CLEANING_STAGES } from '../../verticals/workspaceVertical'
import './CleaningReports.css'

const VIEW_PROSPECTS = 'prospects'
const VIEW_FOLLOW_UP = 'follow-up'
const VIEW_WALKTHROUGHS = 'walkthroughs'

const INTERESTED_STAGES = ['interested', 'walkthrough_offered']

function num(n) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US')
}

/* A RATE NEEDS A DENOMINATOR. Over zero it is not 0%, it is unanswerable, and
 * printing 0% tells a reader their conversion is bad rather than absent. */
function rate(part, whole) {
  if (part === null || part === undefined || !whole) return null
  return Math.round((Number(part) / Number(whole)) * 100)
}

function statValue(payload, label) {
  if (!payload || !Array.isArray(payload.stats)) return null
  const hit = payload.stats.find(s => s.label === label)
  return hit ? hit.value : null
}

function humanise(key) {
  return String(key || '').replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

export default function CleaningReports() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const authority = useWorkspaceAuthority()
  const terminology = useTerminology()
  const branding = authority.branding

  const [views, setViews] = useState([])
  const [prospects, setProspects] = useState(null)
  const [followUp, setFollowUp] = useState(null)
  const [walkthroughs, setWalkthroughs] = useState(null)
  const [pipeline, setPipeline] = useState(null)
  const [activity, setActivity] = useState(null)
  const [team, setTeam] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  // The workspace and the identity are dependencies: switching customer does
  // not remount this component, and a report left showing the previous
  // customer's numbers under this customer's name is the defect this whole
  // pass exists to end.
  const identityKey = `${user?.role || ''}|${user?.organization_id || ''}`
  const workspaceKey = getWorkspaceContext() || ''
  const orgKey = branding?.organization_id || ''

  useEffect(() => {
    let live = true
    let unexpected = 0

    const attempt = (label, promise, fallback) =>
      promise.catch(e => {
        const status = e?.status ?? e?.response?.status
        if (!(status === 402 || status === 403 || status === 404)) {
          unexpected += 1
          // eslint-disable-next-line no-console
          console.warn('[cleaning-reports] %s failed:', label, e?.message || e)
        }
        return fallback
      })
    const view = (key) => attempt(key, api.get(`/workspace-views/${key}?limit=1`), null)

    Promise.all([
      attempt('views', api.get('/workspace-views', { skipRedirect: true }), null),
      view(VIEW_PROSPECTS),
      view(VIEW_FOLLOW_UP),
      view(VIEW_WALKTHROUGHS),
      attempt('pipeline', api.get('/pipeline/stats'), null),
      attempt('activity', api.get('/activity/today?limit=1'), null),
      attempt('team', api.get('/admin/dashboard/metrics'), null),
    ]).then(([v, pr, fu, wt, pl, ac, tm]) => {
      if (!live) return
      setViews(Array.isArray(v?.views) ? v.views : [])
      setProspects(pr)
      setFollowUp(fu)
      setWalkthroughs(wt)
      setPipeline(pl)
      setActivity(ac)
      setTeam(tm)
      setLoadError(unexpected > 0 ? 'Some of this account’s data is unavailable.' : '')
      setLoading(false)
    })
    return () => { live = false }
  }, [identityKey, workspaceKey, orgKey])

  const go = (path) => navigate(path)
  const hasView = (key) => views.some(v => v.key === key)
  const orgName = terminology.orgName || branding?.brand_name || 'this account'

  // ── THE NUMBERS ──────────────────────────────────────────────────────────
  const sourced = prospects?.total ?? null
  const notYetWorked = statValue(prospects, 'Not Yet Worked')
  const worked = (sourced === null || notYetWorked === null)
    ? null : Math.max(0, sourced - notYetWorked)
  const followUpTotal = followUp?.total ?? null

  const byStage = pipeline && typeof pipeline.by_stage === 'object' ? pipeline.by_stage : null
  const stageCount = (key) => (byStage ? Number(byStage[key] || 0) : null)
  const interested = byStage
    ? INTERESTED_STAGES.reduce((sum, s) => sum + Number(byStage[s] || 0), 0)
    : null
  const contacted = byStage
    ? ['contacted', 'decision_maker_found', 'interested', 'follow_up',
       'walkthrough_offered', 'walkthrough_booked', 'walkthrough_confirmed',
       'walkthrough_completed', 'proposal', 'won']
        .reduce((sum, s) => sum + Number(byStage[s] || 0), 0)
    : null

  const wtConfirmed = statValue(walkthroughs, 'Confirmed')
  const wtAwaitingConfirmation = statValue(walkthroughs, 'Awaiting Confirmation')
  const wtUpcoming = statValue(walkthroughs, 'Upcoming')
  const wtCompleted = statValue(walkthroughs, 'Completed')
  const wtAwaitingOutcome = statValue(walkthroughs, 'Awaiting Outcome')
  const wtCancelled = statValue(walkthroughs, 'Cancelled')
  const booked = (wtConfirmed === null && wtAwaitingConfirmation === null)
    ? null : Number(wtConfirmed || 0) + Number(wtAwaitingConfirmation || 0)

  const kpis = [
    { key: 'sourced', label: 'Prospects Sourced', value: sourced,
      sub: 'On this account, excluding dead and do-not-contact.',
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null, tone: 'blue' },
    { key: 'worked', label: 'Prospects Worked', value: worked,
      sub: 'Moved past New Prospect.',
      note: rate(worked, sourced) === null ? null : `${rate(worked, sourced)}% of the list`,
      to: hasView(VIEW_PROSPECTS) ? `/view/${VIEW_PROSPECTS}` : null, tone: 'cyan' },
    { key: 'contacted', label: 'Contacted', value: contacted,
      sub: 'Reached on the pipeline board, at Contacted or beyond.',
      note: rate(contacted, sourced) === null ? null : `${rate(contacted, sourced)}% contact rate`,
      to: contacted === null ? null : '/pipeline', tone: 'blue' },
    { key: 'follow-up', label: 'Follow-Ups', value: followUpTotal,
      sub: 'Alive and owed a next action.',
      to: hasView(VIEW_FOLLOW_UP) ? `/view/${VIEW_FOLLOW_UP}` : null, tone: 'amber' },
    { key: 'interested', label: 'Interested', value: interested,
      sub: 'Moved to Interested or Walkthrough Offered by a person.',
      note: rate(interested, contacted) === null ? null : `${rate(interested, contacted)}% of contacted`,
      to: interested === null ? null : '/pipeline', tone: 'green' },
    { key: 'booked', label: 'Walkthroughs Booked', value: booked,
      sub: 'A time on the calendar. A sent booking link is not counted.',
      note: rate(booked, interested) === null ? null : `${rate(booked, interested)}% of interested`,
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null, tone: 'blue' },
    { key: 'completed', label: 'Walkthroughs Completed', value: wtCompleted,
      sub: 'Recorded as having happened, on the pipeline.',
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null, tone: 'green' },
    { key: 'awaiting', label: 'Awaiting Outcome', value: wtAwaitingOutcome,
      sub: 'The time has passed and nobody has said what happened.',
      to: hasView(VIEW_WALKTHROUGHS) ? `/view/${VIEW_WALKTHROUGHS}` : null, tone: 'amber' },
  ]

  // ── THE BOARD ────────────────────────────────────────────────────────────
  //
  // Ordered by the vertical's own reading order, then anything the server
  // returned that the order does not name — de-underscored rather than
  // dropped, because a stage missing from a report is worse than an
  // unpolished label.
  const stages = useMemo(() => {
    if (!byStage) return []
    const known = CLEANING_STAGES.map(s => ({ ...s, value: Number(byStage[s.key] || 0) }))
    const extra = Object.keys(byStage)
      .filter(k => !CLEANING_STAGES.some(s => s.key === k))
      .map(k => ({ key: k, label: humanise(k), value: Number(byStage[k] || 0) }))
    return known.concat(extra)
  }, [byStage])
  const stagePeak = stages.reduce((max, s) => Math.max(max, s.value), 0)
  const stagesHaveAnything = stages.some(s => s.value > 0)

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

  // WHO THIS TABLE IS ABOUT, AND WHAT IT IS NOT.
  //
  // `/admin/dashboard/metrics` returns one row per user with the `advisor`
  // role in THIS workspace — the people who own prospects and send messages.
  // It is refused outright to a reader who is not an admin here, which is a
  // different fact from "this account has nobody", and the two get different
  // sentences below. The key is `advisor_name`, from `_advisor_row`; reading
  // `name` returned undefined for every row and drew a column of dashes.
  const teamRefused = !loading && !team
  const people = Array.isArray(team?.advisors)
    ? team.advisors.filter(a => a.advisor_id !== 'org_total')
    : []

  return (
    <div className="cr">
      <header className="cr-header">
        <div>
          <h1>Reports</h1>
          <p>{orgName} — prospecting, follow-up and booked walkthroughs.</p>
        </div>
        <span className="cr-scope">This account only</span>
      </header>

      {loadError && <div className="cr-notice">{loadError}</div>}

      {/* ── KPI GRID ───────────────────────────────────────────────────── */}
      <section className="cr-kpis">
        {kpis.map(kpi => (
          <article key={kpi.key}
                   className={`cr-kpi cr-kpi--${kpi.tone}${kpi.to ? ' cr-kpi--link' : ''}`}
                   onClick={kpi.to ? () => go(kpi.to) : undefined}
                   role={kpi.to ? 'button' : undefined}
                   tabIndex={kpi.to ? 0 : undefined}
                   onKeyDown={kpi.to ? (e => { if (e.key === 'Enter') go(kpi.to) }) : undefined}>
            <div className="cr-kpi-top">
              <span className="cr-kpi-label">{kpi.label}</span>
              {kpi.note && <span className="cr-kpi-note">{kpi.note}</span>}
            </div>
            <div className="cr-kpi-value">
              {loading ? '·' : num(kpi.value)}
            </div>
            <div className="cr-kpi-sub">{kpi.sub}</div>
          </article>
        ))}
      </section>

      {/* ── PIPELINE STAGE BREAKDOWN ───────────────────────────────────── */}
      <section className="cr-card">
        <div className="cr-card-head">
          <h2>Pipeline stage breakdown</h2>
          <span className="cr-card-note">Where every prospect currently sits</span>
        </div>
        {loading ? (
          <div className="cr-empty">Loading…</div>
        ) : !byStage ? (
          <div className="cr-empty">
            <strong>The pipeline board is not answering for this account.</strong>
            <span>These counts come from the board, so they appear once it does.</span>
          </div>
        ) : !stagesHaveAnything ? (
          <div className="cr-empty">
            <strong>Nothing is on the board yet.</strong>
            <span>Every stage below is a real zero. Prospects appear here as
                  they are worked; nothing moves on its own.</span>
          </div>
        ) : (
          <ol className="cr-stages">
            {stages.map(stage => (
              <li key={stage.key} className="cr-stage">
                <span className="cr-stage-label">{stage.label}</span>
                <span className="cr-stage-track">
                  <span className="cr-stage-fill"
                        style={{ width: stagePeak ? `${(stage.value / stagePeak) * 100}%` : '0%' }} />
                </span>
                <span className="cr-stage-value">{num(stage.value)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <div className="cr-grid">
        {/* ── WALKTHROUGH OUTCOMES ─────────────────────────────────────── */}
        <section className="cr-card">
          <div className="cr-card-head">
            <h2>Walkthrough outcomes</h2>
            {hasView(VIEW_WALKTHROUGHS) && (
              <button type="button" className="cr-link"
                      onClick={() => go(`/view/${VIEW_WALKTHROUGHS}`)}>Open →</button>
            )}
          </div>
          {loading ? (
            <div className="cr-empty">Loading…</div>
          ) : !walkthroughs ? (
            <div className="cr-empty">
              <strong>The walkthroughs screen is not configured for this account.</strong>
            </div>
          ) : (
            <>
              <ul className="cr-outcomes">
                {outcomes.map(row => (
                  <li key={row.key} className={`cr-outcome cr-outcome--${row.tone}`}>
                    <span className="cr-outcome-dot" aria-hidden="true" />
                    <div className="cr-outcome-body">
                      <span className="cr-outcome-label">{row.label}</span>
                      <span className="cr-outcome-note">{row.note}</span>
                    </div>
                    <span className="cr-outcome-value">{num(row.value ?? 0)}</span>
                  </li>
                ))}
              </ul>
              <p className="cr-footnote">
                Completed is what somebody recorded on the pipeline, never what
                the calendar implies. A visit whose time has passed with no
                outcome recorded is counted as awaiting one.
              </p>
            </>
          )}
        </section>

        {/* ── TODAY'S OUTREACH ─────────────────────────────────────────── */}
        <section className="cr-card">
          <div className="cr-card-head">
            <h2>Today’s outreach</h2>
            <button type="button" className="cr-link" onClick={() => go('/activity')}>
              View activity →
            </button>
          </div>
          {loading ? (
            <div className="cr-empty">Loading…</div>
          ) : !activity ? (
            <div className="cr-empty">
              <strong>Activity is unavailable for this account right now.</strong>
            </div>
          ) : (
            <>
              <div className="cr-mini">
                <div className="cr-mini-cell">
                  <span className="cr-mini-value">{num(activity.total ?? 0)}</span>
                  <span className="cr-mini-label">Sent today</span>
                </div>
                <div className="cr-mini-cell">
                  <span className="cr-mini-value">{num(activity.leads_contacted ?? 0)}</span>
                  <span className="cr-mini-label">Businesses contacted</span>
                </div>
                <div className="cr-mini-cell">
                  <span className="cr-mini-value">{num(activity.contacted_more_than_once ?? 0)}</span>
                  <span className="cr-mini-label">More than once</span>
                </div>
              </div>
              <p className="cr-footnote">
                Counted on this business’s own clock, over everything it sent —
                not over the first page of a longer list.
              </p>
            </>
          )}
        </section>
      </div>

      {/* ── PER-PERSON ACTIVITY ────────────────────────────────────────── */}
      <section className="cr-card">
        <div className="cr-card-head">
          <h2>Activity by person</h2>
          <span className="cr-card-note">Everyone working the list on this account</span>
        </div>
        {loading ? (
          <div className="cr-empty">Loading…</div>
        ) : teamRefused ? (
          <div className="cr-empty">
            <strong>Per-person activity is available to this account’s
                    administrators.</strong>
            <span>Everything else on this page is yours to read.</span>
          </div>
        ) : people.length === 0 ? (
          <div className="cr-empty">
            <strong>Nobody on this account owns prospects yet.</strong>
            <span>This table fills in as people are added and start working the
                  list. It counts only this account’s records.</span>
          </div>
        ) : (
          <div className="cr-tablewrap">
            <table className="cr-table">
              <thead>
                <tr>
                  <th>Person</th><th>Prospects</th><th>Messages</th>
                  <th>Replies</th><th>Reply rate</th><th>Booked</th>
                </tr>
              </thead>
              <tbody>
                {people.map(p => (
                  <tr key={p.advisor_id}>
                    <td><strong>{p.advisor_name || '—'}</strong></td>
                    <td>{num(p.leads_owned)}</td>
                    <td>{num(p.messages_sent)}</td>
                    <td>{num(p.replies)}</td>
                    <td>{p.messages_sent ? `${Math.round(Number(p.reply_rate || 0))}%` : '—'}</td>
                    <td>{num(p.booked_leads)}</td>
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
