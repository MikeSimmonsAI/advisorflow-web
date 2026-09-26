/* EVOSENSE — PROVIDERS & CONTROLS: the control room (Phase 7.3 light rebuild; board screen 4).
 *
 *   EVOSENSE CONTROLS   the kill switches. Each shows its SWITCH (running /
 *                       paused) AND what the channel can actually do, from
 *                       the server's channel truth: SMS with the seller-SMS
 *                       program off reads PROGRAM OFF, never "Running".
 *                       Anyone may pull one; only a workspace admin may release
 *                       it. Every switch is enforced by the server in the
 *                       service that would act - a hidden button is not a control.
 *   BUDGET              organization ceilings on top of each strategy's own,
 *                       and what has actually been spent.
 *   DATA CAPABILITIES   what serves each capability, said honestly:
 *                       REAL CONNECTOR / SANDBOX / MANUAL / IMPORT / INTERFACE ONLY.
 *                       Sandbox is never made to look live.
 *   PROVIDERS           the ONE canonical state per provider (the same value the
 *                       Source Registry shows), cost, last result. A source
 *                       refusing the platform reads BLOCKED everywhere.
 * No credential is stored, sent or shown here.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import {
  Alert, EvoApp, Hero, Metric, Metrics, PageSkeleton, Panel, SandboxTag, TabBar, Tag, Tile, ago, cents, humanize,
} from '../ds/ds'
import '../ds/evo-pages.css'

const SWITCHES = [
  ['paused_all', 'Engine', 'Everything: hunting, lookups and outreach. Seller replies are still saved and opt-outs still honoured.'],
  ['paused_discovery', 'Discovery', 'No hunts, scheduled or manual.'],
  ['paused_paid_data', 'Paid data', 'No paid lookup is made. Budgets are untouched.'],
  ['paused_sms', 'SMS', 'No owner is texted.'],
  ['paused_email', 'Email', 'No owner is emailed.', 'EvoSense has no email outreach yet'],
  ['paused_voice', 'Voice', 'No owner is called.', 'EvoSense has no voice outreach yet'],
  ['paused_ai_replies', 'AI reply reading', 'Replies are read by rules only when paused. Opt-outs are always honoured.'],
]

const KIND = {
  real: ['live', 'Real connector'], sandbox: ['sandbox', 'Sandbox'], manual: [null, 'Manual'],
  import: [null, 'Import'], interface_only: [null, 'Interface only'], not_built: [null, 'Not built'],
}
// Board: a tile per data capability, with the honest connector kind on it.
const CAP_ICON = {
  PROPERTY_SEARCH: 'search', PARCEL: 'map', ASSESSOR: 'building', OWNERSHIP: 'key', TAX: 'dollar',
  FORECLOSURE: 'gavel', PROBATE: 'doc', CODE_VIOLATION: 'alert', VACANCY: 'home', LISTING: 'list',
  VALUATION: 'chart', COMPS: 'layers', CONTACT_ENRICHMENT: 'user', PHONE_VALIDATION: 'phone',
  EMAIL_VALIDATION: 'mail', ENTITY_RESOLUTION: 'users',
}
const KIND_TONE = { real: 'good', sandbox: 'attention', manual: null, import: 'violet', interface_only: 'quiet', not_built: 'quiet' }
const STATE_CLASS = { good: 'good', attention: 'attention', danger: 'attention', quiet: 'quiet' }

function StateBadge({ state, tone, why }) {
  return (
    <span className={`evo-status is-${STATE_CLASS[tone] || 'quiet'}`} title={why || state}
          style={tone === 'danger' ? { color: 'var(--evo-danger-ink)' } : null}>{state}</span>
  )
}

/** configured · enabled · reachable · healthy · operational, said separately. */
function Dims({ x }) {
  if (x.connector_kind !== 'real') return null
  const yn = (v) => (v === null || v === undefined ? 'unknown' : v ? 'yes' : 'no')
  return (
    <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>
      configured {yn(x.configured)} · enabled {yn(x.enabled)} · reachable {yn(x.reachable)} · healthy {yn(x.healthy)} ·{' '}
      <strong style={{ color: x.operational ? 'var(--evo-success-ink)' : undefined }}>operational {yn(x.operational)}</strong>
    </span>
  )
}

