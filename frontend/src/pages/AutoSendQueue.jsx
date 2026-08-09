import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './AutoSendQueue.css'

const PHASE_OPTIONS = [
  { value: 'off', label: 'Off', desc: 'Inbound replies handled manually — nothing goes to this queue.' },
  { value: 'candidate', label: 'Review queue', desc: 'AI drafts a response to eligible inbound replies. You approve each one before it sends.' },
  { value: 'auto', label: 'Full auto', desc: 'Eligible simple inbound replies are sent immediately without review. Use with care.' },
]

export default function AutoSendQueue() {
  const navigate = useNavigate()
  const [queue, setQueue] = useState([])
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('queue')
  const [actioning, setActioning] = useState(null)
  const [approvingAll, setApprovingAll] = useState(false)
  const [error, setError] = useState('')

  // Edit state
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const [editSubject, setEditSubject] = useState('')
  const [saving, setSaving] = useState(false)

  // Settings state
  const [showSettings, setShowSettings] = useState(false)
  const [phase, setPhase] = useState('off')
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaved, setSettingsSaved] = useState(false)

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

  function loadSettings() {
    api.get('/auto-send/settings').then(s => {
      setPhase(s.auto_send_phase || 'off')
    }).catch(() => {})
  }

  useEffect(() => {
    load()
    loadSettings()
  }, [])

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
      await api.post('/auto-send/approve-all', {})
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setApprovingAll(false)
    }
  }

  function startEdit(item) {
    setEditingId(item.id)
    setEditText(item.message || '')
    setEditSubject(item.subject || '')
    setError('')
  }

  function cancelEdit() {
    setEditingId(null)
    setEditText('')
    setEditSubject('')
  }

  async function saveEdit(item) {
    if (!editText.trim()) {
      setError('Message cannot be empty.')
      return
    }
    setSaving(true)
    try {
      const updated = await api.patch(`/auto-send/${item.id}/edit`, {
        message: editText.trim(),
        subject: item.channel === 'email' ? editSubject : undefined,
      })
      setQueue(q => q.map(qi => qi.id === item.id ? { ...qi, message: updated.message, subject: updated.subject } : qi))
      cancelEdit()
    } catch (err) {
      setError(err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function saveSettings() {
    setSettingsLoading(true)
    try {
      await api.post('/auto-send/settings', { auto_send_phase: phase })
      setSettingsSaved(true)
      setTimeout(() => setSettingsSaved(false), 3000)
    } catch (err) {
      setError(err.message || 'Failed to save settings')
    } finally {
      setSettingsLoading(false)
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
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {queue.length > 0 && (
            <button className="btn btn--primary" onClick={handleApproveAll} disabled={approvingAll}>
              {approvingAll ? 'Sending all…' : `✓ Approve & send all ${queue.length}`}
            </button>
          )}
          <button
            className="btn btn--secondary"
            onClick={() => setShowSettings(s => !s)}
            style={{ fontSize: 13 }}
          >
            ⚙ Settings
          </button>
        </div>
      </header>

      {/* Settings panel */}
      {showSettings && (
        <div className="panel" style={{ marginBottom: 20, padding: '18px 20px' }}>
          <h3 style={{ margin: '0 0 14px', fontSize: 15, fontWeight: 700 }}>Auto-Send Mode</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
            {PHASE_OPTIONS.map(opt => (
              <label
                key={opt.value}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                  cursor: 'pointer',
                  padding: '10px 14px',
                  borderRadius: 8,
                  border: `1.5px solid ${phase === opt.value ? 'var(--accent-gold, #b8892a)' : 'var(--border-subtle, var(--border-color))'}`,
                  background: phase === opt.value ? 'var(--accent-gold-dim, rgba(184,137,42,0.08))' : 'transparent',
                }}
              >
                <input
                  type="radio"
                  name="phase"
                  value={opt.value}
                  checked={phase === opt.value}
                  onChange={() => setPhase(opt.value)}
                  style={{ marginTop: 2 }}
                />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{opt.label}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{opt.desc}</div>
                </div>
              </label>
            ))}
          </div>
          {phase === 'auto' && (
            <div style={{ background: 'var(--signal-amber-dim, rgba(234,179,8,0.1))', color: 'var(--signal-amber, #ca8a04)', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
              ⚠ Full auto sends without any human review. Only short scheduling/logistics replies and clear interest signals qualify — but verify your AI tone context is set correctly on each tier before enabling.
            </div>
          )}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn btn--primary" onClick={saveSettings} disabled={settingsLoading} style={{ fontSize: 13 }}>
              {settingsLoading ? 'Saving…' : 'Save'}
            </button>
            <button className="btn btn--secondary" onClick={() => setShowSettings(false)} style={{ fontSize: 13 }}>Cancel</button>
            {settingsSaved && <span style={{ color: 'var(--signal-green)', fontSize: 13 }}>✓ Saved</span>}
          </div>
        </div>
      )}

      {error && (
        <div style={{ background: 'var(--signal-red-dim)', color: 'var(--signal-red)', padding: '10px 14px', borderRadius: 10, fontSize: 13, marginBottom: 14 }}>
          {error}
        </div>
      )}

      <div className="asq-kpi-row">
        <div className="panel asq-kpi-card">
          <span className="asq-kpi-label">Pending review</span>
          <strong className="asq-kpi-value" style={{ color: queue.length > 0 ? 'var(--signal-amber)' : 'var(--signal-green)' }}>
            {loading ? '—' : queue.length}
          </strong>
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
            <p>No messages waiting for review. Enable the review queue in Settings above, and AI-drafted responses to eligible inbound replies will appear here.</p>
            <div className="asq-how-to">
              <p><strong>What gets queued:</strong></p>
              <ul>
                <li>Inbound SMS replies classified as a simple question, interest signal, or callback request</li>
                <li>Must be at least the lead's second reply (first contact always goes to your inbox)</li>
                <li>AI confidence must be high — ambiguous replies always go to your normal inbox</li>
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

                {/* Message preview / inline editor */}
                {editingId === item.id ? (
                  <div style={{ marginTop: 10 }}>
                    {item.channel === 'email' && (
                      <input
                        className="asq-edit-subject"
                        placeholder="Subject line"
                        value={editSubject}
                        onChange={e => setEditSubject(e.target.value)}
                      />
                    )}
                    <textarea
                      className="asq-edit-textarea"
                      rows={4}
                      value={editText}
                      onChange={e => setEditText(e.target.value)}
                    />
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <button
                        className="btn btn--primary"
                        style={{ fontSize: 13 }}
                        onClick={() => saveEdit(item)}
                        disabled={saving}
                      >
                        {saving ? 'Saving…' : 'Save edit'}
                      </button>
                      <button
                        className="btn btn--secondary"
                        style={{ fontSize: 13 }}
                        onClick={cancelEdit}
                        disabled={saving}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {item.subject && <div className="asq-subject">Subject: {item.subject}</div>}
                    <div className="asq-message">{item.message}</div>
                  </>
                )}

                {item.ai_reason && <div className="asq-reason">AI: {item.ai_reason}</div>}

                <div className="asq-item-actions">
                  <button
                    className="btn btn--primary"
                    onClick={() => handleApprove(item.id)}
                    disabled={actioning === item.id || editingId === item.id}
                  >
                    {actioning === item.id ? 'Sending…' : '✓ Approve & send'}
                  </button>
                  {editingId !== item.id && (
                    <button
                      className="btn btn--secondary"
                      onClick={() => startEdit(item)}
                      disabled={!!actioning}
                    >
                      ✎ Edit
                    </button>
                  )}
                  <button
                    className="btn btn--secondary"
                    onClick={() => handleSkip(item.id)}
                    disabled={actioning === item.id || editingId === item.id}
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
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 300 }}>
                      {item.message?.slice(0, 80)}{item.message?.length > 80 ? '…' : ''}
                    </td>
                    <td>
                      <span style={{ color: statusColor(item.status), fontSize: 12, fontWeight: 700, textTransform: 'capitalize' }}>
                        {item.status}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                      {item.actioned_at
                        ? new Date(item.actioned_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
                        : '—'}
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
