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
import { Empty, ErrorBox, errText, fmtLabel, fmtLabels, fmtMoney, Note, Why } from './wsShared'
import { ConfirmDelete } from './wsFiles'
import ContactLookup from './wsContactLookup'
import BuyerIntelligence from './wsBuyerIntelligence'
import './wsUnderwriting.css'
import { Alert, Drawer, EvoApp, Hero } from './ds/ds'
import './ds/evo-pages.css'

const STRATEGIES = ['flip', 'rental', 'brrrr', 'land', 'other']
const REHAB = ['light', 'moderate', 'heavy', 'full_gut']
const PROPERTY_TYPES = ['single_family', 'duplex', 'triplex', 'fourplex',
                        'multi_family', 'condo', 'townhouse', 'mobile', 'land',
                        'commercial', 'other']
// Whole categories (a box naming one matches every subtype in it) and the
// common investment subtypes. A buyer is never matched to a category they do not buy.
const CATEGORY_TYPES = ['residential', 'multifamily', 'commercial', 'industrial', 'land', 'agricultural',
                        'mixed_use', 'specialty']
const SUBTYPE_TYPES = ['warehouse', 'office', 'retail', 'ranch', 'farm', 'undeveloped_acreage', 'self_storage',
                       'mobile_home_park', 'rv_park', 'hotel']

