import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './CampaignBuilder.css'

const STEP_LABELS = ['Build list', 'Write message', 'Review & send']

const TONES = [
  { value: 'cold', label: '❄️ Cold', desc: 'Soft intro, low pressure' },
  { value: 'warm', label: '☀️ Warm', desc: 'Friendly, invite a conversation' },
  { value: 'hot', label: '🔥 Direct', desc: 'Confident, clear ask' },
  { value: 'urgent', label: '⚡ Urgent', desc: 'Brief, time-sensitive' },
]

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'new', label: 'New' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
]

const ENGAGEMENT_OPTIONS = [
  { value: '', label: 'All engagement' },
  { value: 'hot', label: '🔥 Hot' },
  { value: 'warm', label: '☀️ Warm' },
  { value: 'cold', label: '❄️ Cold' },
  { value: 'unknown', label: 'Unknown' },
]

const CONTACT_HISTORY_OPTIONS = [
  { value: '', label: 'Any contact history' },
  { value: 'never_contacted', label: 'Never contacted' },
  { value: 'contacted_no_reply', label: 'Contacted — no reply' },
  { value: 'replied_not_booked', label: 'Replied — not booked' },
]

const RELATIONSHIP_TYPE_OPTIONS = [
  { value: '', label: 'All relationship types' },
  { value: 'cold_lead', label: '❄️ Cold leads' },
  { value: 'warm_lead', label: '☀️ Warm leads' },
  { value: 're_engagement', label: '🔄 Re-engagement' },
  { value: 'previous_prospect', label: '📋 Previous prospects' },
  { value: 'past_customer', label: '🤝 Past customers' },
  { value: 'existing_customer', label: '⭐ Existing customers' },
]

const CHANNEL_OPTIONS = [
  { value: 'sms', label: '📱 SMS only (leads with phone)' },
  { value: 'email', label: '✉️ Email only (email-only leads)' },
  { value: 'auto', label: '🔄 Auto (SMS or email by lead type)' },
]

const OFFER_HOOK_OPTIONS = [
  { value: '', label: 'No specific offer (general outreach)' },
  { value: 'lunch_and_learn', label: '🍽 Lunch & Learn event' },
  { value: 'free_tour', label: '🚪 Free funeral home tour' },
  { value: 'free_space', label: '🌿 Free space consultation' },
  { value: 'family_service_consult', label: '🤝 Free Family Service consult' },
]

const EMPTY_FILTERS = {
  tier: '',
  status: '',
  source_year_min: '',
  source_year_max: '',
  assigned_to_id: '',
  no_contact_days: '',
  lead_type: '',
  engagement_temperature: '',
  contact_history: '',
  import_list_name: '',
  relationship_type: '',
  has_phone: true,
  exclude_dnc: true,
  exclude_duplicates: true,
}

function previewMessage(template, advisorName) {
  return (template || '')
    .replace(/{first_name}/g, 'Jane')
    .replace(/{advisor_name}/g, advisorName || 'your advisor')
    .replace(/{booking_url}/g, 'https://book.example.com/xyz')
}

