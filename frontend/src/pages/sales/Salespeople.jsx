/**
 * Salespeople — the manager's operational view of their team.
 *
 * NOT USER ADMINISTRATION. Creating people, granting memberships and issuing
 * access links live in God Mode (Sales Operations → brand → Sales team) and
 * stay there. This screen answers a different question: who is on my team, what
 * are they carrying, are they free this week, and is anything of theirs stuck.
 * A manager runs a team inside one brand; they do not administer identities.
 *
 * EVERYTHING HERE ALREADY EXISTED. `GET /sales/team` is the roster,
 * `/sales/manager/overview` already returns `team` (never read until now) and
 * the `reps` rollup, `/sales/manager/reps/{id}` is the drill-down, and
 * `/sales/availability/team` is the schedule. No endpoint was added.
 *
 * NO RANKING, NO EFFORT METRICS. Same principle Team Command was built on: this
 * shows workload and blockage, never message counts, response times or a
 * leaderboard. A manager screen that measures effort instead of obstacles
 * becomes a stick, and then people manage the metric instead of the deal.
 * "Last recorded activity" is here because a rep who has gone quiet is a
 * blockage; it is a fact with a date, not a score.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import {
  Card, Chip, Empty, ErrorBar, Info, Metric,
  money, initials, wallTime,
} from './parts'

function ymd(d) {
  const p = n => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

export default function Salespeople() {
  const nav = useNavigate()
  const [overview, setOverview] = useState(null)
  const [roster, setRoster] = useState([])
  const [avail, setAvail] = useState(null)
  const [openRep, setOpenRep] = useState(null)
  const [repData, setRepData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [ov, team] = await Promise.all([
        api.get('/sales/manager/overview'),
        api.get('/sales/team').catch(() => []),
      ])
      setOverview(ov)
      setRoster(Array.isArray(team) ? team : [])
      // Availability is a nicety on this screen, never a blocker.
      api.get('/sales/availability/team?day=' + ymd(new Date()))
        .then(setAvail).catch(() => setAvail(null))
    } catch (e) {
      setError(e.message || 'Could not load the team.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function openDetail(userId) {
    if (openRep === userId) { setOpenRep(null); setRepData(null); return }
    setOpenRep(userId); setRepData(null)
    try {
      setRepData(await api.get('/sales/manager/reps/' + userId))
    } catch (e) {
      setError(e.message || 'Could not open that person.')
    }
  }

  // The roster is the source of truth for WHO is on the team; the rollup is the
  // source of truth for what they are carrying. A person with no open deals
  // appears in the first and not the second, and must still be listed — an
  // empty rep is exactly who a manager needs to notice.
  const people = useMemo(() => {
    const rollup = new Map((overview?.reps || []).map(r => [r.user_id, r]))
    const today = new Map(
      (overview?.team_today?.people || []).map(p => [p.user_id, p]))
    const free = new Map(
      (avail?.people || avail?.team || []).map(p => [p.user_id || p.id, p]))
    const base = roster.length
      ? roster.map(t => ({ user_id: t.id, name: t.full_name, email: t.email,
                           role: t.role, role_label: t.role_label }))
      : (overview?.team || []).map(t => ({ user_id: t.id, name: t.full_name,
                                           email: t.email, role: t.role,
                                           role_label: t.role_label }))
    return base.map(p => ({
      ...p,
      ...(rollup.get(p.user_id) || {}),
      user_id: p.user_id,
      name: p.name,
      role_label: p.role_label,
      today: today.get(p.user_id) || null,
      free: free.get(p.user_id) || null,
      has_rollup: rollup.has(p.user_id),
    }))
  }, [roster, overview, avail])

  const managers = people.filter(p => p.role === 'sales_manager').length
  const idle = people.filter(p => p.has_rollup && !p.last_recorded_activity_ago).length
  const blocked = people.reduce((n, p) => n + (p.needs_attention || 0), 0)
  const pipeline = people.reduce((n, p) => n + (p.pipeline_value || 0), 0)

  if (loading && !overview) {
    return (
      <SalesShell title="Salespeople">
        <Card><p className="sw-muted">Loading…</p></Card>
      </SalesShell>
    )
  }
  if (error && !overview) {
    return (
      <SalesShell title="Salespeople">
        <ErrorBar error={error} onRetry={load} />
        <Card>
          <Empty title="This is a manager screen">
            The team view is available to sales managers. Who sells this brand is
            on Team Availability.
          </Empty>
        </Card>
      </SalesShell>
    )
  }

  return (
    <SalesShell
      title="Salespeople"
      subtitle={`${overview?.brand_name || 'Your brand'} — who is on the team and what they are carrying`}
      actions={<button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>}
    >
      <ErrorBar error={error} onRetry={load} />

      <div className="sw-metrics">
        <Metric label="On the team" value={people.length}
                sub={`${managers} manager${managers === 1 ? '' : 's'}`} />
        <Metric label="Open deals" value={people.reduce((n, p) => n + (p.open_deals || 0), 0)}
                sub="across everyone" />
        <Metric label="Team pipeline" value={money(pipeline) || '—'} sub="open value" />
        <Metric label="Blocked items" value={blocked} attn={blocked > 0}
                sub={blocked ? 'need a manager' : 'nothing stuck'} />
        <Metric label="Booked today" value={people.filter(p => (p.meetings_today || 0) > 0).length}
                sub={`of ${people.length}`} />
        <Metric label="No recorded activity" value={idle} attn={idle > 0}
                sub={idle ? 'worth a conversation' : 'everyone active'} />
      </div>

      {people.length === 0 ? (
        <Card>
          <Empty title="Nobody on this team yet">
            Memberships are granted in God Mode, under Sales Operations → this
            brand → Sales team. This screen shows the people who already sell
            here; it does not create them.
          </Empty>
        </Card>
      ) : (
        <div className="sw-people">
          {people.map(p => (
            <div key={p.user_id} className="sw-pcard">
              <div className="sw-pcard-h">
                <div className="sw-avatar">{initials(p.name)}</div>
                <div style={{ minWidth: 0 }}>
                  <b>{p.name}</b>
                  <small>{p.role_label}{p.email ? ' · ' + p.email : ''}</small>
                </div>
                <div className="sw-spacer" />
                {p.needs_attention
                  ? <Chip tone="amber">{p.needs_attention} need you</Chip>
                  : <Chip tone="green">clear</Chip>}
              </div>

              <div className="sw-pnums">
                <Info label="Open deals" value={p.open_deals ?? 0} />
                <Info label="Pipeline" value={money(p.pipeline_value || 0)} />
                <Info label="Today" value={p.meetings_today ?? 0} />
                <Info label="Overdue" value={p.overdue_actions ?? 0} />
                <Info label="Demos" value={p.demos_to_build ?? 0} />
                <Info label="To send" value={p.proposals_awaiting_send ?? 0} />
              </div>

              {p.today && !p.today.clear ? (
                <div className="sw-mt">
                  <div className="sw-attgroup-h">TODAY</div>
                  {p.today.meetings.slice(0, 4).map(m => (
                    <div key={m.id} className="sw-meet" style={{ paddingLeft: 0 }}>
                      <span className="sw-meet-t">{wallTime(m.starts_at_local)}</span>
                      <span>
                        {m.meeting_type || m.title}
                        {m.company ? <span className="sw-muted"> · {m.company}</span> : null}
                      </span>
                      {m.confirmation_status === 'pending'
                        ? <Chip tone="amber">unconfirmed</Chip> : null}
                    </div>
                  ))}
                  {p.today.meetings.length > 4 ? (
                    <p className="sw-subtle">+{p.today.meetings.length - 4} more</p>
                  ) : null}
                </div>
              ) : (
                <p className="sw-subtle sw-mt">Nothing booked today.</p>
              )}

              <div className="sw-pfoot">
                <span className="sw-muted" style={{ fontSize: 11 }}>
                  Last recorded activity: {p.last_recorded_activity_ago || 'none recorded'}
                </span>
                <span className="sw-actions">
                  <button className="sw-btn sw-ghost"
                          onClick={() => nav('/sales/team-pipeline')}>
                    Their pipeline
                  </button>
                  <button className="sw-btn" onClick={() => openDetail(p.user_id)}>
                    {openRep === p.user_id ? 'Close' : 'Open book'}
                  </button>
                </span>
              </div>

              {openRep === p.user_id ? (
                <div className="sw-repdrill">
                  {!repData ? <p className="sw-muted">Loading…</p>
                    : !repData.deals?.length
                      ? <Empty title="No open deals" />
                      : repData.deals.map(d => (
                        <div key={d.id} className="sw-drillrow"
                             onClick={() => nav('/sales/opportunities/' + d.id)}>
                          <div style={{ minWidth: 0 }}>
                            <b>{d.company_name}</b>
                            <div className="sw-muted">
                              {d.stage_label}
                              {d.proposal_number
                                ? ` · ${d.proposal_number} — ${d.proposal_status_label}` : ''}
                            </div>
                          </div>
                          <div className="sw-drillmeta">
                            {d.attention ? <Chip tone="amber">{d.attention}</Chip> : null}
                            <span className="sw-muted">{d.last_touch_ago || ''}</span>
                          </div>
                        </div>
                      ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <p className="sw-subtle" style={{ marginTop: 16 }}>
        Adding a person, changing their sales role or issuing them a login is
        done in God Mode under Sales Operations — deliberately not here. This
        screen runs the team; it does not administer identities.
      </p>
    </SalesShell>
  )
}
