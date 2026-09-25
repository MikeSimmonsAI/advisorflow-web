/* WHOLESALE — PROPERTIES (Phase 7.2 redesign).
 *
 * The property workspace first: summary, search, filters, the list. Adding a
 * property and importing a list are intentional workflows in their own
 * drawers - they no longer sit permanently open above the list. Every
 * backend call is unchanged.
 *
 * Three ways in, all real: type one, import a CSV, or let EvoSense discover
 * and promote them. With no data provider connected the first two are not a
 * fallback, they ARE the intake, and the screen says so.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { AuthImage } from './wsFiles'
import '../../styles/shared.css'
import './wholesale.css'
import { errText, fmtDate, fmtLabel, fmtMoney, fmtNum, Note, Why } from './wsShared'
import {
  Alert, Drawer, Empty, EvoApp, Hero, Metric, Metrics, Panel, PropertyThumb, Skeleton, Status, Tag,
  useEnvironment,
} from './ds/ds'
import './ds/evo-pages.css'

const PROPERTY_TYPES = ['single_family', 'duplex', 'triplex', 'fourplex',
                        'multi_family', 'condo', 'townhouse', 'mobile', 'land',
                        'commercial', 'other']

function Thumb({ p }) {
  if (p.photo_url) return <span className="evo-thumb"><AuthImage path={p.photo_url} alt={p.address ? `Photo of ${p.address}` : ''} /></span>
  return <PropertyThumb address={p.address} />
}

export default function WholesaleProperties() {
  const navigate = useNavigate()
  const env = useEnvironment()
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [board, setBoard] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [includeTest, setIncludeTest] = useState(true)
  const [showAdd, setShowAdd] = useState(searchParams.get('add') === '1')
  const [showImport, setShowImport] = useState(false)
  const [selected, setSelected] = useState({})
  const [busy, setBusy] = useState(false)

  const stageFilter = searchParams.get('stage') || ''
  const [bandFilter, setBandFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      // The stage filter is applied by the SERVER, so the count above the
      // table always agrees with the rows under it.
      const params = new URLSearchParams({ limit: '50', with_next_action: 'true' })
      if (q) params.set('q', q)
      if (includeTest) params.set('include_test', 'true')
      if (stageFilter) params.set('stage', stageFilter)
      if (bandFilter) params.set('band', bandFilter)
      const [data, counts] = await Promise.all([
        api.get('/wholesale/properties?' + params.toString()),
        api.get('/wholesale/dashboard' + (includeTest ? '?include_test=true' : '')).catch(() => null)])
      setRows(data.properties)
      setTotal(data.total)
      setBoard(counts)
    } catch (e) {
      setError(errText(e))
    } finally {
      setLoading(false)
    }
  }, [q, includeTest, stageFilter, bandFilter])

  useEffect(() => { load() }, [load])

  const chosen = Object.keys(selected).filter((k) => selected[k])

  async function runEnrichment() {
    if (!chosen.length) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const result = await api.post('/wholesale/enrichment/run', { property_ids: chosen })
      const manual = result.results.filter((r) => r.status === 'manual').length
      const found = result.results.filter((r) => r.phones.length || r.emails.length).length
      setNotice(
        `Provider: ${result.provider_label}. ${found} of ${result.results.length} returned contact details.`
        + (manual
          ? ' No skip-trace provider is connected, so nothing was looked up — open a '
            + 'property and enter the phone or email, or import a file that carries it.'
          : ''))
      setSelected({})
      load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  function closeAdd() {
    setShowAdd(false)
    if (searchParams.get('add')) { const n = new URLSearchParams(searchParams); n.delete('add'); setSearchParams(n) }
  }

  return (
    <EvoApp world="operations">
      <Hero scene="aerial" eyebrow="Property Workspace" title="Properties"
            sub="Our pipeline, one property at a time. A property needs only an address, a parcel number or an owner — everything else can arrive later."
            quote="Every property is a potential opportunity."
            meta={[{ label: <><b>{board ? board.properties_imported : total}</b> properties</> }]}
            actions={<>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setShowImport(true)}>Import list</button>
              <button type="button" className="evo-btn evo-btn--primary" onClick={() => setShowAdd(true)}>+ Add property</button>
            </>} />
      <Alert>{error}</Alert>
      <Alert kind="info">{notice}</Alert>

      <Metrics label="Properties summary">
        <Metric label="Properties" value={board ? board.properties_imported : total} tone="primary" />
        <Metric label="Sellers identified" value={board ? board.sellers_identified : null} tone="info" />
        <Metric label="Sellers qualified" value={board ? board.sellers_qualified : null} tone="success" />
        <Metric label="Offers made" value={board ? board.offers_made : null} tone="warning" />
        <Metric label="Closed" value={board ? board.closed_deals : null} tone="success" />
      </Metrics>

      <Panel flush>
        <div className="evo-filterbar">
          <label className="evo-search">
            <span className="evo-sr">Search properties</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
            <input className="evo-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search address, owner or APN" />
          </label>
          <label><span className="evo-sr">Filter by seller qualification</span>
            <select className="evo-select" value={bandFilter} onChange={(e) => setBandFilter(e.target.value)}>
              <option value="">Any seller band</option>
              {['high', 'medium', 'low', 'review', 'excluded'].map((b) => <option key={b} value={b}>{fmtLabel(b)}</option>)}
            </select>
          </label>
          <label className="evo-btn evo-btn--ghost" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={includeTest} onChange={(e) => setIncludeTest(e.target.checked)} style={{ margin: 0 }} />
            Sandbox records
          </label>
          <button type="button" className="evo-btn evo-btn--secondary" disabled={!chosen.length || busy} onClick={runEnrichment}>
            {busy ? 'Working…' : chosen.length ? `Enrich ${chosen.length} selected` : 'Enrich selected'}
          </button>
        </div>
        {stageFilter ? (
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--evo-line)', display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="evo-muted">Showing stage</span><Status status={stageFilter} />
            <button type="button" className="evo-link" onClick={() => navigate('/wholesale/properties')}>Clear</button>
          </div>
        ) : null}

        {loading && !rows.length ? <div style={{ padding: 20 }}><Skeleton rows={5} height={30} /></div> : null}
        {!loading && !rows.length ? (
          <Empty title={stageFilter || q || bandFilter ? 'Nothing matches' : 'No properties yet'}
                 action={!(stageFilter || q || bandFilter) ? (
                   <div className="evo-actionbar" style={{ justifyContent: 'center' }}>
                     <button type="button" className="evo-btn evo-btn--primary" onClick={() => setShowAdd(true)}>+ Add property</button>
                     <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setShowImport(true)}>Import list</button>
                     <Link className="evo-btn evo-btn--ghost" to="/wholesale/evosense">Let EvoSense discover them</Link>
                   </div>) : null}>
            {stageFilter || q || bandFilter ? 'Try a different search or filter.' : 'Add one manually, import a list, or let EvoSense discover them.'}
          </Empty>
        ) : null}

        {rows.length ? (
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <caption className="evo-sr">{total} properties</caption>
              <thead>
                <tr>
                  <th scope="col" style={{ width: 36 }}><span className="evo-sr">Select</span></th>
                  <th scope="col">Property</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Stage</th>
                  <th scope="col" className="is-right">ARV · max offer</th>
                  <th scope="col" className="is-right">Contract · fee</th>
                  <th scope="col">Next action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className="is-link" onClick={() => p.deal && navigate(`/wholesale/deals/${p.deal.id}`)}>
                    <td data-label="Select" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={!!selected[p.id]} style={{ width: 18, height: 18 }}
                             aria-label={`Select ${p.address || 'this property'}`}
                             onChange={(e) => setSelected((s) => ({ ...s, [p.id]: e.target.checked }))} />
                    </td>
                    <td className="is-lead" data-label="">
                      <div className="evo-prop">
                        <Thumb p={p} />
                        <div className="evo-prop__text">
                          {p.deal ? <Link className="evo-prop__addr" to={`/wholesale/deals/${p.deal.id}`} onClick={(e) => e.stopPropagation()}>{p.address || '(no address)'}</Link>
                            : <span className="evo-prop__addr">{p.address || '(no address)'}</span>}
                          <span className="evo-prop__sub">
                            {fmtLabel(p.property_type, '—')}
                            {p.bedrooms || p.bathrooms ? ` · ${fmtNum(p.bedrooms, '?')}bd/${fmtNum(p.bathrooms, '?')}ba` : ''}
                            {p.square_feet ? ` · ${fmtNum(p.square_feet)} sq ft` : ''}
                          </span>
                          {p.is_test && env && !env.local_review ? <Tag kind="sandbox">Sandbox</Tag> : null}
                        </div>
                      </div>
                    </td>
                    <td data-label="Owner">
                      <span className="evo-strong">{p.owner_name || p.seller?.name || <span className="evo-muted">unknown</span>}</span>
                      {p.seller?.qualification_band ? (
                        <span className="evo-prop__sub">{fmtLabel(p.seller.qualification_band)}
                          {p.seller.qualification_score != null ? ` · ${p.seller.qualification_score}` : ''}</span>
                      ) : null}
                      {p.seller?.is_dnc ? <span className="evo-prop__sub" style={{ color: 'var(--evo-danger-ink)' }}>do not contact</span> : null}
                    </td>
                    <td data-label="Stage">{p.deal ? <Status status={p.deal.stage} label={p.deal.stage_label} /> : '—'}</td>
                    <td data-label="ARV · max offer" className="is-right">
                      <span className="evo-money">{fmtMoney(p.deal?.arv)}</span>
                      <span className="evo-prop__sub">max {fmtMoney(p.deal?.max_allowable_offer)}</span>
                    </td>
                    <td data-label="Contract · fee" className="is-right">
                      <span className="evo-money">{fmtMoney(p.deal?.contract_price)}</span>
                      <span className="evo-prop__sub">fee {fmtMoney(p.deal?.assignment_fee)}</span>
                    </td>
                    <td data-label="Next action">
                      {p.next_action ? (
                        <span className="evo-statusnext">
                          <span className="evo-next" style={{ color: p.next_action.tone === 'urgent' ? 'var(--evo-warning-ink)' : undefined }}>{p.next_action.label}</span>
                          <span className="evo-prop__sub" style={{ whiteSpace: 'normal' }}>
                            {p.last_activity_at ? `last activity ${fmtDate(p.last_activity_at)}` : `added ${fmtDate(p.created_at)}`}</span>
                        </span>
                      ) : <span className="evo-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {rows.length ? <div className="evo-pager"><span>{rows.length} of {total} propert{total === 1 ? 'y' : 'ies'}</span></div> : null}
      </Panel>

      <AddProperty open={showAdd} onClose={closeAdd} onDone={() => { closeAdd(); setNotice('Property added.'); load() }} />
      <ImportPanel open={showImport} onClose={() => setShowImport(false)} onDone={(summary) => { setNotice(summary); load() }} />
    </EvoApp>
  )
}


/* INTAKE PRIORITY.
 *
 * A property only has to be identifiable to be worth having. Everything else —
 * beds, baths, size, year, type, ownership, occupancy, county, market — is a
 * detail that enrichment, a CSV column or a later visit can supply, and making
 * somebody stare at fifteen equal boxes before they can record an address is
 * the reason intake gets skipped. Nothing is removed and nothing is required
 * that was not required before: the second group is folded, not deleted, and
 * the enrichment path that fills those same fields is untouched.
 */
