import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

export default function LeadDetail() {
  const { leadId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [messageText, setMessageText] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)

  function load() {
    setLoading(true)
    api.get(`/leads/${leadId}/timeline`)
      .then(setData)
      .catch((err) => setSendError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [leadId])

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

  if (loading) return <div className="empty-state">Loading lead…</div>
  if (!data) return <div className="empty-state">Couldn't load this lead.</div>

  const { lead, events, ai_quality, booking } = data
  const canSend = lead.phone && lead.status !== 'dnc' && !lead.is_duplicate

  return (
    <div>
      <button className="back-link" onClick={() => navigate('/leads')}>
        <i className="ti ti-arrow-left" aria-hidden="true"></i> Back to leads
      </button>

      <header className="page-header">
        <div>
          <h1 className="page-title">{lead.first_name} {lead.last_name}</h1>
          <p className="page-subtitle mono">{lead.phone || lead.email || 'No contact info'}</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <TierBadge tier={lead.tier} />
          <StatusBadge status={lead.status} />
        </div>
      </header>

      <div className="detail-grid">
        <div>
          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="panel-header"><h2 className="panel-title">Conversation</h2></div>
            {events.length === 0 ? (
              <div className="empty-state">No messages yet. Send the first one below.</div>
            ) : (
              <div className="timeline">
                {events.map((e, i) => (
                  <div key={i} className={`timeline-bubble timeline-bubble--${e.type}`}>
                    {e.type === 'inbound' && e.is_hot && <SignalPulse color="red" size={6} label="Hot" />}
                    <p className="timeline-body">{e.body}</p>
                    <span className="timeline-time mono">{new Date(e.timestamp).toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">Send a message</h2></div>
            {!canSend ? (
              <div className="empty-state">
                {lead.is_duplicate ? 'This lead is a duplicate of one another advisor already owns.' :
                 lead.status === 'dnc' ? 'This lead is marked do-not-contact.' :
                 'This lead has no phone number on file — use the Email Queue instead.'}
              </div>
            ) : (
              <div className="compose-box">
                <textarea
                  className="compose-textarea"
                  placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  rows={4}
                />
                <div className="compose-footer">
                  <label className="compose-checkbox">
                    <input type="checkbox" checked={includeBookingLink} onChange={(e) => setIncludeBookingLink(e.target.checked)} />
                    Include booking link
                  </label>
                  <button className="btn btn--primary" onClick={handleSend} disabled={sending || !messageText.trim()}>
                    {sending ? 'Sending…' : 'Send now'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            )}
          </section>
        </div>

        <div>
          {booking && (
            <section className="panel" style={{ marginBottom: 16 }}>
              <div className="panel-header">
                <h2 className="panel-title">Booking</h2>
                <span className={`badge badge--${booking.status === 'booked' ? 'green' : booking.status === 'cancelled' ? 'neutral-dim' : 'amber'}`}>
                  {booking.status}
                </span>
              </div>
              {booking.status === 'booked' && booking.booked_time && (
                <p className="ai-quality-text">
                  Booked for {new Date(booking.booked_time).toLocaleString()}
                  {booking.calendar_event_id && ' — on Google Calendar'}
                </p>
              )}
              {booking.status === 'pending' && (
                <p className="ai-quality-text">Link sent, lead hasn't picked a time yet.</p>
              )}
              {booking.status === 'cancelled' && (
                <p className="ai-quality-text">This booking was cancelled.</p>
              )}
              {booking.status === 'booked' && (
                <button className="btn btn--danger" onClick={() => handleCancelBooking(booking.id)} disabled={cancelling}>
                  {cancelling ? 'Cancelling…' : 'Cancel booking'}
                </button>
              )}
            </section>
          )}

          <section className="panel" style={{ marginBottom: 16 }}>
            <div className="panel-header">
              <h2 className="panel-title">AI read</h2>
              <button className="btn btn--secondary" onClick={handleRunAnalysis} disabled={analyzing}>
                {analyzing ? 'Analyzing…' : ai_quality ? 'Re-analyze' : 'Run analysis'}
              </button>
            </div>
            {ai_quality ? (
              <>
                <div className="ai-quality-badge">
                  <span className={`badge badge--${QUALITY_COLOR[ai_quality.quality] || 'neutral'}`}>
                    {ai_quality.quality || 'unknown'}
                  </span>
                </div>
                {ai_quality.recommended_approach && (
                  <p className="ai-quality-text">{ai_quality.recommended_approach}</p>
                )}
              </>
            ) : (
              <p className="ai-quality-text">No analysis yet. Run it to get a read on this lead based on their call history.</p>
            )}
            {analysisError && <div className="compose-error" style={{ marginTop: 8 }}>{analysisError}</div>}
          </section>

          <section className="panel">
            <div className="panel-header"><h2 className="panel-title">Details</h2></div>
            <table className="detail-table">
              <tbody>
                <tr><td>Email</td><td className="mono">{lead.email || '—'}</td></tr>
                <tr><td>Source year</td><td className="mono">{lead.source_year || '—'}</td></tr>
                <tr><td>Last action</td><td>{lead.last_action_raw || '—'}</td></tr>
                <tr><td>Status reason</td><td>{lead.status_reason_raw || '—'}</td></tr>
                <tr><td>Imported from</td><td className="mono" style={{ fontSize: 11 }}>{lead.source_file || '—'}</td></tr>
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  )
}
