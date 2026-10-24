/* DEAL ANALYSIS - "Analyze deal" on a Wholesale deal (Deal Intelligence Engine).
 *
 * Server: GET/POST /wholesale/deals/{id}/underwriting,
 *         POST /wholesale/deals/{id}/repairs/itemized,
 *         POST /wholesale/deals/{id}/comps/import.
 *
 * Every number comes from the server. A figure the evidence does not support
 * shows as "not yet" with the reason under "What's missing" - never a guess.
 * Starting assumptions are labelled as such and can be edited per deal.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney } from './wsShared'

const STEP_ICON = { done: '✓', attention: '!', todo: '○' }
const REPAIR_CATEGORIES = ['Roof', 'HVAC', 'Foundation', 'Plumbing', 'Electrical', 'Kitchen', 'Bathrooms',
  'Flooring', 'Paint (inside & out)', 'Windows & doors', 'Yard & clean-out', 'Other']
const STRATS = [['wholesale', 'Wholesale'], ['flip', 'Fix & flip'], ['rental', 'Buy & hold']]
const CASES = [['worst', 'Low case'], ['base', 'Expected'], ['best', 'High case']]

const pct = (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%`)
const range = (lo, hi) => (lo == null && hi == null ? 'not yet'
  : lo === hi || hi == null ? fmtMoney(lo) : `${fmtMoney(lo)} – ${fmtMoney(hi)}`)

/* The six steps of a deal, on top of the deal page. */
export function WorkflowStrip({ dealId, goTo, refreshKey }) {
  const [steps, setSteps] = useState(null)
  useEffect(() => {
    let live = true
    api.get(`/wholesale/deals/${dealId}/underwriting`)
      .then((r) => { if (live) setSteps(r.workflow || []) }).catch(() => { if (live) setSteps([]) })
    return () => { live = false }
  }, [dealId, refreshKey])
  if (!steps || !steps.length) return null
  const next = steps.find((s) => s.state !== 'done')
  return (
    <nav className="uw-flow" aria-label="Deal steps">
      {steps.map((s) => (
        <button key={s.key} type="button" className={`uw-step is-${s.state}`} onClick={() => goTo(s.tab)}
                title={s.next || 'Done'}>
          <span className="uw-step__icon" aria-hidden="true">{STEP_ICON[s.state]}</span>
          <span className="uw-step__label">{s.label}</span>
        </button>
      ))}
      {next ? <p className="uw-flow__next"><b>Next:</b> {next.label} - {next.next}</p> : <p className="uw-flow__next">Every step is done.</p>}
    </nav>
  )
}

function Metric({ label, value, sub, tone }) {
  return (
    <div className={`uw-metric ${tone ? `is-${tone}` : ''}`}>
      <div className="uw-metric__label">{label}</div>
      <div className="uw-metric__value">{value}</div>
      {sub ? <div className="uw-metric__sub">{sub}</div> : null}
    </div>
  )
}

function Scenario({ kind, data }) {
  if (!data || !data.available) return <p className="ws-muted">{data?.why || 'Not available yet.'}</p>
  const rows = kind === 'wholesale'
    ? [['Offer range', range(data.offer_low, data.offer_high)], ['Max offer (MAO)', fmtMoney(data.mao)],
       ['Assignment fee', fmtMoney(data.assignment_fee)], ['Price to end buyer (top of range + fee)', fmtMoney(data.buyer_price_at_mao)]]
    : kind === 'flip'
      ? [['Purchase', fmtMoney(data.purchase)], ['Repairs', fmtMoney(data.repairs)],
         ['Closing + holding + financing', fmtMoney((data.buy_closing || 0) + (data.holding || 0) + (data.financing || 0))],
         ['Selling costs', fmtMoney(data.selling)], ['Net profit', fmtMoney(data.net_profit)],
         ['Cash invested', fmtMoney(data.cash_invested)], ['ROI', pct(data.roi_pct)]]
      : [['Rent', `${fmtMoney(data.rent_monthly)}/mo`], ['Net operating income', `${fmtMoney(data.noi_annual)}/yr`],
         ['Mortgage', `${fmtMoney(data.payment_monthly)}/mo`], ['Cash flow', `${fmtMoney(data.cash_flow_monthly)}/mo`],
         ['Cap rate', pct(data.cap_rate_pct)], ['Cash-on-cash', pct(data.cash_on_cash_pct)],
         ['Cash invested', fmtMoney(data.cash_invested)]]
  return (
    <>
      <dl className="uw-dl">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <details className="uw-steps"><summary>How this was calculated</summary>
        <ol>{(data.steps || []).map((s, i) => <li key={i}>{s}</li>)}</ol>
      </details>
    </>
  )
}

