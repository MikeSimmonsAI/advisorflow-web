/**
 * Team Command — the sales manager's workspace. Checkpoint 5.
 *
 * MY DAY answers "what do I need to do?". THIS answers "what does my team need
 * from me?". They are deliberately not the same screen wearing two names: every
 * section here is either something only a manager can act on, or something only
 * a manager can see across more than one person's book.
 *
 * IT IS NOT A DASHBOARD. There is no chart, no trend line and no leaderboard.
 * Every row names a deal or a person and says what to do about it. A number a
 * manager cannot act on costs attention and returns nothing.
 *
 * NOR IS IT SURVEILLANCE. The rep section carries workload and blockage — open
 * deals, what is overdue, what is waiting on a customer. It carries no message
 * counts, no response times and no ranking, because a manager screen that
 * measures effort instead of obstacles becomes a stick, and then people manage
 * the metric instead of the deal.
 *
 * DRILL-DOWN REUSES WHAT EXISTS. A deal row links to /sales/opportunities/:id,
 * which already holds the timeline, the proposal, buyer activity and meetings.
 * Restating any of that here would create a second version of the truth.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import {
  Card, Chip, Empty, ErrorBar, Info, Metric,
  money, dateTime, wallTime, dueLabel, initials,
} from './parts'

const KIND_LABEL = {
  proposal_declined: 'Declined',
  change_requested: 'Change requested',
  proposal_expired: 'Expired',
  proposal_expiring: 'Expiring',
  proposal_unopened: 'Never opened',
  proposal_ready: 'Not sent',
  overdue_action: 'Overdue',
  no_next_action: 'No next action',
  stalled: 'Stalled',
  no_activity: 'Gone quiet',
  calendar_sync: 'Calendar',
  video_failed: 'Video',
}

// Grouping order. Things the CUSTOMER did come first — those have a clock on
// them that we do not control. Then our own slippage. Then the plumbing.
const KIND_ORDER = [
  'proposal_declined', 'change_requested', 'video_failed', 'proposal_expired',
  'proposal_expiring', 'proposal_unopened', 'proposal_ready',
  'overdue_action', 'no_next_action', 'stalled', 'no_activity', 'calendar_sync',
]


export default function ManagerCommand() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [openRep, setOpenRep] = useState(null)
  const [repData, setRepData] = useState(null)
  const [filter, setFilter] = useState(null)     // owner_user_id or null
  const [decideNote, setDecideNote] = useState({})

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await api.get('/sales/manager/overview'))
    } catch (e) {
      setError(e.message || 'Could not load the team workspace.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function decide(reqId, approve) {
    setBusy(true); setError(null); setNote(null)
    try {
      const res = await api.post(`/sales/manager/approvals/${reqId}/decide`, {
        approve, note: decideNote[reqId] || null,
      })
      setNote(res.applied
        ? 'Approved. The price is updated on the proposal.'
        : 'Denied. Nothing on the proposal changed.')
      setDecideNote(s => ({ ...s, [reqId]: '' }))
      await load()
    } catch (e) {
      setError(e.message || 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  async function openRepDetail(userId) {
    if (openRep === userId) { setOpenRep(null); setRepData(null); return }
    setOpenRep(userId); setRepData(null)
    try {
      setRepData(await api.get(`/sales/manager/reps/${userId}`))
    } catch (e) {
      setError(e.message || 'Could not open that rep.')
    }
  }

  if (loading && !data) {
    return <SalesShell title="Team Command"><Card><p className="sw-muted">Loading…</p></Card></SalesShell>
  }
  if (error && !data) {
    return <SalesShell title="Team Command"><ErrorBar error={error} onRetry={load} /></SalesShell>
  }

  const today = data.team_today
  const att = data.attention
  const appr = data.approvals
  const closing = data.closing_pipeline
  const reps = data.reps

  const items = filter ? att.items.filter(i => i.owner_user_id === filter) : att.items
  const grouped = {}
  items.forEach(i => { (grouped[i.kind] = grouped[i.kind] || []).push(i) })
  const kinds = KIND_ORDER.filter(k => grouped[k]?.length)
    .concat(Object.keys(grouped).filter(k => !KIND_ORDER.includes(k)))

  const filterName = filter ? (reps.find(r => r.user_id === filter)?.name || '') : null

  return (
    <SalesShell
      title="Team Command"
      subtitle={`${data.brand_name} — what your team needs from you`}
      actions={<button className="sw-btn" onClick={load} disabled={busy}>Refresh</button>}
    >
      {error ? <ErrorBar error={error} onRetry={load} /> : null}
      {note ? <div className="sw-note">{note}</div> : null}

      {/* ── the four numbers that decide where to look ── */}
      <div className="sw-metrics">
        <Metric label="Needs your attention" value={att.total} attn={att.total > 0}
                sub={att.red ? `${att.red} urgent` : 'nothing urgent'} />
        <Metric label="Waiting on your approval" value={appr.pending_count}
                attn={appr.pending_count > 0}
                sub={appr.pending_count ? 'someone is blocked' : 'nothing pending'} />
        <Metric label="Meetings today" value={today.total_meetings}
                sub={today.unconfirmed ? `${today.unconfirmed} unconfirmed` : 'all confirmed'} />
        <Metric label="In closing" value={closing.count}
                sub={closing.total_value ? money(closing.total_value) : '—'} />
      </div>

      {/* ── APPROVALS — first when someone is blocked on you ── */}
      {appr.pending_count > 0 ? (
        <Card title="WAITING ON YOUR APPROVAL"
              sub="A rep cannot set these prices. Until you answer, they are stuck.">
          {appr.pending.map(r => (
            <div key={r.id} className="sw-appr">
              <div className="sw-appr-head">
                <div>
                  <b>{r.requested_by_name}</b>
                  <span className="sw-muted"> asked {dateTime(r.requested_at)}</span>
                </div>
                <Chip tone="amber">{r.status_label}</Chip>
              </div>
              <div className="sw-appr-money">
                <Info label="List price" value={money(r.base_amount)} />
                <Info label="Asking for" value={money(r.requested_adjustment)} />
                <Info label="Customer would pay" value={money(r.requested_total)} />
              </div>
              <blockquote className="sw-quote">{r.reason}</blockquote>
              <div className="sw-appr-act">
                <input
                  className="sw-input"
                  placeholder="Add a note (optional) — the rep sees this on the deal"
                  value={decideNote[r.id] || ''}
                  onChange={e => setDecideNote(s => ({ ...s, [r.id]: e.target.value }))}
                />
                <button className="sw-btn sw-primary" disabled={busy}
                        onClick={() => decide(r.id, true)}>Approve</button>
                <button className="sw-btn" disabled={busy}
                        onClick={() => decide(r.id, false)}>Deny</button>
                <button className="sw-btn sw-ghost"
                        onClick={() => nav(`/sales/opportunities/${r.opportunity_id}`)}>
                  Open the deal
                </button>
              </div>
            </div>
          ))}
        </Card>
      ) : null}

      <div className="sw-two">
        <div>
          {/* ── ATTENTION ── */}
          <Card
            title="ATTENTION REQUIRED"
            sub={filter
              ? `Filtered to ${filterName} — click their name again to clear`
              : 'Ordered by urgency, then by what it is worth'}
            right={filter
              ? <button className="sw-btn sw-ghost" onClick={() => setFilter(null)}>Clear filter</button>
              : null}
          >
            {items.length === 0 ? (
              <Empty title="Nothing needs you right now">
                Every deal has a next action, every proposal is moving, and the
                calendars and video links are healthy.
              </Empty>
            ) : kinds.map(kind => (
              <div key={kind} className="sw-attgroup">
                <div className="sw-attgroup-h">
                  {KIND_LABEL[kind] || kind}
                  <span className="sw-count">{grouped[kind].length}</span>
                </div>
                {grouped[kind].map((i, n) => (
                  <div key={`${i.opportunity_id}-${kind}-${n}`} className="sw-attrow">
                    <div className="sw-attrow-main">
                      <div className="sw-attrow-t">
                        <b>{i.company}</b>
                        <Chip tone={i.level}>{i.title}</Chip>
                      </div>
                      <div className="sw-muted">{i.detail}</div>
                      <div className="sw-attrow-meta">
                        {i.owner_name} · {i.stage_label}
                        {i.deal_value ? ` · ${money(i.deal_value)}` : ''}
                        {i.action ? <span className="sw-do"> → {i.action}</span> : null}
                      </div>
                    </div>
                    <button className="sw-btn"
                            onClick={() => nav(`/sales/opportunities/${i.opportunity_id}`)}>
                      Open
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </Card>

          {/* ── CLOSING PIPELINE ── */}
          <Card title="CLOSING PIPELINE"
                sub="Deals with a number on them, soonest expiry first">
            {closing.rows.length === 0 ? (
              <Empty title="Nothing in closing">
                A deal appears here once a proposal is in front of a customer.
              </Empty>
            ) : (
              <div className="sw-tablewrap">
                <table className="sw-table">
                  <thead>
                    <tr>
                      <th>Company</th><th>Rep</th><th>Value</th><th>Proposal</th>
                      <th>Buyer</th><th>Last touch</th><th>Next action</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {closing.rows.map(r => (
                      <tr key={r.opportunity_id}>
                        <td><b>{r.company}</b><br /><span className="sw-muted">{r.stage_label}</span></td>
                        <td>{r.owner_name || '—'}</td>
                        <td>{money(r.deal_value)}</td>
                        <td>
                          {r.proposal_number
                            ? <>{r.proposal_number} v{r.proposal_version}<br />
                                <span className="sw-muted">{r.proposal_status_label}</span></>
                            : <span className="sw-muted">None</span>}
                        </td>
                        <td>
                          {r.buyer_events
                            ? <>{r.buyer_events} actions<br />
                                <span className="sw-muted">{r.buyer_last_ago}</span></>
                            : <span className="sw-muted">Never opened</span>}
                        </td>
                        <td><span className="sw-muted">{r.last_touch_ago || '—'}</span></td>
                        <td>
                          {r.next_action || <span className="sw-muted">Not set</span>}
                          {r.next_action_due_at
                            ? <><br /><Chip tone={dueLabel(r.next_action_due_at).tone}>
                                {dueLabel(r.next_action_due_at).text}</Chip></>
                            : null}
                        </td>
                        <td>
                          <button className="sw-btn"
                                  onClick={() => nav(`/sales/opportunities/${r.opportunity_id}`)}>
                            Open
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <div>
          {/* ── TEAM TODAY ── */}
          <Card title="TEAM TODAY"
                sub={`${today.working_today} of ${today.people.length} have something booked`}>
            {today.people.map(p => (
              <div key={p.user_id} className="sw-person">
                <div className="sw-person-h">
                  <div className="sw-avatar">{initials(p.name)}</div>
                  <div>
                    <b>{p.name}</b>
                    <small>{p.role_label}</small>
                  </div>
                  <span className="sw-count">{p.meeting_count}</span>
                </div>
                {p.clear ? (
                  <div className="sw-muted sw-pad">Nothing booked today.</div>
                ) : p.meetings.map(m => (
                  <div key={m.id} className="sw-meet">
                    <span className="sw-meet-t">{wallTime(m.starts_at_local)}</span>
                    <span>
                      {m.meeting_type || m.title}
                      {m.company ? <span className="sw-muted"> · {m.company}</span> : null}
                    </span>
                    {m.confirmation_status === 'pending'
                      ? <Chip tone="amber">unconfirmed</Chip> : null}
                    {m.video_needs_attention
                      ? <Chip tone="red">video</Chip> : null}
                    {m.join_url
                      ? <a className="sw-btn sw-primary" href={m.join_url}
                           target="_blank" rel="noreferrer">Join</a> : null}
                  </div>
                ))}
              </div>
            ))}
          </Card>

          {/* ── REP ACTIVITY ── */}
          <Card title="YOUR REPS"
                sub="Workload and what is blocked — click a name to open their book">
            {reps.map(r => (
              <div key={r.user_id} className="sw-rep">
                <div className="sw-rep-h" onClick={() => openRepDetail(r.user_id)}>
                  <div className="sw-avatar">{initials(r.name)}</div>
                  <div className="sw-rep-id">
                    <b>{r.name}</b>
                    <small>{r.role_label}</small>
                  </div>
                  {r.needs_attention
                    ? <Chip tone="amber">{r.needs_attention} need you</Chip>
                    : <Chip tone="green">clear</Chip>}
                </div>
                <div className="sw-rep-nums">
                  <Info label="Open deals" value={r.open_deals} />
                  <Info label="Pipeline" value={money(r.pipeline_value)} />
                  <Info label="Today" value={r.meetings_today} />
                  <Info label="Overdue" value={r.overdue_actions} />
                  <Info label="To send" value={r.proposals_awaiting_send} />
                  <Info label="With customer" value={r.proposals_with_customer} />
                </div>
                <div className="sw-rep-foot">
                  <span className="sw-muted">
                    Last recorded activity: {r.last_recorded_activity_ago || 'none'}
                  </span>
                  <button className="sw-btn sw-ghost"
                          onClick={() => setFilter(filter === r.user_id ? null : r.user_id)}>
                    {filter === r.user_id ? 'Clear filter' : 'Filter attention'}
                  </button>
                </div>

                {openRep === r.user_id ? (
                  <div className="sw-repdrill">
                    {!repData ? <p className="sw-muted">Loading…</p>
                      : repData.deals.length === 0
                        ? <Empty title="No open deals" />
                        : repData.deals.map(d => (
                          <div key={d.id} className="sw-drillrow"
                               onClick={() => nav(`/sales/opportunities/${d.id}`)}>
                            <div>
                              <b>{d.company_name}</b>
                              <div className="sw-muted">
                                {d.stage_label}
                                {d.proposal_number ? ` · ${d.proposal_number} — ${d.proposal_status_label}` : ''}
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
          </Card>

          {/* ── recently decided, so a manager can see their own calls ── */}
          {appr.recent.length ? (
            <Card title="RECENTLY DECIDED" sub="Your last pricing calls">
              {appr.recent.map(r => (
                <div key={r.id} className="sw-decided">
                  <Chip tone={r.status === 'approved' ? 'green' : null}>{r.status_label}</Chip>
                  <span>{money(r.requested_adjustment)} — {r.requested_by_name}</span>
                  <span className="sw-muted">{dateTime(r.decided_at)}</span>
                </div>
              ))}
            </Card>
          ) : null}
        </div>
      </div>
    </SalesShell>
  )
}
