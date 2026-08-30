import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import SignalPulse from '../components/SignalPulse'
import OutcomeTracker from '../components/OutcomeTracker'
import CaseFile from './CaseFile'
import { useToast } from '../components/Toast'
import { formatPhone } from '../utils/phone'
import '../styles/shared.css'
import './LeadDetail.css'

const QUALITY_COLOR = { hot: 'red', warm: 'amber', cold: 'blue', dead: 'neutral-dim', unknown: 'neutral' }

// Inline styles for the composer's new truth-telling panels: what will be
// sent, and whether it can be sent at all.
const SX = {
  previewOk: {
    background: 'rgba(30,168,255,0.07)', border: '1px solid rgba(30,168,255,0.28)',
    borderRadius: 8, padding: '9px 11px', marginBottom: 8,
  },
  previewWarn: {
    background: 'rgba(255,180,30,0.08)', border: '1px solid rgba(255,180,30,0.32)',
    borderRadius: 8, padding: '9px 11px', marginBottom: 8,
  },
  previewLabel: {
    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.06em',
    textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4,
  },
  previewBody: {
    fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-primary)',
    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  },
  previewMeta: { fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 },
  senderWarn: {
    background: 'rgba(255,80,80,0.08)', border: '1px solid rgba(255,80,80,0.3)',
    borderRadius: 8, padding: '9px 11px', marginBottom: 8,
    fontSize: 12.5, lineHeight: 1.5, color: 'var(--signal-red, #ff8a8a)',
  },
  senderOk: {
    fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 8,
  },
  channelNote: {
    fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 6, lineHeight: 1.5,
  },
}

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

