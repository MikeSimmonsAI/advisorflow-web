/**
 * CRM Integration Page — BookaBoost
 *
 * Allows org admins to:
 *   - Connect outbound webhooks (push bookings/events to any CRM)
 *   - Get their inbound webhook URL (so CRMs can push contacts in)
 *   - Test connections
 *   - Choose which events to push and what annotation tag to use
 *
 * Supports: Generic webhooks (Zapier, Make, GoHighLevel, HubSpot, Salesforce,
 *           any REST-capable CRM), GoHighLevel direct API, HubSpot direct API.
 */

import { useState, useEffect } from 'react'
import { api, getCurrentUser } from '../api/client'
import './CRMIntegration.css'

const CRM_TYPES = [
  { value: 'webhook',      label: 'Generic Webhook (Zapier, Make, any CRM)' },
  { value: 'gohighlevel',  label: 'GoHighLevel (Direct API)' },
  { value: 'hubspot',      label: 'HubSpot (Direct API)' },
]

const SYNC_MODES = [
  { value: 'push_only', label: 'Push Only — BookaBoost → CRM' },
  { value: 'pull_only', label: 'Pull Only — CRM → BookaBoost' },
  { value: 'two_way',   label: 'Two-Way Sync' },
]

const ALL_EVENTS = [
  { key: 'booking',          label: 'New Booking' },
  { key: 'status_change',    label: 'Status Change' },
  { key: 'new_reply',        label: 'Lead Replied' },
  { key: 'pipeline_started', label: 'Pipeline Started' },
]

const BACKEND = import.meta.env.VITE_API_URL || 'https://advisorflow-backend.onrender.com'

const DEFAULT_FORM = {
  name: '',
  crm_type: 'webhook',
  webhook_url: '',
  webhook_secret: '',
  api_key: '',
  api_base_url: '',
  sync_mode: 'push_only',
  push_events: ['booking', 'status_change'],
  annotation_tag: 'BookaBoost',
  active: true,
}

