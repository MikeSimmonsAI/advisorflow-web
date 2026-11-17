/* MY DEAL ROOM — PORTFOLIO ACQUISITIONS (Acquisition Command → Portfolio Acquisitions).
 *
 * One seller, many properties. Five views in two routes, built from the
 * existing Wholesale design system (EvoApp, Hero, Panel, Metrics, tables,
 * drawers, tabs) — no new visual language:
 *
 *   /wholesale/portfolios            A. Portfolio list
 *   /wholesale/portfolios/:id        B. Portfolio detail / inventory   (tab)
 *                                    C. Property evaluation            (drawer)
 *                                    D. Package builder + comparison   (tab)
 *                                    E. Portfolio summary              (tab)
 *
 * Every figure comes from the server (app/services/wholesale_portfolio.py),
 * which reads each property's own deal: ARV, repairs, the MAO formula and the
 * underwriting scenarios are the deal room's. This screen computes nothing;
 * it shows what the server computed and how.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../../api/client'
import '../../../styles/shared.css'
import '../wholesale.css'
import { errText, fmtDate, fmtLabel } from '../wsShared'
import { Alert, Drawer, Empty, EvoApp, Field, Hero, Metric, Metrics, Panel, Skeleton, Status, Tag, TabBar } from '../ds/ds'
import '../ds/evo-pages.css'
import './portfolio.css'

const money = (v, blank = '—') => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? blank
  : (Number(v) < 0 ? '−$' : '$') + Math.abs(Math.round(Number(v))).toLocaleString('en-US'))
const pct = (v, blank = '—') => (v === null || v === undefined ? blank : `${Number(v).toFixed(1)}%`)
const STATUS_TONE = { research_needed: 'attention', repairs_needed: 'attention', offer_needed: 'info', evaluated: 'good' }
const PORTFOLIO_STATUSES = ['researching', 'evaluating', 'negotiating', 'offer_made', 'under_contract', 'closed', 'passed']

function download(path, name) {
  return api.get(path, { asBlob: true }).then((blob) => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  })
}

/** A number the portfolio does not have yet: said plainly, never a dash. */
const Miss = ({ what = 'Missing' }) => <span className="pf-miss">{what}</span>
const has = (v) => !(v === null || v === undefined || v === '')

/** Import → Review → Offer package, with where this portfolio stands. */
function Flow({ s, drafts, chosen, onImport, onReview, onPackage }) {
  const step = !s.properties ? 0 : s.evaluated < s.properties ? 1 : 2
  const items = [
    ['Import the seller’s list', s.properties ? `${s.properties} propert${s.properties === 1 ? 'y' : 'ies'}` : 'Nothing imported yet',
      <button key="i" type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={onImport}>Import</button>],
    ['Review each property', s.properties ? `${s.evaluated} of ${s.properties} evaluated` : '—',
      <button key="r" type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={!s.properties} onClick={onReview}>Needs research</button>],
    ['Build an offer package', drafts ? `${drafts} draft package${drafts === 1 ? '' : 's'}` : 'No package yet',
      <button key="p" type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={!s.properties} onClick={onPackage}>
        {chosen ? `Package ${chosen} selected` : 'Package all'}</button>],
  ]
  return (
    <ol className="pf-flow" aria-label="Portfolio workflow">
      {items.map(([t, sub, btn], i) => (
        <li key={t} className={i < step ? 'is-done' : i === step ? 'is-now' : ''} aria-current={i === step ? 'step' : undefined}>
          <span className="pf-flow__n" aria-hidden="true">{i < step ? '✓' : i + 1}</span>
          <span className="pf-flow__t"><b>{t}</b><span>{sub}</span></span>{btn}
        </li>
      ))}
    </ol>
  )
}

function Partial({ t, of }) {
  if (!t) return null
  if (!t.counted) return <>none on file yet</>
  if (t.missing) return <>partial · {t.counted} of {of}</>
  return <>all {of}</>
}

/* ── A. Portfolio list ──────────────────────────────────────────────────── */
export function PortfolioList() {
  const navigate = useNavigate()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [archived, setArchived] = useState(false)
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => {
    setError(null)
    api.get('/wholesale/portfolios' + (archived ? '?include_archived=true' : ''))
      .then((d) => setRows(d.portfolios)).catch((e) => setError(errText(e)))
  }, [archived])
  useEffect(() => { load() }, [load])

  return (
    <EvoApp world="acquisition">
      <Hero compact scene="estate" eyebrow="Acquire" title="Seller Portfolios"
            sub="One seller, many properties. Evaluate each property, then build and compare purchase packages."
            meta={rows ? [{ label: <><b>{rows.length}</b> portfolio{rows.length === 1 ? '' : 's'}</> }] : null}
            actions={<button type="button" className="evo-btn evo-btn--primary" onClick={() => setCreating(true)}>+ New portfolio</button>} />
      <Alert>{error}</Alert>
      <Panel flush>
        <div className="evo-filterbar">
          <label className="evo-btn evo-btn--ghost" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} style={{ margin: 0 }} />
            Show archived
          </label>
        </div>
        {rows === null ? <div style={{ padding: 20 }}><Skeleton rows={4} height={30} /></div> : null}
        {rows && !rows.length ? (
          <Empty title="No seller portfolios yet"
                 action={<button type="button" className="evo-btn evo-btn--primary" onClick={() => setCreating(true)}>+ New portfolio</button>}>
            Start one for a seller who is offering several properties, then import their list.
          </Empty>
        ) : null}
        {rows && rows.length ? (
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <caption className="evo-sr">Seller portfolios</caption>
              <thead><tr>
                <th scope="col">Portfolio</th><th scope="col">Seller</th><th scope="col">Status</th>
                <th scope="col" className="is-right">Properties</th><th scope="col">Evaluated</th>
                <th scope="col" className="is-right">Seller asking (stated)</th>
                <th scope="col" className="is-right">Our target offers</th><th scope="col" className="is-right">Packages</th>
              </tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="is-link" onClick={() => navigate(`/wholesale/portfolios/${p.id}`)}>
                    <td className="is-lead" data-label="">
                      <Link className="evo-prop__addr" to={`/wholesale/portfolios/${p.id}`} onClick={(e) => e.stopPropagation()}>{p.label}</Link>
                      <span className="evo-prop__sub">{p.name}{p.archived ? ' · archived' : ''}</span>
                      {p.is_test ? <Tag kind="sandbox">Sandbox</Tag> : null}
                    </td>
                    <td data-label="Seller">{p.seller_name || <span className="evo-muted">not set</span>}</td>
                    <td data-label="Status"><Status status={p.status} label={p.status_label} /></td>
                    <td data-label="Properties" className="is-right">{p.summary.properties}</td>
                    <td data-label="Evaluated">
                      <span className="pf-progress" aria-label={`${p.summary.progress_pct}% evaluated`}><span style={{ width: `${p.summary.progress_pct}%` }} /></span>
                      <span className="evo-prop__sub">{p.summary.evaluated} of {p.summary.properties}</span>
                    </td>
                    <td data-label="Seller asking (stated)" className="is-right">{money(p.summary.seller_asking.total)}</td>
                    <td data-label="Our target offers" className="is-right">{money(p.summary.target_offers.total)}
                      {p.summary.target_offers.partial ? <span className="evo-prop__sub">partial</span> : null}</td>
                    <td data-label="Packages" className="is-right">{p.packages}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
      <PortfolioForm open={creating} onClose={() => setCreating(false)}
                     onSaved={(d) => { setCreating(false); navigate(`/wholesale/portfolios/${d.portfolio.id}`) }} />
    </EvoApp>
  )
}

