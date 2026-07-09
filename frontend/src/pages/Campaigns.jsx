import { useEffect, useState, useMemo } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Campaigns.css'

const TONES = [
  { value: 'cold', label: '❄️ Cold', desc: 'Soft intro, no pressure' },
  { value: 'warm', label: '☀️ Warm', desc: 'Friendly, suggest meeting' },
  { value: 'hot', label: '🔥 Hot', desc: 'Direct, clear ask' },
  { value: 'urgent', label: '⚡ Urgent', desc: 'Brief, time-sensitive' },
]

const CONTACT_HISTORY_OPTIONS = [
  { value: '', label: 'Any contact history' },
  { value: 'never_contacted', label: 'Never contacted' },
  { value: 'contacted_no_reply', label: 'Contacted but no reply' },
  { value: 'replied_not_booked', label: 'Replied but not booked' },
]

const TIER_OPTIONS = [
  { value: '', label: 'Any tier' },
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'email_only', label: 'Email Only' },
  { value: 'partial', label: 'Partial / Needs Review' },
]

const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'new', label: 'New' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
  { value: 'dead', label: 'Dead' },
]

const CHANNEL_OPTIONS = [
  { value: '', label: 'Any channel' },
  { value: 'sms', label: 'SMS only' },
  { value: 'email_only', label: 'Email only' },
]

const STEPS = ['Purpose', 'Audience', 'Message', 'Review & Send']

