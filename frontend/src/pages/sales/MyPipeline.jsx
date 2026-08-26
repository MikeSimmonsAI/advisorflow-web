/**
 * The stage board, in two scopes from ONE component.
 *
 *   scope="mine"  → /sales/pipeline       "My Pipeline"    — my own book
 *   scope="team"  → /sales/team-pipeline  "Team Pipeline"  — the whole brand
 *
 * ONE COMPONENT ON PURPOSE. The audit found the team pipeline already existed
 * and had done for a while: `_scoped_opportunities` drops the owner filter for
 * a manager, so this page was ALREADY returning every deal in the brand. It was
 * simply called "My Pipeline" and filed under MY WORK, so no manager could tell.
 * Forking it into a second file would have created two boards that drift, to
 * solve a labelling problem.
 *
 * HOW "MINE" IS ENFORCED FOR A MANAGER. The server cannot answer "just mine"
 * for a manager without an explicit owner filter, and asking for one costs a
 * second round trip before we know whether the caller is a manager at all. So
 * the request is unfiltered and `scope="mine"` narrows the returned cards to
 * this user's own. That is presentation, not access control - a manager is
 * authorised to see every card in the response either way. A REP is never
 * narrowed here, because the server already narrowed them, and filtering again
 * client-side would hide the unowned prospects a rep is meant to be able to
 * pick up.
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

export default function MyPipeline({ scope = 'mine' }) {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [team, setTeam] = useState([])
  const [ownerFilter, setOwnerFilter] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)

  const isTeam = scope === 'team'

  const load = useCallback(async (owner) => {
    setLoading(true); setError(null)
    try {
      const q = owner ? '?owner_user_id=' + encodeURIComponent(owner) : ''
      setData(await api.get('/sales/opportunities' + q))
    } catch (e) {
      setError(e.message || 'Could not load the pipeline.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(isTeam ? ownerFilter : '') }, [load, ownerFilter, isTeam])
  useEffect(() => {
    if (!isTeam) return
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
  }, [isTeam])

  const open = id => nav('/sales/opportunities/' + id)
  const isManager = !!data?.is_manager

  // See the header comment: narrowing a MANAGER's board to their own deals is
  // presentation. A rep is left exactly as the server returned them.
  const meId = data?.viewer_user_id
  const narrow = rows => (
    (!isTeam && isManager && meId)
      ? (rows || []).filter(o => o.owner_user_id === meId)
      : (rows || [])
  )
  const stages = (data?.stages || []).map(s => ({
    ...s,
    opportunities: narrow(s.opportunities),
  })).map(s => ({ ...s, count: s.opportunities.length }))
  const lost = narrow(data?.lost)
  const total = stages.reduce((n, s) => n + s.count, 0)

  const title = isTeam ? 'Team Pipeline' : 'My Pipeline'
  const subtitle = isTeam
    ? 'Every opportunity your team is working, in one board.'
    : 'One continuous record from first contact through sale and handoff.'

  return (
    <SalesShell
      title={title}
      subtitle={subtitle}
      actions={
        <>
          {isTeam && team.length > 0 && (
            <select className="sw-select" style={{ width: 190 }}
                    value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
              <option value="">Everyone on the team</option>
              {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
            </select>
          )}
          <button className="sw-btn" onClick={() => load(isTeam ? ownerFilter : '')}
                  disabled={loading}>Refresh</button>
          <button className="sw-btn sw-primary" onClick={() => setCreating(true)}>+ New Prospect</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={() => load(isTeam ? ownerFilter : '')} />

      {/* A rep who lands on the team URL gets the server's answer, which is
          their own book — not an error and not somebody else's deals. Saying so
          is kinder than silently showing them a board titled "Team". */}
      {isTeam && data && !isManager && (
        <div className="sw-note">
          You are seeing your own opportunities. The team board is available to
          sales managers.
        </div>
      )}

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
            {isTeam && isManager
              ? 'Every opportunity in ' + (data?.brand_sales_org?.name || 'this brand')
                + (ownerFilter
                   ? ', filtered to ' + (team.find(t => t.id === ownerFilter)?.full_name || 'one rep') + '.'
                   : '.')
              : (isManager
                 ? 'The deals you personally own. Your team’s board is under MY TEAM.'
                 : 'Your book of business.')}
          </div>
        </div>
        {lost.length > 0 && <Chip>{lost.length} lost</Chip>}
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
          {stages.map(stage => (
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

      {lost.length > 0 && (
        <div className="sw-mt">
          <div className="sw-stage-h">LOST <span>{lost.length}</span></div>
          <div className="sw-pipeline">
            {lost.map(o => <DealCard key={o.id} opp={o} onOpen={open} />)}
          </div>
        </div>
      )}
    </SalesShell>
  )
}
