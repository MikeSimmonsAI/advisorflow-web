import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Campaigns.css'

const TIER_OPTIONS = [
  { value: '', label: 'Any tier' },
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'email_only', label: 'Email Only' },
  { value: 'addr_only', label: 'Address Only' },
  { value: 'partial', label: 'Partial / Needs Review' },
]

const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'new', label: 'New' },
  { value: 'queued', label: 'Queued' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
  { value: 'dnc', label: 'DNC' },
  { value: 'dead', label: 'Dead' },
  { value: 'needs_tier_review', label: 'Needs Tier Review' },
]

const TRACK_OPTIONS = [
  { value: '', label: 'Leave track unchanged' },
  { value: 'pre_need_lock_price', label: 'Pre-Need Lock Price' },
  { value: 'at_need_support', label: 'At-Need Support' },
  { value: 'imminent_support', label: 'Imminent Support' },
  { value: 'upsell_existing', label: 'Upsell Existing Customer' },
  { value: 'email_only_nurture', label: 'Email-Only Nurture' },
  { value: 'needs_review', label: 'Needs Review' },
]

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function labelFromOptions(options, value) {
  return options.find((option) => option.value === value)?.label || value || '—'
}

function buildCriteria({ tier, sourceYear, status }) {
  const criteria = {}
  if (tier) criteria.tier = tier
  if (sourceYear) criteria.source_year = Number(sourceYear)
  if (status) criteria.status = status
  return criteria
}

function CriteriaPills({ criteria }) {
  const entries = Object.entries(criteria || {})
  if (entries.length === 0) return <span className="campaign-muted">All leads in org</span>

  return (
    <div className="campaign-pill-row">
      {entries.map(([key, value]) => (
        <span className="campaign-filter-pill" key={key}>
          {key.replace('_', ' ')}: {String(value).replaceAll('_', ' ')}
        </span>
      ))}
    </div>
  )
}

