/**
 * CUSTOMER 360 — one page per customer, permanently.
 *
 * THE STATUS WORDS ARE THE SERVER'S, NOT THIS FILE'S. Every badge below renders
 * a status string the backend produced by looking at stored state, along with
 * the reason it gave. This screen has no opinion about whether Twilio is
 * working and cannot invent one; if the server says NOT_CONFIGURED, that is
 * what appears, even where a green tick would look better.
 *
 * ENTERING THE CUSTOMER IS A DELIBERATE ACT. The button says so, the banner
 * that follows says whose records you are about to change, and the server
 * writes an audit row. Nothing here creates a membership.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, setOrgContext } from '../../api/client'
import { errText, whenExact } from './GodOpsShared'
import './GodOps.css'

const TONE = {
  CONFIGURED: 'live', PARTIAL: 'ready', NOT_CONFIGURED: 'new',
  NONE: 'blocked',
}

function Status({ s }) {
  if (!s) return null
  return <span className={'go-pill ' + (TONE[s.status] || 'new')}>{s.status.replace(/_/g, ' ')}</span>
}

export default function CustomerDetail() {
  const { orgId } = useParams()
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState('overview')
  const [busy, setBusy] = useState(false)
  const [invite, setInvite] = useState(null)

  const load = useCallback(() => {
    api.get('/god/customers/' + orgId).then(setD).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(load, [load])

  async function enterContext() {
    try {
      const r = await api.post('/god/platform/context/customer/' + orgId, {})
      setOrgContext(orgId, d.customer.name)
      nav('/')
      void r
    } catch (e) { setErr(errText(e)) }
  }

  async function toggleFeature(key) {
    const cur = d.features.enabled || []
    const next = cur.includes(key) ? cur.filter(k => k !== key) : [...cur, key]
    setBusy(true)
    try {
      await api.put('/god/customers/' + orgId + '/features', { enabled: next })
      load()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function activate() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/customers/' + orgId + '/activate',
                     { acknowledge_warnings: true })
      load()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (err && !d) return <div className="go-wrap"><div className="go-err">{err}</div></div>
  if (!d) return <div className="go-wrap"><div className="go-muted">Loading…</div></div>

  const c = d.customer
  const s = d.readiness.sections

  return (
    <div className="go-wrap">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/platform')}>← Platform</button>
          <h1 className="go-h1">{c.name}</h1>
          <p className="go-sub">
            {c.brand || 'No brand'} · {c.industry || '—'} · {c.plan || '—'} ·{' '}
            <span className={'go-pill ' + (c.is_active ? 'live' : 'blocked')}>
              {c.is_active ? 'Active' : 'Suspended'}
            </span>
          </p>
        </div>
        <div className="go-head-actions">
          <button className="go-btn" onClick={enterContext}>Enter customer</button>
        </div>
      </div>

      {err && <div className="go-err go-dismiss" onClick={() => setErr('')}>{err}</div>}

      {d.readiness.blockers.length > 0 && (
        <div className="go-warn">
          <strong>Not ready to activate.</strong>
          <ul className="go-plain-list">
            {d.readiness.blockers.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </div>
      )}

      <div className="go-tabs">
        {['overview', 'locations', 'people', 'features'].map(t => (
          <button key={t} className={'go-tab' + (tab === t ? ' on' : '')}
                  onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="go-card-list">
          {[
            ['Company', s.company], ['Locations', s.locations], ['Users', s.users],
            ['Features', s.features], ['SMS / Twilio', s.communications_sms],
            ['Email', s.communications_email], ['Calendar', s.calendar],
            ['Booking', s.booking], ['AI / automation', s.ai], ['Data', s.data],
          ].map(([label, sec]) => (
            <div key={label} className="go-card go-pad go-row-between">
              <div>
                <div className="go-card-title">{label}</div>
                <div className="go-muted">{sec.reason}</div>
                {sec.verified_against_provider === false && (
                  <div className="go-hint">
                    Not verified against the provider — this reports stored
                    configuration, not a live check.
                  </div>
                )}
              </div>
              <Status s={sec} />
            </div>
          ))}

          <div className="go-actions">
            <button className="go-btn go-btn-primary" disabled={busy || !d.readiness.can_activate}
                    onClick={activate}>
              {c.is_active ? 'Re-run activation checks' : 'Activate customer'}
            </button>
          </div>
        </div>
      )}

      {tab === 'locations' && (
        <div className="go-card-list">
          {d.locations.length === 0 && <div className="go-muted">No locations yet.</div>}
          {d.locations.map(l => (
            <div key={l.id} className="go-card go-pad go-row-between">
              <div>
                <div className="go-card-title">
                  {l.name} {l.is_primary && <span className="go-pill live">Primary</span>}
                </div>
                <div className="go-muted">
                  {[l.city, l.state].filter(Boolean).join(', ') || '—'}
                  {l.phone && <> · {l.phone}</>}
                  {' · '}{l.staff_count} staff
                </div>
              </div>
              <span className={'go-pill ' + (l.operating_hours_status === 'CONFIGURED' ? 'live' : 'new')}>
                {l.operating_hours_status === 'CONFIGURED' ? 'Hours set' : 'No hours'}
              </span>
            </div>
          ))}
        </div>
      )}

      {tab === 'people' && (
        <>
          <AddPerson orgId={orgId} locations={d.locations}
                     onAdded={(r) => { setInvite(r); load() }} />
          {invite && invite.setup_url && (
            <div className="go-card go-pad">
              <div className="go-card-title">One-time setup link</div>
              <p className="go-hint">
                Shown once. No password exists for this account — this link is
                the only way in.
              </p>
              <code className="go-code">{invite.setup_url}</code>
            </div>
          )}
          <table className="go-table">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Locations</th><th>Signed in</th></tr>
            </thead>
            <tbody>
              {d.users.map(u => (
                <tr key={u.id}>
                  <td>{u.full_name}</td>
                  <td className="go-muted">{u.email}</td>
                  <td>{u.role}</td>
                  <td className="go-muted">{u.locations.join(', ') || '—'}</td>
                  <td className="go-muted">
                    {u.has_signed_in ? whenExact(u.last_login_at) : 'Never'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === 'features' && (
        <div className="go-card go-pad">
          <p className="go-hint">
            Enforced by the server. Switching a feature off here refuses its API,
            not just its menu item.
          </p>
          {d.features.available.map(f => (
            <label key={f.key} className="go-check">
              <input type="checkbox" checked={f.enabled} disabled={busy}
                     onChange={() => toggleFeature(f.key)} />
              <span><strong>{f.key}</strong> — {f.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function AddPerson({ orgId, locations, onAdded }) {
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
          {look.action === 'reuse'
            ? <div className="go-note">{look.reason}</div>
            : (
              <>
                <label className="go-label">Full name</label>
                <input className="go-input" value={name}
                       onChange={e => setName(e.target.value)} />
              </>
            )}

          <label className="go-label">Role</label>
          <select className="go-input" value={role} onChange={e => setRole(e.target.value)}>
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