function PortfolioForm({ open, onClose, onSaved, portfolio }) {
  const blank = { name: '', seller_name: '', source: '', status: 'researching', notes: '', target_offer_total: '', is_test: false }
  const [f, setF] = useState(blank)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setF(portfolio ? { ...blank, ...portfolio, target_offer_total: portfolio.target_offer_total ?? '' } : blank)
    setErr(null)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  async function save(e) {
    e.preventDefault(); setBusy(true); setErr(null)
    try {
      const body = { name: f.name, seller_name: f.seller_name, source: f.source, status: f.status, notes: f.notes,
                     target_offer_total: f.target_offer_total === '' ? null : Number(f.target_offer_total) }
      const d = portfolio ? await api.patch(`/wholesale/portfolios/${portfolio.id}`, body)
        : await api.post('/wholesale/portfolios', { ...body, is_test: f.is_test })
      onSaved(d)
    } catch (x) { setErr(errText(x)) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title={portfolio ? `Edit ${portfolio.label}` : 'New seller portfolio'}
            sub={portfolio ? null : 'Numbered automatically (Seller Portfolio #001, #002 …)'}
            footer={<><button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="submit" form="pf-form" className="evo-btn evo-btn--primary" disabled={busy || !f.name.trim()}>{busy ? 'Saving…' : 'Save'}</button></>}>
      <Alert>{err}</Alert>
      <form id="pf-form" className="pf-form" onSubmit={save}>
        <Field label="Portfolio name"><input className="evo-input" value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="e.g. Oak Cliff rental portfolio" /></Field>
        <Field label="Selling entity / seller"><input className="evo-input" value={f.seller_name} onChange={(e) => set('seller_name', e.target.value)} /></Field>
        <Field label="Source" hint="How the list reached us"><input className="evo-input" value={f.source} onChange={(e) => set('source', e.target.value)} placeholder="e.g. printed list from the seller" /></Field>
        <Field label="Status"><select className="evo-select" value={f.status} onChange={(e) => set('status', e.target.value)}>
          {PORTFOLIO_STATUSES.map((s) => <option key={s} value={s}>{fmtLabel(s)}</option>)}</select></Field>
        <Field label="Portfolio-level target offer (optional)" hint="Shown beside the sum of the property offers, never instead of it">
          <input className="evo-input" inputMode="decimal" value={f.target_offer_total} onChange={(e) => set('target_offer_total', e.target.value)} /></Field>
        <Field label="Notes" full><textarea className="evo-input" rows={3} value={f.notes || ''} onChange={(e) => set('notes', e.target.value)} /></Field>
        {!portfolio ? <label className="ws-checkbox"><input type="checkbox" checked={f.is_test} onChange={(e) => set('is_test', e.target.checked)} />
          Sandbox portfolio — its properties are kept out of every dashboard figure and outreach path</label> : null}
      </form>
    </Drawer>
  )
}

/* ── B–E. Portfolio detail ──────────────────────────────────────────────── */
const SORTS = [
  ['row', 'List order'], ['spread', 'Best potential spread'], ['yield', 'Best stated rent yield'],
  ['repairs', 'Lowest repair burden'], ['confidence', 'Highest value confidence'], ['asking', 'Seller asking (high→low)'],
]
const FILTERS = [
  ['', 'All properties'], ['research_needed', 'Needs research'], ['repairs_needed', 'Needs repairs'],
  ['offer_needed', 'Offer not set'], ['evaluated', 'Evaluated'], ['land', 'Land'], ['tenant', 'Occupied'],
  ['vacant', 'Vacant'], ['risk', 'High risk / unresolved'],
]
const CONF_RANK = { high: 3, medium: 2, low: 1, insufficient: 0 }
const nullLast = (v) => (v === null || v === undefined ? -Infinity : v)

export function PortfolioDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [pk, setPk] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [tab, setTab] = useState('inventory')
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('row')
  const [sel, setSel] = useState({})
  const [open, setOpen] = useState(null)        // item id for the evaluation drawer
  const [drawer, setDrawer] = useState(null)    // 'import' | 'add' | 'assign' | 'edit'
  const [pkgFocus, setPkgFocus] = useState(null)

  const load = useCallback(() => {
    setError(null)
    return Promise.all([api.get(`/wholesale/portfolios/${id}`), api.get(`/wholesale/portfolios/${id}/packages`)])
      .then(([d, p]) => { setData(d); setPk(p) }).catch((e) => setError(errText(e)))
  }, [id])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => {
    if (!data) return []
    let r = data.rows
    const s = q.trim().toLowerCase()
    if (s) r = r.filter((x) => `${x.address} ${x.seller_address_text || ''} ${x.source_row || ''}`.toLowerCase().includes(s))
    if (filter === 'land') r = r.filter((x) => x.is_land)
    else if (filter === 'tenant' || filter === 'vacant') r = r.filter((x) => x.occupancy_status === filter)
    else if (filter === 'risk') r = r.filter((x) => x.review_status === 'needs_review' || ['low', 'insufficient'].includes(x.arv_confidence) || (x.mao !== null && x.target_offer !== null && x.target_offer > x.mao))
    else if (filter) r = r.filter((x) => x.status === filter)
    const by = {
      row: (a, b) => (a.source_row ?? 1e9) - (b.source_row ?? 1e9),
      spread: (a, b) => nullLast(b.estimated_profit) - nullLast(a.estimated_profit),
      yield: (a, b) => nullLast(b.gross_rent_yield_pct) - nullLast(a.gross_rent_yield_pct),
      repairs: (a, b) => (a.repair_ratio_pct ?? Infinity) - (b.repair_ratio_pct ?? Infinity),
      confidence: (a, b) => (CONF_RANK[b.arv_confidence] ?? -1) - (CONF_RANK[a.arv_confidence] ?? -1),
      asking: (a, b) => nullLast(b.seller_price) - nullLast(a.seller_price),
    }[sort]
    return [...r].sort(by)
  }, [data, q, filter, sort])

  const chosen = Object.keys(sel).filter((k) => sel[k])
  const p = data?.portfolio
  const s = data?.summary

  async function act(fn, msg) {
    setError(null); setNotice(null)
    try { const out = await fn(); setNotice(typeof msg === 'function' ? msg(out) : msg); await load() } catch (e) { setError(errText(e)) }
  }

  async function newPackage(fromSelection) {
    const name = window.prompt('Name this package', fromSelection ? `${chosen.length} selected properties` : 'Entire portfolio')
    if (!name) return
    await act(async () => {
      const out = await api.post(`/wholesale/portfolios/${id}/packages`, fromSelection ? { name, item_ids: chosen } : { name, all: true })
      setPkgFocus(out.created_id); setTab('packages'); setSel({})
      return out
    }, `Package "${name}" saved as a draft.`)
  }

  async function removeSelected() {
    if (!window.confirm(`Take ${chosen.length} propert${chosen.length === 1 ? 'y' : 'ies'} out of this portfolio? The property records are kept.`)) return
    try {
      await api.post(`/wholesale/portfolios/${id}/items/remove`, { item_ids: chosen })
    } catch (e) {
      if (e.status === 409 && window.confirm(`${errText(e)}\n\nRemove from those packages too?`)) {
        await act(() => api.post(`/wholesale/portfolios/${id}/items/remove`, { item_ids: chosen, also_from_packages: true }), 'Removed.')
      } else setError(errText(e))
      return
    }
    setSel({}); setNotice('Removed. The property records are kept.'); load()
  }

  if (!data) return <EvoApp world="acquisition"><Alert>{error}</Alert>{error ? null : <Skeleton rows={6} height={34} />}</EvoApp>

  return (
    <EvoApp world="acquisition">
      <Hero compact scene="estate" eyebrow={<><Link to="/wholesale/portfolios" className="pf-crumb">Seller Portfolios</Link> · {p.label}</>}
            title={p.name}
            sub={[p.seller_name ? `Seller: ${p.seller_name}` : 'Seller not set', p.source ? `Source: ${p.source}` : null].filter(Boolean).join(' · ')}
            meta={[{ label: p.status_label }, { label: <><b>{s.properties}</b> properties</> },
              { label: <><b>{s.evaluated}</b> evaluated · {s.awaiting_valuation} awaiting valuation</> },
              p.archived ? { label: 'Archived', tone: 'paused' } : null, p.is_test ? { label: 'Sandbox', tone: 'paused' } : null]}
            actions={<>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setDrawer('import')}>Import seller list</button>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setDrawer('add')}>Add existing</button>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setDrawer('edit')}>Edit</button>
            </>} />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <TabBar label="Portfolio views" value={tab} onChange={setTab}
              items={[{ key: 'inventory', label: 'Inventory', count: s.properties },
                { key: 'packages', label: 'Packages', count: (pk?.packages || []).filter((k) => k.status === 'draft').length },
                { key: 'summary', label: 'Summary' }]}
              actions={<button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
                               onClick={() => download(`/wholesale/portfolios/${id}/export.csv`, `portfolio-${String(p.number).padStart(3, '0')}-inventory.csv`).catch((e) => setError(errText(e)))}>Export inventory</button>} />

      {tab === 'inventory' ? (
        <div role="tabpanel" id="panel-inventory" aria-labelledby="tab-inventory">
          <Flow s={s} drafts={(pk?.packages || []).filter((k) => k.status === 'draft').length} chosen={chosen.length}
                onImport={() => setDrawer('import')} onReview={() => setFilter('research_needed')}
                onPackage={() => newPackage(chosen.length > 0)} />
          {/* Two kinds of number, never mixed: what the SELLER says, and OUR
              assumptions. Each total says how many properties it covers. */}
          <div className="pf-bands">
            <section className="pf-band" aria-label="Seller stated">
              <h3>Seller stated <span>as given, not verified</span></h3>
              <Metrics label="Seller stated totals">
                <Metric label="Asking" value={money(s.seller_asking.total)} sub={<Partial t={s.seller_asking} of={s.properties} />} attention={!!s.seller_asking.missing} />
                <Metric label="Rent / month" value={money(s.seller_rent_monthly.total)} sub={<>scheduled, not collected · <Partial t={s.seller_rent_monthly} of={s.seller_rent_monthly.of} />{s.land ? ` · ${s.land} land excluded` : ''}</>} />
              </Metrics>
            </section>
            <section className="pf-band pf-band--ours" aria-label="Our assumptions">
              <h3>Our assumptions <span>internal estimates</span></h3>
              <Metrics label="Our assumption totals">
                <Metric label="Estimated value" tone="info" value={money(s.estimated_value.total)} sub={<Partial t={s.estimated_value} of={s.properties} />} attention={s.estimated_value.partial} />
                <Metric label="Repairs" value={money(s.repairs.total)} sub={<Partial t={s.repairs} of={s.properties} />} attention={!!s.repairs.missing} />
                <Metric label="Target offers" tone="primary" value={money(s.target_offers.total)} sub={<Partial t={s.target_offers} of={s.properties} />} attention={!!s.target_offers.missing} />
                <Metric label="Spread" tone="success" value={money(s.estimated_spread.total)} sub={<>evaluated only · {s.estimated_spread.counted} of {s.properties}</>} />
              </Metrics>
            </section>
          </div>
          <Panel flush>
            <div className="evo-filterbar">
              <label className="evo-search"><span className="evo-sr">Search this portfolio</span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
                <input className="evo-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search address or row" /></label>
              <select aria-label="Filter properties" className="evo-select" value={filter} onChange={(e) => setFilter(e.target.value)}>
                {FILTERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
              <select aria-label="Sort properties" className="evo-select" value={sort} onChange={(e) => setSort(e.target.value)}>
                {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
              <span className="pf-selbar">
                <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={!chosen.length} onClick={() => newPackage(true)}>
                  {chosen.length ? `Package ${chosen.length} selected` : 'Select to build a package'}</button>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={!chosen.length} onClick={() => setDrawer('assign')}>Assign</button>
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={!chosen.length} onClick={removeSelected}>Remove</button>
              </span>
            </div>
            {sort !== 'row' ? <p className="pf-rank">{{
              spread: 'Ranked by estimated profit at our target offer = value − offer − repairs − transaction costs. Properties without all four are listed last.',
              yield: 'Ranked by the seller-stated rent × 12 ÷ our target offer. Gross, before any expense; the rent is not verified.',
              repairs: 'Ranked by repairs ÷ value. Properties without both are listed last.',
              confidence: 'Ranked by the valuation confidence on file (high, medium, low, insufficient).',
              asking: 'Ordered by the seller’s stated price.',
            }[sort]}</p> : null}
            {!data.rows.length ? (
              <Empty title="No properties in this portfolio yet"
                     action={<div className="evo-actionbar" style={{ justifyContent: 'center' }}>
                       <button type="button" className="evo-btn evo-btn--primary" onClick={() => setDrawer('import')}>Import seller list</button>
                       <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setDrawer('add')}>Add existing properties</button></div>}>
                Import the seller’s list (CSV or Excel), type rows from a photo of it, or link properties already in My Deal Room.
              </Empty>
            ) : (
              <div className="evo-table-wrap">
                <table className="evo-table evo-table--cards pf-table">
                  <caption className="evo-sr">{rows.length} properties</caption>
                  <thead><tr className="pf-thgroup">
                    <th colSpan={3} aria-hidden="true" />
                    <th colSpan={2} scope="colgroup" className="pf-th-seller">Seller stated</th>
                    <th colSpan={4} scope="colgroup" className="pf-th-ours">Our assumptions</th>
                    <th aria-hidden="true" />
                  </tr><tr>
                    <th scope="col" style={{ width: 36 }}><input type="checkbox" aria-label="Select all shown"
                      checked={rows.length > 0 && rows.every((r) => sel[r.item_id])}
                      onChange={(e) => setSel(e.target.checked ? Object.fromEntries(rows.map((r) => [r.item_id, true])) : {})} /></th>
                    <th scope="col">Property</th><th scope="col">Type</th>
                    <th scope="col" className="is-right">Seller asking</th><th scope="col" className="is-right">Stated rent</th>
                    <th scope="col" className="is-right">Est. value</th><th scope="col" className="is-right">Repairs</th>
                    <th scope="col" className="is-right">Our target offer</th><th scope="col" className="is-right">Potential spread</th>
                    <th scope="col">Status</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.item_id} className="is-link" onClick={() => setOpen(r.item_id)}>
                        <td data-label="Select" onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={!!sel[r.item_id]} aria-label={`Select ${r.address}`}
                                 onChange={(e) => setSel((x) => ({ ...x, [r.item_id]: e.target.checked }))} /></td>
                        <td className="is-lead" data-label="">
                          <button type="button" className="evo-prop__addr pf-linkbtn" onClick={(e) => { e.stopPropagation(); setOpen(r.item_id) }}>
                            {r.source_row ? <span className="pf-rownum">#{r.source_row}</span> : null}{r.address}</button>
                          <span className="evo-prop__sub">{r.occupancy_status === 'unknown' ? 'Occupancy unknown' : fmtLabel(r.occupancy_status)}{r.review_status === 'needs_review' ? ' · listing needs review' : ''}{r.extraction_confidence === 'low' ? ' · low confidence' : ''}</span>
                        </td>
                        <td data-label="Type">{r.is_land ? <Tag>Land</Tag> : fmtLabel(r.property_type || r.seller_type, '—')}</td>
                        <td data-label="Seller asking" className="is-right pf-td-seller">{has(r.seller_price) ? money(r.seller_price) : <Miss what="Not stated" />}</td>
                        <td data-label="Stated rent" className="is-right pf-td-seller">{r.is_land ? <span className="evo-muted">n/a</span> : has(r.seller_rent) ? money(r.seller_rent) : <Miss what="Not stated" />}</td>
                        <td data-label="Est. value" className="is-right">{r.arv === null ? <Miss what="Research needed" />
                          : <>{money(r.arv)}<span className="evo-prop__sub">{fmtLabel(r.arv_confidence || r.arv_source)}</span></>}</td>
                        <td data-label="Repairs" className="is-right">{has(r.repairs) ? <>{money(r.repairs)}<span className="evo-prop__sub">{r.repair_status_label}</span></> : <Miss />}</td>
                        <td data-label="Our target offer" className="is-right">{has(r.target_offer) ? money(r.target_offer) : <Miss />}
                          {r.mao !== null ? <span className="evo-prop__sub">MAO {money(r.mao)}</span> : null}</td>
                        <td data-label="Potential spread" className="is-right">{has(r.estimated_profit) ? money(r.estimated_profit) : <Miss what="Needs all four" />}</td>
                        <td data-label="Status"><Status status={r.status} label={r.status_label} tone={STATUS_TONE[r.status]} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <p className="pf-foot">{data.notice} Seller figures are as the seller stated them.</p>
        </div>
      ) : null}

      {tab === 'packages' ? (
        <Packages id={id} rows={data.rows} pk={pk} focus={pkgFocus} setFocus={setPkgFocus} reload={load}
                  onError={setError} onNotice={setNotice} newPackage={newPackage} portfolio={p} />
      ) : null}

      {tab === 'summary' ? <Summary data={data} pk={pk} /> : null}

      {open ? <Evaluation id={id} itemId={open} onClose={() => setOpen(null)} onChanged={load} /> : null}
      <ImportDrawer open={drawer === 'import'} id={id} onClose={() => setDrawer(null)} onDone={(msg) => { setNotice(msg); load() }} />
      <AddExisting open={drawer === 'add'} id={id} have={new Set(data.rows.map((r) => r.property_id))} onClose={() => setDrawer(null)}
                   onDone={(msg) => { setDrawer(null); setNotice(msg); load() }} />
      <Assign open={drawer === 'assign'} id={id} ids={chosen} onClose={() => setDrawer(null)}
              onDone={(msg) => { setDrawer(null); setSel({}); setNotice(msg); load() }} />
      <PortfolioForm open={drawer === 'edit'} portfolio={p} onClose={() => setDrawer(null)} onSaved={() => { setDrawer(null); load() }} />
    </EvoApp>
  )
}

/* ── C. Individual property evaluation ─────────────────────────────────── */
function Evaluation({ id, itemId, onClose, onChanged }) {
  const [ev, setEv] = useState(null)
  const [err, setErr] = useState(null)
  const [form, setForm] = useState({ arv: '', repairs: '', target_offer: '', as_is_value: '', occupancy_status: '', source: 'manual', note: '' })
  const [busy, setBusy] = useState(false)
  const [listing, setListing] = useState(null)
  const base = `/wholesale/portfolios/${id}/items/${itemId}`
  const load = useCallback(() => api.get(`${base}/evaluation`).then(setEv).catch((e) => setErr(errText(e))), [base])
  useEffect(() => { load() }, [load])

  async function save(e) {
    e.preventDefault(); setBusy(true); setErr(null)
    const body = { source: form.source, note: form.note }
    for (const k of ['arv', 'repairs', 'target_offer', 'as_is_value']) if (form[k] !== '') body[k] = Number(form[k])
    if (form.occupancy_status) body.occupancy_status = form.occupancy_status
    try {
      setEv(await api.post(`${base}/evaluation`, body))
      setForm({ arv: '', repairs: '', target_offer: '', as_is_value: '', occupancy_status: '', source: 'manual', note: '' })
      onChanged()
    } catch (x) { setErr(errText(x)) } finally { setBusy(false) }
  }
  async function saveListing() {
    setBusy(true); setErr(null)
    try {
      await api.patch(base, { seller_price: listing.price === '' ? null : Number(listing.price), price_note: listing.note,
                              seller_rent: listing.rent === '' ? null : Number(listing.rent), review_status: listing.review })
      setListing(null); await load(); onChanged()
    } catch (x) { setErr(errText(x)) } finally { setBusy(false) }
  }

  const r = ev?.row
  const a = ev?.analysis
  const base_ = a?.scenarios?.base
  const needsNote = ['arv', 'repairs', 'as_is_value'].some((k) => form[k] !== '')
  return (
    <Drawer open wide onClose={onClose} title={r ? r.address : 'Property evaluation'}
            sub={r ? `${ev.portfolio.label}${r.source_row ? ` · seller’s row ${r.source_row}` : ''} · ${r.status_label}` : null}
            footer={<>
              {r?.deal_id ? <Link className="evo-btn evo-btn--ghost" to={`/wholesale/deals/${r.deal_id}`}>Open full deal room</Link> : null}
              <button type="button" className="evo-btn evo-btn--ghost" disabled={!r}
                      onClick={() => download(`${base}/export.csv`, `property-${r.source_row || 'evaluation'}.csv`).catch((e) => setErr(errText(e)))}>Export</button>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={onClose}>Done</button></>}>
      <Alert>{err}</Alert>
      {!ev ? <Skeleton rows={6} /> : (
        <div className="pf-eval">
          <section className="pf-card">
            <h3>Seller’s listing <Tag kind="estimate">not verified</Tag></h3>
            {listing ? (
              <div className="pf-grid">
                <Field label="Asking price"><input className="evo-input" inputMode="decimal" value={listing.price} onChange={(e) => setListing({ ...listing, price: e.target.value })} /></Field>
                <Field label="Stated rent / month"><input className="evo-input" inputMode="decimal" value={listing.rent} onChange={(e) => setListing({ ...listing, rent: e.target.value })} /></Field>
                <Field label="Why the price changed"><input className="evo-input" value={listing.note} onChange={(e) => setListing({ ...listing, note: e.target.value })} /></Field>
                <Field label="Listing review"><select className="evo-select" value={listing.review} onChange={(e) => setListing({ ...listing, review: e.target.value })}>
                  <option value="needs_review">Needs review</option><option value="reviewed">Checked against the original</option></select></Field>
                <div className="evo-actionbar"><button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={saveListing}>Save listing</button>
                  <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setListing(null)}>Cancel</button></div>
              </div>
            ) : (
              <dl className="pf-dl">
                <div><dt>Asking price</dt><dd>{money(ev.seller.stated_price)}</dd></div>
                <div><dt>Stated rent</dt><dd>{r.is_land ? 'n/a (land)' : money(ev.seller.stated_rent)}</dd></div>
                <div><dt>As listed</dt><dd>{ev.seller.address_as_listed || '—'}{ev.seller.type_as_listed ? ` · ${ev.seller.type_as_listed}` : ''}</dd></div>
                <div><dt>Source</dt><dd>{ev.seller.source_ref || '—'}{ev.seller.source_row ? `, row ${ev.seller.source_row}` : ''} · {fmtDate(ev.seller.source_date)}</dd></div>
                <div><dt>Confidence</dt><dd>{fmtLabel(ev.seller.confidence)} · {ev.seller.review_status === 'reviewed' ? 'checked' : 'needs review'}</dd></div>
                {ev.seller.notes ? <div><dt>Seller notes</dt><dd>{ev.seller.notes}</dd></div> : null}
                {ev.seller.price_history.length > 1 ? <div><dt>Price history</dt><dd>{ev.seller.price_history.map((h) => `${money(h.price)} (${fmtDate(h.at)}${h.note ? ', ' + h.note : ''})`).join(' → ')}</dd></div> : null}
                <button type="button" className="evo-link" onClick={() => setListing({ price: ev.seller.stated_price ?? '', rent: ev.seller.stated_rent ?? '', note: '', review: ev.seller.review_status })}>Edit listing</button>
              </dl>
            )}
          </section>

          <section className="pf-card">
            <h3>Property</h3>
            <dl className="pf-dl">
              <div><dt>Type</dt><dd>{ev.property.property_type ? fmtLabel(ev.property.property_type) : ev.seller.type_as_listed ? `${ev.seller.type_as_listed} (as listed)` : '—'}</dd></div>
              <div><dt>Parcel / APN</dt><dd>{ev.property.parcel_apn ? `${ev.property.parcel_apn} (not verified)` : '—'}</dd></div>
              {!r.is_land ? <div><dt>Beds / baths</dt><dd>{ev.property.bedrooms ?? '?'} / {ev.property.bathrooms ?? '?'}</dd></div> : null}
              <div><dt>Size</dt><dd>{ev.property.square_feet ? `${ev.property.square_feet.toLocaleString()} sq ft` : '—'}{ev.property.lot_size_sqft ? ` · lot ${ev.property.lot_size_sqft.toLocaleString()} sq ft` : ''}</dd></div>
              <div><dt>Year built</dt><dd>{ev.property.year_built || '—'}</dd></div>
              <div><dt>Occupancy</dt><dd>{fmtLabel(ev.property.occupancy_status)}</dd></div>
              <div><dt>Facts from</dt><dd>{ev.property.facts_detail || ev.property.facts_source || '—'}</dd></div>
            </dl>
          </section>

          <section className="pf-card">
            <h3>Valuation</h3>
            <dl className="pf-dl">
              <div><dt>Value (ARV)</dt><dd>{r.arv === null ? <b>Research needed</b> : <>{money(r.arv)} · {fmtLabel(r.arv_source)}{r.arv_confidence ? ` · ${fmtLabel(r.arv_confidence)} confidence` : ''}</>}</dd></div>
              <div><dt>Valuation date</dt><dd>{r.valuation_at ? fmtDate(r.valuation_at) : '—'}</dd></div>
              <div><dt>As-is value</dt><dd>{money(r.as_is_value)}{r.as_is_value_source ? ` · ${fmtLabel(r.as_is_value_source)} (reference only)` : ''}</dd></div>
              <div><dt>Comparable sales</dt><dd>{a?.comps ? `${(a.comps.used || []).length} used${(a.comps.excluded || []).length ? `, ${a.comps.excluded.length} excluded` : ''}` : '—'}
                {r.deal_id ? <> · <Link to={`/wholesale/deals/${r.deal_id}?tab=underwriting`}>add or review comps</Link></> : null}</dd></div>
              {(a?.arv?.limitations || []).length ? <div><dt>Limits</dt><dd>{a.arv.limitations.join('; ')}</dd></div> : null}
            </dl>
          </section>

          <section className="pf-card">
            <h3>Costs and our numbers</h3>
            <dl className="pf-dl">
              <div><dt>Repairs</dt><dd>{money(r.repairs)} · {r.repair_status_label}</dd></div>
              <div><dt>Transaction costs</dt><dd>{money(r.transaction_costs)}</dd></div>
              {base_?.flip?.available ? <div><dt>Holding (flip view)</dt><dd>{money(base_.flip.holding)} · selling {money(base_.flip.selling)}</dd></div> : null}
              <div><dt>Maximum allowable offer</dt><dd>{r.mao !== null ? money(r.mao) : <span className="evo-muted">{r.mao_note || 'not calculated'}</span>}</dd></div>
              <div><dt>Our target offer</dt><dd>{money(r.target_offer)}{r.target_offer !== null && r.mao !== null && r.target_offer > r.mao ? <Tag kind="danger">above MAO</Tag> : null}</dd></div>
              <div><dt>Estimated profit</dt><dd>{money(r.estimated_profit)}{r.estimated_profit !== null ? <span className="evo-prop__sub"> = value − offer − repairs − transaction costs</span> : null}</dd></div>
              <div><dt>Discount from asking</dt><dd>{money(r.discount_from_asking)}</dd></div>
              {base_?.rental?.available ? <div><dt>Rental (stated rent)</dt><dd>NOI {money(base_.rental.noi_annual)}/yr · cash flow {money(base_.rental.cash_flow_monthly)}/mo · cap {pct(base_.rental.cap_rate_pct)}</dd></div>
                : !r.is_land ? <div><dt>Rental</dt><dd className="evo-muted">{base_?.rental?.why || 'needs rent, repairs and a purchase price'}</dd></div> : null}
            </dl>
            {ev.rent_note ? <p className="pf-note">{ev.rent_note}</p> : null}
            {(a?.offer?.steps || []).length ? (
              <details className="pf-steps"><summary>How the maximum allowable offer is worked</summary>
                <ol>{a.offer.steps.map((st, i) => <li key={i}>{st.label} <b>{st.value === null ? '—' : (st.label.includes('%') ? `${st.value}%` : money(st.value))}</b>{st.note ? <span className="evo-muted"> · {st.note}</span> : null}</li>)}</ol>
              </details>) : null}
            {(a?.missing || []).length ? <p className="pf-note"><b>Still missing:</b> {a.missing.join(' · ')}</p> : null}
          </section>

          <section className="pf-card pf-card--wide">
            <h3>Change a number</h3>
            <p className="evo-muted pf-small">Your figure replaces the estimate on this property’s deal. The previous figure is kept in the history below.</p>
            <form className="pf-grid" onSubmit={save}>
              <Field label="Value (ARV)"><input className="evo-input" inputMode="decimal" value={form.arv} placeholder={r.arv ?? ''} onChange={(e) => setForm({ ...form, arv: e.target.value })} /></Field>
              <Field label="Repairs"><input className="evo-input" inputMode="decimal" value={form.repairs} placeholder={r.repairs ?? ''} onChange={(e) => setForm({ ...form, repairs: e.target.value })} /></Field>
              <Field label="Our target offer"><input className="evo-input" inputMode="decimal" value={form.target_offer} placeholder={r.target_offer ?? ''} onChange={(e) => setForm({ ...form, target_offer: e.target.value })} /></Field>
              <Field label="As-is value"><input className="evo-input" inputMode="decimal" value={form.as_is_value} placeholder={r.as_is_value ?? ''} onChange={(e) => setForm({ ...form, as_is_value: e.target.value })} /></Field>
              <Field label="Occupancy"><select className="evo-select" value={form.occupancy_status} onChange={(e) => setForm({ ...form, occupancy_status: e.target.value })}>
                <option value="">No change</option><option value="tenant">Occupied (tenant)</option><option value="owner_occupied">Owner occupied</option><option value="vacant">Vacant</option><option value="unknown">Unknown</option></select></Field>
              <Field label="Source"><select className="evo-select" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                <option value="manual">My estimate</option><option value="verified">Verified against a primary source</option></select></Field>
              <Field label={needsNote ? 'Where this number comes from (required)' : 'Note'} full>
                <input className="evo-input" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="e.g. three closed sales within 0.5 mi, contractor walk-through" /></Field>
              <div className="evo-actionbar"><button type="submit" className="evo-btn evo-btn--primary" disabled={busy || (needsNote && !form.note.trim())}>{busy ? 'Saving…' : 'Save'}</button></div>
            </form>
          </section>

          {ev.history.length ? (
            <section className="pf-card pf-card--wide">
              <h3>History</h3>
              <ul className="pf-history">{ev.history.map((h, i) => <li key={i}><span className="evo-muted">{fmtDate(h.at)}</span> {h.summary}{h.note ? ` — “${h.note}”` : ''}</li>)}</ul>
            </section>) : null}
          <p className="pf-foot">{ev.notice}</p>
        </div>
      )}
    </Drawer>
  )
}

