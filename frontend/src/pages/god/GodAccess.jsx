/**
 * GOD MODE — MANAGE ACCESS.
 *
 * ── THE SCREEN'S ONE JOB ──────────────────────────────────────────────────
 * Make a person's whole access footprint READABLE, and make correcting it
 * something an owner does in one deliberate step rather than something an
 * engineer does in a database.
 *
 * The failure it exists to remove: somebody provisioned into the wrong brand
 * used to be fixed by deleting them and creating a second copy — which throws
 * away their login, their history and every reference to them. Here it is a
 * move: the plan says exactly what is being added, what is being removed, and
 * — just as importantly — what is NOT changing, and the server creates the new
 * access and verifies it BEFORE anything is taken away.
 *
 * ── WHY THE UNCHANGED COLUMN IS NOT DECORATION ────────────────────────────
 * The fear an operator has when correcting somebody is that they are about to
 * lose that person's history. `preserved` answers it in words, every time, and
 * it is what makes the confirm button pressable.
 *
 * ── NO UUIDs ──────────────────────────────────────────────────────────────
 * Ids are carried in state and posted back; they are never the thing on screen.
 * Every picker shows a name, every row says what the access MEANS in a
 * sentence, and the advanced detail is available but never in the way.
 *
 * Data:
 *   GET  /god/access/directory
 *   GET  /god/access/users/{id}
 *   POST /god/access/users/{id}/preview      ← writes nothing, ever
 *   POST /god/access/users/{id}/apply
 *   GET  /god/access/users/{id}/audit
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'

const SCOPE_PLATFORM = 'platform'
const SCOPE_SALES = 'brand_sales_org'
const SCOPE_WORKSPACE = 'customer_org'

const PICKER_SOURCE = {
  platform: 'platforms',
  sales_organization: 'sales_organizations',
  workspace: 'workspaces',
}

const STATE_TONE = { active: 'ok', revoked: 'off' }

function when (iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

/* ── the person picker, for arriving here without a user id ──────────────── */
function Picker () {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const data = await api.get('/god/users?scope=all&limit=500')
      setRows(data.users || [])
    } catch (e) { setErr(e?.message || 'Could not load people.'); setRows([]) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => {
    if (!rows) return []
    const needle = q.trim().toLowerCase()
    if (!needle) return rows.slice(0, 60)
    return rows.filter(u =>
      (u.full_name || '').toLowerCase().includes(needle) ||
      (u.email || '').toLowerCase().includes(needle)).slice(0, 60)
  }, [rows, q])

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>
        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
              Manage Access
            </h1>
            {/* Somebody who is not in this list yet is the commonest reason to
                arrive here, so the way to add them is on this screen. */}
            <button className="gm-btn gm-primary" onClick={() => navigate('/god/access/new')}>
              + ADD PERSON
            </button>
          </div>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 760 }}>
            Choose a person. Their whole footprint — brands, sales
            organizations, customer workspaces, executive portfolio, Demo Suite
            access and training — is on one screen, and correcting where they
            sit never deletes them.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'rgba(255,93,125,.35)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}

        <div className="gm-filters">
          <input className="gm-input" placeholder="Search by name or email…"
                 value={q} onChange={e => setQ(e.target.value)} style={{ minWidth: 300 }} />
          <button className="gm-btn" onClick={load} disabled={loading}>
            {loading ? '…' : 'REFRESH'}
          </button>
          <span style={{ color: T.dim, fontSize: 11 }}>
            {rows ? `${filtered.length} of ${rows.length}` : ''}
          </span>
        </div>

        <SectionLabel note="one row per human">PEOPLE</SectionLabel>
        <div className="gm-card" style={{ padding: 0 }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead><tr>
                <th>IDENTITY</th><th>PLATFORM ROLE</th><th>ORGANIZATION</th>
                <th>CONTEXTS</th><th>STATUS</th><th></th>
              </tr></thead>
              <tbody>
                {loading && <tr><td className="gm-empty" colSpan={6}>Loading people…</td></tr>}
                {!loading && filtered.length === 0 &&
                  <tr><td className="gm-empty" colSpan={6}>Nobody matches that.</td></tr>}
                {filtered.map(u => (
                  <tr key={u.id}>
                    <td>
                      <div className="gm-orgname">{u.full_name || '—'}</div>
                      <div className="gm-orgsub">{u.email}</div>
                    </td>
                    <td><span className="gm-pill">{u.role}</span></td>
                    <td>{u.organization_name || <NoSource>control plane</NoSource>}</td>
                    <td>{(u.memberships || []).filter(m => m.is_active).length}</td>
                    <td><StatusBadge tone={u.is_active ? 'ok' : 'off'}>
                      {u.is_active ? 'ACTIVE' : 'INACTIVE'}</StatusBadge></td>
                    <td>
                      <div className="gm-acts">
                        <button className="gm-act gm-primary"
                                onClick={() => navigate('/god/access/' + u.id)}>
                          MANAGE ACCESS
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── one section of the footprint ────────────────────────────────────────── */
function ContextGroup ({ title, note, rows, onChange, onRemove, emptyText }) {
  if (!rows) return null
  return (
    <>
      <SectionLabel note={note}>{title}</SectionLabel>
      <div className="gm-card" style={{ padding: 0, marginBottom: 18 }}>
        <div className="gm-tablewrap">
          <table className="gm-table">
            <thead><tr>
              <th>WHERE</th><th>ROLE</th><th>WHAT IT MEANS</th><th>STATE</th><th></th>
            </tr></thead>
            <tbody>
              {rows.length === 0 &&
                <tr><td className="gm-empty" colSpan={5}>{emptyText}</td></tr>}
              {rows.map(r => (
                <tr key={r.membership_id}>
                  <td>
                    <div className="gm-orgname">{r.name || 'unknown'}</div>
                    {r.platform_name &&
                      <div className="gm-orgsub">{r.platform_name}</div>}
                    {r.resolves === false &&
                      <div className="gm-orgsub" style={{ color: T.red }}>
                        points at something that no longer exists
                      </div>}
                  </td>
                  <td><span className="gm-pill blue">{r.role_label}</span></td>
                  <td style={{ maxWidth: 420, color: T.dim, fontSize: 11.5 }}>{r.means}</td>
                  <td><StatusBadge tone={STATE_TONE[r.state] || 'off'}>
                    {r.state.toUpperCase()}</StatusBadge></td>
                  <td>
                    {r.is_active && (
                      <div className="gm-acts">
                        {onChange && (
                          <button className="gm-act" onClick={() => onChange(r)}>CHANGE ROLE</button>
                        )}
                        <button className="gm-act gm-danger" onClick={() => onRemove(r)}>REMOVE</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

function PlanRow ({ op, onDrop }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
      border: '1px solid ' + T.line, borderRadius: 8, marginBottom: 8,
      background: 'rgba(57,189,248,.05)',
    }}>
      <span className="gm-pill blue" style={{ flexShrink: 0 }}>{op.label}</span>
      <span style={{ flex: 1, fontSize: 12, color: T.text }}>{op.describe}</span>
      <button className="gm-act gm-danger" onClick={onDrop}>DROP</button>
    </div>
  )
}

/* ── the screen ──────────────────────────────────────────────────────────── */
export default function GodAccess () {
  const { userId } = useParams()
  const navigate = useNavigate()
  const me = getCurrentUser()

  const [fp, setFp] = useState(null)
  const [dir, setDir] = useState(null)
  const [audit, setAudit] = useState([])
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)

  const [plan, setPlan] = useState([])            // [{op, label, describe, body}]
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [link, setLink] = useState(null)          // a one-time setup/reset link
  const [copied, setCopied] = useState(false)

  // Add-access builder state
  const [addScope, setAddScope] = useState(SCOPE_WORKSPACE)
  const [addTarget, setAddTarget] = useState('')
  const [addRole, setAddRole] = useState('advisor')
  const [tplKey, setTplKey] = useState('')
  const [tplTarget, setTplTarget] = useState('')
  const [trainKey, setTrainKey] = useState('')
  const [trainDue, setTrainDue] = useState('')

  const load = useCallback(async () => {
    if (!userId) return
    setLoading(true); setErr('')
    try {
      const [a, b, c] = await Promise.all([
        api.get('/god/access/users/' + userId),
        api.get('/god/access/directory'),
        api.get('/god/access/users/' + userId + '/audit?limit=25'),
      ])
      setFp(a); setDir(b); setAudit(c.entries || [])
    } catch (e) {
      setErr(e?.message || 'Could not load this person.')
      setFp(null)
    } finally { setLoading(false) }
  }, [userId])
  useEffect(() => { load() }, [load])

  const directoryFor = useCallback((scope) => {
    if (!dir) return []
    if (scope === SCOPE_PLATFORM) return dir.platforms
    if (scope === SCOPE_SALES) return dir.sales_organizations
    return dir.workspaces
  }, [dir])

  const rolesFor = useCallback((scope) => {
    if (!dir) return []
    if (scope === SCOPE_PLATFORM) return [{ key: 'brand_executive', label: 'Executive' }]
    if (scope === SCOPE_SALES) return dir.sales_roles
    return [...dir.workspace_roles, { key: 'brand_executive', label: 'Executive portfolio assignment' }]
  }, [dir])

  useEffect(() => {
    const roles = rolesFor(addScope)
    if (roles.length && !roles.some(r => r.key === addRole)) setAddRole(roles[0].key)
    setAddTarget('')
  }, [addScope])                                    // eslint-disable-line

  function pushOp (label, describe, body) {
    setPlan(p => [...p, { label, describe, body }])
    setPreview(null)
    setNotice('')
  }

  function nameOf (scope, id) {
    const row = directoryFor(scope).find(r => r.id === id)
    return row ? row.name : id
  }

  function addAccess () {
    if (!addTarget) { setErr('Choose where the access applies.'); return }
    const roleLabel = (rolesFor(addScope).find(r => r.key === addRole) || {}).label
    pushOp('ADD', `${roleLabel} in ${nameOf(addScope, addTarget)}`,
      { op: 'add_membership', scope_type: addScope, scope_id: addTarget, role: addRole })
    setAddTarget('')
  }

  function applyTemplate () {
    if (!tplKey || !tplTarget) { setErr('Choose a template and where it applies.'); return }
    const tpl = dir.templates.find(t => t.key === tplKey)
    const scope = tpl.scope
    pushOp('TEMPLATE', `${tpl.name} — ${nameOf(scope, tplTarget)}`,
      { op: 'apply_template', template: tplKey, scope_id: tplTarget })
    setTplTarget('')
  }

  function changeRole (row) {
    const roles = rolesFor(row.scope_type)
    const next = window.prompt(
      `New role in ${row.name}.\nOne of: ${roles.map(r => r.key).join(', ')}`,
      row.role)
    if (!next || next === row.role) return
    pushOp('CHANGE', `${row.name}: ${row.role_label} → ${next}`,
      { op: 'change_role', scope_type: row.scope_type, scope_id: row.scope_id, role: next })
  }

  function removeAccess (row) {
    pushOp('REMOVE', `${row.role_label} in ${row.name}`,
      { op: 'remove_membership', scope_type: row.scope_type, scope_id: row.scope_id })
  }

  function moveAccess (row) {
    if (!addTarget) { setErr('Pick the destination in "Add access" first, then press MOVE HERE.'); return }
    const roleLabel = (rolesFor(addScope).find(r => r.key === addRole) || {}).label
    pushOp('MOVE', `${row.name} → ${roleLabel} in ${nameOf(addScope, addTarget)}`,
      {
        op: 'move_membership',
        from: { scope_type: row.scope_type, scope_id: row.scope_id },
        to: { scope_type: addScope, scope_id: addTarget, role: addRole },
      })
    setAddTarget('')
  }

  function grantDemo (platformId, admin) {
    pushOp('DEMO', `${admin ? 'Demo Suite + environment administration' : 'Demo Suite access'} for ${nameOf(SCOPE_PLATFORM, platformId)}`,
      { op: 'grant_demo', platform_id: platformId, admin: !!admin })
  }

  function revokeDemo (platformId) {
    pushOp('DEMO', `Remove Demo Suite access for ${nameOf(SCOPE_PLATFORM, platformId)}`,
      { op: 'revoke_demo', platform_id: platformId })
  }

  function assignTraining () {
    if (!trainKey) { setErr('Choose a training path.'); return }
    const path = dir.training_paths.find(p => p.key === trainKey)
    pushOp('TRAINING', `Assign ${path.name}${trainDue ? ` (due ${trainDue})` : ''}`,
      { op: 'assign_training', path_key: trainKey, due_at: trainDue || null })
    setTrainDue('')
  }

  function revokeTraining (pathKey, name) {
    pushOp('TRAINING', `Un-assign ${name}`, { op: 'revoke_training', path_key: pathKey })
  }

  function setActive (want) {
    pushOp(want ? 'ACCOUNT' : 'ACCOUNT', want ? 'Reactivate this account' : 'Deactivate this account',
      { op: 'set_active', is_active: want })
  }

  function clearHome () {
    pushOp('IDENTITY', 'Clear the home organization — becomes a control-plane identity',
      { op: 'set_home_organization', organization_id: null })
  }

  async function sendSetupLink () {
    setBusy(true); setErr(''); setNotice(''); setLink(null)
    try {
      const out = await api.post('/god/access/users/' + userId + '/invite',
        { base_url: window.location.origin })
      setLink(out)
    } catch (e) {
      setErr(e?.message || 'A link could not be issued.')
    } finally { setBusy(false) }
  }

  async function copyLink () {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link.setup_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2200)
    } catch { setErr('Copying failed — select the link and copy it by hand.') }
  }

  async function runPreview () {
    if (!plan.length) { setErr('Nothing to preview — build a change first.'); return }
    setBusy(true); setErr('')
    try {
      setPreview(await api.post('/god/access/users/' + userId + '/preview',
        { operations: plan.map(p => p.body) }))
    } catch (e) { setErr(e?.message || 'Could not preview that change.'); setPreview(null) }
    finally { setBusy(false) }
  }

  async function confirm () {
    setBusy(true); setErr('')
    try {
      const out = await api.post('/god/access/users/' + userId + '/apply',
        { operations: plan.map(p => p.body) })
      setNotice(out.applied && out.applied.length
        ? out.applied.join(' · ')
        : 'Nothing needed changing.')
      setPlan([]); setPreview(null)
      await load()
    } catch (e) { setErr(e?.message || 'The change could not be applied.') }
    finally { setBusy(false) }
  }

  if (!userId) return <Picker />

  const identity = fp?.identity

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }}
                  onClick={() => navigate('/god/users-all')}>
            ← USERS &amp; IDENTITY
          </button>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
              {identity?.full_name || 'Manage Access'}
            </h1>
            {/* "They never got the link" is the commonest real request about
                somebody who has already been provisioned, and it needs a new
                link and nothing else — so it is one button, not a plan. */}
            {identity && (
              <button className="gm-btn" onClick={sendSetupLink}
                      disabled={busy || !identity.is_active}
                      title={identity.is_active ? ''
                        : 'A deactivated account cannot sign in — reactivate it first.'}>
                {busy ? '…' : (identity.last_login_at ? 'SEND RESET LINK' : 'SEND SETUP LINK')}
              </button>
            )}
          </div>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 760 }}>
            {identity?.email}
            {identity && ' · '}
            {identity && (identity.is_internal ? 'control-plane identity' : identity.home_organization_name)}
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'rgba(255,93,125,.35)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}
        {notice && (
          <div className="gm-card" style={{ borderColor: 'rgba(35,239,178,.35)', marginBottom: 16 }}>
            <div style={{ color: T.teal, fontSize: 12 }}>✓ {notice}</div>
          </div>
        )}

        {/* The link is returned once and is not recoverable, which is exactly
            what makes it safe to put on screen. No password is created,
            changed or shown by issuing one. */}
        {link && (
          <div className="gm-card" style={{ borderColor: 'rgba(35,239,178,.35)', marginBottom: 16 }}>
            <SectionLabel note="shown once — it cannot be shown again">
              {link.purpose === 'reset' ? 'PASSWORD RESET LINK' : 'SETUP LINK'}
            </SectionLabel>
            <div style={{
              background: 'rgba(10,18,28,.75)', border: '1px solid rgba(120,150,190,.22)',
              borderRadius: 6, padding: '10px 12px', color: '#cfe0f2',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: 12, wordBreak: 'break-all',
            }}>{link.setup_url}</div>
            <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="gm-btn gm-primary" onClick={copyLink}>
                {copied ? 'COPIED' : 'COPY LINK'}
              </button>
              <button className="gm-btn" onClick={() => setLink(null)}>DISMISS</button>
              <span style={{ color: T.ghost, fontSize: 11 }}>{link.warning}</span>
            </div>
          </div>
        )}

        {loading && <div className="gm-card">Loading this person…</div>}

        {!loading && fp && (
          <>
            {/* ── WHO THIS IS, IN SENTENCES ────────────────────────────── */}
            <div className="gm-card" style={{ marginBottom: 18 }}>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                <StatusBadge tone={identity.is_active ? 'ok' : 'bad'}>
                  {identity.is_active ? 'ACTIVE' : 'DEACTIVATED'}
                </StatusBadge>
                <span className="gm-pill gold">{identity.platform_role_label}</span>
                {identity.is_internal && <span className="gm-pill">CONTROL PLANE</span>}
                {identity.must_change_password && <span className="gm-pill amber">MUST SET PASSWORD</span>}
                <span style={{ color: T.dim, fontSize: 11 }}>
                  last sign-in {when(identity.last_login_at) || 'never'}
                </span>
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, color: T.text, fontSize: 12.5, lineHeight: 1.75 }}>
                {fp.summary.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>

            <ContextGroup title="BRAND CONTEXTS" note="which brand, and as what"
                          rows={fp.brand_contexts} onRemove={removeAccess}
                          emptyText="No brand-level context." />
            <ContextGroup title="SALES BACK OFFICE" note="who they sell for"
                          rows={fp.back_office} onChange={changeRole} onRemove={removeAccess}
                          emptyText="Not a member of any sales organization." />
            <ContextGroup title="CUSTOMER WORKSPACES" note="which customers they can enter"
                          rows={fp.workspaces} onChange={changeRole} onRemove={removeAccess}
                          emptyText="Cannot enter any customer workspace." />
            <ContextGroup title="EXECUTIVE PORTFOLIO" note="visible to them — not enterable"
                          rows={fp.executive_assignments} onRemove={removeAccess}
                          emptyText="No customers assigned to this executive." />

            {/* ── DEMO SUITE ───────────────────────────────────────────── */}
            <SectionLabel note="separate from every production authority">DEMO SUITE ACCESS</SectionLabel>
            <div className="gm-card" style={{ marginBottom: 18 }}>
              <p style={{ margin: '0 0 12px', color: T.dim, fontSize: 11.5 }}>{fp.demo.note}</p>
              {fp.demo.brands.length === 0 && !fp.demo.is_platform_owner &&
                <div className="gm-empty" style={{ marginBottom: 12 }}>
                  No Demo Suite access. This person cannot present any brand.
                </div>}
              {fp.demo.brands.map(b => (
                <div key={b.platform_id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span className="gm-pill teal">{b.platform_name}</span>
                  <span style={{ fontSize: 11.5, color: T.dim }}>
                    can present{b.may_admin ? ' · can rebuild the environment' : ''}
                  </span>
                  <button className="gm-act gm-danger" onClick={() => revokeDemo(b.platform_id)}>REVOKE</button>
                </div>
              ))}
              {dir && (
                <div className="gm-filters" style={{ marginTop: 12 }}>
                  {dir.platforms.map(p => (
                    <span key={p.id} style={{ display: 'inline-flex', gap: 6 }}>
                      <button className="gm-btn" onClick={() => grantDemo(p.id, false)}>
                        GRANT · {p.name}
                      </button>
                      <button className="gm-btn" onClick={() => grantDemo(p.id, true)}
                              title="Also allows rebuilding the shared demonstration environment">
                        + ADMIN
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* ── TRAINING ─────────────────────────────────────────────── */}
            <SectionLabel note="assigned, and how far they got">TRAINING</SectionLabel>
            <div className="gm-card" style={{ padding: 0, marginBottom: 18 }}>
              <div className="gm-tablewrap">
                <table className="gm-table">
                  <thead><tr>
                    <th>PATH</th><th>FOR</th><th>PROGRESS</th><th>STATUS</th><th>DUE</th><th></th>
                  </tr></thead>
                  <tbody>
                    {fp.training.length === 0 &&
                      <tr><td className="gm-empty" colSpan={6}>No training assigned.</td></tr>}
                    {fp.training.map(t => (
                      <tr key={t.path_key}>
                        <td><div className="gm-orgname">{t.name}</div></td>
                        <td>{t.audience_label}</td>
                        <td>{t.completed_steps} / {t.total_steps}</td>
                        <td><StatusBadge tone={t.status === 'complete' ? 'ok'
                          : t.status === 'revoked' ? 'off' : 'pend'}>
                          {t.status.replace('_', ' ').toUpperCase()}</StatusBadge></td>
                        <td>{when(t.due_at) || <NoSource>no date</NoSource>}</td>
                        <td>{t.is_active && (
                          <div className="gm-acts">
                            <button className="gm-act gm-danger"
                                    onClick={() => revokeTraining(t.path_key, t.name)}>UN-ASSIGN</button>
                          </div>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {dir && (
                <div className="gm-filters" style={{ padding: 12 }}>
                  <select className="gm-input" value={trainKey} onChange={e => setTrainKey(e.target.value)}>
                    <option value="">Assign a training path…</option>
                    {dir.training_paths.map(p => (
                      <option key={p.key} value={p.key}>{p.name} — {p.audience_label}</option>
                    ))}
                  </select>
                  <input className="gm-input" type="date" value={trainDue}
                         onChange={e => setTrainDue(e.target.value)} title="Optional due date" />
                  <button className="gm-btn" onClick={assignTraining}>ADD TO PLAN</button>
                </div>
              )}
            </div>

            {/* ── BUILD A CHANGE ───────────────────────────────────────── */}
            <SectionLabel note="nothing happens until you confirm">BUILD A CHANGE</SectionLabel>
            <div className="gm-card" style={{ marginBottom: 18 }}>

              <div style={{ marginBottom: 14 }}>
                <div style={{ color: T.dim, fontSize: 11, marginBottom: 6 }}>ACCESS TEMPLATE</div>
                <div className="gm-filters">
                  <select className="gm-input" value={tplKey}
                          onChange={e => { setTplKey(e.target.value); setTplTarget('') }}>
                    <option value="">Choose a template…</option>
                    {dir?.templates.map(t => <option key={t.key} value={t.key}>{t.name}</option>)}
                  </select>
                  {tplKey && (
                    <select className="gm-input" value={tplTarget}
                            onChange={e => setTplTarget(e.target.value)}>
                      <option value="">Where…</option>
                      {(dir[PICKER_SOURCE[dir.templates.find(t => t.key === tplKey).picker]] || [])
                        .map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select>
                  )}
                  <button className="gm-btn" onClick={applyTemplate}>ADD TO PLAN</button>
                </div>
                {tplKey && (
                  <p style={{ margin: '8px 0 0', fontSize: 11.5, color: T.dim, maxWidth: 760 }}>
                    <strong style={{ color: T.text }}>Grants:</strong>{' '}
                    {dir.templates.find(t => t.key === tplKey).what}<br />
                    <strong style={{ color: T.text }}>Does not grant:</strong>{' '}
                    {dir.templates.find(t => t.key === tplKey).not}
                  </p>
                )}
              </div>

              <div style={{ borderTop: '1px solid ' + T.line, paddingTop: 14 }}>
                <div style={{ color: T.dim, fontSize: 11, marginBottom: 6 }}>ADD ACCESS DIRECTLY</div>
                <div className="gm-filters">
                  <select className="gm-input" value={addScope} onChange={e => setAddScope(e.target.value)}>
                    <option value={SCOPE_WORKSPACE}>Customer workspace</option>
                    <option value={SCOPE_SALES}>Sales organization</option>
                    <option value={SCOPE_PLATFORM}>Brand</option>
                  </select>
                  <select className="gm-input" value={addTarget} onChange={e => setAddTarget(e.target.value)}>
                    <option value="">Where…</option>
                    {directoryFor(addScope).map(o => (
                      <option key={o.id} value={o.id}>
                        {o.name}{o.is_demo ? ' (demonstration)' : ''}
                      </option>
                    ))}
                  </select>
                  <select className="gm-input" value={addRole} onChange={e => setAddRole(e.target.value)}>
                    {rolesFor(addScope).map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
                  </select>
                  <button className="gm-btn" onClick={addAccess}>ADD TO PLAN</button>
                </div>
                <p style={{ margin: '8px 0 0', fontSize: 11, color: T.ghost }}>
                  To MOVE somebody, choose the destination here and then press
                  MOVE HERE on the access they should no longer have. The server
                  creates the new access and verifies it before removing the old.
                </p>
                {fp.workspaces.concat(fp.back_office, fp.brand_contexts)
                  .filter(r => r.is_active).length > 0 && (
                  <div className="gm-filters" style={{ marginTop: 8 }}>
                    {fp.workspaces.concat(fp.back_office, fp.brand_contexts)
                      .filter(r => r.is_active).map(r => (
                        <button key={r.membership_id} className="gm-btn"
                                onClick={() => moveAccess(r)}>
                          MOVE HERE ← {r.name}
                        </button>
                      ))}
                  </div>
                )}
              </div>

              <div style={{ borderTop: '1px solid ' + T.line, paddingTop: 14, marginTop: 14 }}>
                <button className="gm-btn" onClick={() => setShowAdvanced(v => !v)}>
                  {showAdvanced ? 'HIDE' : 'SHOW'} IDENTITY CONTROLS
                </button>
                {showAdvanced && (
                  <div className="gm-filters" style={{ marginTop: 10 }}>
                    {identity.home_organization_id &&
                      <button className="gm-btn" onClick={clearHome}>
                        CLEAR HOME ORGANIZATION
                      </button>}
                    {identity.is_active
                      ? <button className="gm-btn" disabled={identity.user_id === me?.id}
                                title={identity.user_id === me?.id ? 'your own account' : ''}
                                onClick={() => setActive(false)}>DEACTIVATE ACCOUNT</button>
                      : <button className="gm-btn" onClick={() => setActive(true)}>REACTIVATE ACCOUNT</button>}
                    <span style={{ fontSize: 11, color: T.ghost, maxWidth: 520 }}>
                      Platform ownership is never granted or removed from this
                      screen — there is one root authority and a provisioning
                      surface must not be able to mint another.
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* ── THE PLAN ─────────────────────────────────────────────── */}
            {plan.length > 0 && (
              <>
                <SectionLabel note={`${plan.length} change${plan.length === 1 ? '' : 's'} staged`}>
                  PLANNED CHANGES
                </SectionLabel>
                <div className="gm-card" style={{ marginBottom: 18 }}>
                  {plan.map((p, i) => (
                    <PlanRow key={i} op={p} onDrop={() => {
                      setPlan(list => list.filter((_, j) => j !== i)); setPreview(null)
                    }} />
                  ))}
                  <div className="gm-filters" style={{ marginTop: 10 }}>
                    <button className="gm-btn gm-gold-btn" onClick={runPreview} disabled={busy}>
                      {busy ? 'WORKING…' : 'PREVIEW'}
                    </button>
                    <button className="gm-btn" onClick={() => { setPlan([]); setPreview(null) }}>
                      CLEAR PLAN
                    </button>
                  </div>
                </div>
              </>
            )}

            {/* ── PREVIEW → CONFIRM ────────────────────────────────────── */}
            {preview && (
              <>
                <SectionLabel note="read this before confirming">PREVIEW</SectionLabel>
                <div className="gm-card" style={{ marginBottom: 18 }}>
                  <div style={{ display: 'grid', gap: 18, gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))' }}>
                    <div>
                      <div style={{ color: T.teal, fontSize: 11, marginBottom: 6 }}>ADDING</div>
                      {preview.adding.length === 0 && <NoSource>nothing</NoSource>}
                      {preview.adding.map((c, i) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 5 }}>+ {c.sentence}</div>))}
                    </div>
                    <div>
                      <div style={{ color: T.amber, fontSize: 11, marginBottom: 6 }}>CHANGING</div>
                      {preview.changing.length === 0 && <NoSource>nothing</NoSource>}
                      {preview.changing.map((c, i) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 5 }}>~ {c.sentence}</div>))}
                    </div>
                    <div>
                      <div style={{ color: T.red, fontSize: 11, marginBottom: 6 }}>REMOVING</div>
                      {preview.removing.length === 0 && <NoSource>nothing</NoSource>}
                      {preview.removing.map((c, i) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 5 }}>− {c.sentence}</div>))}
                    </div>
                    <div>
                      <div style={{ color: T.blue, fontSize: 11, marginBottom: 6 }}>UNCHANGED</div>
                      {preview.unchanged.length === 0 && <NoSource>nothing</NoSource>}
                      {preview.unchanged.map((c, i) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 5, color: T.dim }}>= {c.sentence}</div>))}
                    </div>
                  </div>

                  <div style={{
                    marginTop: 18, padding: 14, borderRadius: 10,
                    border: '1px solid rgba(35,239,178,.28)', background: 'rgba(35,239,178,.05)',
                  }}>
                    <div style={{ color: T.teal, fontSize: 11, marginBottom: 8 }}>NOT AFFECTED</div>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: T.text, lineHeight: 1.7 }}>
                      {preview.preserved.map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </div>

                  {preview.warnings.length > 0 && (
                    <div style={{
                      marginTop: 12, padding: 14, borderRadius: 10,
                      border: '1px solid rgba(255,199,90,.3)', background: 'rgba(255,199,90,.05)',
                    }}>
                      <div style={{ color: T.amber, fontSize: 11, marginBottom: 8 }}>WORTH KNOWING</div>
                      {preview.warnings.map((w, i) => (
                        <div key={i} style={{ fontSize: 12, marginBottom: 5 }}>{w}</div>))}
                    </div>
                  )}

                  <div className="gm-filters" style={{ marginTop: 16 }}>
                    <button className="gm-btn gm-gold-btn" onClick={confirm}
                            disabled={busy || !preview.requires_confirmation}>
                      {busy ? 'APPLYING…' : 'CONFIRM AND APPLY'}
                    </button>
                    <button className="gm-btn" onClick={() => setPreview(null)}>BACK</button>
                    {!preview.requires_confirmation &&
                      <span style={{ fontSize: 11.5, color: T.dim }}>
                        Nothing in this plan would change anything.
                      </span>}
                  </div>
                </div>
              </>
            )}

            {/* ── AUDIT ────────────────────────────────────────────────── */}
            <SectionLabel note="what has been done to this person, and by whom">HISTORY</SectionLabel>
            <div className="gm-card" style={{ padding: 0 }}>
              <div className="gm-tablewrap">
                <table className="gm-table">
                  <thead><tr><th>WHEN</th><th>ACTION</th><th>BY</th><th>WHAT</th></tr></thead>
                  <tbody>
                    {audit.length === 0 &&
                      <tr><td className="gm-empty" colSpan={4}>
                        No recorded changes to this person's access.</td></tr>}
                    {audit.map(e => (
                      <tr key={e.id}>
                        <td>{when(e.at) || '—'}</td>
                        <td><span className="gm-pill">{e.action}</span></td>
                        <td>{e.actor || <NoSource>system</NoSource>}</td>
                        <td style={{ maxWidth: 560, color: T.dim, fontSize: 11.5 }}>
                          {e.note || <NoSource>no note</NoSource>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
