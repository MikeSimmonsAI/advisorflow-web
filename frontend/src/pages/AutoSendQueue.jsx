import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './AutoSendQueue.css'

export default function AutoSendQueue() {
  const navigate = useNavigate()
  const [queue, setQueue] = useState([])
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('queue')
  const [actioning, setActioning] = useState(null)
  const [approvingAll, setApprovingAll] = useState(false)
  const [error, setError] = useState('')

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
    setError('')
    try {
      await api.post(`/auto-send/${id}/approve`, {})
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setActioning(null)
    }
  }

  async function handleSkip(id) {
    setActioning(id)
    try {
      await api.post(`/auto-send/${id}/skip`, {})
      load()
    } finally {
      setActioning(null)
    }
  }

  async function handleApproveAll() {
    if (!confirm(`Send all ${queue.length} queued messages now?`)) return
    setApprovingAll(true)
    try {
      const result = await api.post('/auto-send/approve-all', {})
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setApprovingAll(false)
    }
  }

  function statusColor(status) {
    return {
      sent: 'var(--signal-green)',
      pending: 'var(--signal-amber)',
      skipped: 'var(--text-tertiary)',
      failed: 'var(--signal-red)',
    }[status] || 'var(--text-secondary)'
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Auto-Send Queue</h1>
          <p className="page-subtitle">AI-drafted messages waiting for your review before sending.</p>
        </div>
        {queue.length > 0 && (
          <button className="btn btn--primary" onClick={handleApproveAll} disabled={approvingAll}>
            {approvingAll ? 'Sending all…' : `✓ Approve & send all ${queue.length}`}
          </button>
        )}
      </header>

      {error && <div style={{ background: 'var(--signal-red-dim)', color: 'var(--signal-red)', padding: '10px 14px', borderRadius: 10, fontSize: 13, marginBottom: 14 }}>{error}</div>}

      <div className="asq-kpi-row">
        <div className="panel asq-kpi-card">
          <span className="asq-kpi-label">Pending review</span>
          <strong className="asq-kpi-value" style={{ color: queue.length > 0 ? 'var(--signal-amber)' : 'var(--signal-green)' }}>{loading ? '—' : queue.length}</strong>
        </div>
        <div className="panel asq-kpi-card">
          <span className="asq-kpi-label">Sent today</span>
          <strong className="asq-kpi-value" style={{ color: 'var(--signal-green)' }}>
            {loading ? '—' : history.filter(h => h.status === 'sent' && new Date(h.actioned_at) > new Date(Date.now() - 86400000)).length}
          </strong>
        </div>
        <div className="panel asq-kpi-card">
          <span className="asq-kpi-label">Skipped</span>
          <strong className="asq-kpi-value" style={{ color: 'var(--text-secondary)' }}>
            {loading ? '—' : history.filter(h => h.status === 'skipped').length}
          </strong>
        </div>
      </div>

      <div className="asq-tabs">
        <button className={`tab ${tab === 'queue' ? 'tab--active' : ''}`} onClick={() => setTab('queue')}>
          Pending {queue.length > 0 && <span className="asq-badge">{queue.length}</span>}
        </button>
        <button className={`tab ${tab === 'history' ? 'tab--active' : ''}`} onClick={() => setTab('history')}>
          History
        </button>
      </div>

      {tab === 'queue' && (
        loading ? (
          <div className="empty-state">Loading queue…</div>
        ) : queue.length === 0 ? (
          <div className="panel asq-empty">
            <div className="asq-empty-icon">✓</div>
            <h3>Queue is clear</h3>
            <p>No messages waiting for review. AI-drafted messages from campaigns and cadences will appear here when auto-send is enabled.</p>
            <div className="asq-how-to">
              <p><strong>How to get messages here:</strong></p>
              <ul>
                <li>Run a Campaign with "AI auto-reply" toggled on</li>
                <li>Start a Cadence — when AI drafts a reply to an inbound message, it queues here</li>
                <li>Use AI Auto-conversation from the Leads page</li>
              </ul>
            </div>
          </div>
        ) : (
          <div className="asq-item-list">
            {queue.map((item) => (
              <div key={item.id} className="panel asq-item">
                <div className="asq-item-header">
                  <div className="asq-item-lead" onClick={() => navigate(`/leads/${item.lead_id}`)}>
                    <span className="asq-lead-name">{item.lead_name || '—'}</span>
                    <span className="asq-lead-contact mono">{item.channel === 'email' ? item.email : item.phone}</span>
                  </div>
                  <div className="asq-item-meta">
                    <span className={`asq-channel-badge asq-channel-badge--${item.channel}`}>
                      {item.channel === 'email' ? '✉️ Email' : '💬 SMS'}
                    </span>
                    <span className="asq-source">{item.source}</span>
                  </div>
                </div>
                {item.subject && <div className="asq-subject">Subject: {item.subject}</div>}
                <div className="asq-message">{item.message}</div>
                {item.ai_reason && <div className="asq-reason">AI: {item.ai_reason}</div>}
                <div className="asq-item-actions">
                  <button
                    className="btn btn--primary"
                    onClick={() => handleApprove(item.id)}
                    disabled={actioning === item.id}
                  >
                    {actioning === item.id ? 'Sending…' : '✓ Approve & send'}
                  </button>
                  <button
                    className="btn btn--secondary"
                    onClick={() => handleSkip(item.id)}
                    disabled={actioning === item.id}
                  >
                    Skip
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'history' && (
        history.length === 0 ? (
          <div className="empty-state">No history yet.</div>
        ) : (
          <section className="panel">
            <table className="data-table">
              <thead>
                <tr><th>Lead</th><th>Channel</th><th>Message</th><th>Status</th><th>When</th></tr>
              </thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <span style={{ color: 'var(--signal-blue)', cursor: 'pointer' }} onClick={() => navigate(`/leads/${item.lead_id}`)}>
                        {item.lead_name || '—'}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>{item.channel}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 300 }}>{item.message?.slice(0, 80)}{item.message?.length > 80 ? '…' : ''}</td>
                    <td>
                      <span style={{ color: statusColor(item.status), fontSize: 12, fontWeight: 700, textTransform: 'capitalize' }}>
                        {item.status}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                      {item.actioned_at ? new Date(item.actioned_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )
      )}
    </div>
  )
}
