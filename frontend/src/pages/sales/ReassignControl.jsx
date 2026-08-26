/**
 * Reassign an opportunity to another rep.
 *
 * THE ENDPOINT WAS ALWAYS THERE. `POST /sales/opportunities/{id}/reassign` is
 * manager-gated by `require_sales_manager`, audited through `_event`, refuses a
 * new owner with no active membership in that brand, and has been returning 200
 * since Checkpoint 5. `get_opportunity` even returns a `can_reassign` flag on
 * every deal. Nothing in the workspace ever called it. This is that call.
 *
 * `canReassign` COMES FROM THE SERVER, NOT FROM A ROLE STRING. The parent
 * passes the record's own `can_reassign`, which the API computed with
 * `is_sales_manager(user, db, opp.brand_sales_org_id)` — per record, per brand.
 * Deciding it here from a cached role would be a second, weaker opinion about
 * the same question. And it is still only presentation: a rep who forges the
 * request gets a 403 from the dependency, not a reassignment.
 *
 * IT NAMES THE PERSON LOSING THE DEAL. A book changing hands silently is how a
 * rep finds out by noticing something missing. The confirmation says who had it
 * and who has it now, and the timeline records the same thing permanently.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Chip } from './parts'

export default function ReassignControl({
  opportunityId, canReassign, currentOwnerId, currentOwnerName, onReassigned,
}) {
  const [team, setTeam] = useState([])
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  useEffect(() => {
    if (!canReassign || !open || team.length) return
    api.get('/sales/team').then(r => setTeam(Array.isArray(r) ? r : []))
      .catch(() => setTeam([]))
  }, [canReassign, open, team.length])

  if (!canReassign) return null

  async function submit() {
    if (!target || target === currentOwnerId) return
    setBusy(true); setError(null); setDone(null)
    try {
      const res = await api.post(`/sales/opportunities/${opportunityId}/reassign`,
                                 { owner_user_id: target })
      const to = team.find(t => t.id === target)?.full_name || 'the new owner'
      setDone(`Moved from ${currentOwnerName || 'unassigned'} to ${to}.`)
      setOpen(false)
      setTarget('')
      if (onReassigned) onReassigned(res)
    } catch (e) {
      // The API's own message is the useful one here — it says exactly why,
      // including "has no active membership in this brand sales organization".
      setError(e.detail || e.message || 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <div className="sw-reassign">
        <button className="sw-btn" onClick={() => setOpen(true)}>Reassign</button>
        {done ? <Chip tone="green">{done}</Chip> : null}
      </div>
    )
  }

  const others = team.filter(t => t.id !== currentOwnerId)

  return (
    <div className="sw-reassign">
      <select className="sw-select" value={target} disabled={busy}
              onChange={e => setTarget(e.target.value)}>
        <option value="">Reassign to…</option>
        {others.map(t => (
          <option key={t.id} value={t.id}>
            {t.full_name} — {t.role_label}
          </option>
        ))}
      </select>
      <button className="sw-btn sw-primary" disabled={!target || busy}
              onClick={submit}>
        {busy ? 'Moving…' : 'Confirm'}
      </button>
      <button className="sw-btn sw-ghost" disabled={busy}
              onClick={() => { setOpen(false); setTarget(''); setError(null) }}>
        Cancel
      </button>
      {team.length === 0 && !busy
        ? <span className="sw-subtle">Loading the team…</span> : null}
      {others.length === 0 && team.length > 0
        ? <span className="sw-subtle">Nobody else sells this brand.</span> : null}
      {error ? <span className="sw-err" style={{ margin: 0, padding: '6px 10px' }}>{error}</span> : null}
    </div>
  )
}