export default function CampaignBuilder() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [activeTab, setActiveTab] = useState('builder') // 'builder' | 'history'

  // Step 1 — filters
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [advisors, setAdvisors] = useState([])
  const [tierOptions, setTierOptions] = useState([{ value: '', label: 'All tiers' }])
  const [purposeOptions, setPurposeOptions] = useState([])
  const [previewLeads, setPreviewLeads] = useState([])
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState('')

  const [importBatches, setImportBatches] = useState([])

  // Step 2 — message
  const [campaignName, setCampaignName] = useState('')
  const [purpose, setPurpose] = useState('custom')
  const [tone, setTone] = useState('warm')
  const [channel, setChannel] = useState('sms')
  const [offerHook, setOfferHook] = useState('')
  const [messageText, setMessageText] = useState('')
  const [aiDirection, setAiDirection] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [advisorName, setAdvisorName] = useState('')

  // Step 3 — send
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState(null)
  const [sendError, setSendError] = useState('')

  // History tab
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)

  useEffect(() => {
    // Load advisors
    api.get('/admin/users')
      .then(users => {
        const me = getCurrentUser()
        const active = users.filter(u =>
          u.is_active &&
          (u.role === 'advisor' || u.role === 'org_admin') &&
          u.organization_id === me?.organization_id
        )
        setAdvisors(active)
      })
      .catch(() => {})

    // Load tiers from TierDefinitions (not org-settings)
    api.get('/tier-definitions')
      .then(tiers => {
        if (Array.isArray(tiers) && tiers.length) {
          setTierOptions([
            { value: '', label: 'All tiers' },
            ...tiers.filter(t => t.is_active).sort((a, b) => a.sort_order - b.sort_order).map(t => ({
              value: t.tier_key,
              label: t.tier_label,
            })),
          ])
        }
      })
      .catch(() => {})

    // Load campaign purposes for this org's industry
    api.get('/campaigns/purposes')
      .then(purposes => {
        if (Array.isArray(purposes)) {
          setPurposeOptions(purposes)
          if (purposes.length) setPurpose(purposes[0].value)
        }
      })
      .catch(() => {})

    // Get advisor name for message preview
    const raw = localStorage.getItem('bookaboost_user')
    if (raw) {
      try { setAdvisorName(JSON.parse(raw).full_name || '') } catch {}
    }

    // Load import batches for the batch filter
    api.get('/leads/import-batches').then(setImportBatches).catch(() => {})
  }, [])

  function loadHistory() {
    setHistoryLoading(true)
    api.get('/campaigns/history')
      .then(data => setHistory(data || []))
      .catch(() => {})
      .finally(() => setHistoryLoading(false))
  }

  function setFilter(key, value) {
    setFilters(f => ({ ...f, [key]: value }))
    setPreviewLeads([])
    setPreviewError('')
  }

  async function handlePreview() {
    setPreviewing(true)
    setPreviewError('')
    setPreviewLeads([])
    try {
      const params = new URLSearchParams()
      if (filters.tier) params.set('tier', filters.tier)
      if (filters.status) params.set('status', filters.status)
      if (filters.source_year_min) params.set('source_year_min', filters.source_year_min)
      if (filters.source_year_max) params.set('source_year_max', filters.source_year_max)
      if (filters.assigned_to_id) params.set('assigned_to_id', filters.assigned_to_id)
      if (filters.no_contact_days) params.set('no_contact_days', filters.no_contact_days)
      if (filters.lead_type) params.set('lead_type', filters.lead_type)
      if (filters.engagement_temperature) params.set('engagement_temperature', filters.engagement_temperature)
      if (filters.contact_history) params.set('contact_history', filters.contact_history)
      if (filters.import_list_name) params.set('import_list_name', filters.import_list_name)
      if (filters.relationship_type) params.set('relationship_type', filters.relationship_type)
      if (channel) params.set('channel', channel)
      params.set('has_phone', filters.has_phone ? 'true' : 'false')
      params.set('exclude_dnc', filters.exclude_dnc ? 'true' : 'false')
      params.set('exclude_duplicates', filters.exclude_duplicates ? 'true' : 'false')
      const data = await api.get(`/campaigns/builder/preview?${params.toString()}`)
      setPreviewLeads(data || [])
    } catch (err) {
      setPreviewError(err.message || 'Could not preview leads.')
    } finally {
      setPreviewing(false)
    }
  }

  async function handleGenerateMessage() {
    setGenerating(true)
    try {
      const result = await api.post('/campaigns/generate-message', {
        purpose,
        tone,
        lead_type: filters.lead_type || null,
        ai_direction: aiDirection || null,
        offer_hook: offerHook || null,
      })
      if (result.message) setMessageText(result.message)
    } catch (err) {
      setSendError('AI generation failed: ' + (err.message || 'unknown error'))
    } finally {
      setGenerating(false)
    }
  }

  async function handleSend() {
    if (!messageText.trim()) return
    setSending(true)
    setSendError('')
    setSendResult(null)
    try {
      const result = await api.post('/campaigns/builder/send', {
        name: campaignName.trim() || `Campaign ${new Date().toLocaleDateString()}`,
        purpose,
        tone,
        channel,
        offer_hook: offerHook || null,
        message_template: messageText.trim(),
        include_booking_link: includeBookingLink,
        lead_ids: previewLeads.map(l => l.id),
        filters: {
          tier: filters.tier || null,
          status: filters.status || null,
          source_year_min: filters.source_year_min ? Number(filters.source_year_min) : null,
          source_year_max: filters.source_year_max ? Number(filters.source_year_max) : null,
          assigned_to_id: filters.assigned_to_id || null,
          no_contact_days: filters.no_contact_days ? Number(filters.no_contact_days) : null,
          has_phone: filters.has_phone,
          exclude_dnc: filters.exclude_dnc,
          exclude_duplicates: filters.exclude_duplicates,
          lead_type: filters.lead_type || null,
          engagement_temperature: filters.engagement_temperature || null,
          contact_history: filters.contact_history || null,
          import_list_name: filters.import_list_name || null,
          relationship_type: filters.relationship_type || null,
        },
        ai_direction: aiDirection || null,
      })
      setSendResult(result)
    } catch (err) {
      setSendError(err.message || 'Campaign send failed.')
    } finally {
      setSending(false)
    }
  }

  function resetAll() {
    setStep(0)
    setFilters(EMPTY_FILTERS)
    setCampaignName('')
    setMessageText('')
    setAiDirection('')
    setTone('warm')
    setChannel('sms')
    setOfferHook('')
    setPreviewLeads([])
    setSendResult(null)
    setSendError('')
    if (purposeOptions.length) setPurpose(purposeOptions[0].value)
  }

  const eligibleCount = previewLeads.length
  const charCount = messageText.length
  const smsSegments = Math.ceil(charCount / 160) || 1
  const previewText = previewMessage(messageText, advisorName)

  return (
    <div className="campaign-page">
      <header className="page-header campaign-header">
        <div>
          <p className="campaign-eyebrow">Outreach</p>
          <h1 className="page-title">Campaign Builder</h1>
          <p className="page-subtitle">Filter your leads, write your message, and send to exactly who you want.</p>
        </div>
      </header>

      {/* Tab bar */}
      <div className="asq-tabs" style={{ marginBottom: 20 }}>
        <button
          className={`tab ${activeTab === 'builder' ? 'tab--active' : ''}`}
          onClick={() => setActiveTab('builder')}
        >
          New Campaign
        </button>
        <button
          className={`tab ${activeTab === 'history' ? 'tab--active' : ''}`}
          onClick={() => { setActiveTab('history'); if (!history.length) loadHistory() }}
        >
          History
        </button>
      </div>

      {/* ── History tab ── */}
      {activeTab === 'history' && (
        historyLoading ? (
          <div className="empty-state">Loading history…</div>
        ) : history.length === 0 ? (
          <div className="panel" style={{ padding: '32px', textAlign: 'center', color: 'var(--text-secondary)' }}>
            No campaigns sent yet. Run your first campaign to see results here.
          </div>
        ) : (
          <section className="panel">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Date</th>
                  <th>Purpose</th>
                  <th style={{ textAlign: 'right' }}>Sent</th>
                  <th style={{ textAlign: 'right' }}>Skipped</th>
                  <th style={{ textAlign: 'right' }}>Errors</th>
                </tr>
              </thead>
              <tbody>
                {history.map(c => (
                  <tr key={c.id}>
                    <td style={{ fontWeight: 600 }}>{c.name}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {c.sent_at
                        ? new Date(c.sent_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
                        : c.created_at
                          ? new Date(c.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
                          : '—'}
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{c.purpose?.replaceAll('_', ' ') || '—'}</td>
                    <td style={{ textAlign: 'right', color: 'var(--signal-green)', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                      {c.sent_count ?? '—'}
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
                      {c.skipped_count ?? '—'}
                    </td>
                    <td style={{ textAlign: 'right', color: c.error_count > 0 ? 'var(--signal-red)' : 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
                      {c.error_count ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )
      )}

      {/* ── Builder tab ── */}
      {activeTab === 'builder' && (
        <>
          {/* Step indicator */}
          <div className="campaign-steps">
            {STEP_LABELS.map((label, i) => (
              <div
                key={i}
                className={`campaign-step ${i === step ? 'campaign-step--active' : ''} ${i < step ? 'campaign-step--done' : ''}`}
                onClick={() => i < step && setStep(i)}
                style={{ cursor: i < step ? 'pointer' : 'default' }}
              >
                <div className="campaign-step-num">{i < step ? '✓' : i + 1}</div>
                <span>{label}</span>
              </div>
            ))}
            <div className="campaign-step-line" />
          </div>

          {/* ── Step 1: Build list ── */}
          {step === 0 && (
            <div className="campaign-body">
              <section className="panel campaign-filter-panel">
                <div className="panel-header">
                  <h2 className="panel-title">Filter leads</h2>
                  <button className="btn btn--secondary" onClick={() => { setFilters(EMPTY_FILTERS); setPreviewLeads([]) }}>Reset</button>
                </div>

                <div className="campaign-filter-grid">
                  <label className="settings-label">
                    Tier
                    <select className="filter-select" value={filters.tier} onChange={e => setFilter('tier', e.target.value)}>
                      {tierOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    Status
                    <select className="filter-select" value={filters.status} onChange={e => setFilter('status', e.target.value)}>
                      {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    Engagement level
                    <select className="filter-select" value={filters.engagement_temperature} onChange={e => setFilter('engagement_temperature', e.target.value)}>
                      {ENGAGEMENT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    Contact history
                    <select className="filter-select" value={filters.contact_history} onChange={e => setFilter('contact_history', e.target.value)}>
                      {CONTACT_HISTORY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    Advisor
                    <select className="filter-select" value={filters.assigned_to_id} onChange={e => setFilter('assigned_to_id', e.target.value)}>
                      <option value="">All advisors</option>
                      {advisors.map(a => <option key={a.id} value={a.id}>{a.full_name}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    No contact for (days)
                    <input
                      className="settings-input"
                      type="number"
                      min="1"
                      placeholder="e.g. 30"
                      value={filters.no_contact_days}
                      onChange={e => setFilter('no_contact_days', e.target.value)}
                    />
                  </label>

                  <label className="settings-label">
                    Source year from
                    <input
                      className="settings-input"
                      type="number"
                      placeholder="e.g. 2018"
                      value={filters.source_year_min}
                      onChange={e => setFilter('source_year_min', e.target.value)}
                    />
                  </label>

                  <label className="settings-label">
                    Source year to
                    <input
                      className="settings-input"
                      type="number"
                      placeholder="e.g. 2022"
                      value={filters.source_year_max}
                      onChange={e => setFilter('source_year_max', e.target.value)}
                    />
                  </label>

                  <label className="settings-label">
                    Import batch / list name
                    <select className="filter-select" value={filters.import_list_name} onChange={e => setFilter('import_list_name', e.target.value)}>
                      <option value="">All import batches</option>
                      {importBatches.map(b => (
                        <option key={b.source_file} value={b.import_list_name || b.source_file}>
                          {b.import_list_name || b.source_file}{b.lead_count ? ` (${b.lead_count})` : ''}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="settings-label">
                    Relationship type
                    <select className="filter-select" value={filters.relationship_type} onChange={e => setFilter('relationship_type', e.target.value)}>
                      {RELATIONSHIP_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="settings-label">
                    Channel
                    <select className="filter-select" value={channel} onChange={e => setChannel(e.target.value)}>
                      {CHANNEL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>
                </div>

                <div className="campaign-checkbox-row">
                  <label className="compose-checkbox">
                    <input type="checkbox" checked={filters.has_phone} onChange={e => setFilter('has_phone', e.target.checked)} />
                    Has phone number
                  </label>
                  <label className="compose-checkbox">
                    <input type="checkbox" checked={filters.exclude_dnc} onChange={e => setFilter('exclude_dnc', e.target.checked)} />
                    Exclude DNC
                  </label>
                  <label className="compose-checkbox">
                    <input type="checkbox" checked={filters.exclude_duplicates} onChange={e => setFilter('exclude_duplicates', e.target.checked)} />
                    Exclude duplicates
                  </label>
                </div>

                <div className="campaign-preview-actions">
                  <button className="btn btn--primary" onClick={handlePreview} disabled={previewing}>
                    {previewing ? 'Loading…' : 'Preview matching leads'}
                  </button>
                  {previewLeads.length > 0 && (
                    <span className="campaign-match-count">
                      <strong>{eligibleCount.toLocaleString()}</strong> leads match
                    </span>
                  )}
                </div>
                {previewError && <div className="compose-error" style={{ marginTop: 8 }}>{previewError}</div>}
              </section>

              {previewLeads.length > 0 && (
                <section className="panel">
                  <div className="panel-header">
                    <h2 className="panel-title">Matching leads</h2>
                    <span className="panel-count mono">{eligibleCount.toLocaleString()} total</span>
                  </div>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Phone</th>
                        <th>Tier</th>
                        <th>Status</th>
                        <th>Advisor</th>
                        <th>Year</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewLeads.slice(0, 100).map(lead => (
                        <tr key={lead.id} onClick={() => navigate(`/leads/${lead.id}`)} style={{ cursor: 'pointer' }}>
                          <td>{lead.first_name} {lead.last_name}</td>
                          <td className="mono">{lead.phone || '–'}</td>
                          <td><TierBadge tier={lead.tier} /></td>
                          <td><StatusBadge status={lead.status} /></td>
                          <td style={{ fontSize: 12 }}>{lead.assigned_to_name || '–'}</td>
                          <td className="mono" style={{ fontSize: 12 }}>{lead.source_year || '–'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {eligibleCount > 100 && (
                    <p style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '8px 0', textAlign: 'center' }}>
                      Showing first 100 of {eligibleCount.toLocaleString()} — all {eligibleCount.toLocaleString()} will be included.
                    </p>
                  )}
                  <div style={{ padding: '16px 0 0', display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn btn--primary" onClick={() => setStep(1)}>
                      Next: Write message →
                    </button>
                  </div>
                </section>
              )}
            </div>
          )}

          {/* ── Step 2: Write message ── */}
          {step === 1 && (
            <div className="campaign-body">
              <section className="panel">
                <div className="panel-header">
                  <h2 className="panel-title">Campaign message</h2>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                  <div className="campaign-filter-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                    <label className="settings-label">
                      Campaign name (internal)
                      <input
                        className="settings-input"
                        value={campaignName}
                        onChange={e => setCampaignName(e.target.value)}
                        placeholder={`Campaign ${new Date().toLocaleDateString()}`}
                      />
                    </label>
                    <label className="settings-label">
                      Campaign type
                      <select
                        className="filter-select"
                        value={purpose}
                        onChange={e => setPurpose(e.target.value)}
                      >
                        {purposeOptions.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                        <option value="custom">Custom</option>
                      </select>
                    </label>
                  </div>

                  {/* Tone selector */}
                  <div>
                    <div className="settings-label" style={{ marginBottom: 8 }}>Tone</div>
                    <div className="campaign-tone-row">
                      {TONES.map(t => (
                        <button
                          key={t.value}
                          className={`campaign-tone-btn ${tone === t.value ? 'campaign-tone-btn--active' : ''}`}
                          onClick={() => setTone(t.value)}
                          title={t.desc}
                        >
                          {t.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <label className="settings-label">
                    AI direction (optional)
                    <input
                      className="settings-input"
                      value={aiDirection}
                      onChange={e => setAiDirection(e.target.value)}
                      placeholder="e.g. This is a file check campaign — ask if they still need pre-need planning"
                    />
                    <span className="settings-help" style={{ fontSize: 11 }}>
                      The more specific you are, the better the AI message.
                    </span>
                  </label>

                  <label className="settings-label">
                    Offer hook (AI will weave this naturally into the message)
                    <select className="filter-select" value={offerHook} onChange={e => setOfferHook(e.target.value)}>
                      {OFFER_HOOK_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <span className="settings-help" style={{ fontSize: 11 }}>
                      The AI will include this as a soft, low-pressure invite — not the entire focus of the message.
                    </span>
                  </label>

                  <div>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                      <button
                        className="btn btn--secondary"
                        style={{ fontSize: 13 }}
                        onClick={handleGenerateMessage}
                        disabled={generating}
                      >
                        {generating ? '⏳ Writing…' : '✨ AI Write Message'}
                      </button>
                      <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                        Uses your campaign type, tone, direction, and offer hook above
                      </span>
                    </div>
                    <textarea
                      className="compose-textarea"
                      rows={5}
                      value={messageText}
                      onChange={e => setMessageText(e.target.value)}
                      placeholder="Hi {first_name}, this is {advisor_name}…   Use {first_name}, {advisor_name}, {booking_url} as placeholders."
                    />
                    <div className="campaign-char-count">
                      {charCount} chars · {smsSegments} SMS segment{smsSegments !== 1 ? 's' : ''}
                      {charCount > 160 && <span className="campaign-char-warn"> · Keep under 160 for a single segment</span>}
                    </div>
                  </div>

                  {/* Live preview */}
                  {messageText.trim() && (
                    <div className="cb-preview-wrap">
                      <div className="cb-preview-label">Preview — as Jane will see it</div>
                      <div className="cb-preview-bubble">
                        {previewText}
                        {includeBookingLink && !/{booking_url}/i.test(messageText) && (
                          <span style={{ opacity: 0.55, fontSize: 12 }}> https://book.example.com/xyz</span>
                        )}
                      </div>
                    </div>
                  )}

                  <label className="compose-checkbox">
                    <input type="checkbox" checked={includeBookingLink} onChange={e => setIncludeBookingLink(e.target.checked)} />
                    Append booking link if not already in message
                  </label>
                </div>

                {sendError && <div className="compose-error" style={{ marginTop: 12 }}>{sendError}</div>}

                <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between', marginTop: 24 }}>
                  <button className="btn btn--secondary" onClick={() => setStep(0)}>← Back</button>
                  <button
                    className="btn btn--primary"
                    onClick={() => setStep(2)}
                    disabled={!messageText.trim()}
                  >
                    Next: Review & send →
                  </button>
                </div>
              </section>
            </div>
          )}

          {/* ── Step 3: Review & send ── */}
          {step === 2 && (
            <div className="campaign-body">
              {sendResult ? (
                <section className="panel campaign-success">
                  <div className="campaign-success-icon">🚀</div>
                  <h2>Campaign sent!</h2>
                  <div className="campaign-result-stats">
                    <div className="campaign-result-stat">
                      <strong style={{ color: 'var(--signal-green)' }}>{sendResult.sent}</strong>
                      <span>Sent</span>
                    </div>
                    {sendResult.skipped > 0 && (
                      <div className="campaign-result-stat">
                        <strong style={{ color: 'var(--signal-amber)' }}>{sendResult.skipped}</strong>
                        <span>Skipped</span>
                      </div>
                    )}
                    {sendResult.errors > 0 && (
                      <div className="campaign-result-stat campaign-result-stat--warn">
                        <strong>{sendResult.errors}</strong>
                        <span>Errors</span>
                      </div>
                    )}
                    <div className="campaign-result-stat">
                      <strong>{sendResult.total}</strong>
                      <span>Total leads</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 24 }}>
                    <button className="btn btn--secondary" onClick={() => navigate('/leads')}>View leads</button>
                    <button className="btn btn--primary" onClick={resetAll}>New campaign</button>
                  </div>
                </section>
              ) : (
                <section className="panel">
                  <div className="panel-header">
                    <h2 className="panel-title">Review & confirm</h2>
                  </div>

                  <div className="campaign-review-grid">
                    <div className="campaign-review-card">
                      <span className="campaign-review-label">Recipients</span>
                      <strong className="campaign-review-value">{eligibleCount.toLocaleString()}</strong>
                      <span className="campaign-review-sub">leads matched your filters</span>
                    </div>
                    <div className="campaign-review-card">
                      <span className="campaign-review-label">Message length</span>
                      <strong className="campaign-review-value">{charCount}</strong>
                      <span className="campaign-review-sub">{smsSegments} SMS segment{smsSegments !== 1 ? 's' : ''}</span>
                    </div>
                    <div className="campaign-review-card">
                      <span className="campaign-review-label">Tone</span>
                      <strong className="campaign-review-value">{TONES.find(t => t.value === tone)?.label || tone}</strong>
                      <span className="campaign-review-sub">{purposeOptions.find(p => p.value === purpose)?.label || purpose}</span>
                    </div>
                    <div className="campaign-review-card">
                      <span className="campaign-review-label">Booking link</span>
                      <strong className="campaign-review-value">{includeBookingLink ? 'Yes' : 'No'}</strong>
                      <span className="campaign-review-sub">appended per message</span>
                    </div>
                  </div>

                  {/* Message preview */}
                  <div className="campaign-review-message" style={{ marginTop: 20 }}>
                    <p className="campaign-review-label">Message preview</p>
                    <div className="cb-preview-bubble" style={{ marginTop: 8 }}>
                      {previewText}
                      {includeBookingLink && !/{booking_url}/i.test(messageText) && (
                        <span style={{ opacity: 0.55, fontSize: 12 }}> https://book.example.com/xyz</span>
                      )}
                    </div>
                  </div>

                  {/* Filter summary */}
                  <div className="campaign-filter-summary" style={{ marginTop: 16 }}>
                    <p className="campaign-review-label">Filters applied</p>
                    <div className="campaign-filter-tags">
                      {filters.tier && <span className="campaign-filter-tag">Tier: {tierOptions.find(t => t.value === filters.tier)?.label || filters.tier}</span>}
                      {filters.status && <span className="campaign-filter-tag">Status: {filters.status}</span>}
                      {filters.engagement_temperature && <span className="campaign-filter-tag">Engagement: {filters.engagement_temperature}</span>}
                      {filters.contact_history && <span className="campaign-filter-tag">{CONTACT_HISTORY_OPTIONS.find(o => o.value === filters.contact_history)?.label}</span>}
                      {filters.assigned_to_id && <span className="campaign-filter-tag">Advisor filtered</span>}
                      {filters.source_year_min && <span className="campaign-filter-tag">Year ≥ {filters.source_year_min}</span>}
                      {filters.source_year_max && <span className="campaign-filter-tag">Year ≤ {filters.source_year_max}</span>}
                      {filters.no_contact_days && <span className="campaign-filter-tag">No contact {filters.no_contact_days}+ days</span>}
                      {filters.exclude_dnc && <span className="campaign-filter-tag">DNC excluded</span>}
                      {filters.exclude_duplicates && <span className="campaign-filter-tag">Dupes excluded</span>}
                    </div>
                  </div>

                  {sendError && <div className="compose-error" style={{ marginTop: 12 }}>{sendError}</div>}

                  <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between', marginTop: 24 }}>
                    <button className="btn btn--secondary" onClick={() => setStep(1)}>← Back</button>
                    <button
                      className="btn btn--primary"
                      onClick={handleSend}
                      disabled={sending || eligibleCount === 0}
                      style={{ minWidth: 200 }}
                    >
                      {sending ? 'Sending…' : `🚀 Send to ${eligibleCount.toLocaleString()} leads`}
                    </button>
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