export default function Campaigns() {
  const [campaigns, setCampaigns] = useState([])
  const [selectedCampaignId, setSelectedCampaignId] = useState('')
  const [preview, setPreview] = useState(null)
  const [applyResult, setApplyResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const [name, setName] = useState('')
  const [tier, setTier] = useState('')
  const [sourceYear, setSourceYear] = useState('')
  const [status, setStatus] = useState('')
  const [messageTrack, setMessageTrack] = useState('')
  const [startCadence, setStartCadence] = useState(false)

  const selectedCampaign = useMemo(
    () => campaigns.find((campaign) => campaign.id === selectedCampaignId) || null,
    [campaigns, selectedCampaignId],
  )

  async function loadCampaigns() {
    setLoading(true)
    setError('')
    try {
      const data = await api.get('/campaigns')
      setCampaigns(data || [])
      if (!selectedCampaignId && data?.length) setSelectedCampaignId(data[0].id)
    } catch (err) {
      setError(err.message || 'Could not load campaigns.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadCampaigns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function createCampaign(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setPreview(null)
    setApplyResult(null)

    try {
      const payload = {
        name: name.trim(),
        filter_criteria: buildCriteria({ tier, sourceYear, status }),
        message_track: messageTrack || null,
      }
      const created = await api.post('/campaigns', payload)
      setCampaigns((current) => [created, ...current])
      setSelectedCampaignId(created.id)
      setName('')
      setTier('')
      setSourceYear('')
      setStatus('')
      setMessageTrack('')
    } catch (err) {
      setError(err.message || 'Could not create campaign.')
    } finally {
      setBusy(false)
    }
  }

  async function previewCampaign(campaignId = selectedCampaignId) {
    if (!campaignId) return
    setBusy(true)
    setError('')
    setApplyResult(null)
    try {
      const data = await api.post(`/campaigns/${campaignId}/preview`, {})
      setPreview(data)
      setSelectedCampaignId(campaignId)
    } catch (err) {
      setError(err.message || 'Could not preview campaign.')
    } finally {
      setBusy(false)
    }
  }

  async function applyCampaign() {
    if (!selectedCampaignId) return
    setBusy(true)
    setError('')
    try {
      const data = await api.post(`/campaigns/${selectedCampaignId}/apply`, { start_cadence: startCadence })
      setApplyResult(data)
      await previewCampaign(selectedCampaignId)
    } catch (err) {
      setError(err.message || 'Could not apply campaign.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="campaigns-page">
      <header className="page-header campaigns-header">
        <div>
          <p className="campaigns-eyebrow">Cohort Builder</p>
          <h1 className="page-title">Campaigns</h1>
          <p className="page-subtitle">
            Save lead filters, preview the current match count, then apply a message track and optional cadence start in one controlled step.
          </p>
        </div>
        <div className="panel campaigns-command-card">
          <span>Saved Campaigns</span>
          <strong>{loading ? '—' : campaigns.length}</strong>
          <small>Organization-scoped admin tools</small>
        </div>
      </header>

      {error ? <div className="campaigns-alert">{error}</div> : null}

      <section className="campaigns-grid">
        <form className="panel campaign-form" onSubmit={createCampaign}>
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Create Campaign</h2>
              <p className="campaign-panel-subtitle">Build a saved filter using existing lead fields only.</p>
            </div>
          </div>

          <label>
            Campaign name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="2012 Pre-Need Re-Engagement" required />
          </label>

          <div className="campaign-form-row">
            <label>
              Tier
              <select value={tier} onChange={(event) => setTier(event.target.value)}>
                {TIER_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>

            <label>
              Source year
              <input value={sourceYear} onChange={(event) => setSourceYear(event.target.value)} inputMode="numeric" placeholder="2012" />
            </label>
          </div>

          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              {STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>

          <label>
            Message track override
            <select value={messageTrack} onChange={(event) => setMessageTrack(event.target.value)}>
              {TRACK_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>

          <button className="btn btn--primary" type="submit" disabled={busy || !name.trim()}>
            Save Campaign
          </button>
        </form>

        <section className="panel campaign-preview-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Preview & Apply</h2>
              <p className="campaign-panel-subtitle">Preview before committing. DNC leads are counted but skipped on apply.</p>
            </div>
          </div>

          <label>
            Selected campaign
            <select value={selectedCampaignId} onChange={(event) => { setSelectedCampaignId(event.target.value); setPreview(null); setApplyResult(null) }}>
              <option value="">Select campaign</option>
              {campaigns.map((campaign) => (
                <option key={campaign.id} value={campaign.id}>{campaign.name}</option>
              ))}
            </select>
          </label>

          {selectedCampaign ? (
            <div className="campaign-selected-card">
              <strong>{selectedCampaign.name}</strong>
              <CriteriaPills criteria={selectedCampaign.filter_criteria} />
              <small>Track: {labelFromOptions(TRACK_OPTIONS, selectedCampaign.message_track)}</small>
            </div>
          ) : (
            <div className="campaign-empty-state">Create or select a campaign to preview matching leads.</div>
          )}

          <label className="campaign-checkbox">
            <input type="checkbox" checked={startCadence} onChange={(event) => setStartCadence(event.target.checked)} />
            Start cadence for eligible matching leads on apply
          </label>

          <div className="campaign-action-row">
            <button className="btn btn--secondary" type="button" onClick={() => previewCampaign()} disabled={busy || !selectedCampaignId}>
              Preview Matches
            </button>
            <button className="btn btn--primary" type="button" onClick={applyCampaign} disabled={busy || !selectedCampaignId || !preview}>
              Apply Campaign
            </button>
          </div>

          {preview ? (
            <div className="campaign-preview-result">
              <div className="campaign-preview-stats">
                <article><span>Matching</span><strong>{preview.matching_count}</strong></article>
                <article><span>Eligible</span><strong>{preview.eligible_count}</strong></article>
                <article><span>DNC skipped</span><strong>{preview.skipped_dnc_count}</strong></article>
              </div>
              <div className="campaign-sample-list">
                <h3>Sample leads</h3>
                {(preview.sample || []).length === 0 ? (
                  <p className="campaign-muted">No matching leads right now.</p>
                ) : (
                  preview.sample.map((lead) => (
                    <div className="campaign-sample-item" key={lead.id}>
                      <div>
                        <strong>{lead.name}</strong>
                        <span>{lead.phone || 'No phone'}</span>
                      </div>
                      <small>{lead.tier?.replaceAll('_', ' ')} · {lead.status?.replaceAll('_', ' ')} · {lead.source_year || 'no year'}</small>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : null}

          {applyResult ? (
            <div className="campaign-apply-result">
              Applied: {applyResult.updated_count} updated, {applyResult.skipped_dnc_count} DNC skipped, {applyResult.cadence_started_count} cadences started.
            </div>
          ) : null}
        </section>
      </section>

      <section className="panel campaigns-list-panel">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Existing Campaigns</h2>
            <p className="campaign-panel-subtitle">Saved filters available for preview and apply.</p>
          </div>
        </div>

        <div className="campaigns-table-wrap">
          <table className="data-table campaigns-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Criteria</th>
                <th>Track</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="5" className="campaign-empty-cell">Loading campaigns...</td></tr>
              ) : campaigns.length === 0 ? (
                <tr><td colSpan="5" className="campaign-empty-cell">No campaigns saved yet.</td></tr>
              ) : (
                campaigns.map((campaign) => (
                  <tr key={campaign.id}>
                    <td><strong>{campaign.name}</strong></td>
                    <td><CriteriaPills criteria={campaign.filter_criteria} /></td>
                    <td>{labelFromOptions(TRACK_OPTIONS, campaign.message_track)}</td>
                    <td>{formatDate(campaign.created_at)}</td>
                    <td>
                      <button className="btn btn--secondary btn--small" type="button" onClick={() => previewCampaign(campaign.id)} disabled={busy}>
                        Preview
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
