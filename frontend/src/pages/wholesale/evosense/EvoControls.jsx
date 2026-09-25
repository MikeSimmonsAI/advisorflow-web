/* EVOSENSE — PROVIDERS & CONTROLS: the control room (Phase 7.3 light rebuild; board screen 4).
 *
 *   EVOSENSE CONTROLS   the kill switches, each RUNNING / PAUSED (and NOT
 *                       CONFIGURED where EvoSense has no such channel yet).
 *                       Anyone may pull one; only a workspace admin may release
 *                       it. Every switch is enforced by the server in the
 *                       service that would act - a hidden button is not a control.
 *   BUDGET              organization ceilings on top of each strategy's own,
 *                       and what has actually been spent.
 *   DATA CAPABILITIES   what serves each capability, said honestly:
 *                       REAL CONNECTOR / SANDBOX / MANUAL / IMPORT / INTERFACE ONLY.
 *                       Sandbox is never made to look live.
 *   PROVIDERS           health, capability, cost, last result. Healthy providers
 *                       are quiet; failures are prominent.
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

function Kind({ kind }) {
  const [k, l] = KIND[kind] || [null, humanize(kind)]
  return <Tag kind={k}>{l}</Tag>
}

export default function EvoControls() {
  const [ctl, setCtl] = useState(null)
  const [prov, setProv] = useState(null)
  const [spent, setSpent] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [budget, setBudget] = useState({ day: '', month: '', cap: '' })
  const [tab, setTab] = useState(() => (typeof window !== 'undefined' && window.location.hash === '#budget' ? 'usage' : 'providers'))

  const load = useCallback(async () => {
    try {
      const [c, p, cc] = await Promise.all([api.get('/wholesale/evosense/controls'), api.get('/wholesale/evosense/providers'),
        api.get('/wholesale/evosense/command-center').catch(() => null)])
      setCtl(c); setProv(p); setSpent(cc ? cc.spent : null)
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
    try { setProv(await api.patch('/wholesale/evosense/providers', { key, ...body })) }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  if (!ctl || !prov) return <EvoApp world="acquisition">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>

  const pausedCount = SWITCHES.filter(([k]) => ctl[k]).length
  const failing = prov.providers.filter((p) => p.last_failure_reason)
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
          { label: prov.real_connectors.length ? <><b>{prov.real_connectors.length}</b> real connector{prov.real_connectors.length === 1 ? '' : 's'}</> : 'No real vendor connected' },
          pausedCount ? { label: <><b>{pausedCount}</b> switch{pausedCount === 1 ? '' : 'es'} paused</>, tone: 'paused' } : null,
        ]}
      />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <TabBar label="Providers and controls" value={tab} onChange={setTab}
              items={[
                { key: 'providers', label: 'Service Providers', count: prov.providers.length },
                { key: 'controls', label: 'Controls & Compliance', count: pausedCount ? `${pausedCount} paused` : null },
                { key: 'usage', label: 'Usage & Costs' },
              ]} />

      <div className="evo-stack" role="tabpanel" id="panel-providers" aria-labelledby="tab-providers" hidden={tab !== 'providers'}>
        <div>
          <h2 className="evo-section-title">
            Data capabilities
            <span className="evo-panel__hint">{prov.real_connectors.length ? `${prov.real_connectors.length} real connector(s)`
              : 'No real data vendor is connected — nothing here is live data'}</span>
          </h2>
          <div className="evo-tiles">
            {prov.capabilities.map((c) => (
              <Tile key={c.capability} icon={CAP_ICON[c.capability] || 'layers'} tone={KIND_TONE[c.kind]}
                    name={humanize(c.capability.toLowerCase())}
                    desc={c.note || (c.providers.length ? `Served by ${c.providers.join(', ')}` : 'No provider serves this yet')}
                    foot={<><Kind kind={c.kind} />{c.providers.length ? <span className="evo-muted evo-small">{c.providers.length} provider{c.providers.length === 1 ? '' : 's'}</span> : null}</>} />
            ))}
          </div>
        </div>
        <Panel title="Providers" flush hint={sandboxOn ? 'Sandbox providers serve only sandbox properties' : null}>
          {failing.length ? (
            <div style={{ padding: '0 20px' }}>
              <Alert>{failing.length} provider{failing.length === 1 ? ' has' : 's have'} failed recently — see below. A failed paid lookup is refunded.</Alert>
            </div>
          ) : null}
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <thead><tr><th scope="col">Provider</th><th scope="col">Kind</th><th scope="col">Status</th><th scope="col">Cost</th><th scope="col">Last result</th><th scope="col"><span className="evo-sr">Use</span></th></tr></thead>
              <tbody>
                {prov.providers.map((p) => (
                  <tr key={p.key} className={p.last_failure_reason ? 'evo-provider is-failing' : 'evo-provider'}>
                    <td className="is-lead" data-label="">
                      <span className="evo-strong">{p.label}</span>
                      <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>{p.coverage}</span>
                    </td>
                    <td data-label="Kind">{p.connector_kind === 'sandbox' ? <SandboxTag /> : <Kind kind={p.connector_kind} />}</td>
                    <td data-label="Status"><span className={`evo-status is-${p.status === 'CONNECTED' ? 'good' : p.status === 'DISABLED' ? 'quiet' : 'attention'}`}>
                      {humanize(p.status.toLowerCase())}</span></td>
                    <td data-label="Cost" className="evo-small">{Object.entries(p.costs).map(([k, v]) => `${humanize(k.toLowerCase())} ${cents(v)}`).join(' · ') || 'free'}</td>
                    <td data-label="Last result" className="evo-small">
                      {p.last_failure_reason ? (
                        <span style={{ color: 'var(--evo-danger-ink)' }}>Failed · {p.last_failure_reason}{p.last_failure_at ? ` · ${ago(p.last_failure_at)}` : ''}</span>
                      ) : p.calls_total ? <span>{p.successes_total}/{p.calls_total} succeeded</span> : <span className="evo-muted">No calls yet</span>}
                    </td>
                    <td data-label="" className="is-right">
                      {['sandbox', 'real'].includes(p.connector_kind) ? (
                        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                                onClick={() => provider(p.key, { enabled: !p.enabled })}>{p.enabled ? 'Disable' : 'Enable'}</button>
                      ) : <span className="evo-muted evo-small">always available</span>}
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
            {SWITCHES.map(([k, label, help, notConfigured]) => {
              const paused = !!ctl[k]
              return (
                <div key={k} className={`evo-switch${paused ? ' is-paused' : ''}`}>
                  <div className="evo-switch__top">
                    <span className="evo-switch__name">{label}</span>
                    <button type="button" role="switch" className="evo-toggle" aria-checked={!paused} disabled={busy}
                            aria-label={`${label}: ${paused ? 'paused — resume' : 'running — pause'}`}
                            onClick={() => patch({ [k]: !paused })} />
                  </div>
                  <span className="evo-chips">
                    {notConfigured && !paused
                      ? <span className="evo-status is-quiet" title={notConfigured}>Not configured</span>
                      : <span className={`evo-status is-${paused ? 'attention' : 'good'}`}>{paused ? 'Paused' : 'Running'}</span>}
                  </span>
                  <p className="evo-switch__desc">{help}</p>
                </div>
              )
            })}
          </div>
        </Panel>

        <Panel title="Compliance guarantees" hint="enforced by the server, not by this screen">
          <ul className="evo-checklist">
            <li>An opted-out or suppressed owner is never contacted again — by any strategy, ever.</li>
            <li>Owners are contacted at most once every {ctl.owner_touch_cap_days} days, across every strategy.</li>
            <li>EvoSense never makes an offer, signs anything or moves money. It hands the conversation to you.</li>
            <li>Scores, budgets and do-not-contact decisions are deterministic rules; AI only reads replies.</li>
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
