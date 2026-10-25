/* EVOSENSE — PROVIDERS & CONTROLS (redesign, Oct 2026).
 *
 * One clear summary, one obvious next action, then eight tabs:
 *   Source Registry      capability cards (coverage as "n/9 counties", never
 *                        nine paragraphs) + a compact source list; a card or
 *                        row opens a drawer with the county matrix and evidence
 *   Service Providers    every registered provider in one searchable table with
 *                        kind / configuration / switch / latest result / scope /
 *                        cost kept as separate columns
 *   Provider Evaluation  unchanged component
 *   Skip-trace Costs     unchanged component
 *   Distress Lists       unchanged component
 *   Capability Registry  the product capability matrix, compact, drawer for detail
 *   Controls & Compliance  kill switches, scoring weights, guarantees
 *   Usage & Costs        spend, paid providers, budget
 *
 * The data and the actions are the page's existing ones: GET /controls,
 * /providers, /sources, /capabilities, /command-center; PATCH /controls,
 * PATCH /providers, POST /sources/verify, the TAD upload. Admin gates are the
 * server's; this screen disables what the person cannot do. A paid provider is
 * never switched on without an explicit in-page approval, and never test-called.
 * No credential is stored, sent or shown here.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { isWholesaleAdmin } from '../../../auth/workspaceAuthority'
import { errText } from '../wsShared'
import { ev } from '../engine'
import { Alert, Drawer, EvoApp, Metric, Metrics, PageSkeleton, Panel, TabBar, ago, cents, humanize } from '../ds/ds'
import '../ds/evo-pages.css'
import './controls-tabs.css'
import './controls/pc.css'
import EvoEvaluation from './EvoEvaluation'
import EvoLists from './EvoLists'
import SkipTraceCostPanel from '../ops/SkipTraceCostPanel'
import RollFixPanel from './RollFixPanel'
import * as M from './controls/model'
import {
  CapabilityCard, CapabilityDrawer, FilterBar, KindTag, Latest, Pill, ProviderDrawer, Stat,
} from './controls/parts'

const SWITCHES = [
  ['paused_all', 'Engine', 'Everything: hunting, lookups and outreach. Seller replies are still saved and opt-outs still honoured.'],
  ['paused_discovery', 'Discovery', 'No hunts, scheduled or manual.'],
  ['paused_paid_data', 'Paid data', 'No paid lookup is made. Budgets are untouched.'],
  ['paused_sms', 'SMS', 'No owner is texted.'],
  ['paused_email', 'Email', 'No owner is emailed.'],
  ['paused_voice', 'Voice', 'No owner is called.'],
  ['paused_ai_replies', 'AI reply reading', 'Replies are read by rules only when paused. Opt-outs are always honoured.'],
]

const TABS = ['sources', 'providers', 'evaluation', 'skipcost', 'lists', 'capabilities', 'controls', 'usage']
const FILTER_KEYS = ['q', 'county', 'cap', 'status']

const CAP_TONE = { OPERATIONAL: 'good', UNVERIFIED: 'info', DEGRADED: 'danger', BLOCKED: 'danger',
                   SANDBOX: 'attention', 'MANUAL ONLY': 'quiet', 'NOT ENABLED': 'quiet', 'NOT CONFIGURED': 'quiet',
                   'EVALUATION ONLY': 'attention', 'TRUTH GATE NOT MET': 'attention' }
const CAP_STATUS_OPTIONS = [['live', 'Live'], ['attention', 'Needs attention'], ['untested', 'Not yet tested'],
                            ['manual', 'Manual / import'], ['disabled', 'Switched off'], ['sandbox', 'Sandbox only'], ['none', 'Not available']]

function readUrl() {
  if (typeof window === 'undefined') return { tab: 'sources', filters: {}, open: null }
  const h = window.location.hash.replace('#', '')
  const tab = h === 'budget' ? 'usage' : (TABS.includes(h) ? h : 'sources')
  const sp = new URLSearchParams(window.location.search)
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) || '']).filter(([, v]) => v))
  return { tab, filters, open: sp.get('open') }
}
function writeUrl(tab, filters, open) {
  const sp = new URLSearchParams(window.location.search)
  FILTER_KEYS.forEach((k) => { if (filters[k]) sp.set(k, filters[k]); else sp.delete(k) })
  if (open) sp.set('open', open); else sp.delete('open')
  const qs = sp.toString()
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${qs ? '?' + qs : ''}#${tab}`)
}

/** Upload the publisher's own file for a source that refuses our servers
 *  (TAD and JCAD serve it to a person's computer, not to the cloud). */