function RepairEditor({ dealId, evoId, onSaved, busy, setBusy, setErr }) {
  const blank = { category: 'Roof', item: '', quantity: 1, unit: 'each', unit_cost: '' }
  const [lines, setLines] = useState([{ ...blank }])
  const [contingency, setContingency] = useState(15)
  const [quality, setQuality] = useState('standard')
  const [visits, setVisits] = useState([])
  useEffect(() => {
    if (!evoId) return
    api.get(`/wholesale/appointments?property_id=${encodeURIComponent(evoId)}&when=all&include_test=true`)
      .then((r) => setVisits(r.items || [])).catch(() => setVisits([]))
  }, [evoId])
  const set = (i, k, v) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  const subtotal = lines.reduce((s, l) => s + (Number(l.quantity) || 0) * (Number(l.unit_cost) || 0), 0)
  async function save(body) {
    setBusy(true); setErr(null)
    try { await api.post(`/wholesale/deals/${dealId}/repairs/itemized`, body); onSaved() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="uw-repairs">
      <div className="uw-table-wrap">
        <table className="uw-table">
          <thead><tr><th>Category</th><th>What</th><th>Qty</th><th>Unit</th><th>Unit cost</th><th className="ws-num">Line</th><th /></tr></thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td><select value={l.category} onChange={(e) => set(i, 'category', e.target.value)}>
                  {REPAIR_CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select></td>
                <td><input value={l.item} placeholder="e.g. Replace shingles" onChange={(e) => set(i, 'item', e.target.value)} /></td>
                <td><input type="number" min="0" step="any" value={l.quantity} onChange={(e) => set(i, 'quantity', e.target.value)} /></td>
                <td><input value={l.unit} onChange={(e) => set(i, 'unit', e.target.value)} /></td>
                <td><input type="number" min="0" step="any" value={l.unit_cost} placeholder="$" onChange={(e) => set(i, 'unit_cost', e.target.value)} /></td>
                <td className="ws-num">{fmtMoney((Number(l.quantity) || 0) * (Number(l.unit_cost) || 0))}</td>
                <td><button type="button" className="btn btn--secondary btn--sm" aria-label="Remove line"
                            onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : [{ ...blank }])}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="uw-row">
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => setLines([...lines, { ...blank }])}>+ Add a line</button>
        <label>Quality <select value={quality} onChange={(e) => setQuality(e.target.value)}>
          <option value="rental">Rental grade (x0.85)</option><option value="standard">Standard</option><option value="premium">Premium (x1.3)</option></select></label>
        <label>Contingency % <input type="number" min="0" max="100" value={contingency} onChange={(e) => setContingency(e.target.value)} style={{ width: 70 }} /></label>
        <span className="ws-muted">Lines: {fmtMoney(subtotal)}</span>
        <button type="button" className="btn btn--primary" disabled={busy}
                onClick={() => save({ items: lines.filter((l) => l.item || l.unit_cost), contingency_pct: Number(contingency), quality })}>
          Save repair estimate</button>
      </div>
      {visits.length ? (
        <div className="uw-row">
          <span className="ws-muted">Or use a walkthrough:</span>
          {visits.slice(0, 3).map((v) => (
            <button key={v.id} type="button" className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => save({ from_visit: v.id, contingency_pct: Number(contingency), quality })}>
              Visit {v.local_date}</button>
          ))}
        </div>
      ) : null}
      <p className="ws-muted uw-small">You price each line. A photo or walkthrough is not a professional inspection; low = the lines, expected adds the contingency, high adds it twice.</p>
    </div>
  )
}

