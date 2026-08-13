import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import OutcomeTracker from '../components/OutcomeTracker'
import CaseFile from './CaseFile'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

const TONES = [
  { key: 'cold',   label: '❄️ Cold',   color: 'var(--signal-blue)',   desc: 'Soft intro, no pressure' },
  { key: 'warm',   label: '☀️ Warm',   color: 'var(--signal-amber)',  desc: 'Friendly, suggest meeting' },
  { key: 'hot',    label: '🔥 Hot',    color: 'var(--signal-red)',    desc: 'Direct, ask for appointment' },
  { key: 'urgent', label: '⚡ Urgent', color: 'var(--signal-purple)', desc: 'Brief, time-sensitive ask' },
]

// Mirrors APPT_TYPE_MAP in app/services/sms_service.py
const APPT_TYPE_MAP = {
  pre_need:          'Pre-Need Planning Consultation',
  'pre-need':        'Pre-Need Planning Consultation',
  preneed:           'Pre-Need Planning Consultation',
  preplanning:       'Pre-Planning Consultation',
  pre_planning:      'Pre-Planning Consultation',
  at_need:           'At-Need Arrangement Conference',
  'at-need':         'At-Need Arrangement Conference',
  atneed:            'At-Need Arrangement Conference',
  imminent:          'Immediate Need Consultation',
  urgent:            'Urgent Arrangement Consultation',
  file_check:        'Family File Review',
  'file check':      'Family File Review',
  code_lead:         'Family File Review',
  'code lead':       'Family File Review',
  file_review:       'Family File Review',
  property:          'Property Ownership Review',
  property_transfer: 'Property Transfer Appointment',
  plot:              'Cemetery Property Consultation',
  marker:            'Marker & Memorial Consultation',
  memorial:          'Memorial Planning Consultation',
  flower:            'Memorial Flower Review',
  flowers:           'Memorial Flower Review',
  contract:          'Contract Review Appointment',
  contract_sold:     'Contract Review Appointment',
  existing_customer: 'Family Services Appointment',
  referral:          'Family Services Consultation',
  web_lead:          'General Consultation',
  'web lead':        'General Consultation',
  new_inquiry:       'New Family Consultation',
  'new inquiry':     'New Family Consultation',
  insurance:         'Insurance & Benefits Review',
  benefits:          'Benefits & Coverage Consultation',
  veteran:           'Veterans Benefits Consultation',
  veterans:          'Veterans Benefits Consultation',
  general:           'Family Services Appointment',
}

const APPT_TYPE_OPTIONS = [
  'Pre-Need Planning Consultation',
  'Pre-Planning Consultation',
  'At-Need Arrangement Conference',
  'Immediate Need Consultation',
  'Urgent Arrangement Consultation',
  'Family File Review',
  'Property Ownership Review',
  'Property Transfer Appointment',
  'Cemetery Property Consultation',
  'Marker & Memorial Consultation',
  'Memorial Planning Consultation',
  'Memorial Flower Review',
  'Contract Review Appointment',
  'Family Services Appointment',
  'Family Services Consultation',
  'General Consultation',
  'New Family Consultation',
  'Insurance & Benefits Review',
  'Benefits & Coverage Consultation',
  'Veterans Benefits Consultation',
]

// Auto-detect appointment label from lead fields
function detectApptLabel(tier, messageTrack, contactChannel) {
  for (const field of [messageTrack, tier, contactChannel]) {
    if (!field) continue
    const key = field.toLowerCase().trim()
    if (APPT_TYPE_MAP[key]) return APPT_TYPE_MAP[key]
    for (const [mapKey, label] of Object.entries(APPT_TYPE_MAP)) {
      if (mapKey.includes(key) || key.includes(mapKey)) return label
    }
  }
  return 'Family Services Appointment'
}