const PRIMARY_FIELDS = [
  ['street_address', 'Street address', 'ws-field--wide'],
  ['unit', 'Unit / apt', ''],
  ['city', 'City', ''],
  ['state', 'State', ''],
  ['zip_code', 'ZIP', ''],
  ['parcel_apn', 'Parcel / APN', ''],
  ['owner_name', 'Owner name', ''],
]

const DETAIL_FIELDS = [
  ['county', 'County'], ['market', 'Market'],
  ['bedrooms', 'Beds'], ['bathrooms', 'Baths'],
  ['square_feet', 'Sq ft'], ['year_built', 'Year built'],
]

function AddProperty({ open, onClose, onDone }) {
  const [form, setForm] = useState({
    street_address: '', unit: '', city: '', state: '', zip_code: '',
    county: '', market: '',
    parcel_apn: '', property_type: '', bedrooms: '', bathrooms: '', square_feet: '',
    year_built: '', owner_name: '', ownership_type: '', occupancy_status: '',
    notes: '', is_test: false,
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  function set(key, value) { setForm((f) => ({ ...f, [key]: value })) }

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const payload = { is_test: form.is_test, acquisition_source: 'manual' }
      Object.entries(form).forEach(([k, v]) => {
        if (k === 'is_test' || v === '') return
        payload[k] = ['bedrooms', 'bathrooms', 'square_feet', 'year_built'].includes(k)
          ? Number(v) : v
      })
      await api.post('/wholesale/properties', payload)
      onDone()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title="Add a property"
            footer={<>
              <button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="submit" form="ws-add-property" className="evo-btn evo-btn--primary" disabled={busy}>
                {busy ? 'Saving…' : 'Add property'}</button>
            </>}>
      <Alert>{error}</Alert>
      <form id="ws-add-property" className="ws-form" onSubmit={submit}>
        <Note>
          Give us enough to identify the property. The rest can be completed later.
        </Note>
        <div className="ws-grid ws-grid--intake">
          {PRIMARY_FIELDS.map(([key, label, cls]) => (
            <div className={`ws-field ${cls}`} key={key}>
              <label htmlFor={`add-${key}`}>{label}</label>
              <input id={`add-${key}`} value={form[key]}
                     onChange={(e) => set(key, e.target.value)} />
            </div>
          ))}
        </div>

        <details className="ws-intake-more">
          <summary>
            Property details — beds, baths, size, year, type, ownership, occupancy,
            county, market
          </summary>
          <div className="ws-intake-more__body">
            <Note>
              None of this is needed to save the property. A CSV column, a connected
              data provider or a later visit can fill any of it.
            </Note>
            <div className="ws-grid">
              {DETAIL_FIELDS.map(([key, label]) => (
                <div className="ws-field" key={key}>
                  <label htmlFor={`add-${key}`}>{label}</label>
                  <input id={`add-${key}`} value={form[key]}
                         onChange={(e) => set(key, e.target.value)} />
                </div>
              ))}
              <div className="ws-field">
                <label htmlFor="add-property_type">Property type</label>
                <select id="add-property_type" value={form.property_type}
                        onChange={(e) => set('property_type', e.target.value)}>
                  <option value="">Unknown</option>
                  {PROPERTY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="ws-field">
                <label htmlFor="add-ownership_type">Ownership</label>
                <select id="add-ownership_type" value={form.ownership_type}
                        onChange={(e) => set('ownership_type', e.target.value)}>
                  <option value="">Unknown</option>
                  {['individual', 'llc', 'trust', 'estate', 'corporation'].map(
                    (t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="ws-field">
                <label htmlFor="add-occupancy_status">Occupancy</label>
                <select id="add-occupancy_status" value={form.occupancy_status}
                        onChange={(e) => set('occupancy_status', e.target.value)}>
                  <option value="">Unknown</option>
                  {['owner_occupied', 'tenant', 'vacant'].map(
                    (t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>
          </div>
        </details>

        <div className="ws-field">
          <label htmlFor="add-notes">Notes</label>
          <textarea id="add-notes" rows={2} value={form.notes}
                    onChange={(e) => set('notes', e.target.value)} />
        </div>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.is_test}
                 onChange={(e) => set('is_test', e.target.checked)} />
          Sandbox record — kept out of every dashboard figure and every outreach path
        </label>
      </form>
    </Drawer>
  )
}


function ImportPanel({ open, onClose, onDone }) {
  const [file, setFile] = useState(null)
  const [listName, setListName] = useState('')
  const [isTest, setIsTest] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  async function submit(e) {
    e.preventDefault()
    if (!file) return
    setBusy(true); setError(null); setResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const params = new URLSearchParams()
      if (listName) params.set('list_name', listName)
      if (isTest) params.set('is_test', 'true')
      const data = await api.upload(
        '/wholesale/properties/import' + (params.toString() ? '?' + params : ''), fd)
      setResult(data)
      onDone(`${data.created} propert${data.created === 1 ? 'y' : 'ies'} imported.`)
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title="Import a list" sub="CSV file · checked row by row · nothing is dropped silently"
            footer={<>
              <button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>{result ? 'Done' : 'Cancel'}</button>
              <button type="submit" form="ws-import-list" className="evo-btn evo-btn--primary" disabled={!file || busy}>
                {busy ? 'Importing…' : 'Import'}</button>
            </>}>
      <Note>
        CSV. A row that carries a phone or an email creates the owner as well, so
        the property lands ready for outreach instead of needing a skip trace.
      </Note>
      <Why label="Which columns are recognised, and what happens to the rest">
        <p className="ws-comp__sub">
          Address, city, state, ZIP, county, APN, beds, baths, square feet, year
          built, owner name, phone and email, under the usual column names.
          Columns we do not recognise are kept in the property's notes rather
          than dropped.
        </p>
      </Why>
      <Alert>{error}</Alert>
      <form id="ws-import-list" className="ws-form" onSubmit={submit}>
        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="imp-file">File</label>
            <input id="imp-file" type="file" accept=".csv,text/csv"
                   onChange={(e) => setFile(e.target.files[0])} />
          </div>
          <div className="ws-field">
            <label htmlFor="imp-name">List name</label>
            <input id="imp-name" value={listName}
                   onChange={(e) => setListName(e.target.value)}
                   placeholder="e.g. Dallas absentee owners" />
          </div>
        </div>
        <label className="ws-checkbox">
          <input type="checkbox" checked={isTest}
                 onChange={(e) => setIsTest(e.target.checked)} />
          Import as sandbox records
        </label>
      </form>

      {result ? (
        <div style={{ marginTop: 14 }}>
          <div className="ws-good">{result.created} imported from {result.source}.</div>
          {result.skipped && result.skipped.length ? (
            <div className="ws-warn">
              <strong>{result.skipped.length} row(s) could not be used:</strong>
              <ul style={{ margin: '6px 0 0 18px' }}>
                {result.skipped.slice(0, 20).map((s, i) => (
                  <li key={i}>Row {s.row}: {s.reason}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.unmapped_columns && result.unmapped_columns.length ? (
            <div className="ws-notice">
              Unrecognised columns kept in notes: {result.unmapped_columns.join(', ')}
            </div>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  )
}