// Fallback list — overridden by org-specific types fetched from the API
const DEFAULT_APPT_TYPE_OPTIONS = [
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

// The five delivery states an outbound message can be in. Presentation lives
// here and nowhere else, so the transcript cannot describe a row differently
// from the activity feed. Backend vocabulary: app/services/message_state.py.
const DELIVERY_STATES = {
  blocked:   { label: 'Blocked',   color: 'var(--signal-red)',    dot: '\u2298' },
  queued:    { label: 'Queued',    color: 'var(--text-tertiary)', dot: '\u25CB' },
  sent:      { label: 'Sent',      color: 'var(--text-secondary)', dot: '\u2713' },
  delivered: { label: 'Delivered', color: 'var(--signal-green)',  dot: '\u2713\u2713' },
  failed:    { label: 'Failed',    color: 'var(--signal-red)',    dot: '\u2717' },
}

// A receipt-free outbound message reads as Queued, never as delivered. The bug
// this closes: an SMS Twilio returned `undelivered` for still appeared in the
// case file as an ordinary sent message, so an operator believed a family had
// been contacted when no text ever reached the handset.
function DeliveryChip({ delivery }) {
  if (!delivery || !delivery.state) return null
  const meta = DELIVERY_STATES[delivery.state] || DELIVERY_STATES.queued
  const detail = [delivery.error_code ? `Twilio ${delivery.error_code}` : null,
                  delivery.error_message || null].filter(Boolean).join(' \u00B7 ')
  return (
    <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span
        title={delivery.description || ''}
        style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.04em',
          textTransform: 'uppercase', color: meta.color,
          display: 'inline-flex', alignItems: 'center', gap: 4,
        }}
      >
        <span aria-hidden="true">{meta.dot}</span>{meta.label}
      </span>
      {detail && (
        <span style={{ fontSize: 10, color: 'var(--signal-red)', lineHeight: 1.35 }}>
          {detail}
        </span>
      )}
    </div>
  )
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

      {e.type === 'outbound' && <DeliveryChip delivery={e.delivery} />}

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
  const toast = useToast()
  // What the backend says this composer may actually do with this lead:
  // per-channel capability, the resolved SMS sender, and the exact booking URL
  // that Send would use. See app/routers/compose_router.py.
  const [composeCtx, setComposeCtx] = useState(null)
  const [voiceReadiness, setVoiceReadiness] = useState(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [messageText, setMessageText] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [sending, setSending] = useState(false)
  const [sendingEmail, setSendingEmail] = useState(false)
  const [emailSubject, setEmailSubject] = useState('')
  const [emailBody, setEmailBody] = useState('')
  const [emailDraftReady, setEmailDraftReady] = useState(false)
  const [emailAttachment, setEmailAttachment] = useState(null) // File object
  const emailAttachRef = useRef(null)
  const [suggestingReply, setSuggestingReply] = useState(false)
  const [sendError, setSendError] = useState('')
  const [sendMode, setSendMode] = useState('sms') // 'sms' | 'email'
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [resendingLink, setResendingLink] = useState(false)
  const [resendLinkMsg, setResendLinkMsg] = useState(null) // {ok, text}
  const [aiConvStatus, setAiConvStatus] = useState(null)
  const [aiConvLoading, setAiConvLoading] = useState(false)
  const [aiConvChannel, setAiConvChannel] = useState('email')
  const [calling, setCalling] = useState(false)
  const [callResult, setCallResult] = useState(null)
  const [callError, setCallError] = useState('')
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
  const [activityError, setActivityError] = useState('')
  const [activeTab, setActiveTab] = useState('conversation') // 'conversation' | 'calls' | 'timeline'
  const [apptTypeOptions, setApptTypeOptions] = useState(DEFAULT_APPT_TYPE_OPTIONS)
  const timelineRef = useRef(null)

  // Manual flagging
  const [flagging, setFlagging] = useState(false)

  async function handleFlagLead(flagType) {
    if (flagType) {
      const label = flagType === 'bad_email' ? 'bad email' : 'remove from all outreach'
      if (!window.confirm(`Flag "${lead.first_name} ${lead.last_name}" as ${label}?\n\nYou can unflag anytime to restore them to all lists.`)) return
    }
    setFlagging(true)
    try {
      await api.patch(`/leads/${leadId}/flag`, { flag_type: flagType || null })
      load()
    } catch (err) {
      toast.error(err.message || 'The flag could not be saved.', { title: 'Flag failed' })
    } finally {
      setFlagging(false)
    }
  }

  // Phase 4: media/flyer attachment for SMS/MMS
  const [mediaUrl, setMediaUrl] = useState('')
  const [mediaFileName, setMediaFileName] = useState('')
  const [mediaUploading, setMediaUploading] = useState(false)
  const [mediaError, setMediaError] = useState('')
  const mediaInputRef = useRef(null)

  function loadActivity(silent = false) {
    if (!silent) setActivityLoading(true)
    setActivityError('')
    api.get(`/leads/${leadId}/activity`)
      .then(d => setActivity(d))
      .catch(err => {
        if (!silent) setActivityError(err.message || 'Failed to load activity log')
      })
      .finally(() => { if (!silent) setActivityLoading(false) })
  }

  function load() {
    setLoading(true)
    // Capability, sender readiness and the resolved booking URL, in one read.
    // Failures here must never block the page: the composer falls back to what
    // it can infer from the lead record alone.
    api.get(`/compose/${leadId}/context`)
      .then(c => setComposeCtx(c))
      .catch(() => setComposeCtx(null))
    api.get(`/voice/readiness/${leadId}`)
      .then(v => setVoiceReadiness(v))
      .catch(() => setVoiceReadiness(null))
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
    // Also load activity log in background
    loadActivity(true)
  }

  // Load org-specific appointment types once on mount
  useEffect(() => {
    api.get('/settings/appointment-types')
      .then(d => { if (d?.appointment_types?.length) setApptTypeOptions(d.appointment_types) })
      .catch(() => {}) // silently fall back to defaults
  }, [])

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
    if (calling) return                        // guards double submit
    const phone = data?.lead?.phone
    if (!phone) { toast.error('This lead has no phone number.'); return }
    if (!window.confirm(`Call ${data?.lead?.first_name || 'this lead'} at ${formatPhone(phone)}?`)) return
    setCalling(true)
    setCallResult(null)
    setCallError('')
    try {
      const result = await api.post(`/voice/call/${leadId}`, {})
      setCallResult(result)
      toast.success('Call placed.')
      setTimeout(() => load(), 3000)
    } catch (err) {
      // The backend now distinguishes a refusal (409, with the orchestrator's
      // reason) from a provider failure (502, with the provider's message).
      // Both are worth showing verbatim; neither is a network outage.
      const msg = err.message || 'The call could not be placed.'
      setCallError(msg)
      toast.error(msg, { title: err.status === 409 ? 'Call not permitted' : 'Call failed' })
    } finally {
      setCalling(false)                        // resets on EVERY path
    }
  }

  async function handleStartAiConversation() {
    if (aiConvLoading) return
    setAiConvLoading(true)
    try {
      // The channel actually in force, never the stale preference - starting
      // an email sequence for a lead with no email is the exact failure the
      // capability matrix exists to prevent.
      const channel = effectiveAiChannel || aiConvChannel
      const result = await api.post('/ai-conversation/start', { lead_id: leadId, channel })
      if (result.success) {
        setAiConvStatus({ active: true, stage: 'outreach_sent', touch_number: 1, messages_sent: 1 })
        toast.success('AI conversation started.')
        load()
      } else if (result.already_active) {
        toast.info('An AI conversation is already running for this lead.')
      } else {
        toast.error(result.error || 'Could not start the AI conversation.')
      }
    } catch (err) {
      toast.error(err.message || 'Could not start the AI conversation.')
    } finally {
      setAiConvLoading(false)
    }
  }

  async function handlePauseAiConversation() {
    try {
      await api.post('/ai-conversation/pause', { lead_id: leadId })
      setAiConvStatus(s => ({ ...s, active: false, paused: true }))
      toast.success('AI conversation paused.')
    } catch (err) {
      toast.error(err.message || 'Could not pause the AI conversation.')
    }
  }

  async function handleResumeAiConversation() {
    try {
      await api.post('/ai-conversation/resume', { lead_id: leadId })
      setAiConvStatus(s => ({ ...s, active: true, paused: false }))
      toast.success('AI conversation resumed.')
    } catch (err) {
      toast.error(err.message || 'Could not resume the AI conversation.')
    }
  }

  async function handleSuggestReply() {
    if (suggestingReply) return                  // guards double submit
    setSuggestingReply(true)
    setSendError('')
    try {
      const draft = await api.post(`/sms/draft-reply/${leadId}`, {
        tone: TONES[tone].key,
        ai_direction: aiDirection || null,
      })
      // Backend strips URLs before returning, but strip here too as a safety net.
      // The "Include booking link" checkbox appends the clean link at send time.
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
    if (suggestingReply) return                  // guards double submit
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

  async function handleMediaUpload(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setMediaUploading(true)
    setMediaError('')
    try {
      const formData = new FormData()
      formData.append('file', file)
      const result = await api.upload('/sms/upload-media', formData)
      setMediaUrl(result.media_url)
      setMediaFileName(result.filename)
    } catch (err) {
      setMediaError(err.message || 'Upload failed')
    } finally {
      setMediaUploading(false)
      if (mediaInputRef.current) mediaInputRef.current.value = ''
    }
  }

  function handleRemoveMedia() {
    setMediaUrl('')
    setMediaFileName('')
    setMediaError('')
  }

  async function handleSend() {
    if (!messageText.trim() || sending) return   // guards double submit
    setSending(true)
    setSendError('')
    try {
      if (mediaUrl) {
        // Send as MMS with media attachment
        await api.post('/sms/send-mms', {
          lead_id: leadId,
          template: messageText,
          media_url: mediaUrl,
          include_booking_link: includeBookingLink,
        })
        setMediaUrl('')
        setMediaFileName('')
      } else {
        await api.post('/sms/send', {
          lead_id: leadId,
          template: messageText,
          include_booking_link: includeBookingLink,
        })
      }
      setMessageText('')
      load()
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }

  async function handleSendEmail() {
    if (!emailBody.trim() || sendingEmail) return   // guards double submit
    setSendingEmail(true)
    setSendError('')
    try {
      if (emailAttachment) {
        // Use multipart endpoint when an attachment is present
        const formData = new FormData()
        formData.append('subject', emailSubject || smartSubject(lead?.first_name, lead?.tier, lead?.message_track))
        formData.append('body_html', emailBody)
        formData.append('include_booking_link', includeBookingLink ? 'true' : 'false')
        if (apptLabel) formData.append('appt_label', apptLabel)
        formData.append('file', emailAttachment)
        await api.upload(`/email/send-with-attachment/${leadId}`, formData)
        setEmailAttachment(null)
        if (emailAttachRef.current) emailAttachRef.current.value = ''
      } else {
        await api.post(`/email/send/${leadId}`, {
          subject: emailSubject || smartSubject(lead?.first_name, lead?.tier, lead?.message_track),
          body: emailBody,
          include_booking_link: includeBookingLink,
          appt_label: apptLabel,
        })
      }
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
      toast.error(err.message || 'The booking could not be cancelled.', { title: 'Cancel failed' })
    } finally {
      setCancelling(false)
    }
  }

  async function handleResendBookingLink() {
    setResendingLink(true)
    setResendLinkMsg(null)
    try {
      const res = await api.post(`/leads/${leadId}/resend-booking-link`, {})
      setResendLinkMsg({ ok: true, text: `Booking link sent to ${res.email_sent_to}` })
      load() // refresh booking panel
    } catch (err) {
      setResendLinkMsg({ ok: false, text: err.message || 'Failed to send booking link' })
    } finally {
      setResendingLink(false)
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

  function handleRefreshActivity() {
    loadActivity(false)
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

  // ── CHANNEL CAPABILITY ────────────────────────────────────────────────────
  //
  // Each channel depends only on what THAT channel needs. A lead with a phone
  // and no email can be texted and called; the missing email is a reason to
  // withhold Email and nothing else. The backend decides (compose_router), and
  // these locals fall back to the lead record when that read failed, so the
  // page still works if the endpoint is unreachable.
  const ch = composeCtx?.channels
  const notBlocked = lead.status !== 'dnc' && !lead.is_duplicate
  const canSendSMS   = ch ? ch.sms.available   : Boolean(lead.phone && notBlocked)
  const canSendEmail = ch ? ch.email.available : Boolean(lead.email && notBlocked)
  const canSendBoth  = ch ? ch.both.available  : (canSendSMS && canSendEmail)
  const canVoice     = ch ? ch.voice.available
                          : Boolean(lead.phone && notBlocked)
  const smsBlockedReason   = ch ? ch.sms.reason : null
  const emailBlockedReason = ch ? ch.email.reason : 'This lead has no email address.'
  const voiceBlockedReason = (voiceReadiness && !voiceReadiness.ready)
    ? voiceReadiness.reason
    : (ch ? ch.voice.reason : null)
  const smsSender = composeCtx?.sms_sender || null
  const bookingUrl = composeCtx?.booking?.url || ''
  const bookingUrlReason = composeCtx?.booking?.reason || null

  const canSend      = canSendSMS || canSendEmail
  const initials     = `${(lead.first_name || '?')[0]}${(lead.last_name || '?')[0]}`.toUpperCase()
  const currentTone  = TONES[tone]
  const effectiveSendMode = canSendSMS && sendMode === 'sms' ? 'sms' : canSendEmail ? 'email' : 'sms'

  // ── WHAT WILL ACTUALLY BE SENT ────────────────────────────────────────────
  //
  // Mirrors app/services/sms_service.py::compose_body exactly: substitute the
  // {booking_link} placeholder if the advisor used one, otherwise append the
  // URL. "Include booking link" checked used to show nothing in the box and
  // then either append at send time or - for a hand-typed message with no
  // placeholder - send no link at all while still recording one.
  function composePreview(text) {
    const body = String(text || '')
    if (!includeBookingLink || !bookingUrl) return body.replace('{booking_link}', '')
    if (body.includes('{booking_link}')) return body.replaceAll('{booking_link}', bookingUrl)
    if (body.includes(bookingUrl)) return body
    return (body.trimEnd() + '\n\n' + bookingUrl).trim()
  }
  const smsPreview = composePreview(messageText)

  // The AI-conversation channel actually in force. The stored preference is
  // honoured only if the lead can be reached that way; otherwise it falls back
  // to a channel that works, and to null when none does.
  const aiChannelAvailable = { email: canSendEmail, sms: canSendSMS, both: canSendBoth }
  const effectiveAiChannel = aiChannelAvailable[aiConvChannel]
    ? aiConvChannel
    : (canSendBoth ? 'both' : canSendEmail ? 'email' : canSendSMS ? 'sms' : null)

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
              {/* Flag / Unflag button */}
              {lead.manual_flag ? (
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ fontSize: 11, padding: '3px 10px', color: '#ffaa00', border: '1px solid rgba(255,170,0,0.35)' }}
                  onClick={() => handleFlagLead(null)}
                  disabled={flagging}
                  title="Unflag — restore to all lists"
                >
                  ⚑ Unflag
                </button>
              ) : (
                <select
                  style={{ fontSize: 11, padding: '3px 8px', cursor: 'pointer', color: 'var(--text-secondary)', background: 'var(--surface-card)', border: '1px solid var(--border-subtle)', borderRadius: 6 }}
                  defaultValue=""
                  onChange={(e) => { if (e.target.value) { handleFlagLead(e.target.value); e.target.value = '' } }}
                  disabled={flagging}
                  title="Flag this contact"
                >
                  <option value="" disabled>⚑ Flag</option>
                  <option value="bad_email">⚠ Bad email</option>
                  <option value="remove_all">⛔ Remove from all outreach</option>
                </select>
              )}
              {editSuccess && <span style={{ fontSize: 12, color: 'var(--signal-green)' }}>✓ Saved</span>}
            </div>
            <div className="lead-detail-contact">
              {lead.phone && <span className="mono">📱 {formatPhone(lead.phone)}</span>}
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
              {lead.manual_flag === 'bad_email' && (
                <span style={{ fontSize: 11, background: 'rgba(255,170,0,0.15)', color: '#ffaa00', border: '1px solid rgba(255,170,0,0.3)', borderRadius: 6, padding: '2px 8px' }}>⚠ bad email flagged</span>
              )}
              {lead.manual_flag === 'remove_all' && (
                <span style={{ fontSize: 11, background: 'rgba(255,80,80,0.15)', color: '#ff6464', border: '1px solid rgba(255,80,80,0.3)', borderRadius: 6, padding: '2px 8px' }}>⛔ removed from all outreach</span>
              )}
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

          {/* ── Tabbed Conversation + Full Timeline (Phase 2) ── */}
          <section className="panel lead-detail-panel">
            {/* Tab bar */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border-subtle)', marginBottom: 14, gap: 0 }}>
              <button
                onClick={() => setActiveTab('conversation')}
                style={{
                  padding: '8px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  background: 'none', border: 'none', borderBottom: activeTab === 'conversation' ? '2px solid var(--accent)' : '2px solid transparent',
                  color: activeTab === 'conversation' ? 'var(--accent)' : 'var(--text-secondary)',
                  marginBottom: -1,
                }}
              >
                💬 Conversation
                {events.length > 0 && (
                  <span style={{ marginLeft: 6, fontSize: 11, background: 'var(--accent)', color: '#fff', borderRadius: 10, padding: '1px 6px' }}>
                    {events.length}
                  </span>
                )}
              </button>
              <button
                onClick={() => setActiveTab('calls')}
                style={{
                  padding: '8px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  background: 'none', border: 'none', borderBottom: activeTab === 'calls' ? '2px solid var(--accent)' : '2px solid transparent',
                  color: activeTab === 'calls' ? 'var(--accent)' : 'var(--text-secondary)',
                  marginBottom: -1,
                }}
              >
                📞 Calls
                {data?.voice_calls?.length > 0 && (
                  <span style={{ marginLeft: 6, fontSize: 11, background: activeTab === 'calls' ? 'var(--accent)' : 'var(--border-subtle)', color: activeTab === 'calls' ? '#fff' : 'var(--text-secondary)', borderRadius: 10, padding: '1px 6px' }}>
                    {data.voice_calls.length}
                  </span>
                )}
              </button>
              <button
                onClick={() => setActiveTab('timeline')}
                style={{
                  padding: '8px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  background: 'none', border: 'none', borderBottom: activeTab === 'timeline' ? '2px solid var(--accent)' : '2px solid transparent',
                  color: activeTab === 'timeline' ? 'var(--accent)' : 'var(--text-secondary)',
                  marginBottom: -1,
                }}
              >
                🗂️ Full History
                {activity?.event_count > 0 && (
                  <span style={{ marginLeft: 6, fontSize: 11, background: activeTab === 'timeline' ? 'var(--accent)' : 'var(--border-subtle)', color: activeTab === 'timeline' ? '#fff' : 'var(--text-secondary)', borderRadius: 10, padding: '1px 6px' }}>
                    {activity.event_count}
                  </span>
                )}
              </button>
            </div>

            {/* Tab: Conversation */}
            {activeTab === 'conversation' && (
              events.length === 0 ? (
                <div className="empty-state">No messages yet. Send the first one below.</div>
              ) : (
                <div
                  className="lead-timeline"
                  ref={timelineRef}
                  style={{ maxHeight: '420px', overflowY: 'auto' }}
                >
                  {events.map((e, i) => (
                    <ConversationBubble key={i} event={e} />
                  ))}
                </div>
              )
            )}

            {/* Tab: Voice Calls + Transcripts */}
            {activeTab === 'calls' && (() => {
              const calls = data?.voice_calls || []
              if (calls.length === 0) {
                return <div className="empty-state">No AI voice calls yet. Use the "Call with AI" button below to start one.</div>
              }
              return (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '480px', overflowY: 'auto', paddingRight: 4 }}>
                  {calls.map((vc) => {
                    const outcomeColor = {
                      booked: '#1ef082',
                      booking_requested: '#1ea8ff',
                      no_answer: '#888',
                      not_interested: '#ff5050',
                      completed: '#1ef0a8',
                      escalated: '#ffb41e',
                      failed: '#ff5050',
                    }[vc.outcome] || '#888'
                    const outcomeLabel = {
                      booked: '📅 Booked',
                      booking_requested: '🔗 Booking link sent',
                      no_answer: '📵 No answer',
                      not_interested: '🚫 Not interested',
                      completed: '✅ Completed',
                      escalated: '🔔 Escalated',
                      failed: '⚠️ Failed',
                    }[vc.outcome] || vc.outcome || 'Unknown'
                    const startedLabel = vc.started_at
                      ? new Date(vc.started_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                      : new Date(vc.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                    const durationLabel = vc.duration_seconds
                      ? `${Math.floor(vc.duration_seconds / 60)}m ${vc.duration_seconds % 60}s`
                      : null

                    return (
                      <div key={vc.id} style={{
                        background: 'var(--surface-card, rgba(255,255,255,0.04))',
                        border: '1px solid var(--border-subtle)',
                        borderLeft: `3px solid ${outcomeColor}`,
                        borderRadius: 10,
                        padding: '14px 16px',
                      }}>
                        {/* Call header */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                              📞 Call #{vc.call_number || '?'}
                            </span>
                            <span style={{ fontSize: 11, fontWeight: 600, background: outcomeColor + '22', color: outcomeColor, border: `1px solid ${outcomeColor}44`, borderRadius: 20, padding: '2px 8px' }}>
                              {outcomeLabel}
                            </span>
                            {durationLabel && (
                              <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>⏱ {durationLabel}</span>
                            )}
                          </div>
                          <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{startedLabel}</span>
                        </div>

                        {/* Live call transcript */}
                        {vc.transcript && (
                          <div style={{ marginBottom: 10 }}>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                              📝 Call Transcript
                            </div>
                            <div style={{
                              fontSize: 12,
                              color: 'var(--text-primary)',
                              background: 'rgba(0,0,0,0.15)',
                              border: '1px solid var(--border-subtle)',
                              borderRadius: 8,
                              padding: '10px 14px',
                              lineHeight: 1.6,
                              whiteSpace: 'pre-wrap',
                              maxHeight: 280,
                              overflowY: 'auto',
                              fontFamily: 'var(--font-mono, monospace)',
                            }}>
                              {vc.transcript}
                            </div>
                          </div>
                        )}

                        {/* Voicemail transcript */}
                        {vc.voicemail_left && vc.voicemail_transcript && (
                          <div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                              📬 Voicemail Transcript
                            </div>
                            <div style={{
                              fontSize: 12,
                              color: 'var(--text-primary)',
                              background: 'rgba(255,180,30,0.06)',
                              border: '1px solid rgba(255,180,30,0.2)',
                              borderRadius: 8,
                              padding: '10px 14px',
                              lineHeight: 1.6,
                              whiteSpace: 'pre-wrap',
                              maxHeight: 180,
                              overflowY: 'auto',
                            }}>
                              {vc.voicemail_transcript}
                            </div>
                          </div>
                        )}

                        {/* No transcript available */}
                        {!vc.transcript && !(vc.voicemail_left && vc.voicemail_transcript) && (
                          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                            {vc.voicemail_left ? 'Voicemail left — no transcript available.' : 'No transcript for this call.'}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })()}

            {/* Tab: Full Timeline */}
            {activeTab === 'timeline' && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                    Complete activity history — newest first
                  </span>
                  <button
                    onClick={handleRefreshActivity}
                    disabled={activityLoading}
                    style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: '2px 6px' }}
                  >
                    {activityLoading ? '⏳' : '↺ Refresh'}
                  </button>
                </div>
                {activityError && (
                  <div style={{ fontSize: 12, color: 'var(--signal-red)', padding: '8px 12px', background: 'rgba(255,80,80,0.08)', borderRadius: 6, marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    ⚠️ {activityError}
                    <button onClick={handleRefreshActivity} style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>Retry</button>
                  </div>
                )}
                {activityLoading && !activity && (
                  <div className="empty-state" style={{ padding: '20px 0' }}>Loading history…</div>
                )}
                {!activityLoading && !activityError && activity && activity.events.length === 0 && (
                  <div className="empty-state" style={{ padding: '12px 0' }}>No activity recorded yet.</div>
                )}
                {activity && activity.events.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: '500px', overflowY: 'auto', paddingRight: 2 }}>
                    {[...activity.events].reverse().map((ev) => {
                      const typeStyles = {
                        lead_created:      { bg: 'rgba(100,100,255,0.08)', border: 'rgba(100,100,255,0.25)', icon: '👤', color: '#6464ff' },
                        sms_sent:          { bg: 'rgba(30,200,168,0.07)', border: 'rgba(30,200,168,0.25)', icon: '📤', color: '#1ec8a8' },
                        sms_reply:         { bg: ev.meta?.is_hot ? 'rgba(255,80,80,0.10)' : 'rgba(255,200,30,0.08)', border: ev.meta?.is_hot ? 'rgba(255,80,80,0.4)' : 'rgba(255,200,30,0.30)', icon: ev.meta?.is_hot ? '🔥' : '💬', color: ev.meta?.is_hot ? '#ff5050' : '#c8a020' },
                        email_sent:        { bg: 'rgba(80,160,255,0.08)', border: 'rgba(80,160,255,0.25)', icon: '📧', color: '#50a0ff' },
                        booking_booked:    { bg: 'rgba(30,240,130,0.10)', border: 'rgba(30,240,130,0.35)', icon: '📅', color: '#1ef082' },
                        booking_confirmed: { bg: 'rgba(30,240,130,0.10)', border: 'rgba(30,240,130,0.35)', icon: '✅', color: '#1ef082' },
                        booking_expired:   { bg: 'rgba(180,180,180,0.08)', border: 'rgba(180,180,180,0.25)', icon: '⏰', color: '#999' },
                        booking_pending:   { bg: 'rgba(255,180,30,0.08)', border: 'rgba(255,180,30,0.25)', icon: '🔗', color: '#ffb41e' },
                        booking_cancelled: { bg: 'rgba(255,80,80,0.08)', border: 'rgba(255,80,80,0.25)', icon: '❌', color: '#ff5050' },
                        outcome_recorded:  { bg: 'rgba(140,80,255,0.08)', border: 'rgba(140,80,255,0.25)', icon: '📝', color: '#8c50ff' },
                        cadence_started:   { bg: 'rgba(30,168,255,0.07)', border: 'rgba(30,168,255,0.2)', icon: '🤖', color: '#1ea8ff' },
                        cadence_completed: { bg: 'rgba(30,240,130,0.08)', border: 'rgba(30,240,130,0.25)', icon: '🏁', color: '#1ef082' },
                        dnc_flagged:       { bg: 'rgba(255,30,30,0.08)', border: 'rgba(255,30,30,0.30)', icon: '⛔', color: '#ff1e1e' },
                      }
                      const s = typeStyles[ev.type] || { bg: 'rgba(128,128,128,0.06)', border: 'rgba(128,128,128,0.18)', icon: '•', color: 'var(--text-secondary)' }
                      const ts = ev.ts ? new Date(ev.ts).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
                      return (
                        <div key={ev.id} style={{
                          background: s.bg,
                          border: `1px solid ${s.border}`,
                          borderLeft: `3px solid ${s.color}`,
                          borderRadius: 8,
                          padding: '10px 14px',
                        }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                            <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
                              {s.icon} {ev.label}
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap', flexShrink: 0 }}>{ts}</div>
                          </div>
                          {ev.body && (
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 5, lineHeight: 1.45 }}>
                              {ev.body.length > 200 ? ev.body.slice(0, 200) + '…' : ev.body}
                            </div>
                          )}
                          {ev.meta?.hot_reason && (
                            <div style={{ fontSize: 11, color: s.color, marginTop: 4, fontStyle: 'italic' }}>
                              {ev.meta.hot_reason}
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
                <label style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap', fontWeight: 600 }}
                  title="Sets the appointment type on the booking link — what the lead sees on the booking page">
                  📅 Booking type
                </label>
                <select
                  className="filter-select"
                  style={{ flex: 1, fontSize: 12 }}
                  value={apptLabel}
                  onChange={(e) => setApptLabel(e.target.value)}
                >
                  {apptTypeOptions.map((opt) => (
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
                {/* Hidden file input for MMS media */}
                <input
                  ref={mediaInputRef}
                  type="file"
                  accept=".jpg,.jpeg,.png,.gif,.pdf"
                  style={{ display: 'none' }}
                  onChange={handleMediaUpload}
                />
                {/* Media preview strip */}
                {mediaUrl && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    background: 'var(--signal-blue-dim, #e3f2fd)',
                    border: '1px solid var(--signal-blue, #1565c0)',
                    borderRadius: 6, padding: '6px 10px', fontSize: 12,
                    color: 'var(--signal-blue, #1565c0)', marginBottom: 4,
                  }}>
                    <span>📎</span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{mediaFileName}</span>
                    <span style={{ opacity: 0.6, fontSize: 11 }}>Will send as MMS</span>
                    <button
                      onClick={handleRemoveMedia}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, padding: 0, color: 'inherit', opacity: 0.7 }}
                      title="Remove attachment"
                    >✕</button>
                  </div>
                )}
                {mediaError && <div style={{ color: 'var(--signal-red)', fontSize: 12, marginBottom: 4 }}>{mediaError}</div>}
                {/* WHAT WILL BE SENT. Shown whenever a link is being added, so
                    the advisor sees the branded URL before pressing Send rather
                    than discovering it in the delivered message. */}
                {includeBookingLink && (bookingUrl || bookingUrlReason) && (
                  <div style={bookingUrl ? SX.previewOk : SX.previewWarn}>
                    <div style={SX.previewLabel}>
                      {bookingUrl ? 'Will be sent as' : 'Booking link unavailable'}
                    </div>
                    {bookingUrl ? (
                      <>
                        <div style={SX.previewBody}>{smsPreview || bookingUrl}</div>
                        <div style={SX.previewMeta}>
                          {smsPreview.length} characters ·{' '}
                          {smsPreview.length <= 160 ? '1 segment' : `${Math.ceil(smsPreview.length / 153)} segments`}
                        </div>
                      </>
                    ) : (
                      <div style={SX.previewBody}>{bookingUrlReason}</div>
                    )}
                  </div>
                )}

                {/* SENDER READINESS. The composer knows before Send whether a
                    Twilio sender resolves, and from where. This warning is
                    never hidden - an advisor who cannot send should know why
                    while writing, not after pressing the button. */}
                {smsSender && !smsSender.ready && (
                  <div style={SX.senderWarn}>{smsSender.reason}</div>
                )}
                {smsSender && smsSender.ready && smsSender.from_number && (
                  <div style={SX.senderOk}>
                    Sending from {formatPhone(smsSender.from_number)}
                    {smsSender.source === 'organization' ? ' (organization sender)' : ''}
                  </div>
                )}

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
                    className="btn btn--ghost"
                    onClick={() => mediaInputRef.current?.click()}
                    disabled={mediaUploading}
                    title="Attach flyer or image (MMS)"
                    style={{ padding: '6px 10px', fontSize: 13 }}
                  >
                    {mediaUploading ? '⏳' : '📎 Flyer'}
                  </button>
                  <button
                    className="btn btn--primary"
                    onClick={handleSend}
                    disabled={sending || !messageText.trim()}
                  >
                    {sending ? 'Sending…' : mediaUrl ? 'Send MMS 📎' : 'Send SMS'}
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
                {/* Attachment picker */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                  <input
                    ref={emailAttachRef}
                    type="file"
                    accept="image/*,.pdf,.doc,.docx"
                    style={{ display: 'none' }}
                    onChange={(e) => setEmailAttachment(e.target.files?.[0] || null)}
                  />
                  <button
                    className="btn btn--secondary"
                    style={{ fontSize: 12, padding: '4px 10px' }}
                    onClick={() => emailAttachRef.current?.click()}
                    type="button"
                  >
                    📎 {emailAttachment ? 'Change file' : 'Attach file'}
                  </button>
                  {emailAttachment && (
                    <>
                      <span style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {emailAttachment.name}
                      </span>
                      <button
                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--signal-red)', lineHeight: 1, padding: 0 }}
                        onClick={() => { setEmailAttachment(null); if (emailAttachRef.current) emailAttachRef.current.value = '' }}
                        title="Remove attachment"
                      >✕</button>
                    </>
                  )}
                </div>
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
                    {sendingEmail ? 'Sending…' : emailAttachment ? '📎 Send with attachment' : 'Send email'}
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
                {/* Channels this lead can actually be reached on. Email used to
                    be offered - and preselected - for a lead with no email
                    address, so the only way to discover it was to press Start
                    and read "Lead has no email address". Now an unavailable
                    channel is disabled and says why. */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                  {[
                    ['email', '✉️ Email', canSendEmail, emailBlockedReason],
                    ['sms',   '💬 SMS',   canSendSMS,   smsBlockedReason],
                    ['both',  '⚡ Both',  canSendBoth,  ch?.both?.reason],
                  ].map(([key, label, available, why]) => (
                    <button
                      key={key}
                      onClick={() => available && setAiConvChannel(key)}
                      disabled={!available}
                      title={available ? undefined : (why || 'Not available for this lead')}
                      style={{
                        padding: '6px 14px', borderRadius: 20, border: '1px solid',
                        fontSize: 12, fontWeight: 600,
                        cursor: available ? 'pointer' : 'not-allowed',
                        opacity: available ? 1 : 0.42,
                        borderColor: effectiveAiChannel === key ? 'var(--accent)' : 'var(--border-subtle)',
                        background: effectiveAiChannel === key ? 'var(--accent)' : 'transparent',
                        color: effectiveAiChannel === key ? '#fff' : 'var(--text-secondary)',
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {!canSendEmail && emailBlockedReason && (
                  <div style={SX.channelNote}>Email: {emailBlockedReason}</div>
                )}
                {!canSendSMS && smsBlockedReason && (
                  <div style={SX.channelNote}>SMS: {smsBlockedReason}</div>
                )}
                <button
                  className="btn btn--primary"
                  style={{ width: '100%', fontSize: 14, padding: '12px', marginTop: 12 }}
                  onClick={handleStartAiConversation}
                  disabled={aiConvLoading || !effectiveAiChannel}
                >
                  {aiConvLoading ? '⏳ Starting…' : '🤖 Start AI Conversation'}
                </button>
                <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8, textAlign: 'center' }}>
                  {effectiveAiChannel
                    ? 'AI runs a multi-touch sequence over 14 days, responds to replies, and pauses on escalation.'
                    : 'This lead cannot be reached on any channel right now.'}
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
                  ✅ Call placed — call #{callResult.call_number} to {callResult.lead_name}
                  {callResult.from_phone ? ` from ${formatPhone(callResult.from_phone)}` : ''}
                </div>
              )}
              {/* The real reason, kept on the page. It used to be an alert()
                  that said "Unable to reach the server" for every failure,
                  including ones the server had answered clearly. */}
              {callError && (
                <div style={SX.senderWarn}>{callError}</div>
              )}
              {!canVoice && voiceBlockedReason && !callError && (
                <div style={SX.senderWarn}>{voiceBlockedReason}</div>
              )}
              <button
                className="btn btn--primary"
                style={{ width: '100%', fontSize: 14, padding: '12px' }}
                onClick={handleCall}
                disabled={calling || !canVoice}
                title={canVoice ? undefined : (voiceBlockedReason || undefined)}
              >
                {calling ? '⏳ Calling…' : '📞 Call with AI'}
              </button>
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8, textAlign: 'center' }}>
                The AI agent calls, discloses that it is an AI, and books if they say yes.
                Recorded. Maximum 3 attempts.
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
              {booking.status === 'cancelled' && (
                <p className="lead-detail-info-text" style={{ color: 'var(--text-secondary)' }}>
                  Booking was cancelled. Send a fresh link to reschedule.
                </p>
              )}

              {/* Resend link — show for pending, cancelled, or expired states */}
              {booking.status !== 'booked' && (
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <button
                    className="btn btn--primary"
                    style={{ width: '100%', fontSize: 13 }}
                    onClick={handleResendBookingLink}
                    disabled={resendingLink}
                  >
                    {resendingLink ? 'Sending…' : '🔗 Resend Booking Link'}
                  </button>
                  {resendLinkMsg && (
                    <p style={{
                      fontSize: 12,
                      color: resendLinkMsg.ok ? 'var(--color-success, #22c55e)' : 'var(--color-danger, #ef4444)',
                      margin: 0,
                    }}>
                      {resendLinkMsg.ok ? '✓ ' : '✗ '}{resendLinkMsg.text}
                    </p>
                  )}
                </div>
              )}

              {booking.status === 'booked' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <button
                    className="btn btn--primary"
                    style={{ width: '100%', fontSize: 13 }}
                    onClick={() => setShowCaseFile(true)}
                  >
                    📋 Open Client Record
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
          {!booking && lead.email && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">🔗 Booking Link</h2>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                No booking link sent yet. Send one directly to this lead's inbox.
              </p>
              <button
                className="btn btn--primary"
                style={{ width: '100%', fontSize: 13 }}
                onClick={handleResendBookingLink}
                disabled={resendingLink}
              >
                {resendingLink ? 'Sending…' : '🔗 Send Booking Link'}
              </button>
              {resendLinkMsg && (
                <p style={{
                  fontSize: 12,
                  color: resendLinkMsg.ok ? 'var(--color-success, #22c55e)' : 'var(--color-danger, #ef4444)',
                  marginTop: 8, marginBottom: 0,
                }}>
                  {resendLinkMsg.ok ? '✓ ' : '✗ '}{resendLinkMsg.text}
                </p>
              )}
            </section>
          )}

          {!booking && (
            <section className="panel lead-detail-panel">
              <div className="panel-header">
                <h2 className="panel-title">📋 Client Record</h2>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
                Record appointment outcomes, products discussed, and next steps for this client.
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
