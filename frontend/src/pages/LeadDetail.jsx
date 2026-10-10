import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { workspaceRole } from '../auth/workspaceAuthority'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import OutcomeTracker from '../components/OutcomeTracker'
import CaseFile from './CaseFile'
import { useToast } from '../components/Toast'
import { formatPhone } from '../utils/phone'
import { leadDetailTabFromSearch } from '../utils/leadDetailTabs'
import { confirmLeadDelete, deleteLeadIds, deleteSummary } from '../utils/deleteRecords'
import { useTerminology } from '../terminology'
import HumanDialerPanel from '../components/telephony/HumanDialerPanel'
import ConversationBrain from '../components/ConversationBrain'
import '../styles/shared.css'
import './LeadDetail.css'
import './LeadCommandCenter.css'

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

// ── APPOINTMENT LABELS AND SUBJECT LINES ──────────────────────────────────
//
// WHAT WAS HERE, AND WHY IT MATTERED MORE THAN A LABEL. A forty-entry funeral
// appointment taxonomy, a twenty-line funeral fallback list, and - the part
// that actually reached people - a subject-line generator whose file-check and
// property branches returned "Your family file at <a real cemetery customer's
// name>" and "Your property at <the same name>", typed in as literals.
//
// `smartSubject` is not a hint. It is the DEFAULT SUBJECT of the email this
// page sends: an advisor who does not retype the field sends it. So every
// tenant of this platform, in every industry, emailed their own prospects
// naming ANOTHER CUSTOMER'S BUSINESS, and the rest named a funeral home's
// products to people buying electricity.
//
// The organization's own appointment types are already fetched from
// `/settings/appointment-types` and already override the list below; what was
// missing was that the defaults, the auto-detection and the subject lines all
// had one vertical baked in. They now resolve from the organization's
// configuration, and the local fallbacks carry nobody's vertical.

// The pre-fetch fallback only. Neutral on purpose: whatever renders before the
// organization's own list arrives must not be another business's vocabulary.
const DEFAULT_APPT_TYPE_OPTIONS = [
  'General Consultation',
  'Discovery Call',
  'Follow-Up Appointment',
  'Phone Call',
  'Video Call',
  'Referral Appointment',
]

// Words in a tier or track that suggest which of the ORGANIZATION'S OWN
// appointment types to preselect. Matching is done against that fetched list,
// so a funeral home still lands on its arrangement conference and an energy
// business on its rate review - because those are the types each of them
// configured, not because either is named here.
const APPT_HINTS = [
  ['renewal', 'renew'],
  ['contract', 'contract'],
  ['proposal', 'proposal'],
  ['rate', 'rate'],
  ['estimate', 'estimate'],
  ['inspection', 'inspect'],
  ['install', 'install'],
  ['review', 'review'],
  ['consultation', 'consult'],
  ['referral', 'referral'],
  ['urgent', 'urgent'],
  ['imminent', 'immediate'],
]

/**
 * Which of this organization's appointment types fits this lead.
 *
 * `options` is the org's own list. A tier or track whose words appear in one of
 * its types selects that type; otherwise the first type the organization
 * configured, which is its own starting point rather than anybody else's.
 */
function detectApptLabel(tier, messageTrack, contactChannel, options) {
  const list = (options && options.length) ? options : DEFAULT_APPT_TYPE_OPTIONS
  const fields = [messageTrack, tier, contactChannel]
    .filter(Boolean).map(f => String(f).toLowerCase().replace(/[_-]+/g, ' '))

  for (const field of fields) {
    const direct = list.find(o => o.toLowerCase() === field)
    if (direct) return direct
  }
  for (const field of fields) {
    for (const word of field.split(/\s+/).filter(w => w.length > 3)) {
      const hit = list.find(o => o.toLowerCase().includes(word))
      if (hit) return hit
    }
    for (const [needle, inType] of APPT_HINTS) {
      if (!field.includes(needle)) continue
      const hit = list.find(o => o.toLowerCase().includes(inType))
      if (hit) return hit
    }
  }
  return list[0]
}

/**
 * A default subject line that names THIS business, or nothing at all.
 *
 * The organization's own name comes from the caller; when it is not known yet
 * the subject simply omits it rather than substituting somebody else's. No
 * vertical's products are named: a subject a seller has not read should be
 * bland, not wrong.
 */
function smartSubject(firstName, tier, messageTrack, orgName) {
  const name = firstName ? `, ${firstName}` : ''
  const at = orgName ? ` at ${orgName}` : ''
  const track = (messageTrack || '').toLowerCase()
  const t = (tier || '').toLowerCase()
  const says = (...words) => words.some(w => track.includes(w) || t.includes(w))

  if (says('renewal', 'renew')) return `Your renewal is coming up${name}`
  if (says('proposal', 'quote', 'quoted', 'estimate')) {
    return `Following up on your quote${name}`
  }
  if (says('contract')) return `About your contract${at}${name}`
  if (says('referral')) return `Someone thought of you${name}`
  if (says('urgent', 'imminent')) return `We're ready to help${name}`
  if (says('file_check', 'file check', 'file_review', 'code_lead', 'code lead')) {
    return `Your file${at}${name}`
  }
  if (says('insurance', 'benefits')) return `Your benefits review${name}`
  return `Checking in${name}`
}

// The API sends UTC timestamps without a zone ("2026-09-29T19:41:34"), which a
// browser reads as LOCAL time - in Central that put every reply 5 hours in the
// future and it always read "Just now". A string without Z/offset is UTC.
function asUtc(dateStr) {
  if (typeof dateStr !== 'string') return dateStr
  return /[zZ]|[+-]\d\d:?\d\d$/.test(dateStr) || !/T\d/.test(dateStr) ? dateStr : dateStr + 'Z'
}

