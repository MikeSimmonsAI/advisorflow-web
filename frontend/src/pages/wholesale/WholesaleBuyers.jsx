/* Cash buyer CRM — and the structured buy boxes that make matching possible.
 *
 * A buy box on this screen is FIELDS, not a paragraph. That is the whole reason
 * the matching engine can give a number and a list of reasons instead of a
 * guess: a note cannot be matched against, and a buyer list stored as notes is a
 * contact list, not a disposition tool.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import {
  Empty, ErrorBox, errText, fmtDate, fmtLabel, fmtLabels, fmtMoney, fmtNum,
  fmtWhen, Note, Why,
} from './wsShared'
import { ConfirmDelete } from './wsFiles'
import { Alert, Drawer, Empty as EvoEmpty, EvoApp, Hero, Metric, Metrics, Panel, Skeleton, Tag } from './ds/ds'
import './ds/evo-pages.css'

const STRATEGIES = ['flip', 'rental', 'brrrr', 'land', 'other']
const REHAB = ['light', 'moderate', 'heavy', 'full_gut']
const PROPERTY_TYPES = ['single_family', 'duplex', 'triplex', 'fourplex',
                        'multi_family', 'condo', 'townhouse', 'mobile', 'land',
                        'commercial', 'other']

export default function WholesaleBuyers() {
  const [buyers, setBuyers] = useState([])
  const [verifiedOnly, setVerifiedOnly] = useState(false)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [includeTest, setIncludeTest] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const [editing, setEditing] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams({ limit: '500', active_only: 'false', with_activity: 'true' })
      if (q) params.set('q', q)
      if (includeTest) params.set('include_test', 'true')
      const data = await api.get('/wholesale/buyers?' + params.toString())
      setBuyers(data.buyers)
      setTotal(data.total)
    } catch (e) {
      setError(errText(e))
    } finally {
      setLoading(false)
    }
  }, [q, includeTest])

  useEffect(() => { load() }, [load])

  const active = buyers.filter((b) => b.is_active && !b.do_not_contact).length
  const verified = buyers.filter((b) => b.cash_verified).length
  const pof = buyers.filter((b) => b.proof_of_funds_on_file).length
  const boxes = buyers.reduce((n, b) => n + (b.buy_boxes || []).length, 0)

  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Buyer Network" title="Cash Buyers"
            sub="Ready capital. Real relationships. Every buyer with a structured buy box is matched to your deals automatically — with a score and its reasons."
            quote="The right buyer turns opportunity into profit."
            actions={<>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setShowImport(true)}>Import buyers</button>
              <button type="button" className="evo-btn evo-btn--primary" onClick={() => setShowAdd(true)}>+ Add buyer</button>
            </>} />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <Metrics label="Buyer summary">
        <Metric label="Total buyers" value={loading && !buyers.length ? null : total} tone="primary" />
        <Metric label="Active" value={loading && !buyers.length ? null : active} tone="success" sub="not opted out" />
        <Metric label="Verified cash" value={loading && !buyers.length ? null : verified} tone="success" />
        <Metric label="POF on file" value={loading && !buyers.length ? null : pof} tone="info" />
        <Metric label="Buy boxes" value={loading && !buyers.length ? null : boxes} tone="violet"
                sub={buyers.some((b) => !(b.buy_boxes || []).length) ? 'some buyers have none' : null} />
      </Metrics>

      <Panel flush>
        <div className="evo-filterbar">
          <label className="evo-search">
            <span className="evo-sr">Search buyers</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
            <input className="evo-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or email" />
          </label>
          <label className="evo-btn evo-btn--ghost" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} style={{ margin: 0 }} />
            Verified only
          </label>
          <label className="evo-btn evo-btn--ghost" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={includeTest} onChange={(e) => setIncludeTest(e.target.checked)} style={{ margin: 0 }} />
            Sandbox records
          </label>
        </div>

        {loading && !buyers.length ? <div style={{ padding: 20 }}><Skeleton rows={4} height={30} /></div> : null}
        {!loading && !buyers.length ? (
          <EvoEmpty title={q ? 'No buyer matches' : 'No cash buyers yet'}
                 action={!q ? <div className="evo-actionbar" style={{ justifyContent: 'center' }}>
                   <button type="button" className="evo-btn evo-btn--primary" onClick={() => setShowAdd(true)}>+ Add buyer</button>
                   <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setShowImport(true)}>Import buyers</button></div> : null}>
            {q ? 'Try a different name or email.' : 'Add your buyers and their buy boxes, and every deal is matched to them automatically.'}
          </EvoEmpty>
        ) : null}

        {buyers.length ? (
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <caption className="evo-sr">{total} cash buyers</caption>
              <thead>
                <tr>
                  <th scope="col">Buyer</th><th scope="col">Buys in</th><th scope="col">Funds</th>
                  <th scope="col">Track record</th><th scope="col" className="is-num">Close</th>
                  <th scope="col" className="is-num">Rating</th><th scope="col"><span className="evo-sr">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {buyers.filter((b) => !verifiedOnly || b.cash_verified || b.proof_of_funds_on_file).map((b) => (
                  <tr key={b.id}>
                    <td className="is-lead" data-label="">
                      <span className="evo-strong" style={{ fontSize: 14 }}>{b.display_name}</span>
                      <span className="evo-prop__sub">{b.email || 'no email'} · {b.phone || 'no phone'}</span>
                      {(b.do_not_contact || !b.is_active) ? (
                        <span className="evo-chips" style={{ marginTop: 5 }}>
                          {b.do_not_contact ? <Tag kind="danger">Opted out</Tag> : null}
                          {!b.is_active ? <Tag>Inactive</Tag> : null}
                        </span>
                      ) : null}
                    </td>
                    <td data-label="Buys in"><Geography boxes={b.buy_boxes} /></td>
                    <td data-label="Funds">
                      <span className="evo-buyer-flags">
                        {b.cash_verified ? <Tag kind="live">Cash verified</Tag> : null}
                        {b.proof_of_funds_on_file ? <Tag kind="live">POF on file</Tag> : null}
                        {!b.cash_verified && !b.proof_of_funds_on_file ? <span className="evo-muted evo-small">not verified</span> : null}
                      </span>
                      {b.proof_of_funds_expires ? <span className="evo-prop__sub">POF expires {fmtDate(b.proof_of_funds_expires)}</span> : null}
                    </td>
                    <td data-label="Track record"><TrackRecord buyer={b} /></td>
                    <td data-label="Close" className="is-num">{b.typical_close_days ? `${fmtNum(b.typical_close_days)}d` : '—'}</td>
                    <td data-label="Rating" className="is-num">{b.reliability_rating ? `${b.reliability_rating}/5` : '—'}</td>
                    <td data-label="" className="is-right">
                      <span className="evo-actionbar" style={{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
                        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => setExpanded(b.id)}>
                          Buy boxes ({b.buy_boxes.length})</button>
                        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setEditing(b.id)}>Edit</button>
                        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" aria-label={`Delete ${b.display_name}`}
                                onClick={() => setDeleting(b)}>Delete</button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>

      {deleting ? (
        <ConfirmDelete busy={busy}
                       what={`${deleting.display_name} and every buy box on them`}
                       onCancel={() => setDeleting(null)}
                       onConfirm={async () => {
                         setBusy(true); setError(null)
                         try {
                           await api.delete(`/wholesale/buyers/${deleting.id}`)
                           setNotice(`${deleting.display_name} deleted.`)
                           setDeleting(null)
                           await load()
                         } catch (e) {
                           setError(errText(e))
                         } finally { setBusy(false) }
                       }} />
      ) : null}

      {showAdd ? <AddBuyer onClose={() => setShowAdd(false)} onDone={() => { setShowAdd(false); setNotice('Buyer added. Give them a buy box so they appear in matches.'); load() }} /> : null}
      {showImport ? <BuyerImport onClose={() => setShowImport(false)} onDone={(msg) => { setNotice(msg); load() }} /> : null}
      {editing ? <EditBuyer buyer={buyers.find((b) => b.id === editing)} onDone={() => { setEditing(null); load() }} /> : null}
      {expanded ? <BuyBoxes buyer={buyers.find((b) => b.id === expanded)} onChanged={load} onClose={() => setExpanded(null)} /> : null}
    </EvoApp>
  )
}


function AddBuyer({ onDone, onClose }) {
  const [form, setForm] = useState({
    company_name: '', contact_name: '', email: '', phone: '',
    preferred_channel: 'email', typical_close_days: '', reliability_rating: '',
    source: '', notes: '', proof_of_funds_on_file: false, cash_verified: false,
    is_test: false,
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  function set(key, value) { setForm((f) => ({ ...f, [key]: value })) }

  return (
    <Drawer open onClose={onClose} title="Add a cash buyer" sub="Add them, then give them a buy box so they appear in matches.">
      <ErrorBox error={error} />
      <div className="ws-grid">
        {[['company_name', 'Company'], ['contact_name', 'Contact name'],
          ['email', 'Email'], ['phone', 'Phone'],
          ['typical_close_days', 'Typical days to close'],
          ['reliability_rating', 'Reliability 1-5'], ['source', 'Source']].map(
          ([key, label]) => (
            <div className="ws-field" key={key}>
              <label>{label}</label>
              <input value={form[key]} onChange={(e) => set(key, e.target.value)} />
            </div>
          ))}
        <div className="ws-field">
          <label>Preferred channel</label>
          <select value={form.preferred_channel}
                  onChange={(e) => set('preferred_channel', e.target.value)}>
            {['email', 'sms', 'phone'].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.cash_verified}
                 onChange={(e) => set('cash_verified', e.target.checked)} />
          Cash verified
        </label>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.proof_of_funds_on_file}
                 onChange={(e) => set('proof_of_funds_on_file', e.target.checked)} />
          Proof of funds on file
        </label>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.is_test}
                 onChange={(e) => set('is_test', e.target.checked)} />
          Sandbox
        </label>
      </div>
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy}
                onClick={async () => {
                  setBusy(true); setError(null)
                  try {
                    const payload = {}
                    Object.entries(form).forEach(([k, v]) => {
                      if (v === '' ) return
                      payload[k] = ['typical_close_days', 'reliability_rating']
                        .includes(k) ? Number(v) : v
                    })
                    await api.post('/wholesale/buyers', payload)
                    onDone()
                  } catch (e) { setError(errText(e)) } finally { setBusy(false) }
                }}>
          {busy ? 'Saving…' : 'Add buyer'}
        </button>
      </div>
    </Drawer>
  )
}


function BuyBoxes({ buyer, onChanged, onClose }) {
  const [form, setForm] = useState({
    label: '', states: '', counties: '', cities: '', zips: '', markets: '',
    property_types: [], strategies: [], min_price: '', max_price: '',
    min_beds: '', min_sqft: '', max_sqft: '', min_year_built: '',
    rehab_tolerance: '', min_spread: '',
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (!buyer) return null

  function set(key, value) { setForm((f) => ({ ...f, [key]: value })) }

  function toggle(key, value) {
    setForm((f) => ({
      ...f,
      [key]: f[key].includes(value) ? f[key].filter((v) => v !== value)
                                    : [...f[key], value],
    }))
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const payload = { label: form.label || null }
      for (const key of ['states', 'counties', 'cities', 'zips', 'markets']) {
        if (form[key].trim()) {
          payload[key] = form[key].split(',').map((s) => s.trim()).filter(Boolean)
        }
      }
      if (form.property_types.length) payload.property_types = form.property_types
      if (form.strategies.length) payload.strategies = form.strategies
      for (const key of ['min_price', 'max_price', 'min_beds', 'min_sqft',
                         'max_sqft', 'min_year_built', 'min_spread']) {
        if (form[key] !== '') payload[key] = Number(form[key])
      }
      if (form.rehab_tolerance) payload.rehab_tolerance = form.rehab_tolerance
      await api.post(`/wholesale/buyers/${buyer.id}/buy-boxes`, payload)
      onChanged()
      setForm({ label: '', states: '', counties: '', cities: '', zips: '',
                markets: '', property_types: [], strategies: [], min_price: '',
                max_price: '', min_beds: '', min_sqft: '', max_sqft: '',
                min_year_built: '', rehab_tolerance: '', min_spread: '' })
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Drawer open onClose={onClose} wide title={`Buy boxes — ${buyer.display_name}`} sub="Structured buy boxes are what make ranked matching possible.">
      <Note>Leave a field blank to mean "no restriction on this".</Note>
      <Why label="What a blank field does to matching">
        <p className="ws-comp__sub">
          An empty geography is not "matches nothing" — it is "they never said",
          and the matching engine treats it that way rather than quietly
          excluding the buyer.
        </p>
      </Why>
      <ErrorBox error={error} />

      {buyer.buy_boxes.length ? (
        <div className="ws-scroll">
        <table className="ws-table">
          <thead>
            <tr><th>Label</th><th>Where</th><th className="ws-num">Price</th>
              <th>Types / strategy</th><th>Rehab</th><th /></tr>
          </thead>
          <tbody>
            {buyer.buy_boxes.map((b) => (
              <tr key={b.id}>
                <td>{b.label || '—'}</td>
                <td>
                  {[b.states, b.counties, b.cities, b.zips, b.markets]
                    .filter((x) => x && x.length)
                    .map((x) => x.join(', ')).join(' · ')
                    || <span className="ws-muted">anywhere</span>}
                </td>
                <td className="ws-num">
                  {b.min_price || b.max_price
                    ? `${fmtMoney(b.min_price, { blank: 'any' })} – ${fmtMoney(b.max_price, { blank: 'any' })}`
                    : <span className="ws-muted">any</span>}
                </td>
                <td>
                  {fmtLabels(b.property_types)}
                  {b.strategies && b.strategies.length
                    ? ` / ${fmtLabels(b.strategies)}` : ''}
                </td>
                <td>{fmtLabel(b.rehab_tolerance)}</td>
                <td>
                  <button className="btn btn--secondary btn--sm" disabled={busy}
                          onClick={async () => {
                            setBusy(true)
                            try {
                              await api.delete(`/wholesale/buy-boxes/${b.id}`)
                              onChanged()
                            } catch (e) { setError(errText(e)) } finally { setBusy(false) }
                          }}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : <Empty>No buy box yet — this buyer will not appear in ranked matches.</Empty>}

      <div className="ws-grid" style={{ marginTop: 16 }}>
        {[['label', 'Label'], ['states', 'States (comma separated)'],
          ['counties', 'Counties'], ['cities', 'Cities'], ['zips', 'ZIPs'],
          ['markets', 'Markets'], ['min_price', 'Min price'], ['max_price', 'Max price'],
          ['min_beds', 'Min beds'], ['min_sqft', 'Min sq ft'], ['max_sqft', 'Max sq ft'],
          ['min_year_built', 'Built after'], ['min_spread', 'Min spread']].map(
          ([key, label]) => (
            <div className="ws-field" key={key}>
              <label>{label}</label>
              <input value={form[key]} onChange={(e) => set(key, e.target.value)} />
            </div>
          ))}
        <div className="ws-field">
          <label>Rehab tolerance</label>
          <select value={form.rehab_tolerance}
                  onChange={(e) => set('rehab_tolerance', e.target.value)}>
            <option value="">No limit stated</option>
            {REHAB.map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
          </select>
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="ws-k">Property types</div>
        <div className="ws-actions" style={{ marginTop: 6 }}>
          {PROPERTY_TYPES.map((t) => (
            <label className="ws-checkbox" key={t}>
              <input type="checkbox" checked={form.property_types.includes(t)}
                     onChange={() => toggle('property_types', t)} />
              {fmtLabel(t)}
            </label>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="ws-k">Strategy</div>
        <div className="ws-actions" style={{ marginTop: 6 }}>
          {STRATEGIES.map((s) => (
            <label className="ws-checkbox" key={s}>
              <input type="checkbox" checked={form.strategies.includes(s)}
                     onChange={() => toggle('strategies', s)} />
              {fmtLabel(s)}
            </label>
          ))}
        </div>
      </div>

      <div className="ws-actions" style={{ marginTop: 14 }}>
        <button className="btn btn--primary" disabled={busy} onClick={save}>
          {busy ? 'Saving…' : 'Add buy box'}
        </button>
      </div>
    </Drawer>
  )
}


function BuyerImport({ onDone, onClose }) {
  const [file, setFile] = useState(null)
  const [isTest, setIsTest] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  return (
    <Drawer open onClose={onClose} title="Import buyers" sub="CSV file · buyers and their buy boxes">
      <Note>
        CSV. A row that carries buy-box columns builds the buy box at the same
        time, so the list arrives matchable rather than as a pile of contacts.
      </Note>
      <Why label="Which columns are recognised">
        <p className="ws-comp__sub">
          Company, contact, email, phone, states, counties, cities, ZIPs,
          property types, strategy, min and max price, min and max square feet,
          and rehab.
        </p>
      </Why>
      <ErrorBox error={error} />
      <div className="ws-grid">
        <div className="ws-field">
          <label>File</label>
          <input type="file" accept=".csv,text/csv"
                 onChange={(e) => setFile(e.target.files[0])} />
        </div>
      </div>
      <label className="ws-checkbox" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={isTest}
               onChange={(e) => setIsTest(e.target.checked)} />
        Import as sandbox records
      </label>
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={!file || busy}
                onClick={async () => {
                  setBusy(true); setError(null); setResult(null)
                  try {
                    const fd = new FormData()
                    fd.append('file', file)
                    const data = await api.upload(
                      '/wholesale/buyers/import' + (isTest ? '?is_test=true' : ''), fd)
                    setResult(data)
                    onDone(`${data.created} buyer(s) imported, `
                           + `${data.buy_boxes_created} buy box(es) created.`)
                  } catch (e) { setError(errText(e)) } finally { setBusy(false) }
                }}>
          {busy ? 'Importing…' : 'Import'}
        </button>
      </div>
      {result && result.skipped && result.skipped.length ? (
        <div className="ws-warn" style={{ marginTop: 12 }}>
          <strong>{result.skipped.length} row(s) could not be used:</strong>
          <ul style={{ margin: '6px 0 0 18px' }}>
            {result.skipped.slice(0, 20).map((s, i) => (
              <li key={i}>Row {s.row}: {s.reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Drawer>
  )
}


/* Where a buyer actually buys, read off their buy boxes rather than asked for
 * a second time. A buyer with no box says so — that is the single most useful
 * fact on this screen, because a buyer with no box can never be matched. */
