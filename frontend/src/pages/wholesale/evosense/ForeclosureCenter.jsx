/* FORECLOSURE COMMAND CENTER — one place to work every month's foreclosures.
 *
 * A month (an auction cycle) holds every property with a foreclosure notice or
 * a list's claim for a sale that month, plus anything a person carried forward
 * (a postponement). Everything on this screen is a real record:
 *   - the ranking, notice status, owner and contacts are the Top 100's own
 *     (the same engine, for the month's properties);
 *   - "Find contact", "Analyze deal" and "Open acquisition" open the existing
 *     property page, contact-lookup batch, call queue and Wholesale deal;
 *   - an auction outcome is recorded by a manager WITH evidence, and the notice
 *     itself is never changed.
 * Nothing here buys data, sends a message or creates a contract.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Empty, EvoApp, Metric, Metrics, PageHead, Panel, Ring, Search, Seg, Skeleton, Tabs, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { ContactBatchPanel } from './Acquire'
import './evo-actions.css'

const KEY = 'evo_fcc_month'
const TABS = [
  { key: 'overview', label: 'Monthly overview' },
  { key: 'properties', label: 'Foreclosure properties' },
  { key: 'contact', label: 'Contact & acquisition' },
  { key: 'financial', label: 'Financial' },
  { key: 'auction', label: 'Auction tracker' },
  { key: 'history', label: 'Monthly history' },
]
const VIEWS = [{ key: 'top10', label: 'Top 10' }, { key: 'top25', label: 'Top 25' }, { key: 'top100', label: 'Top 100' },
  { key: 'all', label: 'All' }]
const SORTS = [['opportunity', 'Highest opportunity'], ['auction_date', 'Auction date'], ['urgent', 'Most urgent'],
  ['recently_verified', 'Most recently verified'], ['contact_ready', 'Contact ready first'],
  ['underwritten', 'Financially underwritten first']]
const TONE = { notice_verified: 'danger', claimed: 'estimate', not_found: 'estimate', conflict: 'danger',
  postponed: 'estimate', canceled: 'info', sold_at_auction: 'info', reverted_to_lender: 'info',
  outcome_unverified: 'estimate', reported_resolved: 'estimate', awaiting_notice: 'estimate', no_longer_on_record: 'estimate' }
const OUTCOMES = [['notice_verified', 'Notice verified - sale scheduled'], ['postponed', 'Postponed'], ['canceled', 'Canceled'],
  ['sold_at_auction', 'Sold at auction'], ['reverted_to_lender', 'Reverted to lender'],
  ['outcome_unverified', 'Clear - outcome not verified']]

function readMonth() {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}
function saveMonth(m) {
  try { localStorage.setItem(KEY, m) } catch { /* private window: the URL still carries it */ }
}

function Card({ title, main, rows, onClick, attention }) {
  return (
    <button type="button" className={`evo-fcc-card${attention ? ' is-attention' : ''}`} onClick={onClick}>
      <span className="evo-fcc-card__title">{title}</span>
      <span className="evo-fcc-card__main">{main}</span>
      <span className="evo-fcc-card__rows">
        {rows.map(([label, value]) => <span key={label}><b>{value}</b> {label}</span>)}
      </span>
    </button>
  )
}

function StatusTag({ a }) {
  return <Tag kind={TONE[a.status]} title={a.evidence || ''}>{a.label}</Tag>
}

function Actions({ r }) {
  const notice = (r.auction.documents || []).find((d) => d.url)
  return (
    <span className="evo-fcc-actions" onClick={(e) => e.stopPropagation()}>
      <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${r.id}`}>View property</Link>
      {notice ? <a className="evo-btn evo-btn--secondary evo-btn--sm" href={notice.url} target="_blank" rel="noreferrer">Verify notice</a>
        : <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${r.id}#signals`}>Verify notice</Link>}
      <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${r.id}#owner`}>Find contact</Link>
      {r.acquisition.deal_id
        ? <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/deals/${r.acquisition.deal_id}`}>Analyze deal</Link>
        : <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${r.id}#deal`}>Analyze deal</Link>}
      {r.acquisition.deal_id
        ? <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/deals/${r.acquisition.deal_id}`}>Open acquisition</Link>
        : <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${r.id}#deal`}>Open acquisition</Link>}
    </span>
  )
}

