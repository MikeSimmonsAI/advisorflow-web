import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'

export default function AutoSendQueue() {
  const [queue, setQueue] = useState([])
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('queue')
  const [actioning, setActioning] = useState(null)

  function load() {
    setLoading(true)
    Promise.all([
      api.get('/auto-send/queue').catch(() => []),
      api.get('/auto-send/history').catch(() => []),
    ]).then(([q, h]) => {
      setQueue(q || [])
      setHistory(h || [])
      setLoading(false)
    })
  }

  useEffect(() => { load() }, [])

  async function handleApprove(id) {
    setActioning(id)
    try {
      await api.post(`/auto-send/${id}/approve`, {})
      load()
    } catch (err) {
      alert(err.message)
    } finally {
      setActioning(null)
    }
  }

  async function handleSkip(id) {
    setActioning(id)
    try {
      await api.post(`/auto-send/${id}/skip`, {})
      load()
    } catch (err) {
      alert(err.message)
    } finally {
      setActioning(null)
    }
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Auto-send queue</h1>
          <p className="page-subtitle">AI drafts a reply for simple, low-stakes questions — you read it and decide. Nothing sends without you.</p>
        </div>
      </header>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button
          className={`btn ${tab === 'queue' ? 'btn--primary' : 'btn--secondary'}`}
          onClick={() => setTab('queue')}
        >
          Queue
        </button>
        <button
          className={`btn ${tab === 'history' ? 'btn--primary' : 'btn--secondary'}`}
          onClick={() => setTab('history')}
        >
          History
        </button>
      </div>

      <div style={{ display: 'grid', gap: 12, marginBottom: 20 }}>
        <div className="panel" style={{ padding: '20px 24px' }}>
          <strong style={{ fontSize: 28, color: 'var(--text-primary)' }}>
            {tab === 'queue' ? queue.length : history.length}
          </strong>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0' }}>
            {tab === 'queue' ? 'Waiting for review' : 'Last 100'}
          </p>
        </div>
      </div>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading...</div>
        ) : tab === 'queue' && queue.length === 0 ? (
          <div className="empty-state">
            Nothing waiting right now. Eligible replies — simple scheduling questions on leads you've already heard from — will show up here for your review.
          </div>
        ) : tab === 'history' && history.length === 0 ? (
          <div className="empty-state">No history yet.</div>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {(tab === 'queue' ? queue : history).map(item => (
              <li key={item.id} style={{ padding: '16px', borderRadius: 10, border: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <strong style={{ color: 'var(--text-primary)' }}>{item.lead_name || 'Unknown lead'}</strong>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {item.received_at ? new Date(item.received_at).toLocaleString() : ''}
                  </span>
                </div>
                <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 8px' }}>
                  <strong>Their reply:</strong> {item.reply_body}
                </p>
                <p style={{ fontSize: 13, color: 'var(--text-primary)', margin: '0 0 12px', padding: '10px', borderRadius: 8, background: 'rgba(37,99,235,0.08)', border: '1px solid rgba(37,99,235,0.2)' }}>
                  <strong>Suggested:</strong> {item.suggested_response}
                </p>
                {tab === 'queue' && (
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      className="btn btn--primary"
                      onClick={() => handleApprove(item.id)}
                      disabled={actioning === item.id}
                      style={{ fontSize: 13 }}
                    >
                      {actioning === item.id ? 'Sending...' : 'Send this'}
                    </button>
                    <button
                      className="btn btn--secondary"
                      onClick={() => handleSkip(item.id)}
                      disabled={actioning === item.id}
                      style={{ fontSize: 13 }}
                    >
                      Skip
                    </button>
                  </div>
                )}
                {tab === 'history' && (
                  <span className={`badge badge--${item.action === 'approved' ? 'green' : 'neutral-dim'}`}>
                    {item.action === 'approved' ? 'Sent' : 'Skipped'}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
