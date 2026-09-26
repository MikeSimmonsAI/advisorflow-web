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
import { errText } from '../wsShared'
import {
  Alert, Chips, Empty, EvoApp, Hero, Num, Panel, PropertyThumb, Ring, SandboxTag,
  Skeleton, Status, Tag, moneyK, shortDate, statusLabel,
} from '../ds/ds'
import '../ds/evo-pages.css'

const PAGE = 50
// The order an operator scans the buckets in: what needs them first.
const PRIMARY = ['needs_you', 'responded', 'outreach_active', 'ready_for_outreach', 'contact_found',
  'high_opportunity', 'needs_enrichment', 'waiting_for_data', 'budget_blocked', 'needs_review',
  'nurture', 'promoted', 'suppressed', 'closed_out', 'low_opportunity', 'new']

export default function EvoInbox() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const bucket = params.get('bucket') || ''
  const strategy = params.get('strategy') || ''
  const signal = params.get('signal') || ''
  const sort = params.get('sort') || 'opportunity'
  const offset = Number(params.get('offset') || 0)
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
      const p = new URLSearchParams({ limit: PAGE, offset, sort })
      if (bucket) p.set('bucket', bucket)
      if (strategy) p.set('strategy_id', strategy)
      if (signal) p.set('signal', signal)
      if (county) p.set('county', county)
      if (source) p.set('source', source)
      if (minScore) p.set('min_score', minScore)
      if (archived) p.set('archived', 'true')
      if (params.get('q')) p.set('q', params.get('q'))
      setData(await api.get('/wholesale/evosense/inbox?' + p.toString()))
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [bucket, strategy, signal, sort, offset, params, county, source, minScore, archived])
  useEffect(() => { load() }, [load])

  function set(k, v) {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v); else next.delete(k)
    if (k !== 'offset') next.delete('offset')
    setParams(next)
  }

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
        sub="Everything EvoSense found, why it matters, and what happens next."
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
              <span className="evo-sr">Search address, city or ZIP</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
              <input className="evo-input" type="search" placeholder="Search address, city or ZIP"
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
            <select className="evo-select" value={signal} onChange={(e) => set('signal', e.target.value)}>
              <option value="">Any signal</option>
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
          <label><span className="evo-sr">Sort</span>
            <select className="evo-select" value={sort} onChange={(e) => set('sort', e.target.value)}>
              <option value="opportunity">Highest opportunity</option>
              <option value="intent">Highest seller intent</option>
              <option value="contact">Best contact</option>
              <option value="newest">Newest</option>
            </select>
          </label>
        </div>

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
                    <th scope="col">Property</th>
                    <th scope="col" className="is-num">Opportunity</th>
                    <th scope="col" className="is-num">Contact</th>
                    <th scope="col" className="is-num">Intent</th>
                    <th scope="col">Signals</th>
                    <th scope="col">Status · next action</th>
                    <th scope="col">Found</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <tr key={p.id} className={`is-link${p.status === 'needs_you' ? ' is-attention' : ''}`}
                        onClick={() => navigate(`/wholesale/evosense/property/${p.id}`)}>
                      <td className="is-lead" data-label="">
                        <div className="evo-prop">
                          <PropertyThumb address={p.address} />
                          <div className="evo-prop__text">
                            <Link className="evo-prop__addr" to={`/wholesale/evosense/property/${p.id}`}
                                  onClick={(e) => e.stopPropagation()}>{p.address}{p.unit ? ` #${p.unit}` : ''}</Link>
                            <span className="evo-prop__sub">
                              {[p.city, p.state].filter(Boolean).join(', ')} {p.zip_code}
                              {p.estimated_value ? ` · ${moneyK(p.estimated_value)}` : ''}
                              {p.equity_pct !== null && p.equity_pct !== undefined ? ` · ${p.equity_pct}% equity` : ''}
                            </span>
                            {((mixed && p.is_test) || p.has_conflicts) ? (
                              <span className="evo-chips" style={{ marginTop: 5 }}>
                                {mixed && p.is_test ? <SandboxTag /> : null}
                                {p.has_conflicts ? <Tag kind="danger" title="Sources disagree about a fact">Conflict</Tag> : null}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </td>
                      <td className="is-num" data-label="Opportunity"><Ring kind="opportunity" value={p.opportunity_score} /></td>
                      <td className="is-num" data-label="Contact"><Num kind="contact" value={p.contact_confidence} /></td>
                      <td className="is-num" data-label="Intent"><Num kind="intent" value={p.seller_intent} /></td>
                      <td data-label="Signals"><Chips items={p.signals} max={2} /></td>
                      <td data-label="Status">
                        <span className="evo-statusnext">
                          <Status status={p.status} />
                          <span className="evo-next">{p.next_action || '—'}</span>
                        </span>
                      </td>
                      <td data-label="Found" className="evo-nowrap">{shortDate(p.discovered_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="evo-pager">
              <span>{offset + 1}–{offset + data.items.length} of {data.total}</span>
              <span style={{ display: 'flex', gap: 8 }}>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={offset === 0}
                        onClick={() => set('offset', String(Math.max(0, offset - PAGE)))}>Previous</button>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={offset + PAGE >= data.total}
                        onClick={() => set('offset', String(offset + PAGE))}>Next</button>
              </span>
            </div>
          </>
        ) : null}
      </Panel>
    </EvoApp>
  )
}
