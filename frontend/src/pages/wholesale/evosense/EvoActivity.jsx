/* ACQUISITION COMMAND · ACTIVITY & SYSTEM HEALTH (My Deal Room redesign).
 *
 * Everything that used to sit under the work on Acquisition Command and is
 * reporting rather than work: the totals, routing reviews, the readiness
 * funnel, "Act first" and "Ready to run", the pipeline buckets, system status,
 * budget usage and the activity feed. Same endpoint (/command-center), same
 * panels, same controls — moved one click away so the work screen stays short.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { ev } from '../engine'
import { Alert, EvoApp, Feed, Hero, Metric, Metrics, PageSkeleton, Panel, SandboxTag, Status, ago, cents, humanize } from '../ds/ds'
import '../ds/evo-pages.css'
import { ActFirst, FunnelPanel } from './Readiness'
import { ReadyToRun } from './Acquire'
import { ACT_TONE, BudgetUsage, RoutingReviews, SystemStatus } from './EvoCommand'

export default function EvoActivity() {
  const [cc, setCc] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const load = useCallback(async () => {
    try { setCc(await api.get('/wholesale/evosense/command-center')); setError(null) } catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function resolveRouting(reviewId, engagementId) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post(`/wholesale/evosense/routing-reviews/${reviewId}`, { engagement_id: engagementId })
      setNotice(engagementId ? `Message attached and read${r.outcome ? ` as ${humanize(r.outcome).toLowerCase()}` : ''}.`
        : ev('Marked as not belonging to any EvoSense conversation.'))
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const back = <Link className="evo-btn evo-btn--secondary evo-btn--sm" to="/wholesale/evosense">← Back to the work queue</Link>
  if (!cc) return <EvoApp world="acquisition"><Hero title="Activity & system health" actions={back} />{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>

  const t = cc.totals || {}
  const best = (cc.found || []).reduce((b, p) => !b || (p.opportunity_score ?? -1) > (b.opportunity_score ?? -1) ? p : b, null)
  return (
    <EvoApp world="acquisition">
      <Hero eyebrow="Acquisition Command" title="Activity & system health"
            sub={ev('What EvoSense has done, what it has spent, and whether every channel can work.')} actions={back} />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      {cc.sandbox && cc.sandbox.banner ? <p className="evo-sandbox-line"><SandboxTag /> {cc.sandbox.banner}</p> : null}

      <Metrics label={ev('EvoSense totals')}>
        <Metric label="Highest investment evidence shown" value={best ? best.opportunity_score : null} tone="success" sub={best ? best.address : 'nothing scored yet'} />
        <Metric label="Properties evaluated" value={t.properties_evaluated} sub={`${t.hunts || 0} hunt${t.hunts === 1 ? '' : 's'} run`} />
        <Metric label="Contacts found" value={t.contacts_found} tone="primary"
                sub={cc.spent.cost_per_contact_found_cents != null ? `${cents(cc.spent.cost_per_contact_found_cents)} per contact` : null} />
        <Metric label="Action required" value={t.needs_you} tone="warning" attention={t.needs_you > 0} sub={t.needs_you ? 'needs your judgment' : 'nothing waiting'} />
        <Metric label="Active outreach" value={t.in_outreach} tone="info" sub={`${t.nurture || 0} in nurture`} />
        <Metric label="Spent this month" value={cents(cc.spent.month_cents)} tone="violet" sub={`${cents(cc.spent.today_cents)} today`} />
      </Metrics>

      {(cc.routing_reviews || []).length ? (
        <div id="routing"><RoutingReviews items={cc.routing_reviews} busy={busy} onResolve={resolveRouting} /></div>
      ) : null}

      <div className="evo-cols">
        <div className="evo-stack">
          <ActFirst />
          <FunnelPanel />
          <ReadyToRun />
          <Panel title="Pipeline at a glance" hint={ev('every property EvoSense has evaluated, by state')}>
            <div className="evo-buckets">
              {cc.buckets.filter((b) => b.count).map((b) => (
                <Link key={b.key} className="evo-bucket" to={`/wholesale/evosense/inbox?bucket=${b.key}`}>
                  <span className="evo-bucket__n">{b.count}</span>
                  <Status status={b.key} />
                </Link>
              ))}
              {!cc.buckets.some((b) => b.count) ? <span className="evo-muted">No properties discovered yet.</span> : null}
            </div>
          </Panel>
        </div>
        <aside className="evo-rail" aria-label="System status, budget and activity">
          <SystemStatus cc={cc} />
          <BudgetUsage spent={cc.spent} />
          <Panel title="Recent activity">
            <Feed items={(cc.recent_activity || []).map((e, i) => ({
              key: i, tone: ACT_TONE[e.action] || 'quiet',
              text: <>{e.summary}{e.address ? <> — <Link className="evo-feedlink" to={`/wholesale/evosense/property/${e.property_id}`}>{e.address}</Link></> : null}</>,
              meta: `${ago(e.at)} · ${e.actor === 'user' ? 'you' : e.actor}`,
            }))} empty={ev('EvoSense has not done anything yet.')} />
          </Panel>
        </aside>
      </div>
    </EvoApp>
  )
}
