/**
 * USER ACCESS DIAGNOSTIC — God Mode only.
 *
 * Answers "why can this person not see their work" without a database shell.
 * Every number on this screen comes from the server; the browser computes
 * nothing and infers nothing.
 *
 * It lives here and only here. It is not in the customer workspace, not in the
 * Executive Suite, not in the brand sales workspace, and not in advisor
 * navigation — the route behind it is `require_god`, so a URL typed by anyone
 * else is refused by the server rather than by the absence of a link.
 */
import { useState } from 'react'
import { api } from '../../api/client'

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

function Row({ label, value, mono }) {
  return (
    <div style={{ display: 'flex', gap: 16, padding: '5px 0',
                  borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
      <div style={{ minWidth: 210, opacity: 0.62, fontSize: 12.5 }}>{label}</div>
      <div style={{ fontSize: 13.5, fontFamily: mono ? MONO : 'inherit',
                    wordBreak: 'break-all' }}>
        {value === null || value === undefined || value === ''
          ? <span style={{ opacity: 0.4 }}>—</span>
          : String(value)}
      </div>
    </div>
  )
}

function Panel({ title, children }) {
  return (
    <section style={{ marginTop: 26 }}>
      <h3 style={{ fontSize: 11.5, letterSpacing: '0.09em',
                   textTransform: 'uppercase', opacity: 0.55, margin: '0 0 8px' }}>
        {title}
      </h3>
      {children}
    </section>
  )
}

function Pill({ ok, children }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.04em',
      padding: '2px 8px', borderRadius: 999, whiteSpace: 'nowrap',
      background: ok ? 'rgba(30,200,130,0.16)' : 'rgba(240,80,80,0.18)',
      color: ok ? '#1a9c6b' : '#d8434a',
    }}>{children}</span>
  )
}

