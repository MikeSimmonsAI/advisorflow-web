import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './AIHub.css'

const TABS = [
  { key: 'overview', label: '📊 Overview' },
  { key: 'conversations', label: '💬 Active Conversations' },
  { key: 'flagged', label: '⚠️ Needs Attention' },
  { key: 'calls', label: '📞 Voice Calls' },
  { key: 'queue', label: '✉️ Send Queue' },
]

const STAGE_COLORS = {
  // Light-system hues: every one reads at >= 4.5:1 on white.
  outreach_sent: '#1f5eff',
  replied: '#0b8a5f',
  ai_responding: '#6d43cc',
  booking_sent: '#b45309',
  booked: '#a8650f',
  flagged: '#d31f4b',
  stopped: '#5b6b80',
  completed: '#5b6b80',
  cold: '#5b6b80',
}

export default function AIHub() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('overview')
  const [stats, setStats] = useState(null)
  const [forecast, setForecast] = useState(null)
  const [flagged, setFlagged] = useState([])
  const [conversations, setConversations] = useState([])
  const [calls, setCalls] = useState([])
  const [callsToday, setCallsToday] = useState(null)
  const [queue, setQueue] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.get('/pipeline/stats').catch(() => null),
      api.get('/pipeline/forecast').catch(() => null),
      api.get('/pipeline/flagged').catch(() => []),
      api.get('/pipeline/conversations').catch(() => []),
      api.get('/voice/calls').catch(() => []),
      api.get('/auto-send/queue').catch(() => []),
      api.get('/voice/calls-summary').catch(() => null),
    ]).then(([s, f, fl, cv, cl, q, cs]) => {
      setStats(s)
      setForecast(f)
      setFlagged(Array.isArray(fl) ? fl : [])
      setConversations(Array.isArray(cv) ? cv : [])
      setCalls(Array.isArray(cl) ? cl : [])
      setQueue(Array.isArray(q) ? q : [])
      setCallsToday(typeof cs?.today === 'number' ? cs.today : null)
    }).catch(e => setError(e.message))
    .finally(() => setLoading(false))
  }, [])

  // /pipeline/flagged returns at most FLAGGED_LIST_CAP rows. Below the cap the
  // list length is exact (and drops as items are handled); at the cap, use the
  // server's own count for this workspace instead of reporting the cap.
  const FLAGGED_LIST_CAP = 200
  const needsAttention = (flagged.length >= FLAGGED_LIST_CAP && stats && !stats.is_god_view
    && typeof stats.flagged_count === 'number') ? Math.max(stats.flagged_count, flagged.length) : flagged.length

  async function handleApprove(id, message) {
    try {
      await api.post(`/pipeline/approve/${id}`, { pipeline_id: id, message, send: true })
      setFlagged(prev => prev.filter(f => f.id !== id))
    } catch (e) { alert(e.message) }
  }

  async function handleDismiss(id) {
    try {
      await api.post(`/pipeline/dismiss/${id}`, {})
      setFlagged(prev => prev.filter(f => f.id !== id))
    } catch (e) { alert(e.message) }
  }

  return (
    <div className="aihub-page">
      <div className="aihub-header">
        <h1 className="aihub-title">🤖 AI Hub</h1>
        <p className="aihub-subtitle">All AI activity — conversations, calls, queue, and alerts in one place.</p>
      </div>

      <div className="aihub-tabs">
        {TABS.map(t => (
          <button
            key={t.key}
            className={`aihub-tab ${tab === t.key ? 'aihub-tab--active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {t.key === 'flagged' && needsAttention > 0 && (
              <span className="aihub-tab-badge">{needsAttention}</span>
            )}
          </button>
        ))}
      </div>

      {error && <div className="compose-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* ── Overview ── */}
      {tab === 'overview' && (
        <div className="aihub-content">
          <div className="aihub-stat-grid">
            {[
              { label: 'Active conversations', value: forecast?.active_conversations ?? '—', color: 'var(--color-primary)' },
              { label: 'Reply rate', value: forecast?.reply_rate != null ? `${forecast.reply_rate}%` : '—', color: 'var(--color-success)' },
              { label: 'Awaiting booking click', value: forecast?.booking_sent_count ?? '—', color: 'var(--color-warning)' },
              { label: 'Projected bookings', value: forecast?.projected_bookings_this_week ?? '—', color: 'var(--color-info)' },
              { label: 'Calls made today', value: callsToday ?? '—', color: 'var(--signal-purple)' },
              { label: 'Needs attention', value: needsAttention, color: 'var(--color-danger)' },
            ].map(item => (
              <div key={item.label} className="aihub-stat-card">
                <div className="aihub-stat-value" style={{ color: item.color }}>
                  {loading ? '—' : item.value}
                </div>
                <div className="aihub-stat-label">{item.label}</div>
              </div>
            ))}
          </div>

          {forecast?.alerts?.length > 0 && (
            <section className="panel" style={{ marginTop: 20 }}>
              <div className="panel-header"><h2 className="panel-title">🔔 Alerts</h2></div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {forecast.alerts.map((alert, i) => (
                  <div key={i} className={`aihub-alert aihub-alert--${alert.type}`}
                    onClick={() => alert.path && navigate(alert.path)}>
                    <span style={{ fontSize: 18 }}>
                      {alert.type === 'urgent' ? '⚠️' : alert.type === 'opportunity' ? '💡' : 'ℹ️'}
                    </span>
                    <span style={{ flex: 1 }}>{alert.message}</span>
                    {alert.action && <span className="aihub-alert-action">{alert.action} →</span>}
                  </div>
                ))}
              </div>
            </section>
          )}

          {stats && (
            <section className="panel" style={{ marginTop: 20 }}>
              <div className="panel-header"><h2 className="panel-title">Pipeline stages</h2></div>
              {Object.keys(stats.by_stage || {}).length === 0 ? (
                <div className="empty-state" style={{ textAlign: 'left', padding: '4px 0' }}>
                  No AI conversations yet, so there is nothing to place in a stage.
                  Each AI conversation is counted here by stage (outreach sent, replied,
                  booking sent, booked…) once one starts — open a lead and start an AI
                  conversation from it.{' '}
                  <a href="/leads" style={{ color: 'var(--color-primary)', fontWeight: 600 }}>Go to Leads →</a>
                </div>
              ) : null}
              <div className="aihub-stage-grid">
                {Object.entries(stats.by_stage || {}).map(([stage, count]) => (
                  <div key={stage} className="aihub-stage-card">
                    <div className="aihub-stage-dot" style={{ background: STAGE_COLORS[stage] || '#64748b' }} />
                    <div className="aihub-stage-count">{count}</div>
                    <div className="aihub-stage-label">{stage.replace(/_/g, ' ')}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {/* ── Active Conversations ── */}
      {tab === 'conversations' && (
        <div className="aihub-content">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : conversations.length === 0 ? (
            <div className="empty-state">
              No active AI conversations yet.<br />
              <span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
                Open a lead and click 🤖 Start AI Conversation to begin.
              </span>
            </div>
          ) : (
            <div className="aihub-conv-list">
              {conversations.map(conv => (
                <div key={conv.id} className="aihub-conv-card"
                  onClick={() => navigate(`/leads/${conv.lead_id}`)}>
                  <div className="aihub-conv-avatar">
                    {(conv.lead_name || '?')[0].toUpperCase()}
                  </div>
                  <div className="aihub-conv-info">
                    <div className="aihub-conv-name">{conv.lead_name || 'Unknown'}</div>
                    <div className="aihub-conv-meta">
                      Touch {conv.touch_number || 0}{conv.total_touches ? ` of ${conv.total_touches}` : ''} · {conv.messages_sent || 0} sent · {conv.replies_received || 0} replies
                    </div>
                  </div>
                  <div className="aihub-conv-right">
                    <span className="aihub-conv-stage" style={{ background: `${STAGE_COLORS[conv.stage] || '#64748b'}20`, color: STAGE_COLORS[conv.stage] || '#64748b' }}>
                      {conv.stage?.replace(/_/g, ' ')}
                    </span>
                    {conv.paused && <span className="badge badge--amber" style={{ fontSize: 10, marginTop: 4 }}>PAUSED</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Needs Attention (Flagged) ── */}
      {tab === 'flagged' && (
        <div className="aihub-content">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : flagged.length === 0 ? (
            <div className="empty-state">✅ Nothing needs your attention right now.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {flagged.map(item => (
                <div key={item.id} className="panel aihub-flagged-card">
                  <div className="aihub-flagged-header">
                    <div>
                      <span className="aihub-flagged-name"
                        onClick={() => navigate(`/leads/${item.lead_id}`)}
                        style={{ cursor: 'pointer', color: 'var(--accent)' }}>
                        {item.lead_name || 'Unknown lead'}
                      </span>
                      <span className="aihub-flagged-reason">⚠️ {item.flag_reason}</span>
                    </div>
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                      {item.flagged_at ? new Date(item.flagged_at).toLocaleString() : ''}
                    </span>
                  </div>
                  {item.flagged_reply_body && (
                    <div className="aihub-flagged-reply">
                      "{item.flagged_reply_body.slice(0, 200)}{item.flagged_reply_body.length > 200 ? '…' : ''}"
                    </div>
                  )}
                  {item.flagged_suggested_response && (
                    <div className="aihub-flagged-suggestion">
                      <span style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'block', marginBottom: 4 }}>AI suggested response:</span>
                      {item.flagged_suggested_response}
                    </div>
                  )}
                  <div className="aihub-flagged-actions">
                    <button className="btn btn--primary"
                      onClick={() => handleApprove(item.id, item.flagged_suggested_response)}>
                      ✓ Send AI response
                    </button>
                    <button className="btn btn--secondary"
                      onClick={() => navigate(`/leads/${item.lead_id}`)}>
                      ✏️ Handle manually
                    </button>
                    <button className="btn btn--secondary"
                      onClick={() => handleDismiss(item.id)}>
                      Dismiss
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Voice Calls ── */}
      {tab === 'calls' && (
        <div className="aihub-content">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : calls.length === 0 ? (
            <div className="empty-state">
              No voice calls yet.<br />
              <span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
                Open a lead and click 📞 Call with AI to make the first call.
              </span>
            </div>
          ) : (
            <div className="aihub-calls-list">
              {calls.map(call => (
                <div key={call.id} className="aihub-call-card"
                  onClick={() => navigate(`/leads/${call.lead_id}`)}>
                  <div className="aihub-call-icon">📞</div>
                  <div className="aihub-call-info">
                    <div className="aihub-call-name">{call.lead_name || 'Unknown'}</div>
                    <div className="aihub-call-meta">
                      {call.duration_seconds ? `${Math.floor(call.duration_seconds / 60)}m ${call.duration_seconds % 60}s` : 'No answer'} ·{' '}
                      {call.created_at ? new Date(call.created_at).toLocaleString() : ''}
                    </div>
                  </div>
                  <div className="aihub-call-right">
                    <span className={`badge badge--${
                      call.outcome === 'booked' ? 'green' :
                      call.outcome === 'no_answer' ? 'neutral-dim' :
                      call.outcome === 'not_interested' ? 'neutral-dim' :
                      'amber'
                    }`}>
                      {call.outcome?.replace(/_/g, ' ') || 'in progress'}
                    </span>
                    {call.recording_url && (
                      <a href={call.recording_url} target="_blank" rel="noreferrer"
                        onClick={e => e.stopPropagation()}
                        style={{ fontSize: 11, color: 'var(--accent)', marginTop: 4, display: 'block' }}>
                        ▶ Play recording
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Send Queue ── */}
      {tab === 'queue' && (
        <div className="aihub-content">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : queue.length === 0 ? (
            <div className="empty-state">✅ Send queue is clear.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {queue.map(item => (
                <div key={item.id} className="panel" style={{ padding: '14px 16px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: 600 }}>{item.lead_name || 'Unknown'}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{item.scheduled_for}</span>
                  </div>
                  <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 0' }}>{item.message_preview}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