export default function Campaigns() {
  const [step, setStep] = useState(0)
  const [purposes, setPurposes] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [advisors, setAdvisors] = useState([])
  const [cadenceTemplates, setCadenceTemplates] = useState([])
  const [loading, setLoading] = useState(true)

  // Step 1 — Purpose
  const [purpose, setPurpose] = useState('')
  const [campaignName, setCampaignName] = useState('')

  // Step 2 — Audience filters
  const [tier, setTier] = useState('')
  const [status, setStatus] = useState('')
  const [sourceYear, setSourceYear] = useState('')
  const [sourceFile, setSourceFile] = useState('')
  const [channel, setChannel] = useState('')
  const [advisorId, setAdvisorId] = useState('')
  const [contactHistory, setContactHistory] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)

  // Step 3 — Message
  const [tone, setTone] = useState('warm')
  const [message, setMessage] = useState('')
  const [generating, setGenerating] = useState(false)
  const [startCadence, setStartCadence] = useState(false)
  const [cadenceTemplateId, setCadenceTemplateId] = useState('')
  const [autoReply, setAutoReply] = useState(false)

  // Step 4 — Send
  const [savedCampaignId, setSavedCampaignId] = useState(null)
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      api.get('/campaigns/purposes').catch(() => []),
      api.get('/campaigns').catch(() => []),
      api.get('/admin/users').catch(() => []),
      api.get('/cadence-templates/').catch(() => []),
    ]).then(([purposeData, campaignData, userData, templateData]) => {
      setPurposes(purposeData)
      setCampaigns(campaignData)
      setAdvisors(userData.filter(u => u.role === 'advisor' || u.role === 'org_admin'))
      setCadenceTemplates(templateData)
      setLoading(false)
    })
  }, [])

  const filterCriteria = useMemo(() => {
    const c = {}
    if (tier) c.tier = tier
    if (status) c.status = status
    if (sourceYear) c.source_year = parseInt(sourceYear)
    if (sourceFile) c.source_file = sourceFile
    if (channel) c.channel = channel
    if (advisorId) c.advisor_id = advisorId
    if (contactHistory) c.contact_history = contactHistory
    return c
  }, [tier, status, sourceYear, sourceFile, channel, advisorId, contactHistory])

  async function handlePreview() {
    setPreviewing(true)
    setPreview(null)
    try {
      const result = await api.post('/campaigns/preview', { filter_criteria: filterCriteria, purpose, tone })
      setPreview(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setPreviewing(false)
    }
  }

  async function handleGenerateMessage() {
    if (!purpose) { setError('Select a campaign purpose first.'); return }
    setGenerating(true)
    setError('')
    try {
      const result = await api.post('/campaigns/generate-message', { purpose, tone })
      setMessage(result.message)
    } catch (err) {
      setError('Could not generate message. You can write it manually.')
    } finally {
      setGenerating(false)
    }
  }

  async function handleSaveAndReview() {
    if (!campaignName.trim()) { setError('Give your campaign a name.'); return }
    if (!purpose) { setError('Select a campaign purpose.'); return }
    setError('')
    try {
      const result = await api.post('/campaigns', {
        name: campaignName,
        purpose,
        filter_criteria: filterCriteria,
        tone,
        cadence_template_id: cadenceTemplateId || null,
        auto_reply: autoReply,
      })
      setSavedCampaignId(result.id)
      setStep(3)
    } catch (err) {
      setError(err.message || 'Could not save campaign.')
    }
  }

  async function handleSend() {
    if (!savedCampaignId || !message.trim()) return
    setSending(true)
    setError('')
    try {
      const result = await api.post(`/campaigns/${savedCampaignId}/send`, {
        campaign_id: savedCampaignId,
        message,
        start_cadence: startCadence,
        cadence_template_id: cadenceTemplateId || null,
        auto_reply: autoReply,
      })
      setSendResult(result)
    } catch (err) {
      setError(err.message || 'Send failed.')
    } finally {
      setSending(false)
    }
  }

  function reset() {
    setStep(0); setPurpose(''); setCampaignName(''); setTier(''); setStatus('')
    setSourceYear(''); setSourceFile(''); setChannel(''); setAdvisorId('')
    setContactHistory(''); setPreview(null); setTone('warm'); setMessage('')
    setStartCadence(false); setCadenceTemplateId(''); setAutoReply(false)
    setSavedCampaignId(null); setSendResult(null); setError('')
  }

  if (sendResult) {
    return (
      <div>
        <header className="page-header">
          <h1 className="page-title">Campaign sent</h1>
        </header>
        <div className="panel campaign-success">
          <div className="campaign-success-icon">🚀</div>
          <h2 className="campaign-success-title">Campaign launched!</h2>
          <div className="campaign-result-grid">
            <div className="campaign-result-stat"><span>Sent</span><strong style={{ color: 'var(--signal-green)' }}>{sendResult.sent}</strong></div>
            <div className="campaign-result-stat"><span>Skipped</span><strong style={{ color: 'var(--signal-amber)' }}>{sendResult.skipped}</strong></div>
            <div className="campaign-result-stat"><span>Errors</span><strong style={{ color: sendResult.errors > 0 ? 'var(--signal-red)' : 'var(--text-secondary)' }}>{sendResult.errors}</strong></div>
            <div className="campaign-result-stat"><span>Total</span><strong>{sendResult.total}</strong></div>
          </div>
          {autoReply && <p className="campaign-auto-note">✓ AI auto-reply is active — leads who respond will get automatic follow-ups until they book.</p>}
          <button className="btn btn--primary" onClick={reset}>Start new campaign</button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Campaign Builder</h1>
          <p className="page-subtitle">AI-driven outreach — target the right leads with the right message.</p>
        </div>
      </header>

      <div className="campaign-steps">
        {STEPS.map((label, i) => (
          <div key={label} className={`campaign-step ${i === step ? 'campaign-step--active' : i < step ? 'campaign-step--done' : ''}`}
            onClick={() => i < step && setStep(i)} style={{ cursor: i < step ? 'pointer' : 'default' }}>
            <div className="campaign-step-dot">{i < step ? '✓' : i + 1}</div>
            <span>{label}</span>
          </div>
        ))}
      </div>

      {error && <div className="campaign-error">{error}</div>}

      {step === 0 && (
        <section className="panel campaign-section">
          <h2 className="campaign-section-title">What is this campaign about?</h2>
          <label className="campaign-label">Campaign name
            <input className="campaign-input" value={campaignName} onChange={(e) => setCampaignName(e.target.value)} placeholder="e.g. July Pre-Need Push, Memorial Day Outreach" />
          </label>
          <div className="campaign-purpose-grid">
            {loading ? <div className="empty-state">Loading…</div> : purposes.map((p) => (
              <button key={p.value} className={`campaign-purpose-card ${purpose === p.value ? 'campaign-purpose-card--active' : ''}`} onClick={() => setPurpose(p.value)}>
                <span className="campaign-purpose-label">{p.label}</span>
                <span className="campaign-purpose-desc">{p.desc}</span>
              </button>
            ))}
          </div>
          <div className="campaign-nav">
            <button className="btn btn--primary" onClick={() => { if (!campaignName.trim()) { setError('Give your campaign a name.'); return } if (!purpose) { setError('Select a purpose.'); return } setError(''); setStep(1) }}>
              Next: Choose audience →
            </button>
          </div>
        </section>
      )}

      {step === 1 && (
        <section className="panel campaign-section">
          <h2 className="campaign-section-title">Who should receive this campaign?</h2>
          <div className="campaign-filters-grid">
            <label className="campaign-label">Tier
              <select className="campaign-input" value={tier} onChange={(e) => setTier(e.target.value)}>
                {TIER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="campaign-label">Status
              <select className="campaign-input" value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="campaign-label">Contact history
              <select className="campaign-input" value={contactHistory} onChange={(e) => setContactHistory(e.target.value)}>
                {CONTACT_HISTORY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="campaign-label">Channel
              <select className="campaign-input" value={channel} onChange={(e) => setChannel(e.target.value)}>
                {CHANNEL_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="campaign-label">Source year
              <input className="campaign-input" type="number" value={sourceYear} onChange={(e) => setSourceYear(e.target.value)} placeholder="e.g. 2022" />
            </label>
            <label className="campaign-label">Source file (partial match)
              <input className="campaign-input" value={sourceFile} onChange={(e) => setSourceFile(e.target.value)} placeholder="e.g. restland_export" />
            </label>
            <label className="campaign-label">Advisor
              <select className="campaign-input" value={advisorId} onChange={(e) => setAdvisorId(e.target.value)}>
                <option value="">Any advisor</option>
                {advisors.map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
              </select>
            </label>
          </div>

          <button className="btn btn--secondary" onClick={handlePreview} disabled={previewing}>
            {previewing ? 'Previewing…' : '👁 Preview matching leads'}
          </button>

          {preview && (
            <div className="campaign-preview-box">
              <div className="campaign-preview-count">
                <strong style={{ color: preview.total_matched > 0 ? 'var(--signal-green)' : 'var(--signal-amber)' }}>
                  {preview.total_matched.toLocaleString()}
                </strong> leads match these filters
              </div>
              {preview.sample.length > 0 && (
                <table className="data-table campaign-preview-table">
                  <thead><tr><th>Name</th><th>Tier</th><th>Status</th><th>Source</th></tr></thead>
                  <tbody>
                    {preview.sample.map((l) => (
                      <tr key={l.id}>
                        <td>{l.name || '—'}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{l.tier || '—'}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{l.status || '—'}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{l.source_file ? l.source_file.slice(0, 20) : '—'}{l.source_year ? ` (${l.source_year})` : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {preview.total_matched > 10 && <p className="campaign-preview-note">Showing 10 of {preview.total_matched} matches</p>}
            </div>
          )}

          <div className="campaign-nav">
            <button className="btn btn--secondary" onClick={() => setStep(0)}>← Back</button>
            <button className="btn btn--primary" onClick={() => { if (!preview) { handlePreview().then(() => setStep(2)) } else setStep(2) }}>
              Next: Write message →
            </button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="panel campaign-section">
          <h2 className="campaign-section-title">Craft your message</h2>

          <div className="campaign-tone-row">
            {TONES.map((t) => (
              <button key={t.value} className={`campaign-tone-btn ${tone === t.value ? 'campaign-tone-btn--active' : ''}`} onClick={() => setTone(t.value)} title={t.desc}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="campaign-ai-row">
            <button className="btn btn--secondary" onClick={handleGenerateMessage} disabled={generating}>
              {generating ? '⏳ Writing…' : '✨ AI write message'}
            </button>
            <span className="campaign-ai-hint">AI writes an opening message based on your campaign purpose and tone. You can edit it.</span>
          </div>

          <textarea
            className="campaign-textarea"
            placeholder="Your message here — use {first_name} and {booking_url} as variables"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={5}
          />

          <div className="campaign-char-count" style={{ color: message.length > 320 ? 'var(--signal-red)' : 'var(--text-tertiary)' }}>
            {message.length} / 320 characters
          </div>

          <div className="campaign-options">
            <label className="campaign-check-label">
              <input type="checkbox" checked={startCadence} onChange={(e) => setStartCadence(e.target.checked)} />
              Start cadence after sending
            </label>
            {startCadence && cadenceTemplates.length > 0 && (
              <select className="campaign-input" value={cadenceTemplateId} onChange={(e) => setCadenceTemplateId(e.target.value)}>
                <option value="">Use default cadence</option>
                {cadenceTemplates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            )}
            <label className="campaign-check-label">
              <input type="checkbox" checked={autoReply} onChange={(e) => setAutoReply(e.target.checked)} />
              Enable AI auto-reply — AI continues conversation until lead books
            </label>
            {autoReply && (
              <div className="campaign-auto-info">
                When a lead replies, AI will automatically respond based on the conversation context. You'll be notified of hot replies. Auto-reply stops when the lead books or opts out.
              </div>
            )}
          </div>

          <div className="campaign-nav">
            <button className="btn btn--secondary" onClick={() => setStep(1)}>← Back</button>
            <button className="btn btn--primary" onClick={handleSaveAndReview} disabled={!message.trim()}>
              Review & send →
            </button>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="panel campaign-section">
          <h2 className="campaign-section-title">Review and launch</h2>
          <div className="campaign-review-grid">
            <div className="campaign-review-block">
              <span className="campaign-review-label">Campaign</span>
              <span className="campaign-review-value">{campaignName}</span>
            </div>
            <div className="campaign-review-block">
              <span className="campaign-review-label">Purpose</span>
              <span className="campaign-review-value">{purposes.find(p => p.value === purpose)?.label || purpose}</span>
            </div>
            <div className="campaign-review-block">
              <span className="campaign-review-label">Audience</span>
              <span className="campaign-review-value">{preview ? `${preview.total_matched.toLocaleString()} leads` : 'Preview to see count'}</span>
            </div>
            <div className="campaign-review-block">
              <span className="campaign-review-label">Tone</span>
              <span className="campaign-review-value">{TONES.find(t => t.value === tone)?.label}</span>
            </div>
            <div className="campaign-review-block" style={{ gridColumn: '1 / -1' }}>
              <span className="campaign-review-label">Message</span>
              <div className="campaign-review-message">{message}</div>
            </div>
            {startCadence && (
              <div className="campaign-review-block">
                <span className="campaign-review-label">Cadence</span>
                <span className="campaign-review-value">{cadenceTemplateId ? cadenceTemplates.find(t => t.id === cadenceTemplateId)?.name : 'Default cadence'}</span>
              </div>
            )}
            {autoReply && (
              <div className="campaign-review-block">
                <span className="campaign-review-label">AI auto-reply</span>
                <span className="campaign-review-value" style={{ color: 'var(--signal-green)' }}>✓ Enabled</span>
              </div>
            )}
          </div>

          <div className="campaign-nav">
            <button className="btn btn--secondary" onClick={() => setStep(2)}>← Edit message</button>
            <button className="btn btn--primary campaign-send-btn" onClick={handleSend} disabled={sending || !message.trim()}>
              {sending ? 'Launching…' : `🚀 Launch campaign`}
            </button>
          </div>
        </section>
      )}

      {campaigns.length > 0 && (
        <section className="panel" style={{ marginTop: 20 }}>
          <div className="panel-header">
            <h2 className="panel-title">Past campaigns</h2>
            <span className="panel-count">{campaigns.length}</span>
          </div>
          <table className="data-table">
            <thead><tr><th>Name</th><th>Created</th><th>Track</th><th>Filters</th></tr></thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{c.message_track || '—'}</td>
                  <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    {Object.entries(c.filter_criteria || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || 'All leads'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