/* A buy box stores geography normalized and lower-case, because that is what
 * the matcher compares. Printing it back raw ("dfw, dallas, tarrant, tx") is
 * the screen showing a person a database value. This capitalizes WITHOUT
 * translating: nothing is looked up or mapped, so a place this code has never
 * seen still reads correctly. */
function place(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  // State codes and market acronyms are short and have no space in them.
  if (text.length <= 4 && !text.includes(' ')) return text.toUpperCase()
  return text.replace(/\b[a-z]/g, (c) => c.toUpperCase())
}

function Geography({ boxes }) {
  if (!boxes || !boxes.length) {
    return (
      <span className="ws-blocked ws-comp__sub">
        No buy box — cannot be matched
      </span>
    )
  }
  const parts = []
  const seen = new Set()
  for (const box of boxes) {
    for (const key of ['markets', 'counties', 'cities', 'states']) {
      for (const value of (box[key] || [])) {
        const label = place(value)
        if (label && !seen.has(label)) { seen.add(label); parts.push(label) }
      }
    }
  }
  const prices = boxes
    .map((b) => b.max_price)
    .filter((v) => v !== null && v !== undefined)
  return (
    <>
      <div>{parts.length ? parts.slice(0, 4).join(', ') : 'anywhere'}
        {parts.length > 4 ? ` +${parts.length - 4}` : ''}</div>
      <div className="ws-comp__sub">
        {prices.length ? `up to ${fmtMoney(Math.max(...prices))}` : 'no price ceiling set'}
        {boxes.length > 1 ? ` · ${boxes.length} boxes` : ''}
      </div>
    </>
  )
}


