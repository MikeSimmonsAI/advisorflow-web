import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'

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
export default function AdministrationGates({ orgId, onChanged }) {
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
    try {
      setA(await api.put('/god/customers/' + orgId + '/self-management',
                         { allowed: next }))
      onChanged && onChanged()
    }
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
      onChanged && onChanged()
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
          Administered by the platform — never delegated
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