function Kind({ kind }) {
  const [k, l] = KIND[kind] || [null, humanize(kind)]
  return <Tag kind={k}>{l}</Tag>
}

export default function EvoControls() {
  const [ctl, setCtl] = useState(null)
  const [prov, setProv] = useState(null)
  const [reg, setReg] = useState(null)
  const [weights, setWeights] = useState({})
  const [spent, setSpent] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [budget, setBudget] = useState({ day: '', month: '', cap: '' })
  const [tab, setTab] = useState(() => (typeof window !== 'undefined' && window.location.hash === '#budget' ? 'usage' : window.location.hash === '#sources' ? 'sources' : 'providers'))

  const load = useCallback(async () => {
    try {
      const [c, p, cc, r] = await Promise.all([api.get('/wholesale/evosense/controls'), api.get('/wholesale/evosense/providers'),
        api.get('/wholesale/evosense/command-center').catch(() => null),
        api.get('/wholesale/evosense/sources').catch(() => null)])
      setCtl(c); setProv(p); setSpent(cc ? cc.spent : null); setReg(r)
      setWeights(Object.fromEntries(Object.entries({ ...(c.default_weights || {}), ...(c.score_weights || {}) })
        .map(([k, v]) => [k, String(v)])))
      setBudget({ day: c.org_daily_budget_cents == null ? '' : String(c.org_daily_budget_cents / 100),
                  month: c.org_monthly_budget_cents == null ? '' : String(c.org_monthly_budget_cents / 100),
                  cap: String(c.owner_touch_cap_days) })
      setError(null)
    } catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function patch(body, msg) {
    setBusy(true); setError(null); setNotice(null)
    try { setCtl(await api.patch('/wholesale/evosense/controls', body)); if (msg) setNotice(msg) }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function provider(key, body) {
    setBusy(true); setError(null)
    try {
      setProv(await api.patch('/wholesale/evosense/providers', { key, ...body }))
      setReg(await api.get('/wholesale/evosense/sources'))
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function verify(key) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post('/wholesale/evosense/sources/verify', { key })
      setReg(r.registry)
      if (r.ok) setNotice(`${key}: verified against the live source.`)
      else setError(`${key}: ${r.code || 'failed'} — ${r.error || ''}`)
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  function saveWeights() {
    const defaults = ctl.default_weights || {}
    const changed = {}
    Object.entries(weights).forEach(([k, v]) => {
      const n = Number(v)
      if (v !== '' && Number.isFinite(n) && n !== defaults[k]) changed[k] = Math.round(n)
    })
    patch({ score_weights: Object.keys(changed).length ? changed : null }, 'Scoring weights saved. Properties re-score on their next evaluation.')
  }

  if (!ctl || !prov) return <EvoApp world="acquisition">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>

  const pausedCount = SWITCHES.filter(([k]) => ctl[k]).length
  const failing = prov.providers.filter((p) => ['FAILED', 'BLOCKED', 'DEGRADED', 'RATE LIMITED'].includes(p.state))
  const sandboxOn = prov.providers.some((p) => p.connector_kind === 'sandbox' && p.enabled)

  return (
    <EvoApp world="acquisition">
      <Hero
        scene="network"
        eyebrow="System Intelligence"
        title="Providers & Controls"
        sub="Trusted data. Controlled spend. Maximum impact."
        quote="The right data at the right time creates opportunity."
        meta={[
          { label: ctl.paused_all ? 'Engine paused' : 'Engine running', tone: ctl.paused_all ? 'paused' : 'live' },
          { label: prov.real_connectors.length ? <><b>{prov.real_connectors.length}</b> operational source{prov.real_connectors.length === 1 ? '' : 's'}</> : 'No operational real source' },
          (prov.real_connectors_unavailable || []).length ? { label: <><b>{prov.real_connectors_unavailable.length}</b> enabled but unavailable</>, tone: 'paused' } : null,
          pausedCount ? { label: <><b>{pausedCount}</b> switch{pausedCount === 1 ? '' : 'es'} paused</>, tone: 'paused' } : null,
        ]}
      />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <TabBar label="Providers and controls" value={tab} onChange={setTab}
              items={[
                { key: 'sources', label: 'Source Registry', count: reg ? reg.sources.filter((x) => x.operational).length + ' operational' : null },
                { key: 'providers', label: 'Service Providers', count: prov.providers.length },
                { key: 'controls', label: 'Controls & Compliance', count: pausedCount ? `${pausedCount} paused` : null },
                { key: 'usage', label: 'Usage & Costs' },
              ]} />

      <div className="evo-stack" role="tabpanel" id="panel-sources" aria-labelledby="tab-sources" hidden={tab !== 'sources'}>
        <Panel title="Source Registry" flush
               hint="HEALTHY only after a real probe or run succeeded — and it means retrieval worked, not that every derived field is true · paid vendors are not purchased">
          {!reg ? <p className="evo-muted" style={{ padding: '0 20px' }}>Loading sources…</p> : (
            <div className="evo-table-wrap">
              <table className="evo-table evo-table--cards">
                <thead><tr><th scope="col">Source</th><th scope="col">Jurisdiction</th><th scope="col">Access</th>
                  <th scope="col">State</th><th scope="col">Last verified</th><th scope="col"><span className="evo-sr">Use</span></th></tr></thead>
                <tbody>
                  {reg.sources.map((x) => (
                    <tr key={x.key} className={['FAILED', 'BLOCKED'].includes(x.state) ? 'evo-provider is-failing' : 'evo-provider'}>
                      <td className="is-lead" data-label="">
                        <span className="evo-strong">{x.label}</span>
                        <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>
                          {x.source_type}{x.role ? ` · ${x.role}` : ''}{x.cost ? ` · ${x.cost}` : ''}
                          {x.public_url ? <> · <a href={x.public_url} target="_blank" rel="noreferrer">publisher</a></> : null}
                        </span>
                        {x.terms_note ? <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>{x.terms_note}</span> : null}
                        <Dims x={x} />
                      </td>
                      <td data-label="Jurisdiction" className="evo-small">{x.jurisdiction || '—'}</td>
                      <td data-label="Access" className="evo-small">{x.access_method || '—'}{x.refresh ? <><br /><span className="evo-muted">{x.refresh}</span></> : null}</td>
                      <td data-label="State">
                        <StateBadge state={x.state} tone={x.tone} why={x.why} />
                        <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>{x.why}</span>
                      </td>
                      <td data-label="Last verified" className="evo-small">
                        {x.last_verified_at ? ago(x.last_verified_at) : <span className="evo-muted">never</span>}
                        {x.last_record_count != null ? <><br /><span className="evo-muted">{x.last_record_count} record{x.last_record_count === 1 ? '' : 's'} last run</span></> : null}
                      </td>
                      <td data-label="" className="is-right">
                        {x.connector_kind === 'real' ? (
                          <span className="evo-chips">
                            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                    onClick={() => provider(x.key, { enabled: !x.enabled })}>{x.enabled ? 'Disable' : 'Enable'}</button>
                            {x.enabled && x.state !== 'BLOCKED' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                                 onClick={() => verify(x.key)}>Verify</button> : null}
                            {x.enabled && x.state === 'BLOCKED' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                                 title="Blocked platform-wide. Only a platform admin's re-test (one request) is sent; anyone else is refused without calling the source."
                                                 onClick={() => verify(x.key)}>Platform re-test</button> : null}
                          </span>
                        ) : <span className="evo-muted evo-small">{x.state === 'MANUAL ONLY' ? 'manual entry / CSV' : x.state === 'NOT PURCHASED' ? 'not purchased' : x.state.toLowerCase()}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="evo-muted evo-small" style={{ margin: 0, padding: '12px 20px 16px' }}>Public-record sources are read politely:
            no logins, no CAPTCHAs, byte-range reads of published files, one request at a time. Every record keeps its raw
            evidence, the adapter version and the source&apos;s own date.</p>
        </Panel>
      </div>

      <div className="evo-stack" role="tabpanel" id="panel-providers" aria-labelledby="tab-providers" hidden={tab !== 'providers'}>
        <div>
          <h2 className="evo-section-title">
            Data capabilities
            <span className="evo-panel__hint">{prov.real_connectors.length ? `${prov.real_connectors.length} operational real source(s)`
              : 'No real source is operational — nothing here is live data'}{prov.markets && prov.markets.length ? ` · rated for ${prov.markets.join(', ')}` : ''}</span>
          </h2>
          <div className="evo-tiles">
            {prov.capabilities.map((c) => (
              <Tile key={c.capability} icon={CAP_ICON[c.capability] || 'layers'} tone={KIND_TONE[c.kind]}
                    name={humanize(c.capability.toLowerCase())}
                    desc={<>
                      {c.note || (c.providers.length ? `Served by ${c.providers.join(', ')}` : 'Nothing serves this')}
                      {(c.unavailable || []).filter((u) => !['NOT PURCHASED'].includes(u.state)).length ? (
                        <span className="evo-muted" style={{ display: 'block', marginTop: 4 }}>
                          Unavailable: {(c.unavailable || []).filter((u) => u.state !== 'NOT PURCHASED').map((u) => `${u.label} (${u.state.toLowerCase()})`).join(', ')}</span>
                      ) : null}
                      {(c.by_market || []).length ? (
                        <span style={{ display: 'block', marginTop: 6 }}>
                          {c.by_market.map((m) => (
                            <span key={m.market} className="evo-small" style={{ display: 'block' }}>
                              <strong>{m.market}:</strong> <span style={{ color: m.kind === 'real' ? 'var(--evo-success-ink)' : m.kind === 'unavailable' ? 'var(--evo-danger-ink)' : undefined }}>{m.label}</span>
                            </span>))}
                        </span>
                      ) : null}
                    </>}
                    foot={<><Kind kind={c.kind} />{c.providers.length ? <span className="evo-muted evo-small">{c.providers.length} serving</span> : null}</>} />
            ))}
          </div>
        </div>
        <Panel title="Providers" flush hint={sandboxOn ? 'Sandbox providers serve only sandbox properties' : null}>
          {failing.length ? (
            <div style={{ padding: '0 20px' }}>
              <Alert>{failing.length} provider{failing.length === 1 ? ' is' : 's are'} not working — {failing.map((p) => `${p.label}: ${p.state.toLowerCase()}`).join('; ')}. A failed paid lookup is refunded.</Alert>
            </div>
          ) : null}
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <thead><tr><th scope="col">Provider</th><th scope="col">Kind</th><th scope="col">Status</th><th scope="col">Cost</th><th scope="col">Last result</th><th scope="col"><span className="evo-sr">Use</span></th></tr></thead>
              <tbody>
                {prov.providers.map((p) => (
                  <tr key={p.key} className={['FAILED', 'BLOCKED'].includes(p.state) ? 'evo-provider is-failing' : 'evo-provider'}>
                    <td className="is-lead" data-label="">
                      <span className="evo-strong">{p.label}</span>
                      <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>{p.coverage}</span>
                    </td>
                    <td data-label="Kind">{p.connector_kind === 'sandbox' ? <SandboxTag /> : <Kind kind={p.connector_kind} />}</td>
                    <td data-label="Status"><StateBadge state={p.state} tone={p.tone} why={p.why} />
                      <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>{p.why}</span></td>
                    <td data-label="Cost" className="evo-small">{p.connector_kind === 'sandbox' && Object.keys(p.costs).length
                      ? `simulated: ${Object.entries(p.costs).map(([k, v]) => `${humanize(k.toLowerCase())} ${cents(v)}`).join(' · ')}`
                      : p.cost}</td>
                    <td data-label="Last result" className="evo-small">
                      {p.last_failure_reason ? (
                        <span style={{ color: 'var(--evo-danger-ink)' }}>Failed · {p.last_failure_reason}{p.last_failure_at ? ` · ${ago(p.last_failure_at)}` : ''}</span>
                      ) : p.calls_total ? <span>{p.successes_total}/{p.calls_total} succeeded</span> : <span className="evo-muted">No calls yet</span>}
                    </td>
                    <td data-label="" className="is-right">
                      {['sandbox', 'real'].includes(p.connector_kind) ? (
                        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                onClick={() => provider(p.key, { enabled: !p.enabled })}>{p.enabled ? 'Disable' : 'Enable'}</button>
                      ) : <span className="evo-muted evo-small">{p.connector_kind === 'interface_only' ? 'not purchased' : 'manual — nothing is called'}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="evo-muted evo-small" style={{ margin: 0, padding: '12px 20px 16px' }}>Sandbox providers are off for every organization
            until an admin turns them on, and they never serve a real (non-sandbox) property. No credential is ever shown here.</p>
        </Panel>
      </div>

      <div className="evo-stack" role="tabpanel" id="panel-controls" aria-labelledby="tab-controls" hidden={tab !== 'controls'}>
        <Panel title="EvoSense controls" hint="Anyone can pause. Only an admin can resume.">
          <div className="evo-switches">
            {SWITCHES.map(([k, label, help]) => {
              const paused = !!ctl[k]
              const ch = (ctl.channels || {})[k]
              return (
                <div key={k} className={`evo-switch${paused ? ' is-paused' : ''}`}>
                  <div className="evo-switch__top">
                    <span className="evo-switch__name">{label}</span>
                    <button type="button" role="switch" className="evo-toggle" aria-checked={!paused} disabled={busy}
                            aria-label={`${label}: ${paused ? 'paused — resume' : 'running — pause'}`}
                            onClick={() => patch({ [k]: !paused })} />
                  </div>
                  <span className="evo-chips">
                    {ch ? (
                      <span className={`evo-status is-${ch.state === 'OPERATIONAL' ? 'good' : ch.state === 'PAUSED' ? 'attention' : 'quiet'}`}
                            style={['PROGRAM OFF', 'UNAVAILABLE', 'NOTHING CONNECTED', 'NOT CONFIGURED'].includes(ch.state) ? { color: 'var(--evo-warning-ink)' } : null}
                            title={ch.why}>{humanize(ch.state.toLowerCase())}</span>
                    ) : <span className={`evo-status is-${paused ? 'attention' : 'good'}`}>{paused ? 'Paused' : 'Switch on'}</span>}
                    <span className="evo-muted evo-small">switch {paused ? 'paused' : 'not paused'}</span>
                  </span>
                  <p className="evo-switch__desc">{ch && ch.why ? ch.why : help}</p>
                </div>
              )
            })}
          </div>
        </Panel>

        <Panel title="Opportunity scoring weights" hint={`deterministic · ${ctl.score_version || ''}${ctl.score_weights ? ' · customised' : ' · defaults'} · admin only`}>
          <div className="evo-form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
            {Object.keys(ctl.default_weights || {}).map((k) => (
              <label key={k} className="evo-field" htmlFor={`w-${k}`}>
                <span className="evo-field__label">{humanize(k.toLowerCase())}</span>
                <input id={`w-${k}`} className="evo-input" inputMode="numeric" value={weights[k] ?? ''}
                       onChange={(e) => setWeights({ ...weights, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <div style={{ marginTop: 12 }} className="evo-chips">
            <button type="button" className="evo-btn evo-btn--primary" disabled={busy} onClick={saveWeights}>Save weights</button>
            <button type="button" className="evo-btn evo-btn--ghost" disabled={busy}
                    onClick={() => patch({ score_weights: null }, 'Weights reset to the defaults.')}>Reset to defaults</button>
          </div>
          <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>Points each current signal adds (−30 to 30). Aging evidence counts half;
            stale evidence counts nothing. A changed weight changes the score version, so older scores stay explainable. AI never sets a score.</p>
        </Panel>

        <Panel title="Compliance guarantees" hint="enforced by the server, not by this screen">
          <ul className="evo-checklist">
            <li>An opted-out or suppressed owner is never contacted again — by any strategy, ever.</li>
            <li>Owners are contacted at most once every {ctl.owner_touch_cap_days} days, across every strategy.</li>
            <li>EvoSense never makes an offer, signs anything or moves money. It hands the conversation to you.</li>
            <li>Scores, budgets and do-not-contact decisions are deterministic rules; AI only reads replies.</li>
            <li>A phone found in public records or by skip trace is never permission to text. Only a consent record under the Wholesale seller SMS program is.</li>
            <li>Provider credentials stay on the server and are never sent to a browser.</li>
          </ul>
        </Panel>
      </div>

      <div className="evo-stack" role="tabpanel" id="panel-usage" aria-labelledby="tab-usage" hidden={tab !== 'usage'}>
        <Metrics label="Usage summary">
          <Metric label="Spent today" value={spent ? cents(spent.today_cents) : null} tone="primary" />
          <Metric label="Spent this month" value={spent ? cents(spent.month_cents) : null} tone="primary" />
          <Metric label="All time" value={spent ? cents(spent.all_time_cents) : null} />
          <Metric label="Per contact found" value={spent && spent.cost_per_contact_found_cents != null ? cents(spent.cost_per_contact_found_cents) : null} tone="success" />
          <Metric label="Provider failures" value={failing.length} tone={failing.length ? 'danger' : 'success'} />
        </Metrics>
        <Panel title="Budget" id="budget" hint="On top of each strategy's own budget · blank means strategy budgets only">
          <form className="evo-form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', alignItems: 'end' }}
                onSubmit={(e) => {
                  e.preventDefault()
                  patch({ org_daily_budget_cents: budget.day === '' ? null : Math.round(Number(budget.day) * 100),
                          org_monthly_budget_cents: budget.month === '' ? null : Math.round(Number(budget.month) * 100),
                          owner_touch_cap_days: Number(budget.cap) || 7 }, 'Budget saved.')
                }}>
            <label className="evo-field" htmlFor="c-day"><span className="evo-field__label">Daily, all strategies ($)</span>
              <input id="c-day" className="evo-input" inputMode="decimal" value={budget.day} onChange={(e) => setBudget({ ...budget, day: e.target.value })} /></label>
            <label className="evo-field" htmlFor="c-mon"><span className="evo-field__label">Monthly, all strategies ($)</span>
              <input id="c-mon" className="evo-input" inputMode="decimal" value={budget.month} onChange={(e) => setBudget({ ...budget, month: e.target.value })} /></label>
            <label className="evo-field" htmlFor="c-cap"><span className="evo-field__label">Contact one owner at most every (days)</span>
              <input id="c-cap" className="evo-input" inputMode="numeric" value={budget.cap} onChange={(e) => setBudget({ ...budget, cap: e.target.value })} /></label>
            <div><button type="submit" className="evo-btn evo-btn--primary" disabled={busy}>Save budget</button></div>
          </form>
          {spent ? (
            <>
              <div className="evo-divider" />
              <dl className="evo-kv">
                <dt>Spent today</dt><dd>{cents(spent.today_cents)}</dd>
                <dt>Spent this month</dt><dd>{cents(spent.month_cents)}</dd>
                <dt>All time</dt><dd>{cents(spent.all_time_cents)}</dd>
                {spent.cost_per_contact_found_cents != null ? <><dt>Per contact found</dt><dd>{cents(spent.cost_per_contact_found_cents)}</dd></> : null}
              </dl>
              {spent.note ? <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>{spent.note}</p> : null}
            </>
          ) : null}
        </Panel>

      </div>
    </EvoApp>
  )
}