/* What this buyer has actually done HERE. Counts of rows that exist — not a
 * score, not a ranking, and never mixed up with the `past_deals_count` somebody
 * typed in when they added the buyer, which is shown separately and labelled as
 * what it is. */
function TrackRecord({ buyer }) {
  const a = buyer.activity
  if (!a || !a.sheets_sent) {
    return (
      <>
        <span className="ws-muted">Nothing sent yet</span>
        {buyer.past_deals_count ? (
          <div className="ws-comp__sub">
            {buyer.past_deals_count} past deal(s) — stated, not measured
          </div>
        ) : null}
      </>
    )
  }
  return (
    <>
      <div>
        {a.sheets_sent} sent · {a.responded} replied
        {a.offers_made ? ` · ${a.offers_made} offered` : ''}
      </div>
      <div className="ws-comp__sub">
        {a.selected_count
          ? `chosen on ${a.selected_count} deal${a.selected_count === 1 ? '' : 's'}`
          : 'never chosen'}
        {a.best_offer !== null && a.best_offer !== undefined
          ? ` · best ${fmtMoney(a.best_offer)}` : ''}
      </div>
      {a.last_contacted_at ? (
        <div className="ws-comp__sub">last sent {fmtDate(a.last_contacted_at)}</div>
      ) : null}
    </>
  )
}


