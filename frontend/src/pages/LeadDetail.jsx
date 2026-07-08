import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import OutcomeTracker from '../components/OutcomeTracker'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

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
  const [suggestingReply, setSuggestingReply] = useState(false)
  const [sendError, setSendError] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const currentUser = getCurrentUser()
  const canReassignLead = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignmentSaving, setAssignmentSaving] = useState(false)
  const [assignmentError, setAssignmentError] = useState('')

  function load() {
    setLoading(true)
    api.get(`/leads/${leadId}/timeline`)
      .then(setData)
      .catch((err) => setSendError(err.message))
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
      const draft = await api.post(`/sms/draft-reply/${leadId}`, {})
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
  if (!data) return <div className="empty-state" style={{ marginTop: 40 }}>Couldn't load this lead.</div>

  const { lead, events, ai_quality, booking } = data
  const canSend = lead.phone && lead.status !== 'dnc' && !lead.is_duplicate
  const initials = `${(lead.first_name || '?')[0]}${(lead.last_name || '?')[0]}`.toUpperCase()

  return (
    <div className="lead-detail-page">

      {/* ── Back ── */}
      <button className="lead-detail-back" onClick={() => navigate('/leads')}>
        ← Back to leads
      </button>

      {/* ── Hero Header ── */}
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

      {/* ── Main Grid ── */}
      <div className="lead-detail-grid">

        {/* Left — Conversation */}
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
                  <div key={i} className={`lead-bubble lead-bubble--${e.type}`}>
                    {e.type === 'inbound' && e.is_hot && (
                      <div className="lead-bubble-hot"><SignalPulse color="red" size={6} /> Hot reply</div>
                    )}
                    <p className="lead-bubble-text">{e.body}</p>
                    <span className="lead-bubble-time">{timeAgo(e.timestamp)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel lead-detail-panel">
            <div className="panel-header"><h2 className="panel-title">✏️ Send a message</h2></div>
            {!canSend ? (
              <div className="empty-state">
                {lead.is_duplicate ? 'This lead is a duplicate.' :
                 lead.status === 'dnc' ? 'This lead is marked do-not-contact.' :
                 'No phone number — use Email Queue instead.'}
              </div>
            ) : (
              <div className="lead-compose">
                <div className="lead-compose-suggest">
                  <button
                    className="btn btn--secondary"
                    onClick={handleSuggestReply}
                    disabled={suggestingReply}
                  >
                    {suggestingReply ? '⏳ Drafting…' : '✨ Suggest reply'}
                  </button>
                  <span className="lead-compose-hint">AI fills the box. You edit and send manually.</span>
                </div>
                <textarea
                  className="compose-textarea"
                  placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  rows={4}
                />
                <div className="compose-footer">
                  <label className="compose-checkbox">
                    <input
                      type="checkbox"
                      checked={includeBookingLink}
                      onChange={(e) => setIncludeBookingLink(e.target.checked)}
                    />
                    Include booking link
                  </label>
                  <button
                    className="btn btn--primary"
                    onClick={handleSend}
                    disabled={sending || !messageText.trim()}
                  >
                    {sending ? 'Sending…' : 'Send now'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            )}
          </section>
        </div>

        {/* Right — Info panels */}
        <div className="lead-detail-right">

          {/* Booking */}
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

          {/* AI Read */}
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

          {/* Outcome Tracker */}
          <OutcomeTracker leadId={leadId} />

          {/* Details */}
          <section className="panel lead-detail-panel">
            <div className="panel-header"><h2 className="panel-title">📋 Details</h2></div>
            <div className="lead-detail-facts">
              {[
                { label: 'Email', value: lead.email },
                { label: 'Source year', value: lead.source_year },
                { label: 'Last action', value: lead.last_action_raw },
                { label: 'Status reason', value: lead.status_reason_raw },
                { label: 'Source file', value: lead.source_file },
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
