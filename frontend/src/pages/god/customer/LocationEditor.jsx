import { useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'

/**
 * LOCATION EDITOR — the UI half of an API that already existed.
 *
 * POST /god/customers/{org_id}/locations and
 * PATCH /god/customers/{org_id}/locations/{location_id} have been there and
 * authorized by require_god all along; this tab could only report that a
 * customer had no locations, which made a solvable activation blocker look
 * like a dead end.
 *
 * THE FIELDS ARE THE REQUEST MODELS', NOT THIS FILE'S. Everything below maps
 * one-to-one onto LocationIn / LocationPatch in app/routers/customers_router.py.
 * Nothing is invented here, and two things are deliberately absent:
 *
 *   country          LocationIn takes it and defaults to "US"; LocationPatch
 *                    has no such field, so offering it on an edit would show a
 *                    control that silently does nothing. Omitted on create,
 *                    which accepts the model's own default.
 *   operating_hours  a structured payload with its own status in the row above.
 *                    A text box for it would be a way to corrupt it.
 *
 * Primary is not set here either: the server makes a customer's first location
 * primary by itself, and moving it is a separate decision from typing an
 * address.
 */
const LOC_FIELDS = ['name', 'address_line1', 'address_line2', 'city', 'state',
                    'postal_code', 'phone', 'email', 'timezone', 'notes']

export default function LocationEditor({ orgId, location, onCancel, onSaved }) {
  const editing = Boolean(location && location.id)
  const [f, setF] = useState(() => {
    const seed = {}
    for (const k of LOC_FIELDS) seed[k] = (location && location[k]) || ''
    return seed
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => setF(x => ({ ...x, [k]: v }))
  const nameOk = f.name.trim().length > 0

  async function save() {
    // TWO GUARDS, NOT ONE. The button is disabled while saving, and this
    // returns early as well — a disabled attribute does not survive a second
    // click that lands in the same tick, and a duplicate POST here would be
    // refused by the server's slug check rather than silently creating two.
    if (busy || !nameOk) return
    setBusy(true); setErr('')
    const body = {}
    for (const k of LOC_FIELDS) {
      const v = (f[k] || '').trim()
      if (v) body[k] = v
    }
    body.name = f.name.trim()
    try {
      const saved = editing
        ? await api.patch('/god/customers/' + orgId + '/locations/' + location.id, body)
        : await api.post('/god/customers/' + orgId + '/locations', body)
      onSaved(saved, editing ? 'updated' : 'added')
    } catch (e) {
      // WHAT WAS TYPED STAYS TYPED. A refusal that also clears the form makes
      // the operator retype an address to find out what was wrong with it, and
      // the most likely refusal here is a duplicate name, which is fixed by
      // changing one field.
      setErr(errText(e))
    } finally { setBusy(false) }
  }

  return (
    <div className="go-card go-pad">
      <h2 className="go-h2">{editing ? 'Edit location' : 'Add a location'}</h2>
      <p className="go-hint">
        {editing
          ? 'Only the fields you change are sent.'
          : 'Name is required. The first location a customer has becomes its primary automatically.'}
      </p>
      {err && <div className="go-err">{err}</div>}

      <label className="go-label" htmlFor="locationed-location-name">Location name</label>
      <input id="locationed-location-name" className="go-input" value={f.name} autoFocus
             placeholder="Main office"
             onChange={e => set('name', e.target.value)} />

      <label className="go-label" htmlFor="locationed-address">Address</label>
      <input id="locationed-address" className="go-input" value={f.address_line1}
             placeholder="Street address"
             onChange={e => set('address_line1', e.target.value)} />
      <input className="go-input" value={f.address_line2}
             placeholder="Suite, floor (optional)"
             onChange={e => set('address_line2', e.target.value)} />

      <div className="go-two-even">
        <div>
          <label className="go-label" htmlFor="locationed-city">City</label>
          <input id="locationed-city" className="go-input" value={f.city}
                 onChange={e => set('city', e.target.value)} />
        </div>
        <div>
          <label className="go-label" htmlFor="locationed-state">State</label>
          <input id="locationed-state" className="go-input" value={f.state}
                 onChange={e => set('state', e.target.value)} />
        </div>
      </div>

      <div className="go-two-even">
        <div>
          <label className="go-label" htmlFor="locationed-postal-code">Postal code</label>
          <input id="locationed-postal-code" className="go-input" value={f.postal_code}
                 onChange={e => set('postal_code', e.target.value)} />
        </div>
        <div>
          <label className="go-label" htmlFor="locationed-phone">Phone</label>
          <input id="locationed-phone" className="go-input" value={f.phone}
                 onChange={e => set('phone', e.target.value)} />
        </div>
      </div>

      <div className="go-two-even">
        <div>
          <label className="go-label" htmlFor="locationed-email">Email</label>
          <input id="locationed-email" className="go-input" value={f.email}
                 placeholder="location@example.com"
                 onChange={e => set('email', e.target.value)} />
        </div>
        <div>
          <label className="go-label" htmlFor="locationed-timezone">Timezone</label>
          <input id="locationed-timezone" className="go-input" value={f.timezone}
                 placeholder="America/Chicago"
                 onChange={e => set('timezone', e.target.value)} />
        </div>
      </div>

      <label className="go-label" htmlFor="locationed-notes">Notes</label>
      <input id="locationed-notes" className="go-input" value={f.notes}
             onChange={e => set('notes', e.target.value)} />

      <div className="go-actions">
        <button className="go-btn go-btn-primary" onClick={save}
                disabled={busy || !nameOk}>
          {busy
            ? 'Saving…'
            : (editing ? 'Save location' : 'Add location')}
        </button>
        <button className="go-btn" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  )
}
