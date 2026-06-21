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

  useEffect(() => {
    setLoading(true)
    api.get(`/sms/replies${needsAttentionOnly ? '?needs_attention=true' : ''}`)
      .then(setReplies)
      .finally(() => setLoading(false))
  }, [needsAttentionOnly])

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
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
