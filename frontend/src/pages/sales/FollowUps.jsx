/**
 * Follow Ups — "People to contact, without the clutter." (Lane F, #281)
 *
 * The rep's open deals whose next action is due today or earlier, overdue
 * first. One click to call, text or open the lead. After a call the
 * "What happened?" panel saves the result and the next due date, the row
 * leaves the due list if it is no longer due, and the next person is offered.
 *
 * Data: GET /sales/opportunities (the same scoped list My Pipeline uses) and
 * /sales/my-day?scope=mine's `worked_today` for "Completed" — every deal this
 * person called or texted since the team's midnight, counted by the server
 * over the whole day. Nothing here is sample data.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell, { useSales } from './SalesShell'
import NewProspect from './NewProspect'
import { followUpList, STAGE_LABELS, telHref } from './simpleSales'
import {
  Icon, DueStatus, ErrorLine, Skeleton, initialsOf, useNotice, useContactActions,
  CallTextSquares,
} from './SimpleParts'

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
  const [workedList, setWorkedList] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('due')
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [nextUp, setNextUp] = useState(null)
  const [notice, notify] = useNotice()

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [o, d] = await Promise.all([
        // include_lost: a lead called and then closed as Lost today still
        // counts as Completed. Due lists only ever show open deals.
        api.get('/sales/opportunities?include_lost=true'),
        api.get('/sales/my-day?scope=mine').catch(() => null),
      ])
      setDeals(o)
      setWorkedList(d?.worked_today?.opportunity_ids || [])
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
    const all = (deals.stages || []).flatMap(s => s.opportunities || []).concat(deals.lost || [])
    return deals.is_manager ? all.filter(c => c.owner_user_id === deals.viewer_user_id) : all
  }, [deals])

  // ONE rule for every count on this page, the sidebar badge and My Day:
  // followUpList over the person's own open deals, in the TEAM's time zone.
  // Counts never depend on the search box; only the rows shown do.
  const tz = ctx?.brand_sales_org?.timezone
  const dueAll = useMemo(() => followUpList(mine, 'today', '', new Date(), tz), [mine, tz])
  const due = useMemo(() => followUpList(mine, 'today', query, new Date(), tz), [mine, query, tz])
  const workedIds = useMemo(() => new Set(workedList), [workedList])
  // Completed covers every deal worked today, whatever its status now — a
  // follow-up that was called and then marked Won still counts as done.
  const completedAll = useMemo(() => mine.filter(c => workedIds.has(c.id)), [mine, workedIds])
  const completed = useMemo(() => {
    const q = query.trim().toLowerCase()
    return completedAll
      .filter(c => !q || [c.company_name, c.contact_name, c.phone, c.email, c.next_action]
        .filter(Boolean).some(v => String(v).toLowerCase().includes(q)))
      .map(c => ({ ...c, due_bucket: 'done' }))
  }, [completedAll, query])

  const rows = filter === 'overdue' ? due.filter(c => c.due_bucket === 'overdue')
    : filter === 'today' ? due.filter(c => c.due_bucket === 'today')
    : filter === 'completed' ? completed
    : due
  const overdueCount = dueAll.filter(c => c.due_bucket === 'overdue').length
  const todayCount = dueAll.filter(c => c.due_bucket === 'today').length

  async function afterChange(changed) {
    // Work out who is next BEFORE reloading, from the list the rep was looking at.
    const idx = rows.findIndex(r => r.id === changed.id)
    const after = rows.slice(idx + 1).concat(rows.slice(0, Math.max(idx, 0)))
      .find(r => r.id !== changed.id)
    await load()
    // The sidebar badge is the same count; refresh it with the list.
    ctx?.refreshCounts && ctx.refreshCounts()
    setNextUp(after || null)
  }

  const contact = useContactActions({
    me: ctx?.user?.full_name, tz,
    notify,
    onChanged: afterChange,
    onBook: opp => nav('/sales/book?opp=' + encodeURIComponent(opp.id)),
  })

  // Due Now = Overdue + Today (today or earlier, still open). Overdue and
  // Today never overlap, so the two always add up to Due Now.
  const FILTERS = [
    { key: 'due', label: 'Due Now', n: dueAll.length,
      title: 'Everything due today or earlier, overdue first' },
    { key: 'overdue', label: 'Overdue', n: overdueCount, alert: true,
      title: 'The due date or time has passed' },
    { key: 'today', label: 'Today', n: todayCount,
      title: 'Due later today, not overdue yet' },
    { key: 'completed', label: 'Completed', n: completedAll.length,
      title: 'Called or texted today' },
  ]

  return (
    <>
      <div className="ss-sections">
        <div>
          <h2>People to Contact Now ({loading && !deals ? '…' : dueAll.length})</h2>
          <p>Overdue first, then today's follow-ups.</p>
        </div>
        <button type="button" className="ss-btn ss-white" onClick={() => setCreating(true)}>
          <Icon name="plus" /> New Lead
        </button>
      </div>

      <div className="ss-toolbar">
        <div className="ss-filters" role="group" aria-label="Show">
          {FILTERS.map(f => (
            <button key={f.key} type="button" aria-pressed={filter === f.key} title={f.title}
                    className={'ss-pill' + (f.alert && f.n ? ' ss-alert' : '')}
                    onClick={() => setFilter(f.key)}>
              {f.label} ({f.n})
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
          {telHref(nextUp.phone) && !nextUp.do_not_contact && (
            <button type="button" className="ss-btn ss-green ss-xs"
                    onClick={() => { const o = nextUp; setNextUp(null); contact.call(o) }}>
              <Icon name="phone" /> Call
            </button>
          )}
          <button type="button" className="ss-btn ss-white ss-xs"
                  onClick={() => nav('/sales/leads/' + nextUp.id)}>Open</button>
          <button type="button" className="ss-link" onClick={() => setNextUp(null)}>Dismiss</button>
        </div>
      )}

      <section className="ss-panel" aria-labelledby="ss-fu-h">
        <div className="ss-panelhd">
          <div>
            <h3 id="ss-fu-h">Follow Ups</h3>
            <p>{filter === 'completed' ? 'Called or texted today (any time since your team\'s midnight)'
              : filter === 'overdue' ? 'Past their due date or time'
              : filter === 'today' ? 'Due later today'
              : 'Overdue first, then today\'s follow-ups'}</p>
          </div>
          <span className="ss-count-note">{rows.length} shown</span>
        </div>
        {loading && !deals ? <Skeleton rows={5} /> : rows.length === 0 ? (
          <div className="ss-emptylist">
            <strong>{query ? 'No one matches that search.'
              : filter === 'completed' ? 'Nothing logged yet today.'
              : filter === 'overdue' ? 'Nothing overdue.'
              : filter === 'today' ? 'Nothing else due later today.'
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
