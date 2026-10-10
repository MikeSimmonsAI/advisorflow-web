import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './WorkQueue.css'
import '../styles/sciWorkspace.css'

const emptyQueue = {
  needs_text: [],
  needs_reply: [],
  cadence_due: [],
  outcomes_needed: [],
}

const sections = [
  {
    key: 'needs_text',
    title: 'Needs Text',
    subtitle: 'New assigned leads not contacted yet.',
    accent: 'blue',
    empty: 'No new leads waiting for first contact.',
  },
  {
    key: 'needs_reply',
    title: 'Needs Reply',
    subtitle: 'Interested and callback replies still unreviewed.',
    accent: 'red',
    empty: 'No hot replies need review right now.',
  },
  {
    key: 'cadence_due',
    title: 'Cadence Due',
    subtitle: 'Active cadence touches due now.',
    accent: 'amber',
    empty: 'No cadence touches are due.',
  },
  {
    key: 'outcomes_needed',
    title: 'Outcomes Needed',
    subtitle: 'Booked leads missing appointment outcomes.',
    accent: 'green',
    empty: 'No booked leads are missing outcomes.',
  },
]

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function itemTimestamp(sectionKey, item) {
  if (sectionKey === 'needs_reply') return item.received_at
  if (sectionKey === 'cadence_due') return item.next_touch_due_at
  if (sectionKey === 'outcomes_needed') return item.updated_at
  return item.created_at
}

function itemMeta(sectionKey, item) {
  if (sectionKey === 'needs_reply') return item.classification?.replaceAll('_', ' ') || 'reply'
  if (sectionKey === 'cadence_due') return `Touch ${(item.current_touch_number ?? 0) + 1}`
  if (sectionKey === 'outcomes_needed') return 'Booked'
  return item.tier?.replaceAll('_', ' ') || item.status || 'lead'
}

function QueueItem({ item, sectionKey, accent, onOpen }) {
  return (
    <button className="workqueue-item" onClick={() => onOpen(item.lead_id)}>
      <div className={`workqueue-item-signal workqueue-item-signal--${accent}`} />
      <div className="workqueue-item-main">
        <div className="workqueue-item-topline">
          <strong>{item.name || 'Unnamed lead'}</strong>
          <span className="mono">{item.phone || 'No phone'}</span>
        </div>
        <p>{item.context || item.body || 'Open lead for details.'}</p>
        {item.body ? <small>{item.body}</small> : null}
      </div>
      <div className="workqueue-item-meta">
        <span className={`workqueue-pill workqueue-pill--${accent}`}>{itemMeta(sectionKey, item)}</span>
        <span className="mono">{formatDate(itemTimestamp(sectionKey, item))}</span>
      </div>
    </button>
  )
}

// ── Operational queues (SS7) ────────────────────────────────────────────────
// Display-only views over GET /workqueue/queues and /workqueue/queues/{name}.
// Nothing here sends; every action happens from the lead's own page.

const QUEUE_TABS = [
  { key: 'sms', label: 'SMS', accent: 'blue' },
  { key: 'voice', label: 'Voice', accent: 'amber' },
  { key: 'email', label: 'Email', accent: 'green' },
  { key: 'follow_up', label: 'Follow-up', accent: 'red' },
]

// follow_up has no qualification step, so it has no excluded leads to explain.
const QUEUES_WITH_EXCLUSIONS = new Set(['sms', 'voice', 'email'])

function queueCount(key, counts) {
  if (!counts) return null
  if (key === 'follow_up') return counts.total ?? 0
  if (key === 'voice') return (counts.callbacks_due || 0) + (counts.ready || 0)
  return (counts.ready || 0) + (counts.review || 0)
}

function humanize(value) {
  return value ? String(value).replaceAll('_', ' ') : ''
}

