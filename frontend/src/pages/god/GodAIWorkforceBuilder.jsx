/**
 * GOD MODE — AI WORKFORCE DEPLOYMENT.
 *
 * WHAT THIS SCREEN IS FOR, and what the neighbouring two are not.
 *
 *   AI Workforce (T6)      who the AI employees are, what they may do, and the
 *                          activation stage and kill switch.
 *   AI Operations (T7)     what was attempted, what was refused, and why
 *                          nothing is sending.
 *   AI Deployment (here)   what each BRAND sells, to which packages, and where
 *                          every customer's hired employee has got to.
 *
 * THREE THINGS AN OPERATOR DOES HERE:
 *
 *   1. State a brand's commercial terms for a job — which catalogue item sells
 *      it, which packages may hold it, how many, and whether a controlled run
 *      is required first. NO PRICE IS ENTERED ON THIS SCREEN. The amount
 *      belongs to the catalogue item, in Billing, where every other price on
 *      this platform lives.
 *   2. Complete a customer's activation — see the readiness checks, sign off
 *      anything that needs a person, and start the employee with a reason.
 *   3. Find what should have stopped and did not: deployments whose
 *      entitlement lapsed, AI employees no deployment entitles, follow-ups
 *      booked against a retired one.
 *
 * Data:
 *   GET  /god/ai-workforce/overview | templates | deployments
 *   GET  /god/ai-workforce/brands/:platformId/catalog
 *   PUT  /god/ai-workforce/brands/:platformId/terms
 *   POST /god/ai-workforce/deployments/:id/acknowledge-review
 *   POST /god/ai-workforce/deployments/:id/activation | stand-down
 *   POST /god/ai-workforce/reconcile | orphans/repair | proof/run | proof/attack
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'

const TABS = ['Overview', 'Brand terms', 'Deployments', 'Proof']

const MODES = [
  ['addon', 'Recurring add-on'],
  ['included', 'Included with a package'],
  ['quoted', 'Quoted per deal'],
  ['capacity', 'Additional capacity'],
]

function Sec ({ children, top = 0 }) {
  return (
    <div style={{
      color: 'var(--gm-blue)', fontSize: 10, letterSpacing: '.14em',
      textTransform: 'uppercase', margin: `${top}px 0 8px`,
    }}>{children}</div>
  )
}

function Stat ({ k, v, s, tone }) {
  return (
    <div className="gm-stat">
      <div className="gm-k">{k}</div>
      <div className="gm-v" style={tone ? { color: tone } : undefined}>{v}</div>
      {s && <div className="gm-s">{s}</div>}
    </div>
  )
}

function Pill ({ tone, children }) {
  const colour = { ok: T.green, warn: T.amber, bad: T.red, off: 'var(--gm-blue)' }[tone] || 'var(--gm-blue)'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 9px', borderRadius: 999,
      fontSize: 11, letterSpacing: '.04em', textTransform: 'uppercase',
      color: colour, border: `1px solid ${colour}44`, background: `${colour}14`,
    }}>{children}</span>
  )
}

function Row ({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ color: 'var(--gm-blue)', fontSize: 11, marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  )
}

const joined = v => (v || []).join(', ')
const parseList = t => (t || '').split(',').map(s => s.trim()).filter(Boolean)
const message = (e, fallback) =>
  e?.detail?.message || (typeof e?.detail === 'string' ? e.detail : null)
  || e?.message || fallback

export default function GodAIWorkforceBuilder () {
  const navigate = useNavigate()
  const [tab, setTab] = useState('Overview')
  const [overview, setOverview] = useState(null)
  const [templates, setTemplates] = useState([])
  const [brandId, setBrandId] = useState('')
  const [brand, setBrand] = useState(null)
  const [draft, setDraft] = useState(null)
  const [deployments, setDeployments] = useState([])
  const [detail, setDetail] = useState(null)
  const [proof, setProof] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b] = await Promise.all([
        api.get('/god/ai-workforce/overview'),
        api.get('/god/ai-workforce/templates'),
      ])
      setOverview(a); setTemplates(b.templates || [])
    } catch (e) {
      setErr(message(e, 'Could not load the deployment console.'))
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const loadDeployments = useCallback(async () => {
    setErr('')
    try {
      const d = await api.get('/god/ai-workforce/deployments')
      setDeployments(d.deployments || [])
    } catch (e) { setErr(message(e, 'Could not load deployments.')) }
  }, [])
  useEffect(() => {
    if (tab === 'Deployments') loadDeployments()
  }, [tab, loadDeployments])

  async function loadBrand () {
    if (!brandId.trim()) return
    setBusy(true); setErr('')
    try {
      setBrand(await api.get(`/god/ai-workforce/brands/${brandId.trim()}/catalog`))
    } catch (e) {
      setErr(message(e, 'Could not load that brand.')); setBrand(null)
    } finally { setBusy(false) }
  }

  async function saveTerms (templateKey, values) {
    setBusy(true); setErr('')
    try {
      await api.put(`/god/ai-workforce/brands/${brandId.trim()}/terms`, {
        template_key: templateKey,
        commercial_mode: values.commercial_mode,
        catalog_item_key: values.catalog_item_key || '',
        included_plan_keys: parseList(values.included_plan_keys),
        eligible_plan_keys: parseList(values.eligible_plan_keys),
        max_per_customer: values.max_per_customer ? Number(values.max_per_customer) : null,
        requires_controlled_first: !!values.requires_controlled_first,
        is_available: !!values.is_available,
        notes: values.notes || '',
      })
      setDraft(null)
      await loadBrand()
    } catch (e) {
      setErr(message(e, 'Those terms were refused.'))
    } finally { setBusy(false) }
  }

  async function openDeployment (id) {
    setBusy(true); setErr(''); setDetail(null)
    try {
      setDetail(await api.get(`/god/ai-workforce/deployments/${id}`))
    } catch (e) {
      setErr(message(e, 'Could not open that deployment.'))
    } finally { setBusy(false) }
  }

  async function post (path, body, then) {
    setBusy(true); setErr('')
    try {
      const out = await api.post(path, body || {})
      if (then) await then(out)
      return out
    } catch (e) {
      setErr(message(e, 'That was refused.'))
    } finally { setBusy(false) }
  }

  const orphans = overview?.orphans || {}

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 18px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            AI Workforce Deployment
          </h1>
          <p style={{ margin: '9px 0 0', color: 'var(--gm-blue)', fontSize: 12, maxWidth: 880 }}>
            What each brand sells, to which packages, and where every
            customer&apos;s AI employee has got to. No price is set here — an AI
            employee is sold by a catalogue item, in Billing, like everything
            else this platform charges for.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'var(--gm-pill-red-bd)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}

        <div className="gm-stats" style={{ marginBottom: 18 }}>
          <Stat k="Deployments" v={overview?.deployments_total ?? '—'} />
          <Stat k="Working now" v={overview?.live ?? '—'}
            tone={(overview?.live || 0) > 0 ? T.amber : T.green}
            s="an AI employee in a live stage" />
          <Stat k="Customers" v={overview?.customers_with_deployments ?? '—'} />
          <Stat k="Orphans" v={orphans?.total ?? '—'}
            tone={(orphans?.total || 0) > 0 ? T.red : T.green}
            s="things that should have stopped" />
        </div>

        <div className="gm-seg" style={{ marginBottom: 16 }}>
          {TABS.map(t => (
            <button key={t} className={`gm-groupbtn ${tab === t ? 'gm-active' : ''}`}
              onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        {tab === 'Overview' && (
          <>
            <div className="gm-card" style={{ marginBottom: 16 }}>
              <Sec>Platform state</Sec>
              <pre style={{ color: 'var(--gm-blue)', fontSize: 11, whiteSpace: 'pre-wrap', margin: 0 }}>
                {JSON.stringify(overview?.platform || {}, null, 2)}
              </pre>
            </div>

            <div className="gm-card" style={{ marginBottom: 16 }}>
              <Sec>Stop what should have stopped</Sec>
              <p style={{ color: 'var(--gm-blue)', fontSize: 12, marginTop: 0 }}>
                Reconciling only ever stops something. An entitlement arriving
                never starts an AI employee — a person does that, with a reason.
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="gm-btn" disabled={busy}
                  onClick={() => post('/god/ai-workforce/reconcile', {}, load)}>
                  RECONCILE EVERY CUSTOMER
                </button>
                <button className="gm-btn" disabled={busy}
                  onClick={() => post('/god/ai-workforce/orphans/repair', {}, load)}>
                  REPAIR ORPHANS
                </button>
              </div>
            </div>

            <div className="gm-card">
              <Sec>The job library — what each job needs before anybody can run it</Sec>
              <div className="gm-tablewrap">
                <table className="gm-table">
                  <thead className="gm-thead">
                    <tr>
                      <th>Job</th><th>Depth</th><th>Channels</th>
                      <th>Needs</th><th>Calendar</th><th>Management</th>
                    </tr>
                  </thead>
                  <tbody>
                    {templates.map(t => (
                      <tr key={t.template_key}>
                        <td>{t.name}</td>
                        <td>{t.depth}</td>
                        <td>{joined(t.channels) || '—'}</td>
                        <td>{joined(t.requirements?.features) || '—'}</td>
                        <td>{t.requirements?.needs_calendar ? 'Yes' : 'No'}</td>
                        <td>{t.advanced_capability ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {loading && <div className="gm-empty">Loading…</div>}
            </div>
          </>
        )}

        {tab === 'Brand terms' && (
          <div className="gm-card">
            <Sec>Brand commercial terms</Sec>
            <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
              <input className="gm-input" placeholder="Brand (platform) id"
                value={brandId} onChange={e => setBrandId(e.target.value)}
                style={{ minWidth: 340 }} />
              <button className="gm-btn gm-primary" onClick={loadBrand} disabled={busy}>
                LOAD
              </button>
            </div>

            {brand && (
              <>
                <p style={{ color: 'var(--gm-blue)', fontSize: 12 }}>
                  {brand.brand?.name} — {brand.note}
                </p>
                {(brand.catalog || []).map(row => (
                  <div key={row.template_key} className="gm-card" style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                      <div>
                        <div style={{ color: 'var(--gm-head)', fontSize: 14 }}>{row.display_name}</div>
                        <div style={{ color: 'var(--gm-blue)', fontSize: 11 }}>
                          {row.template_key} · {row.depth}
                          {row.advanced_capability ? ' · management capability' : ''}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <Pill tone={row.acquirable ? 'ok' : 'warn'}>
                          {row.acquirable ? 'Acquirable' : 'Blocked'}
                        </Pill>
                        <button className="gm-btn" onClick={() => setDraft(
                          draft?.key === row.template_key ? null : {
                            key: row.template_key,
                            commercial_mode: row.terms?.commercial_mode || 'addon',
                            catalog_item_key: row.terms?.catalog_item_key || '',
                            included_plan_keys: joined(row.terms?.included_plan_keys),
                            eligible_plan_keys: joined(row.terms?.eligible_plan_keys),
                            max_per_customer: row.terms?.max_per_customer || '',
                            requires_controlled_first:
                              row.terms?.requires_controlled_first !== false,
                            is_available: !!row.terms?.is_available,
                            notes: row.terms?.notes || '',
                          })}>
                          {draft?.key === row.template_key ? 'CLOSE' : 'TERMS'}
                        </button>
                      </div>
                    </div>

                    {!!(row.blockers || []).length && (
                      <ul style={{ margin: '8px 0 0', paddingLeft: 18, color: 'var(--gm-blue)', fontSize: 11 }}>
                        {row.blockers.map((b, i) => <li key={i}>{b}</li>)}
                      </ul>
                    )}

                    {draft?.key === row.template_key && (
                      <div style={{ marginTop: 14, borderTop: '1px solid var(--gm-card-line)', paddingTop: 14 }}>
                        <Row label="How it is acquired">
                          <select className="gm-input" value={draft.commercial_mode}
                            onChange={e => setDraft({ ...draft, commercial_mode: e.target.value })}>
                            {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                          </select>
                        </Row>
                        <Row label="Catalogue item key — created in Billing, and no price is set here">
                          <input className="gm-input" value={draft.catalog_item_key}
                            onChange={e => setDraft({ ...draft, catalog_item_key: e.target.value })} />
                        </Row>
                        <Row label="Packages that INCLUDE it (comma separated)">
                          <input className="gm-input" value={draft.included_plan_keys}
                            onChange={e => setDraft({ ...draft, included_plan_keys: e.target.value })} />
                        </Row>
                        <Row label="Packages ELIGIBLE to hold it — blank means no restriction, and a management capability with none named is refused">
                          <input className="gm-input" value={draft.eligible_plan_keys}
                            onChange={e => setDraft({ ...draft, eligible_plan_keys: e.target.value })} />
                        </Row>
                        <Row label="Most one customer may hold (blank = no stated limit)">
                          <input className="gm-input" type="number" min="1"
                            value={draft.max_per_customer}
                            onChange={e => setDraft({ ...draft, max_per_customer: e.target.value })} />
                        </Row>
                        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--gm-blue)', fontSize: 12, marginBottom: 8 }}>
                          <input type="checkbox" checked={draft.requires_controlled_first}
                            onChange={e => setDraft({ ...draft, requires_controlled_first: e.target.checked })} />
                          Must run in the controlled stage before full capacity
                        </label>
                        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--gm-blue)', fontSize: 12, marginBottom: 12 }}>
                          <input type="checkbox" checked={draft.is_available}
                            onChange={e => setDraft({ ...draft, is_available: e.target.checked })} />
                          Available to this brand&apos;s customers
                        </label>
                        <button className="gm-btn gm-primary" disabled={busy}
                          onClick={() => saveTerms(row.template_key, draft)}>
                          SAVE TERMS
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {tab === 'Deployments' && (
          <div className="gm-card">
            <Sec>Every customer&apos;s AI employees</Sec>
            <div className="gm-tablewrap">
              <table className="gm-table">
                <thead className="gm-thead">
                  <tr>
                    <th>Customer</th><th>AI employee</th><th>State</th>
                    <th>Commercial</th><th>Readiness</th><th />
                  </tr>
                </thead>
                <tbody>
                  {deployments.map(d => (
                    <tr key={d.id}>
                      <td>{d.organization_name || d.organization_id}</td>
                      <td>{d.name}</td>
                      <td>{d.state_label}</td>
                      <td>{d.commerce?.commercial_label}</td>
                      <td>{d.readiness?.verdict_label || '—'}</td>
                      <td>
                        <button className="gm-btn" onClick={() => openDeployment(d.id)}>
                          OPEN
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!deployments.length && <div className="gm-empty">No deployments yet.</div>}

            {detail && (
              <div className="gm-card" style={{ marginTop: 16 }}>
                <div style={{ color: 'var(--gm-head)', fontSize: 15 }}>{detail.name}</div>
                <div style={{ color: 'var(--gm-blue)', fontSize: 11, marginBottom: 12 }}>
                  {detail.template_key} · {detail.state_label} · {detail.commerce?.commercial_label}
                </div>

                <Sec>Readiness — decided by code, every time</Sec>
                <ul style={{ margin: '4px 0 14px', paddingLeft: 18, fontSize: 12 }}>
                  {(detail.readiness?.checks || []).map(c => (
                    <li key={c.key} style={{ color: c.passed ? 'var(--gm-blue)' : T.amber, marginBottom: 3 }}>
                      {c.passed ? 'OK' : c.severity.toUpperCase()} — {c.label}. {c.detail}
                      {!c.passed && c.fix ? ` ${c.fix}` : ''}
                    </li>
                  ))}
                </ul>

                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button className="gm-btn" disabled={busy}
                    onClick={() => post(
                      `/god/ai-workforce/deployments/${detail.id}/acknowledge-review`,
                      { note: 'Reviewed in God Mode' },
                      () => openDeployment(detail.id))}>
                    ACKNOWLEDGE REVIEW
                  </button>
                  <button className="gm-btn gm-primary" disabled={busy}
                    onClick={() => post(
                      `/god/ai-workforce/deployments/${detail.id}/activation`,
                      { stage: 'controlled',
                        reason: 'Controlled launch agreed with the customer',
                        expected_state: detail.state },
                      async () => { await openDeployment(detail.id); await loadDeployments() })}>
                    START — CONTROLLED
                  </button>
                  <button className="gm-btn" disabled={busy}
                    onClick={() => post(
                      `/god/ai-workforce/deployments/${detail.id}/activation`,
                      { stage: 'active',
                        reason: 'Full capacity agreed with the customer',
                        expected_state: detail.state },
                      async () => { await openDeployment(detail.id); await loadDeployments() })}>
                    START — FULL CAPACITY
                  </button>
                  <button className="gm-btn gm-danger" disabled={busy}
                    onClick={() => post(
                      `/god/ai-workforce/deployments/${detail.id}/stand-down`,
                      { reason: 'Stood down from God Mode' },
                      async () => { await openDeployment(detail.id); await loadDeployments() })}>
                    STAND DOWN
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'Proof' && (
          <div className="gm-card">
            <Sec>Proof</Sec>
            <p style={{ color: 'var(--gm-blue)', fontSize: 12, marginTop: 0 }}>
              Both runs build synthetic organizations inside a savepoint and roll
              it back. Every contact is a reserved fictional number at an
              unresolvable domain, and no live channel adapter is reachable.
            </p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              <button className="gm-btn gm-primary" disabled={busy}
                onClick={() => post('/god/ai-workforce/proof/run',
                  { confirm_synthetic_data: true, lifecycle: 'all' }, setProof)}>
                RUN THE THREE LIFECYCLES
              </button>
              <button className="gm-btn" disabled={busy}
                onClick={() => post('/god/ai-workforce/proof/attack',
                  { confirm_synthetic_data: true }, setProof)}>
                RUN THE ADVERSARIAL HARNESS
              </button>
            </div>
            {proof && (
              <>
                <div style={{ marginBottom: 10 }}>
                  <Pill tone={proof.all_passed ? 'ok' : 'bad'}>
                    {proof.passed} / {proof.total} passed
                  </Pill>
                </div>
                <pre style={{ color: 'var(--gm-blue)', fontSize: 11, whiteSpace: 'pre-wrap', margin: 0, maxHeight: 520, overflow: 'auto' }}>
                  {JSON.stringify(proof, null, 2)}
                </pre>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