function Provenance({ dealId, refreshKey }) {
  const [p, setP] = useState(null)
  useEffect(() => { api.get(`/wholesale/deals/${dealId}/provenance`).then(setP).catch(() => setP(null)) }, [dealId, refreshKey])
  if (!p) return null
  const show = (v) => (v == null || v === '' ? 'not on file' : typeof v === 'number' && v > 999 ? fmtMoney(v) : String(v))
  return (
    <div className="ws-panel">
      <h4 style={{ marginTop: 0 }}>Where these numbers come from</h4>
      <div className="uw-table-wrap"><table className="uw-table">
        <thead><tr><th>Fact</th><th>Value</th><th>Source</th><th>Checked</th></tr></thead>
        <tbody>{p.facts.map((f) => (
          <tr key={f.fact} className={f.value == null ? 'is-quiet' : ''}><td>{f.fact}</td><td>{show(f.value)}</td>
            <td className="uw-small">{f.source}{f.note ? <div className="ws-muted">{f.note}</div> : null}</td>
            <td>{f.verified === true ? <span className="ws-pill is-ok">verified</span> : f.verified === false ? <span className="ws-pill">not verified</span> : '—'}</td></tr>))}</tbody>
      </table></div>
      <p className="ws-muted uw-small">{p.rule}</p>
    </div>
  )
}

function FindComps({ dealId, onAdded }) {
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [entry, setEntry] = useState({})
  const [radius, setRadius] = useState(1)
  const [months, setMonths] = useState(12)
  async function search(allowRentcast) {
    setBusy(true); setErr(null)
    try { setRes(await api.post(`/wholesale/deals/${dealId}/comps/find`, { radius_miles: Number(radius), months: Number(months), allow_rentcast_call: !!allowRentcast })) }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function add(c) {
    const e = entry[c.key] || {}
    setBusy(true); setErr(null)
    try {
      await api.post(`/wholesale/deals/${dealId}/comps/find/add`, { key: c.key, sale_price: e.price ? Number(e.price) : null, source_reference: e.ref || null, radius_miles: Number(radius), months: Number(months) })
      if (onAdded) onAdded()
      await search(false)
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="uw-steps" style={{ marginTop: 10 }}>
      <div className="uw-row" style={{ marginTop: 0 }}>
        <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => search(false)}>{busy ? 'Searching…' : 'Find comps automatically'}</button>
        <label>Within <input type="number" min="0.25" max="5" step="0.25" value={radius} onChange={(e) => setRadius(e.target.value)} style={{ width: 60 }} /> mi</label>
        <label>Last <input type="number" min="1" max="36" value={months} onChange={(e) => setMonths(e.target.value)} style={{ width: 50 }} /> months</label>
      </div>
      {err ? <div className="ws-error">{err}</div> : null}
      {res ? (
        <>
          <ul className="uw-history" style={{ marginTop: 8 }}>
            {res.sources.map((s) => (
              <li key={s.key}><b>{s.label}</b> ({s.cost}): {s.available ? `${s.found} found` : s.why}{s.error ? ` - ${s.error}` : ''}
                {s.key === 'rentcast_listings' && s.available && !s.found ? <> · <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => search(true)}>Use 1 free RentCast lookup</button></> : null}</li>))}
          </ul>
          {res.gap ? <div className="uw-missing" style={{ marginTop: 6 }}>{res.gap}</div> : null}
          {(res.notes || []).map((n) => <p key={n} className="ws-muted uw-small">{n}</p>)}
          {res.candidates.length ? (
            <div className="uw-table-wrap"><table className="uw-table">
              <thead><tr><th>Address</th><th>What it is</th><th>Date</th><th className="ws-num">Price</th><th className="ws-num">Score</th><th>Add</th></tr></thead>
              <tbody>{res.candidates.map((c) => (
                <tr key={c.key} className={c.excluded.length ? 'is-quiet' : ''}>
                  <td>{c.address}{c.distance_miles != null ? <span className="ws-muted"> · {c.distance_miles} mi</span> : null}
                    <div className="ws-muted uw-small">{[c.square_feet && `${c.square_feet} sq ft`, c.year_built && `built ${c.year_built}`, c.legal].filter(Boolean).join(' · ')}</div>
                    <details><summary className="uw-small">why</summary><div className="uw-small">{c.why.join('; ')}{c.excluded.length ? <div className="ws-error">Not comparable: {c.excluded.join('; ')}</div> : null}<div className="ws-muted">{c.source} · {c.source_reference}</div></div></details></td>
                  <td><span className={`ws-pill ${c.price_kind === 'CLOSED_SALE' ? 'is-ok' : c.price_kind === 'TRANSFER_NO_PRICE' ? 'is-warn' : ''}`}>{c.kind_label}</span></td>
                  <td className="uw-small">{c.date || '—'}<div className="ws-muted">{c.date_kind}</div></td>
                  <td className="ws-num">{c.price != null ? fmtMoney(c.price) : '—'}</td>
                  <td className="ws-num">{c.score}</td>
                  <td>{c.needs_price ? (
                    <div className="uw-row" style={{ marginTop: 0 }}>
                      <input type="number" placeholder="Sale price" style={{ width: 95 }} onChange={(e) => setEntry({ ...entry, [c.key]: { ...(entry[c.key] || {}), price: e.target.value } })} />
                      <input placeholder="MLS # / source" style={{ width: 110 }} onChange={(e) => setEntry({ ...entry, [c.key]: { ...(entry[c.key] || {}), ref: e.target.value } })} />
                      <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => add(c)}>Add</button>
                    </div>) : c.usable_for_arv ? <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => add(c)}>Add</button>
                    : <span className="ws-muted uw-small">{c.excluded.length ? 'not comparable' : 'reference only'}</span>}</td>
                </tr>))}</tbody>
            </table></div>
          ) : <p className="ws-muted">No candidates from the connected sources.</p>}
          <p className="ws-muted uw-small">{res.rule}</p>
        </>
      ) : <p className="ws-muted uw-small">Searches county deed records, the Johnson County roll and RentCast listings (inside the free cap). Nothing is added until you add it.</p>}
    </div>
  )
}

