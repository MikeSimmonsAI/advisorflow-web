/**
 * Demos / Proposals — the team's proposal work queues.
 *
 * NOT A SECOND PROPOSAL SYSTEM. Every row here comes from
 * `/sales/manager/overview`, which has been computing `proposal_queues` from
 * `proposal_workqueue.py` all along and shipping them to the browser on every
 * page load. The audit found `data.proposal_queues` was never read. Nothing was
 * built for this screen; it renders what was already arriving and being thrown
 * away. Editing, sending and the deal room stay exactly where they are — every
 * row links into the existing Opportunity Detail.
 *
 * DEMOS COME FROM SOMEWHERE ELSE, AND THAT IS CORRECT. A demo is a stage on the
 * opportunity (`demo_status`, `demo_due_at`, `demo_owner_user_id`), not a
 * proposal, so the DEMOS TO BUILD column is assembled from the manager
 * overview's `reps` rollup and `attention` items rather than invented here. Two
 * different records, two different sources, one screen — which is the honest
 * shape of the question "what is my team building and sending?".
 *
 * EVERY ROW SAYS WHY IT IS THERE. `proposal_workqueue` attaches a `reason` to
 * each brief precisely so a queue is a call to action rather than a report.
 * Dropping the reason to save a line would turn this back into a list.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, Empty, ErrorBar, Metric, money, dateTime } from './parts'

// Order matters: what the CUSTOMER did comes before what we have not done,
// because their clock is the one we do not control.
const QUEUES = [
  { key: 'follow_up_required', title: 'NEEDS FOLLOW-UP',
    sub: 'Declined, expired, changes asked for, or sent and still unopened' },
  { key: 'recently_viewed', title: 'THE BUYER JUST LOOKED',
    sub: 'Opened recently — the warmest moment there is' },
  { key: 'expiring', title: 'EXPIRING',
    sub: 'Live, in front of a customer, and running out of time' },
  { key: 'ready_to_send', title: 'READY — NOT SENT',
    sub: 'Finished. One click from being in front of a buyer' },
  { key: 'to_finish', title: 'DRAFTS TO FINISH',
    sub: 'Started and abandoned — the commonest way a deal quietly dies' },
]

function QueueRow({ row, onOpen }) {
  return (
    <button className="sw-qrow" onClick={() => onOpen(row.opportunity_id)}>
      <span style={{ minWidth: 0 }}>
        <b>{row.company || 'Unnamed'}</b>
        <span className="sw-why">{row.reason}</span>
        <span className="sw-who">
          {row.owner_name || 'Unassigned'}
          {row.proposal_number ? ` · ${row.proposal_number} v${row.version}` : ''}
        </span>
      </span>
      <span style={{ textAlign: 'right', flexShrink: 0 }}>
        {row.amount != null
          ? <b style={{ fontSize: 11 }}>{money(row.amount)}</b>
          : null}
        {row.urgency
          ? <div style={{ marginTop: 4 }}><Chip tone={row.urgency}>{row.status_label}</Chip></div>
          : <div style={{ marginTop: 4 }}><Chip>{row.status_label}</Chip></div>}
      </span>
    </button>
  )
}

export default function TeamProposals() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [ownerFilter, setOwnerFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await api.get('/sales/manager/overview'))
    } catch (e) {
      setError(e.message || 'Could not load the proposal queues.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const open = id => id && nav('/sales/opportunities/' + id)

  if (loading && !data) {
    return (
      <SalesShell title="Demos / Proposals">
        <Card><p className="sw-muted">Loading…</p></Card>
      </SalesShell>
    )
  }
  if (error && !data) {
    return (
      <SalesShell title="Demos / Proposals">
        <ErrorBar error={error} onRetry={load} />
        <Card>
          <Empty title="This is a manager screen">
            The team's proposal queues are available to sales managers. Your own
            proposal work is on My Day.
          </Empty>
        </Card>
      </SalesShell>
    )
  }

  const queues = data.proposal_queues || {}
  const counts = queues.counts || {}
  const reps = data.reps || []
  const attention = data.attention?.items || []

  const mine = rows => (ownerFilter
    ? (rows || []).filter(r => r.owner_user_id === ownerFilter)
    : (rows || []))

  // Demos are an opportunity stage, not a proposal. Sourced from the rollup the
  // overview already computes rather than reconstructed here.
  const demoOwners = reps.filter(r => r.demos_to_build > 0)
  const demoItems = attention.filter(i => i.kind === 'demo_overdue'
    || /demo/i.test(i.title || ''))
  const totalDemos = reps.reduce((n, r) => n + (r.demos_to_build || 0), 0)

  const approvals = data.approvals || { pending: [], pending_count: 0 }
  const filterName = ownerFilter
    ? (reps.find(r => r.user_id === ownerFilter)?.name || '')
    : null

  return (
    <SalesShell
      title="Demos / Proposals"
      subtitle={`${data.brand_name} — what your team is building and sending`}
      actions={
        <>
          {reps.length > 0 && (
            <select className="sw-select" style={{ width: 180 }}
                    value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
              <option value="">Everyone</option>
              {reps.map(r => <option key={r.user_id} value={r.user_id}>{r.name}</option>)}
            </select>
          )}
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      <div className="sw-metrics">
        <Metric label="Demos to build" value={totalDemos} attn={totalDemos > 0}
                sub={demoOwners.length ? `${demoOwners.length} rep${demoOwners.length === 1 ? '' : 's'}` : 'none open'} />
        <Metric label="Needs follow-up" value={counts.follow_up_required || 0}
                attn={(counts.follow_up_required || 0) > 0}
                sub="declined, expired, unopened" />
        <Metric label="Buyer just looked" value={counts.recently_viewed || 0}
                sub="call them now" />
        <Metric label="Expiring" value={counts.expiring || 0}
                attn={(counts.expiring || 0) > 0} sub="running out of time" />
        <Metric label="Ready, not sent" value={counts.ready_to_send || 0}
                attn={(counts.ready_to_send || 0) > 0} sub="one click away" />
        <Metric label="Drafts to finish" value={counts.to_finish || 0}
                sub="started and parked" />
      </div>

      {filterName ? (
        <div className="sw-note">
          Filtered to {filterName}.{' '}
          <button className="sw-tiny" onClick={() => setOwnerFilter('')}>Clear</button>
        </div>
      ) : null}

      {/* Approvals first: somebody on the team is blocked on the manager. */}
      {approvals.pending_count > 0 ? (
        <Card title="WAITING ON YOUR APPROVAL"
              sub="A rep cannot set these prices. Decide on Team Command."
              right={<button className="sw-btn" onClick={() => nav('/sales/manager')}>
                Open Team Command
              </button>}>
          {approvals.pending.map(r => (
            <div key={r.id} className="sw-decided">
              <Chip tone="amber">{r.status_label}</Chip>
              <span>{r.requested_by_name} — {money(r.requested_adjustment)}</span>
              <span className="sw-muted">{dateTime(r.requested_at)}</span>
            </div>
          ))}
        </Card>
      ) : null}

      <Card title="DEMOS TO BUILD"
            sub="A demo is a stage on the deal, not a proposal — so this comes from the pipeline, not the proposal queues."
            bodyless>
        <div className="sw-card-b">
          {totalDemos === 0 ? (
            <Empty title="No demos waiting">
              Nobody on the team has a deal sitting in demo build.
            </Empty>
          ) : (
            <>
              <div className="sw-pnums" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))' }}>
                {demoOwners
                  .filter(r => !ownerFilter || r.user_id === ownerFilter)
                  .map(r => (
                    <button key={r.user_id} className="sw-info"
                            style={{ cursor: 'pointer', textAlign: 'left' }}
                            onClick={() => setOwnerFilter(
                              ownerFilter === r.user_id ? '' : r.user_id)}>
                      <span>{r.name}</span>
                      <b>{r.demos_to_build} to build</b>
                    </button>
                  ))}
              </div>
              {demoItems.length ? (
                <div className="sw-mt">
                  {demoItems
                    .filter(i => !ownerFilter || i.owner_user_id === ownerFilter)
                    .map((i, n) => (
                      <button key={i.opportunity_id + '-' + n} className="sw-qrow"
                              onClick={() => open(i.opportunity_id)}>
                        <span>
                          <b>{i.company}</b>
                          <span className="sw-why">{i.detail || i.title}</span>
                          <span className="sw-who">{i.owner_name}</span>
                        </span>
                        <Chip tone={i.level}>{i.title}</Chip>
                      </button>
                    ))}
                </div>
              ) : null}
            </>
          )}
        </div>
      </Card>

      <div className="sw-queues sw-mt">
        {QUEUES.map(q => {
          const rows = mine(queues[q.key])
          const full = (counts[q.key] ?? rows.length)
          const capped = full > (queues[q.key] || []).length
          return (
            <Card key={q.key} title={q.title} sub={q.sub}
                  right={<span className="sw-count">{ownerFilter ? rows.length : full}</span>}>
              {rows.length === 0 ? (
                <Empty title="Clear">
                  {ownerFilter ? 'Nothing here for this rep.' : 'Nothing in this queue.'}
                </Empty>
              ) : (
                <>
                  {rows.map(r => (
                    <QueueRow key={r.proposal_id} row={r} onOpen={open} />
                  ))}
                  {capped && !ownerFilter ? (
                    <p className="sw-subtle" style={{ marginTop: 8 }}>
                      Showing {(queues[q.key] || []).length} of {full}. The count
                      above is the honest total.
                    </p>
                  ) : null}
                </>
              )}
            </Card>
          )
        })}
      </div>
    </SalesShell>
  )
}