function QueueRow({ row, accent, showBlockers }) {
  const blockers = Array.isArray(row.blockers) ? row.blockers : []
  return (
    <Link className="workqueue-item workqueue-queue-row" to={`/leads/${row.lead_id}`}>
      <div className={`workqueue-item-signal workqueue-item-signal--${accent}`} />
      <div className="workqueue-item-main">
        <div className="workqueue-item-topline">
          <strong>{row.name || 'Unnamed lead'}</strong>
          <span className="mono">{row.phone || row.email || 'No contact'}</span>
        </div>
        <p>{row.reason || row.stage_label || humanize(row.stage) || 'Open lead for details.'}</p>
        {row.next_action ? <small>{row.next_action}</small> : null}
        {showBlockers && blockers.length > 0 ? (
          <ul className="workqueue-blockers">
            {blockers.map((b, i) => (
              <li key={`${b.code || 'reason'}-${i}`}>{b.label || humanize(b.code)}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="workqueue-item-meta">
        <span className={`workqueue-pill workqueue-pill--${accent}`}>
          {row.priority ? humanize(row.priority).toLowerCase() : humanize(row.stage_label || row.stage || row.bucket) || 'lead'}
        </span>
        {row.due_at ? <span className="mono">{formatDate(row.due_at)}</span> : null}
        {row.attempts ? <span className="mono">{row.attempts} call{row.attempts === 1 ? '' : 's'}</span> : null}
      </div>
    </Link>
  )
}

function QueueGroup({ title, rows, accent, empty, showBlockers }) {
  if (!rows) return null
  return (
    <div className="workqueue-queue-group">
      <h3 className="workqueue-queue-group-title">
        {title} <span className="mono">{rows.length}</span>
      </h3>
      {rows.length === 0 ? (
        <div className="empty-state">{empty}</div>
      ) : (
        <div className="workqueue-list">
          {rows.map((row, i) => (
            <QueueRow key={`${row.lead_id}-${i}`} row={row} accent={accent} showBlockers={showBlockers} />
          ))}
        </div>
      )}
    </div>
  )
}

function OperationalQueues() {
  const [summary, setSummary] = useState(null)
  const [active, setActive] = useState('sms')
  const [showExcluded, setShowExcluded] = useState(false)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api.get('/workqueue/queues')
      .then((res) => { if (!cancelled) setSummary(res) })
      .catch(() => { if (!cancelled) setSummary(null) })
    return () => { cancelled = true }
  }, [])

  const canExclude = QUEUES_WITH_EXCLUSIONS.has(active)
  const wantExcluded = canExclude && showExcluded

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setData(null)
    const qs = wantExcluded ? '?include_excluded=true' : ''
    api.get(`/workqueue/queues/${active}${qs}`)
      .then((res) => { if (!cancelled) setData(res) })
      .catch((err) => { if (!cancelled) setError(err.message || 'Could not load this queue.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [active, wantExcluded])

  const countsByQueue = useMemo(() => {
    const out = {}
    for (const q of summary?.queues || []) out[q.queue] = q.counts
    return out
  }, [summary])

  const tab = QUEUE_TABS.find((t) => t.key === active) || QUEUE_TABS[0]
  const counts = data?.counts || {}

  return (
    <section className="panel workqueue-section workqueue-queues">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Queues</h2>
          <p className="workqueue-section-subtitle">
            Derived fresh from lead state and channel qualification. View only - open a lead to act.
          </p>
        </div>
        {canExclude ? (
          <label className="workqueue-queue-toggle">
            <input
              type="checkbox"
              checked={showExcluded}
              onChange={(e) => setShowExcluded(e.target.checked)}
            />
            Show excluded + why
          </label>
        ) : null}
      </div>

      <div className="workqueue-tabs" role="tablist">
        {QUEUE_TABS.map((t) => {
          const n = queueCount(t.key, countsByQueue[t.key])
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={active === t.key}
              className={`workqueue-tab${active === t.key ? ' workqueue-tab--active' : ''}`}
              onClick={() => setActive(t.key)}
            >
              {t.label}
              <span className={`workqueue-count workqueue-count--${t.accent}`}>{n == null ? '—' : n}</span>
            </button>
          )
        })}
      </div>

      {error ? <div className="workqueue-alert">{error}</div> : null}

      {loading ? (
        <div className="empty-state">Loading {tab.label} queue...</div>
      ) : !data ? null : (
        <div className="workqueue-queue-body">
          {data.authoritative === false ? (
            <p className="workqueue-section-subtitle">
              Qualification for this channel is advisory, not authoritative.
            </p>
          ) : null}

          {active === 'voice' ? (
            <QueueGroup
              title="Callbacks due"
              rows={data.callbacks || []}
              accent="red"
              empty="No callbacks owed."
            />
          ) : null}

          <QueueGroup
            title={active === 'follow_up' ? 'Owed now' : 'Ready'}
            rows={data.items || []}
            accent={tab.accent}
            empty={active === 'follow_up' ? 'Nothing is owed right now.' : `No leads ready for ${tab.label}.`}
          />

          {data.review_items ? (
            <QueueGroup
              title="Needs review"
              rows={data.review_items}
              accent="amber"
              empty="Nothing waiting on review."
              showBlockers
            />
          ) : null}

          {data.has_more ? (
            <p className="workqueue-section-subtitle">
              Showing the first page; {counts.ready ?? counts.total ?? 'more'} in total.
            </p>
          ) : null}

          {wantExcluded ? (
            <QueueGroup
              title={`Excluded${counts.excluded != null ? ` (${counts.excluded} total)` : ''}`}
              rows={data.excluded_items || []}
              accent="red"
              empty="No leads were excluded from this queue."
              showBlockers
            />
          ) : null}
        </div>
      )}
    </section>
  )
}

// ── MY TASKS (My Work, Oct 2026) ────────────────────────────────────────────
// Real tasks from GET /work/tasks - the ones Conversation Brain, the Replies
// workspace and people create. Completing one is PATCH /work/tasks/{id}
// {status: done}; nothing here is inferred from status text.
const TASK_TABS = [
  { key: 'priority', label: 'Priority', params: { status: 'open', due: 'overdue' } },
  { key: 'today', label: 'Today', params: { status: 'open', due: 'today' } },
  { key: 'upcoming', label: 'Upcoming', params: { status: 'open', due: 'upcoming' } },
  { key: 'open', label: 'All open', params: { status: 'open' } },
  { key: 'done', label: 'Completed', params: { status: 'done' } },
]

function MyTasks() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('priority')
  const [who, setWho] = useState('me')
  const [data, setData] = useState(null)
  const [counts, setCounts] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null)

  const load = useCallback(() => {
    const def = TASK_TABS.find(x => x.key === tab) || TASK_TABS[0]
    const params = { ...def.params, page_size: 50 }
    if (who === 'me') params.assigned = 'me'
    setError('')
    api.get('/work/tasks', { params }).then(setData).catch(e => { setData(null); setError(e.message || 'Could not load tasks.') })
    Promise.all(TASK_TABS.map(x => api.get('/work/tasks', { params: { ...x.params, page_size: 1, ...(who === 'me' ? { assigned: 'me' } : {}) } })
      .then(d => [x.key, d.total]).catch(() => [x.key, null])))
      .then(pairs => setCounts(Object.fromEntries(pairs)))
  }, [tab, who])
  useEffect(() => { load() }, [load])

  async function setStatus(task, status) {
    setBusy(task.id)
    try { await api.patch(`/work/tasks/${task.id}`, { status }); load() }
    catch (e) { setError(e.message || 'Could not update the task.') }
    finally { setBusy(null) }
  }

  const items = data?.items || []
  return (
    <section className="panel workqueue-section workqueue-tasks" aria-labelledby="wq-tasks-h">
      <div className="panel-header">
        <div>
          <h2 className="panel-title" id="wq-tasks-h">Tasks</h2>
          <p className="workqueue-section-subtitle">Callbacks, follow-ups and handoffs people and the AI recorded. Completing one is saved with your name.</p>
        </div>
        <select className="filter-select" aria-label="Whose tasks" value={who} onChange={e => setWho(e.target.value)}>
          <option value="me">Assigned to me</option>
          <option value="all">Everyone I can see</option>
        </select>
      </div>
      <div className="wq-tabs" role="tablist" aria-label="Task timing">
        {TASK_TABS.map(x => (
          <button key={x.key} type="button" role="tab" aria-selected={tab === x.key} className={tab === x.key ? 'on' : ''} onClick={() => setTab(x.key)}>
            {x.label}{counts[x.key] != null && <span className="wq-count">{counts[x.key]}</span>}
          </button>
        ))}
      </div>
      {error ? <div className="workqueue-alert" role="alert">{error}</div> : null}
      {!data && !error ? <div className="empty-state">Loading tasks…</div> : null}
      {data && items.length === 0 ? (
        <div className="empty-state">{tab === 'done' ? 'No completed tasks yet.' : tab === 'priority' ? 'Nothing overdue.' : 'No tasks here.'}</div>
      ) : null}
      {items.length > 0 && (
        <ul className="wq-tasklist">
          {items.map(task => (
            <li key={task.id} className={`wq-task${task.overdue ? ' wq-task--overdue' : ''}`}>
              <div className="wq-task-main">
                <strong>{task.title}</strong>
                <div className="wq-task-meta">
                  {task.lead_name && <span>{task.lead_name}</span>}
                  {task.due_at && <span className={task.overdue ? 'wq-overdue' : ''}>{task.overdue ? 'Overdue · ' : 'Due '}{formatDate(task.due_at)}</span>}
                  {task.assigned_to_name && <span>Owner: {task.assigned_to_name}</span>}
                  {task.source && task.source !== 'manual' && <span>From {humanize(task.source)}</span>}
                  {task.status === 'done' && task.completed_at && <span>Done {formatDate(task.completed_at)}</span>}
                </div>
                {task.details && <p className="wq-task-details">{task.details}</p>}
              </div>
              <div className="wq-task-actions">
                {task.lead_id && <button type="button" className="btn btn--secondary" onClick={() => navigate(`/leads/${task.lead_id}`)}>Open contact</button>}
                {task.status === 'open'
                  ? <button type="button" className="btn btn--primary" disabled={busy === task.id} onClick={() => setStatus(task, 'done')}>{busy === task.id ? 'Saving…' : 'Mark done'}</button>
                  : <button type="button" className="btn btn--secondary" disabled={busy === task.id} onClick={() => setStatus(task, 'open')}>Reopen</button>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {data && data.total > items.length && <p className="workqueue-section-subtitle">Showing {items.length} of {data.total}.</p>}
    </section>
  )
}

export default function WorkQueue() {
  const navigate = useNavigate()
  const [queue, setQueue] = useState(emptyQueue)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // Wholesale exceptions assigned to this person. Only people who can work the
  // exception queue get an answer; everyone else simply sees no card.
  const [exceptions, setExceptions] = useState(null)

  // Each bucket holds at most 100 rows; `totals` (when the server sends it) is
  // the real size of each, so the counts never stop at the cap.
  const countOf = (key) => (typeof queue.totals?.[key] === 'number' ? queue.totals[key] : (queue[key]?.length || 0))
  const total = useMemo(() => sections.reduce((sum, section) => sum + countOf(section.key), 0), [queue])

  async function loadQueue() {
    setError('')
    setLoading(true)
    try {
      const data = await api.get('/workqueue/today')
      setQueue({ ...emptyQueue, ...data })
      api.get('/wholesale/exceptions/summary').then(setExceptions).catch(() => setExceptions(null))
    } catch (err) {
      setError(err.message || "Could not load today's work.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadQueue()
  }, [])

  function openLead(leadId) {
    if (leadId) navigate(`/leads/${leadId}`)
  }

  return (
    <div className="workqueue-page sci-ws">
      <header className="page-header workqueue-header">
        <div>
          <p className="workqueue-eyebrow">My Work</p>
          <h1 className="page-title">Today&apos;s Work</h1>
          <p className="page-subtitle">Tasks, replies, cadence touches and missing outcomes that need action now.</p>
        </div>
        <div className="workqueue-summary panel">
          <SignalPulse color={total > 0 ? 'blue' : 'green'} label={total > 0 ? 'Action needed' : 'Clear'} />
          <strong>{loading ? '—' : total}</strong>
          <span>open work items</span>
          <button className="btn btn--secondary" onClick={loadQueue} disabled={loading}>Refresh</button>
        </div>
      </header>

      {error ? <div className="workqueue-alert">{error}</div> : null}

      {exceptions ? (
        <section className="panel workqueue-section workqueue-section--red" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Exceptions</h2>
              <p className="workqueue-section-subtitle">
                Wholesale work that needs a person - assigned to you
                {exceptions.manager ? `; ${exceptions.unassigned || 0} unassigned, ${exceptions.escalated || 0} escalated to you` : ''}.
              </p>
            </div>
            <span className="workqueue-count workqueue-count--red">{exceptions.assigned_to_me}</span>
          </div>
          <button className="btn btn--secondary" onClick={() => navigate('/wholesale/exceptions')}>
            Open exception queue
          </button>
        </section>
      ) : null}

      <MyTasks />

      <div className="workqueue-grid">
        {sections.map((section) => {
          const items = queue[section.key] || []
          return (
            <section key={section.key} className={`panel workqueue-section workqueue-section--${section.accent}`}>
              <div className="panel-header">
                <div>
                  <h2 className="panel-title">{section.title}</h2>
                  <p className="workqueue-section-subtitle">
                    {section.subtitle}
                    {!loading && countOf(section.key) > items.length ? ` Showing the first ${items.length}.` : ''}
                  </p>
                </div>
                <span className={`workqueue-count workqueue-count--${section.accent}`}>{loading ? '—' : countOf(section.key)}</span>
              </div>

              {loading ? (
                <div className="empty-state">Loading {section.title.toLowerCase()}...</div>
              ) : items.length === 0 ? (
                <div className="empty-state">{section.empty}</div>
              ) : (
                <div className="workqueue-list">
                  {items.map((item) => (
                    <QueueItem
                      key={`${section.key}-${item.reply_id || item.cadence_state_id || item.lead_id}`}
                      item={item}
                      sectionKey={section.key}
                      accent={section.accent}
                      onOpen={openLead}
                    />
                  ))}
                </div>
              )}
            </section>
          )
        })}
      </div>

      <OperationalQueues />
    </div>
  )
}
