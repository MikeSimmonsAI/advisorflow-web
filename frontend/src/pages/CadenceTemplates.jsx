import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './CadenceTemplates.css'

const CHANNELS = [
  { value: 'sms', label: '💬 SMS' },
  { value: 'email', label: '✉️ Email' },
  { value: 'both', label: '📡 Both' },
]

const HOURS = Array.from({ length: 24 }, (_, i) => ({
  value: i,
  label: `${i === 0 ? '12' : i > 12 ? i - 12 : i}:00 ${i < 12 ? 'AM' : 'PM'}`,
}))

function emptyTouch(num) {
  return { touch_number: num, day_offset: num === 1 ? 1 : num * 3, send_hour: 10, channel: 'sms', message_template: '', subject_template: '', is_active: true }
}

function TouchEditor({ touch, onChange, onRemove, index }) {
  return (
    <div className="ct-touch-card">
      <div className="ct-touch-header">
        <span className="ct-touch-num">Touch {touch.touch_number}</span>
        <div className="ct-touch-meta">
          <label className="ct-touch-field-inline">
            Day
            <input type="number" min="1" max="365" value={touch.day_offset}
              onChange={(e) => onChange({ ...touch, day_offset: parseInt(e.target.value) || 1 })}
              className="ct-input ct-input--sm" />
          </label>
          <label className="ct-touch-field-inline">
            Time
            <select value={touch.send_hour} onChange={(e) => onChange({ ...touch, send_hour: parseInt(e.target.value) })} className="ct-input ct-input--sm">
              {HOURS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </label>
          <label className="ct-touch-field-inline">
            Channel
            <select value={touch.channel} onChange={(e) => onChange({ ...touch, channel: e.target.value })} className="ct-input ct-input--sm">
              {CHANNELS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </label>
          <label className="ct-touch-active">
            <input type="checkbox" checked={touch.is_active} onChange={(e) => onChange({ ...touch, is_active: e.target.checked })} />
            Active
          </label>
        </div>
        <button className="ct-remove-btn" onClick={onRemove} title="Remove touch">✕</button>
      </div>
      {(touch.channel === 'email' || touch.channel === 'both') && (
        <input
          className="ct-input ct-input--full"
          placeholder="Email subject — use {first_name}, {advisor_name}, {org_name}"
          value={touch.subject_template || ''}
          onChange={(e) => onChange({ ...touch, subject_template: e.target.value })}
        />
      )}
      <textarea
        className="ct-textarea"
        placeholder="Message — use {first_name}, {advisor_name}, {org_name}, {booking_url}"
        value={touch.message_template || ''}
        onChange={(e) => onChange({ ...touch, message_template: e.target.value })}
        rows={3}
      />
    </div>
  )
}

export default function CadenceTemplates() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [seeding, setSeeding] = useState(false)
  const [seedResult, setSeedResult] = useState(null)
  const [editing, setEditing] = useState(null) // null | 'new' | template object
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function load() {
    setLoading(true)
    api.get('/cadence-templates/').then(setTemplates).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleSeedDefaults() {
    setSeeding(true)
    setSeedResult(null)
    try {
      const result = await api.post('/cadence-templates/seed-defaults?industry=all', {})
      setSeedResult(result)
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setSeeding(false)
    }
  }

  function startNew() {
    setEditing({
      name: '',
      description: '',
      industry: 'funeral',
      is_default: false,
      allow_advisor_override: false,
      touches: [emptyTouch(1)],
    })
    setError('')
  }

  function startEdit(template) {
    setEditing({ ...template, touches: template.touches.map((t) => ({ ...t })) })
    setError('')
  }

  function updateTouch(index, updated) {
    setEditing((prev) => {
      const touches = [...prev.touches]
      touches[index] = updated
      return { ...prev, touches }
    })
  }

  function addTouch() {
    setEditing((prev) => ({
      ...prev,
      touches: [...prev.touches, emptyTouch(prev.touches.length + 1)]
    }))
  }

  function removeTouch(index) {
    setEditing((prev) => {
      const touches = prev.touches.filter((_, i) => i !== index).map((t, i) => ({ ...t, touch_number: i + 1 }))
      return { ...prev, touches }
    })
  }

  async function handleSave() {
    if (!editing.name.trim()) { setError('Template name is required.'); return }
    if (editing.touches.length === 0) { setError('Add at least one touch.'); return }
    setSaving(true)
    setError('')
    try {
      if (editing.id) {
        await api.patch(`/cadence-templates/${editing.id}`, editing)
      } else {
        await api.post('/cadence-templates/', editing)
      }
      setEditing(null)
      load()
    } catch (err) {
      setError(err.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id) {
    if (!confirm('Delete this template? This cannot be undone.')) return
    try {
      await api.delete(`/cadence-templates/${id}`)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Cadence Builder</h1>
          <p className="page-subtitle">Build multi-touch sequences — control channel, timing, and message per touch.</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn--secondary" onClick={handleSeedDefaults} disabled={seeding}>
            {seeding ? 'Loading defaults…' : '⚡ Load default templates'}
          </button>
          <button className="btn btn--primary" onClick={startNew}>+ New template</button>
        </div>
      </header>

      {error && <div className="ct-error">{error}</div>}

      {seedResult && (
        <div className="ct-seed-result">
          {seedResult.seeded.map((s) => (
            <span key={s.name} className={`ct-seed-item ${s.status === 'created' ? 'ct-seed-item--new' : 'ct-seed-item--exists'}`}>
              {s.status === 'created' ? '✓' : '—'} {s.name}
            </span>
          ))}
        </div>
      )}

      {editing && (
        <section className="panel ct-editor">
          <div className="panel-header">
            <h2 className="panel-title">{editing.id ? 'Edit template' : 'New template'}</h2>
            <button className="btn btn--secondary" onClick={() => setEditing(null)}>Cancel</button>
          </div>

          <div className="ct-editor-meta">
            <label className="ct-label">Template name
              <input className="ct-input ct-input--full" value={editing.name} onChange={(e) => setEditing((p) => ({ ...p, name: e.target.value }))} placeholder="e.g. Funeral Home 9-Touch" />
            </label>
            <label className="ct-label">Description
              <input className="ct-input ct-input--full" value={editing.description || ''} onChange={(e) => setEditing((p) => ({ ...p, description: e.target.value }))} placeholder="Optional description" />
            </label>
            <div className="ct-editor-options">
              <label className="ct-check-label">
                <input type="checkbox" checked={editing.is_default} onChange={(e) => setEditing((p) => ({ ...p, is_default: e.target.checked }))} />
                Set as org default
              </label>
              <label className="ct-check-label">
                <input type="checkbox" checked={editing.allow_advisor_override} onChange={(e) => setEditing((p) => ({ ...p, allow_advisor_override: e.target.checked }))} />
                Allow advisors to customize their own sequence
              </label>
            </div>
          </div>

          <div className="ct-touches">
            <div className="ct-touches-header">
              <span className="ct-touches-title">Touches ({editing.touches.length})</span>
              <span className="ct-touches-hint">Variables: {'{first_name}'} {'{advisor_name}'} {'{org_name}'} {'{booking_url}'}</span>
            </div>
            {editing.touches.map((touch, i) => (
              <TouchEditor key={i} touch={touch} index={i} onChange={(updated) => updateTouch(i, updated)} onRemove={() => removeTouch(i)} />
            ))}
            <button className="btn btn--secondary ct-add-touch-btn" onClick={addTouch}>+ Add touch</button>
          </div>

          <div className="ct-editor-footer">
            {error && <span className="ct-error-inline">{error}</span>}
            <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save template'}
            </button>
          </div>
        </section>
      )}

      {loading ? (
        <div className="empty-state">Loading templates…</div>
      ) : templates.length === 0 ? (
        <div className="panel" style={{ padding: 32, textAlign: 'center' }}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No cadence templates yet. Load the defaults to get started.</p>
          <button className="btn btn--primary" onClick={handleSeedDefaults} disabled={seeding}>
            {seeding ? 'Loading…' : '⚡ Load default templates'}
          </button>
        </div>
      ) : (
        <div className="ct-template-grid">
          {templates.map((t) => (
            <div key={t.id} className="panel ct-template-card">
              <div className="ct-template-header">
                <div>
                  <span className="ct-template-name">{t.name}</span>
                  {t.is_default && <span className="ct-default-badge">Default</span>}
                  {t.allow_advisor_override && <span className="ct-override-badge">Advisors can customize</span>}
                </div>
                <div className="ct-template-actions">
                  <button className="btn btn--secondary" onClick={() => startEdit(t)} style={{ fontSize: 12, padding: '4px 10px' }}>Edit</button>
                  <button className="btn btn--danger" onClick={() => handleDelete(t.id)} style={{ fontSize: 12, padding: '4px 10px' }}>Delete</button>
                </div>
              </div>
              {t.description && <p className="ct-template-desc">{t.description}</p>}
              <div className="ct-touch-timeline">
                {t.touches.map((touch) => (
                  <div key={touch.id} className={`ct-timeline-touch ${!touch.is_active ? 'ct-timeline-touch--inactive' : ''}`}>
                    <div className="ct-timeline-dot" style={{ background: touch.channel === 'email' ? 'var(--signal-purple)' : touch.channel === 'both' ? 'var(--signal-green)' : 'var(--signal-blue)' }} />
                    <div className="ct-timeline-info">
                      <span className="ct-timeline-day">Day {touch.day_offset}</span>
                      <span className="ct-timeline-channel">{CHANNELS.find((c) => c.value === touch.channel)?.label}</span>
                      <span className="ct-timeline-hour">{HOURS.find((h) => h.value === touch.send_hour)?.label}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
