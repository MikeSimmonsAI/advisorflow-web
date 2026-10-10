// COMMUNICATIONS / REPLIES — the response command center (WS5, 2026-09-28).
//
// Left: the reply queue, searched, filtered, sorted and paginated ON THE SERVER
// (GET /communications/replies — no 200-row cap). Right: the selected
// contact's workspace (conversation, notes, tasks, contact info, composer).
// KPI cards read GET /communications/summary, which counts the caller's whole
// scope, not the page on screen. Nothing on this page invents a number.
//
// A reply is never turned into a lead here: every reply already belongs to a
// lead (the inbound webhook drops texts from unknown numbers), so "Convert to
// Lead" does not apply to anything in this queue and is not offered.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import CommsWorkspace from './comms/CommsWorkspace'
import { CLASSIFICATIONS, STATUSES, ClassTag, StatusTag, Tag, Icon, channelIcon, timeAgo } from './comms/commsShared'
import './Replies.css'
import '../styles/sciWorkspace.css'

const TABS = [
  { key: 'all', label: 'All replies' },
  { key: 'attention', label: 'Needs attention', count: 'needs_attention' },
  { key: 'callbacks', label: 'Callbacks', count: 'callbacks' },
  { key: 'dnc', label: 'DNC / Stop', count: 'dnc_stop' },
  { key: 'ai', label: 'AI handling', count: 'ai_handling' },
  { key: 'reviewed', label: 'Reviewed', count: 'reviewed' },
]

const QUICK = [
  { key: 'unreviewed', label: 'Unreviewed' },
  { key: 'question', label: 'Questions' },
  { key: 'interested', label: 'Positive' },
  { key: 'mine', label: 'Assigned to me' },
  { key: 'unassigned', label: 'Unassigned' },
]

const RANGES = [
  { key: '', label: 'All time' },
  { key: '1', label: 'Last 24 hours' },
  { key: '7', label: 'Last 7 days' },
  { key: '30', label: 'Last 30 days' },
  { key: '90', label: 'Last 90 days' },
]

const PAGE_SIZE = 25

function isoDaysAgo(days) {
  const d = new Date(Date.now() - Number(days) * 86400000)
  return d.toISOString().slice(0, 10)
}

