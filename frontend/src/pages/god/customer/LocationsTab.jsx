/* LOCATIONS — primary, timezone, booking route, assigned users, routing, status.
   Add / edit use the existing POST / PATCH /god/customers/{id}/locations. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'
import LocationEditor from './LocationEditor'
import { Pill } from './ccShared'

export default function LocationsTab({ orgId, cc, reload, openAdd, onOpened }) {
  const [edit, setEdit] = useState(null)
  const [flash, setFlash] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const locs = cc.locations
  const booking = cc.readiness.items.find(i => i.key === 'booking')

  useEffect(() => {
    if (openAdd) { setFlash(''); setEdit({}); onOpened && onOpened() }
  }, [openAdd, onOpened])

  async function makePrimary(l) {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/customers/' + orgId + '/locations/' + l.id, { is_primary: true })
      setFlash('“' + l.name + '” is now the primary location.')
      reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function toggleActive(l) {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/customers/' + orgId + '/locations/' + l.id, { is_active: !l.is_active })
      reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="occ">
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Locations</h3>
            <p className="occ-sub">
              {locs.length} location{locs.length === 1 ? '' : 's'} · the primary location is
              the default booking route
            </p>
          </div>
          <button className="go-btn go-btn-primary"
                  onClick={() => { setFlash(''); setEdit({}) }}>Add location</button>
        </div>
        {booking && (
          <p className="occ-sub">
            Booking readiness: <Pill status={booking.status} label={booking.status_label} />{' '}
            {booking.reason}
          </p>
        )}
        {flash && <div className="go-note">{flash}</div>}
        {err && <div className="go-err">{err}</div>}

        {edit && (
          <LocationEditor
            orgId={orgId}
            location={edit.id ? edit : null}
            onCancel={() => setEdit(null)}
            onSaved={(saved, what) => {
              setEdit(null)
              setFlash('Location “' + saved.name + '” ' + what + '.')
              reload()
            }} />
        )}

        {locs.length === 0 && !edit && (
          <div className="occ-empty">
            <p style={{ marginTop: 0 }}>
              <strong>No locations yet.</strong> A location is where bookings route and where
              people are assigned. The first one you add becomes primary automatically.
            </p>
            <button className="go-btn go-btn-primary" onClick={() => setEdit({})}>
              Add the first location
            </button>
          </div>
        )}

        {locs.length > 0 && (
          <div className="occ-table-wrap">
            <table className="occ-table">
              <thead>
                <tr>
                  <th>Location</th><th>Timezone</th><th>Booking route</th><th>Hours</th>
                  <th>Assigned users</th><th>Routing</th><th>Status</th><th />
                </tr>
              </thead>
              <tbody>
                {locs.map(l => (
                  <tr key={l.id}>
                    <td>
                      <strong>{l.name}</strong>{' '}
                      {l.is_primary && <Pill tone="info" label="Primary" />}
                      <div className="occ-muted">
                        {[l.address_line1, l.city, l.state, l.postal_code].filter(Boolean).join(', ') || 'No address'}
                      </div>
                    </td>
                    <td>{l.timezone || <span className="occ-muted">Inherits / not set</span>}</td>
                    <td className="occ-muted">{l.booking_route}</td>
                    <td>
                      <Pill tone={l.operating_hours_status === 'CONFIGURED' ? 'good' : 'warn'}
                            label={l.operating_hours_status === 'CONFIGURED' ? 'Hours set' : 'No hours'} />
                    </td>
                    <td>
                      {l.assigned_users.length === 0
                        ? <span className="occ-muted">None</span>
                        : l.assigned_users.map(u => u.name).join(', ')}
                    </td>
                    <td className="occ-muted">
                      {l.communication_routing ? l.communication_routing.phone : 'No location phone'}
                    </td>
                    <td>
                      <Pill tone={l.is_active ? 'good' : 'neutral'}
                            label={l.is_active ? 'Active' : 'Inactive'} />
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="go-btn" onClick={() => { setFlash(''); setEdit(l) }}>Edit</button>{' '}
                      {!l.is_primary && l.is_active && (
                        <button className="go-btn" disabled={busy} onClick={() => makePrimary(l)}>
                          Make primary
                        </button>
                      )}{' '}
                      {!l.is_primary && (
                        <button className="go-btn" disabled={busy} onClick={() => toggleActive(l)}>
                          {l.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="occ-sub" style={{ marginTop: 10, marginBottom: 0 }}>
          Operating hours are stored per location but have no editor here yet; a location
          without hours reads as “No hours”, never as open 9–5.
        </p>
      </section>
    </div>
  )
}