export default function CRMIntegration() {
  const user = getCurrentUser()
  const orgId = user?.organization_id || ''
  const inboundUrl = `${BACKEND}/crm/inbound/${orgId}`

  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editId, setEditId] = useState(null)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [testResults, setTestResults] = useState({})
  const [copied, setCopied] = useState(false)

  useEffect(() => { loadConnections() }, [])

  async function loadConnections() {
    setLoading(true)
    try {
      const data = await api.get('/crm/connections')
      setConnections(data)
    } catch (e) {
      setError('Failed to load CRM connections.')
    } finally {
      setLoading(false)
    }
  }

  function openAdd() {
    setEditId(null)
    setForm(DEFAULT_FORM)
    setError('')
    setShowForm(true)
  }

  function openEdit(conn) {
    setEditId(conn.id)
    setForm({
      name: conn.name || '',
      crm_type: conn.crm_type || 'webhook',
      webhook_url: conn.webhook_url || '',
      webhook_secret: '',
      api_key: '',
      api_base_url: conn.api_base_url || '',
      sync_mode: conn.sync_mode || 'push_only',
      push_events: conn.push_events || ['booking'],
      annotation_tag: conn.annotation_tag || 'BookaBoost',
      active: conn.active !== false,
    })
    setError('')
    setShowForm(true)
  }

  function toggleEvent(key) {
    setForm(f => ({
      ...f,
      push_events: f.push_events.includes(key)
        ? f.push_events.filter(e => e !== key)
        : [...f.push_events, key],
    }))
  }

  async function handleSave(e) {
    e.preventDefault()
    if (!form.name.trim()) { setError('Name is required.'); return }
    setSaving(true)
    setError('')
    try {
      if (editId) {
        await api.put(`/crm/connections/${editId}`, form)
      } else {
        await api.post('/crm/connections', form)
      }
      setShowForm(false)
      loadConnections()
    } catch (e) {
      setError(e.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id) {
    if (!confirm('Remove this CRM connection?')) return
    try {
      await api.delete(`/crm/connections/${id}`)
      loadConnections()
    } catch (e) {
      alert('Delete failed: ' + e.message)
    }
  }

  async function handleTest(id) {
    setTestResults(r => ({ ...r, [id]: { loading: true } }))
    try {
      const res = await api.post(`/crm/connections/${id}/test`, {})
      setTestResults(r => ({ ...r, [id]: res }))
    } catch (e) {
      setTestResults(r => ({ ...r, [id]: { success: false, error: e.message } }))
    }
  }

  function copyInbound() {
    navigator.clipboard.writeText(inboundUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const needsWebhookUrl = form.crm_type === 'webhook'
  const needsApiKey = form.crm_type === 'gohighlevel' || form.crm_type === 'hubspot'
  const showPushEvents = form.sync_mode !== 'pull_only'

  return (
    <div className="crm-page">
      <div className="crm-header">
        <div>
          <h1 className="crm-title">CRM Integration</h1>
          <p className="crm-subtitle">
            Push bookings and activity to your clients' CRMs, pull leads in, or both.
            Every record sent is annotated with a BookaBoost tag for clear audit trails.
          </p>
        </div>
        <button className="crm-btn-primary" onClick={openAdd}>+ Add Connection</button>
      </div>

      {/* Inbound webhook URL */}
      <div className="crm-inbound-box">
        <div className="crm-inbound-label">
          <strong>Your Inbound Webhook URL</strong>
          <span className="crm-badge crm-badge--blue">Pull-In</span>
        </div>
        <p className="crm-inbound-desc">
          Give this URL to your client's CRM so it can push contacts directly into BookaBoost.
          Paste it as a webhook endpoint in GoHighLevel, HubSpot, Zapier, Make, or any CRM.
          New contacts are deduplicated by phone and email.
        </p>
        <div className="crm-url-row">
          <code className="crm-inbound-url">{inboundUrl}</code>
          <button className="crm-btn-copy" onClick={copyInbound}>
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
        <p className="crm-inbound-hint">
          Expected payload: <code>{`{ "first_name": "Jane", "last_name": "Smith", "phone": "+15555550001", "email": "jane@example.com" }`}</code>
          — or send a <code>records</code> array for bulk import.
        </p>
      </div>

      {/* Connections list */}
      {loading ? (
        <div className="crm-empty">Loading connections…</div>
      ) : connections.length === 0 && !showForm ? (
        <div className="crm-empty">
          <p>No CRM connections yet.</p>
          <p>Add one to start pushing bookings to your clients' CRMs automatically.</p>
        </div>
      ) : (
        <div className="crm-list">
          {connections.map(conn => {
            const tr = testResults[conn.id]
            return (
              <div key={conn.id} className={`crm-card ${!conn.active ? 'crm-card--inactive' : ''}`}>
                <div className="crm-card-header">
                  <div className="crm-card-title-row">
                    <span className="crm-card-name">{conn.name}</span>
                    <span className={`crm-badge ${conn.active ? 'crm-badge--green' : 'crm-badge--gray'}`}>
                      {conn.active ? 'Active' : 'Paused'}
                    </span>
                    <span className="crm-badge crm-badge--blue">
                      {CRM_TYPES.find(t => t.value === conn.crm_type)?.label?.split(' ')[0] || conn.crm_type}
                    </span>
                    <span className="crm-badge crm-badge--purple">
                      {SYNC_MODES.find(m => m.value === conn.sync_mode)?.label?.split(' ')[0] || conn.sync_mode}
                    </span>
                  </div>
                  <div className="crm-card-actions">
                    <button className="crm-btn-sm" onClick={() => handleTest(conn.id)}>
                      {tr?.loading ? 'Testing…' : 'Test'}
                    </button>
                    <button className="crm-btn-sm" onClick={() => openEdit(conn)}>Edit</button>
                    <button className="crm-btn-sm crm-btn-sm--danger" onClick={() => handleDelete(conn.id)}>Remove</button>
                  </div>
                </div>

                {conn.webhook_url && (
                  <div className="crm-card-detail">
                    <span className="crm-card-detail-label">Webhook:</span>
                    <code className="crm-card-url">{conn.webhook_url}</code>
                  </div>
                )}

                <div className="crm-card-detail">
                  <span className="crm-card-detail-label">Events:</span>
                  <span>{(conn.push_events || []).join(', ') || 'none'}</span>
                  <span className="crm-card-detail-sep">·</span>
                  <span className="crm-card-detail-label">Tag:</span>
                  <span>{conn.annotation_tag || 'BookaBoost'}</span>
                  {conn.last_push_at && (
                    <><span className="crm-card-detail-sep">·</span>
                    <span className="crm-card-detail-label">Last push:</span>
                    <span>{new Date(conn.last_push_at).toLocaleString()}</span>
                    <span className="crm-card-detail-sep">·</span>
                    <span>{conn.total_pushed} sent</span></>
                  )}
                  {conn.last_pull_at && (
                    <><span className="crm-card-detail-sep">·</span>
                    <span className="crm-card-detail-label">Last pull:</span>
                    <span>{new Date(conn.last_pull_at).toLocaleString()}</span>
                    <span className="crm-card-detail-sep">·</span>
                    <span>{conn.total_pulled} imported</span></>
                  )}
                </div>

                {tr && !tr.loading && (
                  <div className={`crm-test-result ${tr.success ? 'crm-test-result--ok' : 'crm-test-result--fail'}`}>
                    {tr.success
                      ? '✓ Test webhook delivered successfully.'
                      : `✗ Test failed: ${tr.detail?.error || tr.error || `HTTP ${tr.detail?.status_code}`}`}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Add / Edit form */}
      {showForm && (
        <div className="crm-form-overlay">
          <div className="crm-form-panel">
            <div className="crm-form-header">
              <h2>{editId ? 'Edit Connection' : 'Add CRM Connection'}</h2>
              <button className="crm-form-close" onClick={() => setShowForm(false)}>✕</button>
            </div>

            <form onSubmit={handleSave} className="crm-form">
              <label className="crm-field">
                <span>Connection Name</span>
                <input
                  className="crm-input"
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. GoHighLevel — Harmony Houses"
                  required
                />
              </label>

              <label className="crm-field">
                <span>CRM Type</span>
                <select
                  className="crm-input"
                  value={form.crm_type}
                  onChange={e => setForm(f => ({ ...f, crm_type: e.target.value }))}
                >
                  {CRM_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>

              <label className="crm-field">
                <span>Sync Mode</span>
                <select
                  className="crm-input"
                  value={form.sync_mode}
                  onChange={e => setForm(f => ({ ...f, sync_mode: e.target.value }))}
                >
                  {SYNC_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </label>

              {needsWebhookUrl && (
                <>
                  <label className="crm-field">
                    <span>Webhook URL <span className="crm-field-hint">(where BookaBoost POSTs events)</span></span>
                    <input
                      className="crm-input"
                      type="url"
                      value={form.webhook_url}
                      onChange={e => setForm(f => ({ ...f, webhook_url: e.target.value }))}
                      placeholder="https://hooks.zapier.com/hooks/catch/..."
                    />
                  </label>
                  <label className="crm-field">
                    <span>Webhook Secret <span className="crm-field-hint">(optional — for HMAC signature validation)</span></span>
                    <input
                      className="crm-input"
                      type="password"
                      value={form.webhook_secret}
                      onChange={e => setForm(f => ({ ...f, webhook_secret: e.target.value }))}
                      placeholder="Leave blank to skip signing"
                      autoComplete="new-password"
                    />
                  </label>
                </>
              )}

              {needsApiKey && (
                <>
                  <label className="crm-field">
                    <span>API Key / Access Token</span>
                    <input
                      className="crm-input"
                      type="password"
                      value={form.api_key}
                      onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))}
                      placeholder={form.crm_type === 'gohighlevel' ? 'GHL API key or location token' : 'HubSpot Private App token'}
                      autoComplete="new-password"
                    />
                    <span className="crm-field-note">
                      {form.crm_type === 'gohighlevel'
                        ? 'Found in GHL → Settings → Integrations → API Keys'
                        : 'Found in HubSpot → Settings → Integrations → Private Apps'}
                    </span>
                  </label>
                </>
              )}

              <label className="crm-field">
                <span>Annotation Tag <span className="crm-field-hint">(added to every record BookaBoost creates/updates)</span></span>
                <input
                  className="crm-input"
                  value={form.annotation_tag}
                  onChange={e => setForm(f => ({ ...f, annotation_tag: e.target.value }))}
                  placeholder="BookaBoost"
                />
              </label>

              {showPushEvents && (
                <div className="crm-field">
                  <span>Push These Events</span>
                  <div className="crm-events-grid">
                    {ALL_EVENTS.map(ev => (
                      <label key={ev.key} className="crm-event-check">
                        <input
                          type="checkbox"
                          checked={form.push_events.includes(ev.key)}
                          onChange={() => toggleEvent(ev.key)}
                        />
                        {ev.label}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <label className="crm-field crm-field--inline">
                <input
                  type="checkbox"
                  checked={form.active}
                  onChange={e => setForm(f => ({ ...f, active: e.target.checked }))}
                />
                <span>Active (uncheck to pause without deleting)</span>
              </label>

              {error && <div className="crm-form-error">{error}</div>}

              <div className="crm-form-actions">
                <button type="button" className="crm-btn-secondary" onClick={() => setShowForm(false)}>
                  Cancel
                </button>
                <button type="submit" className="crm-btn-primary" disabled={saving}>
                  {saving ? 'Saving…' : (editId ? 'Update Connection' : 'Add Connection')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