/* ── D. Package builder + scenario comparison ──────────────────────────── */
const DISCOUNT_LABELS = { none: 'No package discount (sum of offers)', percent: 'Percent off the asking total', amount: 'Dollar amount off the asking total', total: 'Negotiated package total' }

function Packages({ id, rows, pk, focus, setFocus, reload, onError, onNotice, newPackage, portfolio }) {
  const [showArchived, setShowArchived] = useState(false)
  const pkgs = (pk?.packages || []).filter((k) => showArchived || k.status === 'draft')
  const current = pkgs.find((k) => k.id === focus) || pkgs[0] || null
  const cmp = pk?.comparison

  return (
    <div role="tabpanel" id="panel-packages" aria-labelledby="tab-packages" className="pf-stack">
      {cmp && cmp.packages.length ? (
        <Panel title="Compare packages" hint={cmp.note}
               action={<button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
                               onClick={() => download(`/wholesale/portfolios/${id}/packages/compare/export.csv`, `portfolio-${String(portfolio.number).padStart(3, '0')}-comparison.csv`).catch((e) => onError(errText(e)))}>Export comparison</button>}>
          <div className="evo-table-wrap">
            <table className="evo-table pf-compare">
              <caption className="evo-sr">Package comparison</caption>
              <thead><tr><th scope="col">Figure</th>{cmp.packages.map((k) => <th key={k.id} scope="col" className="is-right">
                <button type="button" className="pf-linkbtn" onClick={() => setFocus(k.id)}>{k.name}</button>
                {!k.complete ? <span className="evo-prop__sub">partial</span> : null}{k.stale_lines ? <span className="evo-prop__sub">older figures</span> : null}</th>)}</tr></thead>
              <tbody>{cmp.rows.map((row) => (
                <tr key={row.key}><th scope="row">{row.label}</th>{row.values.map((v, i) => <td key={i} className="is-right">
                  {row.kind === '$' ? money(v) : row.kind === '%' ? pct(v) : (v ?? '—')}
                  {row.partial && row.partial[i] ? <span className="evo-prop__sub">partial</span> : null}</td>)}</tr>))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      <div className="pf-pkghead">
        <div className="pf-pkgtabs" role="group" aria-label="Packages">
          {pkgs.map((k) => <button key={k.id} type="button" className="evo-btn evo-btn--sm" aria-pressed={current && current.id === k.id}
                                   onClick={() => setFocus(k.id)}>{k.name}{k.status === 'archived' ? ' (archived)' : ''}</button>)}
          {!pkgs.length ? <span className="evo-muted">No packages yet.</span> : null}
        </div>
        <span className="pf-selbar">
          <label className="evo-btn evo-btn--ghost evo-btn--sm" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} style={{ margin: 0 }} /> Archived</label>
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={!rows.length} onClick={() => newPackage(false)}>+ Package of the whole portfolio</button>
        </span>
      </div>
      {!pkgs.length ? <Panel><Empty title="No purchase packages yet">Select properties on the Inventory tab and choose “Package selected”, or package the whole portfolio. Each package is its own scenario.</Empty></Panel> : null}
      {current ? <PackageEditor key={current.id} id={id} k={current} rows={rows} reload={reload} onError={onError} onNotice={onNotice} setFocus={setFocus} portfolio={portfolio} /> : null}
    </div>
  )
}

function PackageEditor({ id, k, rows, reload, onError, onNotice, setFocus, portfolio }) {
  const [mode, setMode] = useState(k.discount_mode)
  const [value, setValue] = useState(k.discount_value ?? '')
  const [offers, setOffers] = useState({})
  const [adding, setAdding] = useState(false)
  const [add, setAdd] = useState({})
  const url = `/wholesale/portfolios/${id}/packages/${k.id}`
  const t = k.totals
  const archived = k.status === 'archived'
  const inPkg = new Set(k.lines.map((x) => x.item_id))

  async function patch(body, msg) {
    onError(null)
    try { await api.patch(url, body); onNotice(msg); await reload() } catch (e) { onError(errText(e)) }
  }
  const dirtyOffers = Object.entries(offers).filter(([, v]) => v !== undefined)

  return (
    <Panel title={k.name} hint={archived ? 'Archived scenario — restore it to change it' : 'Draft scenario. Changes here never change another package.'}
           action={<span className="pf-selbar">
             {!archived ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={async () => {
               const name = window.prompt('Name the copy', `${k.name} (copy)`); if (!name) return
               try { const out = await api.post(`${url}/duplicate`, { name }); setFocus(out.created_id); onNotice(`Copied to “${name}”.`); await reload() } catch (e) { onError(errText(e)) }
             }}>Duplicate</button> : null}
             <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
                     onClick={() => download(`${url}/export.csv`, `package-${k.name.replace(/[^\w-]+/g, '-')}.csv`).catch((e) => onError(errText(e)))}>Export</button>
             <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
                     onClick={() => patch({ status: archived ? 'draft' : 'archived' }, archived ? 'Package restored.' : 'Package archived.')}>{archived ? 'Restore' : 'Archive'}</button>
           </span>}>
      {t.stale_lines ? (
        <Alert kind="warn">{t.stale_lines} line{t.stale_lines === 1 ? ' was' : 's were'} built on older figures (marked below). The saved offers are unchanged.
          {!archived ? <> <button type="button" className="evo-link" onClick={() => patch({ refresh_snapshots: true }, 'Figures refreshed; offers unchanged.')}>Use today’s figures</button></> : null}</Alert>) : null}

      <div className="pf-totals">
        <Metric label="Seller asking total" value={money(t.seller_asking)} sub={t.seller_asking_missing ? `${t.seller_asking_missing} without a price` : `${t.properties} properties`} />
        <Metric label="Individual offers" tone="primary" value={money(t.individual_offers)} sub={t.offers_missing ? `${t.offers_missing} offer${t.offers_missing === 1 ? '' : 's'} not set` : 'sum of the lines'} />
        <Metric label="Package offer" tone="primary" value={money(t.package_offer)} sub={t.package_offer_basis || '—'} />
        <Metric label="Estimated value" tone="info" value={money(t.estimated_value)} sub={t.estimated_value_missing ? `${t.estimated_value_missing} need research` : 'all valued'} attention={!!t.estimated_value_missing} />
        <Metric label="Repairs" value={money(t.repairs)} sub={t.repairs_missing ? `${t.repairs_missing} without repairs` : 'all estimated'} />
        <Metric label="Est. profit at package offer" tone="success" value={money(t.profit_at_package_offer)} sub={t.why_no_profit || `at individual offers: ${money(t.profit_at_individual_offers)}`} />
      </div>
      <p className="pf-small evo-muted">Discount from asking {money(t.discount_from_asking)}{t.discount_from_asking_pct !== null ? ` (${pct(t.discount_from_asking_pct)})` : ''} ·
        package vs individual offers {money(t.package_vs_individual)} · gross stated-rent yield {pct(t.gross_rent_yield_pct)}{t.offers_over_mao ? ` · ${t.offers_over_mao} offer${t.offers_over_mao === 1 ? '' : 's'} above MAO` : ''}.
        {(t.steps || []).map((st, i) => <span key={i}> {st}.</span>)}</p>

      {!archived ? (
        <div className="pf-discount">
          <Field label="Package pricing"><select className="evo-select" value={mode} onChange={(e) => setMode(e.target.value)}>
            {Object.entries(DISCOUNT_LABELS).map(([kk, l]) => <option key={kk} value={kk}>{l}</option>)}</select></Field>
          {mode !== 'none' ? <Field label={mode === 'percent' ? 'Percent' : mode === 'amount' ? 'Dollars off' : 'Package total'}>
            <input className="evo-input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></Field> : null}
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm"
                  onClick={() => patch({ discount_mode: mode, discount_value: mode === 'none' ? null : Number(value) }, 'Package pricing saved.')}>Apply</button>
        </div>) : null}

      <div className="evo-table-wrap">
        <table className="evo-table evo-table--cards pf-table">
          <caption className="evo-sr">Properties in {k.name}</caption>
          <thead><tr><th scope="col">Property</th><th scope="col" className="is-right">Seller asking</th><th scope="col" className="is-right">Est. value</th>
            <th scope="col" className="is-right">Repairs</th><th scope="col" className="is-right">MAO</th><th scope="col" className="is-right">This package’s offer</th>
            <th scope="col" className="is-right">Est. profit</th>{!archived ? <th scope="col"><span className="evo-sr">Remove</span></th> : null}</tr></thead>
          <tbody>{k.lines.map((x) => (
            <tr key={x.item_id}>
              <td className="is-lead" data-label="">{x.address}
                <span className="evo-prop__sub">{x.status_label}{x.is_land ? ' · land' : ''}{x.target_changed ? ' · target offer changed since added' : ''}</span>
                {x.stale.length ? <Tag kind="estimate" title={Object.entries(x.stale_detail).map(([key, v]) => `${key}: ${money(v.was)} → ${money(v.now)}`).join('; ')}>older figures</Tag> : null}</td>
              <td data-label="Seller asking" className="is-right">{money(x.seller_price)}</td>
              <td data-label="Est. value" className="is-right">{x.arv === null ? <span className="evo-muted">Research needed</span> : money(x.arv)}</td>
              <td data-label="Repairs" className="is-right">{money(x.repairs)}</td>
              <td data-label="MAO" className="is-right">{money(x.mao)}</td>
              <td data-label="This package’s offer" className="is-right">
                {archived ? money(x.offer) : <input className="evo-input pf-offer" inputMode="decimal" aria-label={`Offer for ${x.address}`}
                  value={offers[x.item_id] ?? (x.offer ?? '')} onChange={(e) => setOffers((o) => ({ ...o, [x.item_id]: e.target.value }))} />}
                {x.offer_over_mao ? <span className="evo-prop__sub pf-warn">above MAO</span> : null}</td>
              <td data-label="Est. profit" className="is-right">{money(x.estimated_profit)}</td>
              {!archived ? <td><button type="button" className="evo-link" onClick={() => patch({ remove_item_ids: [x.item_id] }, 'Removed from this package.')}>Remove</button></td> : null}
            </tr>))}</tbody>
        </table>
      </div>
      {!archived ? (
        <div className="evo-actionbar pf-pkgactions">
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={!dirtyOffers.length}
                  onClick={async () => { await patch({ offers: dirtyOffers.map(([item_id, v]) => ({ item_id, offer_amount: v === '' ? null : Number(v) })) }, 'Offers saved for this package only.'); setOffers({}) }}>Save offers</button>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setAdding((v) => !v)}>{adding ? 'Close' : 'Add properties'}</button>
        </div>) : null}
      {adding ? (
        <div className="pf-addlist">
          {rows.filter((r) => !inPkg.has(r.item_id)).map((r) => (
            <label key={r.item_id} className="ws-checkbox"><input type="checkbox" checked={!!add[r.item_id]} onChange={(e) => setAdd((a) => ({ ...a, [r.item_id]: e.target.checked }))} />
              {r.source_row ? `#${r.source_row} ` : ''}{r.address} · {money(r.seller_price)}</label>))}
          {rows.every((r) => inPkg.has(r.item_id)) ? <p className="evo-muted">Every property in the portfolio is already in this package.</p> : null}
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={!Object.values(add).some(Boolean)}
                  onClick={async () => { await patch({ add_item_ids: Object.keys(add).filter((i) => add[i]) }, 'Added to this package.'); setAdd({}); setAdding(false) }}>Add to package</button>
        </div>) : null}
      <p className="pf-foot">Internal investment estimate. Seller figures are as stated. Offers here belong to this package only.</p>
    </Panel>
  )
}

/* ── E. Portfolio summary ──────────────────────────────────────────────── */
function Summary({ data, pk }) {
  const s = data.summary
  const rows = data.rows
  const top = (key, desc = true, n = 5) => rows.filter((r) => r[key] !== null && r[key] !== undefined)
    .sort((a, b) => (desc ? b[key] - a[key] : a[key] - b[key])).slice(0, n)
  const groups = [
    ['Best potential spread', top('estimated_profit'), (r) => money(r.estimated_profit), 'value − target offer − repairs − transaction costs'],
    ['Best stated rent yield', top('gross_rent_yield_pct'), (r) => pct(r.gross_rent_yield_pct), 'seller-stated rent × 12 ÷ target offer, gross'],
    ['Lowest repair burden', top('repair_ratio_pct', false), (r) => pct(r.repair_ratio_pct), 'repairs ÷ value'],
  ]
  const a = data.assumptions
  return (
    <div role="tabpanel" id="panel-summary" aria-labelledby="tab-summary" className="pf-stack">
      <div className="pf-cols">
        <Panel title="Where the portfolio stands">
          <dl className="pf-dl">
            <div><dt>Properties</dt><dd>{s.properties} ({s.land} land)</dd></div>
            {Object.entries(s.by_status).map(([k, n]) => <div key={k}><dt>{{ research_needed: 'Research needed', repairs_needed: 'Repairs needed', offer_needed: 'Offer not set', evaluated: 'Evaluated' }[k]}</dt><dd>{n}</dd></div>)}
            <div><dt>Seller asking</dt><dd>{money(s.seller_asking.total)} <span className="evo-muted">(seller stated)</span></dd></div>
            <div><dt>Stated rent / month</dt><dd>{money(s.seller_rent_monthly.total)} <span className="evo-muted">(not collected rent)</span></dd></div>
            <div><dt>Estimated value</dt><dd>{money(s.estimated_value.total)} {s.estimated_value.partial ? <span className="evo-muted">(partial: {s.estimated_value.counted} of {s.properties})</span> : null}</dd></div>
            <div><dt>Our target offers</dt><dd>{money(s.target_offers.total)} {s.target_offers.partial ? <span className="evo-muted">(partial)</span> : null}</dd></div>
            <div><dt>Portfolio target (set by hand)</dt><dd>{money(data.portfolio.target_offer_total)}</dd></div>
            <div><dt>Draft packages</dt><dd>{(pk?.packages || []).filter((k) => k.status === 'draft').length}</dd></div>
          </dl>
        </Panel>
        <Panel title="Assumptions in use" hint={a.note}>
          <dl className="pf-dl">
            <div><dt>Investor percentage (MAO)</dt><dd>{a.investor_percentage !== null ? `${a.investor_percentage}%` : 'not set'}</dd></div>
            <div><dt>Wholesale fee (MAO)</dt><dd>{money(a.default_wholesale_fee)}</dd></div>
            <div><dt>Transaction costs</dt><dd>{a.transaction_cost_percent}% of value + {money(a.transaction_cost_flat)}</dd></div>
            <div><dt>Estimated profit</dt><dd>value − offer − repairs − transaction costs</dd></div>
          </dl>
          <p className="pf-small evo-muted">No percentage is applied to every property behind the scenes. Rental figures use the seller’s stated rent until a verified rent is entered on the property.</p>
        </Panel>
      </div>
      <Panel title="Prioritize" hint="Only properties with the figures behind each list are ranked">
        <div className="pf-cols pf-cols--3">
          {groups.map(([title, list, fmt, how]) => (
            <div key={title} className="pf-card"><h3>{title}</h3><p className="pf-small evo-muted">{how}</p>
              {list.length ? <ol className="pf-rank-list">{list.map((r) => <li key={r.item_id}>{r.address} <b>{fmt(r)}</b></li>)}</ol>
                : <p className="evo-muted pf-small">Not enough figures yet.</p>}</div>))}
        </div>
      </Panel>
      <p className="pf-foot">{data.notice}</p>
    </div>
  )
}

/* ── import: review before commit ──────────────────────────────────────── */
function ImportDrawer({ open, id, onClose, onDone }) {
  const [mode, setMode] = useState('file')
  const [file, setFile] = useState(null)
  const [reference, setReference] = useState('')
  const [typed, setTyped] = useState([{ source_row: '1', address: '', city: '', zip_code: '', seller_price: '', seller_rent: '', seller_type: '' }])
  const [src, setSrc] = useState(null)
  const [hist, setHist] = useState([])
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const base = `/wholesale/portfolios/${id}/imports`
  const loadHist = useCallback(() => api.get(base).then((d) => setHist(d.imports)).catch(() => {}), [base])
  useEffect(() => { if (open) { setSrc(null); setErr(null); loadHist() } }, [open, loadHist])

  async function stage() {
    setBusy(true); setErr(null)
    try {
      if (mode === 'file') {
        const fd = new FormData(); fd.append('file', file); if (reference) fd.append('reference', reference)
        setSrc(await api.upload(base, fd))
      } else {
        setSrc(await api.post(`${base}/rows`, { kind: mode, reference, rows: typed.filter((r) => Object.values(r).some((v) => String(v).trim())) }))
      }
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function editRow(n, body) {
    setErr(null)
    try { setSrc(await api.patch(`${base}/${src.id}/rows/${n}`, body)) } catch (e) { setErr(errText(e)) }
  }
  async function commit() {
    setBusy(true); setErr(null)
    try {
      const out = await api.post(`${base}/${src.id}/commit`)
      const r = out.result
      onDone(`Imported: ${r.properties_created} new propert${r.properties_created === 1 ? 'y' : 'ies'}, ${r.properties_linked} linked, ${r.skipped} skipped. Nothing was sent to anyone.`)
      setSrc(null); loadHist()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function undo(sid) {
    if (!window.confirm('Undo this import? Its portfolio links are removed. Properties nobody has worked on since are deleted; any with recorded work are kept.')) return
    try {
      const out = await api.post(`${base}/${sid}/undo`)
      onDone(`Import undone: ${out.result.properties_deleted} properties removed${out.result.properties_kept.length ? `, ${out.result.properties_kept.length} kept because work was recorded on them` : ''}.`)
      loadHist()
    } catch (e) { setErr(errText(e)) }
  }
  const setT = (i, k, v) => setTyped((rows) => rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  const DEC = { create: 'Create new', link: 'Link to existing', skip: 'Skip', fix: 'Needs a correction', decide: 'Decide' }

  return (
    <Drawer open={open} wide onClose={onClose} title="Import the seller’s list"
            sub="Nothing is created until you review the preview and choose Import. Nobody is contacted."
            footer={src ? <>
              <button type="button" className="evo-btn evo-btn--ghost" onClick={async () => { await api.post(`${base}/${src.id}/discard`).catch(() => {}); setSrc(null); loadHist() }}>Discard</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={!src.ready || busy} onClick={commit}>
                {src.ready ? `Import ${src.row_count - (src.counts.skip || 0)} rows` : 'Resolve the flagged rows first'}</button></>
              : <><button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Close</button>
                <button type="button" className="evo-btn evo-btn--primary" disabled={busy || (mode === 'file' ? !file : (mode === 'photo' && !reference.trim()))} onClick={stage}>{busy ? 'Reading…' : 'Preview'}</button></>}>
      <Alert>{err}</Alert>
      {!src ? (
        <div className="pf-stack">
          <div className="evo-seg" role="group" aria-label="Source">
            {[['file', 'CSV or Excel file'], ['photo', 'Rows read off a photo'], ['manual', 'Type rows']].map(([k, l]) =>
              <button key={k} type="button" className="evo-seg__btn" aria-pressed={mode === k} onClick={() => setMode(k)}>{l}</button>)}
          </div>
          <Field label={mode === 'photo' ? 'Which image (required)' : 'Where the list came from'} hint="Kept with every property from this list">
            <input className="evo-input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder={mode === 'photo' ? 'e.g. seller sheet photo 1 of 2' : 'e.g. seller’s printed list, Nov 2026'} /></Field>
          {mode === 'file' ? (
            <Field label="File (.csv or .xlsx)" hint="Columns such as No., Address, City, Zip, Price, Rent, Type and Notes are recognized. Tenant names and phone numbers are not read.">
              <input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(e) => setFile(e.target.files[0])} /></Field>
          ) : (
            <div className="evo-table-wrap">
              <p className="pf-small evo-muted">{mode === 'photo' ? 'Rows typed from a photo start at low confidence and stay marked “needs review” until someone checks them against the image.' : 'Typed rows are marked “needs review”.'}</p>
              <table className="evo-table pf-entry"><thead><tr>{['Row', 'Address', 'City', 'ZIP', 'Price', 'Rent', 'Type'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
                <tbody>{typed.map((r, i) => <tr key={i}>{['source_row', 'address', 'city', 'zip_code', 'seller_price', 'seller_rent', 'seller_type'].map((k) =>
                  <td key={k}><input className="evo-input" aria-label={`${k} row ${i + 1}`} value={r[k]} onChange={(e) => setT(i, k, e.target.value)} /></td>)}</tr>)}</tbody></table>
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setTyped((t) => [...t, { source_row: String(t.length + 1), address: '', city: '', zip_code: '', seller_price: '', seller_rent: '', seller_type: '' }])}>+ Row</button>
            </div>
          )}
          {hist.length ? (
            <Panel title="Earlier imports">
              <ul className="pf-history">{hist.map((h) => <li key={h.id}>{fmtDate(h.created_at)} · {h.file_name || h.reference || fmtLabel(h.kind)} · {h.row_count} rows · <b>{fmtLabel(h.status)}</b>
                {h.status === 'committed' ? <> · <button type="button" className="evo-link" onClick={() => undo(h.id)}>Undo</button></> : null}</li>)}</ul>
            </Panel>) : null}
        </div>
      ) : (
        <div className="pf-stack">
          <p className="pf-small">{src.row_count} rows · stated asking {money(src.stated_totals.seller_price)}{src.stated_totals.seller_price_missing ? ` (${src.stated_totals.seller_price_missing} without a price)` : ''} · stated rent {money(src.stated_totals.seller_rent)}/mo — <i>{src.stated_totals.label}</i></p>
          {src.dropped_columns && src.dropped_columns.length ? <Alert kind="info">Not read (personal details): {src.dropped_columns.join(', ')}.</Alert> : null}
          <div className="evo-table-wrap">
            <table className="evo-table pf-preview">
              <caption className="evo-sr">Import preview</caption>
              <thead><tr><th scope="col">Row</th><th scope="col">Address</th><th scope="col">City / ZIP</th><th scope="col" className="is-right">Price</th>
                <th scope="col" className="is-right">Rent</th><th scope="col">Type</th><th scope="col">Check</th><th scope="col">Decision</th></tr></thead>
              <tbody>{src.rows.map((r) => (
                <PreviewRow key={r.n} r={r} onEdit={(body) => editRow(r.n, body)} label={DEC} />))}</tbody>
            </table>
          </div>
        </div>
      )}
    </Drawer>
  )
}

function PreviewRow({ r, onEdit, label }) {
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState({})
  useEffect(() => { setF({ address: r.address || '', city: r.city || '', zip_code: r.zip_code || '', seller_price: r.seller_price || '', seller_rent: r.seller_rent || '', seller_type: r.seller_type || '' }) }, [r])
  const flagged = r.decision === 'fix' || r.decision === 'decide'
  return (
    <tr className={flagged ? 'pf-flag' : ''}>
      <td>{r.source_row}</td>
      {edit ? (
        <>
          <td><input className="evo-input" aria-label="Address" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></td>
          <td><input className="evo-input" aria-label="City" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
            <input className="evo-input" aria-label="ZIP" value={f.zip_code} onChange={(e) => setF({ ...f, zip_code: e.target.value })} /></td>
          <td><input className="evo-input" aria-label="Price" value={f.seller_price} onChange={(e) => setF({ ...f, seller_price: e.target.value })} /></td>
          <td><input className="evo-input" aria-label="Rent" value={f.seller_rent} onChange={(e) => setF({ ...f, seller_rent: e.target.value })} /></td>
          <td><input className="evo-input" aria-label="Type" value={f.seller_type} onChange={(e) => setF({ ...f, seller_type: e.target.value })} /></td>
          <td colSpan={2}><button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={() => { onEdit(f); setEdit(false) }}>Save row</button>
            <button type="button" className="evo-link" onClick={() => setEdit(false)}>Cancel</button></td>
        </>
      ) : (
        <>
          <td>{r.address || <span className="evo-muted">missing</span>}{r.is_land ? <> <Tag>Land</Tag></> : null}
            {r.source_notes ? <span className="evo-prop__sub">{r.source_notes}</span> : null}</td>
          <td>{[r.city, r.zip_code].filter(Boolean).join(' ') || '—'}</td>
          <td className="is-right">{r.seller_price ? money(r.seller_price, r.seller_price) : '—'}</td>
          <td className="is-right">{r.seller_rent ? money(r.seller_rent, r.seller_rent) : '—'}</td>
          <td>{r.seller_type || '—'}</td>
          <td>{r.issues.length ? <ul className="pf-issues">{r.issues.map((i, k) => <li key={k}>{i.text}</li>)}</ul> : <span className="pf-ok">OK</span>}</td>
          <td>
            <select className="evo-select" aria-label={`Decision for row ${r.source_row}`} value={['create', 'link', 'skip'].includes(r.decision) ? r.decision : ''}
                    onChange={(e) => onEdit({ decision: e.target.value || null, link_property_id: e.target.value === 'link' ? (r.matches[0] || {}).property_id : null })}>
              <option value="">{label[r.decision] || 'Choose'}</option>
              <option value="create">Create new property</option>
              {r.matches.length ? <option value="link">Link to {r.matches[0].address}</option> : null}
              <option value="skip">Skip this row</option>
            </select>
            <button type="button" className="evo-link" onClick={() => setEdit(true)}>Correct</button>
          </td>
        </>
      )}
    </tr>
  )
}

function AddExisting({ open, id, have, onClose, onDone }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState([])
  const [pick, setPick] = useState({})
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!open) return undefined
    const t = setTimeout(() => {
      api.get('/wholesale/properties?limit=50&include_test=true' + (q ? `&q=${encodeURIComponent(q)}` : ''))
        .then((d) => setRes(d.properties || [])).catch((e) => setErr(errText(e)))
    }, 250)
    return () => clearTimeout(t)
  }, [open, q])
  const ids = Object.keys(pick).filter((k) => pick[k])
  return (
    <Drawer open={open} onClose={onClose} title="Add existing properties" sub="Links them to this portfolio. The property records are not copied."
            footer={<><button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={!ids.length} onClick={async () => {
                try { const out = await api.post(`/wholesale/portfolios/${id}/items/add`, { property_ids: ids }); setPick({}); onDone(`${out.added} propert${out.added === 1 ? 'y' : 'ies'} added.`) } catch (e) { setErr(errText(e)) }
              }}>Add {ids.length || ''}</button></>}>
      <Alert>{err}</Alert>
      <Field label="Search properties"><input className="evo-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Address, owner or APN" /></Field>
      <div className="pf-addlist">{res.map((p) => (
        <label key={p.id} className="ws-checkbox"><input type="checkbox" disabled={have.has(p.id)} checked={have.has(p.id) || !!pick[p.id]}
          onChange={(e) => setPick((x) => ({ ...x, [p.id]: e.target.checked }))} />{p.address || '(no address)'}{have.has(p.id) ? ' · already in this portfolio' : ''}</label>))}
        {!res.length ? <p className="evo-muted">No properties found.</p> : null}</div>
    </Drawer>
  )
}

function Assign({ open, id, ids, onClose, onDone }) {
  const [people, setPeople] = useState([])
  const [who, setWho] = useState('')
  const [err, setErr] = useState(null)
  useEffect(() => { if (open) api.get('/wholesale/portfolios/assignees').then((d) => setPeople(d.items)).catch((e) => setErr(errText(e))) }, [open])
  return (
    <Drawer open={open} onClose={onClose} title={`Assign ${ids.length} propert${ids.length === 1 ? 'y' : 'ies'} for research`}
            sub="The person sees these properties under their own access. Nothing is sent to them."
            footer={<><button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={!who} onClick={async () => {
                try { const out = await api.post(`/wholesale/portfolios/${id}/assign`, { item_ids: ids, user_id: who }); onDone(`${out.assigned} assigned to ${out.to.name}.`) } catch (e) { setErr(errText(e)) }
              }}>Assign</button></>}>
      <Alert>{err}</Alert>
      <Field label="Researcher"><select className="evo-select" value={who} onChange={(e) => setWho(e.target.value)}>
        <option value="">Choose a person</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
    </Drawer>
  )
}
