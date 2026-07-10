import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'

const STAGE_CONFIG = {
  outreach_sent:  { label: 'Outreach sent',   color: '#2fb6ff', icon: '📤' },
  replied:        { label: 'Replied',          color: '#f0c040', icon: '💬' },
  ai_responding:  { label: 'AI responding',   color: '#a78bfa', icon: '🤖' },
  booking_sent:   { label: 'Booking sent',    color: '#fb923c', icon: '🔗' },
  booked:         { label: 'Booked',          color: '#1ef0a8', icon: '📅' },
  confirmed:      { label: 'Confirmed',       color: '#1ef0a8', icon: '✅' },
  kept:           { label: 'Appointment kept',color: '#1ef0a8', icon: '🤝' },
  sale:           { label: 'Sale',            color: '#ffd700', icon: '💰' },
  stopped:        { label: 'Stopped',         color: '#6b7280', icon: '⏹' },
  dnc:            { label: 'DNC',             color: '#ff4d4d', icon: '🚫' },
}

const LEAD_TYPES = [
  { value: 'file_check',   label: 'File Check' },
  { value: 'code_lead',    label: 'Code Lead' },
  { value: 'new_inquiry',  label: 'New Inquiry' },
  { value: 'referral',     label: 'Referral' },
  { value: 'web_lead',     label: 'Web Lead' },
  { value: 'at_need',      label: 'At-Need' },
  { value: 'pre_need',     label: 'Pre-Need' },
  { value: 'general',      label: 'General Outreach' },
]

const TONE_OPTIONS = [
  { key: 'cold',   label: '❄️ Cold',   desc: 'Soft intro, low pressure' },
  { key: 'warm',   label: '☀️ Warm',   desc: 'Friendly, suggest a meeting' },
  { key: 'hot',    label: '🔥 Hot',    desc: 'Direct, ask for the appointment' },
  { key: 'urgent', label: '⚡ Urgent', desc: 'Brief, time-sensitive' },
]

