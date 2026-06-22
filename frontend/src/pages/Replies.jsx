import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './Replies.css'

const CLASSIFICATION_CONFIG = {
  interested: { label: 'Interested', color: 'green' },
  callback: { label: 'Callback', color: 'blue' },
  dnc: { label: 'DNC', color: 'red' },
  neutral: { label: 'Neutral', color: 'neutral' },
}

const CLASSIFICATION_OPTIONS = [
  { value: 'interested', label: 'Interested' },
  { value: 'callback', label: 'Callback' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'dnc', label: 'DNC' },
]

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  // needs_attention replaces the old hot_only flag as the default filter -
  // Mike's specific request: "only hand me a hot lead when I'm ready to
  // book", meaning Interested + Callback only, not every reply.
  const [needsAttentionOnly, setNeedsAttentionOnly] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true'
  )
  const [loading, setLoading] = useState(true)
  const [actionBusyId, setActionBusyId] = useState(null)
  const [error, setError] = useState('')

  function loadReplies() {
    setLoading(true)
    setError('')
    api.get(`/sms/replies${needsAttentionOnly ? '?needs_attention=true' : ''}`)
      .then(setReplies)
      .catch((err) => setError(err.message || 'Could not load replies.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadReplies()
  }, [needsAttentionOnly])

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

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Replies</h1>
          <p className="page-subtitle">Every reply from your leads, newest first.</p>
        </div>
        <label className="hot-toggle">
          <input type="checkbox" checked={needsAttentionOnly} onChange={(e) => setNeedsAttentionOnly(e.target.checked)} />
          Needs attention only
        </label>
      </header>

      {error && <div className="panel reply-error">{error}</div>}

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading replies…</div>
        ) : replies.length === 0 ? (
          <div className="empty-state">
            {needsAttentionOnly
              ? "Nothing needs your attention right now — interested and callback replies will show up here."
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