export default function UserAccessDiagnostic() {
  const [ident, setIdent] = useState('')
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function run(e) {
    e && e.preventDefault()
    setBusy(true); setError(''); setReport(null)
    try {
      // Exact lookup only. An input containing '@' is an email, anything else
      // is treated as a user id - the server refuses a partial match either way
      // rather than guessing which person was meant.
      const q = ident.includes('@')
        ? `email=${encodeURIComponent(ident.trim())}`
        : `user_id=${encodeURIComponent(ident.trim())}`
      setReport(await api.get(`/god/ops/diagnostics/user-access?${q}`))
    } catch (err) {
      setError(err?.message || 'Diagnostic failed.')
    } finally {
      setBusy(false)
    }
  }

  const r = report
  const prim = r?.workspace_scenarios?.[0]
  const svc = r?.endpoint_service_counts || {}

  return (
    <div style={{ maxWidth: 940, padding: '4px 0 60px' }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 20 }}>User Access Diagnostic</h2>
      <p style={{ margin: '0 0 18px', opacity: 0.62, fontSize: 13.5, maxWidth: 640 }}>
        Read-only. Resolves one person's identity, memberships, workspace and
        lead scope across every layer. Performs no writes and returns no
        credentials.
      </p>

      <form onSubmit={run} style={{ display: 'flex', gap: 10, maxWidth: 620 }}>
        <input
          value={ident}
          onChange={e => setIdent(e.target.value)}
          placeholder="exact email, or user id"
          style={{ flex: 1, padding: '10px 12px', borderRadius: 8, fontSize: 14,
                   border: '1px solid rgba(128,128,128,0.34)',
                   background: 'transparent', color: 'inherit' }}
        />
        <button type="submit" disabled={busy || !ident.trim()}
                style={{ padding: '10px 18px', borderRadius: 8, fontSize: 14,
                         fontWeight: 600, cursor: busy ? 'default' : 'pointer',
                         border: '1px solid rgba(128,128,128,0.34)',
                         background: 'rgba(128,128,128,0.12)', color: 'inherit' }}>
          {busy ? 'Running…' : 'Run Diagnostic'}
        </button>
      </form>

      {error && (
        <div style={{ marginTop: 16, padding: '10px 14px', borderRadius: 8,
                      background: 'rgba(240,80,80,0.12)', color: '#d8434a',
                      fontSize: 13.5 }}>{error}</div>
      )}

      {r && (
        <>
          {/* The conclusion first. Everything below is the evidence for it. */}
          <Panel title="Findings">
            {(r.findings || []).map((f, i) => (
              <div key={i} style={{ padding: '9px 12px', marginBottom: 6,
                                    borderRadius: 8, fontSize: 13.5,
                                    background: 'rgba(128,128,128,0.10)',
                                    borderLeft: '3px solid rgba(128,128,128,0.5)' }}>
                {f}
              </div>
            ))}
          </Panel>

          <Panel title="Identity">
            <Row label="User id" value={r.identity.user_id} mono />
            <Row label="Name" value={r.identity.full_name} />
            <Row label="Email" value={r.identity.email} />
            <Row label="Account active" value={r.identity.is_active ? 'yes' : 'NO'} />
            <Row label="Platform role" value={r.identity.platform_role} />
            <Row label="Legacy organization_id" value={r.identity.legacy_organization_id} mono />
            <Row label="Legacy organization" value={r.identity.legacy_organization_name} />
          </Panel>

          <Panel title="Customer workspace memberships">
            {r.customer_workspace_memberships.length === 0
              ? <div style={{ fontSize: 13.5, opacity: 0.6 }}>None.</div>
              : r.customer_workspace_memberships.map(m => (
                <div key={m.membership_id}
                     style={{ display: 'flex', alignItems: 'center', gap: 12,
                              padding: '8px 0',
                              borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
                  <Pill ok={m.is_active}>{m.state}</Pill>
                  <span style={{ fontWeight: 600, fontSize: 13.5 }}>
                    {m.organization_name || '(organization missing)'}
                  </span>
                  <span style={{ fontSize: 12, opacity: 0.6 }}>role {m.role}</span>
                  <span style={{ fontSize: 11.5, opacity: 0.45, fontFamily: MONO }}>
                    {m.scope_id}
                  </span>
                  {!m.resolves && <Pill ok={false}>ORG NOT FOUND</Pill>}
                </div>
              ))}
          </Panel>

          <Panel title="Platform / brand-sales memberships">
            {r.brand_sales_memberships.length === 0 && r.platform_memberships.length === 0
              ? <div style={{ fontSize: 13.5, opacity: 0.6 }}>None.</div>
              : [...r.platform_memberships, ...r.brand_sales_memberships].map(m => (
                <div key={m.membership_id}
                     style={{ display: 'flex', alignItems: 'center', gap: 12,
                              padding: '8px 0',
                              borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
                  <Pill ok={m.is_active}>{m.state}</Pill>
                  <span style={{ fontWeight: 600, fontSize: 13.5 }}>
                    {m.brand_sales_org_name || m.scope_type}
                  </span>
                  <span style={{ fontSize: 12, opacity: 0.6 }}>role {m.role}</span>
                </div>
              ))}
          </Panel>

          <Panel title="Authorized contexts (server-built)">
            <Row label="Has back office" value={r.authorized_contexts.has_back_office ? 'yes' : 'no'} />
            <Row label="Workspace count" value={r.authorized_contexts.workspace_count} />
            <Row label="Login would land at" value={r.authorized_contexts.default_context?.path} mono />
          </Panel>

          <Panel title="Workspace resolution and lead counts">
            {r.workspace_scenarios.map((s, i) => (
              <div key={i} style={{ marginBottom: 14, padding: '12px 14px',
                                    borderRadius: 10,
                                    border: '1px solid rgba(128,128,128,0.24)' }}>
                <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 6 }}>
                  {s.scenario}
                </div>
                <Row label="Resolved workspace" value={s.resolved_workspace_name} />
                <Row label="Resolved workspace id" value={s.resolved_workspace_id} mono />
                <Row label="Effective workspace role" value={s.effective_workspace_role} />
                <Row label="A · raw assigned" value={s.A_raw_assigned} />
                <Row label="B · lead_scope" value={s.B_lead_scope_error || s.B_lead_scope_count} />
                <Row label="Organization total" value={s.organization_total_leads} />
                {s.divergence && (
                  <div style={{ marginTop: 8, fontSize: 13, color: '#d8434a' }}>
                    {s.divergence}
                  </div>
                )}
              </div>
            ))}
          </Panel>

          <Panel title="What the customer's screen would show">
            <Row label="C · /leads service total"
                 value={svc.C_leads_service_error || svc.C_leads_service_total} />
            <Row label="D · status funnel total"
                 value={svc.D_status_funnel_error || svc.D_status_funnel_total} />
            {prim && (
              <div style={{ marginTop: 10, fontSize: 13 }}>
                {[prim.A_raw_assigned, prim.B_lead_scope_count,
                  svc.C_leads_service_total, svc.D_status_funnel_total]
                  .every(v => v === prim.A_raw_assigned)
                  ? <Pill ok>A = B = C = D — all four layers agree</Pill>
                  : <Pill ok={false}>the four counts DISAGREE — see above</Pill>}
              </div>
            )}
          </Panel>

          <Panel title="Timings (ms)">
            <div style={{ fontFamily: MONO, fontSize: 12.5, opacity: 0.8 }}>
              {Object.entries(r.timings_ms || {}).map(([k, v]) => (
                <div key={k}>{k.padEnd(24, ' ')} {v}</div>
              ))}
            </div>
          </Panel>

          <details style={{ marginTop: 26 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12.5, opacity: 0.6 }}>
              Raw report
            </summary>
            <pre style={{ marginTop: 10, padding: 14, borderRadius: 8, fontSize: 11.5,
                          overflowX: 'auto', background: 'rgba(128,128,128,0.10)' }}>
              {JSON.stringify(r, null, 2)}
            </pre>
          </details>
        </>
      )}
    </div>
  )
}
