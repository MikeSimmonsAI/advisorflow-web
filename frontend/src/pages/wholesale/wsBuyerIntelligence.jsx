/* BUYER INTELLIGENCE - the one Buyers screen: Find buyers, Qualify buyers, Match deals.
 *
 * Every number comes from /wholesale/buyer-intel (real rows in this workspace).
 * Three separate measurements are always shown apart and never blended:
 *   match score (one property vs a buy box), verification (evidence), readiness.
 * Enrichment is RESEARCH progress, not buyer quality. Nothing here sends a
 * message or buys a lookup: phone/email lookups go through the existing
 * approval drawer, and deals go to buyers only through the disposition desk.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { Alert, Drawer, Empty, Metric, Metrics, Panel, Ring, Search, Seg, Skeleton, Tag, TabBar } from './ds/ds'
import { errText } from './wsShared'
import { BuyerImportWizard, BuyerNetworkPanel } from './wsBuyerNetwork'
import DealRoomTab from './wsDealRoom'
import './wsBuyerIntelligence.css'

const BI = '/wholesale/buyer-intel'
const BADGE_TONE = {
  DEAL_READY_BUYER: 'live', ACTIVE_BUYER: 'live', BUY_BOX_CONFIRMED: 'info', CONTACT_VERIFIED: 'info',
  PURCHASE_HISTORY_VERIFIED: 'info', DISCOVERED: undefined, INACTIVE: undefined, DO_NOT_CONTACT: 'danger',
}
const READY_TONE = { DEAL_READY: 'live', ACTIVE: 'live', SIGNS_OF_ACTIVITY: 'estimate', UNKNOWN: undefined,
  INACTIVE: undefined, DO_NOT_CONTACT: 'danger' }
const STAGE_TONE = { COMPLETE: 'live', PARTIAL: 'estimate', NEEDS_REVIEW: 'danger', NOT_STARTED: undefined,
  UNAVAILABLE: undefined, NOT_APPLICABLE: undefined }
const SEG_LABEL = { single_family: 'Single-family', multifamily: 'Multifamily', land: 'Land', commercial: 'Commercial',
  industrial: 'Industrial', mixed_use: 'Mixed-use', portfolio: 'Portfolio', other: 'Other' }
const words = (s) => (s || '').toString().replace(/_/g, ' ').toLowerCase()
const cap = (s) => { const w = words(s); return w ? w[0].toUpperCase() + w.slice(1) : '' }
const usd = (v) => (v === null || v === undefined ? '—' : '$' + Math.round(Number(v)).toLocaleString('en-US'))
const day = (iso) => (iso ? new Date(iso.length <= 10 ? iso + 'T12:00:00Z' : iso).toLocaleDateString('en-US',
  { month: 'short', day: 'numeric', year: 'numeric' }) : '—')

function Meter({ value, label }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  return (
    <span className="bi-meter" role="img" aria-label={`${label || 'Enrichment'}: ${v}%`} title={`${label || 'Research completeness'}: ${v}%`}>
      <span className="bi-meter__bar"><span className="bi-meter__fill" style={{ width: v + '%' }} /></span>
      <span className="bi-meter__n">{v}%</span>
    </span>
  )
}

function SegmentChips({ segments }) {
  const keys = Object.keys(segments || {})
  if (!keys.length) return <span className="evo-muted evo-small">—</span>
  return (
    <span className="evo-chips">
      {keys.map((k) => (
        <Tag key={k} title={`${SEG_LABEL[k] || k}: ${(segments[k].basis || []).join(', ')}${segments[k].subs.length ? ' · ' + segments[k].subs.map(words).join(', ') : ''}`}>
          {SEG_LABEL[k] || cap(k)}
        </Tag>
      ))}
    </span>
  )
}

// ── page ────────────────────────────────────────────────────────────────────
export default function BuyerIntelligence({ legacy, reloadKey }) {
  const [tab, setTab] = useState(() => {
    try { return sessionStorage.getItem('bi.tab') || 'qualify' } catch { return 'qualify' }
  })
  const [includeTest, setIncludeTest] = useState(false)
  const [summary, setSummary] = useState(null)
  const [tax, setTax] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [openBuyer, setOpenBuyer] = useState(() => {
    try { return new URLSearchParams(window.location.search).get('buyer') } catch { return null }
  })
  const [bump, setBump] = useState(0)

  useEffect(() => { try { sessionStorage.setItem('bi.tab', tab) } catch { /* private window */ } }, [tab])
  const refresh = useCallback(() => setBump((x) => x + 1), [])
  useEffect(() => {
    let alive = true
    setError(null)
    api.get(`${BI}/summary?include_test=${includeTest}`).then((s) => { if (alive) setSummary(s) })
      .catch((e) => { if (alive) setError(errText(e)) })
    return () => { alive = false }
  }, [includeTest, bump, reloadKey])
  useEffect(() => { api.get(`${BI}/taxonomy`).then(setTax).catch(() => setTax(null)) }, [])

  const cards = (summary && summary.cards) || []
  return (
    <>
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      <Metrics label="Buyer intelligence summary">
        {(cards.length ? cards : [{ key: 'a', label: 'Buyers discovered' }, { key: 'b', label: 'Verified active buyers' },
          { key: 'c', label: 'Buyers to qualify' }, { key: 'd', label: 'Deals with buyer matches' }]).map((c, i) => (
          <Metric key={c.key} label={c.label} value={summary ? c.value : null} sub={c.detail}
                  tone={['primary', 'success', 'info', 'violet'][i]} />
        ))}
      </Metrics>
      <div className="bi-tabs"><TabBar label="Buyer activities" value={tab} onChange={setTab}
              items={[{ key: 'find', label: 'Find buyers' }, { key: 'qualify', label: 'Qualify buyers' },
                      { key: 'match', label: 'Match deals' }, { key: 'dealroom', label: 'Deal Room' }]}
              actions={<label className="evo-btn evo-btn--ghost evo-btn--sm" style={{ cursor: 'pointer' }}>
                <input type="checkbox" checked={includeTest} onChange={(e) => setIncludeTest(e.target.checked)}
                       style={{ margin: 0 }} /> Sandbox / practice records</label>} /></div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'qualify' ? <QualifyTab tax={tax} includeTest={includeTest} bump={bump + (reloadKey || 0)}
                                         onOpen={setOpenBuyer} /> : null}
        {tab === 'find' ? <FindTab legacy={legacy} includeTest={includeTest} bump={bump} onOpen={setOpenBuyer}
                                   onChanged={(msg) => { if (msg) setNotice(msg); refresh() }} /> : null}
        {tab === 'dealroom' ? <DealRoomTab includeTest={includeTest} onOpenBuyer={setOpenBuyer} /> : null}
        {tab === 'match' ? <MatchTab includeTest={includeTest} onOpen={setOpenBuyer}
                                     onChanged={(msg) => { if (msg) setNotice(msg); refresh() }} /> : null}
      </div>
      {openBuyer ? <BuyerDetail id={openBuyer} legacy={legacy} onClose={() => setOpenBuyer(null)}
                                onChanged={(msg) => { if (msg) setNotice(msg); refresh() }}
                                onGoFind={() => { setOpenBuyer(null); setTab('find') }} /> : null}
    </>
  )
}