// Smart subject line based on tier / message_track — no AI call needed
function smartSubject(firstName, tier, messageTrack) {
  const name = firstName ? `, ${firstName}` : ''
  const track = (messageTrack || '').toLowerCase()
  const t = (tier || '').toLowerCase()

  if (track.includes('pre_need') || track.includes('preneed') || track.includes('pre-need') ||
      t.includes('pre_need') || t.includes('preneed')) {
    return `Quick question about your pre-need plan${name}`
  }
  if (track.includes('at_need') || track.includes('atneed') || t.includes('at_need')) {
    return `We're here for you${name}`
  }
  if (track.includes('file_check') || track.includes('code_lead') || track.includes('file_review') ||
      t.includes('file_check') || t.includes('code_lead')) {
    return `Your family file at Restland${name}`
  }
  if (track.includes('property') || track.includes('plot') || t.includes('property')) {
    return `Your property at Restland${name}`
  }
  if (track.includes('marker') || track.includes('memorial') || t.includes('marker') || t.includes('memorial')) {
    return `Your memorial arrangement${name}`
  }
  if (track.includes('veteran') || t.includes('veteran')) {
    return `Your veterans benefits${name}`
  }
  if (track.includes('insurance') || track.includes('benefits') || t.includes('insurance')) {
    return `Your insurance & benefits review${name}`
  }
  if (track.includes('referral') || t.includes('referral')) {
    return `Someone thought of you${name}`
  }
  if (track.includes('imminent') || t.includes('imminent')) {
    return `We're ready to help${name}`
  }
  return `Checking in${name}`
}

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

