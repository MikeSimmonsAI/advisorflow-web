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
 * So the columns are: user · platform role · brand · customer · access ·
 * last login · status · actions. The contexts are the row's contents, never
 * its multiplicity.
 *
 * Data: GET /god/users?scope=... — every context resolved in grouped queries
 * server-side, so this page is a constant number of requests at any user count.
 *
 * Actions are the ones that already exist and nothing else:
 *   POST /god/users/{id}/deactivate · /activate
 *   POST /admin/users/{id}/reset-password  — see ResetPasswordDialog
 *
 * The reset lives on /admin rather than /god because that is where the hardened
 * implementation already is: require_super_admin plus load_user_in_scope, which
 * is the pair that closed the August takeover where a platform operator could
 * set the owner's password. A second reset route under /god would be a second
 * place for that guard to be got wrong. A god_admin passes require_super_admin
 * and load_user_in_scope returns any target to them, so the owner reaches every
 * account from here without any rule being widened for anybody else.
 *
 * Role changes deliberately are NOT here. PATCH /god/users/{id}/role exists,
 * but promoting somebody to god_admin from a list row is a one-click change to
 * the platform's most privileged set, and the platform-owner count is a health
 * condition on the Command Center. It belongs behind a deliberate screen, not
 * a dropdown in a table.
 *
 * ── THE LAYOUT CORRECTION (Sep 11 2026) ───────────────────────────────────
 * This table used to render one full-width pill per membership and four
 * equally weighted buttons per row, on top of the shared command table's
 * content-driven sizing. The real width ran past 1700px, so reaching STATUS
 * or DEACTIVATE on a normal desktop meant dragging a horizontal scrollbar —
 * on the screen whose entire job is status and deactivation.
 *
 * NOTHING WAS REMOVED TO FIX IT, and no type was shrunk. ACCESS now states
 * how many contexts an identity holds and the full list opens in an
 * expandable row underneath; MANAGE ACCESS and DEACTIVATE / REACTIVATE stay
 * visible on every row at every width, with only RESET PASSWORD and OPEN IN
 * CUSTOMER moved into a keyboard-reachable overflow. The budget per column
 * lives in `.gm-idtable` in GodStyles.jsx, which is also where the order the
 * columns drop in at narrower widths is written down.
 *
 * AUTHORITY IS UNTOUCHED BY ALL OF THAT. Every endpoint, every guard and
 * every rule about who may do what to whom is exactly what it was; this file
 * changed where things are drawn and nothing about what they do.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'
import ConfirmDialog from './ConfirmDialog'
import ResetPasswordDialog from './ResetPasswordDialog'

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

function whenFull(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString()
}

function roleWords(role) {
  return String(role || '').replace(/_/g, ' ').toUpperCase()
}

/**
 * The row's overflow menu.
 *
 * A real <button> list rather than a styled <select>: every item is keyboard
 * reachable, Escape closes it, and a click anywhere else closes it. The
 * trigger carries an accessible name that includes the person, because "…"
 * twenty-four times over is not a name.
 *
 * IT IS POSITIONED AGAINST THE VIEWPORT. The table region is a scroll
 * container and the card around it clips its overflow, so a menu positioned
 * against its own cell is cut off the moment it is taller than one row —
 * which is how an action ends up unreachable without anyone having hidden it.
 */
function RowMenu({ items, label }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(null)
  const btn = useRef(null)
  const menu = useRef(null)

  useEffect(() => {
    if (!open) return
    const el = btn.current
    if (el) {
      const r = el.getBoundingClientRect()
      setPos({ top: Math.round(r.bottom + 6),
               right: Math.max(8, Math.round(window.innerWidth - r.right)) })
    }
    function onDoc(e) {
      if (btn.current && btn.current.contains(e.target)) return
      if (menu.current && menu.current.contains(e.target)) return
      setOpen(false)
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false) }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [open])

  if (!items.length) return null

  return (
    <span className="gm-menuwrap">
      <button ref={btn} className="gm-act gm-ghost" aria-haspopup="menu" aria-expanded={open}
              aria-label={'More actions for ' + label}
              onClick={() => setOpen(o => !o)}>…</button>
      {open && pos && (
        <div ref={menu} className="gm-menu gm-menu-fixed" role="menu"
             style={{ top: pos.top, right: pos.right }}>
          {items.map((it, i) => it.sep
            ? <div key={'s' + i} className="gm-menu-sep" />
            : (
              <button key={it.label} role="menuitem"
                      className={it.danger ? 'gm-danger-item' : ''}
                      disabled={!!it.disabled}
                      onClick={() => { setOpen(false); it.onClick() }}>
                {it.label}
              </button>
            ))}
        </div>
      )}
    </span>
  )
}