function SourceUpload({ sourceKey, onDone, onError }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState(null)
  const load = useCallback(() => {
    api.get(`/wholesale/evosense/sources/${sourceKey}/upload`).then(setSt).catch(() => setSt(null))
  }, [sourceKey])
  useEffect(() => { load() }, [load])
  if (!st || !st.uploadable) return null
  const cur = st.current
  const blocked = st.access && st.access.blocked
  const pub = st.publisher || 'The publisher'
  const county = st.county || 'County'
  async function send(e) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await api.upload(`/wholesale/evosense/sources/${sourceKey}/upload`, fd)
      setFile(null)
      load()
      onDone(`${st.label}: uploaded (${Math.round((r.size || 0) / 1048576)} MB). ${county} lookups run again on the next hunt or list re-check.`)
    } catch (err) { onError(errText(err)) } finally { setBusy(false) }
  }
  return (
    <section className="pc-upload" id={`${sourceKey}-upload`} aria-label={st.label}>
      <h3 className="pc-h3">{st.label}</h3>
      <p className="pc-sub" style={{ margin: '0 0 8px' }}>{pub} refuses our servers but gives the same free file to your own computer.</p>
      <p className="pc-small">
        {cur ? <>On file: uploaded {ago(cur.uploaded_at)}{cur.source_date ? ` · ${pub} published ${cur.source_date}` : ''} · {Math.round((cur.size || 0) / 1048576)} MB{cur.via === 'feed' ? ' · sent by the nightly job' : ''}.</>
          : blocked ? <strong>No copy on file — {st.missing || 'this source'} {st.missing ? 'are' : 'is'} missing until one is uploaded.</strong>
          : 'No uploaded copy on file.'}
      </p>
      <ol className="pc-small" style={{ margin: '0 0 10px', paddingLeft: 18 }}>
        <li>Download <strong>{st.file_name}</strong> from <a className="evo-link" href={st.download_page} target="_blank" rel="noreferrer">{pub} data downloads ↗</a> (free, no login).</li>
        <li>Upload it here exactly as downloaded — do not unzip it.</li>
      </ol>
      {st.can_upload ? (
        <form onSubmit={send} className="pc-actions">
          <input type="file" accept=".zip,application/zip" aria-label={`${st.label} zip file`}
                 onChange={(e) => setFile(e.target.files && e.target.files[0])} />
          <button type="submit" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy || !file}>
            {busy ? 'Uploading… (about a minute)' : 'Upload file'}</button>
        </form>
      ) : <p className="pc-sub" style={{ margin: 0 }}>Only a platform admin can upload this file.</p>}
    </section>
  )
}

