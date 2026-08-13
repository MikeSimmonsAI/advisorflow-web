import { useEffect, useState, Fragment, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './EmailQueue.css'

// Prefixes that are never a real person's inbox — system/auto senders
const SYSTEM_PREFIXES = [
  'noreply', 'no-reply', 'no_reply', 'donotreply', 'do-not-reply',
  'do_not_reply', 'notifications', 'notification', 'automated',
  'mailer', 'mailer-daemon', 'postmaster', 'bounce', 'bounces',
  'autoresponder', 'newsletter', 'alerts', 'alert', 'system',
  'support@domo', 'info@domo',
]

// Common domain typos — right side of @ has one of these
const MISSPELLED_DOMAINS = [
  // gmail variants
  'gnail.com', 'gmial.com', 'gamil.com', 'gmai.com', 'gmail.co',
  'gmail.org', 'gmail.net', 'gmaill.com', 'gmil.com', 'gmal.com',
  'gmali.com', 'gimail.com', 'gemail.com', 'gmaol.com', 'gmaul.com',
  // yahoo variants
  'yahoa.com', 'yaho.com', 'yahooo.com', 'yaoo.com', 'ymail.co',
  'yahomail.com', 'yhaoo.com', 'yahou.com', 'yhaoo.com', 'yhoo.com',
  // hotmail / outlook variants
  'hotmial.com', 'homail.com', 'hotmai.com', 'hotmal.com', 'hotmale.com',
  'outlok.com', 'outllok.com', 'outook.com', 'otlook.com', 'ourlook.com',
  'outlookl.com', 'outlook.co', 'outloook.com',
  // aol
  'aoll.com', 'aol.co', 'aoo.com',
  // icloud
  'icloud.co', 'iclould.com', 'iclod.com',
  // comcast / att
  'comast.net', 'comacast.net', 'attt.net', 'att.com',
]

// Known system/tool company domains
const SYSTEM_DOMAINS = [
  'domo.com', 'salesforce.com', 'hubspot.com', 'marketo.com', 'mailchimp.com',
  'constantcontact.com', 'sendgrid.net', 'amazonses.com', 'mailgun.org',
  'auto-maildelivery.com', 'mail-delivery.com', 'bulk-mailer.com',
  'massmail.com', 'emaildelivery.com', 'mailinglist.com',
]

// Domain substrings that scream bulk/automated sender
const SYSTEM_DOMAIN_PATTERNS = [
  'auto-mail', 'automail', 'bulk-mail', 'bulkmail', 'mass-mail', 'massmail',
  'mail-delivery', 'maildelivery', 'email-delivery', 'emaildelivery',
  'noreply', 'no-reply', 'donotreply', 'newsletter', 'mailinglist',
  'notification', 'auto-send', 'autosend',
]

function detectBadEmail(lead) {
  if (!lead.email) return null
  const email = lead.email.toLowerCase().trim()
  const [prefix, domain] = email.split('@')
  if (!domain) return 'invalid format'

  // System prefix check
  if (SYSTEM_PREFIXES.some((p) => prefix === p || prefix.startsWith(p + '.')))
    return 'system address'

  // Known system domains or domain patterns
  if (SYSTEM_DOMAINS.some((d) => domain === d))
    return 'system domain'
  if (SYSTEM_DOMAIN_PATTERNS.some((p) => domain.includes(p)))
    return 'system domain'

  // Misspelled domain
  if (MISSPELLED_DOMAINS.includes(domain))
    return 'possible typo'

  return null
}

function detectMismatch(lead) {
  if (!lead.email || (!lead.first_name && !lead.last_name)) return false
  const username = lead.email.split('@')[0].toLowerCase()
  const emailTokens = username.split(/[._\-+0-9]+/).filter((t) => t.length > 1)
  const nameTokens = [lead.first_name, lead.last_name]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length > 1)
  if (!emailTokens.length || !nameTokens.length) return false
  return !emailTokens.some((et) => nameTokens.some((nt) => nt.includes(et) || et.includes(nt)))
}

const TONE_OPTIONS = [
  { key: 'cold',   label: '❄️ Cold',   desc: 'Soft intro, no pressure, just opening a door' },
  { key: 'warm',   label: '☀️ Warm',   desc: 'Friendly, suggest a conversation, low-key CTA' },
  { key: 'hot',    label: '🔥 Hot',    desc: 'Direct, confident, clear ask for the appointment' },
  { key: 'urgent', label: '⚡ Urgent', desc: 'Brief, time-sensitive, gentle urgency' },
]

const STATUS_CONFIG = {
  new:     { label: 'Not contacted', color: 'var(--signal-blue)',  dim: 'var(--signal-blue-dim)' },
  sent:    { label: 'Emailed',       color: 'var(--signal-amber)', dim: 'var(--signal-amber-dim)' },
  replied: { label: 'Replied',       color: 'var(--signal-green)', dim: 'var(--signal-green-dim)' },
  booked:  { label: 'Booked',        color: 'var(--signal-green)', dim: 'var(--signal-green-dim)' },
}