function useDebounced(value, ms) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export default function Replies() {
  const [searchParams, setSearchParams] = useSearchParams()
  const user = getCurrentUser()
  const [tab, setTab] = useState(() => {
    const b = searchParams.get('tab')
    return TABS.some(t => t.key === b) ? b : 'all'
  })
  const [attentionOnly, setAttentionOnly] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true')
  const [q, setQ] = useState('')
  const debouncedQ = useDebounced(q, 300)
  const [classification, setClassification] = useState('')
  const [status, setStatus] = useState('')
  const [channel, setChannel] = useState('')
  const [assigned, setAssigned] = useState('')
  const [range, setRange] = useState('')
  const [sort, setSort] = useState('newest')
  const [quick, setQuick] = useState([])
  const [page, setPage] = useState(1)

  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState(null)
  const [assignees, setAssignees] = useState([])
  const [selected, setSelected] = useState(() => {
    const lead = searchParams.get('lead')
    return lead ? { leadId: lead, replyId: searchParams.get('reply') || null, snapshot: null } : null
  })
  const [composeOpen, setComposeOpen] = useState(false)
  const reqSeq = useRef(0)

  const params = useMemo(() => {
    const p = { page, page_size: PAGE_SIZE, sort }
    if (debouncedQ.trim()) p.q = debouncedQ.trim()
    const cls = new Set(classification ? [classification] : [])
    if (quick.includes('question')) cls.add('question')
    if (quick.includes('interested')) cls.add('interested')
    if (cls.size) p.classification = [...cls].join(',')
    if (status) p.status = status
    if (channel) p.channel = channel
    if (assigned) p.assigned_to = assigned
    if (quick.includes('unassigned')) p.assigned_to = 'unassigned'
    if (quick.includes('mine')) p.assigned_to = 'me'
    if (range) p.date_from = isoDaysAgo(range)
    if (quick.includes('unreviewed')) p.reviewed = false
    if (attentionOnly || tab === 'attention') p.needs_attention = true
    if (tab === 'callbacks') p.callbacks = true
    if (tab === 'dnc') p.dnc = true
    if (tab === 'ai') p.ai_handling = true
    if (tab === 'reviewed') p.reviewed = true
    return p
  }, [page, sort, debouncedQ, classification, quick, status, channel, assigned, range, attentionOnly, tab])

  // Any filter change returns to page 1.
  useEffect(() => { setPage(1) }, [debouncedQ, classification, quick, status, channel, assigned, range, attentionOnly, tab, sort])

  const loadList = useCallback(() => {
    const seq = ++reqSeq.current
    setLoading(true)
    setError('')
    api.get('/communications/replies', { params })
      .then(d => { if (seq === reqSeq.current) setData(d) })
      .catch(e => { if (seq === reqSeq.current) setError(e.message || 'Could not load replies.') })
      .finally(() => { if (seq === reqSeq.current) setLoading(false) })
  }, [params])

  const loadSummary = useCallback(() => {
    api.get('/communications/summary').then(setSummary).catch(() => setSummary(null))
  }, [])

  useEffect(() => { loadList() }, [loadList])
  useEffect(() => { loadSummary() }, [loadSummary])
  useEffect(() => {
    api.get('/communications/assignees').then(setAssignees).catch(() => setAssignees([]))
  }, [])

  // Keep the URL shareable and keep the bell's ?needs_attention=true working.
  useEffect(() => {
    const next = new URLSearchParams()
    if (attentionOnly) next.set('needs_attention', 'true')
    if (tab !== 'all') next.set('tab', tab)
    if (selected?.leadId) next.set('lead', selected.leadId)
    if (selected?.replyId) next.set('reply', selected.replyId)
    setSearchParams(next, { replace: true })
  }, [attentionOnly, tab, selected, setSearchParams])

  const items = data?.items || []
  const selectedReply = selected?.replyId
    ? (items.find(i => i.id === selected.replyId) || selected.snapshot)
    : null

  function onChanged() { loadList(); loadSummary() }
  function toggleQuick(k) { setQuick(qs => qs.includes(k) ? qs.filter(x => x !== k) : [...qs, k]) }
  function clearFilters() {
    setQ(''); setClassification(''); setStatus(''); setChannel(''); setAssigned(''); setRange(''); setQuick([]); setSort('newest')
  }
  const filtersActive = q || classification || status || channel || assigned || range || quick.length || sort !== 'newest'
  const canWrite = !(user && user._executive_observation)

  const kpis = [
    { key: 'needs_attention', label: 'Needs attention', icon: 'alert', tone: 'red', tab: 'attention' },
    { key: 'callbacks', label: 'Open callbacks', icon: 'phone', tone: 'blue', tab: 'callbacks' },
    { key: 'reviewed', label: 'Reviewed', icon: 'check', tone: 'green', tab: 'reviewed' },
    { key: 'dnc_stop', label: 'DNC / Stop', icon: 'ban', tone: 'rose', tab: 'dnc' },
  ]

  const totalPages = data?.pages || 0
  const from = data && data.total ? (data.page - 1) * data.page_size + 1 : 0
  const to = data ? Math.min(data.page * data.page_size, data.total) : 0

  return (
    <div className={`cc sci-ws ${selected ? 'cc--has-selection' : ''}`}>
      <header className="cc-head">
        <div className="cc-head-text">
          <h1>Communications / Replies</h1>
          <p>Triage replies, callbacks, questions and Do Not Contact requests in one place.</p>
        </div>
        <div className="cc-head-actions">
          <Link className="cc-btn" to="/conversation-queue">Conversation queue</Link>
          <label className="cc-switch">
            <input type="checkbox" checked={attentionOnly} onChange={e => setAttentionOnly(e.target.checked)} />
            <span className="cc-switch-track" aria-hidden="true"><span /></span>
            Needs attention only
          </label>
          {canWrite && (
            <button type="button" className="cc-btn cc-btn--primary" onClick={() => setComposeOpen(true)}>
              <Icon name="edit" size={14} /> Compose
            </button>
          )}
        </div>
      </header>

      <nav className="cc-tabs" role="tablist" aria-label="Reply views">
        {TABS.map(t => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`cc-tab ${tab === t.key ? 'is-active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
            {t.count && summary && summary[t.count] > 0 && <span className="cc-tab-count">{summary[t.count]}</span>}
          </button>
        ))}
      </nav>

      <section className="cc-kpis" aria-label="Reply summary">
        {kpis.map(k => (
          <button key={k.key} type="button" className={`cc-kpi ${tab === k.tab ? 'is-active' : ''}`}
            onClick={() => setTab(tab === k.tab ? 'all' : k.tab)}>
            <span className={`cc-kpi-icon cc-tone-${k.tone}`}><Icon name={k.icon} size={20} /></span>
            <span className="cc-kpi-text">
              <span className="cc-kpi-value">{summary ? summary[k.key] : '—'}</span>
              <span className="cc-kpi-label">{k.label}</span>
            </span>
          </button>
        ))}
      </section>

      <section className="cc-filters" aria-label="Search and filters">
        <label className="cc-search">
          <Icon name="search" />
          <input type="search" value={q} onChange={e => setQ(e.target.value)}
            placeholder="Search messages, name, phone or email…" aria-label="Search replies" />
        </label>
        <select value={classification} onChange={e => setClassification(e.target.value)} aria-label="Classification">
          <option value="">All classifications</option>
          {Object.entries(CLASSIFICATIONS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={channel} onChange={e => setChannel(e.target.value)} aria-label="Channel">
          <option value="">All channels</option>
          <option value="sms">SMS</option>
          <option value="email">Email</option>
        </select>
        <select value={assigned} onChange={e => setAssigned(e.target.value)} aria-label="Assignee">
          <option value="">Any assignee</option>
          <option value="unassigned">Unassigned</option>
          {assignees.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select value={range} onChange={e => setRange(e.target.value)} aria-label="Date range">
          {RANGES.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
        <select value={sort} onChange={e => setSort(e.target.value)} aria-label="Sort">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="name">Name A–Z</option>
        </select>
      </section>

      <div className="cc-quick">
        {QUICK.map(f => (
          <button key={f.key} type="button" className={`cc-chip ${quick.includes(f.key) ? 'is-on' : ''}`}
            aria-pressed={quick.includes(f.key)} onClick={() => toggleQuick(f.key)}>{f.label}</button>
        ))}
        {filtersActive ? <button type="button" className="cc-link" onClick={clearFilters}>Clear filters</button> : null}
      </div>

      {error && <div className="cc-flash is-error">{error}</div>}

      <div className="cc-grid">
        <section className="cc-queue" aria-label="Reply queue">
          <div className="cc-queue-head">
            <span>{data ? `${data.total.toLocaleString()} ${data.total === 1 ? 'reply' : 'replies'}` : ' '}</span>
            {loading && <span className="cc-muted">Loading…</span>}
          </div>
          <ul className="cc-queue-list">
            {!loading && items.length === 0 && (
              <li className="cc-empty">
                {filtersActive || tab !== 'all' || attentionOnly
                  ? 'No replies match these filters.'
                  : 'No replies yet. Customer replies appear here as they arrive.'}
              </li>
            )}
            {items.map(r => {
              const active = selected?.replyId === r.id
              return (
                <li key={r.id}>
                  <button type="button" className={`cc-row ${active ? 'is-active' : ''} ${!r.reviewed_at ? 'is-unread' : ''}`}
                    onClick={() => setSelected({ leadId: r.lead_id, replyId: r.id, snapshot: r })}>
                    <span className="cc-row-top">
                      <span className="cc-row-name">
                        {!r.reviewed_at && <span className="cc-dot" aria-label="Unreviewed" />}
                        {r.contact_name}
                      </span>
                      <span className="cc-row-time">{timeAgo(r.received_at)}</span>
                    </span>
                    <span className="cc-row-body">{r.body}</span>
                    <span className="cc-row-tags">
                      <span className="cc-chan"><Icon name={channelIcon(r.channel)} size={12} /> {r.channel?.toUpperCase()}</span>
                      <ClassTag value={r.classification} />
                      <StatusTag value={r.status} />
                      {r.is_dnc && r.classification !== 'dnc' && <Tag tone="red">DNC</Tag>}
                      {r.assigned_to_name && <span className="cc-owner"><Icon name="user" size={11} /> {r.assigned_to_name}</span>}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          {totalPages > 1 && (
            <div className="cc-pager">
              <span className="cc-muted">Showing {from}–{to} of {data.total.toLocaleString()}</span>
              <span className="cc-pager-btns">
                <button type="button" className="cc-iconbtn" disabled={page <= 1} onClick={() => setPage(p => p - 1)} aria-label="Previous page">
                  <Icon name="chevL" />
                </button>
                <span className="cc-pager-num">{page} / {totalPages}</span>
                <button type="button" className="cc-iconbtn" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} aria-label="Next page">
                  <Icon name="chevR" />
                </button>
              </span>
            </div>
          )}
        </section>

        <CommsWorkspace
          leadId={selected?.leadId || null}
          reply={selectedReply}
          assignees={assignees}
          canWrite={canWrite}
          onChanged={onChanged}
          onBack={() => setSelected(null)}
        />
      </div>

      {composeOpen && (
        <ComposePicker onClose={() => setComposeOpen(false)}
          onPick={lead => { setComposeOpen(false); setSelected({ leadId: lead.id, replyId: null, snapshot: null }) }} />
      )}
    </div>
  )
}

// New message: choose an existing contact (server-side search over leads in
// scope), then write in the same workspace — the same gate applies.
function ComposePicker({ onClose, onPick }) {
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 300)
  const [results, setResults] = useState([])
  const [err, setErr] = useState('')
  useEffect(() => {
    if (dq.trim().length < 2) { setResults([]); return }
    let alive = true
    api.get('/leads/', { params: { search: dq.trim(), page_size: 8, page: 1 } })
      .then(d => { if (alive) setResults(d.items || d || []) })
      .catch(e => { if (alive) setErr(e.message) })
    return () => { alive = false }
  }, [dq])
  return (
    <div className="cc-modal" role="dialog" aria-modal="true" aria-label="New message">
      <div className="cc-modal-card">
        <div className="cc-modal-head">
          <strong>New message</strong>
          <button type="button" className="cc-iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <label className="cc-search">
          <Icon name="search" />
          <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search contacts by name, phone or email…" />
        </label>
        {err && <div className="cc-flash is-error">{err}</div>}
        <ul className="cc-pick">
          {results.map(l => (
            <li key={l.id}>
              <button type="button" onClick={() => onPick(l)}>
                <strong>{`${l.first_name || ''} ${l.last_name || ''}`.trim() || 'Unnamed'}</strong>
                <span className="cc-muted">{l.phone || l.email || ''}</span>
              </button>
            </li>
          ))}
          {dq.trim().length >= 2 && results.length === 0 && !err && <li className="cc-muted cc-pad">No matching contacts.</li>}
        </ul>
        <p className="cc-muted cc-small">Messages still go through consent, Do Not Contact, suppression and contact-hours checks.</p>
      </div>
    </div>
  )
}
