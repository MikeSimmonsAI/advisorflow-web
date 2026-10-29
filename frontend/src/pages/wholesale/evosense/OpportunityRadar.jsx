/* BookaBoost private professional workspace — Overlooked Properties.
 * This UI reads only authenticated, tenant-scoped EvoSense records.
 * Empty is empty; no public site, paid lookup, fabricated listing or new deal.
 */
import { useEffect, useState } from 'react'
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
// Every US state and DC: the screen is nationwide; what each workspace
// actually HAS is shown separately from the server's coverage answer.
const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']

function money(v) {
  return typeof v === 'number' ? '$' + v.toLocaleString() : null
}
const PAGE_SIZE = 20

function dateText(value) {
  if (!value) return 'Not recorded'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? 'Not recorded' : parsed.toLocaleDateString()
}

function Evidence({ items }) {
  if (!items?.length) return <p className="bb-radar__missing">Supporting evidence needs additional research.</p>
  return (
    <ul className="bb-radar__evidence" aria-label="Supporting property evidence">
      {items.slice(0, 4).map((item) => (
        <li key={item.type}>
          <strong>{item.label}</strong>
          <span>{item.source || 'Source not recorded'} · {item.freshness} · {dateText(item.effective_at || item.observed_at)}</span>
        </li>
      ))}
    </ul>
  )
}

function PropertyCard({ property: p }) {
  const category = p.property_category === 'unknown' ? 'Property type needs review'
    : [p.property_category, p.property_subtype].filter(Boolean).join(' · ').replaceAll('_', ' ')
  return (
    <article className="bb-radar__property">
      <div className="bb-radar__property-top">
        <span className="bb-radar__type">{category || 'Classification unavailable'}</span>
        {p.classification_review_required ? <span className="bb-radar__review">Classification needs review</span> : null}
      </div>
      <div className="bb-radar__property-main">
        <div>
          <h3>{p.address || (p.parcel_apn ? `Parcel ${p.parcel_apn}` : 'Address not verified')}</h3>
          <p className="bb-radar__place">{[p.city, p.county && `${p.county} County`, p.state].filter(Boolean).join(' · ') || 'Location not verified'}</p>
        </div>
        <div className="bb-radar__score">
          <b>{p.existing_opportunity_score ?? '—'}</b>
          <small>Opportunity score</small>
        </div>
      </div>
      <div className="bb-radar__details">
        <div>
          <h4>Why review this property?</h4>
          <Evidence items={p.why_review} />
        </div>
        <div className="bb-radar__facts">
          <span>Data confidence: <strong>{p.data_confidence || 'Unknown'}</strong></span>
          <span>Last observed: <strong>{dateText(p.last_observed_at)}</strong></span>
          {p.facts?.value != null ? <span>Value ({p.facts.value_basis}): <strong>{money(p.facts.value)}</strong></span> : null}
          {p.facts?.year_built ? <span>Year built: <strong>{p.facts.year_built}</strong></span> : null}
          {p.facts?.occupancy ? <span>Occupancy: <strong>{String(p.facts.occupancy).replaceAll('_', ' ')}</strong></span> : null}
        </div>
      </div>
      {p.recommended_next_action ? (
        <div className="bb-radar__next"><b>Recommended next step</b>{p.recommended_next_action.label}
          {p.recommended_next_action.detail ? <> — {p.recommended_next_action.detail}</> : null}</div>
      ) : null}
      {p.missing_information?.length ? (
        <>
          <p className="bb-radar__gaps-title">Still missing</p>
          <ul className="bb-radar__gaps" aria-label="Missing information">
            {p.missing_information.map((m) => <li key={m}>{m}</li>)}
          </ul>
        </>
      ) : null}
      <div className="bb-radar__property-actions">
        <Link to={`/wholesale/evosense/property/${encodeURIComponent(p.id)}`}>Open property research →</Link>
        {p.promoted_deal_id ? <Link to={`/wholesale/deals/${encodeURIComponent(p.promoted_deal_id)}`}>Open existing deal →</Link> : null}
        {p.promoted_deal_id ? <Link to="/wholesale/deal-room">Deal Room →</Link> : null}
      </div>
    </article>
  )
}