export default function WholesaleBuyers() {
  // The Buyers screen is Buyer Intelligence (Find / Qualify / Match). The
  // drawers below are the existing buyer tools it opens - add, edit, buy boxes,
  // county finder, paid phone/email lookup (approved first), delete - unchanged.
  const [buyers, setBuyers] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [showFind, setShowFind] = useState(false)
  const [lookupIds, setLookupIds] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [editing, setEditing] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [busy, setBusy] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const data = await api.get('/wholesale/buyers?limit=500&active_only=false&with_activity=true&include_test=true')
      setBuyers(data.buyers)
    } catch (e) {
      setError(errText(e))
    }
  }, [])
  useEffect(() => { load() }, [load])
  const changed = useCallback((msg) => { if (msg) setNotice(msg); setReloadKey((k) => k + 1); load() }, [load])
  const unlinked = buyers.filter((b) => !b.org_contact_id && !b.is_test && (b.email || b.phone)).length

  async function linkContacts() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post('/wholesale/buyers/link-contacts?dry_run=false', {})
      const c = r.counts || {}
      const linked = (c.linked_new || 0) + (c.linked_existing || 0) + (c.linked_possible || 0)
      changed(`${linked} buyer(s) linked to your contacts as partners (not leads)` +
              (c.linked_existing ? ` - ${c.linked_existing} matched an existing contact` : '') +
              (c.error ? ` - ${c.error} could not be linked and stay as they were` : '') + '.')
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }
  const byId = (id) => buyers.find((b) => b.id === id)
  const legacy = {
    openAdd: () => setShowAdd(true), openFinder: () => setShowFind(true), unlinked, linkContacts,
    edit: (id) => (byId(id) ? setEditing(id) : load().then(() => setEditing(id))),
    boxes: (id) => (byId(id) ? setExpanded(id) : load().then(() => setExpanded(id))),
    lookup: (ids) => setLookupIds(ids),
    remove: (id, name) => setDeleting({ id, display_name: name }),
  }

  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Buyer Intelligence" title="Buyers"
            sub="Find investors, qualify them, and match them to real deals - single-family, multifamily, land and commercial, in one buyer network."
            quote="The right buyer turns opportunity into profit." />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      <BuyerIntelligence legacy={legacy} reloadKey={reloadKey} />

      {deleting ? (
        <ConfirmDelete busy={busy}
                       what={`${deleting.display_name} and every buy box on them`}
                       onCancel={() => setDeleting(null)}
                       onConfirm={async () => {
                         setBusy(true); setError(null)
                         try {
                           await api.delete(`/wholesale/buyers/${deleting.id}`)
                           setDeleting(null)
                           changed(`${deleting.display_name} deleted.`)
                         } catch (e) {
                           setError(errText(e))
                         } finally { setBusy(false) }
                       }} />
      ) : null}

      {showAdd ? <AddBuyer onClose={() => setShowAdd(false)} onDone={() => { setShowAdd(false); changed('Buyer added. Give them a buy box so they appear in matches.') }} /> : null}
      {showFind ? <FindBuyers onClose={() => setShowFind(false)} onDone={(msg) => changed(msg)} /> : null}
      {lookupIds ? <ContactLookup kind="buyer" what="buyer" ids={lookupIds} onClose={() => setLookupIds(null)}
                                  onDone={(msg) => { setLookupIds(null); changed(msg) }} /> : null}
      {editing && byId(editing) ? <EditBuyer buyer={byId(editing)} onDone={() => { setEditing(null); changed() }} /> : null}
      {expanded && byId(expanded) ? <BuyBoxes buyer={byId(expanded)} onChanged={() => changed()} onClose={() => setExpanded(null)} /> : null}
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
    rehab_tolerance: '', min_spread: '', min_acres: '', max_acres: '', min_units: '', max_units: '',
    min_building_sqft: '', max_building_sqft: '', min_cap_rate: '', distress_tolerance: '', criteria: '', value_add: false,
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
                         'max_sqft', 'min_year_built', 'min_spread', 'min_acres', 'max_acres', 'min_units',
                         'max_units', 'min_building_sqft', 'max_building_sqft', 'min_cap_rate']) {
        if (form[key] !== '') payload[key] = Number(form[key])
      }
      if (form.rehab_tolerance) payload.rehab_tolerance = form.rehab_tolerance
      if (form.distress_tolerance) payload.distress_tolerance = form.distress_tolerance
      if (form.criteria.trim()) payload.criteria = form.criteria.trim()
      if (form.value_add) payload.value_add = true
      await api.post(`/wholesale/buyers/${buyer.id}/buy-boxes`, payload)
      onChanged()
      setForm({ label: '', states: '', counties: '', cities: '', zips: '',
                markets: '', property_types: [], strategies: [], min_price: '',
                max_price: '', min_beds: '', min_sqft: '', max_sqft: '',
                min_year_built: '', rehab_tolerance: '', min_spread: '', min_acres: '', max_acres: '',
                min_units: '', max_units: '', min_building_sqft: '', max_building_sqft: '', min_cap_rate: '',
                distress_tolerance: '', criteria: '', value_add: false })
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
          ['min_year_built', 'Built after'], ['min_spread', 'Min spread'],
          ['min_acres', 'Min acres'], ['max_acres', 'Max acres'], ['min_units', 'Min units'], ['max_units', 'Max units'],
          ['min_building_sqft', 'Min building sq ft'], ['max_building_sqft', 'Max building sq ft'],
          ['min_cap_rate', 'Desired cap rate (%)'], ['distress_tolerance', 'Distress tolerance'],
          ['criteria', 'Other criteria']].map(
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
          {[...CATEGORY_TYPES, ...SUBTYPE_TYPES, ...PROPERTY_TYPES].map((t) => (
            <label className="ws-checkbox" key={t}>
              <input type="checkbox" checked={form.property_types.includes(t)}
                     onChange={() => toggle('property_types', t)} />
              {fmtLabel(t)}
            </label>
          ))}
        </div>
      </div>

      <label className="ws-checkbox" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={form.value_add} onChange={(e) => set('value_add', e.target.checked)} />
        Interested in value-add properties
      </label>

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


function FindBuyers({ onClose, onDone }) {
  const [county, setCounty] = useState('dallas')
  const [data, setData] = useState(null)
  const [recentOnly, setRecentOnly] = useState(true)
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const p = new URLSearchParams({ county, recent_only: String(recentOnly) })
      if (q.trim()) p.set('q', q.trim())
      setData(await api.get('/wholesale/buyers/finder?' + p.toString()))
    } catch (e) { setError(errText(e)) }
  }, [county, recentOnly, q])

  useEffect(() => { load() }, [load])
  const running = data && data.status && data.status.state === 'running'
  useEffect(() => {
    if (!running) return undefined
    const t = setInterval(load, 8000)
    return () => clearInterval(t)
  }, [running, load])

  async function scan() {
    setBusy(true); setError(null)
    try { await api.post('/wholesale/buyers/finder/run', { county }); await load() }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  async function add() {
    const keys = Object.keys(picked).filter((k) => picked[k])
    if (!keys.length) return
    setBusy(true); setError(null)
    try {
      const r = await api.post('/wholesale/buyers/finder/import', { county, keys })
      setPicked({})
      onDone(`${r.created} investor(s) added from ${county === 'dallas' ? 'Dallas' : 'Tarrant'} County records`
             + (r.attached ? ` · ${r.attached} already in your list (their recorded deeds were attached)` : '')
             + (r.needs_review ? ` · ${r.needs_review} look related to a buyer you have - review them under Find buyers` : '')
             + (r.deeds_recorded ? ` · ${r.deeds_recorded} recorded deed(s) saved` : '')
             + '. Look up a phone for each before sending deals.')
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const rows = (data && data.buyers) || []
  const nPicked = Object.values(picked).filter(Boolean).length
  const st = (data && data.status) || {}

  return (
    <Drawer open onClose={onClose} title="Find cash buyers" sub="Local investors from the county property records · free">
      <Note>{data ? data.note : 'Owners in the county file who hold several houses and keep buying.'}</Note>
      <ErrorBox error={error} />
      <div className="ws-actions" style={{ flexWrap: 'wrap', gap: 8, margin: '8px 0' }}>
        <select className="ws-input ws-input--inline" value={county}
                onChange={(e) => { setCounty(e.target.value); setPicked({}) }}>
          <option value="dallas">Dallas County</option>
          <option value="tarrant">Tarrant County</option>
        </select>
        <button className="btn btn--secondary btn--sm" disabled={busy || running} onClick={scan}>
          {running ? 'Scanning the county file…' : data && data.generated_at ? 'Scan again' : 'Scan the county file'}</button>
        <label className="ws-checkbox"><input type="checkbox" checked={recentOnly}
               onChange={(e) => setRecentOnly(e.target.checked)} /> Bought in the last 2 years</label>
        <input className="ws-input ws-input--inline" placeholder="Name or ZIP" value={q}
               onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 160 }} />
      </div>
      <div className="ws-hint">
        {running ? 'Reading every parcel in the county - this takes a few minutes. You can close this and come back.'
          : st.state === 'failed' ? `The last scan failed: ${st.error}`
          : data && data.generated_at ? `Last scan ${new Date(data.generated_at).toLocaleString()} · ${data.total} investors match`
          : 'No scan yet for this county. Click "Scan the county file".'}
      </div>
      {rows.length ? (
        <>
          <div className="ws-actions" style={{ margin: '8px 0' }}>
            <button className="btn btn--primary btn--sm" disabled={busy || !nPicked} onClick={add}>
              Add {nPicked || ''} to my buyers</button>
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => setPicked(Object.fromEntries(rows.filter((b) => !b.already_added).slice(0, 50).map((b) => [b.key, true])))}>
              Pick top 50</button>
          </div>
          <div className="evo-table-wrap" style={{ maxHeight: 520, overflow: 'auto' }}>
            <table className="evo-table">
              <thead><tr><th /><th>Owner</th><th className="is-num">Holds</th><th className="is-num">Bought (2 yrs)</th><th>Where</th><th>Mailing</th></tr></thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={b.key}>
                    <td><input type="checkbox" disabled={b.already_added} checked={!!picked[b.key]}
                               aria-label={`Pick ${b.name}`}
                               onChange={(e) => setPicked((p) => ({ ...p, [b.key]: e.target.checked }))} /></td>
                    <td><span className="evo-strong">{b.name}</span>
                      <span className="evo-prop__sub">{b.kind === 'company' ? 'Company' : 'Person'}{b.already_added ? ' · already in your buyers' : ''}{b.out_of_state ? ' · out of state' : ''}</span></td>
                    <td className="is-num" title={`${b.houses} houses, ${b.multifamily} multifamily, ${b.lots} lots`}>{b.properties}</td>
                    <td className="is-num">{b.bought_recently || '—'}</td>
                    <td className="evo-small">{(b.zips && b.zips.length ? b.zips.slice(0, 4).join(', ') : (b.samples || [])[0]) || '—'}</td>
                    <td className="evo-small">{[b.mailing.city, b.mailing.state].filter(Boolean).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
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
