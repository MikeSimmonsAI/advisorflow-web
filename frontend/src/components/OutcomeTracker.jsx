import { useEffect, useState } from 'react'
import { api } from '../api/client'
import './OutcomeTracker.css'

const GAP_LABELS = {
  funeral_arrangement: 'Funeral arrangement',
  cemetery_property: 'Cemetery property',
  marker: 'Marker',
  memorial: 'Memorial',
}

// The four directly-sellable items - must match
// outcomes_router.py's MANDATORY_OUTCOME_FIELDS exactly. These are the
// only fields the Save button actually enforces; the context fields
// below (preneed/insurance/veteran) stay optional on purpose - forcing
// a guess there would hurt data quality, not help it.
const MANDATORY_FIELDS = ['has_funeral_arrangement', 'has_cemetery_property', 'has_marker', 'has_memorial']

/**
 * Records what a family does/doesn't have after a visit, and shows the
 * confirmed gaps prominently so the next conversation can be specific
 * (e.g. "let's talk about your marker") instead of generic. Real
 * business logic Mike specifically asked for - this data is meant to
 * feed smarter follow-up messaging and the org-wide sales analytics later.
 */
export default function OutcomeTracker({ leadId }) {
  const [gaps, setGaps] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [form, setForm] = useState({
    has_funeral_arrangement: null,
    has_cemetery_property: null,
    has_marker: null,
    has_memorial: null,
    has_open_closed_status: '',
    has_preneed_planning: null,
    has_insurance_funding: null,
    is_veteran: null,
    next_step: '',
    resulted_in_sale: false,
    sale_items: '',
    notes: '',
  })

  const missingMandatoryFields = MANDATORY_FIELDS.filter((f) => form[f] === null)
  const canSave = missingMandatoryFields.length === 0

  function loadGaps() {
    api.get(`/outcomes/lead/${leadId}/latest-gaps`).then(setGaps)
  }

  useEffect(() => { loadGaps() }, [leadId])

  function setTriState(field, value) {
    // Cycles null -> true -> false -> null, since "never asked" is a
    // real, distinct state from "confirmed yes" or "confirmed no" -
    // not just a default before the advisor picks one.
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  async function handleSave() {
    if (!canSave) return // the button is disabled in this case too, but never trust only the disabled attribute
    setSaving(true)
    setSaveError('')
    try {
      await api.post('/outcomes/', {
        lead_id: leadId,
        has_funeral_arrangement: form.has_funeral_arrangement,
        has_cemetery_property: form.has_cemetery_property,
        has_marker: form.has_marker,
        has_memorial: form.has_memorial,
        has_open_closed_status: form.has_open_closed_status || null,
        has_preneed_planning: form.has_preneed_planning,
        has_insurance_funding: form.has_insurance_funding,
        is_veteran: form.is_veteran,
        next_step: form.next_step || null,
        resulted_in_sale: form.resulted_in_sale,
        sale_items: form.sale_items || null,
        notes: form.notes || null,
      })
      setShowForm(false)
      loadGaps()
      setForm({
        has_funeral_arrangement: null, has_cemetery_property: null,
        has_marker: null, has_memorial: null, has_open_closed_status: '',
        has_preneed_planning: null, has_insurance_funding: null, is_veteran: null, next_step: '',
        resulted_in_sale: false, sale_items: '', notes: '',
      })
    } catch (err) {
      setSaveError(err.message || 'Failed to save this outcome.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-header">
        <h2 className="panel-title">What they have</h2>
        <button className="back-link" onClick={() => setShowForm((s) => !s)}>
          {showForm ? 'Cancel' : 'Record visit'}
        </button>
      </div>

      {gaps && gaps.has_outcome_data && gaps.gaps.length > 0 && (
        <div className="outcome-gaps-banner">
          <span className="outcome-gaps-label">Confirmed missing:</span>
          {gaps.gaps.map((g) => (
            <span key={g} className="badge badge--amber" style={{ marginRight: 6 }}>{GAP_LABELS[g] || g}</span>
          ))}
        </div>
      )}
      {gaps && !gaps.has_outcome_data && !showForm && (
        <p className="ai-quality-text">No visit recorded yet. Once a file review happens, record what they have here.</p>
      )}

      {showForm && (
        <div className="outcome-form">
          <p className="outcome-form-section-label">What they have (required)</p>
          <TriStateRow label="Funeral arrangement" value={form.has_funeral_arrangement} onChange={(v) => setTriState('has_funeral_arrangement', v)} />
          <TriStateRow label="Cemetery property" value={form.has_cemetery_property} onChange={(v) => setTriState('has_cemetery_property', v)} />
          <TriStateRow label="Marker" value={form.has_marker} onChange={(v) => setTriState('has_marker', v)} />
          <TriStateRow label="Memorial" value={form.has_memorial} onChange={(v) => setTriState('has_memorial', v)} />

          <label className="settings-label" style={{ marginTop: 10 }}>
            Open / closed status <span className="settings-optional">if applicable</span>
            <select
              className="settings-input"
              value={form.has_open_closed_status}
              onChange={(e) => setForm((p) => ({ ...p, has_open_closed_status: e.target.value }))}
            >
              <option value="">Not applicable / unknown</option>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
            </select>
          </label>

          <p className="outcome-form-section-label" style={{ marginTop: 16 }}>
            Context <span className="settings-optional">optional — helps shape the next conversation</span>
          </p>
          <TriStateRow label="Pre-need planning" value={form.has_preneed_planning} onChange={(v) => setTriState('has_preneed_planning', v)} />
          <TriStateRow label="Insurance / funding" value={form.has_insurance_funding} onChange={(v) => setTriState('has_insurance_funding', v)} />
          <TriStateRow label="Veteran" value={form.is_veteran} onChange={(v) => setTriState('is_veteran', v)} />

          <label className="settings-label" style={{ marginTop: 10 }}>
            Next step <span className="settings-optional">optional</span>
            <input
              className="settings-input"
              placeholder="e.g. Schedule family meeting next Tuesday"
              value={form.next_step}
              onChange={(e) => setForm((p) => ({ ...p, next_step: e.target.value }))}
            />
          </label>

          <label className="compose-checkbox" style={{ marginTop: 10 }}>
            <input
              type="checkbox"
              checked={form.resulted_in_sale}
              onChange={(e) => setForm((p) => ({ ...p, resulted_in_sale: e.target.checked }))}
            />
            This visit resulted in a sale
          </label>

          {form.resulted_in_sale && (
            <input
              className="settings-input"
              placeholder="What was sold (e.g. plot, marker, opening/closing)"
              value={form.sale_items}
              onChange={(e) => setForm((p) => ({ ...p, sale_items: e.target.value }))}
              style={{ marginTop: 8 }}
            />
          )}

          <textarea
            className="compose-textarea"
            placeholder="Notes from this visit…"
            value={form.notes}
            onChange={(e) => setForm((p) => ({ ...p, notes: e.target.value }))}
            rows={2}
            style={{ marginTop: 10 }}
          />

          {!canSave && (
            <p className="outcome-form-missing-hint">
              Select Has it / Doesn't have it for: {missingMandatoryFields.map((f) => GAP_LABELS[f.replace('has_', '')] || f).join(', ')}
            </p>
          )}
          {saveError && <div className="compose-error" style={{ marginTop: 8 }}>{saveError}</div>}

          <div className="settings-actions" style={{ marginTop: 10 }}>
            <button className="btn btn--primary" onClick={handleSave} disabled={saving || !canSave}>
              {saving ? 'Saving…' : 'Save outcome'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function TriStateRow({ label, value, onChange }) {
  return (
    <div className="outcome-tristate-row">
      <span className="outcome-tristate-label">{label}</span>
      <div className="outcome-tristate-buttons">
        <button
          type="button"
          className={`outcome-tristate-btn ${value === true ? 'outcome-tristate-btn--yes-active' : ''}`}
          onClick={() => onChange(value === true ? null : true)}
        >
          Has it
        </button>
        <button
          type="button"
          className={`outcome-tristate-btn ${value === false ? 'outcome-tristate-btn--no-active' : ''}`}
          onClick={() => onChange(value === false ? null : false)}
        >
          Doesn't
        </button>
        <span className="outcome-tristate-unknown">{value === null ? 'Not asked' : ''}</span>
      </div>
    </div>
  )
}
