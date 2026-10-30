/* BookaBoost private professional workspace — Overlooked Properties
 * (shown as OLP - Overlooked Properties, powered by BookaBoost, when the
 * BookaBoost product brand is 'olp'; see src/brand/productBrand.js).
 * Built to the owner-approved interactive mockup (#427). Every number and
 * property on this screen comes from the signed-in workspace's own records
 * through authenticated APIs. Empty is empty: nothing is invented, nothing is
 * saved, sent or bought from here.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { bookaboostProductBrand } from '../../../brand/productBrand'
import heroPhoto from './img/olp-hero.webp'
import photoResidential from '../ds/photos/house08.jpg'
import photoWholesale from '../ds/photos/street.jpg'
// Land, farm and ranch records show open land, never a house (owner request, Oct 29).
import photoLand from '../ds/photos/land-field.jpg'
import photoCommercial from '../ds/photos/invest.jpg'
import './OpportunityRadar.css'

// OLP - Overlooked Properties, powered by BookaBoost (brand exploration): the
// product face of this screen. 'bookaboost' keeps the #428 look and wording.
const IS_OLP = bookaboostProductBrand() === 'olp'
const BRAND = IS_OLP ? 'OLP' : 'BOOKABOOST'
// A record without its own photo shows a plain photo of its KIND, always
// labelled "Representative photo" and never presented as the property
// (same rule as ds/photos/CREDITS.md). An unknown type gets no photo.
const KIND_PHOTO = { residential: photoResidential, wholesale: photoWholesale, land: photoLand, commercial: photoCommercial }

const MARKETS = [
  ['all', 'All Opportunities'],
  ['residential', 'Residential'],
  ['wholesale', 'Wholesale / Distressed'],
  ['land', 'Land, Farms & Ranches'],
  ['commercial', 'Commercial'],
]
const CHIP = { residential: 'Residential', multifamily: 'Residential', land: 'Land & Farms', agricultural: 'Land & Farms',
  commercial: 'Commercial', industrial: 'Commercial', mixed_use: 'Commercial', specialty: 'Commercial' }
const THUMB = { residential: 'residential', multifamily: 'residential', land: 'land', agricultural: 'land',
  commercial: 'commercial', industrial: 'commercial', mixed_use: 'commercial', specialty: 'commercial' }
// Every state and DC: the screen is nationwide. What this workspace actually
// has is shown beside each state from the server's coverage answer.
const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']
const SCORES = [['', 'Any score'], ['50', '50+'], ['65', '65+'], ['75', '75+'], ['85', '85+']]
const SORTS = [['score', 'Highest opportunity score'], ['recent', 'Most recently observed'], ['state', 'State A–Z']]
const PAGE_SIZE = 6

function dateText(value) {
  if (!value) return 'date not recorded'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? 'date not recorded'
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
function money(v) { return typeof v === 'number' ? '$' + v.toLocaleString() : null }
function humane(v) { return String(v || '').replaceAll('_', ' ') }
function sentence(v) { return v ? v.charAt(0).toUpperCase() + v.slice(1) : v }
function place(p) {
  const city = [p.city, p.state].filter(Boolean).join(', ')
  return [city || null, p.county ? `${p.county} County` : null].filter(Boolean).join(' · ') || 'Location not verified'
}
function title(p) { return p.address || (p.parcel_apn ? `Parcel ${p.parcel_apn}` : 'Address not verified') }
function chipFor(p, market) {
  if (market === 'wholesale') return ['Wholesale / Distressed', 'wholesale']
  const c = p.property_category
  if (!c || c === 'unknown') return ['Type needs review', 'unknown']
  return [CHIP[c] || humane(c), THUMB[c] || 'residential']
}

const KPI_ICON = {
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.6-4.6',
  chart: 'M5 20V12M10 20V6M15 20V10M20 20V4M3 20h18',
  users: 'M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.2a3.5 3.5 0 0 1 0 6.6',
  star: 'M12 3l2.7 5.6 6.1.8-4.4 4.3 1 6.1L12 17l-5.4 2.8 1-6.1-4.4-4.3 6.1-.8z',
}
function KpiIcon({ n }) {
  return (
    <span className={'bbr-kpi__ic bbr-kpi__ic--' + n} aria-hidden="true">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" focusable="false"><path d={KPI_ICON[n]} /></svg>
    </span>
  )
}

function Kpis({ data, loading }) {
  const s = data?.summary
  const pageMatches = (data?.items || []).reduce((n, p) => n + (p.buyer_matches?.confirmed || 0), 0)
  const cell = (num, label, help, tone, icon) => (
    <article className="bbr-kpi">{IS_OLP ? <KpiIcon n={icon} /> : null}<div className="bbr-kpi__txt"><div className={'bbr-kpi__num ' + (tone || '')}>{loading ? '…' : num}</div>
      <div className="bbr-kpi__label">{label}</div><div className="bbr-kpi__help">{help}</div></div></article>
  )
  return (
    <div className="bbr-kpis">
      {cell((s?.total ?? 0).toLocaleString(), 'Opportunities in view', 'Your workspace records, current filters', '', 'search')}
      {cell((s?.with_evidence ?? 0).toLocaleString(), 'With research signals', 'Current, sourced evidence on record', 'gold', 'chart')}
      {cell(pageMatches.toLocaleString(), 'Confirmed buyer matches', 'Properties on this page · Buyer Intelligence', 'green', 'users')}
      {cell(s?.average_score != null ? `${s.average_score}/100` : '—', 'Average opportunity score',
        s?.scored ? `Across ${s.scored} scored ${s.scored === 1 ? 'record' : 'records'}` : 'No scored records yet', '', 'star')}
    </div>
  )
}

function Card({ p, market, onOpen }) {
  const [chip, kind] = chipFor(p, market)
  const reasons = p.why_review || []
  const m = p.buyer_matches || {}
  return (
    <article className="bbr-prop">
      <div className="bbr-prop__top">
        <div className={'bbr-thumb bbr-thumb--' + kind} aria-hidden="true" />
        <div className="bbr-prop__meta">
          <span className={'bbr-chip bbr-chip--' + kind}>{chip}</span>
          <h3 title={title(p)}>{title(p)}</h3>
          <p className="bbr-sub">{place(p)}</p>
        </div>
        <div className="bbr-score"><b>{p.existing_opportunity_score ?? '—'}</b><span>Opportunity Score</span></div>
      </div>
      <div className="bbr-prop__body">
        <div className="bbr-why">
          <h4>WHY THIS PROPERTY DESERVES RESEARCH</h4>
          {reasons.length
            ? reasons.slice(0, 2).map((r) => <p className="bbr-signal" key={r.type}>{r.label}</p>)
            : <p className="bbr-signal bbr-signal--none">Evidence needed</p>}
        </div>
        <div className="bbr-facts">
          Buyer matches<b>{m.buyers_on_file ? m.confirmed : '—'}</b>
          Data confidence<b>{p.data_confidence ? humane(p.data_confidence) : 'Unknown'}</b>
        </div>
      </div>
      <div className="bbr-prop__bottom">
        <span className="bbr-observed">Last observed {dateText(p.last_observed_at)}</span>
        <button type="button" className="bbr-more" onClick={() => onOpen(p)}>View opportunity →</button>
      </div>
    </article>
  )
}

// OLP card: photo header (the record's kind, labelled representative), the
// score, then only facts the record actually holds. No save control: the
// watchlist is on hold.
function OlpCard({ p, market, onOpen }) {
  const [chip, kind] = chipFor(p, market)
  const photo = KIND_PHOTO[kind]
  const reasons = p.why_review || []
  const m = p.buyer_matches || {}
  const f = p.facts || {}
  const meta = [
    p.property_subtype ? sentence(humane(p.property_subtype)) : (p.property_category && p.property_category !== 'unknown' ? sentence(humane(p.property_category)) : null),
    f.bedrooms != null ? `${f.bedrooms} bd` : null,
    f.bathrooms != null ? `${f.bathrooms} ba` : null,
    f.square_feet ? `${Number(f.square_feet).toLocaleString()} sqft` : null,
    f.year_built ? `Built ${f.year_built}` : null,
    f.occupancy ? humane(f.occupancy) : null,
  ].filter(Boolean)
  return (
    <article className="bbr-prop bbr-oc">
      <div className={'bbr-oc__media bbr-oc__media--' + kind}>
        {photo ? <img src={photo} alt="" loading="lazy" decoding="async" /> : <span className="bbr-oc__nophoto">No photo on file</span>}
        <span className={'bbr-chip bbr-chip--' + kind}>{chip}</span>
        <span className="bbr-oc__score"><b>{p.existing_opportunity_score ?? '—'}</b><small>Opportunity Score</small></span>
        {photo ? <span className="bbr-oc__rep">Representative photo</span> : null}
      </div>
      <div className="bbr-oc__body">
        {f.value != null ? <p className="bbr-oc__value">{money(f.value)}<small>{f.value_basis || 'Recorded value'}</small></p>
          : <p className="bbr-oc__value bbr-oc__value--none">Value not on record</p>}
        <h3 title={title(p)}>{title(p)}</h3>
        <p className="bbr-sub">{place(p)}</p>
        <div className="bbr-oc__tags">
          {reasons.length ? reasons.slice(0, 2).map((r) => <span key={r.type}>{r.label}</span>)
            : <span className="bbr-oc__tag--none">Evidence needed</span>}
        </div>
        {meta.length ? <p className="bbr-oc__meta">{meta.join(' · ')}</p> : null}
        <p className="bbr-oc__facts">
          <span>Buyer matches <b>{m.buyers_on_file ? m.confirmed : '—'}</b></span>
          <span>Confidence <b>{p.data_confidence ? humane(p.data_confidence) : 'Unknown'}</b></span>
          <span>Observed <b>{dateText(p.last_observed_at)}</b></span>
        </p>
        <button type="button" className="bbr-oc__go" onClick={() => onOpen(p)}>View opportunity <span aria-hidden="true">→</span></button>
      </div>
    </article>
  )
}

function Drawer({ p, market, onClose }) {
  const [matches, setMatches] = useState(null)
  const [mErr, setMErr] = useState('')
  const [showMatches, setShowMatches] = useState(false)
  const closeRef = useRef(null)
  useEffect(() => {
    closeRef.current?.focus()
    const esc = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  function loadMatches() {
    setShowMatches(true)
    if (matches) return
    api.get(`/wholesale/evosense/opportunity-radar/${encodeURIComponent(p.id)}/buyer-matches`)
      .then(setMatches).catch((e) => setMErr(e?.message || 'Buyer matches could not be loaded.'))
  }
  const [chip] = chipFor(p, market)
  const next = p.recommended_next_action
  return (
    <div className="bbr-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <aside className="bbr-drawer" role="dialog" aria-modal="true" aria-labelledby="bbr-drawer-title">
        <div className="bbr-drawer__head">
          <small>{showMatches ? `${BRAND} · BUYER MATCHES` : `${BRAND} · PROPERTY RESEARCH`}</small>
          <button type="button" ref={closeRef} onClick={onClose} aria-label="Close">×</button>
        </div>
        <h2 id="bbr-drawer-title">{showMatches ? 'Buyer Matches' : title(p)}</h2>
        <p>{place(p)} · <strong>{chip}</strong></p>
        {!showMatches ? (
          <>
            <div className="bbr-dscore"><strong>{p.existing_opportunity_score ?? '—'}</strong> Opportunity Score
              <span>The same score used across Acquisition — no new formula.</span></div>
            <div className="bbr-section">
              <h3>Why this property was flagged</h3>
              {(p.why_review || []).length ? p.why_review.map((r) => (
                <div className="bbr-row" key={r.type}>
                  <strong>✓ {r.label}</strong>
                  <span>Source: {r.source || 'not recorded'} · observed {dateText(r.effective_at || r.observed_at)} · {r.freshness}
                    {r.confidence != null ? ` · confidence ${r.confidence}` : ''}</span>
                </div>
              )) : <div className="bbr-row"><strong>No current supporting evidence</strong>
                <span>This record needs research before it can be called an opportunity.</span></div>}
            </div>
            <div className="bbr-section">
              <h3>Opportunity details</h3>
              <div className="bbr-row"><strong>Data confidence: {p.data_confidence ? humane(p.data_confidence) : 'Unknown'}</strong>
                <span>Last observed {dateText(p.last_observed_at)}{p.classification_review_required ? ' · property type needs review' : ''}</span></div>
              {p.facts?.value != null ? <div className="bbr-row"><strong>Value: {money(p.facts.value)}</strong>
                <span>{p.facts.value_basis}{p.facts.value_source ? ` · ${p.facts.value_source}` : ''}</span></div> : null}
              {p.facts?.year_built || p.facts?.occupancy ? <div className="bbr-row">
                <strong>{[p.facts.year_built ? `Built ${p.facts.year_built}` : null, p.facts.occupancy ? humane(p.facts.occupancy) : null].filter(Boolean).join(' · ')}</strong>
                <span>{p.facts.occupancy_source || 'From the property record'}</span></div> : null}
              {next ? <div className="bbr-row"><strong>Next recommended step</strong><span>{next.label}{next.detail ? ` — ${next.detail}` : ''}</span></div> : null}
              {p.missing_information?.length ? <div className="bbr-row"><strong>Still missing</strong>
                <span>{p.missing_information.join(' · ')}</span></div> : null}
            </div>
            <div className="bbr-buttons">
              <Link className="bbr-btn bbr-btn--gold" to={`/wholesale/evosense/property/${encodeURIComponent(p.id)}`}>Open property research</Link>
              <button type="button" className="bbr-btn" onClick={loadMatches}>View buyer matches ({p.buyer_matches?.confirmed ?? 0})</button>
              {p.promoted_deal_id ? <Link className="bbr-btn" to={`/wholesale/deals/${encodeURIComponent(p.promoted_deal_id)}`}>Open existing deal</Link> : null}
              {p.promoted_deal_id ? <Link className="bbr-btn" to="/wholesale/deal-room">Deal Room</Link> : null}
            </div>
          </>
        ) : (
          <>
            <div className="bbr-note">Read only — counted from your Buyer Intelligence buy boxes. Nothing is sent and no match is saved.</div>
            {mErr ? <div className="bbr-row bbr-row--err"><strong>Buyer matches are unavailable.</strong><span>{mErr}</span></div> : null}
            {!matches && !mErr ? <div className="bbr-row" role="status"><strong>Checking buyer criteria…</strong></div> : null}
            {matches ? (
              <div className="bbr-section">
                <h3>{matches.confirmed} confirmed {matches.confirmed === 1 ? 'buyer' : 'buyers'} · {matches.buyers_on_file} on file</h3>
                {matches.items.map((m) => (
                  <div className="bbr-row" key={m.buyer_id}>
                    <strong>{m.name} · match {m.score}</strong>
                    <span>{m.buy_box ? `${m.buy_box}: ` : ''}{m.why.join(' ')}</span>
                    {m.unknown?.length ? <span className="bbr-unknown">Not yet known: {m.unknown.join(' ')}</span> : null}
                  </div>
                ))}
                {!matches.items.length ? <div className="bbr-row"><strong>No confirmed buyer match yet</strong>
                  <span>A buyer counts only when the record confirms both their location and property type.</span></div> : null}
                {matches.budget_note ? <p className="bbr-fine">{matches.budget_note}</p> : null}
                <p className="bbr-fine">{matches.rule}</p>
              </div>
            ) : null}
            <div className="bbr-buttons">
              <button type="button" className="bbr-btn" onClick={() => setShowMatches(false)}>← Back to property</button>
              <Link className="bbr-btn" to="/wholesale/buyers">Buyer Intelligence</Link>
            </div>
          </>
        )}
      </aside>
    </div>
  )
}

// A workspace built on this screen (OLP Farm & Ranch) passes a preset: its own
// market, hero words and photo. Without one the screen is exactly as before.
// ?market=land opens a known market tab (links from Land Partners).
const MARKET_KEYS = new Set(MARKETS.map(([k]) => k))

export default function OpportunityRadar({ preset } = {}) {
  const [params] = useSearchParams()
  const asked = params.get('market')
  const startMarket = preset?.market || (MARKET_KEYS.has(asked) ? asked : 'all')
  const [market, setMarket] = useState(startMarket)
  const [form, setForm] = useState({ state: '', county: '', min: '' })
  const [applied, setApplied] = useState({ state: '', county: '', min: '' })
  const [sort, setSort] = useState('score')
  const [page, setPage] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null)
  const [reload, setReload] = useState(0)
  const [counties, setCounties] = useState([])

  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    const q = new URLSearchParams({ market, sort, limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
    if (applied.state) q.set('state', applied.state)
    if (applied.county) q.set('county', applied.county)
    if (applied.min) q.set('min_score', applied.min)
    api.get('/wholesale/evosense/opportunity-radar?' + q.toString())
      .then((r) => { if (alive) setData(r) })
      .catch((e) => { if (alive) { setError(e?.message || 'Unable to load opportunities.'); setData(null) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [market, applied, sort, page, reload])

  // County choices come from the records this workspace actually holds in the chosen state.
  useEffect(() => {
    let alive = true
    if (!form.state) { setCounties([]); return undefined }
    api.get('/wholesale/evosense/opportunity-radar?' + new URLSearchParams({ market, state: form.state, limit: '1' }).toString())
      .then((r) => { if (alive) setCounties(r?.available?.counties || []) })
      .catch(() => { if (alive) setCounties([]) })
    return () => { alive = false }
  }, [form.state, market])

  const submit = (e) => { e.preventDefault(); setPage(0); setApplied({ ...form }) }
  const reset = () => { const z = { state: '', county: '', min: '' }; setForm(z); setApplied(z); setMarket(preset?.market || 'all'); setSort('score'); setPage(0) }
  const chooseMarket = (m) => { setMarket(m); setPage(0) }
  const closeDrawer = useCallback(() => setOpen(null), [])

  const total = data?.total ?? 0
  const start = page * PAGE_SIZE
  const have = Object.fromEntries((data?.available?.states || []).map((s) => [s.state, s.count]))
  const covered = data?.available?.states || []

  return (
    <main className={'bbr' + (IS_OLP ? ' bbr--olp' : '')} data-product={IS_OLP ? 'olp' : undefined}>
      <section className="bbr-hero">
        {IS_OLP ? <img className="bbr-hero__photo" src={preset?.photo || heroPhoto} alt="" aria-hidden="true" decoding="async" /> : null}
        <div className="bbr-hero__text">
          <div className="bbr-eyebrow">{BRAND} · {preset?.eyebrow || 'OPPORTUNITY INTELLIGENCE'}</div>
          {preset ? <h1>{preset.title[0]} <span className="bbr-gold">{preset.title[1]}</span></h1>
            : <h1>Overlooked {IS_OLP ? <span className="bbr-gold">Properties</span> : 'Properties'}</h1>}
          <p>{preset?.lede || <>Find What Others Miss. Discover, research, and match overlooked real estate opportunities{IS_OLP ? ' in residential, wholesale, land and commercial markets' : ' across every market'}—without leaving your professional workspace.</>}</p>
        </div>
        <div className="bbr-hero__side"><div>{preset?.side?.[0] || 'OPPORTUNITY RADAR'}</div><span>{preset?.side?.[1] || 'Discover → Verify → Match → Deal'}</span></div>
      </section>

      <Kpis data={data} loading={loading} />
      {preset?.slot || null}

      <div className="bbr-worktop"><h2>{preset?.worktop || 'Opportunity Radar'}</h2><span>Nationwide-ready filters · Source-backed research</span></div>
      <section className="bbr-filters" aria-label="Search and filter property opportunities">
        {preset?.lockMarket ? null : (
          <div className="bbr-tabs" role="group" aria-label="Property market">
            {MARKETS.map(([k, label]) => (
              <button key={k} type="button" className={'bbr-tab' + (market === k ? ' is-active' : '')}
                      aria-pressed={market === k} onClick={() => chooseMarket(k)}>{label}</button>
            ))}
          </div>
        )}
        <form className="bbr-row-filters" onSubmit={submit}>
          <label className="bbr-field"><span>State</span>
            <select value={form.state} aria-label="State" onChange={(e) => setForm({ ...form, state: e.target.value, county: '' })}>
              <option value="">All states</option>
              {US_STATES.map((s) => <option key={s} value={s}>{s}{have[s] ? ` · ${have[s]} on record` : ''}</option>)}
            </select>
          </label>
          <label className="bbr-field"><span>County</span>
            <select value={form.county} aria-label="County" disabled={!form.state} onChange={(e) => setForm({ ...form, county: e.target.value })}>
              <option value="">{form.state ? 'All counties' : 'Choose a state first'}</option>
              {counties.map((c) => <option key={c.county} value={c.county}>{c.county} ({c.count})</option>)}
            </select>
          </label>
          <label className="bbr-field"><span>Minimum existing score</span>
            <select value={form.min} aria-label="Minimum existing score" onChange={(e) => setForm({ ...form, min: e.target.value })}>
              {SCORES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <button className="bbr-primary" type="submit">Search opportunities</button>
          <button className="bbr-secondary" type="button" onClick={reset}>Reset</button>
        </form>
        {!loading && !error ? (
          <p className="bbr-coverage">{covered.length
            ? <>Your workspace has records in <b>{covered.length} {covered.length === 1 ? 'state' : 'states'}</b>: {covered.map((s) => `${s.state} (${s.count})`).join(', ')}. Other states show what is on record — nothing is invented.</>
            : <>Your workspace has no property records for this market yet.</>}</p>
        ) : null}
      </section>

      <div className="bbr-results-head">
        <div>
          <h2>{loading ? 'Checking your property records…' : `${total.toLocaleString()} ${total === 1 ? 'opportunity' : 'opportunities'}`}</h2>
          <p>Only records in your authorized workspace · open any card for sources, dates and buyer matches</p>
        </div>
        <div className="bbr-controls">
          <select className="bbr-small" aria-label="Sort properties" value={sort} onChange={(e) => { setSort(e.target.value); setPage(0) }}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
      </div>

      {error ? <div className="bbr-error" role="alert"><strong>Opportunity Radar is unavailable.</strong> {error}
        <button type="button" onClick={() => setReload((n) => n + 1)}>Retry</button></div> : null}
      {loading ? <div className="bbr-cards" role="status" aria-label="Loading opportunities">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="bbr-prop bbr-skeleton" />)}</div> : null}
      {!loading && !error ? (
        <section className="bbr-cards" aria-label="Property results">
          {data?.items?.length ? data.items.map((p) => (IS_OLP
            ? <OlpCard key={p.id} p={p} market={market} onOpen={setOpen} />
            : <Card key={p.id} p={p} market={market} onOpen={setOpen} />)) : (
            <div className="bbr-empty">
              <div className="bbr-empty__icon" aria-hidden="true">⌕</div>
              <h3>No matching opportunities in your workspace</h3>
              <p>Change the market, state, county or score. This screen only shows records your workspace actually holds — it never borrows another account's properties or invents nationwide results.</p>
              <Link to="/wholesale/evosense/inbox">Open Discovery Inbox →</Link>
            </div>
          )}
        </section>
      ) : null}

      {!loading && !error && total > 0 ? (
        <div className="bbr-pages">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>← Previous</button>
          <span>{start + 1}–{Math.min(total, start + PAGE_SIZE)} of {total}</span>
          <button type="button" disabled={start + PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      ) : null}
      <p className="bbr-footnote">The Opportunity Score is a research aid from your existing property evidence — not an appraisal, a listing, a sign the owner will sell, or a guarantee of value. Buyer matches are read from your buy boxes and confirmed only on known location and property type. Confirm source records and licensing before acting.</p>
      {open ? <Drawer p={open} market={market} onClose={closeDrawer} /> : null}
    </main>
  )
}
