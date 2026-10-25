/* EVOSENSE — DISCOVERY INBOX (Phase 7.3 light rebuild; board screen 2).
 *
 * Answers three questions for every property EvoSense has evaluated:
 *   WHAT HAS EVOSENSE FOUND?   the address dominates each row
 *   WHAT STATE IS IT IN?       one status, derived by the server - never set by hand
 *   WHAT HAPPENS NEXT?         the one next action
 *
 * Every property sits in exactly ONE bucket. Paged server-side (50 at a time)
 * so a 100k-property workspace loads like a 30-property one. Filters live in
 * the URL, so a filtered view can be bookmarked and shared.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { CATEGORIES, subtypesOf, TypeLabel } from './ptypes'
import { errText } from '../wsShared'
import {
  Alert, Chips, Empty, EvoApp, Hero, Num, Panel, PropertyThumb, Ring, SandboxTag,
  Skeleton, Status, Tag, moneyK, shortDate, statusLabel,
} from '../ds/ds'
import '../ds/evo-pages.css'
import './evo-actions.css'
import { ev } from '../engine'

const PAGE = 50
// The order an operator scans the buckets in: what needs them first.
const PRIMARY = ['needs_you', 'responded', 'outreach_active', 'ready_for_outreach', 'contact_found',
  'high_opportunity', 'needs_enrichment', 'waiting_for_data', 'budget_blocked', 'needs_review',
  'nurture', 'promoted', 'suppressed', 'closed_out', 'low_opportunity', 'new']

// The deeper search. Each one filters on a public-record column the county
// actually filled; a property with that fact unknown never matches a range.
const EXTRA = [
  ['street', 'Street name', 'text', 'e.g. Macon'],
  ['owner', 'Owner name', 'text', 'e.g. Smith'],
  ['parcel', 'Parcel / account #', 'text', ''],
  ['city', 'City', 'city', ''],
  ['zip', 'ZIP code(s)', 'text', '75215, 76114'],
  ['ptype', 'Property category', 'pcat', ''],
  ['psubtype', 'Property subtype', 'psub', ''],
  ['property_type', 'Older type field', 'ptype', ''],
  ['min_value', 'Tax value from ($)', 'number', '50000'],
  ['max_value', 'Tax value to ($)', 'number', '250000'],
  ['min_beds', 'Bedrooms at least', 'number', '3'],
  ['min_baths', 'Full baths at least', 'number', '2'],
  ['min_sqft', 'Living area from (sq ft)', 'number', ''],
  ['max_sqft', 'Living area to (sq ft)', 'number', ''],
  ['min_year_built', 'Built in or after', 'number', '1950'],
  ['max_year_built', 'Built in or before', 'number', '1990'],
  ['min_owned_years', 'Owned at least (years)', 'number', '10'],
  ['max_owned_years', 'Owned at most (years)', 'number', ''],
  ['occupancy', 'Occupancy', 'occ', ''],
  ['missing', 'Data on file', 'missing', ''],
]

function MoreFilters({ params, data, onApply, onClear }) {
  const [draft, setDraft] = useState(() => Object.fromEntries(EXTRA.map(([k]) => [k, params.get(k) || ''])))
  const sigs = (params.get('signal') || '').split(',').filter(Boolean)
  const [sigDraft, setSigDraft] = useState(sigs)
  const setK = (k, v) => setDraft((d) => ({ ...d, [k]: v }))
  const toggleSig = (k) => setSigDraft((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]))
  return (
    <form className="evo-morefilters" onSubmit={(e) => { e.preventDefault(); onApply({ ...draft, signal: sigDraft.join(',') }) }}
          style={{ padding: '12px 20px 16px', borderTop: '1px solid var(--evo-line, #e5e9f0)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: '10px 14px' }}>
        {EXTRA.map(([k, label, kind, ph]) => (
          <label key={k} className="evo-field" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="evo-small evo-muted">{label}</span>
            {kind === 'city' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => setK(k, e.target.value)}>
                <option value="">Any city</option>
                {(data?.cities || []).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : kind === 'ptype' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => setK(k, e.target.value)}>
                <option value="">Any type</option>
                {(data?.property_types || []).map((c) => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
              </select>
            ) : kind === 'pcat' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => { setK(k, e.target.value); setK('psubtype', '') }}>
                <option value="">All properties</option>
                {CATEGORIES.map(([c, l]) => <option key={c} value={c}>{c === 'unknown' ? 'Unknown (needs review)' : l}</option>)}
              </select>
            ) : kind === 'psub' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => setK(k, e.target.value)} disabled={!draft.ptype}>
                <option value="">Any subtype</option>
                {subtypesOf(draft.ptype).map(([c, l]) => <option key={c} value={c}>{l}</option>)}
              </select>
            ) : kind === 'occ' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => setK(k, e.target.value)}>
                <option value="">Any</option>
                <option value="vacant">Vacant</option>
                <option value="owner_occupied">Owner lives there</option>
                <option value="tenant">Rented</option>
              </select>
            ) : kind === 'missing' ? (
              <select className="evo-select" value={draft[k]} onChange={(e) => setK(k, e.target.value)}>
                <option value="">Anything</option>
                <option value="physical">Missing size / year built</option>
              </select>
            ) : (
              <input className="evo-input" type={kind === 'number' ? 'number' : 'text'} inputMode={kind === 'number' ? 'numeric' : undefined}
                     placeholder={ph} value={draft[k]} onChange={(e) => setK(k, e.target.value)} />
            )}
          </label>
        ))}
      </div>
      <p className="evo-small evo-muted" style={{ margin: '14px 0 6px' }}>Must have ALL of these signals</p>
      <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 6 }}>
        {(data?.signal_types || []).map((s) => (
          <button key={s.key} type="button" className="evo-bucketbtn" aria-pressed={sigDraft.includes(s.key)}
                  onClick={() => toggleSig(s.key)}>{s.label}</button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <button type="submit" className="evo-btn evo-btn--primary evo-btn--sm">Search</button>
        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={onClear}>Clear all filters</button>
      </div>
    </form>
  )
}

export default function EvoInbox() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const bucket = params.get('bucket') || ''
  const strategy = params.get('strategy') || ''
  const signal = params.get('signal') || ''
  const sort = params.get('sort') || 'priority'
  const offset = Number(params.get('offset') || 0)
  // Top 10 / 25 / 100 are views of the SAME ranking, not separate lists
  const top = params.get('top') || ''
  const pageSize = top ? Number(top) : PAGE
  const category = params.get('category') || ''
  const econ = params.get('economics') || ''
  const conf = params.get('confidence') || ''
  const county = params.get('county') || ''
  const source = params.get('source') || ''
  const minScore = params.get('min_score') || ''
  const archived = params.get('archived') === '1'
  const [q, setQ] = useState(params.get('q') || '')
  // The top-bar search can change ?q while this page is open.
  const urlQ = params.get('q') || ''
  useEffect(() => { setQ(urlQ) }, [urlQ])
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const p = new URLSearchParams({ limit: pageSize, offset: top ? 0 : offset, sort })
      if (category) p.set('category', category)
      if (econ) p.set('economics', econ)
      if (conf) p.set('confidence', conf)
      if (bucket) p.set('bucket', bucket)
      if (strategy) p.set('strategy_id', strategy)
      if (signal) p.set('signal', signal)
      if (county) p.set('county', county)
      if (source) p.set('source', source)
      if (minScore) p.set('min_score', minScore)
      if (archived) p.set('archived', 'true')
      if (params.get('q')) p.set('q', params.get('q'))
      EXTRA.forEach(([k]) => { if (params.get(k)) p.set(k, params.get(k)) })
      setData(await api.get('/wholesale/evosense/inbox?' + p.toString()))
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [bucket, strategy, signal, sort, offset, params, county, source, minScore, archived, pageSize, top, category, econ, conf])
  useEffect(() => { load() }, [load])

  function set(k, v) {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v); else next.delete(k)
    if (k !== 'offset') next.delete('offset')
    setParams(next)
  }
  function setMany(obj) {
    const next = new URLSearchParams(params)
    Object.entries(obj).forEach(([k, v]) => { if (v) next.set(k, String(v).trim()); else next.delete(k) })
    next.delete('offset')
    setParams(next)
  }
  const activeExtra = EXTRA.filter(([k]) => params.get(k)).length + (signal.includes(',') ? 1 : 0)
  const [showMore, setShowMore] = useState(activeExtra > 0)
  const [moreKey, setMoreKey] = useState(0)

  const counts = Object.fromEntries((data?.buckets || []).map((b) => [b.key, b.count]))
  const allCount = data ? data.buckets.reduce((n, b) => n + b.count, 0) : null
  const orderedBuckets = (data?.buckets || []).slice().sort((a, b) => PRIMARY.indexOf(a.key) - PRIMARY.indexOf(b.key))
  // Record-level SANDBOX tags only when a list MIXES sandbox and live records;
  // a wholly sandbox workspace is already said once, by the environment pill.
  const mixed = !!data && data.items.some((p) => p.is_test) && data.items.some((p) => !p.is_test)
  const worked = (counts.outreach_active || 0) + (counts.responded || 0) + (counts.nurture || 0) + (counts.needs_you || 0)

  return (
    <EvoApp world="acquisition">
      <Hero
        scene="street"
        eyebrow="Opportunity Discovery"
        title="Discovery Inbox"
        sub={ev('Everything EvoSense found, why it matters, and what happens next.')}
        quote="Every property has a story. We find the ones worth telling."
        meta={data ? [
          { label: <><b>{allCount}</b> evaluated</> },
          counts.needs_you ? { label: <><b>{counts.needs_you}</b> need you</>, tone: 'paused' } : null,
          { label: <><b>{worked}</b> in conversation</> },
        ] : null}
        actions={<Link className="evo-btn evo-btn--secondary" to="/wholesale/evosense/strategies">Strategies</Link>}
      />
      <Alert>{error}</Alert>

      <Panel flush>
        <div className="evo-bucketnav evo-bucketnav--tabs" role="group" aria-label="Filter by state">
          <button type="button" className="evo-bucketbtn" aria-pressed={!bucket} onClick={() => set('bucket', '')}>
            All Properties <span className="evo-seg__n">({allCount ?? '…'})</span>
          </button>
          {orderedBuckets.filter((b) => b.count || bucket === b.key).map((b) => (
            <button key={b.key} type="button" className="evo-bucketbtn" aria-pressed={bucket === b.key}
                    onClick={() => set('bucket', b.key)}>
              {statusLabel(b.key)} <span className="evo-seg__n">({b.count})</span>
            </button>
          ))}
        </div>
        <div className="evo-filterbar">
          <form role="search" onSubmit={(e) => { e.preventDefault(); set('q', q.trim()) }} style={{ flex: 1, minWidth: 220, display: 'flex' }}>
            <label className="evo-search">
              <span className="evo-sr">Search address, owner, parcel, city or ZIP</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
              <input className="evo-input" type="search" placeholder="Search address, owner, parcel, city or ZIP"
                     value={q} onChange={(e) => setQ(e.target.value)} />
            </label>
          </form>
          <label><span className="evo-sr">Strategy</span>
            <select className="evo-select" value={strategy} onChange={(e) => set('strategy', e.target.value)}>
              <option value="">All strategies</option>
              {(data?.strategies || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label><span className="evo-sr">Signal</span>
            <select className="evo-select" value={signal.includes(',') ? '__many' : signal} onChange={(e) => set('signal', e.target.value === '__many' ? signal : e.target.value)}>
              <option value="">Any signal</option>
              {signal.includes(',') ? <option value="__many">{signal.split(',').length} signals (all of)</option> : null}
              {(data?.signal_types || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
          <label><span className="evo-sr">County</span>
            <select className="evo-select" value={county} onChange={(e) => set('county', e.target.value)}>
              <option value="">Any county</option>
              {(data?.counties || []).map((c) => <option key={c} value={c}>{c} County</option>)}
            </select>
          </label>
          <label><span className="evo-sr">Source</span>
            <select className="evo-select" value={source} onChange={(e) => set('source', e.target.value)}>
              <option value="">Any source</option>
              {(data?.sources || []).map((c) => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
            </select>
          </label>
          <label><span className="evo-sr">Minimum opportunity</span>
            <select className="evo-select" value={minScore} onChange={(e) => set('min_score', e.target.value)}>
              <option value="">Any score</option>
              {['30', '45', '60', '75'].map((v) => <option key={v} value={v}>{v}+ opportunity</option>)}
            </select>
          </label>
          <label className="evo-small" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={archived} onChange={(e) => set('archived', e.target.checked ? '1' : '')} />
            Rolled-back pilot
          </label>
          <label><span className="evo-sr">Category</span>
            <select className="evo-select" value={category} onChange={(e) => set('category', e.target.value)}
                    data-testid="inbox-category">
              <option value="">All opportunities</option>
              {(data?.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label} ({c.count})</option>)}
            </select>
          </label>
          <label><span className="evo-sr">Economics</span>
            <select className="evo-select" value={econ} onChange={(e) => set('economics', e.target.value)}>
              <option value="">Any economics</option>
              <option value="supportable">Spread supportable</option>
              <option value="needs_underwriting">Needs underwriting</option>
            </select>
          </label>
          <label><span className="evo-sr">Evidence confidence</span>
            <select className="evo-select" value={conf} onChange={(e) => set('confidence', e.target.value)}>
              <option value="">Any confidence</option>
              <option value="verified">Verified evidence</option>
              <option value="partial">Partial evidence</option>
              <option value="insufficient">Insufficient evidence</option>
            </select>
          </label>
          <label><span className="evo-sr">Show</span>
            <select className="evo-select" value={top} onChange={(e) => set('top', e.target.value)} data-testid="inbox-top">
              <option value="">All (paged)</option>
              <option value="10">Top 10</option>
              <option value="25">Top 25</option>
              <option value="100">Top 100</option>
            </select>
          </label>
          <label><span className="evo-sr">Sort</span>
            <select className="evo-select" value={sort} onChange={(e) => set('sort', e.target.value)}>
              <option value="priority">Overall priority</option>
              <option value="urgency">Most urgent</option>
              <option value="spread">Largest supported spread</option>
              <option value="confidence">Strongest evidence</option>
              <option value="engagement">Seller engagement</option>
              <option value="deadline">Soonest sale date</option>
              <option value="next_due">Next follow-up due</option>
              <option value="last_activity">Latest activity</option>
              <option value="opportunity">Highest seller opportunity</option>
              <option value="intent">Highest seller intent</option>
              <option value="contact">Best contact</option>
              <option value="newest">Newest</option>
              <option value="value_low">Lowest tax value</option>
              <option value="value_high">Highest tax value</option>
              <option value="owned_longest">Owned longest</option>
              <option value="oldest">Oldest built</option>
              <option value="largest">Largest</option>
              <option value="address">Address A–Z</option>
            </select>
          </label>
          <button type="button" className={`evo-btn evo-btn--sm ${showMore ? 'evo-btn--secondary' : 'evo-btn--ghost'}`}
                  aria-expanded={showMore} onClick={() => setShowMore(!showMore)}>
            More filters{activeExtra ? ` (${activeExtra})` : ''}
          </button>
        </div>
        {showMore ? (
          <MoreFilters key={moreKey} params={params} data={data}
                       onApply={(vals) => setMany(vals)}
                       onClear={() => { setMany({ ...Object.fromEntries(EXTRA.map(([k]) => [k, ''])), signal: '' }); setMoreKey((n) => n + 1) }} />
        ) : null}

        {loading && !data ? <div style={{ padding: 20 }}><Skeleton rows={6} height={28} /></div> : null}
        {data && !data.items.length ? (
          <Empty title={bucket ? 'Nothing in this state' : 'No properties match'}>
            {bucket ? 'No property is in this state right now.' : 'Try a different search or filter — or let the next hunt run.'}
          </Empty>
        ) : null}
        {data && data.items.length ? (
          <>
            <div className="evo-table-wrap">
              <table className="evo-table evo-table--cards">
                <caption className="evo-sr">Discovered properties</caption>
                <thead>
                  <tr>
                    {sort === 'priority' ? <th scope="col" className="is-num">#</th> : null}
                    <th scope="col">Property</th>
                    <th scope="col" className="is-num" title="Documented blend of seller opportunity, urgency, economics and engagement - not a probability">Priority</th>
                    <th scope="col" className="is-num">Opportunity</th>
                    <th scope="col">Urgency</th>
                    <th scope="col">Economics</th>
                    <th scope="col" className="is-num">Contact</th>
                    <th scope="col" className="is-num">Intent</th>
                    <th scope="col">Signals</th>
                    <th scope="col">Status · next action</th>
                    <th scope="col">Found</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p, idx) => (
                    <tr key={p.id} className={`is-link${p.status === 'needs_you' ? ' is-attention' : ''}`}
                        onClick={() => navigate(`/wholesale/evosense/property/${p.id}`)}>
                      {sort === 'priority' ? <td className="is-num" data-label="Rank">{(top ? 0 : offset) + idx + 1}</td> : null}
                      <td className="is-lead" data-label="">
                        <div className="evo-prop">
                          <PropertyThumb address={p.address} />
                          <div className="evo-prop__text">
                            <Link className="evo-prop__addr" to={`/wholesale/evosense/property/${p.id}`}
                                  onClick={(e) => e.stopPropagation()}>{p.address}{p.unit ? ` #${p.unit}` : ''}</Link>
                            <span className="evo-prop__sub">
                              {[p.city || (p.county ? `${p.county} County` : null), p.state].filter(Boolean).join(', ')} {p.zip_code}
                              {p.appraisal ? ` · ${moneyK(p.appraisal.value)} tax value` : p.estimated_value ? ` · ${moneyK(p.estimated_value)} est.` : ''}
                              {p.equity_pct !== null && p.equity_pct !== undefined ? ` · ${p.equity_pct}% equity` : ''}
                            </span>
                            <span className="evo-prop__sub"><TypeLabel pc={p.property_class} /></span>
                            {(p.bedrooms || p.square_feet || p.year_built || p.ownership_years != null) ? (
                              <span className="evo-prop__sub">
                                {[p.bedrooms ? `${p.bedrooms} bd` : null, p.bathrooms ? `${p.bathrooms} ba` : null,
                                  p.square_feet ? `${Number(p.square_feet).toLocaleString()} sq ft` : null,
                                  p.year_built ? `built ${p.year_built}` : null,
                                  p.ownership_years != null ? `owned ${p.ownership_years} yrs` : null].filter(Boolean).join(' · ')}
                              </span>
                            ) : null}
                            {((mixed && p.is_test) || p.has_conflicts) ? (
                              <span className="evo-chips" style={{ marginTop: 5 }}>
                                {mixed && p.is_test ? <SandboxTag /> : null}
                                {p.has_conflicts ? <Tag kind="danger" title="Sources disagree about a fact">Conflict</Tag> : null}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </td>
                      <td className="is-num" data-label="Priority" title={p.opportunity?.priority_why || ''}>
                        {p.opportunity?.priority != null ? <b>{p.opportunity.priority}</b> : <span className="evo-muted">—</span>}
                        {p.opportunity?.confidence ? (
                          <span className="evo-small evo-muted" style={{ display: 'block' }}>{p.opportunity.confidence} evidence</span>
                        ) : null}
                      </td>
                      <td className="is-num" data-label="Opportunity"><Ring kind="opportunity" value={p.opportunity_score} /></td>
                      <td data-label="Urgency">
                        {p.opportunity && p.opportunity.urgency ? (
                          <span className="evo-small">
                            <b>{p.opportunity.urgency_label}</b>
                            {p.opportunity.deadline ? <span className="evo-muted" style={{ display: 'block' }}>sale {p.opportunity.deadline}</span> : null}
                          </span>
                        ) : <span className="evo-muted evo-small">none known</span>}
                      </td>
                      <td data-label="Economics" title={(p.opportunity?.economics_needs || []).join(' · ')}>
                        {p.opportunity?.economics_status === 'supportable' || p.opportunity?.economics_status === 'partial'
                          ? <span className="evo-small">{p.opportunity.economics_headline}</span>
                          : <Tag kind="estimate" title={(p.opportunity?.economics_needs || []).join(' · ')}>Needs underwriting</Tag>}
                      </td>
                      <td className="is-num" data-label="Contact"><Num kind="contact" value={p.contact_confidence} /></td>
                      <td className="is-num" data-label="Intent"><Num kind="intent" value={p.seller_intent} /></td>
                      <td data-label="Signals"><Chips items={p.opportunity?.categories?.length ? p.opportunity.categories.map((c) => c.label) : p.signals} max={3} /></td>
                      <td data-label="Status">
                        <span className="evo-statusnext">
                          <Status status={p.status} />
                          <span className="evo-next">{p.opportunity?.next_action ? (
                            <span title={p.opportunity.next_action.why}>{p.opportunity.next_action.label}</span>
                          ) : (p.next_action || '—')}</span>
                        </span>
                        {p.next_step ? (
                          <Link className={`evo-nextstep evo-nextstep--${p.next_step.tone || 'default'}`}
                                data-testid="inbox-next-step"
                                to={p.next_step.href || `/wholesale/evosense/property/${p.id}#actions`}
                                title={p.next_step.reason || 'Open the property to do this'}
                                onClick={(e) => e.stopPropagation()}>
                            {p.next_step.label} →
                          </Link>
                        ) : null}
                      </td>
                      <td data-label="Found" className="evo-nowrap">{shortDate(p.discovered_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {top ? (
              <div className="evo-pager"><span>Top {data.items.length} of {data.total} by {sort === 'priority' ? 'overall priority' : 'this sort'}</span></div>
            ) : (
            <div className="evo-pager">
              <span>{offset + 1}–{offset + data.items.length} of {data.total}</span>
              <span style={{ display: 'flex', gap: 8 }}>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={offset === 0}
                        onClick={() => set('offset', String(Math.max(0, offset - PAGE)))}>Previous</button>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={offset + PAGE >= data.total}
                        onClick={() => set('offset', String(offset + PAGE))}>Next</button>
              </span>
            </div>
            )}
            {data.weights ? (
              <p className="evo-small evo-muted" style={{ padding: '0 20px 14px' }}>
                Priority is a rules-based blend - seller opportunity {data.weights.weights.seller_opportunity}%,
                urgency {data.weights.weights.urgency}%, economics {data.weights.weights.economics}%,
                engagement {data.weights.weights.engagement}%. A dimension that is unknown is left out, never counted as zero.
                It is not a probability of contract or closing.
              </p>
            ) : null}
          </>
        ) : null}
      </Panel>
    </EvoApp>
  )
}
