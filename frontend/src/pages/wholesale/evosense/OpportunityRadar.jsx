/* BookaBoost private professional workspace — Overlooked Properties.
 * Built to the owner-approved interactive mockup (#427). Every number and
 * property on this screen comes from the signed-in workspace's own records
 * through authenticated APIs. Empty is empty: nothing is invented, nothing is
 * saved, sent or bought from here.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import './OpportunityRadar.css'

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

function Kpis({ data, loading }) {
  const s = data?.summary
  const pageMatches = (data?.items || []).reduce((n, p) => n + (p.buyer_matches?.confirmed || 0), 0)
  const cell = (num, label, help, tone) => (
    <article className="bbr-kpi"><div className={'bbr-kpi__num ' + (tone || '')}>{loading ? '…' : num}</div>
      <div className="bbr-kpi__label">{label}</div><div className="bbr-kpi__help">{help}</div></article>
  )
  return (
    <div className="bbr-kpis">
      {cell((s?.total ?? 0).toLocaleString(), 'Opportunities in view', 'Your workspace records, current filters')}
      {cell((s?.with_evidence ?? 0).toLocaleString(), 'With research signals', 'Current, sourced evidence on record', 'gold')}
      {cell(pageMatches.toLocaleString(), 'Confirmed buyer matches', 'Properties on this page · Buyer Intelligence', 'green')}
      {cell(s?.average_score != null ? `${s.average_score}/100` : '—', 'Average opportunity score',
        s?.scored ? `Across ${s.scored} scored ${s.scored === 1 ? 'record' : 'records'}` : 'No scored records yet')}
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
          <small>{showMatches ? 'BOOKABOOST · BUYER MATCHES' : 'BOOKABOOST · PROPERTY RESEARCH'}</small>
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

export default function OpportunityRadar() {
  const [market, setMarket] = useState('all')
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
  const reset = () => { const z = { state: '', county: '', min: '' }; setForm(z); setApplied(z); setMarket('all'); setSort('score'); setPage(0) }
  const chooseMarket = (m) => { setMarket(m); setPage(0) }
  const closeDrawer = useCallback(() => setOpen(null), [])

  const total = data?.total ?? 0
  const start = page * PAGE_SIZE
  const have = Object.fromEntries((data?.available?.states || []).map((s) => [s.state, s.count]))
  const covered = data?.available?.states || []

  return (
    <main className="bbr">
      <section className="bbr-hero">
        <div>
          <div className="bbr-eyebrow">BOOKABOOST · OPPORTUNITY INTELLIGENCE</div>
          <h1>Overlooked Properties</h1>
          <p>Find What Others Miss. Discover, research, and match overlooked real estate opportunities across every market—without leaving your professional workspace.</p>
        </div>
        <div className="bbr-hero__side"><div>OPPORTUNITY RADAR</div><span>Discover → Verify → Match → Deal</span></div>
      </section>

      <Kpis data={data} loading={loading} />

      <div className="bbr-worktop"><h2>Opportunity Radar</h2><span>Nationwide-ready filters · Source-backed research</span></div>
      <section className="bbr-filters" aria-label="Search and filter property opportunities">
        <div className="bbr-tabs" role="group" aria-label="Property market">
          {MARKETS.map(([k, label]) => (
            <button key={k} type="button" className={'bbr-tab' + (market === k ? ' is-active' : '')}
                    aria-pressed={market === k} onClick={() => chooseMarket(k)}>{label}</button>
          ))}
        </div>
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
          {data?.items?.length ? data.items.map((p) => <Card key={p.id} p={p} market={market} onOpen={setOpen} />) : (
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
