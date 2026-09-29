/* ENTITLEMENTS — the organization's blueprint tiers (core / vertical / optional)
   evaluated against its real allow-list, plus the existing per-feature switches.

   The hierarchical matrix (platform → brand → org → workspace → role, with
   overrides, dependencies and audit) is the shared EntitlementsPanel, locked
   to this organization. */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'
import { Pill } from './ccShared'
import EntitlementsPanel from '../EntitlementsPanel'

function Tier({ title, sub, rows }) {
  return (
    <section className="occ-card">
      <h3>{title}</h3>
      <p className="occ-sub">{sub}</p>
      {rows.length === 0 ? <div className="occ-empty">None for this blueprint.</div> : (
        <div className="occ-table-wrap">
          <table className="occ-table">
            <thead><tr><th>Feature</th><th>Key</th><th>State</th><th>Dependencies</th><th>Why</th></tr></thead>
            <tbody>
              {rows.map(f => (
                <tr key={f.key}>
                  <td><strong>{f.label}</strong></td>
                  <td className="occ-muted">{f.key}</td>
                  <td><Pill status={f.status} label={f.status_label} /></td>
                  <td className="occ-muted">
                    {f.requires.length === 0 ? '—'
                      : f.missing_dependencies.length
                        ? 'Missing: ' + f.missing_dependencies.join(', ')
                        : f.requires.join(', ') + ' satisfied'}
                  </td>
                  <td className="occ-muted">{f.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export default function EntitlementsTab({ orgId, cc, reload }) {
  const nav = useNavigate()
  const [bp, setBp] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [showSwitches, setShowSwitches] = useState(false)

  const load = useCallback(() => {
    api.get('/god/customers/' + orgId + '/blueprint').then(setBp).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(load, [load])

  const tools = cc.enabled_tools.tools
  const enabled = tools.filter(t => t.enabled).map(t => t.key)

  async function put(next, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(true); setErr('')
    try {
      await api.put('/god/customers/' + orgId + '/features', { enabled: next })
      load(); reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  function toggle(key) {
    put(enabled.includes(key) ? enabled.filter(k => k !== key) : [...enabled, key])
  }

  function applyRequired() {
    const next = Array.from(new Set([...enabled, ...bp.features.apply_keys])).sort()
    const adding = next.filter(k => !enabled.includes(k))
    put(next, 'Enable ' + adding.length + ' required feature(s) for this organization: ' +
              adding.join(', ') + '? This is audited and does not remove anything.')
  }

  return (
    <div className="occ">
      {err && <div className="go-err go-dismiss" onClick={() => setErr('')}>{err}</div>}

      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Feature Entitlements</h3>
            <p className="occ-sub">
              Platform → brand → organization → workspace → role inheritance, overrides and
              audit history live in the Feature Entitlements control plane.
            </p>
          </div>
          <button className="go-btn go-btn-primary"
                  onClick={() => nav('/god/entitlements?org=' + orgId)}>
            Open Feature Entitlements
          </button>
        </div>
      </section>

      <EntitlementsPanel orgId={orgId} />

      {!bp ? <div className="occ-muted">Loading blueprint…</div> : (
        <>
          <section className="occ-card">
            <div className="occ-card-head">
              <div>
                <h3>Blueprint: {bp.blueprint.label}</h3>
                <p className="occ-sub">{bp.blueprint.description}</p>
                <p className="occ-sub">
                  Selected because: {bp.blueprint.selected_because} ·{' '}
                  {bp.features.required_enabled} of {bp.features.required_total} required
                  features enabled
                </p>
              </div>
              {bp.features.missing_required.length > 0 && (
                <button className="go-btn go-btn-primary" disabled={busy} onClick={applyRequired}>
                  Enable required features
                </button>
              )}
            </div>
            <p className="occ-sub" style={{ marginBottom: 0 }}>
              Always on (not toggles):{' '}
              {bp.blueprint.platform_foundations.map(f => f.label).join(', ')}.
            </p>
          </section>
          <Tier title="Core required" sub="Every organization on the platform needs these."
                rows={bp.features.core_required} />
          <Tier title="Vertical required" sub="Required by this organization's business model."
                rows={bp.features.vertical_required} />
          <Tier title="Optional" sub="Available; never counted against activation."
                rows={bp.features.optional} />
          {bp.vertical_setup.length > 0 && (
            <section className="occ-card">
              <h3>Vertical setup</h3>
              <ul className="occ-list">
                {bp.vertical_setup.map(i => (
                  <li key={i.key}>
                    <span><strong>{i.label}</strong> <span className="occ-muted">({i.requirement})</span>
                      <div className="occ-muted">{i.reason}</div></span>
                    <Pill status={i.status} label={i.status_label} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Organization allow-list</h3>
            <p className="occ-sub">
              What this customer may USE. Enforced by the server — switching a feature off
              refuses its API, not just its menu item. Who may CONFIGURE the infrastructure
              behind a feature is on the Administration tab.
            </p>
            <p className="occ-sub">
              These switches edit the organization's STORED allow-list only. Platform and brand
              overrides are applied on top of it — the Feature Entitlements table above shows
              the resulting effective state.
            </p>
          </div>
          <button className="go-btn" onClick={() => setShowSwitches(s => !s)}>
            {showSwitches ? 'Hide switches' : 'Show switches'}
          </button>
        </div>
        {cc.enabled_tools.dependency_gaps.length > 0 && (
          <div className="go-warn">
            {cc.enabled_tools.dependency_gaps.map((g, k) => <div key={k}>{g.detail}</div>)}
          </div>
        )}
        {showSwitches && tools.map(f => (
          <label key={f.key} className="go-check">
            <input type="checkbox" checked={f.enabled} disabled={busy} onChange={() => toggle(f.key)} />
            <span><strong>{f.key}</strong> — {f.label}</span>
          </label>
        ))}
      </section>
    </div>
  )
}
