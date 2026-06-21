import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './Replies.css'

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  const [hotOnly, setHotOnly] = useState(searchParams.get('hot_only') === 'true')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.get(`/sms/replies${hotOnly ? '?hot_only=true' : ''}`)
      .then(setReplies)
      .finally(() => setLoading(false))
  }, [hotOnly])

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Replies</h1>
          <p className="page-subtitle">Every reply from your leads, newest first.</p>
        </div>
        <label className="hot-toggle">
          <input type="checkbox" checked={hotOnly} onChange={(e) => setHotOnly(e.target.checked)} />
          Hot only
        </label>
      </header>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading replies…</div>
        ) : replies.length === 0 ? (
          <div className="empty-state">No replies {hotOnly ? 'flagged hot' : 'yet'}. Once a lead responds, it'll land here.</div>
        ) : (
          <ul className="reply-feed">
            {replies.map((r) => (
              <li
                key={r.id}
                className={`reply-card ${r.is_hot ? 'reply-card--hot' : ''}`}
                onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}
                style={{ cursor: r.lead_id ? 'pointer' : 'default' }}
              >
                <div className="reply-card-top">
                  {r.is_hot && <SignalPulse color="red" size={7} label="Hot" />}
                  <span className="reply-time mono">{new Date(r.received_at).toLocaleString()}</span>
                </div>
                <p className="reply-card-body">{r.body}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