function Field({ label, children }) {
  return (
    <div className="gm-idfield">
      <h5>{label}</h5>
      <p>{children}</p>
    </div>
  )
}

/**
 * ONE IDENTITY, one compact row, and the whole of it one click below.
 *
 * The expanded panel is not a second data source — it is the same fields the
 * row is already carrying, restated in full where they have room to be read.
 * That matters at narrow widths, where a column drops out of the row: what it
 * held is never lost, it is here and it is also summarised under the name.
 */
function IdentityRow({
  u, isMe, busy, open, onToggle,
  onManage, onAskToggle, onReset, onCustomer,
}) {
  const mems = u.memberships || []
  const n = mems.length
  const role = roleWords(u.role)
  const who = u.full_name || u.email
  const orgWords = u.organization_name || 'Control plane'

  // RESET PASSWORD is offered only on accounts that can currently sign in.
  // Setting a password on a deactivated account would read as restoring
  // access when it restores nothing — reactivate first, deliberately.
  const menu = []
  if (!isMe && u.is_active) {
    menu.push({ label: 'Reset password…', onClick: () => onReset(u) })
  }
  if (u.organization_id) {
    menu.push({ label: 'Open in customer', onClick: () => onCustomer(u) })
  }
  if (menu.length) menu.push({ sep: true })
  menu.push({
    label: open ? 'Hide access detail' : 'Show access detail',
    onClick: () => onToggle(u.id),
  })

  return (
    <>
      <tr>
        <td className="c-user">
          <div className="gm-idhead">
            <button className="gm-idtoggle" aria-expanded={open}
                    aria-label={(open ? 'Hide' : 'Show') + ' access detail for ' + who}
                    onClick={() => onToggle(u.id)}>{open ? '▾' : '▸'}</button>
            <div className="gm-idnames">
              <div className="gm-orgname" title={u.full_name || 'no name recorded'}>
                {u.full_name || <span style={{ color: T.ghost }}>no name recorded</span>}
                {isMe ? <span className="gm-pill blue" style={{ marginLeft: 7 }}>YOU</span> : null}
              </div>
              <div className="gm-orgsub" title={u.email}>{u.email}</div>
              {/* Carries whatever the current width has dropped. Always true,
                  so it is never a second version of the row to reconcile. */}
              <div className="gm-idmeta">
                {role}{u.platform_name ? ' · ' + u.platform_name : ''} · {orgWords}
              </div>
            </div>
          </div>
        </td>

        <td className="c-role">
          <span className={'gm-pill ' + (ROLE_TONE[u.role] || 'off')}>{role}</span>
        </td>

        <td className="c-brand" title={u.platform_name || undefined}>
          {u.platform_name || <span style={{ color: T.ghost }}>—</span>}
        </td>

        <td className="c-org">
          {u.organization_name
            ? <button className="gm-act" title={u.organization_name}
                      onClick={() => onCustomer(u)}>
                {u.organization_name}
              </button>
            : <span className="gm-pill off"
                    title="organization_id IS NULL — this architecture's positive assertion that somebody belongs to the control plane and to no tenant">
                CONTROL PLANE
              </span>}
        </td>

        {/* ACCESS — a count, not a wall. Every context is one click away in
            the row below and every one of them is also in Manage Access. */}
        <td className="c-access">
          {n === 0
            ? <span className="gm-idaccess none"
                    title="No memberships beyond the platform role above">
                NO MEMBERSHIPS
              </span>
            : <button className="gm-idaccess" aria-expanded={open}
                      title={'Show every access context ' + who + ' holds'}
                      onClick={() => onToggle(u.id)}>
                {n} ACCESS ROLE{n === 1 ? '' : 'S'}
              </button>}
        </td>

        <td className="c-last" style={{ whiteSpace: 'nowrap', color: T.dim }}>
          {when(u.last_login_at) || <NoSource>never</NoSource>}
        </td>

        <td className="c-status">
          {u.is_active
            ? (u.must_change_password
                ? <StatusBadge tone="warn" title="Account created, setup link not used yet">PENDING SETUP</StatusBadge>
                : <StatusBadge tone="ok">ACTIVE</StatusBadge>)
            : <StatusBadge tone="bad">DEACTIVATED</StatusBadge>}
        </td>

        <td className="c-act">
          <div className="gm-acts">
            {/* MANAGE ACCESS is available for EVERY row, including the
                operator's own, because reading a footprint is a read — and an
                owner who cannot see their own contexts is the one person who
                most needs to. The server refuses the writes that would matter.
                It never moves into the overflow at any width. */}
            <button className="gm-act gm-primary" onClick={() => onManage(u)}>
              MANAGE ACCESS
            </button>
            {isMe
              ? <span className="gm-idself" title="Your own account">YOUR ACCOUNT</span>
              : <button className={'gm-act ' + (u.is_active ? 'gm-danger' : '')}
                        disabled={busy} onClick={() => onAskToggle(u)}>
                  {busy ? '…' : (u.is_active ? 'DEACTIVATE' : 'REACTIVATE')}
                </button>}
            <RowMenu items={menu} label={who} />
          </div>
        </td>
      </tr>

      {/* colSpan is a CONSTANT EIGHT and must stay one: the stylesheet keeps
          all eight columns in the layout at every width, collapsing the dropped
          ones to zero rather than removing them, precisely so this number never
          has to be measured. */}
      {open && (
        <tr className="gm-iddetail">
          <td colSpan={8}>
            <div className="gm-iddetail-in">
              <Field label="Platform role">
                <span className={'gm-pill ' + (ROLE_TONE[u.role] || 'off')}>{role}</span>
              </Field>
              <Field label="Brand">
                {u.platform_name || <span style={{ color: T.ghost }}>no brand context</span>}
              </Field>
              <Field label="Customer / workspace">
                {u.organization_name
                  ? <button className="gm-act" onClick={() => onCustomer(u)}>
                      {u.organization_name}
                    </button>
                  : 'Control plane — belongs to no tenant organization.'}
              </Field>
              <Field label={'Access contexts (' + n + ')'}>
                {n === 0
                  ? <span style={{ color: T.ghost }}>
                      None. This identity holds its platform role and nothing else.
                    </span>
                  : (
                    <span className="gm-idmem">
                      {mems.map(m => (
                        <span key={m.id} className={'gm-pill ' + (m.is_active ? 'purple' : 'off')}
                              title={m.scope_type + ' · ' + m.scope_id}>
                          {String(m.role || '').replace(/_/g, ' ')}
                          {m.scope_name ? ' @ ' + m.scope_name : ''}
                          {m.is_active ? '' : ' (inactive)'}
                        </span>
                      ))}
                    </span>
                  )}
              </Field>
              <Field label="Last login">
                {whenFull(u.last_login_at) || <NoSource>has never signed in</NoSource>}
              </Field>
              <Field label="Account">
                {u.is_active ? 'Active. ' : 'Deactivated — cannot sign in anywhere. '}
                {u.must_change_password
                  ? 'Setup link not used yet; a password must be chosen at next sign-in. '
                  : ''}
                {when(u.created_at) ? 'Created ' + when(u.created_at) + '.' : ''}
              </Field>
              <Field label="Changing any of this">
                This panel is a read. Roles and memberships are edited on the
                MANAGE ACCESS screen, where the authority checks live.
              </Field>
            </div>
          </td>
        </tr>
      )}
    </>
  )
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
  const [resetting, setResetting] = useState(null)
  const [notice, setNotice] = useState('')
  const [expanded, setExpanded] = useState(() => new Set())

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

  const toggleRow = useCallback(id => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }, [])

  const goManage = useCallback(u => navigate('/god/access/' + u.id), [navigate])
  const goCustomer = useCallback(
    u => navigate('/god/customers/' + u.organization_id + '?tab=people'), [navigate])

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

  async function onResetDone(forced) {
    const who = resetting.full_name || resetting.email
    setResetting(null)
    // States what actually happened, in the two ways it can differ. Never the
    // password, and never a claim the operator cannot check.
    setNotice(forced
      ? `Password reset for ${who}. They must choose their own at next sign-in, `
        + 'and any session they had open has been ended.'
      : `Password reset for ${who}. It is permanent until changed, and any `
        + 'session they had open has been ended.')
    await load()
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
          {/* THE FRONT DOOR. Seating somebody used to mean finding the form
              buried inside one brand's detail page, and a form nobody can find
              is why duplicate accounts get made. */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <h1 style={{ margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
              Users &amp; Identity
            </h1>
            <button className="gm-btn gm-primary"
                    onClick={() => navigate('/god/access/new')}>
              + ADD PERSON
            </button>
          </div>
          <p style={{ margin: '9px 0 0', color: 'var(--gm-blue)', fontSize: 12, maxWidth: 760 }}>
            One row per human. A person who holds platform authority, a
            brand-sales seat and a customer membership is one identity here with
            three contexts — never three accounts.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ padding: '12px 14px', marginBottom: 16,
                                            borderColor: 'var(--gm-pill-red-bd)', color: 'var(--gm-red)', fontSize: 11 }}>
            {err}
          </div>
        )}

        {notice && (
          <div className="gm-card" style={{ padding: '12px 14px', marginBottom: 16,
                                            borderColor: 'var(--gm-pill-teal-bd)', color: 'var(--gm-blue)', fontSize: 11 }}>
            {notice}
          </div>
        )}

        {/* Platform-owner count is an identity FACT, and the model says it is
            one. Stating it here means a second owner cannot appear quietly. */}
        {!loading && data && (
          <div className="gm-card" style={{
            padding: '12px 14px', marginBottom: 16, fontSize: 11,
            borderColor: owners.length === 1 ? 'var(--gm-pill-teal-bd)' : 'var(--gm-pill-red-bd)',
            color: owners.length === 1 ? 'var(--gm-blue)' : 'var(--gm-red)',
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
                      aria-pressed={scope === s.key}
                      onClick={() => setScope(s.key)}>{s.label}</button>
            ))}
          </div>
          <button className="gm-btn" onClick={load} disabled={loading}>
            {loading ? '…' : '↻ REFRESH'}
          </button>
          {/* Expand-all is the answer to "I need to audit every context at
              once" without the table having to carry them all by default. */}
          <button className="gm-btn" disabled={loading || users.length === 0}
                  onClick={() => setExpanded(prev =>
                    prev.size ? new Set() : new Set(users.map(u => u.id)))}>
            {expanded.size ? 'COLLAPSE ALL' : 'EXPAND ALL'}
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
            <table className="gm-table gm-idtable">
              <thead>
                <tr>
                  <th className="c-user">USER</th>
                  <th className="c-role">PRIMARY ROLE</th>
                  <th className="c-brand">BRAND</th>
                  <th className="c-org">CUSTOMER / WORKSPACE</th>
                  <th className="c-access">ACCESS</th>
                  <th className="c-last">LAST LOGIN</th>
                  <th className="c-status">STATUS</th>
                  <th className="c-act">ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={8} className="gm-empty">Loading identities…</td></tr>}
                {!loading && users.length === 0 && (
                  <tr><td colSpan={8} className="gm-empty">No identity matches this filter.</td></tr>
                )}
                {!loading && users.map(u => (
                  <IdentityRow
                    key={u.id}
                    u={u}
                    isMe={!!(me && me.email === u.email)}
                    busy={busy === u.id}
                    open={expanded.has(u.id)}
                    onToggle={toggleRow}
                    onManage={goManage}
                    onAskToggle={askToggle}
                    onReset={x => { setNotice(''); setResetting(x) }}
                    onCustomer={goCustomer}
                  />
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

      {resetting && (
        <ResetPasswordDialog
          user={resetting}
          onCancel={() => setResetting(null)}
          onDone={onResetDone}
        />
      )}
    </div>
  )
}
