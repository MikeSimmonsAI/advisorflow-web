/* EVOSENSE — STRATEGIES + STRATEGY BUILDER (Phase 7.3 light rebuild; board screen 3).
 *
 * A STRATEGY IS A LIVE ACQUISITION MACHINE. Each card says what it hunts,
 * where, how often, what it may spend, when it brings you in - and what it
 * has produced. The one primary action is OPEN STRATEGY; pause, clone and
 * archive are quieter.
 *
 * THE BUILDER is a guided business conversation ("Tell EvoSense what to
 * hunt"), eight questions, and ends with YOUR STRATEGY - the plain-English
 * readback the SERVER writes from the actual settings. The server validates;
 * this screen never decides what a valid strategy is. Strategies are never
 * deleted: archive keeps attribution for everything a strategy ever found.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import {
  Alert, Drawer, Empty, EvoApp, Hero, PageSkeleton, Panel, Status, TabBar, Tag, ago, cents, humanize, moneyK, when,
} from '../ds/ds'
import { Scene } from '../ds/scenes'
import '../ds/evo-pages.css'

// Card headers: generic stock photography, never a photo of a property this
// strategy found.
const STRAT_SCENES = ['card1', 'card2', 'card3', 'card4', 'card5', 'card6']

const CADENCE = { daily: 'Daily', interval: 'Every few hours', manual: 'Manual only' }

function cadenceLabel(a) {
  if (!a) return '—'
  if (a.cadence === 'interval' && a.interval_hours) return `Every ${a.interval_hours} hours`
  return CADENCE[a.cadence] || humanize(a.cadence)
}

function nextHunt(a) {
  if (!a) return '—'
  if (a.state === 'running') return 'Hunting now'
  if (a.state === 'manual') return 'Manual only'
  if (a.state === 'paused') return 'Paused (EvoSense or discovery)'
  if (a.state === 'strategy_paused') return 'Strategy paused'
  return a.next_due_at ? when(a.next_due_at) : '—'
}

export function EvoStrategies() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('all')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirmArchive, setConfirmArchive] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    // Archived strategies are fetched too, so every tab count is real; the
    // "All" tab shows what is not archived, exactly as before.
    try { setData(await api.get('/wholesale/evosense/strategies?include_archived=true')); setError(null) }
    catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function act(s, action) {
    setBusy(true); setError(null)
    try {
      if (action === 'clone') {
        const c = await api.post(`/wholesale/evosense/strategies/${s.id}/clone`, {})
        navigate(`/wholesale/evosense/strategies/${c.id}`); return
      }
      await api.post(`/wholesale/evosense/strategies/${s.id}/${action}`, {})
      setConfirmArchive(null)
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  async function hunt(s) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post(`/wholesale/evosense/strategies/${s.id}/hunt?background=true`, {})
      setNotice(`${s.name}: hunt started on the server. Results appear in the Discovery Inbox as they are ingested.`)
      if (r) await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const all = data ? data.items : []
  const shown = all.filter((x) => (tab === 'all' ? x.status !== 'archived' : tab === 'active' ? x.status === 'active'
    : tab === 'paused' ? (x.status === 'paused' || x.status === 'draft') : x.status === 'archived'))
  const signalLabel = Object.fromEntries((data?.signals || []).map((x) => [x.key, x.label]))

  return (
    <EvoApp world="acquisition">
      <Hero
        scene="bridge"
        eyebrow="Acquisition Strategy"
        title="EvoSense Strategies"
        sub="Tell EvoSense where to hunt and what opportunity looks like."
        quote="Right property. Right people. Right result."
      />
      <TabBar label="Strategy status" value={tab} onChange={setTab} controls={false}
              items={[
                { key: 'all', label: 'All Strategies', count: data ? all.filter((x) => x.status !== 'archived').length : null },
                { key: 'active', label: 'Active', count: data ? all.filter((x) => x.status === 'active').length : null },
                { key: 'paused', label: 'Paused & drafts', count: data ? all.filter((x) => x.status === 'paused' || x.status === 'draft').length : null },
                { key: 'archived', label: 'Archived', count: data ? all.filter((x) => x.status === 'archived').length : null },
              ]}
              actions={<button type="button" className="evo-btn evo-btn--primary" onClick={() => navigate('/wholesale/evosense/strategies/new')}>
                + New Strategy</button>} />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      {!data ? <PageSkeleton /> : !shown.length && all.length ? (
        <Panel><Empty title="No strategies here">Nothing in this view. The other tabs hold the rest.</Empty></Panel>
      ) : !shown.length ? (
        <Panel>
          <Empty page title="No strategies yet" action={
            <button type="button" className="evo-btn evo-btn--primary" onClick={() => navigate('/wholesale/evosense/strategies/new')}>Tell EvoSense what to hunt</button>}>
            A strategy tells EvoSense where to look, what makes a property interesting, how much it may spend finding
            the owner, and when to bring you in.
          </Empty>
        </Panel>
      ) : (
        <div className="evo-strats">
          {shown.map((s, idx) => {
            const a = s.automation || {}
            const m = s.metrics || {}
            const needs = (m.by_status || {}).needs_you || 0
            const live = s.status === 'active' && ['scheduled', 'running'].includes(a.state)
            const place = [...(s.counties || []).map((c) => `${c} County`), ...(s.cities || []), ...(s.states || [])]
            return (
              <section key={s.id} className="evo-panel evo-strat" aria-labelledby={`st-${s.id}`}>
                <div className="evo-strat__art" aria-hidden="true">
                  <Scene name={STRAT_SCENES[idx % STRAT_SCENES.length]} id={`st-${s.id}`} />
                </div>
                <div className="evo-strat__top">
                  <div style={{ minWidth: 0 }}>
                    <span className="evo-strat__live">
                      <span className={`evo-strat__pulse${live ? ' is-on' : ''}`} aria-hidden="true" />
                      {live ? 'Hunting automatically' : humanize(a.state === 'manual' ? 'manual only' : s.status)}
                    </span>
                    <h2 className="evo-strat__name" id={`st-${s.id}`}>{s.name}</h2>
                    {s.description ? <p className="evo-strat__desc">{s.description}</p> : null}
                  </div>
                  <span className="evo-chips" style={{ justifyContent: 'flex-end', flexShrink: 0 }}>
                    <Status status={s.status} />
                    {s.is_test ? <Tag kind="sandbox">Test</Tag> : null}
                    {s.pilot_mode ? <Tag kind="info">Pilot · {s.pilot?.record_cap} max</Tag> : null}
                  </span>
                </div>

                <dl className="evo-strat__grid">
                  <div><dt>Market</dt><dd>{place.length ? place.slice(0, 3).join(' · ') : 'Anywhere'}</dd></div>
                  <div><dt>Property target</dt><dd>{(s.property_types || []).map(humanize).join(', ') || 'Any'}
                    {s.min_value || s.max_value ? ` · ${moneyK(s.min_value, '$0')}–${moneyK(s.max_value, 'any')}` : ''}</dd></div>
                  <div><dt>Priority signals</dt><dd>{(s.preferred_signals || []).map((k) => signalLabel[k] || humanize(k)).join(', ') || '—'}</dd></div>
                  <div><dt>Budget</dt><dd>{cents(s.daily_budget_cents)}/day{s.monthly_budget_cents ? ` · ${cents(s.monthly_budget_cents)}/mo` : ''}</dd></div>
                  <div><dt>Brings you in at</dt><dd>Seller intent {s.handoff_intent_threshold}</dd></div>
                  <div><dt>Automatic hunting</dt><dd>{cadenceLabel(a)}</dd></div>
                  <div><dt>Last hunt</dt><dd>{s.last_hunt_at ? ago(s.last_hunt_at) : 'Never'}
                    {a.last_status === 'failed' ? <span style={{ color: 'var(--evo-danger-ink)' }}> · failed — retrying</span> : null}</dd></div>
                  <div><dt>Next hunt</dt><dd>{nextHunt(a)}</dd></div>
                </dl>

                <div className="evo-strat__results" role="group" aria-label="Results">
                  <div><b>{m.discovered ?? 0}</b><span>Found</span></div>
                  <div className={needs ? 'is-attention' : ''}><b>{needs}</b><span>Needs you</span></div>
                  <div><b>{m.promoted ?? 0}</b><span>Promoted</span></div>
                  <div><b>{cents(m.spent_cents)}</b><span>Spent</span></div>
                </div>

                <div className="evo-strat__foot">
                  {s.status !== 'archived' ? (
                    <Link className="evo-btn evo-btn--primary evo-btn--sm" to={`/wholesale/evosense/strategies/${s.id}`}>Open strategy</Link>
                  ) : null}
                  {s.status === 'draft' ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy} onClick={() => act(s, 'activate')}>Activate</button> : null}
                  {s.status === 'active' ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy} onClick={() => hunt(s)}>Run hunt</button> : null}
                  {s.status === 'active' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => act(s, 'pause')}>Pause</button> : null}
                  {s.pilot_mode ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => act(s, 'pilot-archive')}>Roll back pilot</button> : null}
                  {s.pilot_mode ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => act(s, 'pilot-restore')}>Restore pilot</button> : null}
                  {s.status === 'paused' ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy} onClick={() => act(s, 'resume')}>Resume</button> : null}
                  <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => act(s, 'clone')}>Clone</button>
                  {s.status !== 'archived' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => setConfirmArchive(s)}>Archive</button> : null}
                </div>
              </section>
            )
          })}
        </div>
      )}
      <Drawer open={!!confirmArchive} onClose={() => setConfirmArchive(null)} title="Archive strategy"
              sub={confirmArchive ? confirmArchive.name : ''}
              footer={<>
                <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setConfirmArchive(null)}>Cancel</button>
                <button type="button" className="evo-btn evo-btn--danger" disabled={busy} onClick={() => act(confirmArchive, 'archive')}>Archive</button>
              </>}>
        <p style={{ marginTop: 0 }}>It stops hunting. Nothing is deleted: every property it found keeps its attribution,
          and it can be cloned into a new strategy at any time.</p>
      </Drawer>
    </EvoApp>
  )
}

const EMPTY = {
  name: '', description: '', states: [], counties: [], cities: [], zips: [], property_types: ['single_family'],
  min_value: '', max_value: '', occupancy_preferences: [], min_equity_pct: '', min_ownership_years: '',
  owner_geography: 'any', required_signals: [], preferred_signals: [], excluded_signals: [],
  min_opportunity_score: 60, min_contact_confidence: 60, handoff_intent_threshold: 70, target_fee: '',
  daily_budget: '0', monthly_budget: '', max_cost_per_property: '', approval_over: '',
  outreach_policy: { auto_outreach: false, channels: ['sms'], cold_outreach_compliance_confirmed: false },
  nurture_policy: { allow_nurture: true, default_days: 60 },
  hunt_cadence: 'daily', hunt_interval_hours: 12,
  pilot_mode: false, pilot_max_properties: 50, pilot_allow_paid: false, pilot_max_spend: '',
}

const toList = (s) => String(s || '').split(/[,\n;]/).map((x) => x.trim()).filter(Boolean)
const dollars = (c) => (c === null || c === undefined ? '' : String(c / 100))
const toCents = (d) => (d === '' || d === null || d === undefined ? null : Math.round(Number(d) * 100))

function fromServer(s) {
  return {
    ...EMPTY, ...s,
    daily_budget: dollars(s.daily_budget_cents), monthly_budget: dollars(s.monthly_budget_cents),
    hunt_cadence: (s.automation && s.automation.cadence) || 'daily',
    hunt_interval_hours: (s.automation && s.automation.interval_hours) || 12,
    max_cost_per_property: dollars(s.max_cost_per_property_cents), approval_over: dollars(s.approval_over_cents),
    min_value: s.min_value ?? '', max_value: s.max_value ?? '', min_equity_pct: s.min_equity_pct ?? '',
    min_ownership_years: s.min_ownership_years ?? '', target_fee: s.target_fee ?? '',
    pilot_mode: !!s.pilot_mode, pilot_max_properties: s.pilot_max_properties ?? 50,
    pilot_allow_paid: !!s.pilot_allow_paid, pilot_max_spend: dollars(s.pilot_max_spend_cents),
  }
}

function toServer(f) {
  const out = {
    name: f.name, description: f.description, states: f.states, counties: f.counties, cities: f.cities,
    zips: f.zips, property_types: f.property_types, min_value: f.min_value, max_value: f.max_value,
    occupancy_preferences: f.occupancy_preferences, min_equity_pct: f.min_equity_pct,
    min_ownership_years: f.min_ownership_years, owner_geography: f.owner_geography,
    required_signals: f.required_signals, preferred_signals: f.preferred_signals,
    excluded_signals: f.excluded_signals, min_opportunity_score: f.min_opportunity_score,
    min_contact_confidence: f.min_contact_confidence, handoff_intent_threshold: f.handoff_intent_threshold,
    target_fee: f.target_fee, daily_budget_cents: toCents(f.daily_budget) || 0,
    monthly_budget_cents: toCents(f.monthly_budget), max_cost_per_property_cents: toCents(f.max_cost_per_property),
    approval_over_cents: toCents(f.approval_over), outreach_policy: f.outreach_policy, nurture_policy: f.nurture_policy,
    hunt_cadence: f.hunt_cadence || 'daily',
    hunt_interval_hours: f.hunt_cadence === 'interval' ? Number(f.hunt_interval_hours) || 12 : null,
    pilot_mode: !!f.pilot_mode, pilot_max_properties: f.pilot_mode ? Number(f.pilot_max_properties) || 50 : f.pilot_max_properties,
    pilot_allow_paid: !!f.pilot_allow_paid, pilot_max_spend_cents: toCents(f.pilot_max_spend),
  }
  if (out.pilot_mode) out.hunt_cadence = 'manual'
  Object.keys(out).forEach((k) => { if (out[k] === '') out[k] = null })
  return out
}

function ListInput({ id, label, value, onChange, hint }) {
  const [text, setText] = useState((value || []).join(', '))
  useEffect(() => { setText((value || []).join(', ')) }, [value])
  return (
    <label className="evo-field" htmlFor={id}>
      <span className="evo-field__label">{label}</span>
      <input id={id} className="evo-input" value={text} onChange={(e) => setText(e.target.value)}
             onBlur={() => onChange(toList(text))} placeholder={hint} />
    </label>
  )
}

function Picks({ options, value, onChange, label }) {
  const set = new Set(value || [])
  return (
    <div className="evo-picks" role="group" aria-label={label}>
      {options.map(([k, l]) => (
        <button key={k} type="button" aria-pressed={set.has(k)} className="evo-pick"
                onClick={() => { const n = new Set(set); if (n.has(k)) n.delete(k); else n.add(k); onChange([...n]) }}>
          {l}</button>
      ))}
    </div>
  )
}

function NumIn({ id, label, value, onChange, hint, prefix, suffix }) {
  return (
    <label className="evo-field" htmlFor={id}>
      <span className="evo-field__label">{label}</span>
      <span style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        {prefix ? <span className="evo-muted" style={{ position: 'absolute', left: 12 }}>{prefix}</span> : null}
        <input id={id} className="evo-input" inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)}
               placeholder={hint} style={{ paddingLeft: prefix ? 26 : 12, paddingRight: suffix ? 64 : 12 }} />
        {suffix ? <span className="evo-muted" style={{ position: 'absolute', right: 12, fontSize: 12.5 }}>{suffix}</span> : null}
      </span>
    </label>
  )
}

function Check({ checked, onChange, children }) {
  return (
    <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer', minHeight: 32, color: 'var(--evo-text-primary)' }}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 3, width: 16, height: 16 }} />
      <span>{children}</span>
    </label>
  )
}

function Step({ n, q, hint, children }) {
  return (
    <Panel as="section" className="evo-step" labelledBy={`step-${n}`}>
      <div className="evo-step__head">
        <span className="evo-step__n" aria-hidden="true">{n}</span>
        <div>
          <h2 className="evo-step__q" id={`step-${n}`}>{q}</h2>
          {hint ? <p className="evo-step__hint">{hint}</p> : null}
        </div>
      </div>
      {children}
    </Panel>
  )
}

export function EvoStrategyBuilder() {
  const { strategyId } = useParams()
  const navigate = useNavigate()
  const isNew = !strategyId || strategyId === 'new'
  const [form, setForm] = useState(EMPTY)
  const [meta, setMeta] = useState(null)
  const [status, setStatus] = useState('draft')
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(isNew)

  useEffect(() => {
    (async () => {
      try {
        const list = await api.get('/wholesale/evosense/strategies')
        setMeta(list)
        if (!isNew) {
          const s = await api.get(`/wholesale/evosense/strategies/${strategyId}`)
          setForm(fromServer(s)); setStatus(s.status)
        }
        setLoaded(true)
      } catch (e) { setError(errText(e)) }
    })()
  }, [isNew, strategyId])

  const payload = useMemo(() => toServer(form), [form])
  useEffect(() => {
    const t = setTimeout(async () => {
      try { setPreview(await api.post('/wholesale/evosense/strategies/preview', payload)) } catch (e) { /* preview is advisory */ }
    }, 250)
    return () => clearTimeout(t)
  }, [payload])

  function set(k, v) { setForm((f) => ({ ...f, [k]: v })) }
  function setPol(which, k, v) { setForm((f) => ({ ...f, [which]: { ...f[which], [k]: v } })) }

  async function save(activate) {
    setBusy(true); setError(null)
    try {
      const s = isNew ? await api.post('/wholesale/evosense/strategies', payload)
        : await api.patch(`/wholesale/evosense/strategies/${strategyId}`, payload)
      if (activate && s.status !== 'active') await api.post(`/wholesale/evosense/strategies/${s.id}/activate`, {})
      navigate('/wholesale/evosense/strategies')
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const signals = (meta?.signals || []).map((s) => [s.key, s.label])
  const types = (meta?.property_types || []).map((t) => [t, humanize(t)])
  const problems = preview ? preview.problems.concat(preview.activation_problems || []) : []

  if (!loaded) return <EvoApp world="acquisition">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>

  return (
    <EvoApp world="acquisition">
      <Link className="evo-crumb" to="/wholesale/evosense/strategies">← Strategies</Link>
      <Link className="evo-crumb" to="/wholesale/evosense/strategies">← All strategies</Link>
      <Hero compact scene="bridge" eyebrow="Strategy builder"
            title={isNew ? 'Tell EvoSense what to hunt' : (form.name || 'Edit strategy')}
            sub="Eight plain questions. EvoSense reads the whole strategy back to you before it hunts." />
      <Alert>{error}</Alert>
      <div className="evo-builder">
        <div className="evo-stack">
          <Step n={1} q="Where should EvoSense hunt?" hint="Counties, cities or ZIP codes — as broad or as narrow as your market.">
            <div className="evo-form-grid">
              <label className="evo-field is-full" htmlFor="sb-name"><span className="evo-field__label">Name this strategy</span>
                <input id="sb-name" className="evo-input" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="DFW Distressed SFR" /></label>
              <ListInput id="sb-states" label="States" value={form.states} onChange={(v) => set('states', v)} hint="TX" />
              <ListInput id="sb-counties" label="Counties" value={form.counties} onChange={(v) => set('counties', v)} hint="Dallas, Tarrant" />
              <ListInput id="sb-cities" label="Cities" value={form.cities} onChange={(v) => set('cities', v)} hint="optional" />
              <ListInput id="sb-zips" label="ZIP codes" value={form.zips} onChange={(v) => set('zips', v)} hint="optional" />
            </div>
          </Step>
          <Step n={2} q="What property are you looking for?">
            <Picks options={types} value={form.property_types} onChange={(v) => set('property_types', v)} label="Property types" />
            <div className="evo-form-grid" style={{ marginTop: 14 }}>
              <NumIn id="sb-minv" label="Value at least" prefix="$" value={form.min_value} onChange={(v) => set('min_value', v)} />
              <NumIn id="sb-maxv" label="Value at most" prefix="$" value={form.max_value} onChange={(v) => set('max_value', v)} />
              <NumIn id="sb-eq" label="Equity at least" suffix="%" value={form.min_equity_pct} onChange={(v) => set('min_equity_pct', v)} />
              <NumIn id="sb-yrs" label="Owned at least" suffix="years" value={form.min_ownership_years} onChange={(v) => set('min_ownership_years', v)} />
            </div>
            <p className="evo-field__label" style={{ margin: '16px 0 8px' }}>Occupancy</p>
            <Picks options={[['vacant', 'Vacant'], ['non_owner_occupied', 'Not owner-occupied'], ['tenant', 'Tenant'], ['owner_occupied', 'Owner-occupied']]}
                   value={form.occupancy_preferences} onChange={(v) => set('occupancy_preferences', v)} label="Occupancy" />
            <label className="evo-field" htmlFor="sb-geo" style={{ marginTop: 14, maxWidth: 320 }}><span className="evo-field__label">Owner lives</span>
              <select id="sb-geo" className="evo-select" value={form.owner_geography} onChange={(e) => set('owner_geography', e.target.value)}>
                <option value="any">Anywhere</option><option value="absentee">Somewhere else (absentee)</option>
                <option value="out_of_state">Out of state</option></select></label>
          </Step>
          <Step n={3} q="What signals matter most?" hint="Priority signals raise a property's opportunity; required signals must be present.">
            <p className="evo-field__label" style={{ margin: '0 0 8px' }}>Give extra priority to</p>
            <Picks options={signals} value={form.preferred_signals} onChange={(v) => set('preferred_signals', v)} label="Preferred signals" />
            <p className="evo-field__label" style={{ margin: '16px 0 8px' }}>Every property must show</p>
            <Picks options={signals} value={form.required_signals} onChange={(v) => set('required_signals', v)} label="Required signals" />
          </Step>
          <Step n={4} q="What should EvoSense avoid?" hint="Properties showing any of these are skipped entirely.">
            <Picks options={signals} value={form.excluded_signals} onChange={(v) => set('excluded_signals', v)} label="Excluded signals" />
          </Step>
          <Step n={5} q="How much may EvoSense spend?" hint="Enforced atomically on the server. $0 per day means EvoSense buys nothing.">
            <div className="evo-form-grid">
              <NumIn id="sb-day" label="Per day" prefix="$" value={form.daily_budget} onChange={(v) => set('daily_budget', v)} />
              <NumIn id="sb-mon" label="Per month" prefix="$" value={form.monthly_budget} onChange={(v) => set('monthly_budget', v)} />
              <NumIn id="sb-pp" label="Most on one property" prefix="$" value={form.max_cost_per_property} onChange={(v) => set('max_cost_per_property', v)} />
              <NumIn id="sb-ap" label="Ask me before spending above" prefix="$" value={form.approval_over} onChange={(v) => set('approval_over', v)} />
            </div>
          </Step>
          <Step n={6} q="When should EvoSense bring you in?">
            <div className="evo-form-grid">
              <NumIn id="sb-po" label="Opportunity score worth acting on" value={form.min_opportunity_score} onChange={(v) => set('min_opportunity_score', v)} suffix="/100" />
              <NumIn id="sb-cc" label="Contact confidence to reach out" value={form.min_contact_confidence} onChange={(v) => set('min_contact_confidence', v)} suffix="/100" />
              <NumIn id="sb-si" label="Hand to me at seller intent" value={form.handoff_intent_threshold} onChange={(v) => set('handoff_intent_threshold', v)} suffix="/100" />
              <NumIn id="sb-fee" label="Target assignment fee" prefix="$" value={form.target_fee} onChange={(v) => set('target_fee', v)} />
            </div>
          </Step>
          <Step n={7} q="How often should it hunt?" hint="Automatic hunts run on the platform's own scheduler. Manual strategies hunt only when you press Run hunt.">
            <div className="evo-form-grid">
              <label className="evo-field" htmlFor="sb-cad"><span className="evo-field__label">Automatic hunting</span>
                <select id="sb-cad" className="evo-select" value={form.hunt_cadence} onChange={(e) => set('hunt_cadence', e.target.value)}>
                  <option value="daily">Daily</option>
                  <option value="interval">Every few hours</option>
                  <option value="manual">Manual only</option>
                </select></label>
              {form.hunt_cadence === 'interval' ? (
                <NumIn id="sb-cadh" label="Every" suffix="hours" value={form.hunt_interval_hours} onChange={(v) => set('hunt_interval_hours', v)} />) : null}
            </div>
          </Step>
          <Step n={8} q="Run it as a controlled pilot?"
                hint="A pilot hunts only when you press Run hunt, stops at its record cap, never contacts anyone and buys no paid data unless you allow it.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Check checked={form.pilot_mode} onChange={(v) => set('pilot_mode', v)}>
                PILOT / CONTROLLED mode</Check>
            </div>
            {form.pilot_mode ? (
              <div className="evo-form-grid" style={{ marginTop: 12 }}>
                <NumIn id="sb-pcap" label="Most properties per run (max 100)" value={form.pilot_max_properties} onChange={(v) => set('pilot_max_properties', v)} />
                <Check checked={form.pilot_allow_paid} onChange={(v) => set('pilot_allow_paid', v)}>Allow paid lookups in this pilot</Check>
                {form.pilot_allow_paid ? <NumIn id="sb-pspend" label="Pilot spend cap" prefix="$" value={form.pilot_max_spend} onChange={(v) => set('pilot_max_spend', v)} /> : null}
              </div>
            ) : null}
          </Step>
          <Step n={9} q="How should eligible owners be worked?"
                hint="DNC, suppression and frequency caps always apply and cannot be switched off here.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Check checked={form.outreach_policy.auto_outreach && !form.pilot_mode} onChange={(v) => setPol('outreach_policy', 'auto_outreach', v)}>
                Start outreach automatically when an owner passes every check{form.pilot_mode ? ' (off in a pilot)' : ''}</Check>
              <Check checked={form.outreach_policy.cold_outreach_compliance_confirmed} onChange={(v) => setPol('outreach_policy', 'cold_outreach_compliance_confirmed', v)}>
                My organization has confirmed its cold-SMS compliance for this strategy
                <span className="evo-muted evo-small" style={{ display: 'block' }}>Without it, real (non-sandbox) owners are never texted.</span></Check>
              <Check checked={form.nurture_policy.allow_nurture} onChange={(v) => setPol('nurture_policy', 'allow_nurture', v)}>
                Keep “not now” sellers in nurture</Check>
            </div>
            <div className="evo-form-grid" style={{ marginTop: 12 }}>
              <NumIn id="sb-nd" label="Follow up after (when no date is given)" suffix="days"
                     value={form.nurture_policy.default_days} onChange={(v) => setPol('nurture_policy', 'default_days', Number(v) || 0)} />
            </div>
          </Step>
        </div>

        <aside className="evo-builder__readback">
          <Panel title="Your strategy" action={<Status status={status} />} raised>
            <p className="evo-readback">{preview ? preview.summary : 'Reading your strategy back…'}</p>
            {problems.length ? (
              <div style={{ marginTop: 14 }}>
                <Alert kind="warn"><span><strong>Before it can hunt:</strong>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{problems.map((x, i) => <li key={i}>{x}</li>)}</ul></span></Alert>
              </div>
            ) : null}
            <div className="evo-actionbar" style={{ marginTop: 16 }}>
              <button type="button" className="evo-btn evo-btn--ghost" disabled={busy || !form.name} onClick={() => save(false)}>Save draft</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !form.name || problems.length > 0} onClick={() => save(true)}>
                {status === 'active' ? 'Save strategy' : 'Save and start hunting'}</button>
            </div>
            <p className="evo-muted evo-small" style={{ margin: '12px 0 0' }}>Written by the server from your actual settings.</p>
          </Panel>
        </aside>
      </div>
    </EvoApp>
  )
}

export default EvoStrategies