/* Editing a buyer. Same fields as adding one, so a field cannot exist in one
 * form and be missing from the other, with an explicit Save and Cancel and a
 * Save that stays disabled until something changed. */
const EDIT_FIELDS = [
  ['company_name', 'Company'],
  ['contact_name', 'Contact'],
  ['email', 'Email'],
  ['phone', 'Phone'],
  ['typical_close_days', 'Typical close (days)'],
  ['reliability_rating', 'Rating out of 5'],
  ['past_deals_count', 'Past deals (stated)'],
  ['source', 'Where they came from'],
]

function EditBuyer({ buyer, onDone }) {
  const initial = () => EDIT_FIELDS.reduce(
    (acc, [k]) => ({ ...acc, [k]: buyer[k] ?? '' }),
    { notes: buyer.notes ?? '', do_not_contact: !!buyer.do_not_contact,
      is_active: buyer.is_active !== false,
      proof_of_funds_on_file: !!buyer.proof_of_funds_on_file })
  const [form, setForm] = useState(initial)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const clean = JSON.stringify(form) === JSON.stringify(initial())

  if (!buyer) return null

  return (
    <Drawer open onClose={onDone} title={`Edit ${buyer.display_name}`} sub="Changes apply the moment you save.">
      <ErrorBox error={error} />
      <div className="ws-grid">
        {EDIT_FIELDS.map(([key, label]) => (
          <div className="ws-field" key={key}>
            <label htmlFor={`eb-${key}`}>{label}</label>
            <input id={`eb-${key}`} className="ws-input" value={form[key] ?? ''}
                   onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
          </div>
        ))}
      </div>
      <div className="ws-field" style={{ marginTop: 10 }}>
        <label htmlFor="eb-notes">Notes</label>
        <textarea id="eb-notes" className="ws-input" rows={2} value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} />
      </div>
      <div className="ws-actions" style={{ marginTop: 10 }}>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.is_active}
                 onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))} />
          Active
        </label>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.do_not_contact}
                 onChange={(e) => setForm(
                   (f) => ({ ...f, do_not_contact: e.target.checked }))} />
          Opted out of contact
        </label>
        <label className="ws-checkbox">
          <input type="checkbox" checked={form.proof_of_funds_on_file}
                 onChange={(e) => setForm(
                   (f) => ({ ...f, proof_of_funds_on_file: e.target.checked }))} />
          Proof of funds on file
        </label>
      </div>
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy || clean}
                onClick={async () => {
                  setBusy(true); setError(null)
                  try {
                    const body = {}
                    Object.entries(form).forEach(([k, v]) => {
                      if (typeof v === 'boolean') { body[k] = v; return }
                      const text = String(v ?? '').trim()
                      if (text === '') { body[k] = null; return }
                      body[k] = ['typical_close_days', 'reliability_rating',
                                 'past_deals_count'].includes(k)
                        ? Number(text) : text
                    })
                    await api.patch(`/wholesale/buyers/${buyer.id}`, body)
                    onDone()
                  } catch (e) {
                    setError(errText(e))
                  } finally { setBusy(false) }
                }}>
          Save buyer
        </button>
        <button className="btn btn--secondary" disabled={busy} onClick={onDone}>
          Cancel
        </button>
      </div>
    </Drawer>
  )
}
