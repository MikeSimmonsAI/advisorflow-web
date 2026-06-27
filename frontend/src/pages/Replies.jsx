import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import '../styles/shared.css'
import './Replies.css'

const CLASSIFICATION_CONFIG = {
  interested: { label: 'Hot Lead', color: 'green' },
  callback: { label: 'Callback Requested', color: 'blue' },
  question: { label: 'Question', color: 'purple' },
  not_interested: { label: 'Not Interested', color: 'amber' },
  wrong_number: { label: 'Wrong Number', color: 'neutral-dim' },
  dnc: { label: 'DNC', color: 'red' },
  neutral: { label: 'Neutral', color: 'neutral' },
}

const CLASSIFICATION_OPTIONS = [
  { value: 'interested', label: 'Hot Lead' },
  { value: 'callback', label: 'Callback Requested' },
  { value: 'question', label: 'Question' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'not_interested', label: 'Not Interested' },
  { value: 'wrong_number', label: 'Wrong Number' },
  { value: 'dnc', label: 'DNC' },
]

// Action-center scorecards - per Mike's explicit request that Replies
// "should not just send me back to the lead sheet... it should feel
// like an action center, not just a message list." Each card's `key`
// matches exactly the field names from GET /sms/replies/counts and the
// bucket= values GET /sms/replies accepts, so clicking a card and the
// number it shows always agree with each other.
const BUCKET_CARDS = [
  { key: 'needs_follow_up', label: 'Needs follow-up', accent: 'red' },
  { key: 'hot', label: 'Hot replies', accent: 'green' },
  { key: 'callback', label: 'Callbacks', accent: 'blue' },
  { key: 'question', label: 'Questions', accent: 'purple' },
  { key: 'not_interested', label: 'Not interested', accent: 'amber' },
  { key: 'dnc', label: 'DNC / stop', accent: 'red' },
  { key: 'reviewed', label: 'Reviewed', accent: 'neutral' },
]

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  const [counts, setCounts] = useState(null)
  // activeBucket replaces the old single needs_attention checkbox as the
  // way to filter - null means "show everything," matching the
  // pre-existing default behavior exactly.
  const [activeBucket, setActiveBucket] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true'
      ? 'needs_follow_up'
      : null
  )
  const [loading, setLoading] = useState(true)
  const [actionBusyId, setActionBusyId] = useState(null)
  const [error, setError] = useState('')

  function loadCounts() {
    api.get('/sms/replies/counts').then(setCounts).catch(() => {})
  }

  function loadReplies() {
    setLoading(true)
    setError('')
    const query = activeBucket ? `?bucket=${activeBucket}` : ''
    api.get(`/sms/replies${query}`)
      .then(setReplies)
      .catch((err) => setError(err.message || 'Could not load replies.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadCounts()
    loadReplies()
  }, [activeBucket])

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
      loadCounts() // the reviewed/needs-follow-up counts just changed
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
      loadCounts() // bucket counts just shifted
    } catch (err) {
      setError(err.message || 'Could not reclassify reply.')
    } finally {
      setActionBusyId(null)
    }
  }

  function handleQuickDnc(replyId) {
    const confirmed = window.confirm(
      "Flag this as DNC? This stops any active cadence and blocks this lead's phone number from all future sends across the org."
    )
    if (!confirmed) return
    reclassify(replyId, 'dnc')
  }

  function toggleBucket(key) {
    setActiveBucket((current) => (current === key ? null : key))
  }

  const activeBucketLabel = BUCKET_CARDS.find((c) => c.key === activeBucket)?.label

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Replies</h1>
          <p className="page-subtitle">
            {activeBucketLabel ? `Showing: ${activeBucketLabel}` : 'Every reply from your leads, newest first.'}
          </p>
        </div>
        {activeBucket && (
          <button className="btn btn--secondary" onClick={() => setActiveBucket(null)}>
            Clear filter
          </button>
        )}
      </header>

      <div className="reply-scorecard-grid">
        {BUCKET_CARDS.map((card) => (
          <button
            key={card.key}
            type="button"
            className={`reply-scorecard-btn ${activeBucket === card.key ? 'reply-scorecard-btn--active' : ''}`}
            onClick={() => toggleBucket(card.key)}
          >
            <StatCard label={card.label} value={counts ? counts[card.key] : '—'} accent={card.accent} />
          </button>
        ))}
      </div>

      {error && <div className="panel reply-error">{error}</div>}

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading replies…</div>
        ) : replies.length === 0 ? (
          <div className="empty-state">
            {activeBucket
              ? `Nothing in "${activeBucketLabel}" right now.`
              : "No replies yet. Once a lead responds, it'll land here."}
          </div>
        ) : (
          <ul className="reply-feed">
            {replies.map((r) => {
              const config = CLASSIFICATION_CONFIG[r.classification] || CLASSIFICATION_CONFIG.neutral
              const isBusy = actionBusyId === r.id
              const reviewed = Boolean(r.reviewed_at)

              return (
                <li
                  key={r.id}
                  className={`reply-card ${r.is_hot ? 'reply-card--hot' : ''}`}
                  onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}
                  style={{ cursor: r.lead_id ? 'pointer' : 'default' }}
                >
                  <div className="reply-card-top">
                    <span className={`badge badge--${config.color}`}>{config.label}</span>
                    <span className="reply-time mono">{new Date(r.received_at).toLocaleString()}</span>
                  </div>
                  <p className="reply-card-body">{r.body}</p>

                  <div className="reply-actions" onClick={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="reply-action-button"
                      disabled={isBusy || reviewed}
                      onClick={() => markReviewed(r.id)}
                    >
                      {reviewed ? 'Reviewed' : 'Mark reviewed'}
                    </button>

                    {r.classification !== 'dnc' && (
                      <button
                        type="button"
                        className="reply-action-button reply-action-button--danger"
                        disabled={isBusy}
                        onClick={() => handleQuickDnc(r.id)}
                        title="One click if this is a stop/do-not-contact request the system missed"
                      >
                        Mark DNC
                      </button>
                    )}

                    <label className="reply-reclassify">
                      <span>Reclassify</span>
                      <select
                        value={r.classification || 'neutral'}
                        disabled={isBusy}
                        onChange={(event) => reclassify(r.id, event.target.value)}
                      >
                        {CLASSIFICATION_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
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
