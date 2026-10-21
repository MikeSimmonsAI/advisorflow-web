/**
 * Follow Ups — "People to contact, without the clutter." (Lane F, #281)
 *
 * The rep's open deals whose next action is due today or earlier, overdue
 * first. One click to call, text or open the lead. After a call the
 * "What happened?" panel saves the result and the next due date, the row
 * leaves the due list if it is no longer due, and the next person is offered.
 *
 * Data: GET /sales/opportunities (the same scoped list My Pipeline uses) and
 * /sales/my-day's recent activity for "Completed" — deals with a call or text
 * recorded today. Nothing here is sample data.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell, { useSales } from './SalesShell'
import NewProspect from './NewProspect'
import { followUpList, STAGE_LABELS } from './simpleSales'
import {
  Icon, DueStatus, ErrorLine, Skeleton, initialsOf, useNotice, useContactActions,
  CallTextSquares,
} from './SimpleParts'

const WORKED = ['call_outcome', 'text_logged']

function isToday(iso) {
  if (!iso) return false
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(String(iso)) ? iso : iso + 'Z')
  const n = new Date()
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
}

export default function FollowUps() {
  return (
    <SalesShell title="Follow Ups" subtitle="People to contact, without the clutter.">
      <FollowUpsBody />
    </SalesShell>
  )
}

function FollowUpsBody() {
  const nav = useNavigate()
  const ctx = useSales()
  const [deals, setDeals] = useState(null)
  const [recent, setRecent] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [nextUp, setNextUp] = useState(null)
  const [notice, notify] = useNotice()

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [o, d] = await Promise.all([
        api.get('/sales/opportunities'),
        api.get('/sales/my-day').catch(() => null),
      ])
      setDeals(o)
      setRecent(d?.recent_activity || [])
    } catch (e) {
      setError(e.message || 'Could not load your follow ups.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  // A manager's list is the brand; Follow Ups is their OWN people to contact.
  const mine = useMemo(() => {
    if (!deals) return []
    const all = (deals.stages || []).flatMap(s => s.opportunities || [])
    return deals.is_manager ? all.filter(c => c.owner_user_id === deals.viewer_user_id) : all
  }, [deals])

  const due = useMemo(() => followUpList(mine, 'today', query), [mine, query])
  const workedIds = useMemo(() => new Set(recent
    .filter(e => WORKED.includes(e.event_type) && isToday(e.occurred_at))
    .map(e => e.opportunity_id)), [recent])
  const completed = useMemo(() => followUpList(mine, 'all', query).filter(c => workedIds.has(c.id)),
    [mine, query, workedIds])

  const rows = filter === 'overdue' ? due.filter(c => c.due_bucket === 'overdue')
    : filter === 'today' ? due.filter(c => c.due_bucket === 'today')
    : filter === 'completed' ? completed
    : due
  const overdueCount = due.filter(c => c.due_bucket === 'overdue').length

  async function afterChange(changed) {
    // Work out who is next BEFORE reloading, from the list the rep was looking at.
    const idx = rows.findIndex(r => r.id === changed.id)
    const after = rows.slice(idx + 1).concat(rows.slice(0, Math.max(idx, 0)))
      .find(r => r.id !== changed.id)
    await load()
    setNextUp(after || null)
  }

  const contact = useContactActions({
    me: ctx?.user?.full_name,
    notify,
    onChanged: afterChange,
    onBook: opp => nav('/sales/book?opp=' + encodeURIComponent(opp.id)),
  })

  const FILTERS = [
    { key: 'all', label: 'All', n: due.length },
    { key: 'overdue', label: 'Overdue', n: overdueCount, alert: true },
    { key: 'today', label: 'Today' },
    { key: 'completed', label: 'Completed', n: completed.length },
  ]

  return (
    <>
      <div className="ss-sections">
        <div>
          <h2>People to contact today ({loading && !deals ? '…' : due.length})</h2>
          <p>One click to call, text, or open a lead.</p>
        </div>
        <button type="button" className="ss-btn ss-white" onClick={() => setCreating(true)}>
          <Icon name="plus" /> New Lead
        </button>
      </div>

      <div className="ss-toolbar">
        <div className="ss-filters" role="group" aria-label="Show">
          {FILTERS.map(f => (
            <button key={f.key} type="button" aria-pressed={filter === f.key}
                    className={'ss-pill' + (f.alert && f.n ? ' ss-alert' : '')}
                    onClick={() => setFilter(f.key)}>
              {f.label}{f.n != null && (f.key !== 'all') ? ' (' + f.n + ')' : ''}
            </button>
          ))}
        </div>
        <label className="ss-search">
          <Icon name="search" />
          <span className="ss-sr">Search name or company</span>
          <input type="search" placeholder="Search name or company…" value={query}
                 onChange={e => setQuery(e.target.value)} />
        </label>
      </div>

      <ErrorLine error={error} onRetry={load} />
      {nextUp && (
        <div className="ss-okbar" role="status">
          <span>Next up: <strong>{nextUp.contact_name || nextUp.company_name}</strong>
            {nextUp.next_action ? ' — ' + nextUp.next_action : ''}</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="ss-btn ss-green ss-xs" disabled={!nextUp.phone}
                  onClick={() => { const o = nextUp; setNextUp(null); contact.call(o) }}>
            <Icon name="phone" /> Call
          </button>
          <button type="button" className="ss-btn ss-white ss-xs"
                  onClick={() => nav('/sales/leads/' + nextUp.id)}>Open</button>
          <button type="button" className="ss-link" onClick={() => setNextUp(null)}>Dismiss</button>
        </div>
      )}

      <section className="ss-panel" aria-labelledby="ss-fu-h">
        <div className="ss-panelhd">
          <div>
            <h3 id="ss-fu-h">Follow Ups</h3>
            <p>{filter === 'completed' ? 'Called or texted today' : 'Overdue first, then due today'}</p>
          </div>
          <span className="ss-count-note">{rows.length} shown</span>
        </div>
        {loading && !deals ? <Skeleton rows={5} /> : rows.length === 0 ? (
          <div className="ss-emptylist">
            <strong>{query ? 'No one matches that search.'
              : filter === 'completed' ? 'Nothing logged yet today.'
              : filter === 'overdue' ? 'Nothing overdue.'
              : 'You are all caught up.'}</strong>
            {!query && filter !== 'completed' && <>Nobody is due right now. <button type="button" className="ss-link"
              onClick={() => nav('/sales/prospects')}>See all leads</button></>}
          </div>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {rows.map(c => (
              <li key={c.id} className="ss-listrow">
                <div className="ss-avatar" aria-hidden="true">{initialsOf(c.contact_name || c.company_name)}</div>
                <button type="button" className="ss-personinfo" onClick={() => nav('/sales/leads/' + c.id)}>
                  <strong>{c.contact_name || c.company_name}</strong>
                  <small>{[c.contact_name ? c.company_name : null, STAGE_LABELS[c.stage] || c.stage_label]
                    .filter(Boolean).join(' · ')}</small>
                </button>
                <div className="ss-rowmeta">
                  {workedIds.has(c.id) && filter === 'completed'
                    ? <span className="ss-status ss-sgreen">Done today</span>
                    : <DueStatus bucket={c.due_bucket} iso={c.next_action_due_at} />}
                  <span>{c.next_action || 'No next step set'}</span>
                </div>
                <div className="ss-rowactions">
                  <CallTextSquares opp={c} onCall={contact.call} onText={contact.text} />
                  <button type="button" className="ss-btn ss-soft ss-xs"
                          onClick={() => nav('/sales/leads/' + c.id)}>Open</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {contact.modals}
      {creating && (
        <NewProspect onClose={() => setCreating(false)}
                     onCreated={opp => { setCreating(false); nav('/sales/leads/' + opp.id) }} />
      )}
      {notice}
    </>
  )
}
