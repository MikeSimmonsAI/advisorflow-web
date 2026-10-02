import { useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'

export default function AddPerson({ orgId, locations, onAdded }) {
  const [email, setEmail] = useState('')
  const [look, setLook] = useState(null)
  const [name, setName] = useState('')
  const [role, setRole] = useState('advisor')
  const [locs, setLocs] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function lookup() {
    if (!email.trim()) return
    setBusy(true); setErr(''); setLook(null)
    try {
      setLook(await api.get('/god/customers/' + orgId + '/identity-lookup?email=' +
                            encodeURIComponent(email.trim())))
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function add() {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/god/customers/' + orgId + '/users', {
        email: email.trim(), full_name: name.trim(), role, location_ids: locs,
      })
      setEmail(''); setName(''); setLook(null); setLocs([])
      onAdded(r)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="go-card go-pad">
      <h2 className="go-h2">Add a person</h2>
      <p className="go-hint">
        Email first. One human is one identity — if this address already exists
        anywhere, you will be told before anything is created.
      </p>
      {err && <div className="go-err">{err}</div>}

      <div className="go-two">
        <input className="go-input" value={email} placeholder="email@example.com"
               onChange={e => { setEmail(e.target.value); setLook(null) }} />
        <button className="go-btn" onClick={lookup} disabled={busy || !email.trim()}>
          Look up
        </button>
      </div>

      {look && !look.can_add && (
        <div className="go-warn">{look.reason}</div>
      )}

      {look && look.can_add && (
        <>
          {/* `add_context` is an existing identity from elsewhere — brand
              sales, or another customer. It is reused, not re-created, and it
              needs no name: the note carries what will actually happen. */}
          {look.action === 'reuse' || look.action === 'add_context'
            ? <div className="go-note">{look.reason}</div>
            : (
              <>
                <label className="go-label" htmlFor="addperson-full-name">Full name</label>
                <input id="addperson-full-name" className="go-input" value={name}
                       onChange={e => setName(e.target.value)} />
              </>
            )}

          <label className="go-label" htmlFor="addperson-role">Role</label>
          <select id="addperson-role" aria-label="Role" className="go-input" value={role} onChange={e => setRole(e.target.value)}>
            <option value="advisor">Advisor</option>
            <option value="org_admin">Customer admin</option>
            <option value="viewer">Viewer</option>
          </select>

          {locations.length > 0 && (
            <>
              <label className="go-label">Locations</label>
              {locations.map(l => (
                <label key={l.id} className="go-check">
                  <input type="checkbox" checked={locs.includes(l.id)}
                         onChange={() => setLocs(x => x.includes(l.id)
                           ? x.filter(i => i !== l.id) : [...x, l.id])} />
                  <span>{l.name}</span>
                </label>
              ))}
            </>
          )}

          <div className="go-actions">
            <button className="go-btn go-btn-primary" onClick={add}
                    disabled={busy || (look.action === 'create' && !name.trim())}>
              Add and issue setup link
            </button>
          </div>
        </>
      )}
    </div>
  )
}
