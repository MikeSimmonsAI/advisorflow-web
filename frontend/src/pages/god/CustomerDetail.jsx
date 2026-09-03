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
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { enterCustomer } from './enterCustomer'
import { errText, whenExact } from './GodOpsShared'
import './GodOps.css'

// `administration` is a SEPARATE TAB from `features` on purpose. They answer
// different questions — what the customer may USE versus who may CONFIGURE the
// infrastructure behind it — and putting them on one screen is how one gets
// changed while the operator believes they changed the other.
const TABS = ['overview', 'locations', 'people', 'features', 'administration']

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
  // ?tab=people lets the command table's USERS action and the identity screen's
  // IN CUSTOMER action land on the right tab instead of on the overview, which
  // is one click and one scan away from what they were asked for.
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState(() => {
    const t = params.get('tab')
    return TABS.includes(t) ? t : 'overview'
  })
  function chooseTab(t) {
    setTab(t)
    // Keep the URL honest so the tab survives a refresh and can be shared.
    const next = new URLSearchParams(params)
    if (t === 'overview') next.delete('tab'); else next.set('tab', t)
    setParams(next, { replace: true })
  }
  const [busy, setBusy] = useState(false)
  const [invite, setInvite] = useState(null)

  const load = useCallback(() => {
    api.get('/god/customers/' + orgId).then(setD).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(load, [load])

  async function enterContext() {
    // The shared helper — the same one the Command Center and the organization
    // command table call. One way into a tenant is one thing to audit.
    try {
      await enterCustomer(orgId, d.customer.name)
      nav('/god/customer-app')
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
        {TABS.map(t => (
          <button key={t} className={'go-tab' + (tab === t ? ' on' : '')}
                  onClick={() => chooseTab(t)}>{t}</button>
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
            <strong>FEATURES ENABLED</strong> — what this customer may USE.
            Enforced by the server: switching a feature off here refuses its API,
            not just its menu item.
          </p>
          <p className="go-hint">
            This is not the same question as who may CONFIGURE the infrastructure
            behind a feature. That lives on the <strong>administration</strong>
            {' '}tab, and enabling SMS here does not hand anyone the Twilio
            account.
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

      {tab === 'administration' && <Administration orgId={orgId} />}
    </div>
  )
}


/* ───────────────────────────────────────────────────────────────────────────
 * ADMINISTRATION — the two delegation gates, stated separately.
 *
 * THREE STATES ARE SHOWN AS THREE BLOCKS AND NONE IS INFERRED FROM ANOTHER:
 *
 *   FEATURES ENABLED        the `features` tab — what the customer may USE
 *   SELF-MANAGEMENT ALLOWED block 1 below — may the ORGANIZATION administer it
 *   AUTHORIZED ADMINISTRATORS block 2 below — who actually holds it
 *
 * Every value comes from GET /god/customers/{id}/administration, which builds
 * them with the same resolver the routes use. This screen computes no
 * authorization of its own — a screen that reimplements the rule is a screen
 * that will eventually disagree with the server it is describing.
 * ───────────────────────────────────────────────────────────────────────── */
function Administration({ orgId }) {
  const [a, setA] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get('/god/customers/' + orgId + '/administration')
      .then(setA).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(load, [load])

  async function toggleSelfManage(key) {
    const cur = a.self_management.allowed || []
    const next = cur.includes(key) ? cur.filter(k => k !== key) : [...cur, key]
    setBusy(true); setErr('')
    try { setA(await api.put('/god/customers/' + orgId + '/self-management',
                             { allowed: next })) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function toggleGrant(userId, key) {
    const person = a.administrators.users.find(u => u.id === userId)
    const cur = person?.capabilities || []
    const next = cur.includes(key) ? cur.filter(k => k !== key) : [...cur, key]
    setBusy(true); setErr('')
    try {
      setA(await api.put(
        '/god/customers/' + orgId + '/users/' + userId + '/capabilities',
        { capabilities: next }))
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (err && !a) return <div className="go-err">{err}</div>
  if (!a) return <div className="go-muted">Loading…</div>

  const delegable = a.self_management.available.filter(c => c.delegable)
  const platformOnly = a.self_management.available.filter(c => !c.delegable)

  return (
    <div className="go-card-list">
      {err && <div className="go-err">{err}</div>}

      {/* ── 2. SELF-MANAGEMENT ALLOWED ─────────────────────────────────── */}
      <div className="go-card go-pad">
        <div className="go-card-title">SELF-MANAGEMENT ALLOWED</div>
        <p className="go-hint">
          May this <strong>organization</strong> administer the infrastructure
          behind a service at all? This is separate from whether they may use
          the service, and separate again from which of their administrators
          holds it. Everything here is off until you switch it on.
        </p>
        {delegable.map(c => (
          <label key={c.key}
                 className={'go-check' + (c.blocked_reason ? ' go-check--off' : '')}>
            <input type="checkbox" checked={c.allowed}
                   disabled={busy || !!c.blocked_reason}
                   onChange={() => toggleSelfManage(c.key)} />
            <span>
              <strong>{c.key}</strong> — {c.label}
              <div className="go-muted">{c.why}</div>
              {c.blocked_reason && <div className="go-hint">{c.blocked_reason}</div>}
            </span>
          </label>
        ))}

        <div className="go-card-title" style={{ marginTop: 16 }}>
          Administered by AdvisorFlow — never delegated
        </div>
        <p className="go-hint">
          These are platform-wide. The server refuses to delegate them, so they
          have no switch rather than a switch that does nothing.
        </p>
        {platformOnly.map(c => (
          <div key={c.key} className="go-muted">
            <strong>{c.key}</strong> — {c.label}
          </div>
        ))}
      </div>

      {/* ── 3. AUTHORIZED ADMINISTRATORS ───────────────────────────────── */}
      <div className="go-card go-pad">
        <div className="go-card-title">AUTHORIZED ADMINISTRATORS</div>
        <p className="go-hint">
          Which specific administrator holds each capability. A grant here does
          nothing until the organization is also allowed to self-manage it —
          both gates must pass, and <strong>effective</strong> below is what the
          person can actually do right now with both applied.
        </p>
        <p className="go-hint">
          Only {a.administrators.eligible_roles.join(' and ')} accounts can be
          listed: {a.administrators.eligible_count} of {a.administrators.users_in_org}
          {' '}people in this organization. Advisors are never eligible for
          infrastructure administration, whatever features they use.
        </p>
        {a.administrators.users.length === 0 && (
          <p className="go-muted">
            No eligible administrators in this organization yet.
          </p>
        )}
        {a.administrators.users.map(u => (
          <div key={u.id} className="go-card go-pad" style={{ marginTop: 12 }}>
            <div className="go-row-between">
              <div>
                <strong>{u.full_name || u.email}</strong>
                <div className="go-muted">{u.email} · {u.role}</div>
              </div>
              <span className={'go-pill ' + (u.effective.length ? 'live' : 'new')}>
                {u.effective.length} effective
              </span>
            </div>
            {delegable.map(c => {
              const granted = u.capabilities.includes(c.key)
              const effective = u.effective.includes(c.key)
              return (
                <label key={c.key} className="go-check">
                  <input type="checkbox" checked={granted} disabled={busy}
                         onChange={() => toggleGrant(u.id, c.key)} />
                  <span>
                    {c.label}
                    {granted && !effective && (
                      <div className="go-hint">
                        Granted, but inert — the organization is not allowed to
                        self-manage this.
                      </div>
                    )}
                  </span>
                </label>
              )
            })}
          </div>
        ))}
      </div>
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