/** Compact provider table; becomes a card list on narrow screens. */
function ProviderTable({ rows, onOpen, empty, caption }) {
  if (!rows.length) return <div className="pc-empty">{empty}</div>
  return (
    <div className="pc-table-wrap">
      <table className="pc-table">
        <caption className="evo-sr">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Provider</th><th scope="col">Kind</th><th scope="col">Configuration</th>
            <th scope="col">Switched on</th><th scope="col">Latest result</th><th scope="col">Cost</th>
            <th scope="col"><span className="evo-sr">Details</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const g = M.groupOf(p)
            const conf = M.configOf(p)
            const en = M.enabledOf(p)
            return (
              <tr key={p.key} className={g === 'attention' ? 'is-attention' : undefined}>
                <th scope="row" data-label="Provider">
                  <button type="button" className="pc-rowbtn" onClick={() => onOpen(p.key)}>
                    <span className="pc-strong">{M.shortLabel(p.label)}</span>
                    <span className="pc-sub">{M.scopeOf(p).label} · {(p.capabilities || []).map(M.capName).join(', ')}</span>
                  </button>
                </th>
                <td data-label="Kind"><KindTag p={p} compact /></td>
                <td data-label="Configuration"><Pill tone={conf.tone} title={conf.label}>{conf.short || conf.label}</Pill></td>
                <td data-label="Switched on"><Pill tone={en.tone} title={en.label}>{en.short || en.label}</Pill></td>
                <td data-label="Latest result"><Latest p={p} compact /></td>
                <td data-label="Cost" className="pc-small">{p.cost}</td>
                <td className="pc-right"><button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => onOpen(p.key)}
                                                 aria-label={`Details for ${p.label}`}>Details</button></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** The product capability matrix (/capabilities), compact. */
function CapabilityRegistry({ data, error, onOpen }) {
  if (error) return <Alert>{error}</Alert>
  if (!data) return <div className="pc-loading" aria-busy="true">Loading the capability registry…</div>
  return (
    <div className="pc-table-wrap">
      <table className="pc-table">
        <caption className="evo-sr">Capability registry</caption>
        <thead><tr><th scope="col">Capability</th><th scope="col">State</th><th scope="col">Providers</th><th scope="col"><span className="evo-sr">Details</span></th></tr></thead>
        <tbody>
          {data.capabilities.map((c) => {
            const real = c.providers.filter((p) => p.connector_kind !== 'sandbox')
            return (
              <tr key={c.capability} className={['DEGRADED', 'BLOCKED'].includes(c.state) ? 'is-attention' : undefined}>
                <th scope="row" data-label="Capability">
                  <button type="button" className="pc-rowbtn" onClick={() => onOpen(c.capability)}>
                    <span className="pc-strong">{c.label}</span>
                    {c.note ? <span className="pc-sub pc-clamp">{c.note}</span> : null}
                  </button>
                </th>
                <td data-label="State"><Pill tone={CAP_TONE[c.state] || 'quiet'}>{humanize(c.state.toLowerCase())}</Pill></td>
                <td data-label="Providers">
                  <span className="pc-chips">
                    {real.length ? real.slice(0, 4).map((p) => (
                      <span key={p.provider} className={`pc-chip is-${CAP_TONE[p.state] || 'quiet'}`} title={`${p.label}: ${p.state}`}>
                        {M.shortLabel(p.label)}<span className="evo-sr">: {p.state}</span></span>
                    )) : <span className="pc-muted">No provider can supply this yet</span>}
                    {real.length > 4 ? <span className="pc-chip">+{real.length - 4}</span> : null}
                  </span>
                </td>
                <td className="pc-right"><button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => onOpen(c.capability)}
                                                 aria-label={`Details for ${c.label}`}>Details</button></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RegistryCapDrawer({ c, onClose, onOpenProvider, providersByKey }) {
  if (!c) return null
  const yn = (v) => (v === null || v === undefined ? 'unknown' : v ? 'yes' : 'no')
  return (
    <Drawer open title={c.label} sub={c.note} onClose={onClose} wide>
      <div className="pc-drawer">
        <div className="pc-drawer__status"><Pill tone={CAP_TONE[c.state] || 'quiet'}>{humanize(c.state.toLowerCase())}</Pill></div>
        <ul className="pc-srclist">
          {c.providers.map((p) => (
            <li key={p.provider}>
              <button type="button" className="pc-srclist__btn" disabled={!providersByKey[p.provider]}
                      onClick={() => onOpenProvider(p.provider)}>
                <span className="pc-srclist__name">{p.label}</span>
                <span className="pc-srclist__meta"><Pill tone={CAP_TONE[p.state] || 'quiet'}>{humanize(p.state.toLowerCase())}</Pill>
                  {p.synthetic ? <span className="pc-sub">synthetic</span> : null}</span>
                <span className="pc-sub">{p.why}</span>
                {p.state === 'MANUAL ONLY' ? null : (
                  <span className="pc-sub">platform: configured {yn(p.platform.configured)}{p.platform.blocked ? ', blocked' : ''} ·
                    workspace: on {yn(p.tenant.enabled)}, reachable {yn(p.tenant.reachable)}, healthy {yn(p.tenant.healthy)}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Drawer>
  )
}

export default function EvoControls() {
  // Providers, verification, scoring, budgets and RESUMING are workspace-admin
  // actions on the server (evosense_router._admin / _is_admin_here). Anyone
  // may pause. Controls the person cannot use are disabled, not offered.
  const isAdmin = isWholesaleAdmin()
  const initial = useMemo(readUrl, [])
  const [tab, setTabState] = useState(initial.tab)
  const [filters, setFiltersState] = useState(initial.filters)
  const [open, setOpenState] = useState(initial.open)
  const [ctl, setCtl] = useState(null)
  const [prov, setProv] = useState(null)
  const [reg, setReg] = useState(null)
  const [regErr, setRegErr] = useState(null)
  const [capReg, setCapReg] = useState(null)
  const [capErr, setCapErr] = useState(null)
  const [spent, setSpent] = useState(null)
  const [weights, setWeights] = useState({})
  const [budget, setBudget] = useState({ day: '', month: '', cap: '' })
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loadedAt, setLoadedAt] = useState(null)

  const setTab = (t) => { setTabState(t); writeUrl(t, filters, open) }
  const setFilters = (f) => { const clean = Object.fromEntries(Object.entries(f).filter(([, v]) => v)); setFiltersState(clean); writeUrl(tab, clean, open) }
  const setOpen = (o) => { setOpenState(o); writeUrl(tab, filters, o) }
  const goProviders = (f) => { const clean = Object.fromEntries(Object.entries(f).filter(([, v]) => v)); setFiltersState(clean); setTabState('providers'); writeUrl('providers', clean, open) }

  const load = useCallback(async () => {
    try {
      const [c, p, cc, r, caps] = await Promise.all([
        api.get('/wholesale/evosense/controls'), api.get('/wholesale/evosense/providers'),
        api.get('/wholesale/evosense/command-center').catch(() => null),
        api.get('/wholesale/evosense/sources').then((x) => { setRegErr(null); return x }).catch((e) => { setRegErr(errText(e)); return null }),
        api.get('/wholesale/evosense/capabilities').then((x) => { setCapErr(null); return x }).catch((e) => { setCapErr(errText(e)); return null })])
      setCtl(c); setProv(p); setSpent(cc ? cc.spent : null); setReg(r); setCapReg(caps)
      setWeights(Object.fromEntries(Object.entries({ ...(c.default_weights || {}), ...(c.score_weights || {}) })
        .map(([k, v]) => [k, String(v)])))
      setBudget({ day: c.org_daily_budget_cents == null ? '' : String(c.org_daily_budget_cents / 100),
                  month: c.org_monthly_budget_cents == null ? '' : String(c.org_monthly_budget_cents / 100),
                  cap: String(c.owner_touch_cap_days) })
      setLoadedAt(new Date().toISOString())
      setError(null)
    } catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])
  // A link to #usage / #providers on this same page (or Back/Forward) moves the tab too.
  useEffect(() => {
    const onHash = () => setTabState(readUrl().tab)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  async function patch(body, msg) {
    setBusy(true); setError(null); setNotice(null)
    try { setCtl(await api.patch('/wholesale/evosense/controls', body)); if (msg) setNotice(msg) }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function setProvider(p, enabled) {
    setBusy(true); setError(null); setNotice(null)
    try {
      setProv(await api.patch('/wholesale/evosense/providers', { key: p.key, enabled }))
      api.get('/wholesale/evosense/sources').then(setReg).catch(() => null)
      api.get('/wholesale/evosense/capabilities').then(setCapReg).catch(() => null)
      setNotice(`${M.shortLabel(p.label)} switched ${enabled ? 'on' : 'off'}.`)
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  // A paid provider is only switched on from the drawer's explicit approval step.
  function toggle(p, enabled) { setProvider(p, enabled) }
  async function verify(key) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post('/wholesale/evosense/sources/verify', { key })
      if (r.registry) setReg(r.registry)
      setProv(await api.get('/wholesale/evosense/providers'))
      if (r.ok) setNotice(`${key}: the live source answered — verified just now.`)
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

  const merged = useMemo(() => (prov ? M.mergeSources(prov.providers, reg) : []), [prov, reg])
  const byKey = useMemo(() => Object.fromEntries(merged.map((p) => [p.key, p])), [merged])
  const counties = useMemo(() => M.countyUniverse(merged), [merged])
  const allCaps = useMemo(() => [...new Set(merged.flatMap((p) => p.capabilities || []))], [merged])

  if (!ctl || !prov) return <EvoApp world="acquisition">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>

  const att = M.attentionList(merged)
  const paid = M.paidExposure(merged, ctl)
  const pausedCount = SWITCHES.filter(([k]) => ctl[k]).length
  const liveCount = (prov.real_connectors || []).length
  const unavailable = prov.real_connectors_unavailable || []
  const groupCounts = merged.reduce((a, p) => { const g = M.groupOf(p); a[g] = (a[g] || 0) + 1; return a }, {})
  const engineCaps = prov.capabilities || []

  const cardMatches = (c) => {
    const f = filters
    if (f.q) {
      const t = [M.capName(c.capability), M.CAP_PURPOSE[c.capability], c.note,
                 ...merged.filter((p) => (p.capabilities || []).includes(c.capability)).map((p) => p.label)].join(' ').toLowerCase()
      if (!t.includes(f.q.trim().toLowerCase())) return false
    }
    if (f.county) {
      const row = M.coverageFor(c.capability, merged, [f.county]).rows[0]
      if (!row || row.cell === 'none') return false
    }
    if (f.status && M.capabilityStatus(c, merged).key !== f.status) return false
    return true
  }
  const cards = engineCaps.filter(cardMatches)
  const paidVendors = merged.filter((p) => M.isPaid(p))
  const sourceRows = M.sortProviders(merged.filter((p) => p.connector_kind !== 'sandbox' && !M.isPaid(p) && (p.source || M.isManual(p))
    && M.matchesProvider(p, { q: filters.q, county: filters.county })))
  const providerRows = M.sortProviders(merged.filter((p) => M.matchesProvider(p, filters)))

  // Drawer target from the URL: p:<provider key> | c:<engine capability> | r:<registry capability>
  const [openKind, openKey] = open ? [open.slice(0, 1), open.slice(2)] : [null, null]
  const openProvider = openKind === 'p' ? byKey[openKey] : null
  const openCap = openKind === 'c' ? engineCaps.find((c) => c.capability === openKey) : null
  const openRegCap = openKind === 'r' && capReg ? capReg.capabilities.find((c) => c.capability === openKey) : null

  const next = att.current[0]
  const ch = paid.channel
  const clearLink = <button type="button" className="pc-linkbtn" onClick={() => setFilters({})}>Clear filters</button>

  return (
    <EvoApp world="acquisition">
      <header className="pc-banner">
        <div className="pc-banner__text">
          <p className="pc-banner__eyebrow">{ev('EvoSense · System intelligence')}</p>
          <h1 className="pc-banner__title">Providers &amp; Controls</h1>
          <p className="pc-banner__sub">Where your data comes from, what is working, and what can cost money.</p>
        </div>
        <div className="pc-banner__side">
          <Pill tone={ctl.paused_all ? 'attention' : 'good'}>{ctl.paused_all ? 'Engine paused' : 'Engine running'}</Pill>
          {pausedCount && !ctl.paused_all ? <Pill tone="attention">{pausedCount} switch{pausedCount === 1 ? '' : 'es'} paused</Pill> : null}
          <button type="button" className="pc-banner__refresh" onClick={() => { setNotice(null); load() }} disabled={busy}>Refresh</button>
          {loadedAt ? <span className="pc-banner__time">Updated {ago(loadedAt)}</span> : null}
        </div>
      </header>

      <div className="pc-stats" role="group" aria-label="Summary">
        <Stat label="Operational sources" value={liveCount}
              sub={liveCount ? `Live connectors answering · ${unavailable.length ? `${unavailable.length} on but unavailable` : 'none unavailable'}` : 'No live connector is operational'}
              tone={liveCount ? 'good' : 'warn'} onClick={() => goProviders({ status: 'live' })}
              ariaLabel={`${liveCount} operational sources. Show them.`} />
        <Stat label="Registered providers" value={merged.length}
              sub={`${groupCounts.live || 0} live · ${groupCounts.manual || 0} manual · ${(groupCounts.unconfigured || 0)} not set up · ${groupCounts.sandbox || 0} sandbox`}
              onClick={() => goProviders({})} ariaLabel={`${merged.length} registered providers. Show all.`} />
        <Stat label="Needs attention" value={att.current.length}
              sub={att.current.length ? att.current.slice(0, 2).map((p) => M.shortLabel(p.label)).join(', ') + (att.current.length > 2 ? '…' : '')
                : att.recovered.length ? `Nothing failing now · ${att.recovered.length} recovered this week` : 'Nothing failing now'}
              tone={att.current.length ? 'danger' : (att.recovered.length ? 'warn' : 'good')}
              onClick={() => goProviders({ status: 'attention' })}
              ariaLabel={`${att.current.length} providers need attention. Show them.`} />
        <Stat label="Paid lookups" value={paid.paused ? 'Paused' : paid.usable.length ? `On · ${paid.usable.length}` : 'Off'}
              sub={`${paid.on.length} of ${paid.paid.length} paid providers switched on${spent ? ` · ${cents(spent.month_cents)} spent this month` : ''}`}
              tone={paid.usable.length && !paid.paused ? 'warn' : 'good'}
              onClick={() => setTab('usage')} ariaLabel="Paid lookups. Open usage and costs." />
      </div>

      <div className={`pc-next${next ? ' is-danger' : att.recovered.length ? ' is-warn' : ''}`} role="status">
        {next ? (
          <>
            <span><b>Fix first:</b> {M.shortLabel(next.label)} — {M.latestOf(next).detail || next.why}</span>
            <button type="button" className="evo-btn evo-btn--sm evo-btn--primary" onClick={() => setOpen('p:' + next.key)}>Open {M.shortLabel(next.label)}</button>
          </>
        ) : att.recovered.length ? (
          <>
            <span><b>Recovered this week:</b> {att.recovered.map((p) => `${M.shortLabel(p.label)} (${M.latestOf(p).earlier.detail})`).join('; ')}. The latest attempt succeeded.</span>
            <button type="button" className="evo-btn evo-btn--sm evo-btn--ghost" onClick={() => setOpen('p:' + att.recovered[0].key)}>Review</button>
          </>
        ) : (
          <span>{liveCount ? 'Everything switched on is answering. ' : 'No live connector is on yet. '}
            {ch ? <>Paid data: {humanize(ch.state.toLowerCase())}.</> : null}</span>
        )}
      </div>

      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <TabBar label="Providers and controls" value={tab} onChange={setTab}
              items={[
                { key: 'sources', label: 'Source Registry' },
                { key: 'providers', label: 'Service Providers', count: merged.length },
                { key: 'evaluation', label: 'Provider Evaluation' },
                { key: 'skipcost', label: 'Skip-trace Costs' },
                { key: 'lists', label: 'Distress Lists' },
                { key: 'capabilities', label: 'Capability Registry' },
                { key: 'controls', label: 'Controls & Compliance', count: pausedCount ? `${pausedCount} paused` : null },
                { key: 'usage', label: 'Usage & Costs' },
              ]} />

      {/* ── SOURCE REGISTRY ─────────────────────────────────────────────── */}
      <div className="evo-stack" role="tabpanel" id="panel-sources" aria-labelledby="tab-sources" hidden={tab !== 'sources'}>
        <div className="pc-head">
          <div>
            <h2 className="pc-h2">Data capabilities</h2>
            <p className="pc-muted pc-small">What you can actually use, county by county. Select a capability for its coverage matrix and evidence.</p>
          </div>
        </div>
        <FilterBar value={filters} onChange={setFilters} counties={counties} capabilities={allCaps} showCap={false}
                   statusOptions={CAP_STATUS_OPTIONS} count={`${cards.length} of ${engineCaps.length}`} placeholder="Search capabilities and sources…" />
        {cards.length ? (
          <div className="pc-cards">
            {cards.map((c) => <CapabilityCard key={c.capability} cap={c} providers={merged} counties={counties}
                                              selected={openKind === 'c' && openKey === c.capability} onOpen={(k) => setOpen('c:' + k)} />)}
          </div>
        ) : <div className="pc-empty">No capability matches these filters. {clearLink}</div>}

        <div className="pc-head" style={{ marginTop: 8 }}>
          <div>
            <h2 className="pc-h2">Sources</h2>
            <p className="pc-muted pc-small">Free public-record connectors and manual feeds. Retrieval succeeding is not the same as every derived field being true.</p>
          </div>
          <span className="pc-filters__count">{sourceRows.length} shown</span>
        </div>
        {regErr ? <Alert>Couldn&apos;t load the source registry: {regErr}</Alert> : null}
        {!reg && !regErr ? <div className="pc-loading" aria-busy="true">Loading sources…</div>
          : <ProviderTable rows={sourceRows} onOpen={(k) => setOpen('p:' + k)} caption="Sources"
                           empty={<>No source matches these filters. {clearLink}</>} />}
        {paidVendors.length ? (
          <p className="pc-note">{paidVendors.length} paid vendors are registered ({paidVendors.filter((p) => p.enabled).length} switched on).{' '}
            <button type="button" className="pc-linkbtn" onClick={() => goProviders({ q: '' , status: '' })}>See them in Service Providers</button> or{' '}
            <button type="button" className="pc-linkbtn" onClick={() => setTab('usage')}>their prices in Usage &amp; Costs</button>.</p>
        ) : null}
        <p className="pc-sub">Public-record sources are read politely: no logins, no CAPTCHAs, byte-range reads of published files, one request at a time.
          Every record keeps its raw evidence, the adapter version and the source&apos;s own date.</p>
      </div>

      {/* ── SERVICE PROVIDERS ───────────────────────────────────────────── */}
      <div className="evo-stack" role="tabpanel" id="panel-providers" aria-labelledby="tab-providers" hidden={tab !== 'providers'}>
        <div className="pc-head">
          <div>
            <h2 className="pc-h2">Provider registry</h2>
            <p className="pc-muted pc-small">Every registered provider. Kind, configuration, switch, latest result and cost are separate columns — one never stands in for another.</p>
          </div>
        </div>
        <FilterBar value={filters} onChange={setFilters} counties={counties} capabilities={allCaps}
                   count={`${providerRows.length} of ${merged.length}`} />
        <div className="pc-legend" aria-label="Status groups">
          {M.GROUPS.filter(([k]) => groupCounts[k]).map(([k, l]) => (
            <button key={k} type="button" className={`pc-legend__item${filters.status === k ? ' is-on' : ''}`}
                    aria-pressed={filters.status === k} onClick={() => setFilters({ ...filters, status: filters.status === k ? '' : k })}>
              {l} <b>{groupCounts[k]}</b></button>
          ))}
        </div>
        <ProviderTable rows={providerRows} onOpen={(k) => setOpen('p:' + k)} caption="Service providers"
                       empty={<>No provider matches these filters. {clearLink}</>} />
        <p className="pc-sub">Sandbox providers are off for every organization until an admin turns them on, and they never serve a real property.
          Paid providers need an explicit approval to switch on and are never test-called from this screen. A failed paid lookup is refunded.</p>
      </div>

      <div className="evo-stack" role="tabpanel" id="panel-evaluation" aria-labelledby="tab-evaluation" hidden={tab !== 'evaluation'}>
        {tab === 'evaluation' ? <EvoEvaluation /> : null}
      </div>
      <div className="evo-stack" role="tabpanel" id="panel-skipcost" aria-labelledby="tab-skipcost" hidden={tab !== 'skipcost'}>
        {tab === 'skipcost' ? <SkipTraceCostPanel /> : null}
      </div>
      <div className="evo-stack" role="tabpanel" id="panel-lists" aria-labelledby="tab-lists" hidden={tab !== 'lists'}>
        {tab === 'lists' ? <EvoLists /> : null}
      </div>

      {/* ── CAPABILITY REGISTRY ─────────────────────────────────────────── */}
      <div className="evo-stack" role="tabpanel" id="panel-capabilities" aria-labelledby="tab-capabilities" hidden={tab !== 'capabilities'}>
        <div className="pc-head">
          <div>
            <h2 className="pc-h2">Capability registry</h2>
            <p className="pc-muted pc-small">Per product capability, per provider. Unverified means configured and on but never proven; sandbox is synthetic.</p>
          </div>
          {capReg ? <span className="pc-filters__count">{capReg.capabilities.filter((x) => x.state === 'OPERATIONAL').length} of {capReg.capabilities.length} operational</span> : null}
        </div>
        <CapabilityRegistry data={capReg} error={capErr} onOpen={(k) => setOpen('r:' + k)} />
      </div>

      {/* ── CONTROLS & COMPLIANCE ───────────────────────────────────────── */}
      <div className="evo-stack" role="tabpanel" id="panel-controls" aria-labelledby="tab-controls" hidden={tab !== 'controls'}>
        <Panel title={ev('EvoSense controls')} hint="Anyone can pause. Only an admin can resume.">
          {!isAdmin ? <p className="pc-sub" style={{ margin: '0 0 12px' }}>You can pause any switch. Resuming, provider changes, scoring weights and budgets need an administrator of this workspace.</p> : null}
          <div className="evo-switches">
            {SWITCHES.map(([k, label, help]) => {
              const paused = !!ctl[k]
              const chn = (ctl.channels || {})[k]
              return (
                <div key={k} className={`evo-switch${paused ? ' is-paused' : ''}`}>
                  <div className="evo-switch__top">
                    <span className="evo-switch__name">{label}</span>
                    <button type="button" role="switch" className="evo-toggle" aria-checked={!paused} disabled={busy || (paused && !isAdmin)}
                            aria-label={`${label}: ${paused ? 'paused — resume' : 'running — pause'}`}
                            onClick={() => patch({ [k]: !paused })} />
                  </div>
                  <span className="pc-chips">
                    <Pill tone={paused ? 'attention' : 'good'}>Switch {paused ? 'paused' : 'running'}</Pill>
                    {chn ? <Pill tone={chn.state === 'OPERATIONAL' ? 'good' : chn.state === 'PAUSED' ? 'attention' : 'quiet'}
                                 title={chn.why}>{humanize(chn.state.toLowerCase())}</Pill> : null}
                  </span>
                  <p className="evo-switch__desc">{chn && chn.why ? chn.why : help}</p>
                </div>
              )
            })}
          </div>
        </Panel>

        {isAdmin ? <RollFixPanel /> : null}

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
          <div style={{ marginTop: 12 }} className="pc-actions">
            <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !isAdmin} onClick={saveWeights}>Save weights</button>
            <button type="button" className="evo-btn evo-btn--ghost" disabled={busy || !isAdmin}
                    onClick={() => patch({ score_weights: null }, 'Weights reset to the defaults.')}>Reset to defaults</button>
          </div>
          <p className="pc-sub" style={{ margin: '10px 0 0' }}>Points each current signal adds (−30 to 30). Aging evidence counts half;
            stale evidence counts nothing. A changed weight changes THIS workspace&apos;s score version only, so older scores stay explainable. AI never sets a score.
            {(ctl.never_scored || []).length ? <> {ctl.never_scored.map((k) => humanize(k.toLowerCase())).join(' and ')} are shown as evidence but never scored, whatever the weight.</> : null}</p>
          {ctl.scoring_options ? (
            <label className="evo-field" style={{ marginTop: 12, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" checked={!!ctl.scoring_options.exclude_institutional} disabled={busy || !isAdmin}
                     onChange={(e) => patch({ scoring_options: { ...ctl.scoring_options, exclude_institutional: e.target.checked } },
                       'Scoring option saved. Properties re-score on their next evaluation.')} />
              <span>Exclude institutional owners (government, religious organizations, nonprofits / associations) from scoring
                <span className="pc-muted"> · platform default: on</span></span>
            </label>
          ) : null}
        </Panel>

        <Panel title="Compliance guarantees" hint="enforced by the server, not by this screen">
          <ul className="evo-checklist">
            <li>An opted-out or suppressed owner is never contacted again — by any strategy, ever.</li>
            <li>Owners are contacted at most once every {ctl.owner_touch_cap_days} days, across every strategy.</li>
            <li>{ev('EvoSense never makes an offer, signs anything or moves money. It hands the conversation to you.')}</li>
            <li>Scores, budgets and do-not-contact decisions are deterministic rules; AI only reads replies.</li>
            <li>A phone found in public records or by skip trace is never permission to text. Only a consent record under the Wholesale seller SMS program is.</li>
            <li>Provider credentials stay on the server and are never sent to a browser.</li>
          </ul>
        </Panel>
      </div>

      {/* ── USAGE & COSTS ───────────────────────────────────────────────── */}
      <div className="evo-stack" role="tabpanel" id="panel-usage" aria-labelledby="tab-usage" hidden={tab !== 'usage'}>
        <Metrics label="Usage summary">
          <Metric label="Spent today" value={spent ? cents(spent.today_cents) : null} tone="primary" />
          <Metric label="Spent this month" value={spent ? cents(spent.month_cents) : null} tone="primary" />
          <Metric label="All time" value={spent ? cents(spent.all_time_cents) : null} />
          <Metric label="Per contact found" value={spent && spent.cost_per_contact_found_cents != null ? cents(spent.cost_per_contact_found_cents) : null} tone="success" />
          <Metric label="Failing now" value={att.current.length} tone={att.current.length ? 'danger' : 'success'} />
        </Metrics>
        {!spent ? <p className="pc-sub">Spend figures could not be loaded; budgets below still apply on the server.</p> : null}

        <Panel title="Paid providers" flush hint={ch ? `Paid data: ${humanize(ch.state.toLowerCase())}` : null}>
          <div className="pc-table-wrap pc-table-wrap--flush">
            <table className="pc-table">
              <caption className="evo-sr">Paid providers</caption>
              <thead><tr><th scope="col">Provider</th><th scope="col">Configuration</th><th scope="col">Switched on</th>
                <th scope="col">Price per call</th><th scope="col">Calls</th></tr></thead>
              <tbody>
                {M.sortProviders(paid.paid).map((p) => (
                  <tr key={p.key}>
                    <th scope="row" data-label="Provider"><button type="button" className="pc-rowbtn" onClick={() => setOpen('p:' + p.key)}>
                      <span className="pc-strong">{M.shortLabel(p.label)}</span><span className="pc-sub">{(p.capabilities || []).map(M.capName).join(', ')}</span></button></th>
                    <td data-label="Configuration"><Pill tone={M.configOf(p).tone}>{M.configOf(p).label}</Pill></td>
                    <td data-label="Switched on"><Pill tone={M.enabledOf(p).tone}>{M.enabledOf(p).label}</Pill></td>
                    <td data-label="Price per call" className="pc-small">{Object.keys(p.costs || {}).length
                      ? Object.entries(p.costs).map(([k, v]) => `${M.capName(k)} ${cents(v)}`).join(' · ') : 'not priced (not purchased)'}</td>
                    <td data-label="Calls" className="pc-small">{p.calls_total ? `${p.successes_total}/${p.calls_total} succeeded` : 'No calls yet'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

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
            <div><button type="submit" className="evo-btn evo-btn--primary" disabled={busy || !isAdmin}>Save budget</button></div>
          </form>
          {spent && spent.note ? <p className="pc-sub" style={{ margin: '10px 0 0' }}>{spent.note}</p> : null}
        </Panel>
      </div>

      {/* ── DRAWERS ─────────────────────────────────────────────────────── */}
      {openProvider ? (
        <ProviderDrawer p={openProvider} isAdmin={isAdmin} busy={busy} onClose={() => setOpen(null)} controls={ctl}
                        onToggle={toggle} onVerify={verify}
                        extra={['tad', 'johnson_cad'].includes(openProvider.key) ? <SourceUpload sourceKey={openProvider.key} onDone={(msg) => { setNotice(msg); load() }} onError={setError} /> : null} />
      ) : null}
      {openCap ? (
        <CapabilityDrawer cap={openCap} providers={merged} counties={counties} onClose={() => setOpen(null)}
                          onOpenProvider={(k) => setOpen('p:' + k)} />
      ) : null}
      {openRegCap ? (
        <RegistryCapDrawer c={openRegCap} providersByKey={byKey} onClose={() => setOpen(null)} onOpenProvider={(k) => setOpen('p:' + k)} />
      ) : null}
    </EvoApp>
  )
}