function PropertyTable({ items, columns, navigate, extra }) {
  if (!items.length) return <Empty title="Nothing matches">Clear a filter to see more of this month.</Empty>
  return (
    <div className="evo-table-wrap">
      <table className="evo-table evo-table--cards evo-fcc-table">
        <caption className="evo-sr">Foreclosure properties</caption>
        <thead><tr>
          <th scope="col" className="is-num">#</th><th scope="col">Property</th>
          {columns.map(([k, l]) => <th key={k} scope="col">{l}</th>)}
          {extra ? <th scope="col">Record</th> : null}
        </tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id} className={`is-link${r.needs_attention ? ' is-attention' : ''}`}
                onClick={() => navigate(`/wholesale/evosense/property/${r.id}`)}>
              <td className="is-num" data-label="Rank">{r.rank}</td>
              <td className="is-lead" data-label="">
                <Link className="evo-prop__addr" to={`/wholesale/evosense/property/${r.id}`} onClick={(e) => e.stopPropagation()}>
                  {r.street_address}</Link>
                <span className="evo-prop__sub" style={{ display: 'block' }}>
                  {[r.city, r.county ? `${r.county} County` : null].filter(Boolean).join(' · ')}</span>
                {(r.auction.flags || []).map((f) => <span key={f} className="evo-prop__sub evo-fcc-flag">{f}</span>)}
                <Actions r={r} />
              </td>
              {columns.map(([k, l, render]) => <td key={k} data-label={l} className="is-stack">{render(r)}</td>)}
              {extra ? <td data-label="Record" className="is-stack" onClick={(e) => e.stopPropagation()}>{extra(r)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const COLS = {
  sale: ['sale', 'Auction', (r) => (<>
    <StatusTag a={r.auction} />
    <span className="evo-prop__sub" style={{ display: 'block' }}>
      {r.auction.sale_date ? `Sale ${r.auction.sale_date}` : 'No date on record'}
      {r.foreclosure?.foreclosure_type ? ` · ${r.foreclosure.foreclosure_type}` : ''}</span></>)],
  work: ['work', 'Contact · money · stage', (r) => (<>
    <span style={{ display: 'block' }}>{r.contact_state.label}</span>
    <span className="evo-prop__sub" style={{ display: 'block' }}>{r.financial.label} · {r.acquisition.label}</span></>)],
  type: ['type', 'Foreclosure type', (r) => r.foreclosure?.foreclosure_type || <span className="evo-muted">Not stated</span>],
  date: ['date', 'Auction date', (r) => r.auction.sale_date || <span className="evo-muted">No date on record</span>],
  notice: ['notice', 'Notice', (r) => <StatusTag a={r.auction} />],
  owner: ['owner', 'Owner', (r) => r.owner?.status === 'VERIFIED' ? <Tag kind="live">Verified</Tag>
    : r.owner?.status === 'NO OWNER' ? <Tag kind="danger">Owner missing</Tag> : <Tag kind="estimate">Verify owner</Tag>],
  score: ['score', 'Opportunity', (r) => <Ring kind="opportunity" value={r.score} />],
  contact: ['contact', 'Contact', (r) => r.contact_state.label],
  money: ['money', 'Financial', (r) => r.financial.label],
  stage: ['stage', 'Acquisition', (r) => r.acquisition.label],
  next: ['next', 'Next action', (r) => r.opportunity?.next_action?.label || r.next_action || '—'],
}

function RecordOutcome({ r, month, onDone }) {
  const [status, setStatus] = useState('')
  const [evidence, setEvidence] = useState('')
  const [newMonth, setNewMonth] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  if (!r.auction.event_id) return null
  async function save() {
    setBusy(true); setErr('')
    try {
      await api.post(`/wholesale/evosense/foreclosure-events/${r.auction.event_id}/status`,
        { status, evidence, new_month: status === 'postponed' && newMonth ? newMonth : null })
      setStatus(''); setEvidence(''); setNewMonth(''); onDone()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="evo-fcc-record">
      <select className="evo-select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Record an outcome">
        <option value="">Record what happened…</option>
        {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      {status ? (
        <>
          <input className="evo-input" value={evidence} onChange={(e) => setEvidence(e.target.value)}
                 placeholder="What shows this? (notice number, clerk record, call with the trustee)" />
          {status === 'postponed' ? (
            <input className="evo-input" type="month" value={newMonth} min={month} onChange={(e) => setNewMonth(e.target.value)}
                   aria-label="New sale month (if known)" />
          ) : null}
          <button className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={save}>Save</button>
        </>
      ) : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
    </div>
  )
}

export default function ForeclosureCenter() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [month, setMonth] = useState(params.get('month') || readMonth())
  const [tab, setTab] = useState(params.get('tab') || 'overview')
  const [data, setData] = useState(null)
  const [list, setList] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)
  const [view, setView] = useState('top25')
  const [sort, setSort] = useState('opportunity')
  const [county, setCounty] = useState('')
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState({})
  const [page, setPage] = useState(1)
  const [history, setHistory] = useState(null)

  const load = useCallback(() => {
    setErr('')
    api.get(`/wholesale/evosense/foreclosures${month ? `?month=${month}` : ''}`).then((d) => {
      setData(d)
      if (!month) setMonth(d.month)
      saveMonth(d.month)
    }).catch((e) => setErr(errText(e)))
  }, [month])
  useEffect(() => { setData(null); load() }, [load])
  useEffect(() => {
    const next = new URLSearchParams()
    if (month) next.set('month', month)
    if (tab !== 'overview') next.set('tab', tab)
    setParams(next, { replace: true })
  }, [month, tab, setParams])

  const listQuery = useMemo(() => {
    const p = new URLSearchParams({ month: data?.month || month || '', view, sort, page: String(page), per_page: '50' })
    if (county) p.set('county', county)
    if (q.trim()) p.set('q', q.trim())
    Object.entries(filter).forEach(([k, v]) => { if (v) p.set(k, v) })
    return p.toString()
  }, [data, month, view, sort, page, county, q, filter])
  const loadList = useCallback(() => {
    if (!data?.exists) { setList(null); return }
    api.get(`/wholesale/evosense/foreclosures/properties?${listQuery}`).then(setList).catch((e) => setErr(errText(e)))
  }, [listQuery, data])
  useEffect(() => { if (['properties', 'contact', 'financial', 'auction'].includes(tab)) loadList() }, [tab, loadList])
  useEffect(() => {
    if (tab === 'history') api.get('/wholesale/evosense/foreclosures/history').then((r) => setHistory(r.months)).catch((e) => setErr(errText(e)))
  }, [tab])

  function go(m) { setMonth(m); setPage(1); setPreview(null) }
  function drill(f, t = 'properties') { setFilter(f); setView('all'); setPage(1); setTab(t) }
  async function openPreview() {
    setBusy(true); setErr('')
    try { setPreview(await api.get(`/wholesale/evosense/foreclosures/start-preview?month=${data.month}`)) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function start() {
    setBusy(true); setErr('')
    try { await api.post('/wholesale/evosense/foreclosures/start', { month: data.month }); setPreview(null); load() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function refresh() {
    setBusy(true); setErr('')
    try { await api.post('/wholesale/evosense/foreclosures/refresh', { month: data.month }); load(); loadList() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const c = data?.cards
  const months = useMemo(() => {
    const set = new Map((data?.months || []).map((m) => [m.month, `${m.label} · ${m.properties}`]))
    if (data && !set.has(data.month)) set.set(data.month, data.label)
    return [...set.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  }, [data])
  const items = list?.items || []

  return (
    <EvoApp>
      <PageHead eyebrow="Wholesale · Acquisition" title="Foreclosure Command Center"
                sub="Find opportunities. Verify notices. Work owners. Track auctions." />
      <div className="evo-fcc-bar">
        <button className="evo-btn evo-btn--secondary" onClick={() => data && go(data.previous)} disabled={!data}>‹ Previous month</button>
        <select className="evo-select evo-fcc-month" value={data?.month || month} onChange={(e) => go(e.target.value)}
                aria-label="Auction month">
          {months.map(([m, l]) => <option key={m} value={m}>{l}</option>)}
        </select>
        <button className="evo-btn evo-btn--secondary" onClick={() => data && go(data.next)} disabled={!data}>Next month ›</button>
        {data?.exists ? <button className="evo-btn evo-btn--secondary" onClick={refresh} disabled={busy}>Refresh</button> : null}
        {data?.can_manage ? <button className="evo-btn evo-btn--primary" onClick={openPreview} disabled={busy}>
          {data?.exists ? 'Start month again (safe)' : 'Start this month'}</button> : null}
      </div>
      {data ? (
        <p className="evo-muted evo-small" style={{ margin: '4px 0 12px' }}>
          Sale day <b>{data.sale_date}</b> (first Tuesday rule).
          {Object.keys(data.sale_dates_on_notices || {}).length
            ? ` Dates on the notices: ${Object.entries(data.sale_dates_on_notices).map(([d, n]) => `${d} (${n})`).join(', ')}.` : ''}
          {data.last_refreshed ? ` Refreshed ${new Date(data.last_refreshed).toLocaleString()}.` : ''}
        </p>
      ) : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {preview ? (
        <Panel title={`Start ${preview.label}`} raised>
          <p>{preview.with_notices} properties have a notice for this month: {preview.would_add} will be added,
            {' '}{preview.already_in_month} are already in it, {preview.also_in_earlier_month} were in an earlier month
            {' '}(kept, and linked), and {preview.postponed_to_carry} postponed properties will be carried forward.</p>
          <p className="evo-muted evo-small">It will not {preview.will_not.join(', ')}. Running it again adds nothing.</p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="evo-btn evo-btn--primary" onClick={start} disabled={busy}>{busy ? 'Working…' : 'Start month'}</button>
            <button className="evo-btn evo-btn--secondary" onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </Panel>
      ) : null}
      {!data && !err ? <Skeleton rows={6} height={40} /> : null}
      {data && !data.exists ? (
        <Empty title={`${data.label} has not been started`}
               action={<span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
                 {data.can_manage ? <button className="evo-btn evo-btn--primary" onClick={openPreview}>Preview the month</button> : null}
                 <button className="evo-btn evo-btn--secondary" onClick={() => setTab('history')}>Monthly history</button></span>}>
          Start the month to gather every property with a foreclosure notice for its sale.
          {data.can_manage ? '' : ' Ask a workspace manager to start it.'}
        </Empty>
      ) : null}
      {data?.exists ? (
        <>
          <div className="evo-fcc-cards" role="group" aria-label="This month at a glance">
            <Card title="Properties found" main={c.found.total} onClick={() => drill({})}
                  rows={[['new this month', c.found.new], ['carried forward', c.found.carried]]} />
            <Card title="Notices verified" main={c.verified.verified} onClick={() => drill({ verified: 'yes' })}
                  rows={[['pending', c.verified.pending], ['conflicting', c.verified.conflict]]} />
            <Card title="Ready for contact" main={c.contact.ready} onClick={() => drill({ contact: 'contact_found,outreach_eligible' }, 'contact')}
                  rows={[['outreach allowed', c.contact.eligible], ['contact missing', c.contact.awaiting]]} />
            <Card title="Needs attention" main={c.attention.urgent} attention={c.attention.urgent > 0}
                  onClick={() => drill({ attention: 'yes' })}
                  rows={[['owner missing', c.attention.missing_owners], ['conflicts', c.attention.conflicts],
                    ['postponed', c.attention.postponed], ['ownership', c.attention.ownership]]} />
            <Card title="Potential deals" main={c.deals.active} onClick={() => drill({ deals: 'yes' }, 'financial')}
                  rows={[['supported analysis', c.deals.supported], ['offers', c.deals.offers], ['contracts', c.deals.contracts]]} />
          </div>
          <p className="evo-muted evo-small">{c.contact.note}</p>
          <Tabs items={TABS} value={tab} onChange={setTab} label="Foreclosure month" />

          {tab === 'overview' ? (
            <div className="evo-fcc-grid">
              <Panel title="Counties" count={Object.keys(data.counties).length}>
                <ul className="evo-fcc-list">{Object.entries(data.counties).map(([k, v]) => (
                  <li key={k}><button className="evo-linkbtn" onClick={() => { setCounty(k === 'Unknown' ? '' : k); drill({}) }}>{k}</button><b>{v}</b></li>))}</ul>
              </Panel>
              <Panel title="Notices">
                <ul className="evo-fcc-list">{Object.entries(data.statuses).map(([k, v]) => (
                  <li key={k}><button className="evo-linkbtn" onClick={() => drill({ status: k })}>{v.label}</button><b>{v.count}</b></li>))}</ul>
              </Panel>
              <Panel title="Contact readiness">
                <ul className="evo-fcc-list">{Object.entries(data.contact_states).map(([k, v]) => (
                  <li key={k}><button className="evo-linkbtn" onClick={() => drill({ contact: k }, 'contact')}>{v.label}</button><b>{v.count}</b></li>))}</ul>
              </Panel>
              <Panel title="Acquisition">
                <ul className="evo-fcc-list">{Object.entries(data.acquisition).map(([k, v]) => (
                  <li key={k}><button className="evo-linkbtn" onClick={() => drill({ acquisition: k }, 'contact')}>{v.label}</button><b>{v.count}</b></li>))}</ul>
              </Panel>
              <Panel title="Work these first" hint="Needs a person">
                {data.urgent.length ? (
                  <ul className="evo-fcc-list">{data.urgent.map((u) => (
                    <li key={u.id}><Link to={`/wholesale/evosense/property/${u.id}`}>{u.address}</Link>
                      <span className="evo-small">{(u.flags || [])[0] || u.next_action || u.status}</span></li>))}</ul>
                ) : <p className="evo-muted">Nothing urgent.</p>}
              </Panel>
              <Panel title="County sources" hint="What was really checked">
                <ul className="evo-fcc-list">{(data.sources || []).map((s) => (
                  <li key={s.county}><span><b>{s.county}</b> · {s.state}
                    {s.last_checked ? ` · ${new Date(s.last_checked).toLocaleDateString()}` : ''}</span>
                    {s.manual_action ? <span className="evo-small evo-muted">{s.manual_action}</span> : null}</li>))}</ul>
              </Panel>
            </div>
          ) : null}

          {['properties', 'contact', 'financial', 'auction'].includes(tab) ? (
            <Panel flush>
              <div className="evo-fcc-filters">
                <Seg items={VIEWS} value={view} onChange={(v) => { setView(v); setPage(1) }} label="How many" />
                <Search value={q} onChange={(v) => { setQ(v); setPage(1) }} placeholder="Search an address" />
                <select className="evo-select" value={county} onChange={(e) => { setCounty(e.target.value); setPage(1) }} aria-label="County">
                  <option value="">All counties</option>
                  {Object.keys(data.counties).map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
                <select className="evo-select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
                  {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <select className="evo-select" value={filter.status || ''} aria-label="Notice status"
                        onChange={(e) => { setFilter({ ...filter, status: e.target.value }); setPage(1) }}>
                  <option value="">Any notice status</option>
                  {Object.entries(list?.statuses || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                {Object.values(filter).some(Boolean) || county || q
                  ? <button className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => { setFilter({}); setCounty(''); setQ('') }}>Clear filters</button> : null}
              </div>
              {tab === 'contact' ? (
                <div style={{ padding: '0 16px' }}>
                  <p className="evo-small">Calling happens from the call queue on <Link to="/wholesale/evosense">Acquisition Command</Link> and
                    {' '}the VA tasks on <Link to="/wholesale/exceptions">Exceptions</Link>. Owner lookups for this month:</p>
                  <ContactBatchPanel source={`cycle:${data.month}`} />
                </div>
              ) : null}
              {!list ? <Skeleton rows={6} height={28} /> : (
                <>
                  <p className="evo-small evo-muted" style={{ padding: '0 16px' }}>{list.total} properties</p>
                  <PropertyTable navigate={navigate} items={items}
                    columns={tab === 'contact' ? [COLS.owner, COLS.contact, COLS.stage, COLS.next]
                      : tab === 'financial' ? [COLS.score, COLS.money, ['needs', 'Missing evidence', (r) => (r.financial.needs || []).join(' · ') || '—'], COLS.stage]
                        : tab === 'auction' ? [COLS.date, COLS.notice, ['docs', 'Notices on record', (r) => (r.auction.documents || []).map((d) => (
                          <span key={d.signal_id} className="evo-prop__sub" style={{ display: 'block' }}>
                            {d.url ? <a href={d.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{d.document}</a> : d.document}
                            {d.sale_date ? ` · sale ${d.sale_date}` : ''}{d.claim ? ' · list claim' : ''}</span>))]]
                          : [COLS.sale, COLS.owner, COLS.score, COLS.work, COLS.next]}
                    extra={tab === 'auction' && data.can_manage ? (r) => <RecordOutcome r={r} month={data.month} onDone={() => { load(); loadList() }} /> : null} />
                  {list.total > list.per_page ? (
                    <div className="evo-fcc-pager">
                      <button className="evo-btn evo-btn--secondary evo-btn--sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
                      <span className="evo-small">Page {page} of {Math.ceil(list.total / list.per_page)}</span>
                      <button className="evo-btn evo-btn--secondary evo-btn--sm" disabled={page * list.per_page >= list.total} onClick={() => setPage(page + 1)}>Next</button>
                    </div>
                  ) : null}
                </>
              )}
            </Panel>
          ) : null}
        </>
      ) : null}

      {tab === 'history' ? (
        <Panel title="Months worked" hint="Nothing is deleted when a new month starts">
          {!history ? <Skeleton rows={4} /> : !history.length ? <p className="evo-muted">No month started yet.</p> : (
            <div className="evo-table-wrap">
              <table className="evo-table evo-table--cards">
                <thead><tr><th>Month</th><th>Sale day</th><th className="is-num">Properties</th><th className="is-num">Verified</th>
                  <th className="is-num">Carried in</th><th className="is-num">Contacted</th><th className="is-num">Deals</th><th>Outcomes</th><th /></tr></thead>
                <tbody>{history.map((h) => (
                  <tr key={h.month}>
                    <td className="is-lead" data-label=""><b>{h.label}</b></td>
                    <td data-label="Sale day">{h.sale_date}</td>
                    <td className="is-num" data-label="Properties">{h.properties}</td>
                    <td className="is-num" data-label="Verified">{h.verified}</td>
                    <td className="is-num" data-label="Carried in">{h.carried_in}</td>
                    <td className="is-num" data-label="Contacted">{h.contacted}</td>
                    <td className="is-num" data-label="Deals">{h.deals}</td>
                    <td data-label="Outcomes" className="is-stack">{Object.entries(h.outcomes).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td>
                    <td data-label=""><button className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => { go(h.month); setTab('overview') }}>Open</button></td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : null}
    </EvoApp>
  )
}
