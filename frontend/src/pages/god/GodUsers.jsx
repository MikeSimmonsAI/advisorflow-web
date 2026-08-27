/**
 * GOD MODE — USERS & IDENTITY.
 *
 * ── THE ONE IDEA THIS SCREEN EXISTS TO PROTECT ────────────────────────────
 * ONE HUMAN IS ONE ROW. A person who owns the platform, sells for EvoSys Pro
 * and administers a customer appears here once, carrying three contexts — not
 * three times. A user list that split them by context would quietly teach the
 * operator that they are three people, which is the exact mistake the
 * centralized identity model exists to prevent.
 *
 * So the columns are: identity · platform · organization · memberships · roles
 * · status. The contexts are the row's contents, never its multiplicity.
 *
 * Data: GET /god/users?scope=... — every context resolved in grouped queries
 * server-side, so this page is a constant number of requests at any user count.
 *
 * Actions are the ones that already exist and nothing else:
 *   POST /god/users/{id}/deactivate · /activate
 * Role changes deliberately are NOT here. PATCH /god/users/{id}/role exists,
 * but promoting somebody to god_admin from a list row is a one-click change to
 * the platform's most privileged set, and the platform-owner count is a health
 * condition on the Command Center. It belongs behind a deliberate screen, not
 * a dropdown in a table.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'
import ConfirmDialog from './ConfirmDialog'

const SCOPES = [
  { key: 'all',      label: 'EVERYONE' },
  { key: 'admins',   label: 'ADMINS' },
  { key: 'internal', label: 'CONTROL PLANE' },
  { key: 'tenant',   label: 'CUSTOMER USERS' },
]

const ROLE_TONE = {
  god_admin: 'gold', super_admin: 'purple', org_admin: 'blue',
  advisor: 'teal', viewer: 'off',
}

function when(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

export default function GodUsers() {
  const navigate = useNavigate()
  const me = getCurrentUser()

  const [scope, setScope] = useState('all')
  const [q, setQ] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      setData(await api.get('/god/users?scope=' + scope + '&limit=500'))
    } catch (e) {
      setErr(e?.message || 'Could not load users.')
      setData(null)
    } finally { setLoading(false) }
  }, [scope])

  useEffect(() => { load() }, [load])

  const users = useMemo(() => {
    const rows = (data && data.users) || []
    const needle = q.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter(u =>
      (u.email || '').toLowerCase().includes(needle) ||
      (u.full_name || '').toLowerCase().includes(needle) ||
      (u.organization_name || '').toLowerCase().includes(needle) ||
      (u.platform_name || '').toLowerCase().includes(needle) ||
      (u.memberships || []).some(m => (m.scope_name || '').toLowerCase().includes(needle))
    )
  }, [data, q])

  const owners = useMemo(
    () => ((data && data.users) || []).filter(u => u.role === 'god_admin' && u.is_active),
    [data]
  )

  function askToggle(u) {
    const off = u.is_active
    setConfirm({
      user: u,
      tone: off ? 'danger' : 'blue',
      eyebrow: off ? '⚠ DEACTIVATE ACCOUNT' : '✓ REACTIVATE ACCOUNT',
      title: u.full_name || u.email,
      body: off
        ? 'This person will be unable to sign in anywhere on the platform — every '
        + 'organization and every membership at once. Their records are untouched '
        + 'and this is reversible.'
        : 'Sign-in is restored. Their existing roles and memberships are unchanged.',
      confirmLabel: off ? 'DEACTIVATE' : 'REACTIVATE',
    })
  }

  async function runConfirm() {
    if (!confirm) return
    const u = confirm.user
    setBusy(u.id); setErr('')
    try {
      await api.post(`/god/users/${u.id}/${u.is_active ? 'deactivate' : 'activate'}`, {})
      setConfirm(null)
      await load()
    } catch (e) {
      setErr(e?.message || 'The action was refused.')
      setConfirm(null)
    } finally { setBusy('') }
  }

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            Users &amp; Identity
          </h1>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 760 }}>
            One row per human. A person who holds platform authority, a
            brand-sales seat and a customer membership is one identity here with
            three contexts — never three accounts.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ padding: '12px 14px', marginBottom: 16,
                                            borderColor: 'rgba(255,93,125,.35)', color: '#ff8299', fontSize: 11 }}>
            {err}
          </div>
        )}

        {/* Platform-owner count is an identity FACT, and the model says it is
            one. Stating it here means a second owner cannot appear quietly. */}
        {!loading && data && (
          <div className="gm-card" style={{
            padding: '12px 14px', marginBottom: 16, fontSize: 11,
            borderColor: owners.length === 1 ? 'rgba(35,239,178,.25)' : 'rgba(255,93,125,.35)',
            color: owners.length === 1 ? '#8fb6cf' : '#ff8299',
          }}>
            {owners.length === 1
              ? <>Platform authority: <strong style={{ color: T.gold }}>{owners[0].full_name || owners[0].email}</strong> is
                  the only active god_admin identity. That is the intended state.</>
              : <>There {owners.length === 0 ? 'is no' : 'are ' + owners.length}
                  {' '}active platform-owner {owners.length === 1 ? 'identity' : 'identities'}.
                  The identity model says there should be exactly one.</>}
          </div>
        )}

        <div className="gm-filters">
          <input className="gm-input" style={{ flex: '1 1 220px', maxWidth: 320 }}
                 value={q} onChange={e => setQ(e.target.value)}
                 placeholder="Search name, email, organization or brand…" />
          <div className="gm-seg">
            {SCOPES.map(s => (
              <button key={s.key} className={scope === s.key ? 'on' : ''}
                      onClick={() => setScope(s.key)}>{s.label}</button>
            ))}
          </div>
          <button className="gm-btn" onClick={load} disabled={loading}>
            {loading ? '…' : '↻ REFRESH'}
          </button>
          <span style={{ color: T.dim, fontSize: 10, marginLeft: 'auto' }}>
            {loading ? 'loading…' : `${users.length} of ${data?.total ?? users.length}`}
          </span>
        </div>

        <SectionLabel note="· deactivating removes sign-in everywhere at once">
          IDENTITIES
        </SectionLabel>

        <div className="gm-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead>
                <tr>
                  <th>IDENTITY</th>
                  <th>PLATFORM ROLE</th>
                  <th>BRAND</th>
                  <th>ORGANIZATION</th>
                  <th>MEMBERSHIPS</th>
                  <th>LAST SIGN-IN</th>
                  <th>STATUS</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={8} className="gm-empty">Loading identities…</td></tr>}
                {!loading && users.length === 0 && (
                  <tr><td colSpan={8} className="gm-empty">No identity matches this filter.</td></tr>
                )}
                {!loading && users.map(u => (
                  <tr key={u.id}>
                    <td>
                      <div className="gm-orgname">
                        {u.full_name || <span style={{ color: T.ghost }}>no name recorded</span>}
                        {me && me.email === u.email
                          ? <span className="gm-pill blue" style={{ marginLeft: 7 }}>YOU</span> : null}
                      </div>
                      <div className="gm-orgsub">{u.email}</div>
                    </td>
                    <td>
                      <span className={'gm-pill ' + (ROLE_TONE[u.role] || 'off')}>
                        {String(u.role || '').replace(/_/g, ' ').toUpperCase()}
                      </span>
                    </td>
                    <td>{u.platform_name || <span style={{ color: T.ghost }}>—</span>}</td>
                    <td>
                      {u.organization_name
                        ? <button className="gm-act"
                                  onClick={() => navigate('/god/customers/' + u.organization_id)}>
                            {u.organization_name}
                          </button>
                        : <span className="gm-pill off"
                                title="organization_id IS NULL — this architecture's positive assertion that somebody belongs to the control plane and to no tenant">
                            CONTROL PLANE
                          </span>}
                    </td>
                    <td>
                      {(u.memberships || []).length === 0
                        ? <span style={{ color: T.ghost }}>none</span>
                        : (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                            {u.memberships.map(m => (
                              <span key={m.id} className={'gm-pill ' + (m.is_active ? 'purple' : 'off')}
                                    title={m.scope_type + ' · ' + m.scope_id}>
                                {String(m.role || '').replace(/_/g, ' ')}
                                {m.scope_name ? ' @ ' + m.scope_name : ''}
                                {m.is_active ? '' : ' (inactive)'}
                              </span>
                            ))}
                          </div>
                        )}
                    </td>
                    <td style={{ whiteSpace: 'nowrap', color: T.dim }}>
                      {when(u.last_login_at) || <NoSource>never</NoSource>}
                    </td>
                    <td>
                      {u.is_active
                        ? (u.must_change_password
                            ? <StatusBadge tone="warn" title="Account created, setup link not used yet">PENDING SETUP</StatusBadge>
                            : <StatusBadge tone="ok">ACTIVE</StatusBadge>)
                        : <StatusBadge tone="bad">DEACTIVATED</StatusBadge>}
                    </td>
                    <td>
                      <div className="gm-acts">
                        {me && me.email === u.email ? (
                          <span style={{ color: T.ghost, fontSize: 8.5 }}>
                            your own account
                          </span>
                        ) : (
                          <button
                            className={'gm-act ' + (u.is_active ? 'gm-danger' : '')}
                            disabled={busy === u.id}
                            onClick={() => askToggle(u)}
                          >
                            {busy === u.id ? '…' : (u.is_active ? 'DEACTIVATE' : 'REACTIVATE')}
                          </button>
                        )}
                        {u.organization_id ? (
                          <button className="gm-act"
                                  onClick={() => navigate('/god/customers/' + u.organization_id + '?tab=people')}>
                            IN CUSTOMER
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <p style={{ marginTop: 16, fontSize: 10, color: T.dim, lineHeight: 1.7, maxWidth: 860 }}>
          Role changes are deliberately not available from this table. <code>PATCH
          /god/users/&#123;id&#125;/role</code> exists, but promoting somebody into the platform's
          most privileged set should not be one click away from a list — and the number of
          platform owners is a health condition on the Command Center for the same reason.
          People are added to a customer from that customer's own page, so the invitation and
          the identity check happen together.
        </p>
      </div>

      {confirm && (
        <ConfirmDialog
          tone={confirm.tone} eyebrow={confirm.eyebrow} title={confirm.title}
          body={confirm.body} confirmLabel={confirm.confirmLabel}
          busy={!!busy} onConfirm={runConfirm} onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
