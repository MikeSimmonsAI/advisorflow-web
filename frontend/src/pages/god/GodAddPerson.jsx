/**
 * GOD MODE — ADD A PERSON.
 *
 * ── THE DEFECT THIS REMOVES ───────────────────────────────────────────────
 * Seating a new salesperson used to mean knowing which screen buried the
 * form: the customer People page is a roster, Users & Identity had no direct
 * way in, and the one real provisioning form lived inside a single brand's
 * detail page. So the commonest administrative act on the platform — "give
 * this person access" — had no obvious front door, and the workaround people
 * reach for when a form is hard to find is making a second account.
 *
 * ── EMAIL FIRST, ALWAYS ───────────────────────────────────────────────────
 * The first control on the screen is the address, and nothing else can be
 * touched until it has been looked up. If somebody already holds it, their
 * identity is reused and everything they already hold is shown BEFORE the new
 * access is chosen — because seating somebody who already sells for another
 * brand is a real decision, not a surprise to discover afterwards.
 *
 * ── TWO HALVES OF THE ESTATE, ONE PERSON ──────────────────────────────────
 * A brand sales / back-office seat and a customer workspace membership are
 * different things and stay different things. A person may legitimately hold
 * both, so both can be granted in one deliberate act — never merged into one
 * ambiguous "access" toggle, and a brand role never quietly hands out customer
 * workspace access.
 *
 * ── NO SECOND PERMISSION SYSTEM ───────────────────────────────────────────
 * Every grant here is an ordinary `add_membership` operation on the existing
 * authority model, applied through the same preview → confirm → audit pipeline
 * Manage Access uses. Nothing on this screen knows what a role means; it asks
 * the server for the roles that exist and posts one of them back.
 *
 * Data:
 *   GET  /god/access/directory
 *   GET  /god/access/identity-lookup?email=…        ← writes nothing
 *   GET  /god/access/sales-managers/{brandId}
 *   POST /god/access/users/{id}/preview             ← existing people only
 *   POST /god/access/provision
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'

const SCOPE_SALES = 'brand_sales_org'
const SCOPE_WORKSPACE = 'customer_org'

const LABEL = {
  fontSize: 10, letterSpacing: '.12em', color: '#6f86a0',
  textTransform: 'uppercase', display: 'block', marginBottom: 6,
}

function Field ({ label, hint, children, width }) {
  return (
    <div style={{ minWidth: width || 240, flex: width ? '0 0 auto' : '1 1 240px' }}>
      <span style={LABEL}>{label}</span>
      {children}
      {hint && <div style={{ color: T.dim, fontSize: 11, marginTop: 5 }}>{hint}</div>}
    </div>
  )
}

/* ── what somebody already holds, so it is seen before anything is added ─── */
function Already ({ lookup }) {
  const rows = [
    ...(lookup.brand_contexts || []).map(r => ({ ...r, where: 'Brand' })),
    ...(lookup.back_office || []).map(r => ({ ...r, where: 'Sales organization' })),
    ...(lookup.workspaces || []).map(r => ({ ...r, where: 'Customer workspace' })),
  ].filter(r => r.state !== 'revoked')

  return (
    <div className="gm-card" style={{ marginBottom: 16, borderColor: 'rgba(120,190,255,.28)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ color: '#fff', fontSize: 16 }}>{lookup.identity?.full_name || '—'}</div>
        <div style={{ color: '#758ba4', fontSize: 12 }}>{lookup.email}</div>
        <StatusBadge tone={lookup.identity?.is_active ? 'ok' : 'off'}>
          {lookup.identity?.is_active ? 'ACTIVE' : 'DEACTIVATED'}
        </StatusBadge>
      </div>
      <p style={{ margin: '10px 0 0', color: '#8fa6bd', fontSize: 12, maxWidth: 820 }}>
        {lookup.note}
      </p>
      {(lookup.summary || []).length > 0 && (
        <ul style={{ margin: '10px 0 0', paddingLeft: 18, color: T.dim, fontSize: 12 }}>
          {lookup.summary.map((s, i) => <li key={i} style={{ marginBottom: 3 }}>{s}</li>)}
        </ul>
      )}
      {rows.length > 0 && (
        <div className="gm-tablewrap" style={{ marginTop: 14 }}>
          <table className="gm-table">
            <thead><tr><th>ALREADY HOLDS</th><th>ROLE</th><th>WHAT IT MEANS</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>
                    <div className="gm-orgname">{r.name}</div>
                    <div className="gm-orgsub">{r.where}</div>
                  </td>
                  <td><span className="gm-pill">{r.role_label || r.role}</span></td>
                  <td style={{ color: '#8fa6bd' }}>{r.means || <NoSource>no description</NoSource>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length === 0 && (
        <div style={{ marginTop: 12, color: T.dim, fontSize: 12 }}>
          They hold no active access anywhere yet.
        </div>
      )}
    </div>
  )
}

export default function GodAddPerson () {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const preset = (params.get('context') || '').toLowerCase()
  const presetBrand = params.get('brand') || ''
  const presetWorkspace = params.get('workspace') || ''

  const [dir, setDir] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // ── step one: who ──
  const [email, setEmail] = useState('')
  const [lookup, setLookup] = useState(null)
  const [looking, setLooking] = useState(false)
  const [fullName, setFullName] = useState('')

  // ── step two: what access ──
  const [wantSales, setWantSales] = useState(preset === 'sales' || preset === 'both')
  const [wantWorkspace, setWantWorkspace] = useState(preset === 'workspace' || preset === 'both')
  const [brandId, setBrandId] = useState(presetBrand)
  const [salesRole, setSalesRole] = useState('')
  const [managerId, setManagerId] = useState('')
  const [managers, setManagers] = useState([])
  const [wsId, setWsId] = useState(presetWorkspace)
  const [wsRole, setWsRole] = useState('')

  // ── step three: the link, the preview, the outcome ──
  const [sendLink, setSendLink] = useState(true)
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const d = await api.get('/god/access/directory')
        if (!alive) return
        setDir(d)
        const rep = (d.sales_roles || []).find(r => r.key === 'sales_rep')
        setSalesRole(rep ? rep.key : (d.sales_roles || [{}])[0].key || '')
        const adv = (d.workspace_roles || []).find(r => r.key === 'advisor')
        setWsRole(adv ? adv.key : (d.workspace_roles || [{}])[0].key || '')
      } catch (e) {
        if (alive) setErr(e?.message || 'Could not load brands and workspaces.')
      } finally { if (alive) setLoading(false) }
    })()
    return () => { alive = false }
  }, [])

  // Real places only. Demo tenants are reachable through Demo Suite access,
  // and seating a real person inside one from here would put live people in
  // the environment the sales demo resets.
  const salesOrgs = useMemo(
    () => (dir?.sales_organizations || []).filter(b => !b.is_demo), [dir])
  const workspaces = useMemo(
    () => (dir?.workspaces || []).filter(o => !o.is_demo), [dir])

  // Who a new salesperson may report to depends on the brand, so the picker
  // reloads with it — offering a manager from another brand would offer a
  // choice the server refuses on submit.
  useEffect(() => {
    let alive = true
    setManagerId(''); setManagers([])
    if (!brandId) return undefined
    ;(async () => {
      try {
        const d = await api.get('/god/access/sales-managers/' + brandId)
        if (alive) setManagers(d.managers || [])
      } catch { if (alive) setManagers([]) }
    })()
    return () => { alive = false }
  }, [brandId])

  function resetPlan () { setPreview(null); setResult(null); setErr('') }

  async function runLookup () {
    const address = email.trim()
    if (!address) { setErr('Type the email address first.'); return }
    setLooking(true); setErr(''); setLookup(null); resetPlan()
    try {
      const d = await api.get('/god/access/identity-lookup?email=' +
                              encodeURIComponent(address))
      setLookup(d)
      if (d.exists) setFullName(d.identity?.full_name || '')
    } catch (e) {
      setErr(e?.message || 'That address could not be looked up.')
    } finally { setLooking(false) }
  }

  const operations = useMemo(() => {
    const ops = []
    if (wantSales && brandId) {
      ops.push({
        op: 'add_membership', scope_type: SCOPE_SALES, scope_id: brandId,
        role: salesRole, reports_to_user_id: managerId || null,
      })
    }
    if (wantWorkspace && wsId) {
      ops.push({
        op: 'add_membership', scope_type: SCOPE_WORKSPACE, scope_id: wsId,
        role: wsRole,
      })
    }
    return ops
  }, [wantSales, brandId, salesRole, managerId, wantWorkspace, wsId, wsRole])

  function nameOf (list, id) {
    const row = (list || []).find(r => r.id === id)
    return row ? row.name : id
  }

  function roleLabel (list, key) {
    const row = (list || []).find(r => r.key === key)
    return row ? row.label : key
  }

  // The sentences the operator confirms. For somebody who already exists the
  // server answers this properly — including what is NOT changing. For a brand
  // new identity there is nobody to preview against yet, so the plan says so
  // in the same words rather than pretending a server preview happened.
  const localPlan = useMemo(() => {
    const out = []
    if (!lookup) return out
    out.push(lookup.exists
      ? `Reuse the existing account for ${lookup.email} — no second identity is created.`
      : `Create one identity for ${lookup.email}.`)
    if (wantSales && brandId) {
      const mgr = managers.find(m => m.user_id === managerId)
      out.push(`Seat them in ${nameOf(salesOrgs, brandId)} as ` +
               `${roleLabel(dir?.sales_roles, salesRole)}` +
               (mgr ? `, reporting to ${mgr.name}.` : '.'))
    }
    if (wantWorkspace && wsId) {
      out.push(`Give them entry to the ${nameOf(workspaces, wsId)} workspace as ` +
               `${roleLabel(dir?.workspace_roles, wsRole)}.`)
    }
    if (wantSales && wantWorkspace) {
      out.push('These stay separate: the brand seat does not grant customer ' +
               'workspace access, and the workspace membership grants nothing ' +
               'in the brand.')
    }
    out.push(sendLink
      ? 'Send a one-time setup link. No password is created, changed or shown.'
      : 'Send no setup link — they can be invited later from Manage Access.')
    return out
  }, [lookup, wantSales, brandId, salesRole, managerId, managers, wantWorkspace,
      wsId, wsRole, sendLink, dir, salesOrgs, workspaces])

  function validate () {
    if (!lookup) return 'Look up the email address first.'
    if (!lookup.exists && !fullName.trim()) return 'A new identity needs a full name.'
    if (lookup.exists && lookup.identity && lookup.identity.is_active === false) {
      return 'This account is deactivated. Reactivate it from Manage Access ' +
             'before giving it new access.'
    }
    if (!wantSales && !wantWorkspace) return 'Choose at least one kind of access.'
    if (wantSales && !brandId) return 'Choose the sales organization.'
    if (wantWorkspace && !wsId) return 'Choose the customer workspace.'
    return ''
  }

  async function runPreview () {
    const bad = validate()
    if (bad) { setErr(bad); return }
    setErr(''); setPreview(null)
    if (!lookup.exists) { setPreview({ local: true }); return }
    setBusy(true)
    try {
      const d = await api.post(
        '/god/access/users/' + lookup.user_id + '/preview', { operations })
      setPreview(d)
    } catch (e) {
      setErr(e?.message || 'That change could not be previewed.')
    } finally { setBusy(false) }
  }

  async function confirm () {
    const bad = validate()
    if (bad) { setErr(bad); return }
    setBusy(true); setErr('')
    try {
      const out = await api.post('/god/access/provision', {
        email: email.trim(),
        full_name: fullName.trim() || null,
        operations,
        send_setup_link: sendLink,
        base_url: window.location.origin,
      })
      setResult(out)
      setPreview(null)
    } catch (e) {
      setErr(e?.message || 'The person could not be added.')
    } finally { setBusy(false) }
  }

  async function copyLink (url) {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2200)
    } catch { setErr('Copying failed — select the link and copy it by hand.') }
  }

  function startOver () {
    setEmail(''); setLookup(null); setFullName('')
    setBrandId(presetBrand); setWsId(presetWorkspace)
    setManagerId(''); setManagers([])
    setPreview(null); setResult(null); setErr(''); setSendLink(true)
  }

  const ready = !validate()
  const heading = preset === 'sales' ? 'Add a salesperson' : 'Add a person'

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1180, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }}
                  onClick={() => navigate('/god/users-all')}>
            ← USERS &amp; IDENTITY
          </button>
          <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            {heading}
          </h1>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 780 }}>
            One person is one identity. Start with their email address: if they
            already have an account it is reused and the new access is added to
            it, so nobody ever ends up with two logins and half a history.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'rgba(255,93,125,.35)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}

        {loading && (
          <div className="gm-card"><div className="gm-empty">Loading brands and workspaces…</div></div>
        )}

        {/* ── DONE ───────────────────────────────────────────────────────── */}
        {!loading && result && (
          <>
            <SectionLabel note="what now exists">DONE</SectionLabel>
            <div className="gm-card" style={{ borderColor: 'rgba(74,222,128,.35)', marginBottom: 18 }}>
              <div style={{ color: '#fff', fontSize: 16 }}>
                {result.full_name || result.email}
              </div>
              <div style={{ color: '#758ba4', fontSize: 12, marginTop: 4 }}>
                {result.email} · {result.created_identity
                  ? 'new identity created'
                  : 'existing identity reused — no second account was made'}
              </div>
              <ul style={{ margin: '12px 0 0', paddingLeft: 18, color: '#8fa6bd', fontSize: 12 }}>
                {(result.applied || []).length === 0 && (
                  <li>They already held everything that was asked for — nothing needed changing.</li>
                )}
                {(result.applied || []).map((s, i) => <li key={i} style={{ marginBottom: 4 }}>{s}</li>)}
              </ul>
            </div>

            {result.activation && (
              <>
                <SectionLabel note="shown once — it cannot be shown again">
                  {result.activation.purpose === 'reset' ? 'PASSWORD RESET LINK' : 'SETUP LINK'}
                </SectionLabel>
                <div className="gm-card" style={{ marginBottom: 18 }}>
                  <div style={{
                    background: 'rgba(10,18,28,.75)', border: '1px solid rgba(120,150,190,.22)',
                    borderRadius: 6, padding: '10px 12px', color: '#cfe0f2',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    fontSize: 12, wordBreak: 'break-all',
                  }}>{result.activation.setup_url}</div>
                  <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button className="gm-btn gm-primary"
                            onClick={() => copyLink(result.activation.setup_url)}>
                      {copied ? 'COPIED' : 'COPY LINK'}
                    </button>
                    <span style={{ color: T.dim, fontSize: 11 }}>
                      {result.activation.warning}
                    </span>
                  </div>
                </div>
              </>
            )}

            {result.activation_error && (
              <div className="gm-card" style={{ borderColor: 'rgba(255,196,84,.35)', marginBottom: 18 }}>
                <div style={{ color: T.amber || '#ffc454', fontSize: 12 }}>
                  The access was granted, but the setup link could not be sent:
                  {' '}{result.activation_error} — send one from Manage Access.
                </div>
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="gm-btn gm-primary"
                      onClick={() => navigate('/god/access/' + result.user_id)}>
                OPEN MANAGE ACCESS
              </button>
              <button className="gm-btn" onClick={startOver}>ADD ANOTHER PERSON</button>
              <button className="gm-btn" onClick={() => navigate('/god/users-all')}>
                BACK TO USERS &amp; IDENTITY
              </button>
            </div>
          </>
        )}

        {/* ── 1. WHO ─────────────────────────────────────────────────────── */}
        {!loading && !result && (
          <>
            <SectionLabel note="the address decides everything after it">
              1 · WHO IS THIS
            </SectionLabel>
            <div className="gm-card" style={{ marginBottom: 18 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <Field label="Email address" width={420}
                       hint="Checked against every account on the platform before anything is created.">
                  <input className="gm-input" type="email" autoComplete="off"
                         placeholder="person@company.com"
                         value={email}
                         onChange={e => { setEmail(e.target.value); setLookup(null); resetPlan() }}
                         onKeyDown={e => { if (e.key === 'Enter') runLookup() }}
                         style={{ width: '100%' }} />
                </Field>
                <button className="gm-btn gm-primary" onClick={runLookup}
                        disabled={looking || !email.trim()}>
                  {looking ? 'CHECKING…' : 'LOOK UP'}
                </button>
              </div>

              {lookup && !lookup.exists && (
                <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid rgba(120,150,190,.16)' }}>
                  <div style={{ color: '#8fa6bd', fontSize: 12, marginBottom: 12 }}>
                    {lookup.note}
                  </div>
                  <Field label="Full name" width={320}
                         hint="How they will appear to their team and on everything they touch.">
                    <input className="gm-input" value={fullName}
                           onChange={e => setFullName(e.target.value)}
                           placeholder="Dana Whitfield" style={{ width: '100%' }} />
                  </Field>
                </div>
              )}
            </div>

            {lookup && lookup.exists && <Already lookup={lookup} />}
          </>
        )}

        {/* ── 2. WHAT ACCESS ─────────────────────────────────────────────── */}
        {!loading && !result && lookup && (
          <>
            <SectionLabel note="a person may hold either, or both">
              2 · WHAT ACCESS
            </SectionLabel>

            <div className="gm-card" style={{ marginBottom: 14 }}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
                <input type="checkbox" checked={wantSales} style={{ marginTop: 3 }}
                       onChange={e => { setWantSales(e.target.checked); resetPlan() }} />
                <span>
                  <span style={{ color: '#fff', fontSize: 14 }}>Brand sales &amp; back office</span>
                  <span style={{ display: 'block', color: T.dim, fontSize: 12, marginTop: 3 }}>
                    They sell for one of our brands. Grants a seat in that sales
                    organization — and nothing inside any customer's workspace.
                  </span>
                </span>
              </label>

              {wantSales && (
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 16,
                              paddingTop: 16, borderTop: '1px solid rgba(120,150,190,.16)' }}>
                  <Field label="Sales organization">
                    <select className="gm-input" value={brandId} style={{ width: '100%' }}
                            onChange={e => { setBrandId(e.target.value); resetPlan() }}>
                      <option value="">Choose…</option>
                      {salesOrgs.map(b => (
                        <option key={b.id} value={b.id}>
                          {b.name}{b.platform_name ? ` — ${b.platform_name}` : ''}
                          {b.is_active ? '' : ' (inactive)'}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Role">
                    <select className="gm-input" value={salesRole} style={{ width: '100%' }}
                            onChange={e => { setSalesRole(e.target.value); resetPlan() }}>
                      {(dir?.sales_roles || []).map(r => (
                        <option key={r.key} value={r.key}>{r.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Reports to"
                         hint={brandId
                           ? (managers.length
                             ? 'Optional. Only active managers of this brand can be named.'
                             : 'This brand has no sales manager yet — leave it unset.')
                           : 'Choose the sales organization first.'}>
                    <select className="gm-input" value={managerId} style={{ width: '100%' }}
                            disabled={!brandId || managers.length === 0}
                            onChange={e => { setManagerId(e.target.value); resetPlan() }}>
                      <option value="">Nobody</option>
                      {managers.map(m => (
                        <option key={m.user_id} value={m.user_id}>{m.name}</option>
                      ))}
                    </select>
                  </Field>
                </div>
              )}
            </div>

            <div className="gm-card" style={{ marginBottom: 18 }}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
                <input type="checkbox" checked={wantWorkspace} style={{ marginTop: 3 }}
                       onChange={e => { setWantWorkspace(e.target.checked); resetPlan() }} />
                <span>
                  <span style={{ color: '#fff', fontSize: 14 }}>Customer workspace</span>
                  <span style={{ display: 'block', color: T.dim, fontSize: 12, marginTop: 3 }}>
                    They work inside one customer's account — their leads,
                    conversations and calendar. Nothing in the brand's own
                    sales organization comes with it.
                  </span>
                </span>
              </label>

              {wantWorkspace && (
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 16,
                              paddingTop: 16, borderTop: '1px solid rgba(120,150,190,.16)' }}>
                  <Field label="Workspace">
                    <select className="gm-input" value={wsId} style={{ width: '100%' }}
                            onChange={e => { setWsId(e.target.value); resetPlan() }}>
                      <option value="">Choose…</option>
                      {workspaces.map(o => (
                        <option key={o.id} value={o.id}>
                          {o.name}{o.platform_name ? ` — ${o.platform_name}` : ''}
                          {o.is_active ? '' : ' (inactive)'}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Role">
                    <select className="gm-input" value={wsRole} style={{ width: '100%' }}
                            onChange={e => { setWsRole(e.target.value); resetPlan() }}>
                      {(dir?.workspace_roles || []).map(r => (
                        <option key={r.key} value={r.key}>{r.label}</option>
                      ))}
                    </select>
                  </Field>
                </div>
              )}
            </div>

            {/* ── 3. THE LINK ──────────────────────────────────────────── */}
            <SectionLabel note="no password is ever created or shown">
              3 · HOW THEY GET IN
            </SectionLabel>
            <div className="gm-card" style={{ marginBottom: 18 }}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
                <input type="checkbox" checked={sendLink} style={{ marginTop: 3 }}
                       onChange={e => { setSendLink(e.target.checked); resetPlan() }} />
                <span>
                  <span style={{ color: '#fff', fontSize: 14 }}>
                    Issue a one-time {lookup.exists && lookup.identity?.last_login_at
                      ? 'password reset link' : 'setup link'}
                  </span>
                  <span style={{ display: 'block', color: T.dim, fontSize: 12, marginTop: 3 }}>
                    Shown once, on the next screen, and not recoverable. Leave it
                    off for somebody who already signs in — you can always send
                    one later from Manage Access.
                  </span>
                </span>
              </label>
            </div>
          </>
        )}

        {/* ── 4. PREVIEW AND CONFIRM ─────────────────────────────────────── */}
        {!loading && !result && lookup && (
          <>
            <SectionLabel note="read it, then press the button">
              4 · WHAT WILL HAPPEN
            </SectionLabel>

            <div className="gm-card" style={{ marginBottom: 18 }}>
              <ul style={{ margin: 0, paddingLeft: 18, color: '#8fa6bd', fontSize: 12.5 }}>
                {localPlan.map((s, i) => <li key={i} style={{ marginBottom: 5 }}>{s}</li>)}
              </ul>

              {preview && !preview.local && (
                <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid rgba(120,150,190,.16)' }}>
                  <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
                    <div>
                      <span style={LABEL}>Adding</span>
                      {(preview.adding || []).length === 0
                        ? <div style={{ color: T.dim, fontSize: 12 }}>Nothing.</div>
                        : (preview.adding || []).map((c, i) => (
                          <div key={i} style={{ color: '#4ade80', fontSize: 12, marginBottom: 4 }}>+ {c.sentence}</div>))}
                    </div>
                    <div>
                      <span style={LABEL}>Changing</span>
                      {(preview.changing || []).length === 0
                        ? <div style={{ color: T.dim, fontSize: 12 }}>Nothing.</div>
                        : (preview.changing || []).map((c, i) => (
                          <div key={i} style={{ color: '#ffc454', fontSize: 12, marginBottom: 4 }}>~ {c.sentence}</div>))}
                    </div>
                    <div>
                      <span style={LABEL}>Removing</span>
                      {(preview.removing || []).length === 0
                        ? <div style={{ color: T.dim, fontSize: 12 }}>Nothing is taken away.</div>
                        : (preview.removing || []).map((c, i) => (
                          <div key={i} style={{ color: T.red, fontSize: 12, marginBottom: 4 }}>− {c.sentence}</div>))}
                    </div>
                    <div>
                      <span style={LABEL}>Already true</span>
                      {(preview.unchanged || []).length === 0
                        ? <div style={{ color: T.dim, fontSize: 12 }}>Nothing.</div>
                        : (preview.unchanged || []).map((c, i) => (
                          <div key={i} style={{ color: '#8fa6bd', fontSize: 12, marginBottom: 4 }}>= {c.sentence}</div>))}
                    </div>
                  </div>

                  <div style={{ marginTop: 16 }}>
                    <span style={LABEL}>Not affected</span>
                    <ul style={{ margin: 0, paddingLeft: 18, color: '#8fa6bd', fontSize: 12 }}>
                      {(preview.preserved || []).map((s, i) => <li key={i} style={{ marginBottom: 3 }}>{s}</li>)}
                    </ul>
                  </div>

                  {(preview.warnings || []).length > 0 && (
                    <div style={{ marginTop: 16 }}>
                      <span style={LABEL}>Worth knowing</span>
                      {(preview.warnings || []).map((w, i) => (
                        <div key={i} style={{ color: '#ffc454', fontSize: 12, marginBottom: 4 }}>{w}</div>))}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              {/* Only offered for somebody who already exists. For a new
                  identity there is nobody to preview against, so the list
                  above IS the preview — and a button that answers nothing is
                  worse than no button. */}
              {lookup.exists && (
                <button className="gm-btn" onClick={runPreview} disabled={busy || !ready}>
                  {busy ? '…' : 'PREVIEW'}
                </button>
              )}
              <button className="gm-btn gm-primary" onClick={confirm} disabled={busy || !ready}>
                {busy ? 'WORKING…' : (lookup.exists ? 'CONFIRM AND ADD ACCESS' : 'CONFIRM AND CREATE')}
              </button>
              {!ready && (
                <span style={{ color: T.dim, fontSize: 11 }}>{validate()}</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
