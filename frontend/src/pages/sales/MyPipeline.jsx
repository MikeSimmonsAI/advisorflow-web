/**
 * My Pipeline — the stage board.
 *
 * The stage list and its order come from the server (/sales/opportunities
 * returns `stages` in vocabulary order). The client does not own the lifecycle;
 * if it did, the board and the API would drift the first time a stage changed.
 *
 * Clicking a card opens the SAME continuous record — a deal is never
 * re-created to move stage.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import NewProspect from './NewProspect'
import { Chip, Empty, ErrorBar, money, shortDate, dueLabel } from './parts'

function DealCard({ opp, onOpen }) {
  const due = dueLabel(opp.next_action_due_at)
  const tone = opp.attention ? 'sw-warn' : (opp.deal_value ? 'sw-hot' : '')
  return (
    <button className={'sw-deal ' + tone} onClick={() => onOpen(opp.id)}>
      <div className="sw-company">{opp.company_name}</div>
      {opp.contact_name && <div className="sw-contact">{opp.contact_name}</div>}
      <div className="sw-value">
        {opp.deal_value != null
          ? money(opp.deal_value)
          : (opp.next_action || 'No next action')}
      </div>
      {opp.attention && (
        <div style={{ marginTop: 7 }}><Chip tone="amber">{opp.attention}</Chip></div>
      )}
      <footer>
        <small>
          {opp.days_in_stage != null ? opp.days_in_stage + 'd in stage' : '—'}
          {opp.owner_name ? ' · ' + opp.owner_name.split(' ')[0] : ''}
        </small>
        {due.text
          ? <Chip tone={due.tone}>{due.text}</Chip>
          : (opp.next_action_due_at ? <Chip>{shortDate(opp.next_action_due_at)}</Chip> : null)}
      </footer>
    </button>
  )
}

export default function MyPipeline() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [team, setTeam] = useState([])
  const [ownerFilter, setOwnerFilter] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async (owner) => {
    setLoading(true); setError(null)
    try {
      const q = owner ? '?owner_user_id=' + encodeURIComponent(owner) : ''
      setData(await api.get('/sales/opportunities' + q))
    } catch (e) {
      setError(e.message || 'Could not load the pipeline.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(ownerFilter) }, [load, ownerFilter])
  useEffect(() => { api.get('/sales/team').then(setTeam).catch(() => setTeam([])) }, [])

  const open = id => nav('/sales/opportunities/' + id)
  const isManager = !!data?.is_manager
  const total = data?.total ?? 0

  return (
    <SalesShell
      title="My Pipeline"
      subtitle="One continuous record from first contact through sale and handoff."
      actions={
        <>
          {isManager && team.length > 0 && (
            <select className="sw-select" style={{ width: 190 }}
                    value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
              <option value="">Everyone on the team</option>
              {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
            </select>
          )}
          <button className="sw-btn" onClick={() => load(ownerFilter)} disabled={loading}>Refresh</button>
          <button className="sw-btn sw-primary" onClick={() => setCreating(true)}>+ New Prospect</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={() => load(ownerFilter)} />

      {creating && (
        <NewProspect onClose={() => setCreating(false)}
                     onCreated={opp => { setCreating(false); open(opp.id) }} />
      )}

      <div className="sw-flex sw-between" style={{ marginBottom: 14 }}>
        <div>
          <b style={{ fontSize: 14 }}>
            {total} {total === 1 ? 'opportunity' : 'opportunities'}
          </b>
          <div className="sw-subtle">
            {isManager
              ? 'Manager view — every opportunity in ' + (data?.brand_sales_org?.name || 'this brand') + '.'
              : 'Your book of business.'}
          </div>
        </div>
        {data?.lost?.length > 0 && <Chip>{data.lost.length} lost</Chip>}
      </div>

      {loading && !data && <div className="sw-subtle">Loading…</div>}

      {data && total === 0 && (
        <div className="sw-card">
          <Empty title="No opportunities yet">
            Production stays clean until a real prospect is entered — nothing here
            is seeded. Use <b>+ New Prospect</b> to create the first one, and it
            will move through every stage as one continuous record.
          </Empty>
        </div>
      )}

      {data && total > 0 && (
        <div className="sw-pipeline">
          {data.stages.map(stage => (
            <div className="sw-stage" key={stage.key}>
              <div className="sw-stage-h">
                <span style={{ background: 'none', padding: 0 }}>
                  {stage.label.toUpperCase()}
                </span>
                <span>{stage.count}</span>
              </div>
              {stage.opportunities.length
                ? stage.opportunities.map(o => <DealCard key={o.id} opp={o} onOpen={open} />)
                : <div className="sw-stage-empty">Empty</div>}
            </div>
          ))}
        </div>
      )}

      {data?.lost?.length > 0 && (
        <div className="sw-mt">
          <div className="sw-stage-h">LOST <span>{data.lost.length}</span></div>
          <div className="sw-pipeline">
            {data.lost.map(o => <DealCard key={o.id} opp={o} onOpen={open} />)}
          </div>
        </div>
      )}
    </SalesShell>
  )
}