function timeAgo(dateStr) {
  if (!dateStr) return ''
  dateStr = asUtc(dateStr)
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

// ── TIMELINE PAGING (SS5) ────────────────────────────────────────────────────
//
// /leads/{id}/timeline returns the newest page (up to `limit` rows per channel)
// plus has_more / next_before. Older pages are fetched with `before` and merged
// in; timeline events carry no id, so they are deduplicated on a composite key.
function timelineEventKey(e) {
  if (!e) return ''
  if (e.id != null) return `id|${e.channel || ''}|${e.id}`
  return [
    e.type || '', e.channel || '', e.timestamp || '',
    (e.subject || '').slice(0, 60), (e.body || e.body_preview || '').slice(0, 60),
  ].join('|')
}

function timelineTs(t) {
  if (!t) return null
  const n = Date.parse(t)
  return Number.isNaN(n) ? null : n
}

function mergeTimelineEvents(...lists) {
  const seen = new Set()
  const out = []
  for (const list of lists) {
    for (const e of list || []) {
      const k = timelineEventKey(e)
      if (seen.has(k)) continue
      seen.add(k)
      out.push(e)
    }
  }
  // Same order as the backend: oldest first, undated last.
  return out.sort((a, b) => {
    const ta = timelineTs(a.timestamp)
    const tb = timelineTs(b.timestamp)
    if (ta === null && tb === null) return 0
    if (ta === null) return 1
    if (tb === null) return -1
    return ta - tb
  })
}

// The cursor for the page OLDER than `page`. The backend's next_before is the
// oldest timestamp across every channel, but only the channels that filled a
// whole page are truncated - a quiet channel reaching further back would make
// that cursor skip rows of a busy one. So the safe cursor is the NEWEST of the
// full channels' oldest timestamps; anything older that this page already
// returned comes back again and is removed by the dedupe above.
function olderTimelineCursor(page) {
  if (!page || !page.has_more) return { hasMore: false, before: null }
  const limit = page.limit || 200
  const groups = { 'outbound|sms': [], 'inbound|sms': [], 'outbound|email': [] }
  for (const e of page.events || []) {
    const g = groups[`${e.type}|${e.channel}`]
    if (g && e.timestamp) g.push(e.timestamp)
  }
  let best = null
  for (const stamps of Object.values(groups)) {
    if (stamps.length < limit) continue
    let oldest = null
    for (const t of stamps) {
      if (oldest === null || timelineTs(t) < timelineTs(oldest)) oldest = t
    }
    if (oldest !== null && (best === null || timelineTs(oldest) > timelineTs(best))) best = oldest
  }
  const before = best || page.next_before || null
  return { hasMore: Boolean(before), before }
}

// ── CADENCE HISTORY (SS6) ────────────────────────────────────────────────────
const CADENCE_OUTCOMES = {
  sent:       { label: 'Sent',       color: 'var(--signal-green, #1ef082)' },
  failed:     { label: 'Failed',     color: 'var(--signal-red, #ff5050)' },
  blocked:    { label: 'Blocked',    color: 'var(--signal-amber, #ffb41e)' },
  skipped:    { label: 'Skipped',    color: 'var(--text-tertiary, #888)' },
  stopped:    { label: 'Stopped',    color: 'var(--text-tertiary, #888)' },
  abandoned:  { label: 'Abandoned',  color: 'var(--signal-red, #ff5050)' },
  suppressed: { label: 'Suppressed', color: 'var(--signal-amber, #ffb41e)' },
}

function groupCadenceTouches(history) {
  const byTouch = new Map()
  for (const a of history || []) {
    const n = a.touch_number ?? 0
    if (!byTouch.has(n)) byTouch.set(n, [])
    byTouch.get(n).push(a)
  }
  return [...byTouch.entries()].sort((x, y) => x[0] - y[0])
}

function CadencePanel({ cadence, loading }) {
  if (loading && !cadence) {
    return <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Loading cadence…</div>
  }
  if (!cadence || cadence.unavailable) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
        {cadence?.notFound || !cadence ? 'Not enrolled in a cadence.' : (cadence.error || 'Cadence history unavailable.')}
      </div>
    )
  }
  const history = cadence.history || []
  if (!cadence.status && history.length === 0) {
    return <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Not enrolled in a cadence.</div>
  }
  const total = cadence.total_touches
  const current = cadence.current_touch_number || 0
  const touches = groupCadenceTouches(history)
  return (
    <div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>
        Touch {current}{total ? ` of ${total}` : ''}
        {cadence.status && <span style={{ color: 'var(--text-tertiary)' }}> · {cadence.status}</span>}
        {cadence.next_touch_due_at && (
          <span style={{ color: 'var(--text-tertiary)', display: 'block', fontSize: 11, marginTop: 2 }}>
            Next touch due: {new Date(cadence.next_touch_due_at).toLocaleString()}
          </span>
        )}
      </div>
      {touches.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>No touches attempted yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 320, overflowY: 'auto' }}>
          {touches.map(([touchNumber, attempts]) => (
            <div key={touchNumber} style={{ border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '6px 10px' }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Touch {touchNumber}{total ? ` of ${total}` : ''}
              </div>
              {attempts.map((a, idx) => {
                const oc = CADENCE_OUTCOMES[a.outcome] || { label: a.outcome || 'Unknown', color: 'var(--text-tertiary, #888)' }
                const when = a.attempted_at || a.scheduled_for
                const providerErr = [a.provider_error_code, a.provider_error_message].filter(Boolean).join(': ')
                return (
                  <div key={`${touchNumber}-${a.attempt ?? idx}`} style={{ fontSize: 12, marginTop: idx ? 4 : 0 }}>
                    <span style={{ fontWeight: 600, color: oc.color }}>{oc.label}</span>
                    {a.channel && <span style={{ color: 'var(--text-secondary)' }}> · {a.channel}</span>}
                    {when && <span style={{ color: 'var(--text-tertiary)' }}> · {new Date(when).toLocaleString()}</span>}
                    {a.attempt > 1 && <span style={{ color: 'var(--text-tertiary)' }}> · attempt {a.attempt}</span>}
                    {a.reason && <div style={{ color: 'var(--text-secondary)' }}>Reason: {a.reason}</div>}
                    {providerErr && <div style={{ color: 'var(--signal-red)' }}>Provider: {providerErr}</div>}
                    {a.body_preview && (
                      <div style={{ color: 'var(--text-tertiary)', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.body_preview}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ConversationBubble is a proper sub-component (not inline in .map)
// so useState hooks are always called at the top level — no rules-of-hooks violations.
//
// Every message says which way it went and on which channel, in words: an
// advisor must never have to infer direction from which side a bubble sits on.
const CHANNEL_WORD = { sms: 'text', email: 'email', cadence: 'cadence', voice: 'call' }

function fullWhen(dateStr) {
  if (!dateStr) return ''
  const d = new Date(asUtc(dateStr))
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function ConversationBubble({ event: e }) {
  const [expanded, setExpanded] = useState(false)

  // Prefer body; fall back to body_preview for email messages
  const rawText = (e.body || e.body_preview || '').trim()
  const THRESHOLD = 240
  const isLong = rawText.length > THRESHOLD
  const displayText = isLong && !expanded ? rawText.slice(0, THRESHOLD) + '…' : rawText
  const dir = e.type === 'inbound' ? 'Inbound' : e.type === 'outbound' ? 'Outbound' : 'System'
  const chan = CHANNEL_WORD[e.channel] || e.channel || ''

  return (
    <div className={`lcc-msg lcc-msg--${e.type || 'system'}${e.channel === 'email' ? ' lcc-msg--email' : ''}`}>
      <div className="lcc-msg-meta">
        <span className="lcc-msg-dir">{dir}{chan ? ` ${chan}` : ''}</span>
        {e.type === 'inbound' && e.is_hot && <span className="lcc-chip lcc-chip--red">Hot reply</span>}
        <time dateTime={asUtc(e.timestamp) || undefined} title={fullWhen(e.timestamp)}>{timeAgo(e.timestamp)}</time>
      </div>
      {e.subject && <div className="lcc-msg-subject">{e.subject}</div>}
      {rawText ? (
        <p className="lcc-msg-text">{displayText}</p>
      ) : (
        <p className="lcc-msg-text lcc-muted"><em>{e.subject ? '(email — no body preview)' : '(no message body)'}</em></p>
      )}
      {e.type === 'outbound' && <DeliveryChip delivery={e.delivery} />}
      {isLong && (
        <button type="button" className="lcc-linkbtn" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}

export default function LeadDetail() {
  const { leadId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [deletingLead, setDeletingLead] = useState(false)
  const [deleteLeadErr, setDeleteLeadErr] = useState('')
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
  const canReassignLead = workspaceRole(currentUser) === 'org_admin' || workspaceRole(currentUser) === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignmentSaving, setAssignmentSaving] = useState(false)
  const [assignmentError, setAssignmentError] = useState('')
  const [showCaseFile, setShowCaseFile] = useState(false)
  const [activity, setActivity] = useState(null)
  const [activityLoading, setActivityLoading] = useState(false)
  const [activityError, setActivityError] = useState('')
  // Command center: Conversation Brain's state (owner, next best action), the
  // dialer's call + voicemail history, and view-only filters.
  const [brainCtx, setBrainCtx] = useState(null)
  const [dialerHist, setDialerHist] = useState(null)
  const [convFilter, setConvFilter] = useState('all')
  const [historyQuery, setHistoryQuery] = useState('')
  const [activityKind, setActivityKind] = useState('all')
  const [menu, setMenu] = useState(null)
  const [showDiag, setShowDiag] = useState(false)
  const composerRef = useRef(null)
  useEffect(() => {
    if (!menu) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setMenu(null) }
    const onDown = (e) => { if (!e.target.closest || !e.target.closest('.lcc-menu-wrap')) setMenu(null) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown) }
  }, [menu])
  const location = useLocation()
  // ?tab=timeline|calls|conversation opens that tab (the Sales Board links to ?tab=timeline).
  const [activeTab, setActiveTab] = useState(() => leadDetailTabFromSearch(location.search)) // 'conversation' | 'calls' | 'timeline'
  useEffect(() => {
    setActiveTab(leadDetailTabFromSearch(location.search))
  }, [location.search, leadId])
  // SS5: older timeline pages, kept apart from `data` so the 30s refresh of the
  // newest page never discards what the advisor has scrolled back through.
  const [olderEvents, setOlderEvents] = useState([])
  const [olderCursorState, setOlderCursorState] = useState(null) // null = derive from newest page
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [olderError, setOlderError] = useState('')
  // SS6: per-touch cadence attempts from /cadence/lead/{id}/history
  const [cadenceHistory, setCadenceHistory] = useState(null)
  const [cadenceLoading, setCadenceLoading] = useState(false)
  const [apptTypeOptions, setApptTypeOptions] = useState(DEFAULT_APPT_TYPE_OPTIONS)
  // The timeline load preselects an appointment label and can finish before or
  // after the org's own type list arrives, so the list is read through a ref
  // rather than captured in that closure.
  const apptTypeOptionsRef = useRef(DEFAULT_APPT_TYPE_OPTIONS)
  useEffect(() => { apptTypeOptionsRef.current = apptTypeOptions }, [apptTypeOptions])
  // This organization's own name, for the default email subject.
  const terminology = useTerminology()
  const orgName = terminology.orgName
  // Funeral-only panels and examples stay with funeral workspaces. An energy
  // workspace was shown "Funeral arrangement / Cemetery property / Marker".
  const isFuneral = terminology.industry === 'funeral'
  const aiDirectionHint = isFuneral
    ? 'AI direction: e.g. file check — ask if they still need planning'
    : terminology.industry === 'energy'
      ? 'AI direction: e.g. ask when their current contract ends and offer a rate review'
      : 'AI direction: e.g. what this message should focus on'
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
          prev || detectApptLabel(d?.lead?.tier, d?.lead?.message_track,
                                  d?.lead?.contact_channel, apptTypeOptionsRef.current)
        )
      })
      .catch((err) => {
        console.error('LeadDetail load error:', err)
        setSendError(err.message || 'Failed to load lead')
      })
      .finally(() => setLoading(false))
    // Calls + voicemails for the summary strip (the Calls tab refreshes it).
    api.get(`/dialer/leads/${leadId}/history`)
      .then(h => setDialerHist(h))
      .catch(() => setDialerHist({ calls: [], voicemails: [], unavailable: true }))
    // Also load activity log in background
    loadActivity(true)
    loadCadenceHistory()
  }

  function loadCadenceHistory() {
    setCadenceLoading(true)
    api.get(`/cadence/lead/${leadId}/history`)
      .then((c) => setCadenceHistory(c))
      .catch((err) => setCadenceHistory({
        unavailable: true,
        notFound: err?.status === 404,
        error: err?.message || '',
      }))
      .finally(() => setCadenceLoading(false))
  }

  async function loadOlderActivity() {
    const cursor = olderCursorState || olderTimelineCursor(data)
    if (!cursor.hasMore || !cursor.before || loadingOlder) return
    setLoadingOlder(true)
    setOlderError('')
    try {
      const page = await api.get(`/leads/${leadId}/timeline`, { params: { before: cursor.before } })
      setOlderEvents((prev) => mergeTimelineEvents(prev, page?.events || []))
      setOlderCursorState(olderTimelineCursor(page))
    } catch (err) {
      setOlderError(err?.message || 'Could not load older activity')
    } finally {
      setLoadingOlder(false)
    }
  }

  // Load org-specific appointment types once on mount
  useEffect(() => {
    api.get('/settings/appointment-types')
      .then(d => { if (d?.appointment_types?.length) setApptTypeOptions(d.appointment_types) })
      .catch(() => {}) // silently fall back to defaults
  }, [])

  // Initial load
  useEffect(() => {
    setOlderEvents([])
    setOlderCursorState(null)
    setOlderError('')
    setCadenceHistory(null)
    load()
  }, [leadId])

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
        booking_type: apptLabel || null,
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
        booking_type: apptLabel || null,
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
        smartSubject(lead?.first_name, lead?.tier, lead?.message_track, orgName)
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
    const subject = emailSubject || smartSubject(lead?.first_name, lead?.tier, lead?.message_track, orgName)
    const send = (allowDuplicate) => {
      if (emailAttachment) {
        // Use multipart endpoint when an attachment is present
        const formData = new FormData()
        formData.append('subject', subject)
        formData.append('body_html', emailBody)
        formData.append('include_booking_link', includeBookingLink ? 'true' : 'false')
        if (apptLabel) formData.append('appt_label', apptLabel)
        if (allowDuplicate) formData.append('allow_duplicate', 'true')
        formData.append('file', emailAttachment)
        return api.upload(`/email/send-with-attachment/${leadId}`, formData)
      }
      return api.post(`/email/send/${leadId}`, {
        subject,
        body: emailBody,
        include_booking_link: includeBookingLink,
        appt_label: apptLabel,
        allow_duplicate: !!allowDuplicate,
      })
    }
    try {
      let res
      try {
        res = await send(false)
      } catch (err) {
        // The server refuses an identical email sent minutes ago - almost
        // always a retry. Only a person's explicit yes sends it again.
        const d = err && err.detail
        if (err && err.status === 409 && d && d.code === 'duplicate_send') {
          if (!window.confirm(d.message || 'This email was already sent. Send it again anyway?')) {
            setSendError('Not sent again. The earlier email is already on its way.')
            return
          }
          res = await send(true)
        } else {
          throw err
        }
      }
      setEmailAttachment(null)
      if (emailAttachRef.current) emailAttachRef.current.value = ''
      setEmailSubject('')
      setEmailBody('')
      setEmailDraftReady(false)
      if (res && res.recorded === false) {
        setSendError(res.warning || 'The email was sent, but it could not be saved to this lead\'s history. Do not send it again.')
      } else {
        toast.success('Email sent.')
      }
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

  if (loading && !data) return <div className="empty-state" style={{ marginTop: 40 }} role="status">Loading contact…</div>
  if (!data) return (
    <div className="empty-state" style={{ marginTop: 40 }}>
      <div>Couldn't load this lead.</div>
      {sendError && <div style={{ fontSize: 13, color: 'var(--signal-red)', marginTop: 8 }}>{sendError}</div>}
      <button className="btn btn--secondary" style={{ marginTop: 16 }} onClick={load}>Try again</button>
    </div>
  )

  const { lead, ai_quality, booking } = data
  const events = olderEvents.length
    ? mergeTimelineEvents(olderEvents, data.events || [])
    : (data.events || [])
  const olderCursor = olderCursorState || olderTimelineCursor(data)
  // Stable keys: index keys would shift every bubble's expanded state when
  // older pages are prepended.
  const eventKeyCounts = {}
  const eventKeys = events.map((e) => {
    const k = timelineEventKey(e)
    eventKeyCounts[k] = (eventKeyCounts[k] || 0) + 1
    return `${k}#${eventKeyCounts[k]}`
  })
  const wholesaleLinks = composeCtx?.wholesale || []

  // ── INTERNAL TEST RECORD ──────────────────────────────────────────────────
  //
  // A test record (Lead.is_test, app/services/test_records.py) gets NO outbound
  // control on this screen: text, email, click-to-call, AI voice, AI
  // conversation, cadence and booking links are all switched off here, with one
  // reason, before anyone presses anything. The server's own gates still apply
  // underneath; this is so no button is offered that should not be pressed.
  const isTest = Boolean(lead.is_test)
  const TEST_REASON = 'Internal test record — excluded from all outreach.'

  // ── CHANNEL CAPABILITY ────────────────────────────────────────────────────
  //
  // Each channel depends only on what THAT channel needs. A lead with a phone
  // and no email can be texted and called; the missing email is a reason to
  // withhold Email and nothing else. The backend decides (compose_router), and
  // these locals fall back to the lead record when that read failed, so the
  // page still works if the endpoint is unreachable.
  const ch = composeCtx?.channels
  const notBlocked = lead.status !== 'dnc' && !lead.is_duplicate
  const canSendSMS   = !isTest && (ch ? ch.sms.available   : Boolean(lead.phone && notBlocked))
  const canSendEmail = !isTest && (ch ? ch.email.available : Boolean(lead.email && notBlocked))
  const canSendBoth  = !isTest && (ch ? ch.both.available  : (canSendSMS && canSendEmail))
  const canVoice     = !isTest && (ch ? ch.voice.available
                          : Boolean(lead.phone && notBlocked))
  const smsBlockedReason   = isTest ? TEST_REASON : (ch ? ch.sms.reason : null)
  const emailBlockedReason = isTest ? TEST_REASON : (ch ? ch.email.reason : 'This lead has no email address.')
  const voiceBlockedReason = isTest ? TEST_REASON : (voiceReadiness && !voiceReadiness.ready)
    ? voiceReadiness.reason
    : (ch ? ch.voice.reason : null)
  const smsSender = composeCtx?.sms_sender || null
  // Email is now the ONLY channel carrying the booking link, so whether we
  // can actually send one belongs on screen beside the button - not
  // discovered by pressing it.
  const emailSender = composeCtx?.email_sender || null
  // SMS carries no URL under the current campaign - see the backend's
  // sms_content_policy. The composer must reflect that BEFORE the advisor
  // writes, not strip their text afterwards: no link is offered, and the
  // reason is stated where the link preview used to be.
  const smsLinksAllowed = composeCtx?.sms_content_policy?.links_allowed !== false
  const smsPolicyReason = composeCtx?.sms_content_policy?.reason || null
  const bookingUrl = smsLinksAllowed ? (composeCtx?.booking?.url || '') : ''
  const bookingUrlReason = smsLinksAllowed
    ? (composeCtx?.booking?.reason || null)
    : null

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
    if (!smsLinksAllowed) return body.replaceAll('{booking_link}', '').trim()
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

  async function handleDeleteThisLead() {
    if (deletingLead) return
    const nm = [lead.first_name, lead.last_name].filter(Boolean).join(' ')
    if (!confirmLeadDelete(1, nm)) return
    setDeletingLead(true); setDeleteLeadErr('')
    try {
      const out = deleteSummary(await deleteLeadIds(api, [lead.id]))
      if (out.ok) navigate('/leads')
      else setDeleteLeadErr(out.text)
    } finally {
      setDeletingLead(false)
    }
  }

  // ── COMMAND CENTER VIEW MODEL ─────────────────────────────────────────────
  // Everything below is read from data this page already loads. Nothing is
  // invented: an unknown value says so.
  const fullName = [lead.first_name, lead.last_name].filter(Boolean).join(' ') || 'Unnamed contact'
  const brainState = brainCtx?.state || {}
  const humanActive = brainState.mode === 'human_active'
  const nba = brainCtx?.next_best_action || null
  const lastInbound = [...events].reverse().find((e) => e.type === 'inbound') || null
  const lastEvent = [...events].reverse().find((e) => e.type === 'inbound' || e.type === 'outbound') || null
  const inboundVoicemails = dialerHist?.voicemails || []
  const latestVoicemail = inboundVoicemails[0] || null
  const assignedUser = assignableUsers.find((u) => u.id === lead.assigned_to_id)
  const assignedLabel = !lead.assigned_to_id ? 'Unassigned'
    : lead.assigned_to_id === currentUser?.id ? 'You'
    : (assignedUser?.full_name || 'Another advisor')
  const sourceLabel = lead.source_detail || lead.import_list_name || lead.source || null
  let customFields = {}
  try { customFields = lead.custom_fields ? JSON.parse(lead.custom_fields) : {} } catch { customFields = {} }
  const locationLabel = customFields.location || customFields.location_name || customFields.funeral_home
    || customFields.facility || customFields.campus || null
  const canBookLink = !isTest && wholesaleLinks.length === 0 && Boolean(lead.email) && canSendEmail
  const bookBlockedReason = isTest ? TEST_REASON
    : wholesaleLinks.length > 0 ? 'Sellers are scheduled by a person, not sent a booking link.'
    : !lead.email ? 'Booking links go by email, and this contact has no email address.'
    : !canSendEmail ? (emailBlockedReason || 'Email is not available for this contact.') : null
  const aiStartBlockedReason = isTest ? TEST_REASON
    : humanActive ? 'A person is handling this conversation. Use “Resume AI” in Conversation Brain to hand it back first.'
    : null

  const readiness = [
    { key: 'email', label: 'Email', ok: canSendEmail, why: emailBlockedReason },
    { key: 'sms', label: 'Text', ok: canSendSMS, why: smsBlockedReason },
    { key: 'voice', label: 'Voice', ok: canVoice, why: voiceBlockedReason },
  ]
  const readyCount = readiness.filter((r) => r.ok).length
  const missingContact = !lead.phone && !lead.email

  const bookingLabel = !booking ? (wholesaleLinks.length ? 'By a person' : 'None')
    : booking.status === 'booked' ? 'Booked'
    : booking.status === 'pending' ? 'Pending'
    : booking.status === 'cancelled' ? 'Cancelled'
    : booking.status === 'expired' ? 'Expired'
    : String(booking.status || 'Unknown')
  const bookingSub = !booking ? (wholesaleLinks.length ? 'Seller is scheduled by hand' : 'No link sent yet')
    : booking.status === 'booked' && booking.booked_time ? new Date(booking.booked_time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : booking.status === 'pending' ? 'Link sent; waiting for a time'
    : booking.status === 'cancelled' ? 'Send a new link to reschedule'
    : `Link sent ${timeAgo(booking.created_at)}`

  const filteredEvents = convFilter === 'all' ? events : events.filter((e) => e.channel === convFilter)
  const filteredKeys = convFilter === 'all' ? eventKeys : eventKeys.filter((_, i) => events[i].channel === convFilter)
  const historyRows = [...events].reverse().filter((e) => {
    const q = historyQuery.trim().toLowerCase()
    if (!q) return true
    return [e.subject, e.body, e.body_preview, e.channel, e.type].filter(Boolean).join(' ').toLowerCase().includes(q)
  })
  const ACTIVITY_KINDS = {
    all: { label: 'All', test: () => true },
    messages: { label: 'Messages', test: (t) => /^(sms|email)/.test(t) },
    booking: { label: 'Booking', test: (t) => t.startsWith('booking') },
    automation: { label: 'AI & cadence', test: (t) => t.startsWith('cadence') || t.startsWith('ai') },
    consent: { label: 'Consent & flags', test: (t) => /dnc|consent|suppress|flag|opt/.test(t) },
    other: { label: 'Other', test: (t) => !/^(sms|email|booking|cadence|ai)|dnc|consent|suppress|flag|opt/.test(t) },
  }
  const activityRows = activity?.events
    ? [...activity.events].reverse().filter((ev) => ACTIVITY_KINDS[activityKind].test(String(ev.type || '')))
    : []
  // Every call (both directions, AI and human) is listed once, in the dialer's
  // Call history. The AI section only adds what that list cannot show: the
  // transcripts of AI calls.
  const aiCalls = (data?.voice_calls || [])
    .filter((vc) => vc.direction !== 'inbound' && !vc.is_human_call && vc.provider !== 'manual')
  const aiTranscripts = aiCalls.filter((vc) => vc.transcript || (vc.voicemail_left && vc.voicemail_transcript))
  const callCount = (dialerHist?.calls?.length || 0)
    + (dialerHist?.voicemails || []).filter((v) => !v.call_id).length

  function openTab(t) {
    setActiveTab(t)
    const params = new URLSearchParams(location.search)
    params.set('tab', t)
    navigate({ search: `?${params.toString()}` }, { replace: true })
  }
  function focusComposer() {
    openTab('conversation')
    setTimeout(() => {
      const box = composerRef.current?.querySelector('textarea')
      if (box) { box.scrollIntoView({ behavior: 'smooth', block: 'center' }); box.focus() }
    }, 60)
  }
  function goBack() {
    if (window.history.state && window.history.state.idx > 0) navigate(-1)
    else navigate('/leads')
  }
  async function headerBook() {
    if (booking?.status === 'booked') { setShowCaseFile(true); return }
    if (!canBookLink) return
    if (!window.confirm(`Email a booking link to ${lead.first_name || 'this contact'} at ${lead.email}?`)) return
    handleResendBookingLink()
  }
  function openEdit() {
    setEditForm({ first_name: lead.first_name || '', last_name: lead.last_name || '', phone: lead.phone || '', email: lead.email || '', notes: lead.notes || '', street_address: lead.street_address || '', city: lead.city || '', state: lead.state || '', zip_code: lead.zip_code || '', relationship_type: lead.relationship_type || 'cold_lead' })
    setEditError('')
    setShowEdit(true)
    setMenu(null)
    openTab('overview')
  }

  const TABS = [
    ['conversation', 'Conversation', events.length || null],
    ['calls', 'Calls', callCount || null],
    ['activity', 'Activity', activity?.event_count || null],
    ['history', 'History', null],
    ['overview', 'Overview', null],
  ]

  // The tone + direction + suggest controls, shared by the text and email composers.
  const aiAssist = (onSuggest, label) => (
    <details className="lcc-assist">
      <summary>AI assist <span className="lcc-muted">· {currentTone.label} tone</span></summary>
      <div className="lcc-assist-body">
        <div className="lead-tone-pills" role="group" aria-label="Message tone">
          {TONES.map((t, i) => (
            <button key={t.key} type="button"
              className={`lead-tone-pill ${tone === i ? 'lead-tone-pill--active' : ''}`}
              style={tone === i ? { borderColor: t.color, color: t.color, background: `${t.color}18` } : {}}
              onClick={() => setTone(i)} title={t.desc} aria-pressed={tone === i}
            >{t.label}</button>
          ))}
        </div>
        <input className="compose-subject" aria-label="AI direction" placeholder={aiDirectionHint}
          value={aiDirection} onChange={(e) => setAiDirection(e.target.value)} />
        <div className="lead-compose-suggest">
          <button type="button" className="btn btn--secondary" onClick={onSuggest} disabled={suggestingReply}>
            {suggestingReply ? 'Drafting…' : label}
          </button>
          <span className="lead-compose-hint">AI fills the box. You edit and send.</span>
        </div>
      </div>
    </details>
  )

  return (
    <div className="lcc" data-testid="lead-command-center">
      <nav className="lcc-crumbs" aria-label="Breadcrumb">
        <button type="button" className="lcc-linkbtn" onClick={goBack}>← Leads</button>
        <span aria-hidden="true">›</span>
        <span>{fullName}</span>
        {orgName && <span className="lcc-crumb-org">· {orgName}</span>}
      </nav>
      {deleteLeadErr && (
        <div role="alert" data-testid="lead-delete-error" className="lcc-alert lcc-alert--red">{deleteLeadErr}</div>
      )}

      {/* ── HEADER: identity, state, owner, actions ── */}
      <header className="lcc-card lcc-head">
        <div className="lcc-id">
          <div className="lcc-avatar" aria-hidden="true">{initials}</div>
          <div className="lcc-id-text">
            <div className="lcc-name-row">
              <h1 className="lcc-name">{fullName}</h1>
              {editSuccess && <span className="lcc-chip lcc-chip--green" role="status">Saved</span>}
            </div>
            <div className="lcc-contact">
              {lead.phone && <span>☎ {formatPhone(lead.phone)}</span>}
              {lead.callback_phone && lead.callback_phone !== lead.phone && (
                <span title="Callback number the contact gave">↩ {formatPhone(lead.callback_phone)}</span>
              )}
              {lead.email && <span>✉ {lead.email}</span>}
              {missingContact && <span className="lcc-muted">No phone or email on file</span>}
            </div>
            <div className="lcc-badges">
              <StatusBadge status={lead.status} />
              {lead.tier && <TierBadge tier={lead.tier} />}
              {brainState.mode && (
                <span className={`lcc-chip ${humanActive ? 'lcc-chip--blue' : brainState.mode === 'ai_paused' ? 'lcc-chip--amber' : 'lcc-chip--neutral'}`}
                  title={humanActive ? 'A person owns this conversation. The AI will not send on its own.' : 'Who is handling this conversation'}>
                  {{ ai_active: 'AI active', human_active: 'Human active', ai_paused: 'AI paused',
                     waiting_on_customer: 'Waiting on customer', waiting_on_human: 'Waiting on a person' }[brainState.mode] || brainState.mode}
                </span>
              )}
              {brainState.state === 'waiting_on_staff' && <span className="lcc-chip lcc-chip--amber" title="The customer is waiting for us">Waiting on us</span>}
              {brainState.state === 'waiting_on_customer' && <span className="lcc-chip lcc-chip--neutral">Waiting on customer</span>}
              {isTest && <span className="lcc-chip lcc-chip--red" data-testid="lcc-test-chip" title={lead.test_note || TEST_REASON}>Internal test · no outreach</span>}
              {lead.status === 'dnc' && <span className="lcc-chip lcc-chip--red">Do not contact</span>}
              {lead.is_duplicate && <span className="lcc-chip lcc-chip--neutral">Duplicate</span>}
              {lead.manual_flag === 'bad_email' && <span className="lcc-chip lcc-chip--amber">Bad email flagged</span>}
              {lead.manual_flag === 'remove_all' && <span className="lcc-chip lcc-chip--red">Removed from all outreach</span>}
            </div>
          </div>
        </div>

        <dl className="lcc-meta">
          <div>
            <dt>Last activity</dt>
            <dd>{lastEvent ? timeAgo(lastEvent.timestamp) : 'None yet'}</dd>
            <dd className="lcc-sub">{lastEvent ? `${lastEvent.type === 'inbound' ? 'Inbound' : 'Outbound'} ${CHANNEL_WORD[lastEvent.channel] || lastEvent.channel}` : 'No messages'}</dd>
          </div>
          <div>
            <dt id="lead-assign-label">Assigned advisor</dt>
            {canReassignLead ? (
              <dd>
                <select aria-labelledby="lead-assign-label" className="lcc-select" value={lead.assigned_to_id || ''}
                  onChange={handleAssignmentChange} disabled={assignmentSaving}>
                  <option value="">Unassigned</option>
                  {assignableUsers.map((user) => <option key={user.id} value={user.id}>{user.full_name}</option>)}
                </select>
              </dd>
            ) : <dd>{assignedLabel}</dd>}
          </div>
          <div>
            <dt>Location · source</dt>
            <dd>{locationLabel || orgName || 'This workspace'}</dd>
            <dd className="lcc-sub">{sourceLabel || 'Source not recorded'}</dd>
          </div>
        </dl>

        <div className="lcc-actions">
          <button type="button" className="lcc-btn" onClick={() => openTab('calls')}
            disabled={isTest || !lead.phone}
            title={isTest ? TEST_REASON : !lead.phone ? 'No phone number on file' : 'Open calling for this contact'}>
            Call
          </button>
          <button type="button" className="lcc-btn" onClick={focusComposer}
            disabled={!canSend}
            title={canSend ? 'Write a text or email' : (isTest ? TEST_REASON : (smsBlockedReason || emailBlockedReason || 'Messaging is not available'))}>
            Message
          </button>
          <button type="button" className="lcc-btn lcc-btn--primary" onClick={headerBook}
            disabled={booking?.status === 'booked' ? false : (!canBookLink || resendingLink)}
            title={booking?.status === 'booked' ? 'Open the client record for this booking' : (bookBlockedReason || 'Email a booking link')}>
            {booking?.status === 'booked' ? 'Client record' : resendingLink ? 'Sending…' : booking ? 'Resend link' : 'Book'}
          </button>
          <div className="lcc-menu-wrap">
            <button type="button" className="lcc-btn lcc-btn--icon" aria-haspopup="menu" aria-expanded={menu === 'more'}
              aria-label="More actions" onClick={() => setMenu(menu === 'more' ? null : 'more')}>⋯</button>
            {menu === 'more' && (
              <div className="lcc-menu" role="menu">
                <button type="button" role="menuitem" onClick={openEdit}>Edit details</button>
                <button type="button" role="menuitem" onClick={() => { setMenu(null); setShowCaseFile(true) }}>Open client record</button>
                {lead.manual_flag ? (
                  <button type="button" role="menuitem" disabled={flagging} onClick={() => { setMenu(null); handleFlagLead(null) }}>Unflag (restore to lists)</button>
                ) : (<>
                  <button type="button" role="menuitem" disabled={flagging} onClick={() => { setMenu(null); handleFlagLead('bad_email') }}>Flag: bad email</button>
                  <button type="button" role="menuitem" disabled={flagging} onClick={() => { setMenu(null); handleFlagLead('remove_all') }}>Flag: remove from all outreach</button>
                </>)}
                <button type="button" role="menuitem" className="lcc-danger" data-testid="lead-detail-delete"
                  disabled={deletingLead} onClick={() => { setMenu(null); handleDeleteThisLead() }}>
                  {deletingLead ? 'Deleting…' : 'Delete contact'}
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      {resendLinkMsg && (
        <div role="status" className={`lcc-alert ${resendLinkMsg.ok ? 'lcc-alert--green' : 'lcc-alert--red'}`}>{resendLinkMsg.text}</div>
      )}
      {assignmentError && <div role="alert" className="lcc-alert lcc-alert--red">{assignmentError}</div>}

      {/* ── KPI STRIP ── */}
      <div className="lcc-kpis">
        <div className="lcc-card lcc-kpi">
          <div className="lcc-kpi-label">Last inbound</div>
          <div className="lcc-kpi-value">{lastInbound ? timeAgo(lastInbound.timestamp) : 'None'}</div>
          <div className="lcc-kpi-sub" title={lastInbound?.body || ''}>
            {lastInbound ? `“${(lastInbound.body || lastInbound.subject || '').slice(0, 40)}${(lastInbound.body || '').length > 40 ? '…' : ''}”` : 'No reply yet'}
          </div>
        </div>
        <button type="button" className="lcc-card lcc-kpi lcc-kpi--btn" onClick={() => openTab('calls')}>
          <div className="lcc-kpi-label">Voicemails</div>
          <div className="lcc-kpi-value">{!dialerHist ? '…' : dialerHist.unavailable ? '—' : inboundVoicemails.length}</div>
          <div className="lcc-kpi-sub">
            {!dialerHist ? 'Loading' : dialerHist.unavailable ? 'Call history unavailable' : latestVoicemail ? `Latest ${timeAgo(latestVoicemail.received_at)}${dialerHist.unreviewed_voicemails ? ` · ${dialerHist.unreviewed_voicemails} new` : ''}` : 'None from this contact'}
          </div>
        </button>
        <div className="lcc-card lcc-kpi">
          <div className="lcc-kpi-label">Booking</div>
          <div className={`lcc-kpi-value ${booking?.status === 'booked' ? 'lcc-green' : booking?.status === 'pending' ? 'lcc-amber' : ''}`}>{bookingLabel}</div>
          <div className="lcc-kpi-sub">{bookingSub}</div>
        </div>
        <div className="lcc-card lcc-kpi">
          <div className="lcc-kpi-label">Reachability</div>
          <div className={`lcc-kpi-value ${readyCount === 0 ? 'lcc-red' : readyCount < 3 ? 'lcc-amber' : 'lcc-green'}`}>
            {isTest || readyCount === 0 ? 'Blocked' : readyCount === 3 ? 'All ready' : `${readyCount} of 3 ready`}
          </div>
          <div className="lcc-kpi-sub">{isTest ? 'Internal test record' : readiness.map((r) => `${r.label} ${r.ok ? '✓' : '✕'}`).join(' · ')}</div>
        </div>
        <div className="lcc-card lcc-kpi">
          <div className="lcc-kpi-label">Next best action</div>
          <div className="lcc-kpi-value">{nba ? ({ reply: 'Reply', wait: 'Wait', call: 'Call', create_task: 'Create task', schedule: 'Schedule', assign: 'Assign', request_info: 'Request info', move_stage: 'Move stage', nurture: 'Nurture', escalate: 'Escalate', human_takeover: 'Human takeover', do_nothing: 'Do nothing' }[nba.action] || nba.action) : (brainCtx?.unavailable ? 'Unavailable' : brainCtx ? 'None yet' : '…')}</div>
          <div className="lcc-kpi-sub" title={nba?.reason || ''}>
            {isTest && nba && nba.action !== 'wait' && nba.action !== 'do_nothing' ? 'Outreach is off for this test record' : (nba?.reason || (humanActive ? 'A person owns this conversation' : 'From Conversation Brain'))}
          </div>
        </div>
      </div>

      <div className="lcc-main">
        {/* ── WORKSPACE: tabs + panes ── */}
        <section className="lcc-card lcc-work" aria-label="Contact workspace">
          <div className="lcc-tabbar">
            <div className="lcc-tabs" role="tablist" aria-label="Contact views">
              {TABS.map(([key, label, count]) => (
                <button key={key} type="button" role="tab" id={`lcc-tab-${key}`} aria-selected={activeTab === key}
                  aria-controls={`lcc-pane-${key}`} className={`lcc-tab ${activeTab === key ? 'lcc-tab--on' : ''}`}
                  onClick={() => openTab(key)}>
                  {label}{count ? <span className="lcc-count">{count}</span> : null}
                </button>
              ))}
            </div>
            {activeTab === 'conversation' && (
              <select className="lcc-select" aria-label="Show channel" value={convFilter} onChange={(e) => setConvFilter(e.target.value)}>
                <option value="all">All channels</option>
                <option value="sms">Text only</option>
                <option value="email">Email only</option>
                <option value="cadence">Cadence notes</option>
              </select>
            )}
          </div>

          {/* Tab: Conversation */}
          {activeTab === 'conversation' && (
            <div className="lcc-pane lcc-pane--conv" id="lcc-pane-conversation" role="tabpanel" aria-labelledby="lcc-tab-conversation">
              <div className="lcc-thread" ref={timelineRef}>
                {olderCursor.hasMore && (
                  <div className="lcc-center">
                    <button type="button" className="lcc-btn lcc-btn--sm" onClick={loadOlderActivity} disabled={loadingOlder}>
                      {loadingOlder ? 'Loading older messages…' : 'Load older messages'}
                    </button>
                    {olderError && <div className="lcc-err">{olderError}</div>}
                  </div>
                )}
                {filteredEvents.length === 0 ? (
                  <div className="lcc-empty">{events.length === 0 ? 'No messages yet.' : 'Nothing on this channel.'}</div>
                ) : filteredEvents.map((e, i) => <ConversationBubble key={filteredKeys[i]} event={e} />)}
              </div>

              {/* DOCKED COMPOSER */}
              <div className="lcc-composer" ref={composerRef}>
                {isTest ? (
                  <div className="lcc-composer-off" data-testid="lcc-composer-blocked">
                    <strong>Messaging is off.</strong> {TEST_REASON}
                    <textarea className="compose-textarea" disabled rows={2} aria-label="Message (disabled)"
                      placeholder="Messaging is disabled for an internal test record." />
                  </div>
                ) : !canSend ? (
                  <div className="lcc-composer-off">
                    {lead.is_duplicate ? 'This contact is a duplicate.' :
                     lead.status === 'dnc' ? 'This contact is marked do-not-contact.' :
                     missingContact ? 'No phone or email on file.' : (
                      <>
                        <strong>Messaging isn't available for this contact yet.</strong>
                        {[['Text', smsBlockedReason], ['Email', emailBlockedReason]].filter(([, r]) => r)
                          .map(([label, r]) => <div key={label}><strong>{label}:</strong> {r}</div>)}
                      </>)}
                  </div>
                ) : (
                  <>
                    <div className="lcc-composer-head">
                      <div className="lcc-seg" role="group" aria-label="Send as">
                        <button type="button" className={effectiveSendMode === 'sms' ? 'on' : ''} disabled={!canSendSMS}
                          title={canSendSMS ? undefined : smsBlockedReason || undefined} onClick={() => setSendMode('sms')}>Text</button>
                        <button type="button" className={effectiveSendMode === 'email' ? 'on' : ''} disabled={!canSendEmail}
                          title={canSendEmail ? undefined : emailBlockedReason || undefined} onClick={() => setSendMode('email')}>Email</button>
                      </div>
                      <label className="lcc-inline">
                        <span>Booking type</span>
                        <select className="lcc-select" value={apptLabel} onChange={(e) => setApptLabel(e.target.value)}
                          title="Sets the appointment type on the booking link">
                          {apptTypeOptions.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                        </select>
                      </label>
                      {humanActive && <span className="lcc-chip lcc-chip--blue" title="You are replying as a person; the AI will not send on its own.">You own this conversation</span>}
                    </div>

                    {effectiveSendMode === 'sms' && canSendSMS ? (
                      <div className="lead-compose">
                        {aiAssist(handleSuggestReply, `Suggest ${currentTone.label} reply`)}
                        <textarea className="compose-textarea" aria-label="Text message"
                          placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                          value={messageText} onChange={(e) => setMessageText(e.target.value)} rows={3} />
                        <input ref={mediaInputRef} type="file" accept=".jpg,.jpeg,.png,.gif,.pdf" style={{ display: 'none' }} onChange={handleMediaUpload} />
                        {mediaUrl && (
                          <div className="lcc-attach">
                            <span>📎 {mediaFileName}</span><span className="lcc-muted">Will send as MMS</span>
                            <button type="button" className="lcc-linkbtn" onClick={handleRemoveMedia} aria-label="Remove attachment">Remove</button>
                          </div>
                        )}
                        {mediaError && <div className="lcc-err">{mediaError}</div>}
                        {!smsLinksAllowed && (
                          <div style={SX.previewWarn}>
                            <div style={SX.previewLabel}>Text messages carry no links</div>
                            <div style={SX.previewBody}>The scheduling link goes out by email. Anything that looks like a link or a phone number is removed from a text before it is sent.</div>
                            {smsPolicyReason && <div style={SX.previewMeta}>{smsPolicyReason}</div>}
                          </div>
                        )}
                        {smsLinksAllowed && includeBookingLink && (bookingUrl || bookingUrlReason) && (
                          <div style={bookingUrl ? SX.previewOk : SX.previewWarn}>
                            <div style={SX.previewLabel}>{bookingUrl ? 'Will be sent as' : 'Booking link unavailable'}</div>
                            {bookingUrl ? (<>
                              <div style={SX.previewBody}>{smsPreview || bookingUrl}</div>
                              <div style={SX.previewMeta}>{smsPreview.length} characters · {smsPreview.length <= 160 ? '1 segment' : `${Math.ceil(smsPreview.length / 153)} segments`}</div>
                            </>) : <div style={SX.previewBody}>{bookingUrlReason}</div>}
                          </div>
                        )}
                        {smsSender && !smsSender.ready && <div style={SX.senderWarn}>{smsSender.reason}</div>}
                        <div className="compose-footer lcc-compose-foot">
                          <span className="lcc-muted lcc-small">
                            {smsSender && smsSender.ready && smsSender.from_number
                              ? `From ${formatPhone(smsSender.from_number)}${smsSender.source === 'organization' ? ' (organization sender)' : ''}` : ''}
                          </span>
                          {smsLinksAllowed && (
                            <label className="compose-checkbox">
                              <input type="checkbox" checked={includeBookingLink} onChange={(e) => setIncludeBookingLink(e.target.checked)} />
                              Include booking link
                            </label>
                          )}
                          <button type="button" className="lcc-btn lcc-btn--sm" onClick={() => mediaInputRef.current?.click()}
                            disabled={mediaUploading} title="Attach flyer or image (MMS)">{mediaUploading ? 'Uploading…' : '📎 Flyer'}</button>
                          <button type="button" className="lcc-btn lcc-btn--primary" onClick={handleSend}
                            disabled={sending || !messageText.trim() || (smsSender ? !smsSender.ready : false)}>
                            {sending ? 'Sending…' : mediaUrl ? 'Send MMS' : 'Send text'}
                          </button>
                        </div>
                        {sendError && <div className="compose-error" role="alert">{sendError}</div>}
                      </div>
                    ) : canSendEmail ? (
                      <div className="lead-compose">
                        {aiAssist(handleSuggestEmail, `AI draft ${currentTone.label} email`)}
                        {emailDraftReady && (
                          <div className="lcc-alert lcc-alert--green" role="status">
                            Draft ready — review and edit below, then send.
                            <button type="button" className="lcc-linkbtn" onClick={() => setEmailDraftReady(false)} aria-label="Dismiss">Dismiss</button>
                          </div>
                        )}
                        <input className="compose-subject" aria-label="Email subject"
                          placeholder={`Subject — e.g. ${smartSubject(lead.first_name, lead.tier, lead.message_track, orgName)}`}
                          value={emailSubject} onChange={(e) => setEmailSubject(e.target.value)} />
                        <textarea className="compose-textarea" aria-label="Email body"
                          placeholder={`Hi ${lead.first_name || 'there'}, this is...`}
                          value={emailBody} onChange={(e) => setEmailBody(e.target.value)} rows={4} />
                        <input ref={emailAttachRef} type="file" accept="image/*,.pdf,.doc,.docx" style={{ display: 'none' }}
                          onChange={(e) => setEmailAttachment(e.target.files?.[0] || null)} />
                        {emailAttachment && (
                          <div className="lcc-attach">
                            <span>📎 {emailAttachment.name}</span>
                            <button type="button" className="lcc-linkbtn" aria-label="Remove attachment"
                              onClick={() => { setEmailAttachment(null); if (emailAttachRef.current) emailAttachRef.current.value = '' }}>Remove</button>
                          </div>
                        )}
                        {emailSender && !emailSender.ready && <div style={SX.senderWarn}>{emailSender.reason}</div>}
                        <div className="compose-footer lcc-compose-foot">
                          <span className="lcc-muted lcc-small">
                            {emailSender && emailSender.ready && emailSender.from_email
                              ? `From ${emailSender.from_email}${emailSender.channel === 'microsoft_365' ? ' (your Microsoft 365 inbox)' : ''}${emailSender.reply_to_email ? ` · replies to ${emailSender.reply_to_email}` : ''}`
                              : ''}
                          </span>
                          <label className="compose-checkbox">
                            <input type="checkbox" checked={includeBookingLink} onChange={(e) => setIncludeBookingLink(e.target.checked)} />
                            Include booking button
                          </label>
                          <button type="button" className="lcc-btn lcc-btn--sm" onClick={() => emailAttachRef.current?.click()}>
                            📎 {emailAttachment ? 'Change file' : 'Attach'}
                          </button>
                          <button type="button" className="lcc-btn lcc-btn--primary" onClick={handleSendEmail}
                            disabled={sendingEmail || !emailBody.trim() || (emailSender ? !emailSender.ready : false)}>
                            {sendingEmail ? 'Sending…' : emailAttachment ? 'Send with attachment' : 'Send email'}
                          </button>
                        </div>
                        {sendError && <div className="compose-error" role="alert">{sendError}</div>}
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </div>
          )}

          {/* Tab: Calls */}
          {activeTab === 'calls' && (
            <div className="lcc-pane lcc-scroll" id="lcc-pane-calls" role="tabpanel" aria-labelledby="lcc-tab-calls">
              <div className="lcc-calls">
                <section className="lcc-sub-card" aria-label="Call this contact">
                  <h3 className="lcc-h3">Call from your phone</h3>
                  {lead.phone ? (
                    <HumanDialerPanel leadId={leadId} phone={lead.phone} blockedReason={isTest ? TEST_REASON : null}
                      compact onHistory={setDialerHist} />
                  ) : <p className="lcc-muted">No phone number on file.</p>}
                </section>
                <section className="lcc-sub-card" aria-label="AI voice call">
                  <h3 className="lcc-h3">AI voice call</h3>
                  {callResult && (
                    <div className="lcc-alert lcc-alert--green" role="status">
                      Call placed — call #{callResult.call_number} to {callResult.lead_name}
                      {callResult.from_phone ? ` from ${formatPhone(callResult.from_phone)}` : ''}
                    </div>
                  )}
                  {callError && <div style={SX.senderWarn} role="alert">{callError}</div>}
                  {!canVoice && voiceBlockedReason && !callError && <div style={SX.senderWarn}>{voiceBlockedReason}</div>}
                  <button type="button" className="lcc-btn lcc-btn--primary" onClick={handleCall}
                    disabled={calling || !canVoice || !lead.phone} title={canVoice ? undefined : (voiceBlockedReason || undefined)}>
                    {calling ? 'Calling…' : 'Call with AI'}
                  </button>
                  <p className="lcc-muted lcc-small">The AI agent calls, says it is an AI, and books if they say yes. Recorded. Up to 3 attempts.</p>
                  <h4 className="lcc-h4">AI call transcripts</h4>
                  {aiTranscripts.length === 0 ? (
                    <p className="lcc-muted lcc-small">No AI call transcripts yet. Every call is listed under Call history.</p>
                  ) : (
                    <ul className="lcc-calllist">
                      {aiTranscripts.map((vc) => {
                        const outcomeLabel = { booked: 'Booked', booking_requested: 'Booking link sent', no_answer: 'No answer', not_interested: 'Not interested', completed: 'Completed', escalated: 'Escalated', failed: 'Failed' }[vc.outcome] || (vc.outcome ? vc.outcome.replace(/_/g, ' ') : (vc.status || 'Unknown'))
                        const tone = { booked: 'green', booking_requested: 'blue', completed: 'green', escalated: 'amber', failed: 'red', not_interested: 'red' }[vc.outcome] || 'neutral'
                        const startedLabel = fullWhen(vc.started_at || vc.created_at)
                        const durationLabel = vc.duration_seconds ? `${Math.floor(vc.duration_seconds / 60)}m ${vc.duration_seconds % 60}s` : null
                        return (
                          <li key={vc.id} className="lcc-call">
                            <div className="lcc-call-head">
                              <strong>Outbound AI call{vc.call_number ? ` #${vc.call_number}` : ''}</strong>
                              <span className={`lcc-chip lcc-chip--${tone}`}>{outcomeLabel}</span>
                              {vc.voicemail_left && <span className="lcc-chip lcc-chip--amber">Voicemail left</span>}
                              {durationLabel && <span className="lcc-muted">{durationLabel}</span>}
                              <span className="lcc-muted lcc-right">{startedLabel}</span>
                            </div>
                            {vc.transcript && (
                              <details><summary>Call transcript</summary><pre className="lcc-transcript">{vc.transcript}</pre></details>
                            )}
                            {vc.voicemail_left && vc.voicemail_transcript && (
                              <details><summary>Voicemail transcript</summary><pre className="lcc-transcript">{vc.voicemail_transcript}</pre></details>
                            )}
                            {!vc.transcript && !(vc.voicemail_left && vc.voicemail_transcript) && (
                              <div className="lcc-muted lcc-small">{vc.voicemail_left ? 'Voicemail left — no transcript.' : 'No transcript for this call.'}</div>
                            )}
                            {vc.recording_url && <div className="lcc-muted lcc-small">Recording saved with the phone provider.</div>}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>
              </div>
            </div>
          )}

          {/* Tab: Activity — the granular log for this contact, newest first */}
          {activeTab === 'activity' && (
            <div className="lcc-pane lcc-scroll" id="lcc-pane-activity" role="tabpanel" aria-labelledby="lcc-tab-activity">
              <div className="lcc-filterbar">
                <div className="lcc-seg" role="group" aria-label="Activity type">
                  {Object.entries(ACTIVITY_KINDS).map(([k, v]) => (
                    <button key={k} type="button" className={activityKind === k ? 'on' : ''} onClick={() => setActivityKind(k)}>{v.label}</button>
                  ))}
                </div>
                <button type="button" className="lcc-linkbtn" onClick={handleRefreshActivity} disabled={activityLoading}>
                  {activityLoading ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
              {activityError && (
                <div className="lcc-alert lcc-alert--red" role="alert">{activityError}
                  <button type="button" className="lcc-linkbtn" onClick={handleRefreshActivity}>Retry</button></div>
              )}
              {activityLoading && !activity && <div className="lcc-empty">Loading activity…</div>}
              {activity && activityRows.length === 0 && <div className="lcc-empty">No activity of this kind.</div>}
              <ol className="lcc-events">
                {activityRows.map((ev) => (
                  <li key={ev.id} className={`lcc-event lcc-event--${String(ev.type || '').split('_')[0]}`}>
                    <div className="lcc-event-head">
                      <strong>{String(ev.label || '').replace(/_/g, ' ')}</strong>
                      <time className="lcc-muted" title={fullWhen(ev.ts)}>{fullWhen(ev.ts)}</time>
                    </div>
                    {ev.body && <div className="lcc-event-body">{ev.body.length > 220 ? ev.body.slice(0, 220) + '…' : ev.body}</div>}
                    {ev.meta?.hot_reason && <div className="lcc-small lcc-red">{ev.meta.hot_reason}</div>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Tab: History — every message on record, searchable, plus cadence touches */}
          {activeTab === 'history' && (
            <div className="lcc-pane lcc-scroll" id="lcc-pane-history" role="tabpanel" aria-labelledby="lcc-tab-history">
              <div className="lcc-filterbar">
                <input className="lcc-search" type="search" placeholder="Search messages and emails" aria-label="Search history"
                  value={historyQuery} onChange={(e) => setHistoryQuery(e.target.value)} />
                {olderCursor.hasMore && (
                  <button type="button" className="lcc-btn lcc-btn--sm" onClick={loadOlderActivity} disabled={loadingOlder}>
                    {loadingOlder ? 'Loading…' : 'Load older records'}
                  </button>
                )}
              </div>
              {olderError && <div className="lcc-err">{olderError}</div>}
              <div className="lcc-tablewrap">
                <table className="lcc-table">
                  <thead><tr><th scope="col">When</th><th scope="col">Direction</th><th scope="col">Channel</th><th scope="col">Message</th><th scope="col">Result</th></tr></thead>
                  <tbody>
                    {historyRows.length === 0 && <tr><td colSpan={5} className="lcc-muted">{events.length ? 'No match.' : 'No records yet.'}</td></tr>}
                    {historyRows.map((e, i) => (
                      <tr key={`${timelineEventKey(e)}#h${i}`}>
                        <td className="lcc-nowrap">{fullWhen(e.timestamp)}</td>
                        <td>{e.type === 'inbound' ? 'Inbound' : e.type === 'outbound' ? 'Outbound' : 'System'}</td>
                        <td>{CHANNEL_WORD[e.channel] || e.channel}</td>
                        <td>{e.subject && <strong>{e.subject} · </strong>}{(e.body || e.body_preview || '').slice(0, 160)}</td>
                        <td>{e.type === 'outbound' ? (DELIVERY_STATES[e.delivery?.state]?.label || e.status || '—') : (e.status || '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3 className="lcc-h3">Cadence touches</h3>
              <CadencePanel cadence={cadenceHistory} loading={cadenceLoading} />
            </div>
          )}

          {/* Tab: Overview — details, edit, record */}
          {activeTab === 'overview' && (
            <div className="lcc-pane lcc-scroll" id="lcc-pane-overview" role="tabpanel" aria-labelledby="lcc-tab-overview">
              {showEdit ? (
                <section className="lcc-sub-card" aria-label="Edit details">
                  <div className="lcc-formgrid">
                    {[['first_name', 'First name'], ['last_name', 'Last name'], ['phone', 'Phone', 'e.g. 214-555-0199'], ['email', 'Email', 'email@example.com'],
                      ['street_address', 'Street address', '123 Main St'], ['city', 'City'], ['state', 'State', 'TX'], ['zip_code', 'ZIP', '75001']].map(([k, label, ph]) => (
                      <label key={k} className="leads-add-label">{label}
                        <input className="search-input" value={editForm[k] || ''} placeholder={ph}
                          onChange={(e) => setEditForm((f) => ({ ...f, [k]: e.target.value }))} />
                      </label>
                    ))}
                  </div>
                  <label className="leads-add-label">Lead relationship
                    <span className="lcc-small lcc-muted">The main AI context — it controls how familiar the AI sounds.</span>
                    <select className="search-input" value={editForm.relationship_type || 'cold_lead'}
                      onChange={(e) => setEditForm((f) => ({ ...f, relationship_type: e.target.value }))}>
                      <option value="cold_lead">Cold lead — no prior relationship</option>
                      <option value="warm_lead">Warm lead — showed prior interest / referral</option>
                      <option value="re_engagement">Re-engagement — contacted before, went quiet</option>
                      <option value="previous_prospect">Previous prospect — was in pipeline, didn't close</option>
                      <option value="past_customer">Past customer — was a customer, lapsed</option>
                      <option value="existing_customer">Existing customer — active relationship</option>
                    </select>
                  </label>
                  <label className="leads-add-label">Notes
                    <textarea className="search-input" rows={3} value={editForm.notes || ''}
                      onChange={(e) => setEditForm((f) => ({ ...f, notes: e.target.value }))} style={{ resize: 'vertical', fontFamily: 'inherit' }} />
                  </label>
                  <div className="lcc-row">
                    <button type="button" className="lcc-btn lcc-btn--primary" onClick={handleSaveEdit} disabled={editSaving}>{editSaving ? 'Saving…' : 'Save changes'}</button>
                    <button type="button" className="lcc-btn" onClick={() => setShowEdit(false)}>Cancel</button>
                    {editError && <span className="lcc-err" role="alert">{editError}</span>}
                  </div>
                </section>
              ) : (
                <div className="lcc-row lcc-row--end"><button type="button" className="lcc-btn lcc-btn--sm" onClick={openEdit}>Edit details</button></div>
              )}
              <dl className="lcc-facts">
                {[
                  ['Phone', lead.phone ? formatPhone(lead.phone) : null],
                  ['Callback phone', lead.callback_phone ? formatPhone(lead.callback_phone) : null],
                  ['Email', lead.email],
                  ['Address', [lead.street_address, lead.city, lead.state, lead.zip_code].filter(Boolean).join(', ')],
                  ['Relationship', lead.relationship_type ? lead.relationship_type.replace(/_/g, ' ') : null],
                  ['Appointment type', apptLabel],
                  ['Tier', lead.tier ? lead.tier.replace(/_/g, ' ') : null],
                  ['Source', sourceLabel],
                  ['Source file', lead.source_file],
                  ['Source year', lead.source_year],
                  ['Last action', lead.last_action_raw],
                  ['Status reason', lead.status_reason_raw],
                  ['Flag', lead.manual_flag ? lead.manual_flag.replace(/_/g, ' ') : null],
                  ['Test record', isTest ? (lead.test_note || 'Yes — no outreach') : null],
                  ['Notes', lead.notes],
                ].filter(([, v]) => v !== null && v !== undefined && v !== '').map(([label, value]) => (
                  <div key={label} className="lcc-fact"><dt>{label}</dt><dd>{String(value)}</dd></div>
                ))}
              </dl>
              <div className="lcc-row">
                <button type="button" className="lcc-btn" onClick={() => setShowCaseFile(true)}>Open client record</button>
              </div>
              {isFuneral && <OutcomeTracker leadId={leadId} />}
            </div>
          )}
        </section>

        {/* ── RIGHT RAIL ── */}
        <aside className="lcc-rail" aria-label="Contact state and next steps">
          <ConversationBrain leadId={leadId} compact onContext={setBrainCtx}
            outreachBlocked={isTest ? TEST_REASON : null}
            onUseDraft={(text, channel) => {
              if (channel === 'email' && canSendEmail) { setSendMode('email'); setEmailBody(text) }
              else { setSendMode('sms'); setMessageText(text) }
              focusComposer()
            }} />

          <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-ready-h">
            <h2 className="lcc-h2" id="lcc-ready-h">Channel readiness</h2>
            {isTest ? (
              <div className="lcc-alert lcc-alert--red" role="note" data-testid="lcc-test-block">
                <strong>Production outreach blocked</strong>
                <div>{TEST_REASON}</div>
              </div>
            ) : readyCount === 0 && (
              <div className="lcc-alert lcc-alert--amber" role="note">
                <strong>No channel can reach this contact right now.</strong>
              </div>
            )}
            <ul className="lcc-ready">
              {readiness.map((r) => (
                <li key={r.key}>
                  <span>{r.label}</span>
                  <span className={`lcc-chip ${r.ok ? 'lcc-chip--green' : isTest || /consent|opt|dnc|do-not|suppress/i.test(r.why || '') ? 'lcc-chip--red' : 'lcc-chip--amber'}`}
                    title={r.ok ? 'Ready' : (r.why || 'Unavailable')}>
                    {r.ok ? 'Ready' : isTest ? 'Blocked' : 'Unavailable'}
                  </span>
                </li>
              ))}
            </ul>
            <button type="button" className="lcc-linkbtn" aria-expanded={showDiag} onClick={() => setShowDiag(!showDiag)}>
              {showDiag ? 'Hide details' : 'View details'}
            </button>
            {showDiag && (
              <div className="lcc-diag">
                {readiness.filter((r) => !r.ok && r.why).map((r) => <div key={r.key}><strong>{r.label}:</strong> {r.why}</div>)}
                {isTest && lead.test_note && <div><strong>Why it is a test record:</strong> {lead.test_note}</div>}
                {smsSender && <div><strong>Text sender:</strong> {smsSender.ready ? (smsSender.from_number ? formatPhone(smsSender.from_number) : 'Ready') : smsSender.reason}</div>}
                {emailSender && <div><strong>Email sender:</strong> {emailSender.ready ? (emailSender.from_email || 'Ready') : emailSender.reason}</div>}
                {!smsLinksAllowed && smsPolicyReason && <div><strong>Text links:</strong> {smsPolicyReason}</div>}
              </div>
            )}
          </section>

          <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-ai-h">
            <div className="lcc-rail-head">
              <h2 className="lcc-h2" id="lcc-ai-h">AI conversation</h2>
              {aiConvStatus?.active && <span className="lcc-chip lcc-chip--green">Running</span>}
              {aiConvStatus?.paused && <span className="lcc-chip lcc-chip--amber">Paused</span>}
              {aiConvStatus?.flagged && <span className="lcc-chip lcc-chip--red">Needs you</span>}
            </div>
            {aiConvStatus?.flagged && <div className="lcc-alert lcc-alert--red">{aiConvStatus.flag_reason || 'Human response needed'}</div>}
            {aiConvStatus?.active && !aiConvStatus?.flagged && (
              <p className="lcc-small">Touch {aiConvStatus.touch_number || 0}{aiConvStatus.total_touches ? ` of ${aiConvStatus.total_touches}` : ''} · {aiConvStatus.messages_sent || 0} sent
                {aiConvStatus.next_send_at && <span className="lcc-muted"> · next {new Date(aiConvStatus.next_send_at).toLocaleString()}</span>}</p>
            )}
            {!aiConvStatus?.active || aiConvStatus?.status === 'not_started' ? (
              aiStartBlockedReason ? (
                <p className="lcc-small lcc-muted" data-testid="lcc-ai-blocked">{aiStartBlockedReason}</p>
              ) : (<>
                <div className="lcc-seg" role="group" aria-label="AI channel">
                  {[['email', 'Email', canSendEmail, emailBlockedReason], ['sms', 'Text', canSendSMS, smsBlockedReason], ['both', 'Both', canSendBoth, ch?.both?.reason]]
                    .map(([key, label, available, why]) => (
                      <button key={key} type="button" className={effectiveAiChannel === key ? 'on' : ''} disabled={!available}
                        title={available ? undefined : (why || 'Not available for this contact')} onClick={() => available && setAiConvChannel(key)}>{label}</button>
                    ))}
                </div>
                <button type="button" className="lcc-btn lcc-btn--primary lcc-btn--block" onClick={handleStartAiConversation}
                  disabled={aiConvLoading || !effectiveAiChannel}>
                  {aiConvLoading ? 'Starting…' : 'Start AI conversation'}
                </button>
                <p className="lcc-small lcc-muted">{effectiveAiChannel ? 'Runs a multi-touch sequence, answers replies, and pauses when a person is needed.' : 'This contact cannot be reached on any channel right now.'}</p>
              </>)
            ) : (
              aiConvStatus?.paused || aiConvStatus?.flagged ? (
                <button type="button" className="lcc-btn lcc-btn--primary lcc-btn--block" onClick={handleResumeAiConversation}
                  disabled={Boolean(aiStartBlockedReason)} title={aiStartBlockedReason || undefined}>Resume AI</button>
              ) : (
                <button type="button" className="lcc-btn lcc-btn--block" onClick={handlePauseAiConversation}>Pause AI</button>
              )
            )}
            {aiConvStatus?.active && aiStartBlockedReason && (aiConvStatus?.paused || aiConvStatus?.flagged) && (
              <p className="lcc-small lcc-muted">{aiStartBlockedReason}</p>
            )}
          </section>

          <div className="lcc-rail-pair">
            <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-book-h">
              <div className="lcc-rail-head">
                <h2 className="lcc-h2" id="lcc-book-h">Booking</h2>
                <span className={`lcc-chip ${booking?.status === 'booked' ? 'lcc-chip--green' : booking?.status === 'pending' ? 'lcc-chip--amber' : 'lcc-chip--neutral'}`}>{bookingLabel}</span>
              </div>
              <p className="lcc-small">{bookingSub}{booking?.status === 'booked' && booking.calendar_event_id ? ' · on the calendar' : ''}</p>
              {booking?.status === 'booked' ? (
                <div className="lcc-col">
                  <button type="button" className="lcc-btn lcc-btn--sm" onClick={() => setShowCaseFile(true)}>Open client record</button>
                  <button type="button" className="lcc-btn lcc-btn--sm lcc-danger" onClick={() => handleCancelBooking(booking.id)} disabled={cancelling}>
                    {cancelling ? 'Cancelling…' : 'Cancel booking'}
                  </button>
                </div>
              ) : wholesaleLinks.length === 0 && (
                <button type="button" className="lcc-btn lcc-btn--sm" onClick={headerBook}
                  disabled={!canBookLink || resendingLink} title={bookBlockedReason || undefined}>
                  {resendingLink ? 'Sending…' : booking ? 'Resend booking link' : 'Send booking link'}
                </button>
              )}
              {!canBookLink && booking?.status !== 'booked' && bookBlockedReason && wholesaleLinks.length === 0 && (
                <p className="lcc-small lcc-muted">{bookBlockedReason}</p>
              )}
            </section>
            <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-read-h">
              <div className="lcc-rail-head">
                <h2 className="lcc-h2" id="lcc-read-h">AI read</h2>
                {ai_quality && <span className={`badge badge--${QUALITY_COLOR[ai_quality.quality] || 'neutral'}`}>{ai_quality.quality || 'unknown'}</span>}
              </div>
              {ai_quality?.recommended_approach
                ? <p className="lcc-small">{ai_quality.recommended_approach}</p>
                : <p className="lcc-small lcc-muted">{ai_quality ? 'No recommendation recorded.' : 'No analysis yet.'}</p>}
              <button type="button" className="lcc-btn lcc-btn--sm" onClick={handleRunAnalysis} disabled={analyzing}>
                {analyzing ? 'Analyzing…' : ai_quality ? 'Re-analyze' : 'Run analysis'}
              </button>
              {analysisError && <div className="lcc-err">{analysisError}</div>}
            </section>
          </div>

          {wholesaleLinks.length > 0 && (
            <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-wh-h">
              <h2 className="lcc-h2" id="lcc-wh-h">Wholesale seller</h2>
              <ul className="lcc-plain">
                {wholesaleLinks.map((w) => (
                  <li key={w.seller_profile_id}>
                    {w.deal_id
                      ? <a href={`/wholesale/deals/${w.deal_id}`} onClick={(e) => { e.preventDefault(); navigate(`/wholesale/deals/${w.deal_id}`) }}>{w.address || 'Property'}</a>
                      : <span>{w.address || 'Property'}</span>}
                    <div className="lcc-small lcc-muted">
                      {[w.stage && w.stage.replace(/_/g, ' '), w.primary_seller ? 'seller of record' : 'additional contact — verify',
                        w.source === 'seller_inquiry' ? 'came in via the seller form' : null,
                        w.appointment_status && w.appointment_status !== 'none' ? `appointment ${w.appointment_status.replace(/_/g, ' ')}` : null]
                        .filter(Boolean).join(' · ')}
                    </div>
                  </li>
                ))}
              </ul>
              <p className="lcc-small lcc-muted">Sellers are scheduled by a person, not sent a booking link.</p>
            </section>
          )}

          <details className="lcc-card lcc-rail-card lcc-fold">
            <summary><span className="lcc-h2">Cadence</span>
              <span className="lcc-muted lcc-small">{cadenceHistory?.status || (cadenceHistory?.history?.length ? 'history' : 'not enrolled')}</span></summary>
            <div className="lcc-row lcc-row--end">
              <button type="button" className="lcc-linkbtn" onClick={loadCadenceHistory} disabled={cadenceLoading}>{cadenceLoading ? 'Refreshing…' : 'Refresh'}</button>
            </div>
            <CadencePanel cadence={cadenceHistory} loading={cadenceLoading} />
          </details>
        </aside>
      </div>

      {showCaseFile && (
        <CaseFile lead={lead} onClose={() => setShowCaseFile(false)} onSaved={() => {}} />
      )}
    </div>
  )
}
