/**
 * Conversation Queue - the manager's view of Conversation Intelligence.
 *
 *   GET /conversation-intel/insights   counters across the workspace
 *   GET /conversation-intel/queue      conversations by priority (server-scoped)
 *   GET /conversation-intel/search?q=  search what customers TOLD us (memory)
 *
 * Everything is computed by the server from the record; this page only
 * groups and links. Rows open the lead, where the Conversation Brain panel
 * shows the evidence (quotes and sources) behind each line.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './ConversationQueue.css'

const PRIORITIES = [
  ['respond_now', 'Respond now'],
  ['needs_human', 'Needs a person'],
  ['follow_up_today', 'Follow up today'],
  ['waiting_on_customer', 'Waiting on customer'],
  ['nurture', 'Nurture'],
  ['low_priority', 'Low priority'],
]

const INSIGHTS = [
  ['respond_now', 'Respond now'],
  ['unanswered_questions', 'Unanswered questions'],
  ['needs_human', 'Need a person'],
  ['overdue_replies', 'Replies at risk'],
  ['follow_up_today', 'Follow-ups today'],
  ['human_active', 'Team handling'],
  ['not_now', 'Said "not now"'],
  ['stopped', 'Stopped'],
]

const humanize = (s) => String(s || '').replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

function when(iso) {
  if (!iso) return ''
  const d = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function ConversationQueue() {
  const [insights, setInsights] = useState(null)
  const [queue, setQueue] = useState(null)
  const [priority, setPriority] = useState('')
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    api.get('/conversation-intel/insights').then(setInsights).catch((e) => setError(e?.message || 'Insights could not be loaded.'))
  }, [])

  useEffect(() => {
    setQueue(null)
    api.get('/conversation-intel/queue' + (priority ? '?priority=' + encodeURIComponent(priority) : ''))
      .then(setQueue).catch((e) => setError(e?.message || 'The queue could not be loaded.'))
  }, [priority])

  async function search(e) {
    e.preventDefault()
    if (q.trim().length < 2) return
    setSearching(true)
    try { setResults(await api.get('/conversation-intel/search?q=' + encodeURIComponent(q.trim()))) }
    catch (err) { setError(err?.message || 'Search failed.') }
    finally { setSearching(false) }
  }

  const counts = queue?.counts || {}
  const items = queue?.items || []

  return (
    <div className="cq">
      <header className="page-header">
        <div>
          <h1 className="page-title">Conversation Queue</h1>
          <p className="page-subtitle">Every active conversation, by what it needs next - from what customers actually said.</p>
        </div>
        <Link className="btn btn-secondary" to="/replies">Open replies inbox</Link>
      </header>

      {error && <div className="empty-state" role="alert">{error}</div>}

      <section className="cq-insights" aria-label="Insights">
        {INSIGHTS.map(([k, label]) => (
          <div key={k} className="cq-stat">
            <span className="cq-stat-n">{insights ? (insights[k] ?? 0) : '—'}</span>
            <span className="cq-stat-l">{label}</span>
          </div>
        ))}
      </section>

      {insights?.common_objections?.length > 0 && (
        <p className="cq-objections">
          Most common objections: {insights.common_objections.slice(0, 4).map(o => `${humanize(o.key || o[0])} (${o.count ?? o[1]})`).join(' · ')}
        </p>
      )}

      <div className="cq-tabs" role="tablist" aria-label="Priority">
        <button type="button" role="tab" aria-selected={!priority} className={'cq-tab' + (!priority ? ' is-on' : '')}
          onClick={() => setPriority('')}>All</button>
        {PRIORITIES.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={priority === k}
            className={'cq-tab' + (priority === k ? ' is-on' : '')} onClick={() => setPriority(k)}>
            {label} <span className="cq-count">{counts[k] ?? 0}</span>
          </button>
        ))}
      </div>

      <section className="panel cq-list">
        {queue === null && !error && <div className="empty-state">Loading…</div>}
        {queue !== null && items.length === 0 && <div className="empty-state">Nothing in this queue.</div>}
        <ul>
          {items.map((it) => (
            <li key={it.lead_id}>
              <Link to={'/leads/' + it.lead_id} className="cq-row">
                <span className={'cq-pill cq-pill--' + (it.priority || 'low_priority')}>{humanize(it.priority)}</span>
                <span className="cq-main">
                  <span className="cq-name">{it.name || 'Unnamed lead'}</span>
                  <span className="cq-detail">
                    {it.needs_human_reason || it.last_meaningful_event || humanize(it.state)}
                  </span>
                </span>
                <span className="cq-meta">
                  {it.mode === 'human_active' && <span className="cq-tag">Team handling</span>}
                  {it.has_open_question && <span className="cq-tag cq-tag--q">Open question</span>}
                  {it.follow_up_due && <span className="cq-tag">Follow up {it.follow_up_due}</span>}
                  <span className="cq-when">{when(it.last_inbound_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel cq-search">
        <form onSubmit={search} className="cq-search-form">
          <label htmlFor="cq-q" className="cq-search-label">Search what customers told us</label>
          <div className="cq-search-row">
            <input id="cq-q" className="filter-select" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. wife, retirement, moving, roof" />
            <button type="submit" className="btn btn-primary" disabled={searching || q.trim().length < 2}>
              {searching ? 'Searching…' : 'Search'}
            </button>
          </div>
        </form>
        {results && (results.results || []).length === 0 && <div className="empty-state">No matches.</div>}
        <ul>
          {(results?.results || []).map((r) => (
            <li key={r.lead_id} className="cq-hit">
              <Link to={'/leads/' + r.lead_id} className="cq-name">{r.name || 'Unnamed lead'}</Link>
              {(r.matches || []).slice(0, 3).map((m, i) => (
                <div key={i} className="cq-quote">“{m.quote || m.value}” <span className="cq-when">{humanize(m.key)}</span></div>
              ))}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