export default function OpportunityRadar() {
  const [market, setMarket] = useState('all')
  const [stateInput, setStateInput] = useState('')
  const [countyInput, setCountyInput] = useState('')
  const [scoreInput, setScoreInput] = useState('')
  const [applied, setApplied] = useState({ state: '', county: '', min_score: '', page: 0 })
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [inputError, setInputError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    const query = new URLSearchParams({
      market, limit: String(PAGE_SIZE), offset: String(applied.page * PAGE_SIZE),
    })
    if (applied.state) query.set('state', applied.state)
    if (applied.county) query.set('county', applied.county)
    if (applied.min_score !== '') query.set('min_score', String(applied.min_score))
    api.get('/wholesale/evosense/opportunity-radar?' + query.toString())
      .then((result) => { if (alive) setData(result) })
      .catch((err) => { if (alive) { setError(err?.message || 'Unable to load opportunities.'); setData(null) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [market, applied])

  function apply(e) {
    e.preventDefault()
    const state = stateInput.trim().toUpperCase()
    if (state && !/^[A-Z]{2}$/.test(state)) {
      setInputError('Enter a two-letter state code, such as TX or GA.')
      return
    }
    if (scoreInput !== '' && (!/^\d+$/.test(scoreInput) || Number(scoreInput) > 100)) {
      setInputError('Minimum score must be between 0 and 100.')
      return
    }
    setInputError('')
    setApplied({ state, county: countyInput.trim(), min_score: scoreInput, page: 0 })
  }

  function chooseMarket(next) {
    setMarket(next)
    setApplied((prev) => ({ ...prev, page: 0 }))
  }

  const total = data?.total ?? 0
  const haveState = Object.fromEntries((data?.available?.states || []).map((s) => [s.state, s.count]))
  const coveredStates = data?.available?.states || []
  const start = applied.page * PAGE_SIZE
  return (
    <main className="bb-radar">
      <header className="bb-radar__hero">
        <div>
          <p className="bb-radar__eyebrow">BOOKABOOST · PROFESSIONAL WORKSPACE</p>
          <h1>Overlooked Properties</h1>
          <p className="bb-radar__tagline">Find What Others Miss.</p>
          <p className="bb-radar__subtitle">Research evidence-backed opportunities in your workspace, in any state.</p>
        </div>
        <div className="bb-radar__hero-side">
          <span>OPPORTUNITY RADAR</span>
          <p>Discover · Research · Move the Deal</p>
        </div>
      </header>

      <nav className="bb-radar__markets" aria-label="Property opportunity markets">
        {MARKETS.map(([key, name]) => (
          <button key={key} type="button" className={market === key ? 'is-active' : ''}
                  aria-pressed={market === key} onClick={() => chooseMarket(key)}>{name}</button>
        ))}
      </nav>

      <form className="bb-radar__filters" onSubmit={apply}>
        <label>State (nationwide)
          <select value={stateInput} onChange={(e) => { setStateInput(e.target.value); setCountyInput('') }} aria-label="State">
            <option value="">All states</option>
            {US_STATES.map((s) => <option key={s} value={s}>{s}{haveState[s] ? ` · ${haveState[s]} in your records` : ''}</option>)}
          </select>
        </label>
        <label>County
          <input value={countyInput} onChange={(e) => setCountyInput(e.target.value)}
                 maxLength={128} placeholder="Any county" list="bb-radar-counties" aria-label="County" />
          <datalist id="bb-radar-counties">
            {(data?.available?.counties || []).map((c) => <option key={c.county} value={c.county}>{c.count} records</option>)}
          </datalist>
        </label>
        <label>Minimum existing score
          <input value={scoreInput} onChange={(e) => setScoreInput(e.target.value)}
                 inputMode="numeric" placeholder="Any score" />
        </label>
        <button className="bb-radar__apply" type="submit">Find opportunities</button>
      </form>
      {inputError ? <p className="bb-radar__error" role="alert">{inputError}</p> : null}

      <div className="bb-radar__results-heading" aria-live="polite">
        <div>
          <h2>{loading ? 'Checking your property records…' : `${total.toLocaleString()} properties`}</h2>
          <p>Only properties available to your authorized workspace are included. Coverage varies by source and region.</p>
          {!loading && !error ? (
            <p className="bb-radar__coverage">
              {coveredStates.length
                ? <>Your records today cover <b>{coveredStates.length} {coveredStates.length === 1 ? 'state' : 'states'}</b>: {coveredStates.map((s) => `${s.state} (${s.count})`).join(', ')}.</>
                : <>Your workspace has no property records for this market yet.</>}
            </p>
          ) : null}
        </div>
        <Link to="/wholesale/evosense/inbox">Discovery Inbox →</Link>
      </div>

      {error ? <div className="bb-radar__error" role="alert">
        <strong>Opportunity Radar is unavailable.</strong> {error}
        <button type="button" onClick={() => setApplied((p) => ({ ...p }))}>Retry</button>
      </div> : null}
      {loading ? <div className="bb-radar__empty" role="status">Loading existing property evidence…</div> : null}
      {!loading && !error && !data?.items?.length ? (
        <div className="bb-radar__empty">
          <h3>No matching properties in this workspace yet.</h3>
          <p>Try another market or state, or open Discovery Inbox to review your available data. This screen never copies opportunities from another tenant or invents nationwide results.</p>
          <Link to="/wholesale/evosense/inbox">Open Discovery Inbox →</Link>
        </div>
      ) : null}
      {!loading && !error && data?.items?.length ? (
        <div className="bb-radar__property-list">{data.items.map((p) => <PropertyCard key={p.id} property={p} />)}</div>
      ) : null}

      {!loading && !error && total > PAGE_SIZE && (
        <div className="bb-radar__pages">
          <button type="button" disabled={applied.page === 0}
                  onClick={() => setApplied((p) => ({ ...p, page: p.page - 1 }))}>Previous</button>
          <span>{start + 1}–{Math.min(total, start + PAGE_SIZE)} of {total}</span>
          <button type="button" disabled={start + PAGE_SIZE >= total}
                  onClick={() => setApplied((p) => ({ ...p, page: p.page + 1 }))}>Next</button>
        </div>
      )}
      <p className="bb-radar__disclaimer">The opportunity score is a research aid, not an appraisal, listing verification, indication of seller willingness, or guarantee of value. Confirm source records and applicable licensing before taking action.</p>
    </main>
  )
}
