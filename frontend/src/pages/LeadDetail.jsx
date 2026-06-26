import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import OutcomeTracker from '../components/OutcomeTracker'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

const TIER_OPTIONS = [
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'new_inquiry', label: 'New Inquiry' },
]

export default function LeadDetail() {
  const { leadId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [messageText, setMessageText] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [sending, setSending] = useState(false)
  const [suggestingReply, setSuggestingReply] = useState(false)
  const [replyTone, setReplyTone] = useState('standard')
  const [sendError, setSendError] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const currentUser = getCurrentUser()
  const canReassignLead = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignmentSaving, setAssignmentSaving] = useState(false)
  const [assignmentError, setAssignmentError] = useState('')
  const [tierSaving, setTierSaving] = useState(false)
  const [tierError, setTierError] = useState('')
  const [markingDnc, setMarkingDnc] = useState(false)
  const [dncError, setDncError] = useState('')

  // Editable Details panel - per Mike's explicit complaint: Lead Detail
  // let him VIEW phone/email but never edit them, with "no clear save
  // button in some areas."
  const [editingDetails, setEditingDetails] = useState(false)
  const [detailsForm, setDetailsForm] = useState({ phone: '', email: '', notes: '' })
  const [detailsSaving, setDetailsSaving] = useState(false)
  const [detailsError, setDetailsError] = useState('')
  const [detailsSaved, setDetailsSaved] = useState(false)

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
      const draft = await api.post(`/sms/draft-reply/${leadId}`, { tone: replyTone })
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

  async function handleTierChange(event) {
    const newTier = event.target.value
    if (!newTier) return
    setTierSaving(true)
    setTierError('')
    try {
      await api.patch(`/leads/${leadId}/tier?new_tier=${newTier}`, {})
      load()
    } catch (err) {
      setTierError(err.message || 'Could not update tier.')
    } finally {
      setTierSaving(false)
    }
  }

  async function handleMarkDnc() {
    const confirmed = window.confirm(
      "Mark this lead as Do Not Contact? This stops any active cadence and blocks this phone number from all future sends across the org. This can't be casually undone."
    )
    if (!confirmed) return

    setMarkingDnc(true)
    setDncError('')
    try {
      await api.patch(`/leads/${leadId}/mark-dnc`, {})
      load()
    } catch (err) {
      setDncError(err.message || 'Could not mark this lead DNC.')
    } finally {
      setMarkingDnc(false)
    }
  }

  function startEditingDetails(lead) {
    setDetailsForm({ phone: lead.phone || '', email: lead.email || '', notes: lead.notes || '' })
    setEditingDetails(true)
    setDetailsError('')
    setDetailsSaved(false)
  }

  async function handleSaveDetails() {
    setDetailsSaving(true)
    setDetailsError('')
    try {
      await api.patch(`/leads/${leadId}/details`, {
        phone: detailsForm.phone.trim() || null,
        email: detailsForm.email.trim() || null,
        notes: detailsForm.notes.trim() || null,
      })
      setEditingDetails(false)
      setDetailsSaved(true)
      load()
      setTimeout(() => setDetailsSaved(false), 3000)
    } catch (err) {
      setDetailsError(err.message || 'Could not save changes.')
    } finally {
      setDetailsSaving(false)
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
        <div className="lead-detail-header-actions">
          <TierBadge tier={lead.tier} />
          <label className="lead-tier-control">
            <span>{lead.status === 'needs_tier_review' ? 'Assign tier' : 'Change tier'}</span>
            <select
              value=""
              onChange={handleTierChange}
              disabled={tierSaving}
            >
              <option value="" disabled>{lead.tier ? 'Reassign…' : 'Assign…'}</option>
              {TIER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <StatusBadge status={lead.status} />
          {lead.status !== 'dnc' && (
            <button
              type="button"
              className="btn btn--danger lead-mark-dnc-btn"
              onClick={handleMarkDnc}
              disabled={markingDnc}
              title="Flag this lead as Do Not Contact - for when you spot a stop/opt-out request the system missed"
            >
              {markingDnc ? 'Marking…' : 'Mark DNC'}
            </button>
          )}
          {canReassignLead && (
            <label className="lead-assignment-control">
              <span>Assigned to</span>
              <select
                value={lead.assigned_to_id || ''}
                onChange={handleAssignmentChange}
                disabled={assignmentSaving}
              >
                <option value="">Unassigned</option>
                {assignableUsers.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.full_name} {user.role !== 'advisor' ? `(${user.role.replace('_', ' ')})` : ''}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </header>

      {assignmentError && <div className="compose-error lead-assignment-error">{assignmentError}</div>}
      {tierError && <div className="compose-error lead-assignment-error">{tierError}</div>}
      {dncError && <div className="compose-error lead-assignment-error">{dncError}</div>}

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
                <div className="compose-suggestion-row">
                  <select
                    className="compose-tone-select"
                    value={replyTone}
                    onChange={(e) => setReplyTone(e.target.value)}
                    disabled={suggestingReply}
                    title="How strongly should the suggested reply push for a follow-up?"
                  >
                    <option value="soft">Soft</option>
                    <option value="standard">Standard</option>
                    <option value="urgent">Urgent</option>
                    <option value="direct">Direct</option>
                  </select>
                  <button
                    type="button"
                    className="btn btn--secondary"
                    onClick={handleSuggestReply}
                    disabled={suggestingReply}
                  >
                    {suggestingReply ? 'Drafting…' : 'Suggest reply'}
                  </button>
                  <span className="compose-suggestion-hint">AI fills the box. You still edit and send manually.</span>
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

          <OutcomeTracker leadId={leadId} />

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
            <div className="panel-header">
              <h2 className="panel-title">Details</h2>
              {!editingDetails && (
                <button className="btn btn--secondary" onClick={() => startEditingDetails(lead)}>
                  Edit
                </button>
              )}
            </div>

            {detailsSaved && !editingDetails && (
              <div className="lead-details-saved-banner">Saved.</div>
            )}

            {editingDetails ? (
              <div className="lead-details-edit-form">
                <label className="settings-label">
                  Phone
                  <input
                    className="settings-input"
                    type="tel"
                    value={detailsForm.phone}
                    onChange={(e) => setDetailsForm({ ...detailsForm, phone: e.target.value })}
                    placeholder="(214) 555-0100"
                  />
                </label>
                <label className="settings-label">
                  Email
                  <input
                    className="settings-input"
                    type="email"
                    value={detailsForm.email}
                    onChange={(e) => setDetailsForm({ ...detailsForm, email: e.target.value })}
                  />
                </label>
                <label className="settings-label">
                  Notes
                  <textarea
                    className="compose-textarea"
                    rows={4}
                    value={detailsForm.notes}
                    onChange={(e) => setDetailsForm({ ...detailsForm, notes: e.target.value })}
                    placeholder="Anything worth remembering about this lead — call notes, family details, next steps…"
                  />
                </label>

                {detailsError && <div className="compose-error">{detailsError}</div>}

                <div className="settings-actions">
                  <button className="btn btn--secondary" onClick={() => setEditingDetails(false)} disabled={detailsSaving}>
                    Cancel
                  </button>
                  <button className="btn btn--primary" onClick={handleSaveDetails} disabled={detailsSaving}>
                    {detailsSaving ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </div>
            ) : (
              <table className="detail-table">
                <tbody>
                  <tr><td>Phone</td><td className="mono">{lead.phone || '—'}</td></tr>
                  <tr><td>Email</td><td className="mono">{lead.email || '—'}</td></tr>
                  <tr><td>Notes</td><td className="lead-notes-cell">{lead.notes || '—'}</td></tr>
                  <tr><td>Source year</td><td className="mono">{lead.source_year || '—'}</td></tr>
                  <tr><td>Last action</td><td>{lead.last_action_raw || '—'}</td></tr>
                  <tr><td>Status reason</td><td>{lead.status_reason_raw || '—'}</td></tr>
                  <tr><td>Imported from</td><td className="mono" style={{ fontSize: 11 }}>{lead.source_file || '—'}</td></tr>
                </tbody>
              </table>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