export default function EmailQueue() {
  const navigate = useNavigate()
  const [leads, setLeads]             = useState([])
  const [loading, setLoading]         = useState(true)
  const [selected, setSelected]       = useState(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [showMismatchOnly, setShowMismatchOnly] = useState(false)
  const [showFlagged, setShowFlagged] = useState(false)
  const [showManualFlagged, setShowManualFlagged] = useState(false)
  const [manualFlaggedLeads, setManualFlaggedLeads] = useState([])
  const [flagging, setFlagging] = useState(null)

  // ── Recently Sent log ─────────────────────────────────────────────────────
  const [sentLog, setSentLog]           = useState([])
  const [sentLogVisible, setSentLogVisible] = useState(false)

  function loadSentLog() {
    api.get('/email/sent-log?limit=150')
      .then(setSentLog)
      .catch(() => {})
  }

  function loadManualFlagged() {
    api.get('/leads/flagged').then(setManualFlaggedLeads).catch(() => {})
  }

  async function handleFlagLead(lead, flagType) {
    if (flagType) {
      const label = flagType === 'bad_email' ? 'bad email' : 'remove from all outreach'
      if (!window.confirm(`Flag "${lead.first_name} ${lead.last_name}" as ${label}?\n\nYou can unflag anytime to restore them.`)) return
    }
    setFlagging(lead.id)
    try {
      await api.patch(`/leads/${lead.id}/flag`, { flag_type: flagType || null })
      load()
      loadManualFlagged()
    } catch (err) {
      alert(`Flag failed: ${err.message}`)
    } finally {
      setFlagging(null)
    }
  }

  // ── Batch compose drawer ──────────────────────────────────────────────────
  const [composeOpen, setComposeOpen]       = useState(false)
  const [tone, setTone]                     = useState('warm')
  const [aiDirection, setAiDirection]       = useState('')
  const [batchSubject, setBatchSubject]     = useState('')
  const [batchBody, setBatchBody]           = useState('')
  const [batchBookingLink, setBatchBookingLink] = useState(true)
  const [batchDrafting, setBatchDrafting]   = useState(false)
  const [batchDraftErr, setBatchDraftErr]   = useState('')
  const [batchSending, setBatchSending]     = useState(false)
  const [batchResult, setBatchResult]       = useState(null)

  // ── Per-lead draft panel (single lead, still exists) ─────────────────────
  const [draftLead, setDraftLead]         = useState(null)
  const [drafting, setDrafting]           = useState(false)
  const [draftResult, setDraftResult]     = useState(null)
  const [draftError, setDraftError]       = useState('')
  const [selectedOption, setSelectedOption] = useState(null)
  const [editedSubject, setEditedSubject] = useState('')
  const [editedBody, setEditedBody]       = useState('')
  const [sendingDraft, setSendingDraft]   = useState(false)
  const [draftSentMsg, setDraftSentMsg]   = useState('')

  function load(query = searchQuery) {
    setLoading(true)
    const trimmed = query.trim()
    const path = trimmed ? `/email/queue?search=${encodeURIComponent(trimmed)}` : '/email/queue'
    api.get(path)
      .then((rows) => { setLeads(rows || []); setSelected(new Set()) })
      .catch(() => setLeads([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = setTimeout(() => load(searchQuery), 250)
    return () => clearTimeout(timer)
  }, [searchQuery])

  useEffect(() => { loadManualFlagged() }, [])
  useEffect(() => { loadSentLog() }, [])

  function toggle(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleAll() {
    if (visibleLeads.length > 0 && selected.size === visibleLeads.length) setSelected(new Set())
    else setSelected(new Set(visibleLeads.map((l) => l.id)))
  }

  // ── Batch: AI-generate a draft from the first selected lead ──────────────
  async function handleBatchAiDraft() {
    const ids = Array.from(selected)
    if (!ids.length) return
    setBatchDrafting(true)
    setBatchDraftErr('')
    try {
      const result = await api.post(`/email/draft/${ids[0]}`, {
        tone,
        ai_direction: aiDirection || null,
      })
      // Use the first option as a starting point
      const opt = result.options?.[0]
      if (opt) {
        setBatchSubject(opt.subject || '')
        setBatchBody(opt.body || '')
      } else {
        setBatchDraftErr('AI returned no options — try adjusting your direction.')
      }
    } catch (err) {
      setBatchDraftErr(err.message || 'AI draft failed.')
    } finally {
      setBatchDrafting(false)
    }
  }

  // ── Batch: send custom message to all selected leads ─────────────────────
  async function handleBatchComposeSend() {
    if (!selected.size || !batchBody.trim()) return
    const ids = Array.from(selected)

    // Mismatch warning — now a non-blocking banner (we already show it in the drawer)
    const mismatchCount = ids.filter((id) => {
      const lead = leads.find((l) => l.id === id)
      return lead && detectMismatch(lead)
    }).length

    if (mismatchCount > 0) {
      const ok = window.confirm(
        `⚠️ ${mismatchCount} of your selected leads have a name/email mismatch.\n\n` +
        `In funeral home data, the email often belongs to a surviving family member.\n\n` +
        `Continue sending to all ${ids.length} selected leads?`
      )
      if (!ok) return
    }

    setBatchSending(true)
    setBatchResult(null)

    const results = await Promise.allSettled(
      ids.map((id) =>
        api.post(`/email/send/${id}`, {
          subject: batchSubject.trim() || 'Hi there',
          body: batchBody.trim(),
          include_booking_link: batchBookingLink,
        })
      )
    )

    const sent   = results.filter((r) => r.status === 'fulfilled').length
    const failed = results.filter((r) => r.status === 'rejected').length

    setBatchResult({ sent, failed, total: ids.length })
    setBatchSending(false)

    if (sent > 0) {
      setSelected(new Set())
      setComposeOpen(false)
      setBatchSubject('')
      setBatchBody('')
      load()
      loadSentLog()
      setSentLogVisible(true)  // auto-expand so they can see who was sent
    }
  }

  // ── Per-lead draft ────────────────────────────────────────────────────────
  async function handleOpenDraft(lead) {
    if (draftLead?.id === lead.id) {
      setDraftLead(null)
      setDraftResult(null)
      return
    }
    setDraftLead(lead)
    setDraftResult(null)
    setDraftError('')
    setSelectedOption(null)
    setEditedSubject('')
    setEditedBody('')
    setDraftSentMsg('')
  }

  async function handleGenerateDraft() {
    if (!draftLead) return
    setDrafting(true)
    setDraftResult(null)
    setDraftError('')
    setSelectedOption(null)
    try {
      const result = await api.post(`/email/draft/${draftLead.id}`, {
        tone,
        ai_direction: aiDirection || null,
      })
      setDraftResult(result)
    } catch (err) {
      setDraftError(err.message || 'AI draft failed.')
    } finally {
      setDrafting(false)
    }
  }

  function handleSelectOption(option) {
    setSelectedOption(option)
    setEditedSubject(option.subject)
    setEditedBody(option.body)
  }

  async function handleSendDraft() {
    if (!draftLead || !editedBody.trim()) return
    setSendingDraft(true)
    setDraftSentMsg('')
    try {
      const formData = new FormData()
      formData.append('subject', editedSubject || `Hi ${draftLead.first_name || 'there'}`)
      formData.append('body_html', editedBody.replace(/\n/g, '<br>'))
      await api.upload(`/email/send-with-attachment/${draftLead.id}`, formData)
      setDraftSentMsg(`✓ Email sent to ${draftLead.first_name} ${draftLead.last_name}`)
      setDraftLead(null)
      setDraftResult(null)
      load()
      loadSentLog()
      setSentLogVisible(true)
    } catch (err) {
      setDraftSentMsg(`Failed: ${err.message}`)
    } finally {
      setSendingDraft(false)
    }
  }

  const mismatchLeads  = leads.filter(detectMismatch)
  const badEmailLeads  = leads.filter((l) => detectBadEmail(l))
  const badEmailIds    = new Set(badEmailLeads.map((l) => l.id))
  // Main list: exclude flagged bad-email leads unless user chose to show them
  const cleanLeads     = leads.filter((l) => !badEmailIds.has(l.id))
  const visibleLeads   = showMismatchOnly ? mismatchLeads.filter((l) => !badEmailIds.has(l.id)) : cleanLeads
  const selectedIds    = Array.from(selected)
  const selectedMismatches = selectedIds.filter((id) => {
    const lead = leads.find((l) => l.id === id)
    return lead && detectMismatch(lead)
  })

  const counts = {
    total:    leads.length,
    cold:     leads.filter((l) => l.status === 'new').length,
    warm:     leads.filter((l) => l.status === 'sent').length,
    hot:      leads.filter((l) => l.status === 'replied' || l.status === 'booked').length,
    mismatch: mismatchLeads.length,
    badEmail: badEmailLeads.length,
  }

  const currentTone = TONE_OPTIONS.find(t => t.key === tone) || TONE_OPTIONS[1]

  const STATS = [
    { key: 'total',    label: 'In queue',       value: counts.total,    color: 'var(--text-primary)',   dot: 'rgba(255,255,255,0.3)', icon: '📬' },
    { key: 'cold',     label: 'Cold',            value: counts.cold,     color: 'var(--signal-blue)',   dot: 'var(--signal-blue)',  icon: '❄️' },
    { key: 'warm',     label: 'Warm',            value: counts.warm,     color: 'var(--signal-amber)',  dot: 'var(--signal-amber)', icon: '☀️' },
    { key: 'hot',      label: 'Replied/Booked',  value: counts.hot,      color: 'var(--signal-green)',  dot: 'var(--signal-green)', icon: '🔥' },
    ...(counts.badEmail > 0 ? [{ key: 'badEmail', label: 'Bad emails', value: counts.badEmail, color: '#e74c3c', dot: '#e74c3c', icon: '🚫' }] : []),
  ]

  return (
    <div style={{ paddingBottom: composeOpen ? 520 : selected.size > 0 ? 72 : 0 }}>

      {/* ── Page header ───────────────────────────────────────────────── */}
      <div className="eq-page-header">
        <div className="eq-page-header-left">
          <div className="eq-page-title-row">
            <h1 className="page-title" style={{ margin: 0 }}>Email queue</h1>
            {!loading && (
              <span className="eq-queue-badge">{counts.total} leads</span>
            )}
          </div>
          <p className="page-subtitle" style={{ marginTop: 6 }}>
            Check the boxes to select leads, then hit <span className="eq-inline-chip">✉️ Compose &amp; Send</span> to write and send your campaign.
            For a single lead, use <span className="eq-inline-chip">✨ Draft</span> to get AI-personalized options.
          </p>
        </div>
      </div>

      {/* ── Unified stats strip ───────────────────────────────────────── */}
      <div className="panel eq-stats-strip">
        {STATS.map((s, i) => (
          <div key={s.key} className="eq-stat-segment" style={{ '--stat-color': s.color, '--stat-dot': s.dot }}>
            <div className="eq-stat-icon">{s.icon}</div>
            <strong className="eq-stat-value">{loading ? '—' : s.value}</strong>
            <span className="eq-stat-label">{s.label}</span>
            {i < STATS.length - 1 && <div className="eq-stat-divider" />}
          </div>
        ))}

        {/* Mismatch segment — only when present */}
        {!loading && counts.mismatch > 0 && (
          <>
            <div className="eq-stat-divider eq-stat-divider--standalone" />
            <div
              className="eq-stat-segment eq-stat-segment--warn"
              onClick={() => setShowMismatchOnly((v) => !v)}
              title="Click to filter to mismatched leads only"
              style={{ '--stat-color': 'var(--signal-red)', '--stat-dot': 'var(--signal-red)', cursor: 'pointer' }}
            >
              <div className="eq-stat-icon">⚠️</div>
              <strong className="eq-stat-value">{counts.mismatch}</strong>
              <span className="eq-stat-label" style={{ color: 'var(--signal-red)' }}>
                {showMismatchOnly ? 'Mismatch · clear ×' : 'Mismatch'}
              </span>
            </div>
          </>
        )}
      </div>

      {/* ── AI controls bar ───────────────────────────────────────────── */}
      <div className="panel eq-controls-bar">
        <div className="eq-controls-left">
          <span className="eq-controls-label">✨ AI tone</span>
          <div className="eq-controls-pills">
            {TONE_OPTIONS.map((t) => (
              <button
                key={t.key}
                className={`lead-tone-pill ${tone === t.key ? 'lead-tone-pill--active' : ''}`}
                onClick={() => setTone(t.key)}
                title={t.desc}
              >
                {t.label}
              </button>
            ))}
          </div>
          <span className="eq-tone-hint">{currentTone.desc}</span>
        </div>
        <div className="eq-controls-right">
          <span className="eq-controls-label">Campaign direction</span>
          <input
            className="settings-input eq-direction-input"
            placeholder="e.g. file check — ask if they still need pre-need planning"
            value={aiDirection}
            onChange={(e) => setAiDirection(e.target.value)}
          />
        </div>
      </div>

      {batchResult && (
        <div className={`eq-send-result ${batchResult.failed === batchResult.total ? 'eq-send-result--error' : 'eq-send-result--success'}`}>
          {batchResult.failed === batchResult.total
            ? `All ${batchResult.total} sends failed — check the console or backend logs.`
            : `✓ Sent to ${batchResult.sent} lead${batchResult.sent !== 1 ? 's' : ''}${batchResult.failed > 0 ? ` · ${batchResult.failed} failed` : ''}`}
        </div>
      )}

      {draftSentMsg && (
        <div className={`eq-send-result ${draftSentMsg.startsWith('Failed') ? 'eq-send-result--error' : 'eq-send-result--success'}`}>
          {draftSentMsg}
        </div>
      )}

      {/* ── Recently Sent log ─────────────────────────────────────────────── */}
      <section style={{ margin: '0 0 16px 0' }}>
        <button
          onClick={() => setSentLogVisible(v => !v)}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: 10,
            background: sentLog.length > 0 ? 'rgba(30,240,168,0.08)' : 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(30,240,168,0.2)',
            borderRadius: sentLogVisible ? '8px 8px 0 0' : 8,
            padding: '10px 16px', cursor: 'pointer',
            color: sentLog.length > 0 ? 'var(--signal-green)' : 'var(--text-secondary)',
            fontSize: 13, fontWeight: 600,
          }}
        >
          <span>
            ✉️ Recently sent
            {sentLog.length > 0 && (
              <span style={{ marginLeft: 8, fontWeight: 400, opacity: 0.7 }}>
                — {sentLog.length} email{sentLog.length !== 1 ? 's' : ''} logged
              </span>
            )}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 11, opacity: 0.6 }}>
            {sentLogVisible ? '▲ Hide' : '▼ Show who you\'ve emailed'}
          </span>
        </button>
        {sentLogVisible && (
          <div style={{ border: '1px solid rgba(30,240,168,0.2)', borderTop: 'none', borderRadius: '0 0 8px 8px', overflow: 'hidden' }}>
            {sentLog.length === 0 ? (
              <div style={{ padding: '20px 16px', fontSize: 13, color: 'var(--text-secondary)', textAlign: 'center' }}>
                No emails sent yet. Sent emails will appear here immediately after sending.
              </div>
            ) : (
              <>
                <div style={{ padding: '8px 16px', background: 'rgba(30,240,168,0.05)', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid rgba(30,240,168,0.1)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Showing your {sentLog.length} most recent email sends — newest first.</span>
                  <span
                    style={{ cursor: 'pointer', textDecoration: 'underline', color: 'var(--accent)' }}
                    onClick={(e) => { e.stopPropagation(); loadSentLog() }}
                  >
                    ↻ Refresh
                  </span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'rgba(255,255,255,0.03)', fontSize: 11, color: 'var(--text-secondary)' }}>
                      <th style={{ padding: '6px 16px', textAlign: 'left', fontWeight: 600 }}>Name</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600 }}>Email</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600 }}>Subject</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600 }}>Sent at</th>
                      <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sentLog.map((entry) => {
                      const sentDate = entry.sent_at ? new Date(entry.sent_at) : null
                      const isToday = sentDate && new Date().toDateString() === sentDate.toDateString()
                      const timeStr = sentDate
                        ? isToday
                          ? sentDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                          : sentDate.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' + sentDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                        : '—'
                      return (
                        <tr key={entry.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                          <td style={{ padding: '9px 16px', fontSize: 13, fontWeight: 600 }}>
                            <span
                              style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline' }}
                              onClick={() => navigate(`/leads/${entry.lead_id}`)}
                            >
                              {entry.lead_name}
                            </span>
                            {isToday && (
                              <span style={{ marginLeft: 6, fontSize: 10, background: 'rgba(30,240,168,0.15)', color: 'var(--signal-green)', border: '1px solid rgba(30,240,168,0.3)', borderRadius: 4, padding: '1px 5px', fontWeight: 700 }}>
                                TODAY
                              </span>
                            )}
                          </td>
                          <td className="mono" style={{ padding: '9px 8px', fontSize: 12, color: 'var(--text-secondary)' }}>
                            {entry.lead_email || '—'}
                          </td>
                          <td style={{ padding: '9px 8px', fontSize: 12, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {entry.subject || '—'}
                          </td>
                          <td style={{ padding: '9px 8px', fontSize: 12, color: isToday ? 'var(--signal-green)' : 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                            {timeStr}
                          </td>
                          <td style={{ padding: '9px 8px' }}>
                            <span style={{
                              fontSize: 11, borderRadius: 4, padding: '2px 6px',
                              background: entry.status === 'sent' ? 'rgba(30,240,168,0.12)' : 'rgba(255,200,0,0.12)',
                              color: entry.status === 'sent' ? 'var(--signal-green)' : 'var(--signal-amber)',
                              border: `1px solid ${entry.status === 'sent' ? 'rgba(30,240,168,0.3)' : 'rgba(255,200,0,0.3)'}`,
                            }}>
                              {entry.status === 'sent' ? '✓ Sent' : entry.status}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="eq-filter-bar">
            <input
              type="text"
              placeholder="Search by name or email…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
              style={{ width: 280 }}
            />
            {showMismatchOnly && (
              <span style={{ fontSize: 12, color: '#c0392b', fontWeight: 600 }}>
                ⚠️ Filtered: mismatches only ·{' '}
                <span style={{ cursor: 'pointer', textDecoration: 'underline' }} onClick={() => setShowMismatchOnly(false)}>
                  clear
                </span>
              </span>
            )}
            <span className="panel-count">{visibleLeads.length} shown</span>
          </div>
          {selected.size > 0 && <span className="eq-selected-badge">{selected.size} selected</span>}
        </div>

        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : visibleLeads.length === 0 ? (
          <div className="empty-state">No leads in email queue.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input
                    type="checkbox"
                    checked={selected.size === visibleLeads.length && visibleLeads.length > 0}
                    onChange={toggleAll}
                  />
                </th>
                <th>Name</th>
                <th>Email</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Source year</th>
                <th>Last action</th>
                <th>AI draft</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visibleLeads.map((lead) => {
                const cfg        = STATUS_CONFIG[lead.status] || STATUS_CONFIG.new
                const isOpen     = draftLead?.id === lead.id
                const isMismatch = detectMismatch(lead)
                const badEmail   = detectBadEmail(lead)
                return (
                  <Fragment key={lead.id}>
                    <tr className={selected.has(lead.id) ? 'eq-row--selected' : ''} style={{ borderBottom: isOpen ? 'none' : undefined }}>
                      <td><input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} /></td>
                      <td>
                        <span
                          className="eq-lead-name"
                          onClick={() => navigate(`/leads/${lead.id}`)}
                          style={{ cursor: 'pointer', fontWeight: 600, color: 'var(--accent)', textDecoration: 'underline' }}
                          title="Open contact record"
                        >
                          {`${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'}
                        </span>
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {lead.email || '—'}
                        {isMismatch && (
                          <span
                            title="Email username doesn't match lead name — may belong to a surviving family member."
                            style={{ marginLeft: 6, fontSize: 11, background: '#fff3cd', color: '#856404',
                              border: '1px solid #ffc107', borderRadius: 4, padding: '1px 5px', cursor: 'help' }}
                          >
                            ⚠️ mismatch
                          </span>
                        )}
                        {badEmail && (
                          <span
                            title={`Bad email detected: ${badEmail}. This address is likely a system notification, wrong domain, or has a typo.`}
                            style={{ marginLeft: 6, fontSize: 11, background: '#ffe4e4', color: '#c0392b',
                              border: '1px solid #e74c3c', borderRadius: 4, padding: '1px 5px', cursor: 'help' }}
                          >
                            🚫 {badEmail}
                          </span>
                        )}
                      </td>
                      <td style={{ fontSize: 12 }}>{lead.tier || '—'}</td>
                      <td>
                        <span className="eq-status-pill" style={{ color: cfg.color, background: cfg.dim }}>{cfg.label}</span>
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>{lead.source_year || '—'}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-secondary)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {lead.last_action_raw || '—'}
                      </td>
                      <td>
                        <button className="btn btn--secondary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => handleOpenDraft(lead)}>
                          {isOpen ? '✕ Close' : '✨ Draft'}
                        </button>
                      </td>
                      <td>
                        <select
                          style={{ fontSize: 11, padding: '2px 6px', cursor: 'pointer', color: 'var(--text-secondary)', background: 'var(--surface-card)', border: '1px solid var(--border-subtle)', borderRadius: 4 }}
                          defaultValue=""
                          onChange={(e) => { if (e.target.value) { handleFlagLead(lead, e.target.value); e.target.value = '' } }}
                          disabled={flagging === lead.id}
                          title="Flag this lead"
                        >
                          <option value="" disabled>⚑ Flag</option>
                          <option value="bad_email">⚠ Bad email</option>
                          <option value="remove_all">⛔ Remove from all outreach</option>
                        </select>
                      </td>
                    </tr>

                    {/* Inline per-lead AI Draft Panel */}
                    {isOpen && (
                      <tr>
                        <td colSpan={8} style={{ padding: 0 }}>
                          <div className="eq-draft-panel">
                            <div className="eq-draft-header">
                              <div>
                                <strong>AI email draft</strong> for {lead.first_name} {lead.last_name}
                                {lead.tier && <span className="tier-chip" style={{ marginLeft: 8, fontSize: 11 }}>{lead.tier}</span>}
                                {lead.source_year && <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>({lead.source_year})</span>}
                              </div>
                              <button className="btn btn--primary" onClick={handleGenerateDraft} disabled={drafting} style={{ fontSize: 13 }}>
                                {drafting ? '⏳ Generating…' : '✨ Generate options'}
                              </button>
                            </div>

                            {isMismatch && (
                              <div style={{ background: '#fff3cd', border: '1px solid #ffc107', borderRadius: 6,
                                padding: '8px 12px', margin: '8px 0', fontSize: 13, color: '#856404' }}>
                                ⚠️ <strong>Name/email mismatch:</strong> The email address doesn't match this lead's name.
                                In funeral home records, this often means the email belongs to a surviving family member.
                                Double-check before sending.
                              </div>
                            )}
                            {badEmail && (
                              <div style={{ background: '#ffe4e4', border: '1px solid #e74c3c', borderRadius: 6,
                                padding: '8px 12px', margin: '8px 0', fontSize: 13, color: '#c0392b' }}>
                                🚫 <strong>Suspicious email ({badEmail}):</strong> {lead.email} looks like a{' '}
                                {badEmail === 'system address' ? 'system/notification address that will never be read by a real person.' :
                                 badEmail === 'system domain' ? 'tool or platform notification address, not a personal inbox.' :
                                 'possible typo — verify the domain before sending.'}
                              </div>
                            )}

                            {draftError && (
                              <div className="compose-error" style={{ margin: '8px 0' }}>⚠️ {draftError}</div>
                            )}

                            {draftResult && (
                              <div className="eq-draft-body">
                                {draftResult.talking_points?.length > 0 && (
                                  <div className="eq-talking-points">
                                    <div className="eq-talking-label">💡 Talking points for this lead</div>
                                    <ul className="eq-talking-list">
                                      {draftResult.talking_points.map((pt, i) => (
                                        <li key={i}>{pt}</li>
                                      ))}
                                    </ul>
                                  </div>
                                )}

                                <div className="eq-options-label">Choose a message to start from:</div>
                                <div className="eq-options-grid">
                                  {draftResult.options?.map((opt, i) => (
                                    <div
                                      key={i}
                                      className={`eq-option-card ${selectedOption === opt ? 'eq-option-card--selected' : ''}`}
                                      onClick={() => handleSelectOption(opt)}
                                    >
                                      <div className="eq-option-label">{opt.label}</div>
                                      <div className="eq-option-subject">Subject: {opt.subject}</div>
                                      <div className="eq-option-preview">{opt.body.slice(0, 120)}…</div>
                                    </div>
                                  ))}
                                </div>

                                {selectedOption && (
                                  <div className="eq-edit-section">
                                    <div className="eq-edit-label">Edit before sending:</div>
                                    <input
                                      className="compose-subject"
                                      value={editedSubject}
                                      onChange={(e) => setEditedSubject(e.target.value)}
                                      placeholder="Subject"
                                    />
                                    <textarea
                                      className="compose-textarea"
                                      rows={7}
                                      value={editedBody}
                                      onChange={(e) => setEditedBody(e.target.value)}
                                    />
                                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8 }}>
                                      <button className="btn btn--secondary" onClick={() => { setSelectedOption(null); setEditedBody(''); setEditedSubject('') }}>
                                        ← Back to options
                                      </button>
                                      <button className="btn btn--primary" onClick={handleSendDraft} disabled={sendingDraft || !editedBody.trim()}>
                                        {sendingDraft ? 'Sending…' : `Send to ${lead.first_name || lead.email}`}
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}

                            {!draftResult && !drafting && (
                              <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '12px 0 0' }}>
                                Click "Generate options" to get AI-crafted talking points and 3 email drafts personalized to this lead's history.
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* ── Flagged emails section (hidden by default) ─────────────────────── */}
      {badEmailLeads.length > 0 && (
        <section style={{ margin: '0 0 16px 0' }}>
          <button
            onClick={() => setShowFlagged((v) => !v)}
            style={{
              width: '100%', display: 'flex', alignItems: 'center', gap: 10,
              background: 'rgba(231,76,60,0.08)', border: '1px solid rgba(231,76,60,0.25)',
              borderRadius: 8, padding: '10px 16px', cursor: 'pointer', color: '#e74c3c',
              fontSize: 13, fontWeight: 600,
            }}
          >
            <span>🚫 {badEmailLeads.length} flagged email{badEmailLeads.length !== 1 ? 's' : ''} hidden from main list</span>
            <span style={{ marginLeft: 'auto', fontSize: 11, opacity: 0.7 }}>
              {showFlagged ? '▲ Hide' : '▼ Show for review'}
            </span>
          </button>
          {showFlagged && (
            <div style={{ border: '1px solid rgba(231,76,60,0.25)', borderTop: 'none', borderRadius: '0 0 8px 8px', overflow: 'hidden' }}>
              <div style={{ padding: '8px 16px', background: 'rgba(231,76,60,0.05)', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid rgba(231,76,60,0.15)' }}>
                These addresses are system notifications, bulk-mail senders, or have domain typos. Verify before sending — most should be deleted.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {badEmailLeads.map((lead) => {
                    const badEmail = detectBadEmail(lead)
                    const isMismatch = detectMismatch(lead)
                    const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'
                    return (
                      <tr key={lead.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)', opacity: 0.85 }}>
                        <td style={{ padding: '10px 16px', fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', width: 180 }}>
                          <span style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline' }}
                            onClick={() => navigate(`/leads/${lead.id}`)}>
                            {name}
                          </span>
                        </td>
                        <td className="mono" style={{ padding: '10px 8px', fontSize: 12 }}>
                          {lead.email || '—'}
                          {badEmail && (
                            <span style={{ marginLeft: 6, fontSize: 11, background: '#ffe4e4', color: '#c0392b',
                              border: '1px solid #e74c3c', borderRadius: 4, padding: '1px 5px' }}>
                              🚫 {badEmail}
                            </span>
                          )}
                          {isMismatch && (
                            <span style={{ marginLeft: 6, fontSize: 11, background: '#fff3cd', color: '#856404',
                              border: '1px solid #ffc107', borderRadius: 4, padding: '1px 5px' }}>
                              ⚠️ mismatch
                            </span>
                          )}
                        </td>
                        <td style={{ padding: '10px 8px', whiteSpace: 'nowrap' }}>
                          <button className="btn btn--secondary" style={{ fontSize: 11, padding: '3px 10px', marginRight: 6 }}
                            onClick={() => navigate(`/leads/${lead.id}`)}>
                            Open →
                          </button>
                          <button className="btn btn--ghost" style={{ fontSize: 11, padding: '3px 10px', color: '#ffaa00', border: '1px solid rgba(255,170,0,0.3)' }}
                            onClick={() => handleFlagLead(lead, 'bad_email')}
                            disabled={flagging === lead.id}
                            title="Confirm — flag this as a bad email address">
                            ⚑ Flag
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* ── Manually flagged leads section ────────────────────────────────── */}
      {manualFlaggedLeads.length > 0 && (
        <section style={{ margin: '0 0 16px 0' }}>
          <button
            onClick={() => setShowManualFlagged(v => !v)}
            style={{
              width: '100%', display: 'flex', alignItems: 'center', gap: 10,
              background: 'rgba(255,100,100,0.08)', border: '1px solid rgba(255,100,100,0.25)',
              borderRadius: 8, padding: '10px 16px', cursor: 'pointer', color: '#ff6464',
              fontSize: 13, fontWeight: 600,
            }}
          >
            <span>⛔ {manualFlaggedLeads.length} manually flagged lead{manualFlaggedLeads.length !== 1 ? 's' : ''} hidden from outreach</span>
            <span style={{ marginLeft: 'auto', fontSize: 11, opacity: 0.7 }}>
              {showManualFlagged ? '▲ Hide' : '▼ Show'}
            </span>
          </button>
          {showManualFlagged && (
            <div style={{ border: '1px solid rgba(255,100,100,0.25)', borderTop: 'none', borderRadius: '0 0 8px 8px', overflow: 'hidden' }}>
              <div style={{ padding: '8px 16px', background: 'rgba(255,100,100,0.05)', fontSize: 12, color: 'var(--text-secondary)', borderBottom: '1px solid rgba(255,100,100,0.15)' }}>
                These leads were manually flagged by an advisor. Unflag them to restore to all lists and email queue.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {manualFlaggedLeads.map((lead) => {
                    const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'
                    return (
                      <tr key={lead.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                        <td style={{ padding: '10px 16px', fontSize: 13, fontWeight: 600, width: 180 }}>
                          <span style={{ cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline' }}
                            onClick={() => navigate(`/leads/${lead.id}`)}>
                            {name}
                          </span>
                        </td>
                        <td className="mono" style={{ padding: '10px 8px', fontSize: 12 }}>{lead.email || '—'}</td>
                        <td style={{ padding: '10px 8px' }}>
                          {lead.manual_flag === 'bad_email'
                            ? <span style={{ fontSize: 11, background: 'rgba(255,170,0,0.15)', color: '#ffaa00', border: '1px solid rgba(255,170,0,0.3)', borderRadius: 4, padding: '2px 6px' }}>⚠ bad email</span>
                            : <span style={{ fontSize: 11, background: 'rgba(255,80,80,0.15)', color: '#ff6464', border: '1px solid rgba(255,80,80,0.3)', borderRadius: 4, padding: '2px 6px' }}>⛔ remove all</span>
                          }
                        </td>
                        <td style={{ padding: '10px 8px', fontSize: 12, color: 'var(--text-secondary)' }}>{lead.manual_flag_reason || ''}</td>
                        <td style={{ padding: '10px 8px' }}>
                          <button className="btn btn--ghost" style={{ fontSize: 11, padding: '3px 10px', color: 'var(--signal-green)', border: '1px solid rgba(100,255,150,0.3)' }}
                            onClick={() => handleFlagLead(lead, null)}
                            disabled={flagging === lead.id}>
                            ✓ Unflag
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* ── Fixed bottom bar + Compose Drawer ─────────────────────────────── */}
      {selected.size > 0 && (
        <div className="eq-batch-bar">
          <div className="eq-batch-bar-inner">
            <span className="eq-batch-count">
              {selected.size} lead{selected.size !== 1 ? 's' : ''} selected
            </span>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button
                className="btn btn--secondary"
                style={{ fontSize: 13 }}
                onClick={() => setSelected(new Set())}
              >
                Deselect all
              </button>
              <button
                className="btn btn--primary"
                style={{ fontSize: 14, fontWeight: 700 }}
                onClick={() => setComposeOpen((v) => !v)}
              >
                {composeOpen ? '✕ Close compose' : `✉️ Compose & Send (${selected.size})`}
              </button>
            </div>
          </div>

          {/* Compose Drawer — slides up from the bar */}
          {composeOpen && (
            <div className="eq-compose-drawer">
              <div className="eq-compose-drawer-header">
                <div>
                  <span className="eq-compose-drawer-title">Compose email campaign</span>
                  <span className="eq-compose-drawer-sub">
                    Will send to {selected.size} selected lead{selected.size !== 1 ? 's' : ''}
                    {selectedMismatches.length > 0 && (
                      <span style={{ color: '#c0392b', marginLeft: 8 }}>
                        · ⚠️ {selectedMismatches.length} name/email mismatch{selectedMismatches.length !== 1 ? 'es' : ''}
                      </span>
                    )}
                  </span>
                </div>
              </div>

              {/* Tone + AI Direction */}
              <div className="eq-compose-section">
                <div className="eq-compose-label">Tone</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                  {TONE_OPTIONS.map((t) => (
                    <button
                      key={t.key}
                      className={`lead-tone-pill ${tone === t.key ? 'lead-tone-pill--active' : ''}`}
                      onClick={() => setTone(t.key)}
                      title={t.desc}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
                <input
                  className="settings-input"
                  placeholder="Campaign direction (optional): e.g. open enrollment reminder, file check, anniversary outreach"
                  value={aiDirection}
                  onChange={(e) => setAiDirection(e.target.value)}
                  style={{ marginBottom: 10 }}
                />
                <button
                  className="btn btn--secondary"
                  style={{ fontSize: 13, alignSelf: 'flex-start' }}
                  onClick={handleBatchAiDraft}
                  disabled={batchDrafting}
                >
                  {batchDrafting ? '⏳ Drafting…' : '✨ AI Draft (fills below)'}
                </button>
                {batchDraftErr && (
                  <div className="compose-error" style={{ marginTop: 8 }}>⚠️ {batchDraftErr}</div>
                )}
              </div>

              {/* Subject + Body */}
              <div className="eq-compose-section" style={{ flex: 1 }}>
                <div className="eq-compose-label">Subject</div>
                <input
                  className="compose-subject"
                  placeholder="Email subject line…"
                  value={batchSubject}
                  onChange={(e) => setBatchSubject(e.target.value)}
                  style={{ marginBottom: 10 }}
                />
                <div className="eq-compose-label">Message</div>
                <textarea
                  className="compose-textarea"
                  rows={6}
                  placeholder="Write your message here, or click ✨ AI Draft to generate one. This message will go to all selected leads."
                  value={batchBody}
                  onChange={(e) => setBatchBody(e.target.value)}
                />
              </div>

              {/* Footer controls */}
              <div className="eq-compose-footer">
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={batchBookingLink}
                    onChange={(e) => setBatchBookingLink(e.target.checked)}
                  />
                  Include booking link
                </label>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button
                    className="btn btn--secondary"
                    onClick={() => { setBatchSubject(''); setBatchBody(''); setBatchDraftErr('') }}
                    style={{ fontSize: 13 }}
                  >
                    Clear
                  </button>
                  <button
                    className="btn btn--primary"
                    style={{ fontSize: 14, fontWeight: 700, minWidth: 180 }}
                    onClick={handleBatchComposeSend}
                    disabled={batchSending || !batchBody.trim()}
                  >
                    {batchSending
                      ? `Sending… (${selected.size})`
                      : `📤 Send to ${selected.size} lead${selected.size !== 1 ? 's' : ''}`}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
