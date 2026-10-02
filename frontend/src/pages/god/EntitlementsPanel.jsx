/**
 * EntitlementsPanel — the hierarchical feature entitlement table.
 *
 * Two exports:
 *   EntitlementsWorkbench({ target })  the table + KPI cards + detail panel for
 *                                      ANY scope target (used by the God
 *                                      Feature Entitlements page).
 *   default EntitlementsPanel({ orgId }) the same thing locked to one
 *                                      organization, for the Organization
 *                                      Control Center's Entitlements tab.
 *
 * Every number and state on this screen comes from GET /god/entitlements/*,
 * which evaluates through the same resolver `require_feature` enforces with.
 * Nothing is computed or invented client-side.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import ConfirmDialog from './ConfirmDialog'
import './FeatureEntitlements.css'

const STATE_META = {
  enabled: { label: 'Enabled', tone: 'teal' },
  disabled: { label: 'Disabled', tone: 'red' },
  requires_setup: { label: 'Requires Setup', tone: 'gold' },
  blocked_by_dependency: { label: 'Blocked by Dependency', tone: 'purple' },
}
const LAYER_LABEL = {
  platform: 'Platform', brand: 'Brand', org: 'Organization',
  workspace: 'Workspace', role: 'Role', user: 'User',
}
const SCOPE_NOUN = {
  platform: 'the whole platform', brand: 'this brand', org: 'this organization',
  workspace: 'this workspace', role: 'this role', user: 'this user',
}

function qs(obj) {
  const p = new URLSearchParams()
  Object.entries(obj || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v) })
  return p.toString()
}

function fmtDate(iso) {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric', year: 'numeric' })
}

function fmtDateTime(iso) {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function StatePill({ state }) {
  const m = STATE_META[state] || { label: state, tone: 'off' }
  return <span className={`fe-pill fe-pill-${m.tone}`}><span className="fe-dot" />{m.label}</span>
}

function LayerPill({ layer }) {
  if (!layer) return <span className="fe-muted">—</span>
  const st = layer.state
  if (layer.override) {
    return <span className={`fe-pill fe-pill-${layer.override.state === 'enabled' ? 'blue' : 'red'} fe-pill-sm`}>
      {layer.override.state === 'enabled' ? 'On' : 'Off'} · Override
    </span>
  }
  if (st === 'default_enabled') return <span className="fe-pill fe-pill-off fe-pill-sm">Default on</span>
  if (st === 'not_in_allow_list') return <span className="fe-pill fe-pill-off fe-pill-sm">Not in plan</span>
  if (st === 'no_brand') return <span className="fe-muted">No brand</span>
  if (st === 'capped_by_organization') return <span className="fe-pill fe-pill-off fe-pill-sm">Capped</span>
  return <span className="fe-pill fe-pill-off fe-pill-sm">Inherited</span>
}

function DepPill({ f }) {
  const n = f.dependencies.length
  if (!n) return <span className="fe-muted">—</span>
  const unmet = f.unmet_dependencies.length
  if (unmet) return <span className="fe-pill fe-pill-red fe-pill-sm" title={f.unmet_dependencies.join(', ')}>{unmet} unmet</span>
  return <span className="fe-pill fe-pill-teal fe-pill-sm">{n} met</span>
}

function Toggle({ on, onChange, disabled, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label}
      className={`fe-toggle ${on ? 'is-on' : ''}`} disabled={disabled}
      onClick={(e) => { e.stopPropagation(); onChange(!on) }}>
      <span className="fe-toggle-knob" />
    </button>
  )
}

export function EntitlementsWorkbench({ target, compact = false }) {
  const [catalog, setCatalog] = useState(null)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)
  const [category, setCategory] = useState('all')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [source, setSource] = useState('all')
  const [selected, setSelected] = useState({})
  const [openKey, setOpenKey] = useState(null)
  const [confirm, setConfirm] = useState(null)   // {title, body, action}
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const targetKey = JSON.stringify(target || {})
  const ready = !!target && !!target.scope

  useEffect(() => {
    api.get('/god/entitlements/catalog').then(setCatalog).catch(() => setCatalog(null))
  }, [])

  const load = useCallback(async () => {
    if (!ready) return
    setLoading(true); setErr(null)
    try {
      const d = await api.get('/god/entitlements/matrix?' + qs(target))
      setData(d)
    } catch (e) {
      setErr(e.message || 'Failed to load entitlements'); setData(null)
    } finally { setLoading(false) }
  }, [targetKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setSelected({}); load() }, [load])

  const features = data?.features || []
  const categories = useMemo(() => {
    const counts = {}
    features.forEach(f => { counts[f.category] = (counts[f.category] || 0) + 1 })
    return Object.entries(counts).sort((a, b) => a[0].localeCompare(b[0]))
  }, [features])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return features.filter(f => {
      if (category !== 'all' && f.category !== category) return false
      if (status !== 'all' && f.effective_state !== status) return false
      if (source === 'override' && f.is_inherited) return false
      if (source === 'inherited' && !f.is_inherited) return false
      if (q && !(f.label.toLowerCase().includes(q) || f.key.includes(q) || f.category.toLowerCase().includes(q))) return false
      return true
    })
  }, [features, category, status, source, search])

  const selectedKeys = Object.keys(selected).filter(k => selected[k])
  const allVisibleSelected = rows.length > 0 && rows.every(r => selected[r.key])
  const openFeature = features.find(f => f.key === openKey) || null
  const scope = target?.scope
  const scopeLabel = LAYER_LABEL[scope] || scope

  function ask(title, body, action) { setReason(''); setConfirm({ title, body, action }) }

  async function run(action) {
    setBusy(true); setNotice(null)
    try {
      await action(reason.trim() || null)
      setConfirm(null)
      await load()
    } catch (e) {
      setNotice({ tone: 'red', text: e.message || 'Change failed' })
      setConfirm(null)
    } finally { setBusy(false) }
  }

  function setOne(f, state) {
    ask(`${state === 'enabled' ? 'Enable' : 'Disable'} ${f.label} for ${SCOPE_NOUN[scope]}?`,
      <>This creates an explicit <b>{scopeLabel}</b> override ({state}). It replaces the inherited
        decision ({f.inherited_from}) until someone resets it to inherited.</>,
      (r) => api.post('/god/entitlements/overrides', { ...target, feature_key: f.key, state, reason: r }))
  }

  function resetOne(f) {
    ask(`Reset ${f.label} to inherited?`,
      <>The {scopeLabel.toLowerCase()} override is removed and the feature follows the layer above it again.</>,
      (r) => api.post('/god/entitlements/overrides/reset', { ...target, feature_key: f.key, reason: r }))
  }

  function bulk(action, state) {
    const keys = selectedKeys
    const verb = action === 'reset' ? 'Reset to inherited' : (state === 'enabled' ? 'Enable' : 'Disable')
    ask(`${verb}: ${keys.length} feature${keys.length === 1 ? '' : 's'}?`,
      <>Applies to {SCOPE_NOUN[scope]}: {keys.join(', ')}</>,
      async (r) => {
        await api.post('/god/entitlements/bulk', { ...target, action, state, feature_keys: keys, reason: r })
        setSelected({})
      })
  }

  if (!ready) return <div className="fe-empty">Choose a scope to view its entitlements.</div>

  const k = data?.kpis
  return (
    <div className="fe-workbench">
      {k && (
        <div className="fe-kpis">
          <Kpi label="Total Features" value={k.total} tone="blue" />
          <Kpi label="Enabled" value={k.enabled} tone="teal" />
          <Kpi label="Disabled" value={k.disabled} tone="red" />
          <Kpi label="Inherited" value={k.inherited} tone="off" />
          <Kpi label={`${scopeLabel} Overrides`} value={k.overrides} tone="purple" />
          <Kpi label="Requires Setup" value={k.requires_setup} tone="gold" />
          <Kpi label="Blocked by Dependency" value={k.blocked} tone="red" />
        </div>
      )}

      {data?.note && <div className="fe-banner">{data.note}</div>}
      {notice && <div className={`fe-banner fe-banner-${notice.tone}`}>{notice.text}</div>}

      <div className="fe-chips" role="group" aria-label="Feature categories">
        <button className={`fe-chip ${category === 'all' ? 'is-active' : ''}`} aria-pressed={category === 'all'} onClick={() => setCategory('all')}>
          All Features <span>{features.length}</span>
        </button>
        {categories.map(([c, n]) => (
          <button key={c} className={`fe-chip ${category === c ? 'is-active' : ''}`} aria-pressed={category === c} onClick={() => setCategory(c)}>
            {c} <span>{n}</span>
          </button>
        ))}
      </div>

      <div className={`fe-body ${openFeature && !compact ? 'has-detail' : ''}`}>
        <div className="fe-card fe-table-card">
          <div className="fe-filters">
            <input className="fe-input fe-search" placeholder="Search features or keys…" value={search}
              onChange={e => setSearch(e.target.value)} aria-label="Search features" />
            <select className="fe-input" value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
              <option value="all">All statuses</option>
              <option value="enabled">Enabled</option>
              <option value="disabled">Disabled</option>
              <option value="requires_setup">Requires setup</option>
              <option value="blocked_by_dependency">Blocked by dependency</option>
            </select>
            <select className="fe-input" value={source} onChange={e => setSource(e.target.value)} aria-label="Source">
              <option value="all">All sources</option>
              <option value="override">Overridden here</option>
              <option value="inherited">Inherited</option>
            </select>
            <button className="fe-btn" onClick={() => { setSearch(''); setStatus('all'); setSource('all'); setCategory('all') }}>Reset</button>
          </div>

          {selectedKeys.length > 0 && (
            <div className="fe-bulkbar">
              <b>{selectedKeys.length} selected</b>
              <button className="fe-btn" onClick={() => bulk('set', 'enabled')}>Enable</button>
              <button className="fe-btn" onClick={() => bulk('set', 'disabled')}>Disable</button>
              <button className="fe-btn" onClick={() => bulk('reset')}>Reset to inherited</button>
              <button className="fe-btn fe-btn-quiet" onClick={() => setSelected({})}>Clear</button>
            </div>
          )}

          {err && <div className="fe-banner fe-banner-red">{err}</div>}
          {loading && !data && <div className="fe-empty">Loading entitlements…</div>}

          {data && (
            <div className="fe-table-wrap">
              <table className="fe-table">
                <thead>
                  <tr>
                    <th className="fe-col-check">
                      <input type="checkbox" aria-label="Select all visible" checked={allVisibleSelected}
                        onChange={e => {
                          const next = { ...selected }
                          rows.forEach(r => { next[r.key] = e.target.checked })
                          setSelected(next)
                        }} />
                    </th>
                    <th>Feature</th>
                    <th>Category</th>
                    <th>Effective State</th>
                    <th>Inherited From</th>
                    <th>Override</th>
                    <th>Dependencies</th>
                    <th>Last Updated</th>
                    <th className="fe-col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(f => (
                    <tr key={f.key} className={openKey === f.key ? 'is-open' : ''} onClick={() => setOpenKey(f.key)}>
                      <td className="fe-col-check" onClick={e => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select ${f.label}`} checked={!!selected[f.key]}
                          onChange={e => setSelected(s => ({ ...s, [f.key]: e.target.checked }))} />
                      </td>
                      <td data-label="Feature">
                        <div className="fe-feat">{f.label}</div>
                        <div className="fe-key">{f.key}</div>
                      </td>
                      <td data-label="Category"><span className="fe-cat">{f.category}</span></td>
                      <td data-label="Effective"><StatePill state={f.effective_state} /></td>
                      <td data-label="Inherited from" className="fe-from">{f.inherited_from}</td>
                      <td data-label="Override">
                        <div className="fe-ovr">
                          <Toggle on={f.layers[scope]?.result ?? f.enabled}
                            label={`${f.label} at ${scopeLabel}`}
                            onChange={(v) => setOne(f, v ? 'enabled' : 'disabled')} />
                          {f.override && <span className="fe-ovr-tag">Override</span>}
                        </div>
                      </td>
                      <td data-label="Dependencies"><DepPill f={f} /></td>
                      <td data-label="Last updated">
                        {f.last_updated
                          ? <><div>{fmtDate(f.last_updated.at)}</div><div className="fe-key">{f.last_updated.by || 'Unknown actor'}</div></>
                          : <span className="fe-muted">Never overridden</span>}
                      </td>
                      <td className="fe-col-actions" onClick={e => e.stopPropagation()}>
                        <button className="fe-btn fe-btn-sm" onClick={() => setOpenKey(f.key)}>Manage</button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={9} className="fe-empty">No features match these filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {openFeature && (
          <DetailPanel f={openFeature} target={target} scope={scope} catalog={catalog}
            onClose={() => setOpenKey(null)} onSet={setOne} onReset={resetOne} overlay={compact} />
        )}
      </div>

      {confirm && (
        <ConfirmDialog tone="blue" eyebrow="FEATURE ENTITLEMENT" title={confirm.title}
          body={<>
            <div style={{ marginBottom: 10 }}>{confirm.body}</div>
            <label className="fe-reason-label" htmlFor="fe-reason">Reason (recorded in the audit history)</label>
            <textarea id="fe-reason" className="fe-input fe-reason" rows={2} value={reason}
              onChange={e => setReason(e.target.value)} placeholder="Why is this changing?" />
          </>}
          confirmLabel="Confirm" busy={busy}
          onConfirm={() => run(confirm.action)} onCancel={() => setConfirm(null)} />
      )}
    </div>
  )
}

function Kpi({ label, value, tone }) {
  return (
    <div className="fe-kpi">
      <div className={`fe-kpi-icon fe-tone-${tone}`} aria-hidden="true" />
      <div>
        <div className="fe-kpi-value">{value ?? '—'}</div>
        <div className="fe-kpi-label">{label}</div>
      </div>
    </div>
  )
}

function DetailPanel({ f, target, scope, catalog, onClose, onSet, onReset, overlay }) {
  const [tab, setTab] = useState('details')
  const [explain, setExplain] = useState(null)
  const [events, setEvents] = useState(null)
  const [err, setErr] = useState(null)
  const cat = catalog?.features?.find(c => c.key === f.key)

  useEffect(() => { setExplain(null); setEvents(null); setErr(null) }, [f.key, f.last_updated?.at, f.effective_state])

  useEffect(() => {
    if (tab === 'inheritance' && !explain) {
      api.get('/god/entitlements/explain?' + qs({ ...target, feature_key: f.key }))
        .then(setExplain).catch(e => setErr(e.message))
    }
    if (tab === 'audit' && !events) {
      const scopeFilter = target.org_id ? { org_id: target.org_id } : (target.platform_id ? { platform_id: target.platform_id } : {})
      api.get('/god/entitlements/history?' + qs({ ...scopeFilter, feature_key: f.key }))
        .then(d => setEvents(d.events)).catch(e => setErr(e.message))
    }
  }, [tab, explain, events, f.key]) // eslint-disable-line react-hooks/exhaustive-deps

  const layerRows = ['platform', 'brand', 'org', 'workspace', 'role', 'user'].filter(l => f.layers[l])

  return (
    <aside className={`fe-card fe-detail ${overlay ? 'is-overlay' : ''}`} aria-label={`${f.label} details`}>
      <div className="fe-detail-head">
        <div>
          <div className="fe-detail-title">{f.label}</div>
          <div className="fe-key">Key: {f.key}</div>
        </div>
        <div className="fe-detail-headright">
          <StatePill state={f.effective_state} />
          <button className="fe-x" onClick={onClose} aria-label="Close details">×</button>
        </div>
      </div>
      <div className="fe-tabs" role="tablist">
        {[['details', 'Details'], ['inheritance', 'Inheritance'], ['dependencies', 'Dependencies'], ['audit', 'Audit Log']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`fe-tab ${tab === id ? 'is-active' : ''}`}
            onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {err && <div className="fe-banner fe-banner-red">{err}</div>}

      {tab === 'details' && (
        <dl className="fe-dl">
          <dt>Description</dt><dd>{f.label}</dd>
          <dt>Feature key</dt><dd className="fe-mono">{f.key}</dd>
          <dt>Category</dt><dd>{f.category}</dd>
          <dt>Effective state</dt><dd><StatePill state={f.effective_state} /></dd>
          <dt>Inherited from</dt><dd>{f.inherited_from}</dd>
          {layerRows.map(l => (
            <FragmentRow key={l} dt={`${LAYER_LABEL[l]} state`} dd={<LayerPill layer={f.layers[l]} />} />
          ))}
          <dt>Override here</dt>
          <dd>{f.override ? `${f.override.state} (${f.override.actor || 'unknown actor'})` : 'None — inherited'}</dd>
          {f.override?.reason && (<><dt>Reason</dt><dd>{f.override.reason}</dd></>)}
          {f.setup && (<><dt>Setup</dt><dd>{f.setup.configured ? 'Configured' : 'Not configured'} — {f.setup.reason}</dd></>)}
          {!f.setup && cat?.setup_signal == null && (<><dt>Setup</dt><dd className="fe-muted">No setup signal for this feature</dd></>)}
          <dt>Last changed</dt>
          <dd>{f.last_updated ? `${fmtDateTime(f.last_updated.at)} by ${f.last_updated.by || 'unknown actor'}` : 'Never overridden'}</dd>
        </dl>
      )}

      {tab === 'inheritance' && (
        explain ? (
          <div>
            <div className="fe-why">Why {scope === 'user' ? 'does this user' : `does this ${LAYER_LABEL[scope].toLowerCase()}`} have this feature?</div>
            <ol className="fe-chain">
              {explain.steps.map(s => (
                <li key={s.layer} className={s.override ? 'has-override' : ''}>
                  <div className="fe-chain-head">
                    <b>{s.label}</b>
                    <span className={`fe-pill fe-pill-sm fe-pill-${s.result ? 'teal' : 'red'}`}>{s.result ? 'On' : 'Off'} after this layer</span>
                  </div>
                  <div className="fe-chain-text">{s.explanation}</div>
                  {s.override && <div className="fe-key">{s.override.actor || 'Unknown actor'} · {fmtDateTime(s.override.updated_at)}{s.override.reason ? ` · “${s.override.reason}”` : ''}</div>}
                </li>
              ))}
              {explain.dependencies.map(d => (
                <li key={'dep-' + d.key} className={d.met ? '' : 'is-bad'}>
                  <div className="fe-chain-head"><b>Dependency: {d.label}</b><StatePill state={d.state} /></div>
                  <div className="fe-chain-text">{d.explanation}</div>
                </li>
              ))}
              <li className="is-final">
                <div className="fe-chain-head"><b>Final effective state</b><StatePill state={explain.state} /></div>
                <div className="fe-chain-text">{explain.summary}</div>
              </li>
            </ol>
          </div>
        ) : <div className="fe-empty">Evaluating…</div>
      )}

      {tab === 'dependencies' && (
        <div>
          <div className="fe-subhead">Requires</div>
          {f.dependencies.length === 0 && <div className="fe-muted fe-pad">No prerequisites.</div>}
          {f.dependencies.map(d => (
            <div key={d.key} className="fe-dep">
              <span className={`fe-depmark ${d.met ? 'ok' : 'bad'}`}>{d.met ? '✓' : '!'}</span>
              <div className="fe-dep-main"><div>{d.label}</div><div className="fe-key">{d.key}</div></div>
              <StatePill state={d.state} />
            </div>
          ))}
          {f.dependencies.some(d => !d.met && !d.blocks_access) && (
            <div className="fe-banner">A prerequisite is missing from this organization's allow-list. This is reported as a configuration gap; access is not changed by it.</div>
          )}
          <div className="fe-subhead">Required by</div>
          {f.required_by.length === 0
            ? <div className="fe-muted fe-pad">Nothing depends on this feature.</div>
            : <div className="fe-pad">{f.required_by.map(k => <span key={k} className="fe-cat fe-mr">{k}</span>)}</div>}
        </div>
      )}

      {tab === 'audit' && (
        events ? (events.length === 0
          ? <div className="fe-empty">No override history for this feature in this scope.</div>
          : <ul className="fe-audit">
              {events.map(e => (
                <li key={e.id}>
                  <div><b>{e.action === 'reset' ? 'Reset' : 'Set'}</b> at {LAYER_LABEL[e.scope]}: {e.previous_state} → {e.new_state}</div>
                  <div className="fe-key">{e.actor || 'Unknown actor'} · {fmtDateTime(e.at)}</div>
                  {e.reason && <div className="fe-chain-text">“{e.reason}”</div>}
                </li>
              ))}
            </ul>)
          : <div className="fe-empty">Loading history…</div>
      )}

      <div className="fe-detail-actions">
        {(f.layers[scope]?.result ?? f.enabled)
          ? <button className="fe-btn fe-btn-danger" onClick={() => onSet(f, 'disabled')}>Disable for {LAYER_LABEL[scope]}</button>
          : <button className="fe-btn fe-btn-primary" onClick={() => onSet(f, 'enabled')}>Enable for {LAYER_LABEL[scope]}</button>}
        <button className="fe-btn" disabled={!f.override} onClick={() => onReset(f)}
          title={f.override ? '' : 'Nothing to reset: this scope has no override'}>Reset to Inherited</button>
      </div>
    </aside>
  )
}

function FragmentRow({ dt, dd }) {
  return <><dt>{dt}</dt><dd>{dd}</dd></>
}

export default function EntitlementsPanel({ orgId }) {
  const target = useMemo(() => (orgId ? { scope: 'org', org_id: orgId } : null), [orgId])
  if (!orgId) return <div className="fe-empty">No organization selected.</div>
  return <div className="go-scope fe-root fe-embedded"><EntitlementsWorkbench target={target} /></div>
}