export default function Pipeline() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('overview') // overview | flagged | conversations | launch
  const [stats, setStats] = useState(null)
  const [forecast, setForecast] = useState(null)
  const [flagged, setFlagged] = useState([])
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(true)
  const [stageFilter, setStageFilter] = useState('')

  // Launch form
  const [leads, setLeads] = useState([])
  const [selectedLeads, setSelectedLeads] = useState(new Set())
  const [leadType, setLeadType] = useState('general')
  const [tone, setTone] = useState('warm')
  const [aiDirection, setAiDirection] = useState('')
  const [channel, setChannel] = useState('sms')
  const [autoRespond, setAutoRespond] = useState(true)
  const [launching, setLaunching] = useState(false)
  const [launchResult, setLaunchResult] = useState(null)

  // Approve/edit state
  const [editingPipeline, setEditingPipeline] = useState(null)
  const [editedMessage, setEditedMessage] = useState('')
  const [approving, setApproving] = useState(false)

  function load() {
    setLoading(true)
    Promise.all([
      api.get('/pipeline/stats').catch(() => null),
      api.get('/pipeline/forecast').catch(() => null),
      api.get('/pipeline/flagged').catch(() => []),
      api.get('/pipeline/conversations').catch(() => []),
      api.get('/leads/').catch(() => []),
    ]).then(([statsData, forecastData, flaggedData, convoData, leadsData]) => {
      setStats(statsData)
      setForecast(forecastData)
      setFlagged(flaggedData || [])
      setConversations(convoData || [])
      setLeads(leadsData || [])
      setLoading(false)
    })
  }

  useEffect(() => { load() }, [])

  async function handleLaunch() {
    if (selectedLeads.size === 0) return
    setLaunching(true)
    setLaunchResult(null)
    try {
      const result = await api.post('/pipeline/launch', {
        lead_ids: Array.from(selectedLeads),
        lead_type: leadType,
        tone,
        ai_direction: aiDirection,
        channel,
        auto_respond: autoRespond,
      })
      setLaunchResult(result)
      setSelectedLeads(new Set())
      load()
    } catch (err) {
      setLaunchResult({ error: err.message })
    } finally {
      setLaunching(false)
    }
  }

  async function handleApprove(pipelineId, message, send) {
    setApproving(true)
    try {
      await api.post(`/pipeline/approve/${pipelineId}`, { pipeline_id: pipelineId, message, send })
      setEditingPipeline(null)
      load()
    } catch (err) {
      alert(`Error: ${err.message}`)
    } finally {
      setApproving(false)
    }
  }

  async function handleDismiss(pipelineId) {
    try {
      await api.post(`/pipeline/dismiss/${pipelineId}`, {})
      load()
    } catch (err) {
      alert(`Error: ${err.message}`)
    }
  }

  const filteredConvos = stageFilter
    ? conversations.filter(c => c.stage === stageFilter)
    : conversations

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">AI Pipeline</h1>
          <p className="page-subtitle">Full conversation pipeline — from first outreach to confirmed appointment.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {forecast?.flagged_count > 0 && (
            <button className="btn btn--danger" style={{ background: '#ff4d4d', color: '#fff', border: 'none' }}
              onClick={() => setTab('flagged')}>
              ⚠️ {forecast.flagged_count} need review
            </button>
          )}
          <button className="btn btn--primary" onClick={() => setTab('launch')}>
            🚀 Launch pipeline
          </button>
        </div>
      </header>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--border)', paddingBottom: 0 }}>
        {[
          { key: 'overview', label: '📊 Overview' },
          { key: 'flagged', label: `⚠️ Flagged${flagged.length > 0 ? ` (${flagged.length})` : ''}` },
          { key: 'conversations', label: '💬 All conversations' },
          { key: 'launch', label: '🚀 Launch' },
        ].map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{
              padding: '10px 18px', border: 'none', background: 'transparent',
              fontSize: 14, fontWeight: 600, cursor: 'pointer',
              color: tab === t.key ? 'var(--accent)' : 'var(--text-secondary)',
              borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom: -1,
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* OVERVIEW TAB */}
      {tab === 'overview' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* AI Forecast */}
          {forecast && (
            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">🤖 AI Forecast</h2>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 20 }}>
                {[
                  { label: 'Active conversations', value: forecast.active_conversations, color: '#2fb6ff' },
                  { label: 'Reply rate', value: `${forecast.reply_rate}%`, color: '#1ef0a8' },
                  { label: 'Awaiting booking click', value: forecast.booking_sent_count, color: '#fb923c' },
                  { label: 'Projected bookings this week', value: forecast.projected_bookings_this_week, color: '#ffd700' },
                ].map(item => (
                  <div key={item.label} style={{ background: 'var(--surface-2)', borderRadius: 12, padding: '20px 16px', textAlign: 'center' }}>
                    <div style={{ fontSize: 32, fontWeight: 900, color: item.color, lineHeight: 1 }}>{loading ? '—' : item.value}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{item.label}</div>
                  </div>
                ))}
              </div>
              {/* Alerts */}
              {forecast.alerts?.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {forecast.alerts.map((alert, i) => (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
                      borderRadius: 10, cursor: 'pointer',
                      background: alert.type === 'urgent' ? 'rgba(255,77,77,0.08)' : alert.type === 'opportunity' ? 'rgba(30,240,168,0.08)' : 'rgba(47,182,255,0.06)',
                      border: `1px solid ${alert.type === 'urgent' ? 'rgba(255,77,77,0.2)' : alert.type === 'opportunity' ? 'rgba(30,240,168,0.2)' : 'rgba(47,182,255,0.15)'}`,
                    }} onClick={() => navigate(alert.path)}>
                      <span style={{ fontSize: 18 }}>{alert.type === 'urgent' ? '⚠️' : alert.type === 'opportunity' ? '💡' : 'ℹ️'}</span>
                      <span style={{ flex: 1, fontSize: 14, color: 'var(--text-primary)' }}>{alert.message}</span>
                      <span style={{ fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>{alert.action} →</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* Stage breakdown */}
          {stats && (
            <section className="panel">
              <div className="panel-header"><h2 className="panel-title">Pipeline stages</h2></div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px,1fr))', gap: 12 }}>
                {Object.entries(STAGE_CONFIG).map(([stage, cfg]) => {
                  const count = stats.by_stage?.[stage] || 0
                  return (
                    <button key={stage} onClick={() => { setStageFilter(stage); setTab('conversations') }}
                      style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 12, padding: '16px 14px', textAlign: 'center', cursor: 'pointer' }}>
                      <div style={{ fontSize: 22 }}>{cfg.icon}</div>
                      <div style={{ fontSize: 24, fontWeight: 900, color: cfg.color, marginTop: 6 }}>{count}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{cfg.label}</div>
                    </button>
                  )
                })}
              </div>
            </section>
          )}

          {/* Engagement stats */}
          {stats && (
            <section className="panel">
              <div className="panel-header"><h2 className="panel-title">📈 Engagement</h2></div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16 }}>
                {[
                  { label: 'Total messages sent', value: stats.total_messages_sent, color: '#2fb6ff' },
                  { label: 'Total replies received', value: stats.total_replies_received, color: '#1ef0a8' },
                  { label: 'AI auto-responses sent', value: stats.ai_auto_sent, color: '#a78bfa' },
                  { label: 'Flagged for review', value: stats.ai_flagged, color: '#f0c040' },
                ].map(item => (
                  <div key={item.label} style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 28, fontWeight: 900, color: item.color }}>{item.value}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{item.label}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {/* FLAGGED TAB */}
      {tab === 'flagged' && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">⚠️ Flagged conversations — needs your review</h2>
            <span className="panel-count">{flagged.length}</span>
          </div>
          {flagged.length === 0 ? (
            <div className="empty-state">No flagged conversations. AI is handling everything. ✓</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {flagged.map(item => (
                <div key={item.pipeline_id} style={{ border: '1px solid rgba(255,77,77,0.25)', borderRadius: 14, padding: 20, background: 'rgba(255,77,77,0.04)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 16 }}>{item.lead_name}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
                        {item.lead_tier} · {item.lead_phone} · {item.messages_sent} sent, {item.replies_received} replied
                      </div>
                    </div>
                    <div style={{ fontSize: 11, color: '#ff4d4d', background: 'rgba(255,77,77,0.1)', padding: '4px 10px', borderRadius: 20 }}>
                      {item.flag_reason}
                    </div>
                  </div>

                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>Their reply:</div>
                    <div style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '10px 14px', fontSize: 14, borderLeft: '3px solid var(--accent)' }}>
                      {item.flagged_reply}
                    </div>
                  </div>

                  {editingPipeline === item.pipeline_id ? (
                    <div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>Edit response before sending:</div>
                      <textarea
                        className="compose-textarea"
                        rows={3}
                        value={editedMessage}
                        onChange={e => setEditedMessage(e.target.value)}
                        style={{ marginBottom: 10 }}
                      />
                      <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn--primary" onClick={() => handleApprove(item.pipeline_id, editedMessage, true)} disabled={approving}>
                          {approving ? '⏳ Sending…' : '✅ Send this response'}
                        </button>
                        <button className="btn btn--secondary" onClick={() => handleApprove(item.pipeline_id, editedMessage, false)} disabled={approving}>
                          Save without sending
                        </button>
                        <button className="btn btn--ghost" onClick={() => setEditingPipeline(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>AI suggested response:</div>
                      <div style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '10px 14px', fontSize: 14, marginBottom: 12, color: 'var(--text-primary)', borderLeft: '3px solid #a78bfa' }}>
                        {item.suggested_response}
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn--primary" onClick={() => handleApprove(item.pipeline_id, item.suggested_response, true)} disabled={approving}>
                          ✅ Approve &amp; send
                        </button>
                        <button className="btn btn--secondary" onClick={() => { setEditingPipeline(item.pipeline_id); setEditedMessage(item.suggested_response) }}>
                          ✏️ Edit before sending
                        </button>
                        <button className="btn btn--ghost" onClick={() => handleDismiss(item.pipeline_id)}>
                          Dismiss — I'll handle it
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* CONVERSATIONS TAB */}
      {tab === 'conversations' && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">All pipeline conversations</h2>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select className="filter-select" value={stageFilter} onChange={e => setStageFilter(e.target.value)} style={{ fontSize: 13 }}>
                <option value="">All stages</option>
                {Object.entries(STAGE_CONFIG).map(([key, cfg]) => (
                  <option key={key} value={key}>{cfg.icon} {cfg.label}</option>
                ))}
              </select>
              <span className="panel-count">{filteredConvos.length}</span>
            </div>
          </div>
          {filteredConvos.length === 0 ? (
            <div className="empty-state">No conversations yet. Launch a pipeline to get started.</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Stage</th>
                  <th>Type</th>
                  <th>Sent</th>
                  <th>Replies</th>
                  <th>AI sent</th>
                  <th>Last activity</th>
                </tr>
              </thead>
              <tbody>
                {filteredConvos.map(c => {
                  const cfg = STAGE_CONFIG[c.stage] || { label: c.stage, color: '#888', icon: '•' }
                  return (
                    <tr key={c.pipeline_id} onClick={() => navigate(`/leads/${c.lead_id}`)} style={{ cursor: 'pointer' }}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{c.lead_name}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{c.lead_phone}</div>
                      </td>
                      <td>
                        <span style={{ color: cfg.color, fontSize: 13, fontWeight: 600 }}>
                          {cfg.icon} {cfg.label}
                        </span>
                        {c.flagged && <span style={{ marginLeft: 6, fontSize: 11, color: '#ff4d4d' }}>⚠️ Flagged</span>}
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{c.lead_type || '—'}</td>
                      <td style={{ fontSize: 13 }}>{c.messages_sent}</td>
                      <td style={{ fontSize: 13 }}>{c.replies_received}</td>
                      <td style={{ fontSize: 13 }}>{c.ai_responses_sent}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        {c.last_inbound_at ? new Date(c.last_inbound_at).toLocaleDateString() : c.last_outbound_at ? new Date(c.last_outbound_at).toLocaleDateString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </section>
      )}

      {/* LAUNCH TAB */}
      {tab === 'launch' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">🚀 Launch AI pipeline</h2></div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, padding: '4px 0 20px' }}>
              <label className="settings-label">
                Lead type / context
                <select className="filter-select" value={leadType} onChange={e => setLeadType(e.target.value)}>
                  {LEAD_TYPES.map(lt => <option key={lt.value} value={lt.value}>{lt.label}</option>)}
                </select>
                <span className="settings-help">Tells the AI what kind of conversation to have</span>
              </label>
              <label className="settings-label">
                Channel
                <select className="filter-select" value={channel} onChange={e => setChannel(e.target.value)}>
                  <option value="sms">SMS text message</option>
                  <option value="email">Email</option>
                  <option value="both">Both — SMS + Email</option>
                </select>
              </label>
              <label className="settings-label">
                Tone
                <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                  {TONE_OPTIONS.map(t => (
                    <button key={t.key}
                      className={`lead-tone-pill ${tone === t.key ? 'lead-tone-pill--active' : ''}`}
                      onClick={() => setTone(t.key)} title={t.desc}>
                      {t.label}
                    </button>
                  ))}
                </div>
              </label>
              <label className="settings-label">
                AI direction
                <input className="settings-input" value={aiDirection}
                  onChange={e => setAiDirection(e.target.value)}
                  placeholder="e.g. Reconnect with old file check leads, ask if they still need pre-need planning" />
                <span className="settings-help">The more specific, the better the AI will perform</span>
              </label>
            </div>
            <label className="compose-checkbox" style={{ marginBottom: 20 }}>
              <input type="checkbox" checked={autoRespond} onChange={e => setAutoRespond(e.target.checked)} />
              <span>Auto-respond when confidence ≥ 85% — flag for review when below</span>
            </label>
            {!autoRespond && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16, padding: '10px 14px', background: 'rgba(47,182,255,0.06)', borderRadius: 8 }}>
                Manual review mode — every AI response will be queued for your approval before sending
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Select leads</h2>
              <span className="panel-count">{selectedLeads.size} selected</span>
            </div>
            <div style={{ maxHeight: 400, overflowY: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th><input type="checkbox" onChange={e => e.target.checked ? setSelectedLeads(new Set(leads.filter(l => l.status !== 'dnc' && !l.is_duplicate).map(l => l.id))) : setSelectedLeads(new Set())} /></th>
                    <th>Name</th>
                    <th>Phone</th>
                    <th>Tier</th>
                    <th>Status</th>
                    <th>Source year</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.filter(l => l.status !== 'dnc' && !l.is_duplicate).slice(0, 200).map(lead => (
                    <tr key={lead.id}>
                      <td><input type="checkbox" checked={selectedLeads.has(lead.id)}
                        onChange={e => {
                          const next = new Set(selectedLeads)
                          e.target.checked ? next.add(lead.id) : next.delete(lead.id)
                          setSelectedLeads(next)
                        }} /></td>
                      <td style={{ fontWeight: 600 }}>{lead.first_name} {lead.last_name}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{lead.phone || '—'}</td>
                      <td style={{ fontSize: 12 }}>{lead.tier || '—'}</td>
                      <td style={{ fontSize: 12 }}>{lead.status}</td>
                      <td style={{ fontSize: 12 }}>{lead.source_year || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ padding: '16px 0 0', display: 'flex', gap: 10, alignItems: 'center' }}>
              <button className="btn btn--primary" onClick={handleLaunch}
                disabled={launching || selectedLeads.size === 0}
                style={{ fontSize: 15, padding: '12px 28px' }}>
                {launching ? '⏳ Launching…' : `🚀 Launch pipeline for ${selectedLeads.size} leads`}
              </button>
              {launchResult && (
                <div style={{ fontSize: 13, color: launchResult.error ? 'var(--signal-red)' : 'var(--signal-green)' }}>
                  {launchResult.error || `✓ Launched ${launchResult.launched} · Skipped ${launchResult.skipped} · Errors ${launchResult.errors}`}
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