// ── Qualify: the main list ──────────────────────────────────────────────────
const MORE = [
  ['verification', 'Verification', [['', 'Any'], ['PURCHASE_HISTORY_VERIFIED', 'Purchase history verified'],
    ['CONTACT_VERIFIED', 'Contact verified'], ['BUY_BOX_CONFIRMED', 'Buy box confirmed'], ['ACTIVE_BUYER', 'Active buyer'],
    ['DEAL_READY_BUYER', 'Deal-ready'], ['INACTIVE', 'Inactive'], ['DO_NOT_CONTACT', 'Do not contact']]],
  ['readiness', 'Readiness', [['', 'Any'], ['DEAL_READY', 'Deal-ready'], ['ACTIVE', 'Active'],
    ['SIGNS_OF_ACTIVITY', 'Signs of activity'], ['UNKNOWN', 'Not assessed'], ['INACTIVE', 'Inactive']]],
  ['ai', 'AI qualification', [['', 'Any'], ['none', 'Not started'], ['open', 'In progress'],
    ['handed_off', 'With a person'], ['completed', 'Completed'], ['stopped', 'Stopped']]],
  ['contactable', 'Contactable', [['', 'Any'], ['yes', 'Has phone or email'], ['no', 'No contact yet']]],
  ['recent', 'Recent purchases', [['', 'Any'], ['12m', 'In the last 12 months'], ['24m', 'In the last 24 months']]],
  ['funding', 'Funding evidence', [['', 'Any'], ['documented', 'Documented'], ['stated', 'Stated only'], ['none', 'None']]],
  ['match', 'Deal matching', [['', 'Any'], ['matched', 'Matched to a deal'], ['unmatched', 'No match yet']]],
  ['interaction', 'Last interaction', [['', 'Any'], ['30d', 'Last 30 days'], ['90d', 'Last 90 days'], ['never', 'Never']]],
  ['strategy', 'Strategy', [['', 'Any'], ['flip', 'Flip'], ['rental', 'Rental / hold'], ['brrrr', 'BRRRR'],
    ['value_add', 'Value-add'], ['development', 'Development'], ['land', 'Land']]],
]
const RANGE = [['state', 'State'], ['county', 'County'], ['city', 'City'], ['zip', 'ZIP'], ['price_min', 'Price from'],
  ['price_max', 'Price to'], ['units_min', 'Units from'], ['units_max', 'Units to'], ['acres_min', 'Acres from'],
  ['acres_max', 'Acres to']]

