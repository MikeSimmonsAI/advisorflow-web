import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './CampaignBuilder.css'

const STEP_LABELS = ['Build list', 'Write message', 'Review & send']

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'new', label: 'New' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
]

const TIER_OPTIONS = [
  { value: '', label: 'All tiers' },
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
]

const LEAD_TYPE_OPTIONS = [
  { value: '', label: 'All lead types' },
  { value: 'file_check', label: 'File Check' },
  { value: 'code_lead', label: 'Code Lead' },
  { value: 'new_inquiry', label: 'New Inquiry' },
  { value: 'referral', label: 'Referral' },
  { value: 'web_lead', label: 'Web Lead' },
  { value: 'at_need', label: 'At-Need Contact' },
  { value: 'pre_need', label: 'Pre-Need Interest' },
]

const ENGAGEMENT_OPTIONS = [
  { value: '', label: 'All engagement levels' },
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
  ai_direction: '',
  has_phone: true,
  exclude_dnc: true,
  exclude_duplicates: true,
}

export default function CampaignBuilder() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)

  // Step 1 — filters
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [advisors, setAdvisors] = useState([])
  const [previewLeads, setPreviewLeads] = useState([])
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState('')

  // Step 2 — message
  const [campaignName, setCampaignName] = useState('')
  const [messageText, setMessageText] = useState('')
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [scheduleType, setScheduleType] = useState('now') // 'now' | 'scheduled'
  const [scheduledAt, setScheduledAt] = useState('')

  // Step 3 — send
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState(null)
  const [sendError, setSendError] = useState('')

  useEffect(() => {
    api.get('/admin/users')
      .then(users => setAdvisors(users.filter(u => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch(() => {})
  }, [])

  function setFilter(key, value) {
    setFilters(f => ({ ...f, [key]: value }))
    // Reset preview when filters change
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

  async function handleSend() {
    if (!messageText.trim()) return
    setSending(true)
    setSendError('')
    setSendResult(null)
    try {
      const payload = {
        name: campaignName.trim() || `Campaign ${new Date().toLocaleDateString()}`,
        message_template: messageText.trim(),
        include_booking_link: includeBookingLink,
        lead_ids: previewLeads.map(l => l.id),
        schedule_type: scheduleType,
        scheduled_at: scheduleType === 'scheduled' ? scheduledAt : null,
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
        },
        ai_direction: filters.ai_direction || null,
      }
      const result = await api.post('/campaigns/builder/send', payload)
      setSendResult(result)
    } catch (err) {
      setSendError(err.message || 'Campaign send failed.')
    } finally {
      setSending(false)
    }
  }

  const eligibleCount = previewLeads.length
  const charCount = messageText.length
  const smsSegments = Math.ceil(charCount / 160) || 1

  return (
    <div className="campaign-page">
      <header className="page-header campaign-header">
        <div>
          <p className="campaign-eyebrow">Outreach</p>
          <h1 className="page-title">Campaign Builder</h1>
          <p className="page-subtitle">Filter your leads, write your message, and send to exactly who you want.</p>
        </div>
      </header>

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
              <button className="btn btn--secondary" onClick={() => setFilters(EMPTY_FILTERS)}>Reset</button>
            </div>

            <div className="campaign-filter-grid">
              <label className="settings-label">
                Tier
                <select className="filter-select" value={filters.tier} onChange={e => setFilter('tier', e.target.value)}>
                  {TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>

              <label className="settings-label">
                Status
                <select className="filter-select" value={filters.status} onChange={e => setFilter('status', e.target.value)}>
                  {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>

              <label className="settings-label">
                Advisor
                <select className="filter-select" value={filters.assigned_to_id} onChange={e => setFilter('assigned_to_id', e.target.value)}>
                  <option value="">All advisors</option>
                  <option value="unassigned">Unassigned only</option>
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
                Lead type
                <select className="filter-select" value={filters.lead_type} onChange={e => setFilter('lead_type', e.target.value)}>
                  {LEAD_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
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
                  <strong>{eligibleCount}</strong> leads match
                </span>
              )}
            </div>

            {previewError && <div className="compose-error" style={{ marginTop: 8 }}>{previewError}</div>}
          </section>

          {/* Preview table */}
          {previewLeads.length > 0 && (
            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">Matching leads</h2>
                <span className="panel-count mono">{eligibleCount} total</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Phone</th>
                    <th>Tier</th>
                    <th>Status</th>
                    <th>Advisor</th>
                    <th>Source year</th>
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
                  Showing first 100 of {eligibleCount} — all {eligibleCount} will be included in the campaign.
                </p>
              )}
              <div style={{ padding: '16px 0 0', display: 'flex', justifyContent: 'flex-end' }}>
                <button className="btn btn--primary" onClick={() => setStep(1)} disabled={eligibleCount === 0}>
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

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <label className="settings-label">
                Campaign name (internal only)
                <input
                  className="settings-input"
                  value={campaignName}
                  onChange={e => setCampaignName(e.target.value)}
                  placeholder={`Campaign ${new Date().toLocaleDateString()}`}
                />
              </label>

              <label className="settings-label">
                AI Direction (optional)
                <input
                  className="settings-input"
                  value={filters.ai_direction}
                  onChange={e => setFilter('ai_direction', e.target.value)}
                  placeholder="e.g. This is a file check campaign — ask if they still need pre-need planning"
                />
                <span className="settings-help" style={{ fontSize: 11 }}>
                  Tell the AI what this campaign is about. The more specific, the better the message.
                </span>
              </label>

              <label className="settings-label">
                Message
                <textarea
                  className="compose-textarea"
                  rows={6}
                  value={messageText}
                  onChange={e => setMessageText(e.target.value)}
                  placeholder="Hi {first_name}, this is {advisor_name} from Restland. I wanted to reach out personally…"
                />
                <div className="campaign-char-count">
                  {charCount} chars · {smsSegments} SMS segment{smsSegments !== 1 ? 's' : ''}
                  {charCount > 160 && <span className="campaign-char-warn"> · Keep under 160 for single segment</span>}
                </div>
              </label>

              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn--secondary"
                  style={{ fontSize: 13 }}
                  onClick={async () => {
                    try {
                      const result = await api.post('/campaigns/generate-message', {
                        purpose: filters.lead_type || 'custom',
                        tone: filters.engagement_temperature === 'hot' ? 'direct' : filters.engagement_temperature === 'cold' ? 'soft' : 'warm',
                        lead_type: filters.lead_type || null,
                        ai_direction: filters.ai_direction || null,
                      })
                      if (result.message) setMessageText(result.message)
                    } catch (err) {
                      alert('AI generation failed: ' + err.message)
                    }
                  }}
                >
                  ✨ AI Write Message
                </button>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)', alignSelf: 'center' }}>
                  Uses your lead type and direction above
                </span>
              </div>

              <p className="settings-help">
                Placeholders: <code>&#123;first_name&#125;</code>, <code>&#123;advisor_name&#125;</code>, <code>&#123;booking_link&#125;</code> — filled in per lead at send time.
              </p>

              <label className="compose-checkbox">
                <input type="checkbox" checked={includeBookingLink} onChange={e => setIncludeBookingLink(e.target.checked)} />
                Append booking link to each message
              </label>

              <div className="campaign-schedule-row">
                <label className="compose-checkbox">
                  <input type="radio" name="schedule" checked={scheduleType === 'now'} onChange={() => setScheduleType('now')} />
                  Send immediately
                </label>
                <label className="compose-checkbox">
                  <input type="radio" name="schedule" checked={scheduleType === 'scheduled'} onChange={() => setScheduleType('scheduled')} />
                  Schedule for later
                </label>
                {scheduleType === 'scheduled' && (
                  <input
                    className="settings-input"
                    type="datetime-local"
                    value={scheduledAt}
                    onChange={e => setScheduledAt(e.target.value)}
                    style={{ maxWidth: 240 }}
                  />
                )}
              </div>
            </div>

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
              <div className="campaign-success-icon">✓</div>
              <h2>Campaign {scheduleType === 'scheduled' ? 'scheduled' : 'sent'}!</h2>
              <div className="campaign-result-stats">
                <div className="campaign-result-stat">
                  <strong>{sendResult.sent_count ?? sendResult.queued_count ?? eligibleCount}</strong>
                  <span>{scheduleType === 'scheduled' ? 'Queued' : 'Sent'}</span>
                </div>
                {sendResult.skipped_count > 0 && (
                  <div className="campaign-result-stat">
                    <strong>{sendResult.skipped_count}</strong>
                    <span>Skipped</span>
                  </div>
                )}
                {sendResult.failed_count > 0 && (
                  <div className="campaign-result-stat campaign-result-stat--warn">
                    <strong>{sendResult.failed_count}</strong>
                    <span>Failed</span>
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 24 }}>
                <button className="btn btn--secondary" onClick={() => navigate('/leads')}>View leads</button>
                <button className="btn btn--primary" onClick={() => { setStep(0); setFilters(EMPTY_FILTERS); setMessageText(''); setCampaignName(''); setSendResult(null) }}>
                  New campaign
                </button>
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
                  <strong className="campaign-review-value">{eligibleCount}</strong>
                  <span className="campaign-review-sub">leads matched your filters</span>
                </div>
                <div className="campaign-review-card">
                  <span className="campaign-review-label">Message length</span>
                  <strong className="campaign-review-value">{charCount}</strong>
                  <span className="campaign-review-sub">{smsSegments} SMS segment{smsSegments !== 1 ? 's' : ''}</span>
                </div>
                <div className="campaign-review-card">
                  <span className="campaign-review-label">Send time</span>
                  <strong className="campaign-review-value">{scheduleType === 'now' ? 'Now' : 'Scheduled'}</strong>
                  <span className="campaign-review-sub">{scheduleType === 'scheduled' && scheduledAt ? new Date(scheduledAt).toLocaleString() : scheduleType === 'now' ? 'Immediately on confirm' : 'No time set'}</span>
                </div>
                <div className="campaign-review-card">
                  <span className="campaign-review-label">Booking link</span>
                  <strong className="campaign-review-value">{includeBookingLink ? 'Yes' : 'No'}</strong>
                  <span className="campaign-review-sub">appended to each message</span>
                </div>
              </div>

              <div className="campaign-review-message">
                <p className="campaign-review-label">Message preview</p>
                <div className="campaign-message-preview">
                  <p>{messageText}</p>
                  {includeBookingLink && <p style={{ opacity: 0.5, fontSize: 12, marginTop: 8 }}>[booking link appended]</p>}
                </div>
              </div>

              {/* Filter summary */}
              <div className="campaign-filter-summary">
                <p className="campaign-review-label">Filters applied</p>
                <div className="campaign-filter-tags">
                  {filters.tier && <span className="campaign-filter-tag">Tier: {filters.tier.replaceAll('_', ' ')}</span>}
                  {filters.status && <span className="campaign-filter-tag">Status: {filters.status}</span>}
                  {filters.assigned_to_id && <span className="campaign-filter-tag">Advisor filtered</span>}
                  {filters.source_year_min && <span className="campaign-filter-tag">Year ≥ {filters.source_year_min}</span>}
                  {filters.source_year_max && <span className="campaign-filter-tag">Year ≤ {filters.source_year_max}</span>}
                  {filters.no_contact_days && <span className="campaign-filter-tag">No contact {filters.no_contact_days}+ days</span>}
                  {filters.exclude_dnc && <span className="campaign-filter-tag">DNC excluded</span>}
                  {filters.exclude_duplicates && <span className="campaign-filter-tag">Dupes excluded</span>}
                  {!filters.tier && !filters.status && !filters.assigned_to_id && !filters.source_year_min && !filters.source_year_max && !filters.no_contact_days && (
                    <span className="campaign-filter-tag">All leads (filtered by checkboxes only)</span>
                  )}
                </div>
              </div>

              {sendError && <div className="compose-error" style={{ marginTop: 12 }}>{sendError}</div>}

              <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between', marginTop: 24 }}>
                <button className="btn btn--secondary" onClick={() => setStep(1)}>← Back</button>
                <button
                  className="btn btn--primary"
                  onClick={handleSend}
                  disabled={sending || eligibleCount === 0 || (scheduleType === 'scheduled' && !scheduledAt)}
                  style={{ minWidth: 180 }}
                >
                  {sending
                    ? 'Sending…'
                    : scheduleType === 'scheduled'
                    ? `Schedule to ${eligibleCount} leads`
                    : `Send to ${eligibleCount} leads now`}
                </button>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
