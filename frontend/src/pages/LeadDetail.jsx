import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import OutcomeTracker from '../components/OutcomeTracker'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

const TONES = [
  { key: 'cold', label: '❄️ Cold', color: 'var(--signal-blue)', desc: 'Soft intro, no pressure' },
  { key: 'warm', label: '☀️ Warm', color: 'var(--signal-amber)', desc: 'Friendly, suggest meeting' },
  { key: 'hot', label: '🔥 Hot', color: 'var(--signal-red)', desc: 'Direct, ask for appointment' },
  { key: 'urgent', label: '⚡ Urgent', color: 'var(--signal-purple)', desc: 'Brief, time-sensitive ask' },
]

function timeAgo(dateStr) {
  if (!dateStr) return ''
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return new Date(dateStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function LeadDetail() {
  const { leadId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [messageText, setMessageText] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [sending, setSending] = useState(false)
  const [sendingEmail, setSendingEmail] = useState(false)
  const [emailSubject, setEmailSubject] = useState('')
  const [emailBody, setEmailBody] = useState('')
  const [suggestingReply, setSuggestingReply] = useState(false)
  const [sendError, setSendError] = useState('')
  const [sendMode, setSendMode] = useState('sms') // 'sms' | 'email'
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [tone, setTone] = useState(1) // 0=cold 1=warm 2=hot 3=urgent
  const [aiDirection, setAiDirection] = useState('')  // per-lead AI messaging direction
  const currentUser = getCurrentUser()
  const canReassignLead = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignmentSaving, setAssignmentSaving] = useState(false)
  const [assignmentError, setAssignmentError] = useState('')

  function load() {
    setLoading(true)
    api.get(`/leads/${leadId}/timeline`)
      .then(setData)
      .catch((err) => {
        console.error('LeadDetail load error:', err)
        setSendError(err.message || 'Failed to load lead')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [leadId])

  useEffect(() => {
    if (!canReassignLead) return
    api.get('/admin/users')
      .then((users) => setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch((err) => setAssignmentError(err.message))
  }, [canReassignLead])

  async function handleSuggestReply() {
    setSuggestingReply(true)
    setSendError('')
    try {
      const draft = await api.post(`/sms/draft-reply/${leadId}`, { tone: TONES[tone].key, ai_direction: aiDirection || null })
      setMessageText(draft.suggested_reply || '')
      if (draft.booking_url) setIncludeBookingLink(false)
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSuggestingReply(false)
    }
  }

  async function handleSend() {
    if (!messageText.trim()) return
    setSending(true)
    setSendError('')
    try {
      await api.post('/sms/send', {
        lead_id: leadId,
        template: messageText,
        include_booking_link: includeBookingLink,
      })
      setMessageText('')
      load()
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }

  async function handleSendEmail() {
    if (!emailBody.trim()) return
    setSendingEmail(true)
    setSendError('')
    try {
      await api.post(`/email/send/${leadId}`, {
        subject: emailSubject || `Following up, ${lead?.first_name || 'there'}`,
        body: emailBody,
        include_booking_link: includeBookingLink,
      })
      setEmailSubject('')
      setEmailBody('')
      load()
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSendingEmail(false)
    }
  }

  async function handleRunAnalysis() {
    setAnalyzing(true)
    setAnalysisError('')
    try {
      await api.post(`/ai/analyze/${leadId}`, {})
      load()
    } catch (err) {
      setAnalysisError(err.message)
    } finally {
      setAnalyzing(false)
    }
  }

  async function handleCancelBooking(bookingId) {
    if (!confirm('Cancel this booking? This removes the calendar event too.')) return
    setCancelling(true)
    try {
      await api.post(`/calendar/cancel-booking/${bookingId}`, {})
      load()
    } catch (err) {
      alert(`Failed to cancel: ${err.message}`)
    } finally {
      setCancelling(false)
    }
  }

  async function handleAssignmentChange(event) {
    const newAssignedToId = event.target.value || null
    setAssignmentSaving(true)
    setAssignmentError('')
    try {
      await api.post('/admin/leads/reassign', {
        lead_ids: [leadId],
        new_assigned_to_id: newAssignedToId,
      })
      load()
    } catch (err) {
      setAssignmentError(err.message)
    } finally {
      setAssignmentSaving(false)
    }
  }

  if (loading) return <div className="empty-state" style={{ marginTop: 40 }}>Loading lead…</div>
  if (!data) return (
    <div className="empty-state" style={{ marginTop: 40 }}>
      <div>Couldn't load this lead.</div>
      {sendError && <div style={{ fontSize: 13, color: 'var(--signal-red)', marginTop: 8 }}>{sendError}</div>}
      <button className="btn btn--secondary" style={{ marginTop: 16 }} onClick={load}>Try again</button>
    </div>
  )

  const { lead, events, ai_quality, booking } = data
  const canSendSMS = lead.phone && lead.status !== 'dnc' && !lead.is_duplicate
  const canSendEmail = lead.email && lead.status !== 'dnc' && !lead.is_duplicate
  const canSend = canSendSMS || canSendEmail
  const initials = `${(lead.first_name || '?')[0]}${(lead.last_name || '?')[0]}`.toUpperCase()
  const currentTone = TONES[tone]
  // Default send mode based on what's available
  const effectiveSendMode = canSendSMS && sendMode === 'sms' ? 'sms' : canSendEmail ? 'email' : 'sms'

  return (
    <div className="lead-detail-page">
      <button className="lead-detail-back" onClick={() => navigate('/leads')}>
        ← Back to leads
      </button>

      <div className="lead-detail-hero">
        <div className="lead-detail-hero-left">
          <div className="lead-detail-avatar">{initials}</div>
          <div>
            <h1 className="lead-detail-name">{lead.first_name} {lead.last_name}</h1>
            <div className="lead-detail-contact">
              {lead.phone && <span className="mono">📱 {lead.phone}</span>}
              {lead.email && <span className="mono">✉️ {lead.email}</span>}
            </div>
            <div className="lead-detail-badges">
              <TierBadge tier={lead.tier} />
              <StatusBadge status={lead.status} />
              {lead.is_duplicate && <span className="badge badge--neutral-dim">Duplicate</span>}
            </div>
          </div>
        </div>
        {canReassignLead && (
          <div className="lead-detail-assign">
            <span className="lead-detail-assign-label">Assigned to</span>
            <select
              className="filter-select"
              value={lead.assigned_to_id || ''}
              onChange={handleAssignmentChange}
              disabled={assignmentSaving}
            >
              <option value="">Unassigned</option>
              {assignableUsers.map((user) => (
                <option key={user.id} value={user.id}>{user.full_name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {assignmentError && <div className="compose-error">{assignmentError}</div>}

      <div className="lead-detail-grid">
        <div className="lead-detail-left">
          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">💬 Conversation</h2>
              <span className="panel-count">{events.length}</span>
            </div>
            {events.length === 0 ? (
              <div className="empty-state">No messages yet. Send the first one below.</div>
            ) : (
              <div className="lead-timeline">
                {events.map((e, i) => (
                  <div key={i} className={`lead-bubble lead-bubble--${e.type} ${e.channel === 'email' ? 'lead-bubble--email' : ''} ${e.channel === 'cadence' ? 'lead-bubble--system' : ''}`}>
                    {e.type === 'inbound' && e.is_hot && (
                      <div className="lead-bubble-hot"><SignalPulse color="red" size={6} /> Hot reply</div>
                    )}
                    {e.channel && e.channel !== 'sms' && (
                      <span className="lead-bubble-channel">{e.channel === 'email' ? '✉️ Email' : e.channel === 'cadence' ? '🔁 Cadence' : e.channel}</span>
                    )}
                    <p className="lead-bubble-text">{e.body}</p>
                    {e.body_preview && <p className="lead-bubble-preview">{e.body_preview}</p>}
                    <span className="lead-bubble-time">{timeAgo(e.timestamp)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">✏️ Send a message</h2>
              {canSendSMS && canSendEmail && (
                <div className="lead-send-mode-tabs">
                  <button className={`lead-send-tab ${effectiveSendMode === 'sms' ? 'lead-send-tab--active' : ''}`} onClick={() => setSendMode('sms')}>💬 SMS</button>
                  <button className={`lead-send-tab ${effectiveSendMode === 'email' ? 'lead-send-tab--active' : ''}`} onClick={() => setSendMode('email')}>✉️ Email</button>
                </div>
              )}
            </div>
            {!canSend ? (
              <div className="empty-state">
                {lead.is_duplicate ? 'This lead is a duplicate.' :
                 lead.status === 'dnc' ? 'This lead is marked do-not-contact.' :
                 'No phone or email on file.'}
              </div>
            ) : effectiveSendMode === 'sms' && canSendSMS ? (
              <div className="lead-compose">
                <div className="lead-tone-bar">
                  <span className="lead-tone-label">Message tone</span>
                  <div className="lead-tone-pills">
                    {TONES.map((t, i) => (
                      <button key={t.key} className={`lead-tone-pill ${tone === i ? 'lead-tone-pill--active' : ''}`}
                        style={tone === i ? { borderColor: t.color, color: t.color, background: `${t.color}18` } : {}}
                        onClick={() => setTone(i)} title={t.desc}>
                        {t.label}
                      </button>
                    ))}
                  </div>
                  <span className="lead-tone-desc">{currentTone.desc}</span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    className="compose-subject"
                    style={{ flex: 1, fontSize: 12 }}
                    placeholder="AI direction: e.g. file check — ask if they still need planning"
                    value={aiDirection}
                    onChange={e => setAiDirection(e.target.value)}
                  />
                </div>
                <div className="lead-compose-suggest">
                  <button className="btn btn--secondary" onClick={handleSuggestReply} disabled={suggestingReply}>
                    {suggestingReply ? '⏳ Drafting…' : `✨ Suggest ${currentTone.label} reply`}
                  </button>
                  <span className="lead-compose-hint">AI fills the box. You edit and send manually.</span>
                </div>
                <textarea className="compose-textarea" placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                  value={messageText} onChange={(e) => setMessageText(e.target.value)} rows={4} />
                <div className="compose-footer">
                  <label className="compose-checkbox">
                    <input type="checkbox" checked={includeBookingLink} onChange={(e) => setIncludeBookingLink(e.target.checked)} />
                    Include booking link
                  </label>
                  <button className="btn btn--primary" onClick={handleSend} disabled={sending || !messageText.trim()}>
                    {sending ? 'Sending…' : 'Send SMS'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            ) : canSendEmail ? (
              <div className="lead-compose">
                <div className="lead-compose-suggest">
                  <button className="btn btn--secondary" onClick={async () => {
                    setSuggestingReply(true)
                    try {
                      const draft = await api.post(`/sms/draft-reply/${leadId}`, { tone: TONES[tone].key, ai_direction: aiDirection || null })
                      setEmailBody(draft.suggested_reply || '')
                      setEmailSubject(`Following up, ${lead.first_name || 'there'}`)
                    } catch (err) { setSendError(err.message) }
                    finally { setSuggestingReply(false) }
                  }} disabled={suggestingReply}>
                    {suggestingReply ? '⏳ Drafting…' : '✨ AI draft email'}
                  </button>
                  <span className="lead-compose-hint">Sends from your connected Microsoft 365 inbox.</span>
                </div>
                <input className="compose-subject" placeholder={`Subject — e.g. Following up, ${lead.first_name || 'there'}`}
                  value={emailSubject} onChange={(e) => setEmailSubject(e.target.value)} />
                <textarea className="compose-textarea" placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                  value={emailBody} onChange={(e) => setEmailBody(e.target.value)} rows={5} />
                <div className="compose-footer">
                  <button className="btn btn--primary" onClick={handleSendEmail} disabled={sendingEmail || !emailBody.trim()}>
                    {sendingEmail ? 'Sending…' : 'Send email'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            ) : null}
          </section>
        </div>

        <div className="lead-detail-right">
          {booking && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">📅 Booking</h2>
                <span className={`badge badge--${booking.status === 'booked' ? 'green' : booking.status === 'cancelled' ? 'neutral-dim' : 'amber'}`}>
                  {booking.status}
                </span>
              </div>
              {booking.status === 'booked' && booking.booked_time && (
                <p className="lead-detail-info-text">
                  📅 {new Date(booking.booked_time).toLocaleString()}
                  {booking.calendar_event_id && ' · on Google Calendar'}
                </p>
              )}
              {booking.status === 'pending' && (
                <p className="lead-detail-info-text">Link sent — waiting for lead to pick a time.</p>
              )}
              {booking.status === 'booked' && (
                <button className="btn btn--danger" onClick={() => handleCancelBooking(booking.id)} disabled={cancelling}>
                  {cancelling ? 'Cancelling…' : 'Cancel booking'}
                </button>
              )}
            </section>
          )}

          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">🤖 AI read</h2>
              <button className="btn btn--secondary" onClick={handleRunAnalysis} disabled={analyzing} style={{ fontSize: 11, padding: '4px 10px' }}>
                {analyzing ? 'Analyzing…' : ai_quality ? 'Re-analyze' : 'Run analysis'}
              </button>
            </div>
            {ai_quality ? (
              <div className="lead-ai-read">
                <span className={`badge badge--${QUALITY_COLOR[ai_quality.quality] || 'neutral'}`}>
                  {ai_quality.quality || 'unknown'}
                </span>
                {ai_quality.recommended_approach && (
                  <p className="lead-detail-info-text">{ai_quality.recommended_approach}</p>
                )}
              </div>
            ) : (
              <p className="lead-detail-info-text" style={{ color: 'var(--text-tertiary)' }}>
                No analysis yet. Run it to get a read on this lead.
              </p>
            )}
            {analysisError && <div className="compose-error">{analysisError}</div>}
          </section>

          <OutcomeTracker leadId={leadId} />

          <section className="panel lead-detail-panel">
            <div className="panel-header"><h2 className="panel-title">📋 Details</h2></div>
            <div className="lead-detail-facts">
              {[
                { label: 'Email', value: lead.email },
                { label: 'Source', value: lead.source_file },
                { label: 'Source year', value: lead.source_year },
                { label: 'Last action', value: lead.last_action_raw },
                { label: 'Status reason', value: lead.status_reason_raw },
              ].map(({ label, value }) => value ? (
                <div key={label} className="lead-detail-fact">
                  <span className="lead-detail-fact-label">{label}</span>
                  <span className="lead-detail-fact-value mono">{value}</span>
                </div>
              ) : null)}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
