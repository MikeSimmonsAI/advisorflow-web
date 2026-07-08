import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './Replies.css'

const CLASSIFICATION_CONFIG = {
  interested: { label: 'Hot Lead', color: 'green', icon: '🔥' },
  callback: { label: 'Callback', color: 'blue', icon: '📞' },
  question: { label: 'Question', color: 'purple', icon: '❓' },
  not_interested: { label: 'Not Interested', color: 'amber', icon: '👎' },
  wrong_number: { label: 'Wrong Number', color: 'neutral-dim', icon: '❌' },
  dnc: { label: 'DNC', color: 'red', icon: '🚫' },
  neutral: { label: 'Neutral', color: 'neutral', icon: '💬' },
}

const CLASSIFICATION_OPTIONS = [
  { value: 'interested', label: 'Hot Lead' },
  { value: 'callback', label: 'Callback' },
  { value: 'question', label: 'Question' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'not_interested', label: 'Not Interested' },
  { value: 'wrong_number', label: 'Wrong Number' },
  { value: 'dnc', label: 'DNC' },
]

function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  const [needsAttentionOnly, setNeedsAttentionOnly] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true'
  )
  const [loading, setLoading] = useState(true)
  const [actionBusyId, setActionBusyId] = useState(null)
  const [error, setError] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [classificationFilter, setClassificationFilter] = useState('')

  function loadReplies() {
    setLoading(true)
    setError('')
    api.get(`/sms/replies${needsAttentionOnly ? '?needs_attention=true' : ''}`)
      .then(setReplies)
      .catch((err) => setError(err.message || 'Could not load replies.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadReplies() }, [needsAttentionOnly])

  function updateReplyInState(updatedReply) {
    setReplies((current) =>
      current.map((reply) => (reply.id === updatedReply.id ? { ...reply, ...updatedReply } : reply))
    )
  }

  async function markReviewed(replyId) {
    setActionBusyId(replyId)
    setError('')
    try {
      const updatedReply = await api.patch(`/sms/replies/${replyId}/mark-reviewed`, {})
      updateReplyInState(updatedReply)
    } catch (err) {
      setError(err.message || 'Could not mark reply reviewed.')
    } finally {
      setActionBusyId(null)
    }
  }

  async function reclassify(replyId, classification) {
    setActionBusyId(replyId)
    setError('')
    try {
      const updatedReply = await api.patch(`/sms/replies/${replyId}/reclassify`, { classification })
      updateReplyInState(updatedReply)
    } catch (err) {
      setError(err.message || 'Could not reclassify reply.')
    } finally {
      setActionBusyId(null)
    }
  }

  const filteredReplies = replies.filter((reply) => {
    const matchesClassification = !classificationFilter || reply.classification === classificationFilter
    const q = searchQuery.trim().toLowerCase()
    const matchesSearch = !q || (reply.body || '').toLowerCase().includes(q)
    return matchesClassification && matchesSearch
  })

  const stats = {
    total: replies.length,
    attention: replies.filter((r) => r.classification === 'interested' || r.classification === 'callback').length,
    callbacks: replies.filter((r) => r.classification === 'callback').length,
    reviewed: replies.filter((r) => Boolean(r.reviewed_at)).length,
    dnc: replies.filter((r) => r.classification === 'dnc').length,
  }

  const priorityReplies = filteredReplies
    .filter((r) => r.classification === 'interested' || r.classification === 'callback')
    .slice(0, 4)

  return (
    <div className="replies-page">

      {/* ── Header ── */}
      <header className="replies-header">
        <div>
          <p className="replies-eyebrow">Reply command</p>
          <h1 className="page-title">Replies</h1>
          <p className="page-subtitle">Triage hot responses, callbacks, DNC requests, and questions.</p>
        </div>
        <label className="replies-attention-toggle">
          <div className={`replies-toggle-track ${needsAttentionOnly ? 'replies-toggle-track--on' : ''}`}
            onClick={() => setNeedsAttentionOnly(!needsAttentionOnly)}>
            <div className="replies-toggle-thumb" />
          </div>
          <span>Needs attention only</span>
        </label>
      </header>

      {/* ── KPI Cards ── */}
      <div className="replies-kpi-grid">
        {[
          { label: 'NEEDS ATTENTION', value: stats.attention, accent: 'red', icon: '🔥', sub: 'Interested + callback' },
          { label: 'CALLBACKS', value: stats.callbacks, accent: 'blue', icon: '📞', sub: 'Requesting timing' },
          { label: 'REVIEWED', value: stats.reviewed, accent: 'green', icon: '✅', sub: 'Already acknowledged' },
          { label: 'DNC / STOP', value: stats.dnc, accent: 'amber', icon: '🚫', sub: 'Handle carefully' },
        ].map(({ label, value, accent, icon, sub }) => (
          <div key={label} className={`replies-kpi-card replies-kpi-card--${accent}`}>
            <div className="replies-kpi-top">
              <span className="replies-kpi-label">{label}</span>
              <span className="replies-kpi-icon">{icon}</span>
            </div>
            <div className={`replies-kpi-value replies-kpi-value--${accent}`}>{loading ? '—' : value}</div>
            <div className="replies-kpi-sub">{sub}</div>
          </div>
        ))}
      </div>

      {/* ── Priority Lane ── */}
      {priorityReplies.length > 0 && (
        <section className="panel replies-priority-panel">
          <div className="panel-header">
            <h2 className="panel-title">🎯 Book-first priority lane</h2>
            <span className="panel-count">{priorityReplies.length}</span>
          </div>
          <div className="replies-priority-grid">
            {priorityReplies.map((reply) => {
              const config = CLASSIFICATION_CONFIG[reply.classification] || CLASSIFICATION_CONFIG.neutral
              const initials = (reply.lead_name || '??').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
              return (
                <button
                  key={reply.id}
                  className={`replies-priority-card replies-priority-card--${config.color}`}
                  onClick={() => reply.lead_id && navigate(`/leads/${reply.lead_id}`)}
                >
                  <div className="replies-priority-top">
                    <div className="replies-avatar">{initials}</div>
                    <div>
                      <div className="replies-priority-name">{reply.lead_name || 'Unknown lead'}</div>
                      <span className={`badge badge--${config.color}`}>{config.icon} {config.label}</span>
                    </div>
                    <span className="replies-time">{timeAgo(reply.received_at)}</span>
                  </div>
                  <p className="replies-priority-body">{reply.body}</p>
                </button>
              )
            })}
          </div>
        </section>
      )}

      {/* ── Filter Bar ── */}
      <div className="replies-filter-bar">
        <div className="replies-search-wrap">
          <span className="replies-search-icon">🔍</span>
          <input
            className="replies-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search reply text…"
          />
        </div>
        <select
          className="filter-select"
          value={classificationFilter}
          onChange={(e) => setClassificationFilter(e.target.value)}
        >
          <option value="">All classifications</option>
          {CLASSIFICATION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <span className="replies-count-pill">{loading ? '—' : filteredReplies.length} shown</span>
      </div>

      {error && <div className="panel replies-error">{error}</div>}

      {/* ── Reply Feed ── */}
      <section className="panel replies-feed-panel">
        {loading ? (
          <div className="empty-state">Loading replies…</div>
        ) : filteredReplies.length === 0 ? (
          <div className="empty-state">
            {needsAttentionOnly
              ? 'Nothing needs your attention right now.'
              : 'No replies yet. Once a lead responds, it\'ll land here.'}
          </div>
        ) : (
          <ul className="replies-feed">
            {filteredReplies.map((r) => {
              const config = CLASSIFICATION_CONFIG[r.classification] || CLASSIFICATION_CONFIG.neutral
              const isBusy = actionBusyId === r.id
              const reviewed = Boolean(r.reviewed_at)
              const initials = (r.lead_name || '??').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()

              return (
                <li
                  key={r.id}
                  className={`replies-card ${r.is_hot ? 'replies-card--hot' : ''} ${reviewed ? 'replies-card--reviewed' : ''}`}
                  onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}
                  style={{ cursor: r.lead_id ? 'pointer' : 'default' }}
                >
                  <div className="replies-card-left">
                    <div className={`replies-avatar replies-avatar--${config.color}`}>{initials}</div>
                  </div>
                  <div className="replies-card-body">
                    <div className="replies-card-top">
                      <div className="replies-card-meta">
                        <span className="replies-card-name">{r.lead_name || 'Unknown lead'}</span>
                        <span className={`badge badge--${config.color}`}>{config.icon} {config.label}</span>
                        {reviewed && <span className="badge badge--neutral-dim">✓ Reviewed</span>}
                      </div>
                      <span className="replies-time">{timeAgo(r.received_at)}</span>
                    </div>
                    <p className="replies-card-text">{r.body}</p>
                    <div className="replies-card-actions" onClick={(e) => e.stopPropagation()}>
                      <button
                        className="btn btn--secondary replies-action-btn"
                        disabled={isBusy || reviewed}
                        onClick={() => markReviewed(r.id)}
                      >
                        {reviewed ? '✓ Reviewed' : 'Mark reviewed'}
                      </button>
                      <div className="replies-reclassify">
                        <span className="replies-reclassify-label">Reclassify</span>
                        <select
                          className="filter-select replies-reclassify-select"
                          value={r.classification || 'neutral'}
                          disabled={isBusy}
                          onChange={(e) => reclassify(r.id, e.target.value)}
                        >
                          {CLASSIFICATION_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