function QualifyTab({ tax, includeTest, bump, onOpen }) {
  const [segment, setSegment] = useState('all')
  const [sub, setSub] = useState('')
  const [q, setQ] = useState('')
  const [f, setF] = useState({})
  const [showMore, setShowMore] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const params = useMemo(() => {
    const p = new URLSearchParams({ segment, include_test: String(includeTest), limit: '200' })
    if (sub) p.set('sub', sub)
    if (q) p.set('q', q)
    Object.entries(f).forEach(([k, v]) => { if (v !== '' && v !== undefined && v !== null) p.set(k, v) })
    return p.toString()
  }, [segment, sub, q, f, includeTest])

  const load = useCallback(() => {
    setLoading(true); setError(null)
    api.get(`${BI}/buyers?${params}`).then(setData).catch((e) => setError(errText(e))).finally(() => setLoading(false))
  }, [params])
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [load, bump])

  const counts = (data && data.segment_counts) || {}
  const segs = (tax && tax.segments) || [{ key: 'all', label: 'All buyers' }]
  const subs = (tax && tax.subsegments && tax.subsegments[segment]) || []
  const active = Object.values(f).filter((v) => v !== '' && v !== undefined).length
  return (
    <Panel flush>
      <div className="bi-segbar">
        <Seg label="Buyer segment" value={segment} onChange={(k) => { setSegment(k); setSub('') }}
             items={segs.map((s) => ({ key: s.key, label: s.label, count: counts[s.key] }))} />
        {subs.length ? (
          <Seg label="Sub-segment" value={sub} onChange={(k) => setSub(k === sub ? '' : k)}
               items={[{ key: '', label: 'All' }, ...subs.map((s) => ({ key: s.key, label: s.label }))]} />
        ) : null}
      </div>
      <div className="evo-filterbar">
        <Search value={q} onChange={setQ} placeholder="Search buyer name or ZIP" label="Search buyers" />
        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" aria-expanded={showMore}
                onClick={() => setShowMore((x) => !x)}>Filters{active ? ` (${active})` : ''}</button>
        {active ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setF({})}>Clear</button> : null}
      </div>
      {showMore ? (
        <div className="bi-filters">
          {MORE.map(([key, label, opts]) => (
            <label key={key} className="bi-filter"><span>{label}</span>
              <select className="evo-input" value={f[key] || ''} onChange={(e) => setF((x) => ({ ...x, [key]: e.target.value }))}>
                {opts.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select></label>
          ))}
          {RANGE.map(([key, label]) => (
            <label key={key} className="bi-filter"><span>{label}</span>
              <input className="evo-input" value={f[key] || ''} inputMode={/min|max/.test(key) ? 'decimal' : 'text'}
                     onChange={(e) => setF((x) => ({ ...x, [key]: e.target.value }))} /></label>
          ))}
        </div>
      ) : null}
      <Alert>{error ? <>{error} <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={load}>Retry</button></> : null}</Alert>
      {loading && !data ? <div style={{ padding: 20 }}><Skeleton rows={5} height={30} /></div> : null}
      {data && !data.buyers.length ? (
        <Empty title="No buyers in this view">
          {segment !== 'all' || active || q ? 'Nothing matches these filters - clear a filter or pick All buyers.'
            : 'Find buyers from the county records, import your buyer list, or add one by hand (Find buyers tab).'}
        </Empty>
      ) : null}
      {data && data.buyers.length ? (
        <div className="evo-table-wrap">
          <table className="evo-table evo-table--cards bi-table">
            <caption className="evo-sr">{data.total} buyers</caption>
            <thead><tr>
              <th scope="col">Buyer / investment group</th><th scope="col">Property segment</th>
              <th scope="col">Geography</th><th scope="col" className="is-num">Recent purchases</th>
              <th scope="col">Verification</th><th scope="col">Enrichment</th><th scope="col">AI conversation</th>
              <th scope="col">Next action</th>
            </tr></thead>
            <tbody>
              {data.buyers.map((b) => (
                <tr key={b.buyer_id} className="bi-row" tabIndex={0} onClick={() => onOpen(b.buyer_id)}
                    onKeyDown={(e) => { if (e.key === 'Enter') onOpen(b.buyer_id) }}>
                  <td className="is-lead" data-label="">
                    <span className="evo-strong">{b.name}</span>
                    <span className="evo-prop__sub">{b.has_phone || b.has_email ? [b.has_phone ? 'phone' : null, b.has_email ? 'email' : null].filter(Boolean).join(' · ') : 'no contact yet'}
                      {b.is_test ? ' · sandbox' : ''}</span>
                  </td>
                  <td data-label="Segment"><SegmentChips segments={b.segments} /></td>
                  <td data-label="Geography" className="evo-small">
                    {[...b.geography.counties.map((c) => c + ' Co.'), ...b.geography.cities].slice(0, 3).join(', ') || (b.geography.states.join(', ') || '—')}
                  </td>
                  <td data-label="Recent purchases" className="is-num" title={(b.purchases.lines || []).join(' · ')}>
                    {b.recent_purchases || (b.purchases.status === 'COUNT_ONLY' ? 'count only' : 0)}
                  </td>
                  <td data-label="Verification">
                    <Tag kind={BADGE_TONE[b.headline]}>{b.headline_label}</Tag>
                    <span className="evo-prop__sub">Readiness: {b.readiness.label}</span>
                  </td>
                  <td data-label="Enrichment"><Meter value={b.enrichment.percent} /></td>
                  <td data-label="AI" className="evo-small">{b.ai.status === 'none' ? 'Not started' : cap(b.ai.status)}{b.ai.mode ? ` (${b.ai.mode})` : ''}</td>
                  <td data-label="Next action" className="evo-small">{b.enrichment.next.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.buyers.length ? <p className="evo-muted evo-small" style={{ padding: 12 }}>Showing {data.buyers.length} of {data.total} - narrow the filters to see the rest.</p> : null}
        </div>
      ) : null}
    </Panel>
  )
}

// ── Find buyers ─────────────────────────────────────────────────────────────
function FindTab({ legacy, includeTest, bump, onOpen, onChanged }) {
  const [review, setReview] = useState(null)
  const [status, setStatus] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [importing, setImporting] = useState(false)

  const load = useCallback(() => {
    api.get(`${BI}/identity-review?include_test=${includeTest}`).then(setReview).catch((e) => setError(errText(e)))
    Promise.all(['dallas', 'tarrant'].map((c) => api.get(`${BI}/evidence/status?county=${c}`).catch(() => null)))
      .then(([d, t]) => setStatus({ dallas: d, tarrant: t }))
  }, [includeTest])
  useEffect(() => { load() }, [load, bump])
  useEffect(() => {
    const running = Object.values(status).some((s) => s && s.status && s.status.state === 'running')
    if (!running) return undefined
    const t = setTimeout(load, 15000)
    return () => clearTimeout(t)
  }, [status, load])

  async function refreshEvidence(county) {
    setBusy(county); setError(null)
    try {
      const r = await api.post(`${BI}/evidence/refresh`, { county })
      onChanged(r.started_scan ? r.message : `${r.deeds_added} recorded deed(s) attached to ${r.attached_buyers} buyer(s) from ${cap(county)} County records.`)
      load()
    } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  async function decide(id, decision) {
    setBusy(id); setError(null)
    try {
      await api.post(`${BI}/identity-review/${id}`, { decision })
      onChanged(decision === 'same' ? 'Linked as one investor - both records kept.' : 'Kept as separate investors.')
      load()
    } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  async function practice() {
    setBusy('practice'); setError(null)
    try {
      const r = await api.post(`${BI}/practice-buyer`, {})
      onChanged(`${r.name} created (sandbox, fictional contact details). Tick "Sandbox / practice records" to see it.`)
      onOpen(r.buyer_id)
    } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }

  return (
    <>
      <Alert>{error}</Alert>
      <div className="bi-grid">
        <Panel title="County records" hint="free public records">
          <p className="evo-small">Owners in the Dallas and Tarrant appraisal files who hold several properties and keep
            taking title - houses, multifamily, land, commercial and industrial. Each recent deed is saved as a recorded
            title transfer (Texas records show no price).</p>
          <div className="evo-actionbar">
            <button type="button" className="evo-btn evo-btn--primary" onClick={legacy.openFinder}>Find investors in county records</button>
          </div>
          <ul className="bi-list">
            {['dallas', 'tarrant'].map((c) => {
              const s = status[c]
              const st = s && s.status ? s.status.state : null
              return (
                <li key={c}>
                  <span><b>{cap(c)} County</b> · {s && s.generated_at ? `scanned ${day(s.generated_at)}` : 'not scanned yet'}
                    {s && !s.has_deed_records && s.generated_at ? ' · deed-level records not in that scan' : ''}
                    {st === 'running' ? ' · scanning now…' : st === 'failed' ? ' · last scan failed' : ''}</span>
                  <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === c || st === 'running'}
                          onClick={() => refreshEvidence(c)}>
                    {st === 'running' ? 'Scanning…' : 'Attach recorded deeds to my buyers'}</button>
                </li>
              )
            })}
          </ul>
          <p className="evo-muted evo-small">Collin, Denton, Ellis, Johnson, Kaufman and Rockwall: no free investor scan is
            connected yet (their files differ or refuse cloud servers). Paid sources - ATTOM, PropStream, Reonomy, Trepp -
            are not switched on and are never used without your approval.</p>
        </Panel>
        <Panel title="Your buyer lists">
          <p className="evo-small">Import your own buyer file (CSV or Excel). You review the duplicate check before anything is
            saved, and an import can be rolled back. What a file says about proof of funds or past purchases is kept as a claim.</p>
          <div className="evo-actionbar">
            <button type="button" className="evo-btn evo-btn--primary" onClick={() => setImporting(true)}>Import a buyer list</button>
            <button type="button" className="evo-btn evo-btn--secondary" onClick={legacy.openAdd}>+ Add a buyer</button>
            {legacy.unlinked ? <button type="button" className="evo-btn evo-btn--ghost" onClick={legacy.linkContacts}>
              Link {legacy.unlinked} to contacts</button> : null}
          </div>
          <p className="evo-small" style={{ marginTop: 12 }}>Testing the AI buyer qualifier? Create a practice buyer - a sandbox
            record with fictional contact details that nothing can reach.</p>
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'practice'} onClick={practice}>
            Create a practice buyer</button>
        </Panel>
      </div>
      <Panel title="Possible related investors" count={review ? review.items.length : null}
             hint="similar LLC names or a shared mailing address - never merged without you">
        {!review ? <Skeleton rows={2} /> : !review.items.length ? (
          <p className="evo-muted evo-small" style={{ padding: '0 4px' }}>Nothing to review.</p>
        ) : (
          <ul className="bi-list">
            {review.items.map((r) => (
              <li key={r.id}>
                <span><button type="button" className="evo-link" onClick={() => onOpen(r.buyer_id)}>{r.buyer_name}</button>
                  {' '}and <b>{r.candidate_name}</b><span className="evo-prop__sub">{r.reason}</span></span>
                <span className="evo-actionbar" style={{ flexWrap: 'nowrap' }}>
                  <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === r.id}
                          onClick={() => decide(r.id, 'same')}>Same investor</button>
                  <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === r.id}
                          onClick={() => decide(r.id, 'different')}>Different</button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <BuyerNetworkPanel includeTest={includeTest} refreshKey={bump} />
      {importing ? <BuyerImportWizard onClose={() => setImporting(false)} onDone={(msg) => { onChanged(msg); load() }} /> : null}
    </>
  )
}

// ── Match deals ─────────────────────────────────────────────────────────────
export function MatchResults({ subjectType, subjectId, onOpen, onChanged, compact }) {
  const [asking, setAsking] = useState('')
  const [res, setRes] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [why, setWhy] = useState(null)
  const [showOut, setShowOut] = useState(false)

  const run = useCallback(() => {
    if (!subjectId) return
    setError(null)
    api.post(`${BI}/match`, { subject_type: subjectType, subject_id: subjectId,
      asking_price: asking ? Number(String(asking).replace(/[^0-9.]/g, '')) : null })
      .then(setRes).catch((e) => setError(errText(e)))
  }, [subjectType, subjectId, asking])
  useEffect(() => { setRes(null); run() }, [subjectType, subjectId]) // eslint-disable-line react-hooks/exhaustive-deps

  async function act(kind, b) {
    setBusy(kind + (b ? b.buyer_id : '')); setError(null)
    try {
      if (kind === 'save') {
        await api.post(`${BI}/match/save`, { deal_id: res.subject.deal_id, buyer_id: b.buyer_id })
        onChanged && onChanged(`Match saved for ${b.name}.`)
      } else if (kind === 'qualify') {
        await api.post(`${BI}/buyers/${b.buyer_id}/request-qualification`, {})
        onChanged && onChanged(`${b.name} added to the work queue for qualification.`)
      } else if (kind === 'prepare') {
        const r = await api.post(`${BI}/match/prepare`, { evosense_id: res.subject.evosense_id })
        onChanged && onChanged(`Deal prepared for disposition review - ${r.matches_saved} buyer match(es) saved. Nothing was sent.`)
        window.location.assign(`/wholesale/deals/${r.deal_id}`)
        return
      }
      run()
    } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }

  if (!subjectId) return null
  const rows = res ? res.buyers.filter((b) => showOut || !b.disqualified) : []
  return (
    <div>
      <Alert>{error}</Alert>
      <div className="evo-filterbar">
        {res && !res.subject.deal_id ? (
          <label className="bi-filter" style={{ minWidth: 220 }}><span>Asking price to buyers (optional)</span>
            <input className="evo-input" inputMode="decimal" value={asking} placeholder="$ - leave blank if unknown"
                   onChange={(e) => setAsking(e.target.value)} onBlur={run} /></label>
        ) : null}
        {res ? <span className="evo-small evo-muted">{res.eligible} of {res.count} buyers fit · {res.price_note || ''}</span> : null}
        <label className="evo-btn evo-btn--ghost evo-btn--sm" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={showOut} onChange={(e) => setShowOut(e.target.checked)} style={{ margin: 0 }} /> Show non-fits</label>
        {res && res.subject.deal_id ? (
          <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/deals/${res.subject.deal_id}`}>
            Open disposition (approve outreach, track responses)</Link>
        ) : res && res.subject.evosense_id ? (
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy === 'prepare'}
                  onClick={() => act('prepare')}>Prepare deal package</button>
        ) : null}
      </div>
      {!res && !error ? <Skeleton rows={4} height={28} /> : null}
      {res && !rows.length ? <Empty title="No buyer fits this property yet">Buyers appear here when a buy box covers this
        property's type and area. {res.count ? 'Tick "Show non-fits" to see why the others do not.' : 'Add or find buyers first.'}</Empty> : null}
      {rows.length ? (
        <div className="evo-table-wrap">
          <table className="evo-table evo-table--cards bi-table">
            <caption className="evo-sr">Buyers for {res.subject.label}</caption>
            <thead><tr><th scope="col">Buyer</th><th scope="col">Match score</th><th scope="col">Verification · purchase history</th>
              <th scope="col">Readiness</th><th scope="col">Missing information · next action</th>
              <th scope="col"><span className="evo-sr">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.buyer_id}>
                  <td className="is-lead" data-label=""><button type="button" className="evo-link evo-strong" onClick={() => onOpen(b.buyer_id)}>{b.name}</button>
                    {b.buy_box ? <span className="evo-prop__sub">box: {b.buy_box.label || 'unnamed'}{b.buy_box.source ? ` (${words(b.buy_box.source)})` : ''}</span> : null}</td>
                  <td data-label="Match score">
                    <span className="bi-score"><Ring value={b.checked ? b.match_score : null} size="sm" title="Buyer match score" />
                      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" aria-expanded={why === b.buyer_id}
                              onClick={() => setWhy(why === b.buyer_id ? null : b.buyer_id)}>Why</button></span>
                    {b.disqualified ? <span className="evo-prop__sub"><Tag kind="danger">{b.disqualified_reason}</Tag></span> : null}
                    {why === b.buyer_id ? (
                      <ul className="bi-why">
                        {b.factors.filter((x) => x.weight || x.unknown).map((x, i) => (
                          <li key={i} className={x.matched === true ? 'is-yes' : x.matched === false ? 'is-no' : 'is-unknown'}>
                            {x.matched === true ? '✓' : x.matched === false ? '✗' : '?'} {x.detail}</li>
                        ))}
                      </ul>
                    ) : null}
                  </td>
                  <td data-label="Verification"><Tag kind={BADGE_TONE[(b.verification.badges || []).slice(-1)[0]]}>{b.verification.headline}</Tag>
                    <span className="evo-prop__sub">{b.verification.contact} · {b.purchase_history.label}{b.purchase_history.recent ? ` (${b.purchase_history.recent} in 24 mo)` : ''}</span></td>
                  <td data-label="Readiness" className="evo-small">{b.readiness.label}</td>
                  <td data-label="Missing" className="evo-small">{b.missing.length ? b.missing.slice(0, 2).join(' · ') : 'Nothing missing'}
                    <span className="evo-prop__sub"><b>Next:</b> {b.next_action.label}</span></td>
                  <td data-label="" className="is-right">
                    <span className="evo-actionbar" style={{ justifyContent: 'flex-end' }}>
                      {res.subject.can_save && !b.disqualified ? (
                        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={b.saved || busy === 'save' + b.buyer_id}
                                onClick={() => act('save', b)}>{b.saved ? 'Saved' : 'Save match'}</button>) : null}
                      {!compact ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => onOpen(b.buyer_id)}>Review buyer</button> : null}
                      {!b.disqualified && b.next_action.key === 'qualify' ? (
                        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === 'qualify' + b.buyer_id}
                                onClick={() => act('qualify', b)}>Request qualification</button>) : null}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="evo-muted evo-small" style={{ padding: 12 }}>{res.rules}</p>
        </div>
      ) : null}
    </div>
  )
}

function MatchTab({ includeTest, onOpen, onChanged }) {
  const [q, setQ] = useState('')
  const [cands, setCands] = useState(null)
  const [pick, setPick] = useState(null)
  const [demand, setDemand] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => {
      api.get(`${BI}/match-candidates?include_test=${includeTest}${q ? '&q=' + encodeURIComponent(q) : ''}`)
        .then(setCands).catch((e) => setError(errText(e)))
    }, 250)
    return () => clearTimeout(t)
  }, [q, includeTest])
  useEffect(() => { api.get(`${BI}/demand?include_test=${includeTest}`).then(setDemand).catch(() => setDemand(null)) }, [includeTest])
  return (
    <>
      <Alert>{error}</Alert>
      <div className="bi-grid bi-grid--match">
        <Panel title="Pick a property" hint="open deals, then discoveries in work order">
          <Search value={q} onChange={setQ} placeholder="Address, city or ZIP" label="Search properties" />
          {!cands ? <Skeleton rows={4} /> : !cands.items.length ? <p className="evo-muted evo-small">No open deals or urgent discoveries{q ? ' match that search' : ''}.</p> : (
            <ul className="bi-list bi-list--pick">
              {cands.items.map((c) => (
                <li key={c.type + c.id}>
                  <button type="button" className={`bi-pick${pick && pick.id === c.id ? ' is-on' : ''}`} onClick={() => setPick(c)}>
                    <b>{c.label || '(no address)'}</b>
                    <span className="evo-prop__sub">{c.type === 'deal' ? `Deal · ${words(c.stage)}` : `Discovery${c.work_priority ? ' · ' + words(c.work_priority) : ''}`}
                      {c.category ? ' · ' + words(c.category) : ''}{c.is_test ? ' · sandbox' : ''}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title={pick ? `Matching buyers - ${pick.label}` : 'Matching buyers'}>
          {pick ? <MatchResults subjectType={pick.type} subjectId={pick.id} onOpen={onOpen} onChanged={onChanged} />
            : <p className="evo-muted evo-small">Choose a property to score every buyer against it. Scores explain themselves;
              unknown facts are listed as missing, never counted against a buyer. Nothing is sent from here.</p>}
        </Panel>
      </div>
      <Panel title="Buyer demand" hint="what verified buyers want - suggested hunting criteria, read only">
        {!demand ? <Skeleton rows={2} /> : !demand.groups.length ? (
          <p className="evo-muted evo-small">No verified buyer demand yet. Confirm buy boxes or qualify buyers to see it here.</p>
        ) : (
          <ul className="bi-list">
            {demand.groups.map((g) => (
              <li key={g.segment}><span><b>{g.label}</b> · {g.buyers} buyer{g.buyers === 1 ? '' : 's'}
                <span className="evo-prop__sub">{g.counties.length ? g.counties.join(', ') : 'no counties stated'}
                  {g.suggested_hunt.price_min || g.suggested_hunt.price_max ? ` · ${usd(g.suggested_hunt.price_min)}–${usd(g.suggested_hunt.price_max)}` : ''}
                  {g.suggested_hunt.units_min || g.suggested_hunt.units_max ? ` · ${g.suggested_hunt.units_min || '?'}–${g.suggested_hunt.units_max || '?'} units` : ''}
                  {g.strategies.length ? ' · ' + g.strategies.map(words).join(', ') : ''}</span></span>
                <Link className="evo-btn evo-btn--ghost evo-btn--sm" to="/wholesale/evosense/strategies">Review hunts</Link></li>
            ))}
          </ul>
        )}
        {demand ? <p className="evo-muted evo-small">{demand.note}</p> : null}
      </Panel>
    </>
  )
}

// ── buyer detail ────────────────────────────────────────────────────────────
function BuyerDetail({ id, legacy, onClose, onChanged, onGoFind }) {
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [sec, setSec] = useState('overview')
  const [conv, setConv] = useState(null)

  const load = useCallback(() => {
    setError(null)
    api.get(`${BI}/buyers/${id}`).then(setD).catch((e) => setError(errText(e)))
  }, [id])
  useEffect(() => { load() }, [load])

  async function call(key, fn, msg) {
    setBusy(key); setError(null)
    try { const r = await fn(); if (r && r.buyer_id && r.assessment) setD(r); else load(); if (msg) onChanged(msg) }
    catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  const facts = (body, msg) => call('facts', () => api.patch(`${BI}/buyers/${id}/facts`, body), msg)
  async function startConv(mode) {
    setBusy('conv'); setError(null)
    try { const c = await api.post(`${BI}/buyers/${id}/qualify`, { mode }); setConv(c.id); setSec('ai'); load() }
    catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  async function nextStep(n) {
    if (n.key === 'evidence_refresh') {
      return call('next', () => api.post(`${BI}/evidence/refresh`, { county: n.county }), 'County deed records requested.')
    }
    if (n.key === 'identity_review') { onGoFind(); return null }
    if (n.key === 'qualify') { setSec('ai'); return null }
    if (n.key === 'find_contact') { legacy.lookup([id]); return null }
    if (n.key === 'match') { onClose(); return null }
    return null
  }

  const a = d && d.assessment
  const practiceable = d && d.is_test && (d.source === 'practice')
  return (
    <Drawer open wide onClose={onClose} title={d ? d.name : 'Buyer'}
            sub={d ? `${d.badges.map((b) => cap(b)).join(' · ')}` : 'Loading…'}
            footer={d ? (
              <div className="evo-actionbar">
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => legacy.edit(id)}>Edit details</button>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => legacy.boxes(id)}>Edit buy boxes</button>
                {!d.do_not_contact ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => legacy.lookup([id])}
                        title="Paid phone/email lookup - you approve the cost first">Get phone &amp; email</button> : null}
                {!d.do_not_contact ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === 'dnc'}
                        onClick={() => call('dnc', () => api.post(`${BI}/buyers/${id}/opt-out`, { reason: 'Asked not to be contacted' }), 'Marked do-not-contact.')}>Do not contact</button> : null}
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => legacy.remove(id, d.name)}>Delete</button>
              </div>
            ) : null}>
      <Alert>{error ? <>{error} <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={load}>Retry</button></> : null}</Alert>
      {!d ? <Skeleton rows={6} /> : (
        <>
          <div className="bi-head">
            <div><span className="evo-small evo-muted">Enrichment (research done)</span><Meter value={a.enrichment.percent} /></div>
            <div><span className="evo-small evo-muted">Verification</span><Tag kind={BADGE_TONE[d.headline]}>{d.headline_label}</Tag></div>
            <div><span className="evo-small evo-muted">Readiness</span><Tag kind={READY_TONE[a.readiness.level]}>{a.readiness.label}</Tag></div>
            <div className="bi-head__next"><span className="evo-small evo-muted">Next research action</span>
              <span>{a.enrichment.next.label}</span>
              {a.enrichment.next.one_click ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy === 'next'}
                      onClick={() => nextStep(a.enrichment.next)}>Do it</button> : null}</div>
          </div>
          <Seg label="Buyer sections" value={sec} onChange={setSec}
               items={[{ key: 'overview', label: 'Overview' }, { key: 'boxes', label: 'Buy boxes', count: d.buy_boxes.length },
                       { key: 'evidence', label: 'Purchase evidence', count: d.evidence.length },
                       { key: 'enrichment', label: 'Enrichment' }, { key: 'ai', label: 'AI conversations', count: d.conversations.length },
                       { key: 'deals', label: 'Matched deals', count: d.matched_deals.length }]} />
          <div className="bi-sec">
            {sec === 'overview' ? <Overview d={d} a={a} busy={busy} facts={facts} /> : null}
            {sec === 'boxes' ? <Boxes d={d} busy={busy} confirm={(boxId) => call('box', () => api.post(`${BI}/buyers/${id}/buy-boxes/${boxId}/confirm`, {}), 'Buy box confirmed.')} /> : null}
            {sec === 'evidence' ? <Evidence d={d} busy={busy} add={(body) => call('ev', () => api.post(`${BI}/buyers/${id}/evidence`, body), 'Purchase recorded.')} /> : null}
            {sec === 'enrichment' ? <Stages a={a} /> : null}
            {sec === 'ai' ? <Conversations d={d} conv={conv} setConv={setConv} start={startConv} busy={busy}
                                           practiceable={practiceable} onChanged={() => { load(); onChanged() }} /> : null}
            {sec === 'deals' ? (
              !d.matched_deals.length ? <p className="evo-muted evo-small">No saved matches yet. Use Match deals to score a property.</p> : (
                <ul className="bi-list">{d.matched_deals.map((m) => (
                  <li key={m.deal_id}><span><Link className="evo-link" to={`/wholesale/deals/${m.deal_id}`}>{m.address || 'Deal'}</Link>
                    <span className="evo-prop__sub">{words(m.stage)} · match {m.score} · {day(m.computed_at)}</span></span></li>))}</ul>)
            ) : null}
          </div>
        </>
      )}
    </Drawer>
  )
}

function Overview({ d, a, busy, facts }) {
  const o = d.overview
  const [dm, setDm] = useState(o.decision_maker || '')
  return (
    <div className="bi-cols">
      <div>
        <h3 className="bi-h">Who they are</h3>
        <p className="evo-small"><b>{a.identity.label}</b>{a.identity.entities.length ? ` · entities: ${a.identity.entities.join(', ')}` : ''}</p>
        {d.linked_buyers.length ? <p className="evo-small">Also buys as: {d.linked_buyers.map((x) => x.name).join(', ')}</p> : null}
        <p className="evo-small evo-muted">{a.identity.note}</p>
        <p className="evo-small">{o.contact_name || '—'}{o.company_name ? ` · ${o.company_name}` : ''}<br />
          {o.phone || 'no phone'} · {o.email || 'no email'}</p>
        <p className="evo-small"><b>Contact:</b> {a.contact.label} <span className="evo-muted">({a.contact.why})</span></p>
        <div className="evo-actionbar">
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'facts' || d.do_not_contact || !(o.phone || o.email)}
                  onClick={() => facts({ contact_verified: true }, 'Contact marked verified today.')}>I reached them - contact works</button>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === 'facts' || !(o.phone || o.email)}
                  onClick={() => facts({ contact_bad: true }, 'Contact marked not working.')}>Wrong number / bounced</button>
          {a.identity.status !== 'CONFIRMED' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === 'facts'}
                  onClick={() => facts({ identity_confirmed: true }, 'Identity confirmed.')}>Identity confirmed</button> : null}
        </div>
        <label className="bi-filter" style={{ marginTop: 10 }}><span>Decision-maker</span>
          <span style={{ display: 'flex', gap: 6 }}>
            <input className="evo-input" value={dm} onChange={(e) => setDm(e.target.value)} placeholder="Name of the person who decides" />
            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'facts'}
                    onClick={() => facts({ decision_maker_name: dm, decision_maker_confirmed: !!dm }, 'Decision-maker saved.')}>Save</button>
          </span></label>
        {o.source_detail ? <p className="evo-small evo-muted">Source: {o.source_detail}</p> : null}
      </div>
      <div>
        <h3 className="bi-h">Can they buy now?</h3>
        <ul className="bi-why">
          {a.readiness.have.map((h, i) => <li key={'h' + i} className="is-yes">✓ {h}</li>)}
          {a.readiness.missing.map((h, i) => <li key={'m' + i} className="is-unknown">? {h}</li>)}
        </ul>
        <div className="evo-actionbar">
          {['buying', 'paused', 'not_buying'].map((s) => (
            <button key={s} type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy === 'facts' || d.do_not_contact}
                    onClick={() => facts({ stated_buying_status: s }, 'Buying status recorded (as they said it, today).')}>Says: {words(s)}</button>
          ))}
        </div>
        <h3 className="bi-h">Purchase history</h3>
        <ul className="bi-why">{a.purchases.lines.length ? a.purchases.lines.map((l, i) => <li key={i}>{l}</li>) : <li className="is-unknown">No purchase evidence yet.</li>}</ul>
        <p className="evo-small evo-muted">{a.purchases.note}</p>
      </div>
    </div>
  )
}

function Boxes({ d, busy, confirm }) {
  if (!d.buy_boxes.length) return <p className="evo-muted evo-small">No buy box yet. Use "Edit buy boxes" below, or qualify them to learn what they buy.</p>
  return (
    <ul className="bi-list">
      {d.buy_boxes.map((b) => (
        <li key={b.id}>
          <span><b>{b.label || 'Buy box'}</b> {b.active ? null : <Tag>inactive</Tag>}{' '}
            <Tag kind={b.confirmed_at ? 'live' : undefined}>{b.confirmed_at ? `Confirmed ${day(b.confirmed_at)}` : cap(b.source || 'file')}</Tag>
            <span className="evo-prop__sub">
              {[b.property_types.map(words).join(', '), [...b.counties, ...b.cities].map(cap).join(', '),
                b.zips.length ? `${b.zips.length} ZIPs` : null,
                b.min_price || b.max_price ? `${usd(b.min_price)}–${usd(b.max_price)}` : null,
                b.min_units || b.max_units ? `${b.min_units || '?'}–${b.max_units || '?'} units` : null,
                b.min_acres || b.max_acres ? `${b.min_acres || '?'}–${b.max_acres || '?'} acres` : null,
                b.strategies.length ? b.strategies.map(words).join(', ') : null,
                b.rehab_tolerance ? `rehab up to ${words(b.rehab_tolerance)}` : null].filter(Boolean).join(' · ') || 'no criteria yet'}
            </span></span>
          {!b.confirmed_at ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'box'}
                  onClick={() => confirm(b.id)} title="Only after the buyer confirmed these criteria with you">Confirmed with buyer</button> : null}
        </li>
      ))}
    </ul>
  )
}

const EV_CLASSES = [['REPORTED_INVESTOR_PURCHASE', 'Reported investor purchase (you know of it)'],
  ['RECORDED_TITLE_TRANSFER', 'Recorded title transfer (deed / record number)'],
  ['VERIFIED_PURCHASE', 'Verified purchase (closing document)'], ['VERIFIED_CASH_PURCHASE', 'Verified cash purchase (closing document, no lender)'],
  ['CLAIMED_PURCHASE', 'Claimed by the buyer or their file']]

function Evidence({ d, busy, add }) {
  const [f, setF] = useState({ evidence_class: 'REPORTED_INVESTOR_PURCHASE' })
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  return (
    <>
      {!d.evidence.length ? <p className="evo-muted evo-small">No purchase records yet.</p> : (
        <div className="evo-table-wrap"><table className="evo-table evo-small">
          <caption className="evo-sr">Purchase evidence</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Property</th><th scope="col">Evidence</th><th scope="col">Source / reference</th></tr></thead>
          <tbody>{d.evidence.map((e) => (
            <tr key={e.id}><td>{day(e.date)}</td><td>{e.address || '—'}{e.kind ? ` · ${words(e.kind)}` : ''}</td>
              <td><Tag kind={/VERIFIED/.test(e.class) ? 'live' : e.class === 'CLAIMED_PURCHASE' ? undefined : 'info'}>{e.class_label}</Tag>{e.price ? ` ${usd(e.price)}` : ''}</td>
              <td>{e.source}{e.reference ? ` · ${e.reference}` : ''}{e.grantee ? ` · to ${e.grantee}` : ''}</td></tr>))}</tbody>
        </table></div>
      )}
      <h3 className="bi-h">Add a purchase you know of</h3>
      <div className="bi-filters">
        <label className="bi-filter"><span>Evidence</span><select className="evo-input" value={f.evidence_class} onChange={set('evidence_class')}>
          {EV_CLASSES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        {[['street', 'Street'], ['city', 'City'], ['zip', 'ZIP'], ['county', 'County'], ['date', 'Date (YYYY-MM-DD)'],
          ['reference', 'Document / record number'], ['price', 'Price (only if a document states it)'], ['note', 'Note']].map(([k, l]) => (
          <label key={k} className="bi-filter"><span>{l}</span><input className="evo-input" value={f[k] || ''} onChange={set(k)} /></label>
        ))}
      </div>
      <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'ev'}
              onClick={() => add({ ...f, price: f.price ? Number(String(f.price).replace(/[^0-9.]/g, '')) : null, price_source: f.price ? 'document' : null })}>Record it</button>
    </>
  )
}

function Stages({ a }) {
  return (
    <>
      <p className="evo-small evo-muted">{a.enrichment.note}</p>
      <ul className="bi-stages">
        {a.enrichment.stages.map((s) => (
          <li key={s.key}><span className="bi-stage__n">{s.points}/20</span><b>{s.label}</b>
            <Tag kind={STAGE_TONE[s.status]}>{words(s.status)}</Tag><span className="evo-prop__sub">{s.why}</span></li>
        ))}
      </ul>
    </>
  )
}

function Conversations({ d, conv, setConv, start, busy, practiceable, onChanged }) {
  const open = d.conversations.find((c) => c.id === conv) || d.conversations[0]
  const [text, setText] = useState('')
  const [err, setErr] = useState(null)
  const [working, setWorking] = useState(false)
  const [when, setWhen] = useState('')
  async function post(path, body) {
    setWorking(true); setErr(null)
    try { await api.post(`${BI}/conversations/${open.id}/${path}`, body || {}); setText(''); onChanged() }
    catch (e) { setErr(errText(e)) } finally { setWorking(false) }
  }
  return (
    <>
      <p className="evo-small evo-muted">The AI asks the qualification questions and reads each answer into the buyer record
        (stated, with the quote). It sends nothing: <b>practice</b> runs on practice buyers only; <b>recorded</b> is you typing
        what a real buyer said on a call. Live AI messaging to investors stays off until you approve it.</p>
      <div className="evo-actionbar">
        {practiceable ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy === 'conv' || d.do_not_contact}
                                onClick={() => start('practice')}>Start practice conversation</button> : null}
        {!d.is_test ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === 'conv' || d.do_not_contact}
                              onClick={() => start('recorded')}>Record a call with this buyer</button> : null}
        {d.conversations.length > 1 ? (
          <select className="evo-input" value={open ? open.id : ''} onChange={(e) => setConv(e.target.value)} aria-label="Conversation">
            {d.conversations.map((c) => <option key={c.id} value={c.id}>{day(c.created_at)} · {c.mode} · {words(c.status)}</option>)}
          </select>) : null}
      </div>
      {d.do_not_contact ? <Alert>This buyer asked not to be contacted - no new conversation can start.</Alert> : null}
      <Alert>{err}</Alert>
      {!open ? <p className="evo-muted evo-small">No conversations yet.</p> : (
        <div className="bi-conv">
          <div className="bi-conv__meta"><Tag>{open.mode}</Tag> <Tag kind={open.status === 'open' ? 'live' : undefined}>{words(open.status)}</Tag>
            {open.handoff_reason ? <span className="evo-small"> · with a person: {open.handoff_reason}</span> : null}</div>
          <ol className="bi-transcript">
            {open.transcript.map((t, i) => (
              <li key={i} className={`bi-turn bi-turn--${t.role}`}><span className="bi-turn__who">{t.role === 'ai' ? 'AI' : t.role === 'buyer' ? 'Buyer' : t.role === 'staff' ? 'Team' : 'System'}</span>
                <span>{t.text}{t.extracted && Object.keys(t.extracted).length ? <span className="evo-prop__sub">learned: {Object.keys(t.extracted).map(words).join(', ')} ({t.reader})</span> : null}</span></li>
            ))}
          </ol>
          {open.summary ? <p className="evo-small"><b>Summary:</b> {open.summary}</p> : null}
          {open.objections.length ? <p className="evo-small"><b>Objections:</b> {open.objections.join(' · ')}</p> : null}
          {open.commitments.length ? <p className="evo-small"><b>Follow-ups:</b> {open.commitments.map((c) => `${c.what}${c.due ? ' (' + day(c.due) + ')' : ''}`).join(' · ')}</p> : null}
          {open.status === 'open' ? (
            <div className="bi-reply">
              <textarea className="evo-input" rows={2} value={text} onChange={(e) => setText(e.target.value)}
                        placeholder={open.mode === 'practice' ? 'Type the practice buyer’s answer…' : 'Type what the buyer said…'} />
              <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={working || !text.trim()}
                      onClick={() => post('reply', { text })}>Add answer</button>
            </div>
          ) : null}
          <div className="evo-actionbar" style={{ marginTop: 8 }}>
            {open.status === 'open' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={working} onClick={() => post('pause')}>Pause AI</button> : null}
            {['paused', 'handed_off'].includes(open.status) && !d.do_not_contact ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={working} onClick={() => post('resume')}>Resume</button> : null}
            {open.status === 'open' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={working} onClick={() => post('takeover')}>Take over (person)</button> : null}
            <input className="evo-input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} aria-label="Follow-up time" style={{ maxWidth: 210 }} />
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={working || d.do_not_contact}
                    onClick={() => post('schedule', { kind: 'call', starts_at: when || null })}>Schedule call</button>
          </div>
        </div>
      )}
    </>
  )
}