export function UnderwritingTab({ dealId, goTo, onChanged }) {
  const [a, setA] = useState(null)
  const [form, setForm] = useState({})
  const [strat, setStrat] = useState('wholesale')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [note, setNote] = useState(null)

  const load = useCallback(() => api.get(`/wholesale/deals/${dealId}/underwriting`)
    .then((r) => { setA(r); setForm(r.assumptions || {}) }).catch((e) => setErr(e.message)), [dealId])
  useEffect(() => { load() }, [load])

  async function run() {
    setBusy(true); setErr(null); setNote(null)
    try {
      const r = await api.post(`/wholesale/deals/${dealId}/underwriting`, { assumptions: form })
      setA(r); setForm(r.assumptions || {}); setNote('Analysis updated and saved to the history.')
      if (onChanged) onChanged()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function importCsv(file) {
    if (!file) return
    setBusy(true); setErr(null); setNote(null)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await api.post(`/wholesale/deals/${dealId}/comps/import`, fd)
      setNote(`${r.added} comparable sale${r.added === 1 ? '' : 's'} added${r.refused?.length ? `; ${r.refused.length} row(s) skipped: ${r.refused.slice(0, 3).map((x) => `row ${x.row} ${x.why}`).join('; ')}` : ''}.`)
      await load(); if (onChanged) onChanged()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function requestApproval() {
    setBusy(true); setErr(null)
    try {
      await api.post(`/wholesale/deals/${dealId}/approvals`, { kind: 'offer', amount: a.offer.range_high })
      setNote('Offer approval requested. It is waiting in the Approval Center.'); if (onChanged) onChanged()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!a) return <div className="ws-panel">{err ? <div className="ws-error">{err}</div> : 'Loading the analysis…'}</div>
  const p = a.profile || {}
  const s = a.scenarios || {}
  return (
    <div className="uw">
      <div className="ws-panel uw-head">
        <div className="uw-head__row">
          <div>
            <h3 style={{ margin: 0 }}>Deal analysis</h3>
            <p className="ws-muted uw-small">From the comps, repairs and records on file. Confidence: <b>{a.confidence}</b>.</p>
          </div>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={run}>{busy ? 'Working…' : 'Analyze deal'}</button>
        </div>
        {err ? <div className="ws-error">{err}</div> : null}
        {note ? <div className="ws-good">{note}</div> : null}
        <div className="uw-metrics">
          <Metric label="After-repair value" value={a.arv.value != null ? fmtMoney(a.arv.value) : 'not yet'}
                  sub={a.arv.value != null ? `range ${range(a.arv.low, a.arv.high)}` : (a.arv.label || 'needs closed sales')} />
          <Metric label="Repairs" value={a.repairs.amount != null ? fmtMoney(a.repairs.amount) : 'not yet'}
                  sub={a.repairs.amount != null ? `${range(a.repairs.low, a.repairs.high)} · ${a.repairs.label}` : a.repairs.label} />
          <Metric label="Max offer (MAO)" value={a.offer.mao != null ? fmtMoney(a.offer.mao) : 'not yet'} sub={a.offer.gate} tone={a.offer.mao != null ? 'good' : null} />
          <Metric label="Offer range" value={range(a.offer.range_low, a.offer.range_high)} sub="rounded down to $500" />
        </div>
        {a.missing?.length ? (
          <div className="uw-missing"><b>What's missing</b>
            <ul>{a.missing.map((m) => <li key={m}>{m}</li>)}</ul></div>
        ) : null}
        <div className="uw-row">
          <button type="button" className="btn btn--secondary" disabled={busy || !a.offer.can_request_approval} onClick={requestApproval}
                  title={a.offer.can_request_approval ? '' : 'Needs a calculated MAO first'}>Prepare offer (request approval)</button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo('buyers')}>Find buyers</button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo('sharing')}>Create deal package</button>
          <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo('closing')}>Manage disposition</button>
        </div>
      </div>

      <div className="ws-panel">
        <div className="uw-head__row"><h4 style={{ margin: 0 }}>Scenarios</h4>
          <div className="uw-seg" role="tablist">{STRATS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={strat === k} className={strat === k ? 'is-on' : ''} onClick={() => setStrat(k)}>{l}</button>))}</div>
        </div>
        <div className="uw-cases">
          {CASES.map(([k, l]) => (
            <div key={k} className={`uw-case ${k === 'base' ? 'is-base' : ''}`}>
              <div className="uw-case__head">{l}<span className="ws-muted uw-small"> · ARV {fmtMoney(s[k]?.arv)} · repairs {fmtMoney(s[k]?.repairs)}</span></div>
              <Scenario kind={strat} data={s[k]?.[strat]} />
            </div>
          ))}
        </div>
        <details className="uw-assume">
          <summary>Assumptions (starting values - edit for this deal)</summary>
          <div className="uw-assume__grid">
            {Object.keys(a.assumption_labels || {}).filter((k) => (strat === 'wholesale' ? k === 'offer_range_pct' : k.startsWith(strat === 'flip' ? 'flip_' : 'rent_'))).map((k) => (
              <label key={k}>{a.assumption_labels[k]}
                <input type="number" min="0" step="any" value={form[k] ?? ''} placeholder={k === 'rent_monthly' ? 'required' : ''}
                       onChange={(e) => setForm({ ...form, [k]: e.target.value === '' ? null : e.target.value })} /></label>
            ))}
          </div>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={run}>Re-run with these</button>
        </details>
      </div>

      <div className="ws-panel">
        <div className="uw-head__row"><h4 style={{ margin: 0 }}>Comparable sales ({a.comps.counted} of {a.comps.count} counted)</h4>
          <label className="btn btn--secondary btn--sm" style={{ cursor: 'pointer' }}>Import CSV
            <input type="file" accept=".csv,text/csv" hidden onChange={(e) => { importCsv(e.target.files[0]); e.target.value = '' }} /></label>
        </div>
        <p className="ws-muted uw-small">Closed sales only, each with a source (MLS number or document). CSV columns: address, sale_price, sale_date, sqft, beds, baths, distance_miles, mls_number. Estimates and list prices never count as sales.</p>
        {a.comps.items.length ? (
          <div className="uw-table-wrap"><table className="uw-table">
            <thead><tr><th>Address</th><th className="ws-num">Sold</th><th>Date</th><th className="ws-num">$/sqft</th><th className="ws-num">Miles</th><th>Counts?</th><th>Source</th></tr></thead>
            <tbody>{a.comps.items.map((c) => (
              <tr key={c.id} className={c.counted_in_arv ? '' : 'is-quiet'}>
                <td>{c.address || '—'}{c.latitude != null ? <> · <a href={`https://www.google.com/maps?q=${c.latitude},${c.longitude}`} target="_blank" rel="noreferrer">map</a></> : null}</td>
                <td className="ws-num">{fmtMoney(c.sale_price)}</td>
                <td>{c.sale_date || '—'}{c.stale ? <span className="ws-pill is-warn" style={{ marginLeft: 4 }}>older than 6 mo</span> : null}</td>
                <td className="ws-num">{c.price_per_sqft ? `$${Math.round(c.price_per_sqft)}` : '—'}</td>
                <td className="ws-num">{c.distance_miles != null ? c.distance_miles.toFixed(2) : '—'}</td>
                <td>{c.counted_in_arv ? <span className="ws-pill is-ok">Yes</span> : <span title={(c.why_not_counted || []).join('; ')} className="ws-pill">No{c.why_not_counted?.length ? `: ${c.why_not_counted[0]}` : ''}</span>}</td>
                <td className="ws-muted">{c.source_reference || c.source}{c.verification === 'human_verified' ? ' · checked' : ''}</td>
              </tr>))}</tbody>
          </table></div>
        ) : <p className="ws-muted">No comparable sales yet. Import a CSV or add them under Analysis &amp; comps.</p>}
        <FindComps dealId={dealId} onAdded={() => { load(); if (onChanged) onChanged() }} />
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => goTo('analysis')}>Edit comps</button>
      </div>

      <div className="ws-panel">
        <h4 style={{ marginTop: 0 }}>Estimate repairs</h4>
        <RepairEditor dealId={dealId} evoId={p.evosense_property_id} busy={busy} setBusy={setBusy} setErr={setErr}
                      onSaved={() => { setNote('Repair estimate saved.'); load(); if (onChanged) onChanged() }} />
      </div>

      <div className="ws-panel">
        <h4 style={{ marginTop: 0 }}>Property profile</h4>
        <dl className="uw-dl uw-dl--wide">
          {[['Address', [p.address, p.city, p.state, p.zip].filter(Boolean).join(', ')], ['County', p.county], ['Parcel', p.parcel],
            ['Legal description', p.legal_description], ['Type', p.property_type], ['Beds / baths', [p.beds, p.baths].filter((x) => x != null).join(' / ')],
            ['Living area', p.square_feet ? `${Number(p.square_feet).toLocaleString()} sq ft` : null], ['Lot', p.lot_size_sqft ? `${Number(p.lot_size_sqft).toLocaleString()} sq ft` : null],
            ['Year built', p.year_built], ['Owner of record', p.owner],
            ['Tax value', p.tax_value != null ? `${fmtMoney(p.tax_value)} (${p.tax_value_source || 'appraisal district'}) - reference only` : null],
            ['Mortgage balance', p.mortgage_balance != null ? `${fmtMoney(p.mortgage_balance)} (${p.mortgage_source || 'reported'})` : null],
            ['Foreclosure', p.foreclosure ? `${p.foreclosure.status}${p.foreclosure.sale_date ? ` · sale ${p.foreclosure.sale_date}` : ''}${p.foreclosure.document ? ` · notice ${p.foreclosure.document}` : ''}` : null],
            ['Distress signals', (p.distress || []).join(', ').replace(/_/g, ' ').toLowerCase()],
            ['Sources', (p.sources || []).join(', ')], ['Last checked', p.last_verified ? p.last_verified.slice(0, 10) : null]]
            .map(([k, v]) => <div key={k}><dt>{k}</dt><dd className={v ? '' : 'is-quiet'}>{v || 'not on file'}</dd></div>)}
        </dl>
      </div>

      <Provenance dealId={dealId} refreshKey={a.calculated_at} />

      {a.history?.length ? (
        <div className="ws-panel">
          <h4 style={{ marginTop: 0 }}>Analysis history</h4>
          <ul className="uw-history">{a.history.map((h, i) => <li key={i}><span className="ws-muted">{(h.at || '').replace('T', ' ').slice(0, 16)} UTC</span> {h.summary}</li>)}</ul>
        </div>
      ) : null}
    </div>
  )
}