// ConversationBubble is a proper sub-component (not inline in .map)
// so useState hooks are always called at the top level — no rules-of-hooks violations.
function ConversationBubble({ event: e }) {
  const [expanded, setExpanded] = useState(false)

  // Prefer body; fall back to body_preview for email messages
  const rawText = (e.body || e.body_preview || '').trim()
  const THRESHOLD = 120
  const isLong = rawText.length > THRESHOLD
  const displayText = isLong && !expanded ? rawText.slice(0, THRESHOLD) + '…' : rawText

  return (
    <div className={[
      'lead-bubble',
      `lead-bubble--${e.type}`,
      e.channel === 'email'   ? 'lead-bubble--email'  : '',
      e.channel === 'cadence' ? 'lead-bubble--system' : '',
    ].join(' ').trim()}>
      {e.type === 'inbound' && e.is_hot && (
        <div className="lead-bubble-hot">
          <SignalPulse color="red" size={6} /> Hot reply
        </div>
      )}

      {/* Header row: channel icon + subject + timestamp on same line */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        {e.channel && e.channel !== 'sms' && (
          <span className="lead-bubble-channel">
            {e.channel === 'email' ? '✉️' : e.channel === 'cadence' ? '🔁' : e.channel}
          </span>
        )}
        {e.subject && (
          <span style={{
            fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)',
            flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {e.subject}
          </span>
        )}
        <span className="lead-bubble-time">{timeAgo(e.timestamp)}</span>
      </div>

      {/* Message body */}
      {rawText ? (
        <p className="lead-bubble-text" style={{ margin: 0 }}>{displayText}</p>
      ) : (
        <p className="lead-bubble-text" style={{ margin: 0, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
          {e.subject ? '(email — no body preview)' : '(no message body)'}
        </p>
      )}

      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          style={{
            fontSize: 11, color: 'var(--accent)', background: 'none',
            border: 'none', cursor: 'pointer', padding: '2px 0', marginTop: 2,
          }}
        >
          {expanded ? 'Show less ▲' : 'Show more ▼'}
        </button>
      )}
    </div>
  )
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
  const [emailDraftReady, setEmailDraftReady] = useState(false)
  const [suggestingReply, setSuggestingReply] = useState(false)
  const [sendError, setSendError] = useState('')
  const [sendMode, setSendMode] = useState('sms') // 'sms' | 'email'
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [aiConvStatus, setAiConvStatus] = useState(null)
  const [aiConvLoading, setAiConvLoading] = useState(false)
  const [aiConvChannel, setAiConvChannel] = useState('email')
  const [calling, setCalling] = useState(false)
  const [callResult, setCallResult] = useState(null)
  const [tone, setTone] = useState(1) // 0=cold 1=warm 2=hot 3=urgent
  const [aiDirection, setAiDirection] = useState('')
  // Appointment type: auto-detected from tier, manually overridable
  const [apptLabel, setApptLabel] = useState('')
  // Lead editing
  const [showEdit, setShowEdit] = useState(false)
  const [editForm, setEditForm] = useState({})
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState('')
  const [editSuccess, setEditSuccess] = useState(false)
  const currentUser = getCurrentUser()
  const canReassignLead = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignmentSaving, setAssignmentSaving] = useState(false)
  const [assignmentError, setAssignmentError] = useState('')
  const [showCaseFile, setShowCaseFile] = useState(false)
  const [activity, setActivity] = useState(null)
  const [activityLoading, setActivityLoading] = useState(false)
  const [showActivity, setShowActivity] = useState(false)
  const timelineRef = useRef(null)

  function load() {
    setLoading(true)
    // Also load AI conversation status
    api.get(`/ai-conversation/status/${leadId}`)
      .then(s => setAiConvStatus(s))
      .catch(() => {})
    api.get(`/leads/${leadId}/timeline`)
      .then((d) => {
        setData(d)
        // Auto-detect appt label on first load; preserve manual selection afterward
        setApptLabel((prev) =>
          prev || detectApptLabel(d?.lead?.tier, d?.lead?.message_track, d?.lead?.contact_channel)
        )
      })
      .catch((err) => {
        console.error('LeadDetail load error:', err)
        setSendError(err.message || 'Failed to load lead')
      })
      .finally(() => setLoading(false))
  }

  // Initial load
  useEffect(() => { load() }, [leadId])

  // Auto-refresh every 30 seconds — reuses existing load(), clears on unmount
  useEffect(() => {
    const interval = setInterval(() => {
      api.get(`/leads/${leadId}/timeline`)
        .then((d) => setData(d))
        .catch(() => {/* silent on background refresh */})
    }, 30000)
    return () => clearInterval(interval)
  }, [leadId])

  // Scroll conversation to bottom whenever events change
  useEffect(() => {
    if (timelineRef.current) {
      timelineRef.current.scrollTop = timelineRef.current.scrollHeight
    }
  }, [data?.events?.length])

  useEffect(() => {
    if (!canReassignLead) return
    api.get('/admin/users')
      .then((users) =>
        setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin')))
      )
      .catch((err) => setAssignmentError(err.message))
  }, [canReassignLead])

  async function handleSaveEdit() {
    setEditError('')
    setEditSaving(true)
    setEditSuccess(false)
    try {
      await api.patch(`/leads/${leadId}`, editForm)
      setEditSuccess(true)
      setShowEdit(false)
      load()
      setTimeout(() => setEditSuccess(false), 3000)
    } catch (err) {
      setEditError(err.message || 'Save failed')
    } finally {
      setEditSaving(false)
    }
  }

  async function handleCall() {
    if (!lead.phone) { alert('This lead has no phone number.'); return }
    if (!confirm(`Call ${lead.first_name || 'this lead'} at ${lead.phone}?`)) return
    setCalling(true)
    setCallResult(null)
    try {
      const result = await api.post(`/voice/call/${leadId}`, {})
      setCallResult(result)
      setTimeout(() => load(), 3000)
    } catch (err) {
      alert(err.message)
    } finally {
      setCalling(false)
    }
  }

  async function handleStartAiConversation() {
    setAiConvLoading(true)
    try {
      const result = await api.post('/ai-conversation/start', { lead_id: leadId, channel: aiConvChannel })
      if (result.success) {
        setAiConvStatus({ active: true, stage: 'outreach_sent', touch_number: 1, messages_sent: 1 })
        load()
      } else if (result.already_active) {
        alert('AI conversation is already active for this lead.')
      } else {
        alert(result.error || 'Failed to start AI conversation')
      }
    } catch (err) {
      alert(err.message)
    } finally {
      setAiConvLoading(false)
    }
  }

  async function handlePauseAiConversation() {
    try {
      await api.post('/ai-conversation/pause', { lead_id: leadId })
      setAiConvStatus(s => ({ ...s, active: false, paused: true }))
    } catch (err) {
      alert(err.message)
    }
  }

  async function handleResumeAiConversation() {
    try {
      await api.post('/ai-conversation/resume', { lead_id: leadId })
      setAiConvStatus(s => ({ ...s, active: true, paused: false }))
    } catch (err) {
      alert(err.message)
    }
  }

  async function handleSuggestReply() {
    setSuggestingReply(true)
    setSendError('')
    try {
      const draft = await api.post(`/sms/draft-reply/${leadId}`, {
        tone: TONES[tone].key,
        ai_direction: aiDirection || null,
      })
      // Strip raw booking URLs from the text — the link is appended cleanly at send time
      const cleanReply = (draft.suggested_reply || '').replace(/https?:\/\/\S+/g, '').trim()
      setMessageText(cleanReply)
      // Keep includeBookingLink checked so the link is added cleanly on send
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSuggestingReply(false)
    }
  }

  async function handleSuggestEmail() {
    setSuggestingReply(true)
    setSendError('')
    try {
      const draft = await api.post(`/email/draft/${leadId}`, {
        tone: TONES[tone].key,
        ai_direction: aiDirection || null,
      })
      // Use first option body; strip any raw booking URLs — button added once by backend
      const option = draft.options?.[0] || {}
      const cleanBody = (option.body || draft.suggested_reply || '')
        .replace(/https?:\/\/\S+/g, '')
        .trim()
      setEmailBody(cleanBody)
      setEmailDraftReady(true)
      // Smart subject from tier/track — no AI call needed
      const lead = data?.lead
      setEmailSubject(
        option.subject ||
        smartSubject(lead?.first_name, lead?.tier, lead?.message_track)
      )
      setIncludeBookingLink(true)
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
        subject: emailSubject || smartSubject(lead?.first_name, lead?.tier, lead?.message_track),
        body: emailBody,
        include_booking_link: includeBookingLink,
        appt_label: apptLabel,
      })
      setEmailSubject('')
      setEmailBody('')
      setEmailDraftReady(false)
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

  async function handleLoadActivity() {
    if (activity) { setShowActivity(a => !a); return }
    setActivityLoading(true)
    try {
      const d = await api.get(`/leads/${leadId}/activity`)
      setActivity(d)
      setShowActivity(true)
    } catch (err) {
      console.error('Activity load error:', err)
    } finally {
      setActivityLoading(false)
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
  const canSendSMS   = lead.phone  && lead.status !== 'dnc' && !lead.is_duplicate
  const canSendEmail = lead.email  && lead.status !== 'dnc' && !lead.is_duplicate
  const canSend      = canSendSMS || canSendEmail
  const initials     = `${(lead.first_name || '?')[0]}${(lead.last_name || '?')[0]}`.toUpperCase()
  const currentTone  = TONES[tone]
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <h1 className="lead-detail-name">{lead.first_name} {lead.last_name}</h1>
              <button
                className="btn btn--secondary btn--sm"
                style={{ fontSize: 11, padding: '3px 10px' }}
                onClick={() => {
                  setEditForm({ first_name: lead.first_name || '', last_name: lead.last_name || '', phone: lead.phone || '', email: lead.email || '', notes: lead.notes || '', street_address: lead.street_address || '', city: lead.city || '', state: lead.state || '', zip_code: lead.zip_code || '', relationship_type: lead.relationship_type || 'cold_lead' })
                  setEditError('')
                  setShowEdit(e => !e)
                }}
              >
                {showEdit ? '✕ Cancel' : '✏️ Edit'}
              </button>
              {editSuccess && <span style={{ fontSize: 12, color: 'var(--signal-green)' }}>✓ Saved</span>}
            </div>
            <div className="lead-detail-contact">
              {lead.phone && <span className="mono">📱 {lead.phone}</span>}
              {lead.email && <span className="mono">✉️ {lead.email}</span>}
              {(lead.city || lead.street_address) && (
                <span className="mono" style={{ color: 'var(--text-secondary)' }}>
                  📍 {[lead.street_address, lead.city, lead.state, lead.zip_code].filter(Boolean).join(', ')}
                </span>
              )}
            </div>
            <div className="lead-detail-badges">
              <TierBadge tier={lead.tier} />
              <StatusBadge status={lead.status} />
              {lead.relationship_type && lead.relationship_type !== 'cold_lead' && (
                <span className="badge badge--neutral-dim" title="Lead relationship type — affects AI familiarity">
                  {{
                    warm_lead: '☀️ Warm',
                    re_engagement: '🔄 Re-engage',
                    previous_prospect: '📋 Prev. prospect',
                    past_customer: '🤝 Past customer',
                    existing_customer: '⭐ Existing customer',
                  }[lead.relationship_type] || lead.relationship_type}
                </span>
              )}
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

      {/* ── Inline Lead Edit Panel ── */}
      {showEdit && (
        <section className="panel" style={{ marginBottom: 16, padding: '16px 20px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 12 }}>
            <label className="leads-add-label">First name
              <input className="search-input" value={editForm.first_name || ''}
                onChange={e => setEditForm(f => ({ ...f, first_name: e.target.value }))} />
            </label>
            <label className="leads-add-label">Last name
              <input className="search-input" value={editForm.last_name || ''}
                onChange={e => setEditForm(f => ({ ...f, last_name: e.target.value }))} />
            </label>
            <label className="leads-add-label">Phone
              <input className="search-input" value={editForm.phone || ''}
                onChange={e => setEditForm(f => ({ ...f, phone: e.target.value }))}
                placeholder="e.g. 214-555-0199" />
            </label>
            <label className="leads-add-label">Email
              <input className="search-input" value={editForm.email || ''}
                onChange={e => setEditForm(f => ({ ...f, email: e.target.value }))}
                placeholder="email@example.com" />
            </label>
            <label className="leads-add-label">Street address
              <input className="search-input" value={editForm.street_address || ''}
                onChange={e => setEditForm(f => ({ ...f, street_address: e.target.value }))}
                placeholder="123 Main St" />
            </label>
            <label className="leads-add-label">City
              <input className="search-input" value={editForm.city || ''}
                onChange={e => setEditForm(f => ({ ...f, city: e.target.value }))} />
            </label>
            <label className="leads-add-label">State
              <input className="search-input" value={editForm.state || ''}
                onChange={e => setEditForm(f => ({ ...f, state: e.target.value }))}
                placeholder="TX" style={{ maxWidth: 80 }} />
            </label>
            <label className="leads-add-label">ZIP
              <input className="search-input" value={editForm.zip_code || ''}
                onChange={e => setEditForm(f => ({ ...f, zip_code: e.target.value }))}
                placeholder="75001" style={{ maxWidth: 120 }} />
            </label>
          </div>
          <label className="leads-add-label" style={{ marginBottom: 12 }}>
            Lead relationship
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '2px 0 6px' }}>
              This is the PRIMARY AI context — it controls how familiar the AI sounds.
            </p>
            <select
              className="search-input"
              value={editForm.relationship_type || 'cold_lead'}
              onChange={e => setEditForm(f => ({ ...f, relationship_type: e.target.value }))}
            >
              <option value="cold_lead">❄️ Cold lead — no prior relationship</option>
              <option value="warm_lead">☀️ Warm lead — showed prior interest / referral</option>
              <option value="re_engagement">🔄 Re-engagement — contacted before, went quiet</option>
              <option value="previous_prospect">📋 Previous prospect — was in pipeline, didn't close</option>
              <option value="past_customer">🤝 Past customer — was a customer, lapsed</option>
              <option value="existing_customer">⭐ Existing customer — active relationship</option>
            </select>
          </label>
          <label className="leads-add-label" style={{ marginBottom: 12 }}>Notes
            <textarea className="search-input" rows={2} value={editForm.notes || ''}
              onChange={e => setEditForm(f => ({ ...f, notes: e.target.value }))}
              style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13 }} />
          </label>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn btn--primary" onClick={handleSaveEdit} disabled={editSaving}>
              {editSaving ? 'Saving…' : 'Save changes'}
            </button>
            <button className="btn btn--secondary" onClick={() => setShowEdit(false)}>Cancel</button>
            {editError && <span style={{ color: 'var(--signal-red)', fontSize: 12 }}>{editError}</span>}
          </div>
        </section>
      )}

      {assignmentError && <div className="compose-error">{assignmentError}</div>}

      <div className="lead-detail-grid">
        <div className="lead-detail-left">

          {/* ── Conversation thread ── */}
          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">💬 Conversation</h2>
              <span className="panel-count">{events.length}</span>
            </div>
            {events.length === 0 ? (
              <div className="empty-state">No messages yet. Send the first one below.</div>
            ) : (
              <div
                className="lead-timeline"
                ref={timelineRef}
                style={{ maxHeight: '400px', overflowY: 'auto' }}
              >
                {events.map((e, i) => (
                  <ConversationBubble key={i} event={e} />
                ))}
              </div>
            )}
          </section>

          {/* ── Appointment type + Send a message ── */}
          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">✏️ Send a message</h2>
              {canSendSMS && canSendEmail && (
                <div className="lead-send-mode-tabs">
                  <button
                    className={`lead-send-tab ${effectiveSendMode === 'sms' ? 'lead-send-tab--active' : ''}`}
                    onClick={() => setSendMode('sms')}
                  >💬 SMS</button>
                  <button
                    className={`lead-send-tab ${effectiveSendMode === 'email' ? 'lead-send-tab--active' : ''}`}
                    onClick={() => setSendMode('email')}
                  >✉️ Email</button>
                </div>
              )}
            </div>

            {/* Appointment type dropdown — visible for both SMS and email */}
            {canSend && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <label style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap', fontWeight: 600 }}>
                  📅 Appt type
                </label>
                <select
                  className="filter-select"
                  style={{ flex: 1, fontSize: 12 }}
                  value={apptLabel}
                  onChange={(e) => setApptLabel(e.target.value)}
                >
                  {APPT_TYPE_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              </div>
            )}

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
                      <button
                        key={t.key}
                        className={`lead-tone-pill ${tone === i ? 'lead-tone-pill--active' : ''}`}
                        style={tone === i ? { borderColor: t.color, color: t.color, background: `${t.color}18` } : {}}
                        onClick={() => setTone(i)}
                        title={t.desc}
                      >{t.label}</button>
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
                    onChange={(e) => setAiDirection(e.target.value)}
                  />
                </div>
                <div className="lead-compose-suggest">
                  <button className="btn btn--secondary" onClick={handleSuggestReply} disabled={suggestingReply}>
                    {suggestingReply ? '⏳ Drafting…' : `✨ Suggest ${currentTone.label} reply`}
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
                    {sending ? 'Sending…' : 'Send SMS'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            ) : canSendEmail ? (
              <div className="lead-compose">
                <div className="lead-tone-bar">
                  <span className="lead-tone-label">Message tone</span>
                  <div className="lead-tone-pills">
                    {TONES.map((t, i) => (
                      <button
                        key={t.key}
                        className={`lead-tone-pill ${tone === i ? 'lead-tone-pill--active' : ''}`}
                        style={tone === i ? { borderColor: t.color, color: t.color, background: `${t.color}18` } : {}}
                        onClick={() => setTone(i)}
                        title={t.desc}
                      >{t.label}</button>
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
                    onChange={(e) => setAiDirection(e.target.value)}
                  />
                </div>
                <div className="lead-compose-suggest">
                  <button className="btn btn--secondary" onClick={handleSuggestEmail} disabled={suggestingReply}>
                    {suggestingReply ? '⏳ Drafting…' : `✨ AI draft ${currentTone.label} email`}
                  </button>
                  <span className="lead-compose-hint">Sends from your connected Microsoft 365 inbox.</span>
                </div>
                {emailDraftReady && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    background: 'var(--signal-green-dim, #e8f5e9)',
                    border: '1px solid var(--signal-green, #2e7d32)',
                    borderRadius: 6, padding: '7px 12px', fontSize: 13,
                    color: 'var(--signal-green, #2e7d32)', fontWeight: 600,
                  }}>
                    ✅ Draft ready — review and edit below, then send.
                    <button
                      style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
                      onClick={() => setEmailDraftReady(false)}
                      title="Dismiss"
                    >×</button>
                  </div>
                )}
                <input
                  className="compose-subject"
                  placeholder={`Subject — e.g. ${smartSubject(lead.first_name, lead.tier, lead.message_track)}`}
                  value={emailSubject}
                  onChange={(e) => setEmailSubject(e.target.value)}
                />
                <textarea
                  className="compose-textarea"
                  placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                  value={emailBody}
                  onChange={(e) => setEmailBody(e.target.value)}
                  rows={5}
                />
                <div className="compose-footer">
                  <label className="compose-checkbox">
                    <input
                      type="checkbox"
                      checked={includeBookingLink}
                      onChange={(e) => setIncludeBookingLink(e.target.checked)}
                    />
                    Include booking button
                  </label>
                  <button
                    className="btn btn--primary"
                    onClick={handleSendEmail}
                    disabled={sendingEmail || !emailBody.trim()}
                  >
                    {sendingEmail ? 'Sending…' : 'Send email'}
                  </button>
                </div>
                {sendError && <div className="compose-error">{sendError}</div>}
              </div>
            ) : null}
          </section>
        </div>

        <div className="lead-detail-right">
          {/* ── AI Conversation ── */}
          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">🤖 AI Conversation</h2>
              {aiConvStatus?.active && (
                <span className="badge badge--green" style={{ fontSize: 10 }}>ACTIVE</span>
              )}
              {aiConvStatus?.paused && (
                <span className="badge badge--amber" style={{ fontSize: 10 }}>PAUSED</span>
              )}
              {aiConvStatus?.flagged && (
                <span className="badge badge--red" style={{ fontSize: 10 }}>⚠️ NEEDS YOU</span>
              )}
            </div>

            {aiConvStatus?.flagged && (
              <div style={{ background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13, color: 'var(--signal-red)' }}>
                ⚠️ {aiConvStatus.flag_reason || 'Human response needed'}
              </div>
            )}

            {aiConvStatus?.active && !aiConvStatus?.flagged && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
                Touch {aiConvStatus.touch_number || 0} of 8 · {aiConvStatus.messages_sent || 0} sent
                {aiConvStatus.next_send_at && (
                  <span style={{ color: 'var(--text-tertiary)', display: 'block', fontSize: 11, marginTop: 2 }}>
                    Next: {new Date(aiConvStatus.next_send_at).toLocaleString()}
                  </span>
                )}
              </div>
            )}

            {!aiConvStatus?.active || aiConvStatus?.status === 'not_started' ? (
              <div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                  {['email', 'sms', 'both'].map(ch => (
                    <button
                      key={ch}
                      onClick={() => setAiConvChannel(ch)}
                      style={{
                        padding: '6px 14px', borderRadius: 20, border: '1px solid',
                        fontSize: 12, fontWeight: 600, cursor: 'pointer',
                        borderColor: aiConvChannel === ch ? 'var(--accent)' : 'var(--border-subtle)',
                        background: aiConvChannel === ch ? 'var(--accent)' : 'transparent',
                        color: aiConvChannel === ch ? '#fff' : 'var(--text-secondary)',
                      }}
                    >
                      {ch === 'email' ? '✉️ Email' : ch === 'sms' ? '💬 SMS' : '⚡ Both'}
                    </button>
                  ))}
                </div>
                <button
                  className="btn btn--primary"
                  style={{ width: '100%', fontSize: 14, padding: '12px' }}
                  onClick={handleStartAiConversation}
                  disabled={aiConvLoading || lead.status === 'dnc' || lead.is_duplicate}
                >
                  {aiConvLoading ? '⏳ Starting…' : '🤖 Start AI Conversation'}
                </button>
                <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8, textAlign: 'center' }}>
                  AI sends 8 emails over 14 days. Responds to replies 24/7. Pauses on escalation.
                </p>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: 8 }}>
                {aiConvStatus?.paused || aiConvStatus?.flagged ? (
                  <button className="btn btn--primary" style={{ flex: 1 }} onClick={handleResumeAiConversation}>
                    ▶️ Resume AI
                  </button>
                ) : (
                  <button className="btn btn--secondary" style={{ flex: 1 }} onClick={handlePauseAiConversation}>
                    ⏸️ Pause AI
                  </button>
                )}
              </div>
            )}
          </section>

          {/* ── Voice Call ── */}
          {lead.phone && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">📞 AI Voice Call</h2>
              </div>
              {callResult && (
                <div style={{ background: 'rgba(30,240,168,0.1)', border: '1px solid rgba(30,240,168,0.3)', borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: 13, color: 'var(--signal-green, #1ef0a8)' }}>
                  ✅ Call initiated — Call #{callResult.call_number} to {callResult.lead_name}
                </div>
              )}
              <button
                className="btn btn--primary"
                style={{ width: '100%', fontSize: 14, padding: '12px' }}
                onClick={handleCall}
                disabled={calling || lead.status === 'dnc' || lead.is_duplicate}
              >
                {calling ? '⏳ Calling…' : '📞 Call with AI'}
              </button>
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8, textAlign: 'center' }}>
                AI calls lead, discloses it's AI, books if they say yes. Records call. Max 3 attempts.
              </p>
            </section>
          )}

          {booking && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">📅 Booking</h2>
                <span className={`badge badge--${
                  booking.status === 'booked'    ? 'green' :
                  booking.status === 'cancelled' ? 'neutral-dim' : 'amber'
                }`}>
                  {booking.status}
                </span>
              </div>
              {booking.status === 'booked' && booking.booked_time && (
                <p className="lead-detail-info-text">
                  📅 {new Date(booking.booked_time).toLocaleString()}
                  {booking.calendar_event_id && ' · on Outlook Calendar'}
                </p>
              )}
              {booking.status === 'pending' && (
                <p className="lead-detail-info-text">Link sent — waiting for lead to pick a time.</p>
              )}
              {booking.status === 'booked' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <button
                    className="btn btn--primary"
                    style={{ width: '100%', fontSize: 13 }}
                    onClick={() => setShowCaseFile(true)}
                  >
                    📁 Open Case File
                  </button>
                  <button
                    className="btn btn--danger"
                    onClick={() => handleCancelBooking(booking.id)}
                    disabled={cancelling}
                  >
                    {cancelling ? 'Cancelling…' : 'Cancel booking'}
                  </button>
                </div>
              )}
            </section>
          )}

          <section className="panel lead-detail-panel">
            <div className="panel-header">
              <h2 className="panel-title">🤖 AI read</h2>
              <button
                className="btn btn--secondary"
                onClick={handleRunAnalysis}
                disabled={analyzing}
                style={{ fontSize: 11, padding: '4px 10px' }}
              >
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

          {/* ── Case File (always accessible, not just booked) ── */}
          {!booking && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">📁 Case File</h2>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                Record appointment outcomes, products discussed, policy details, and next steps.
              </p>
              <button
                className="btn btn--primary"
                style={{ width: '100%', fontSize: 13 }}
                onClick={() => setShowCaseFile(true)}
              >
                📁 Open Case File
              </button>
            </section>
          )}

          <OutcomeTracker leadId={leadId} />

          {/* ── Full Activity Log ── */}
          <section className="panel lead-detail-panel">
            <div className="panel-header" style={{ cursor: 'pointer' }} onClick={handleLoadActivity}>
              <h2 className="panel-title">🗂️ Activity Log</h2>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                {activityLoading ? 'Loading…' : showActivity ? '▲ collapse' : '▼ expand'}
              </span>
            </div>
            {showActivity && activity && (
              <div style={{ marginTop: 8 }}>
                {activity.events.length === 0 ? (
                  <div className="empty-state" style={{ padding: '12px 0' }}>No activity recorded yet.</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[...activity.events].reverse().map((ev) => {
                      const typeColors = {
                        lead_created:     { bg: 'rgba(100,100,255,0.08)', border: 'rgba(100,100,255,0.25)', icon: '👤' },
                        sms_sent:         { bg: 'rgba(30,200,168,0.07)', border: 'rgba(30,200,168,0.25)', icon: '📤' },
                        sms_reply:        { bg: ev.meta?.is_hot ? 'rgba(255,80,80,0.10)' : 'rgba(255,200,30,0.08)', border: ev.meta?.is_hot ? 'rgba(255,80,80,0.4)' : 'rgba(255,200,30,0.25)', icon: ev.meta?.is_hot ? '🔥' : '💬' },
                        email_sent:       { bg: 'rgba(80,160,255,0.08)', border: 'rgba(80,160,255,0.25)', icon: '📧' },
                        booking_booked:   { bg: 'rgba(30,240,130,0.10)', border: 'rgba(30,240,130,0.35)', icon: '📅' },
                        booking_confirmed:{ bg: 'rgba(30,240,130,0.10)', border: 'rgba(30,240,130,0.35)', icon: '✅' },
                        booking_expired:  { bg: 'rgba(180,180,180,0.08)', border: 'rgba(180,180,180,0.25)', icon: '⏰' },
                        booking_pending:  { bg: 'rgba(255,180,30,0.08)', border: 'rgba(255,180,30,0.25)', icon: '🔗' },
                        booking_cancelled:{ bg: 'rgba(255,80,80,0.08)', border: 'rgba(255,80,80,0.25)', icon: '❌' },
                        outcome_recorded: { bg: 'rgba(140,80,255,0.08)', border: 'rgba(140,80,255,0.25)', icon: '📝' },
                        cadence_started:  { bg: 'rgba(30,168,255,0.07)', border: 'rgba(30,168,255,0.2)', icon: '🤖' },
                        cadence_completed:{ bg: 'rgba(30,240,130,0.08)', border: 'rgba(30,240,130,0.25)', icon: '🏁' },
                        dnc_flagged:      { bg: 'rgba(255,30,30,0.08)', border: 'rgba(255,30,30,0.30)', icon: '⛔' },
                      }
                      const style = typeColors[ev.type] || { bg: 'rgba(128,128,128,0.06)', border: 'rgba(128,128,128,0.18)', icon: '•' }
                      const ts = ev.ts ? new Date(ev.ts).toLocaleString() : ''
                      return (
                        <div key={ev.id} style={{ background: style.bg, border: `1px solid ${style.border}`, borderRadius: 8, padding: '9px 12px' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                            <div style={{ fontWeight: 600, fontSize: 13 }}>
                              {style.icon} {ev.label}
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap', flexShrink: 0 }}>{ts}</div>
                          </div>
                          {ev.body && (
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>
                              {ev.body.length > 180 ? ev.body.slice(0, 180) + '…' : ev.body}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="panel lead-detail-panel">
            <div className="panel-header"><h2 className="panel-title">📋 Details</h2></div>
            <div className="lead-detail-facts">
              {[
                { label: 'Email',         value: lead.email },
                { label: 'Appt type',     value: apptLabel },
                { label: 'Source',        value: lead.source_file },
                { label: 'Source year',   value: lead.source_year },
                { label: 'Last action',   value: lead.last_action_raw },
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

      {showCaseFile && (
        <CaseFile
          lead={lead}
          onClose={() => setShowCaseFile(false)}
          onSaved={() => {}}
        />
      )}
    </div>
  )
}
