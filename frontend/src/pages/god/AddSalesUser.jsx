/**
 * + ADD SALES USER — the flow that did not exist.
 *
 * EMAIL FIRST, ALWAYS. The form asks for one thing, looks the address up, and
 * only then decides what to ask next. That order is the whole duplicate story:
 * a name field shown before the lookup invites somebody to fill it in and
 * create a second row for a human who is already here. The lookup route writes
 * nothing, so this step is free to be taken every time.
 *
 * WHAT THE OPERATOR SEES WHEN THE PERSON EXISTS. Every membership they already
 * hold, including in other brands and inside customer organisations. Giving a
 * customer advisor a sales seat is a legitimate and deliberate act; discovering
 * afterwards that you did it is not. The name field disappears, because their
 * name is already a fact and this screen does not get to rewrite it.
 *
 * THE LINK IS THE LAST STEP, NOT A SEPARATE ERRAND. Adding somebody and giving
 * them a way in used to be three disconnected actions. It ends here with the
 * one-time link on screen.
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { errText, whenExact } from './GodOpsShared'

const ROLES = [
  { value: 'sales_rep', label: 'Sales Representative',
    hint: 'Sees and works their own book. No team surfaces.' },
  { value: 'sales_manager', label: 'Sales Manager',
    hint: 'Everything a rep has, plus the whole brand: Team Command, team pipeline, reassignment, approvals.' },
]

export default function AddSalesUser({ brandId, brandName, managers, onClose, onAdded }) {
  const [email, setEmail] = useState('')
  const [looked, setLooked] = useState(null)     // null | {exists:false} | identity summary
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState('sales_rep')
  const [reportsTo, setReportsTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function lookup() {
    const e = email.trim().toLowerCase()
    if (!e) return
    setBusy(true); setErr(''); setLooked(null)
    try {
      const r = await api.get('/god/ops/brands/' + brandId +
                              '/identity-lookup?email=' + encodeURIComponent(e))
      setLooked(r)
      if (r.exists && r.membership_here) {
        setRole(r.membership_here.role)
      }
    } catch (e2) { setErr(errText(e2)) } finally { setBusy(false) }
  }

  async function submit() {
    setBusy(true); setErr('')
    try {
      const body = {
        email: email.trim().toLowerCase(),
        role,
        send_setup_link: true,
        base_url: window.location.origin,
      }
      if (!looked?.exists) body.full_name = fullName.trim()
      if (role === 'sales_rep' && reportsTo) body.reports_to_user_id = reportsTo
      const r = await api.post('/god/ops/brands/' + brandId + '/sales-team', body)
      onAdded(r)
    } catch (e) { setErr(errText(e)); setBusy(false) }
  }

  const exists = !!looked?.exists
  const alreadyHere = !!looked?.already_in_this_brand
  const canSubmit = looked !== null && !busy &&
    (exists || fullName.trim().length >= 2)

  return (
    <div className="go-modal-back" onClick={onClose}>
      <div className="go-modal" onClick={e => e.stopPropagation()}>
        <div className="go-modal-h">
          <h3>Add a sales user to {brandName}</h3>
          <button className="go-btn sm ghost" onClick={onClose}>Close</button>
        </div>

        <div className="go-body">
          {err ? <div className="go-note err">{err}</div> : null}

          {/* ── step 1: the address ── */}
          <label className="go-label">Email address</label>
          <div className="go-actions" style={{ marginBottom: 4 }}>
            <input
              className="go-input"
              style={{ flex: '1 1 260px' }}
              placeholder="person@company.com"
              value={email}
              disabled={busy}
              onChange={e => { setEmail(e.target.value); setLooked(null) }}
              onKeyDown={e => { if (e.key === 'Enter') lookup() }}
            />
            <button className="go-btn sm" onClick={lookup} disabled={busy || !email.trim()}>
              {busy && looked === null ? 'Checking…' : 'Check'}
            </button>
          </div>
          <p className="go-hint">
            Checked before anything is created, so one person never becomes two rows.
          </p>

          {/* ── step 2: what we found ── */}
          {looked && !exists ? (
            <div className="go-note" style={{ marginTop: 12 }}>
              <strong>No account with that address.</strong> A new identity will be
              created with <em>no customer organisation</em> — brand-sales access
              comes from the membership, never from a tenant.
            </div>
          ) : null}

          {exists ? (
            <div className="go-note warn" style={{ marginTop: 12 }}>
              <strong>{looked.full_name}</strong> already has an account
              ({looked.email}). It will be reused — no second identity is created,
              and nothing they already hold is changed.
              <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                <li>
                  Customer organisation:{' '}
                  {looked.organization_id
                    ? <strong>{looked.organization_id}</strong>
                    : <em>none (brand-sales identity)</em>}
                </li>
                <li>
                  Existing memberships:{' '}
                  {looked.memberships.length
                    ? looked.memberships.map(m => (
                        <span key={m.id} className="go-badge" style={{ marginRight: 5 }}>
                          {(m.scope_name || m.scope_type)} · {m.role.replace('sales_', '')}
                          {m.is_active ? '' : ' (inactive)'}
                        </span>
                      ))
                    : <em>none</em>}
                </li>
                <li>
                  Has signed in before: {looked.has_signed_in ? 'yes' : 'no'}
                </li>
              </ul>
              {alreadyHere ? (
                <p style={{ margin: '8px 0 0' }}>
                  They already hold a{' '}
                  <strong>{looked.membership_here.role.replace('sales_', '')}</strong> seat
                  in this brand
                  {looked.membership_here.is_active ? '' : ' (currently inactive)'}.
                  Continuing updates that seat and issues a fresh link rather than
                  adding a second one.
                </p>
              ) : null}
            </div>
          ) : null}

          {/* ── step 3: the details ── */}
          {looked ? (
            <>
              {!exists ? (
                <>
                  <label className="go-label" style={{ marginTop: 14 }}>Full name</label>
                  <input
                    className="go-input"
                    placeholder="Jordan Wells"
                    value={fullName}
                    disabled={busy}
                    onChange={e => setFullName(e.target.value)}
                  />
                </>
              ) : null}

              <label className="go-label" style={{ marginTop: 14 }}>Role in this brand</label>
              {ROLES.map(r => (
                <label key={r.value} className="go-radio">
                  <input
                    type="radio"
                    name="sales-role"
                    value={r.value}
                    checked={role === r.value}
                    disabled={busy}
                    onChange={() => { setRole(r.value); if (r.value === 'sales_manager') setReportsTo('') }}
                  />
                  <span>
                    <strong>{r.label}</strong>
                    <small>{r.hint}</small>
                  </span>
                </label>
              ))}
              <p className="go-hint">
                These are the only two brand-sales roles the system has. There is no
                Product Specialist role yet, and this screen will not invent one —
                a role string the guards do not know would grant nothing.
              </p>

              {role === 'sales_rep' && managers.length ? (
                <>
                  <label className="go-label" style={{ marginTop: 14 }}>
                    Reports to <span style={{ fontWeight: 400 }}>(optional)</span>
                  </label>
                  <select className="go-input" value={reportsTo} disabled={busy}
                          onChange={e => setReportsTo(e.target.value)}>
                    <option value="">No reporting manager</option>
                    {managers.map(m => (
                      <option key={m.user_id} value={m.user_id}>{m.full_name}</option>
                    ))}
                  </select>
                  <p className="go-hint">
                    Org chart only. Nothing grants or withholds access based on this.
                  </p>
                </>
              ) : null}

              <div className="go-actions" style={{ marginTop: 18 }}>
                <button className="go-btn" onClick={submit} disabled={!canSubmit}>
                  {busy ? 'Working…'
                    : exists ? 'Add existing person to this team'
                             : 'Create identity and add to team'}
                </button>
                <button className="go-btn sm ghost" onClick={onClose} disabled={busy}>
                  Cancel
                </button>
              </div>
              <p className="go-hint" style={{ marginTop: 8 }}>
                No password is created or shown. They will receive a one-time link
                and choose their own.
              </p>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
