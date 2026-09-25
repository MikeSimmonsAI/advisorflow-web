/* The property workspace: every fact about the house, editable, with a Save
 * button that is where you expect it.
 *
 * Phase 1/2 showed these as read-only key/value pairs. A property record that
 * cannot be corrected is a record that goes stale the first time a skip trace
 * gets a year built wrong, and the only way to fix one was the API.
 *
 * WHY THE FORM IS ALWAYS EDITABLE rather than having an Edit mode: a wholesaler
 * updates one field at a time, all day, as facts arrive over the phone. An edit
 * toggle adds a click to every one of those. The Save button is disabled until
 * something actually changes, so the screen still says plainly whether there is
 * unsaved work.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { errText, fmtLabel } from './wsShared'
import { PhotoGrid } from './wsFiles'

const TEXT_FIELDS = [
  ['street_address', 'Street address'],
  ['city', 'City'],
  ['county', 'County'],
  ['state', 'State'],
  ['zip_code', 'ZIP'],
  ['market', 'Market'],
  ['parcel_apn', 'Parcel / APN'],
  ['owner_name', 'Owner of record'],
]

const NUMBER_FIELDS = [
  ['bedrooms', 'Beds'],
  ['bathrooms', 'Baths'],
  ['square_feet', 'Square feet'],
  ['lot_size', 'Lot size'],
  ['year_built', 'Year built'],
  ['estimated_value', 'Estimated value'],
]

const CHOICE_FIELDS = [
  ['property_type', 'Property type',
   ['single_family', 'duplex', 'triplex', 'fourplex', 'multi_family', 'condo',
    'townhouse', 'mobile', 'land', 'commercial', 'other']],
  ['ownership_type', 'Ownership',
   ['individual', 'joint', 'trust', 'llc', 'corporation', 'estate', 'other']],
  ['occupancy_status', 'Occupancy',
   ['owner_occupied', 'tenant_occupied', 'vacant', 'unknown']],
]


export function PropertyWorkspace({ property, photos, capability, onSaved,
                                    onPhotosChanged }) {
  const [form, setForm] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  // Re-seed whenever the property arrives or is replaced, so the form never
  // shows a previous deal's values for a moment after navigating.
  useEffect(() => { setForm({}); setNotice(null) }, [property?.id])

  if (!property) return null

  const value = (key) => (form[key] !== undefined
    ? form[key]
    : (property[key] === null || property[key] === undefined ? '' : property[key]))

  const dirty = Object.keys(form).some(
    (k) => String(form[k] ?? '') !== String(property[k] ?? ''))

  function set(key, v) { setForm((f) => ({ ...f, [key]: v })) }

  async function save() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const payload = {}
      Object.keys(form).forEach((k) => {
        if (String(form[k] ?? '') === String(property[k] ?? '')) return
        // An emptied field means "no longer known", which is null — not the
        // empty string, which would read back as a value somebody typed.
        payload[k] = form[k] === '' ? null : form[k]
      })
      if (!Object.keys(payload).length) { setBusy(false); return }
      await api.patch(`/wholesale/properties/${property.id}`, payload)
      setForm({})
      setNotice('Property saved.')
      if (onSaved) await onSaved()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  /* One request for the whole set, not one per file. A twenty-photo
   * walkthrough was twenty round trips, and a failure halfway through left
   * the operator guessing which ones landed. The batch endpoint reports each
   * file's own outcome, so a rejected HEIC is named rather than silently
   * missing from the grid. */
  async function uploadPhotos(files) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const fd = new FormData()
      for (const file of files) fd.append('files', file)
      const result = await api.upload(
        `/wholesale/properties/${property.id}/photos/batch`, fd)
      const added = result.uploaded_count || 0
      const refused = result.rejected || []
      setNotice(added === 1 ? 'Photo uploaded.' : `${added} photos uploaded.`)
      if (refused.length) {
        setError(`${refused.length} file(s) were not accepted: `
                 + refused.map((r) => `${r.filename} — ${r.reason}`).join('; '))
      }
      if (onPhotosChanged) await onPhotosChanged()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  function patchNotice(changes) {
    if ('buyer_visible' in changes) {
      return changes.buyer_visible
        ? 'Photo shared with investors.'
        : 'Photo hidden from investors.'
    }
    if ('is_primary' in changes) return 'Cover photo changed.'
    if ('category' in changes) return 'Category saved.'
    if ('caption' in changes) return 'Caption saved.'
    return 'Saved.'
  }


  async function photoAction(fn, message) {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(message)
      if (onPhotosChanged) await onPhotosChanged()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">
          <span>Property</span>
          <span className="ws-actions">
            {dirty ? <span className="ws-hint">Unsaved changes</span> : null}
            <button className="btn btn--secondary btn--sm" disabled={!dirty || busy}
                    onClick={() => setForm({})}>Cancel</button>
            <button className="btn btn--primary btn--sm" disabled={!dirty || busy}
                    onClick={save}>{busy ? 'Saving…' : 'Save property'}</button>
          </span>
        </div>

        {error ? <div className="ws-error">{error}</div> : null}
        {notice ? <div className="ws-good">{notice}</div> : null}

        <div className="ws-grid">
          {TEXT_FIELDS.map(([key, label]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`p-${key}`}>{label}</label>
              <input id={`p-${key}`} value={value(key)}
                     onChange={(e) => set(key, e.target.value)} />
            </div>
          ))}
          {NUMBER_FIELDS.map(([key, label]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`p-${key}`}>{label}</label>
              <input id={`p-${key}`} type="number" value={value(key)}
                     onChange={(e) => set(key, e.target.value)} />
            </div>
          ))}
          {CHOICE_FIELDS.map(([key, label, options]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`p-${key}`}>{label}</label>
              <select id={`p-${key}`} value={value(key)}
                      onChange={(e) => set(key, e.target.value)}>
                <option value="">Unknown</option>
                {options.map((o) => (
                  <option key={o} value={o}>{fmtLabel(o)}</option>
                ))}
              </select>
            </div>
          ))}
        </div>

        <div className="ws-field" style={{ marginTop: 14 }}>
          <label htmlFor="p-notes">Notes</label>
          <textarea id="p-notes" rows={3} value={value('notes')}
                    onChange={(e) => set('notes', e.target.value)} />
        </div>
      </div>

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">
          <span>Photos</span>
          <span className="ws-hint">
            The cover appears on the deal header. Nothing reaches an investor
            until you tick it.
          </span>
        </div>
        <PhotoGrid photos={photos} capability={capability} busy={busy}
                   onUpload={uploadPhotos}
                   onPatch={(p, changes) => photoAction(
                     () => api.patch(`/wholesale/files/${p.id}`, changes),
                     patchNotice(changes))}
                   onReorder={(ids) => photoAction(
                     () => api.post(
                       `/wholesale/properties/${property.id}/photos/reorder`,
                       { file_ids: ids }),
                     'Order saved.')}
                   onDelete={(p) => photoAction(
                     () => api.delete(`/wholesale/files/${p.id}`),
                     'Photo deleted.')} />
      </div>
    </>
  )
}
