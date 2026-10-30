/* OLP LAND & FARM TOOLS - what makes Land Partners and Farm & Ranch their own
 * workspaces, not Wholesale filtered to land.
 *
 *   Land Partners  land deal calculator (offer by price per acre + seller
 *                  financing), due-diligence checklist, exit plan
 *   Farm & Ranch   acreage & income calculator, farm checklist
 *
 * Everything is saved on the parcel's existing property-details record
 * (/wholesale/evosense/properties/:id/type-details and /type-facts): the same
 * tenant checks, the same audit entry, no new table. The calculators only use
 * numbers a person enters - nothing is assumed or invented. */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { num, landDeal, farmIncome } from './landMath'

const RADAR = '/wholesale/evosense/opportunity-radar?'
const details = (id) => `/wholesale/evosense/properties/${encodeURIComponent(id)}/type-details`
const facts = (id) => `/wholesale/evosense/properties/${encodeURIComponent(id)}/type-facts`

const usd = (v) => (v == null ? '—' : (v < 0 ? '−$' : '$') + Math.round(Math.abs(v)).toLocaleString())
const usd2 = (v) => (v == null ? '—' : '$' + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const label = (p) => p.address || (p.parcel_apn ? `Parcel ${p.parcel_apn}` : 'Address not verified')
const errText = (e) => e?.message || e?.detail || 'Something went wrong.'

/* ---------- data ---------- */

export function useParcels(market) {
  const [s, setS] = useState({ items: [], loading: true, error: '' })
  useEffect(() => {
    let alive = true
    api.get(RADAR + new URLSearchParams({ market, sort: 'score', limit: '50' }))
      .then((d) => { if (alive) setS({ items: d?.items || [], loading: false, error: '' }) })
      .catch((e) => { if (alive) setS({ items: [], loading: false, error: errText(e) }) })
    return () => { alive = false }
  }, [market])
  return s
}

function useDetails(id) {
  const [d, setD] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!id) { setD(null); return undefined }
    let alive = true
    setD(null); setError('')
    api.get(details(id)).then((r) => { if (alive) setD(r) }).catch((e) => { if (alive) setError(errText(e)) })
    return () => { alive = false }
  }, [id])
  const save = useCallback(async (values, evidence) => {
    const r = await api.post(facts(id), { values, evidence: evidence || {} })
    setD(r)
    return r
  }, [id])
  return { d, error, save }
}

/* ---------- small parts ---------- */

function Field({ id, label: l, value, onChange, prefix, suffix, hint }) {
  return (
    <label className="lt-field" htmlFor={id}>
      <span>{l}</span>
      <span className="lt-input">
        {prefix ? <i aria-hidden="true">{prefix}</i> : null}
        <input id={id} inputMode="decimal" value={value ?? ''} onChange={(e) => onChange(e.target.value)} placeholder="—" />
        {suffix ? <i aria-hidden="true">{suffix}</i> : null}
      </span>
      {hint ? <small>{hint}</small> : null}
    </label>
  )
}

function Out({ label: l, value, strong, tone }) {
  return (
    <div className={'lt-out' + (strong ? ' lt-out--strong' : '') + (tone ? ' lt-out--' + tone : '')}>
      <span>{l}</span><b>{value}</b>
    </div>
  )
}

function SaveBar({ busy, msg, error, onSave, disabled, children }) {
  return (
    <div className="lt-savebar">
      <button type="button" className="lt-btn" onClick={onSave} disabled={busy || disabled}>{busy ? 'Saving…' : children}</button>
      {msg ? <span className="lt-ok" role="status">{msg}</span> : null}
      {error ? <span className="lt-err" role="alert">{error}</span> : null}
    </div>
  )
}

export function ParcelPicker({ market, value, onChange, emptyText }) {
  const { items, loading, error } = useParcels(market)
  useEffect(() => { if (!value && items.length) onChange(items[0].id) }, [items, value, onChange])
  if (loading) return <p className="lp-empty" role="status">Loading your parcels…</p>
  if (error) return <p className="lt-err" role="alert">Parcels are unavailable. {error}</p>
  if (!items.length) return <p className="lp-empty"><b>{emptyText}</b><span>The tools below still work as a calculator; to save results, add the parcel first.</span></p>
  return (
    <label className="lt-field lt-picker">
      <span>Parcel</span>
      <select value={value || ''} onChange={(e) => onChange(e.target.value)} aria-label="Parcel">
        {items.map((p) => (
          <option key={p.id} value={p.id}>{label(p)} · {[p.city, p.state].filter(Boolean).join(', ') || 'location not verified'}</option>
        ))}
      </select>
    </label>
  )
}

function Tabs({ tabs, value, onChange, label: l }) {
  return (
    <div className="lt-tabs" role="tablist" aria-label={l}>
      {tabs.map(([k, t]) => (
        <button key={k} type="button" role="tab" aria-selected={value === k} className={'lt-tab' + (value === k ? ' is-on' : '')}
                onClick={() => onChange(k)}>{t}</button>
      ))}
    </div>
  )
}

/* ---------- checklist (land and farm) ---------- */

const STATUS = [['ok', 'OK'], ['missing', 'Missing'], ['problem', 'Problem']]

function Checklist({ rows, values, save, withExit, exitPlans }) {
  // Read once per parcel (the parent re-mounts this for another parcel); after a
  // save the saved answers become the new baseline.
  const [start, setStart] = useState(() => {
    const o = {}
    rows.forEach((r) => {
      o[r.status] = values?.[r.status]?.value || ''
      o[r.notes] = values?.[r.notes]?.value ?? ''
    })
    if (withExit) o.exit_plan = values?.exit_plan?.value || ''
    return o
  })
  const [form, setForm] = useState(start)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const counts = rows.reduce((c, r) => { const s = form[r.status]; c[s || 'open'] = (c[s || 'open'] || 0) + 1; return c }, {})
  const changed = Object.keys(form).filter((k) => String(form[k] ?? '') !== String(start[k] ?? ''))

  async function onSave() {
    setBusy(true); setMsg(''); setError('')
    try {
      const out = {}
      changed.forEach((k) => { out[k] = form[k] === '' ? null : form[k] })
      await save(out)
      setStart({ ...form })
      setMsg('Saved to this parcel')
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="lt-check">
      <p className="lt-sum" aria-live="polite">
        <b>{counts.ok || 0}</b> OK · <b className="warn">{counts.missing || 0}</b> missing · <b className="bad">{counts.problem || 0}</b> problem · <b>{counts.open || 0}</b> not checked
      </p>
      <ul className="lt-items">
        {rows.map((r) => (
          <li key={r.status} className={'lt-item' + (form[r.status] ? ' is-' + form[r.status] : '')}>
            <div className="lt-item__head">
              <span className="lt-item__name"><b>{r.item}</b><small>{r.help}</small></span>
              <span className="lt-seg" role="group" aria-label={r.item + ' status'}>
                {STATUS.map(([k, t]) => (
                  <button key={k} type="button" aria-pressed={form[r.status] === k} className={'lt-seg__b lt-seg__b--' + k + (form[r.status] === k ? ' is-on' : '')}
                          onClick={() => setForm({ ...form, [r.status]: form[r.status] === k ? '' : k })}>{t}</button>
                ))}
              </span>
            </div>
            <input className="lt-note" aria-label={r.item + ' notes'} placeholder="Notes - what you found and where"
                   value={form[r.notes] ?? ''} onChange={(e) => setForm({ ...form, [r.notes]: e.target.value })} />
          </li>
        ))}
      </ul>
      {withExit ? (
        <div className="lt-exit" role="radiogroup" aria-label="Exit plan">
          <span className="lt-exit__t">Exit plan</span>
          {exitPlans.map((x) => (
            <button key={x.key} type="button" role="radio" aria-checked={form.exit_plan === x.key}
                    className={'lt-chip' + (form.exit_plan === x.key ? ' is-on' : '')}
                    onClick={() => setForm({ ...form, exit_plan: form.exit_plan === x.key ? '' : x.key })}>{x.label}</button>
          ))}
        </div>
      ) : null}
      <SaveBar busy={busy} msg={msg} error={error} onSave={onSave} disabled={!changed.length}>
        {changed.length ? `Save ${changed.length} change${changed.length === 1 ? '' : 's'}` : 'No changes'}
      </SaveBar>
    </div>
  )
}

/* ---------- land deal calculator ---------- */

function LandCalculator({ values, save, canSave }) {
  const [i, setI] = useState(() => ({
    acres: values?.acreage?.value ?? '', ppa: values?.comp_price_per_acre?.value ?? '',
    offer: values?.purchase_price?.value ?? '', offerPct: '', closing: values?.acquisition_expenses?.value ?? '',
    sale: values?.buyer_price?.value ?? '', down: values?.sf_down_payment?.value ?? '',
    rate: values?.sf_rate?.value ?? '', years: values?.sf_term_years?.value ?? '',
  }))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const r = landDeal(i)
  const set = (k) => (v) => setI({ ...i, [k]: v, ...(k === 'offerPct' ? { offer: '' } : {}) })

  async function onSave() {
    setBusy(true); setMsg(''); setError('')
    try {
      await save({
        acreage: num(i.acres), comp_price_per_acre: num(i.ppa),
        purchase_price: r.offer != null ? Math.round(r.offer) : null,
        acquisition_expenses: num(i.closing), buyer_price: num(i.sale),
        sf_down_payment: num(i.down), sf_rate: num(i.rate), sf_term_years: num(i.years),
      })
      setMsg('Saved to this parcel')
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="lt-calc">
      <div className="lt-cols">
        <fieldset className="lt-box">
          <legend>1 · Your offer</legend>
          <Field id="lt-acres" label="Acres" value={i.acres} onChange={set('acres')} suffix="ac" />
          <Field id="lt-ppa" label="Recent land sales - price per acre" value={i.ppa} onChange={set('ppa')} prefix="$" hint="From sold land nearby. Not filled in for you." />
          <Field id="lt-pct" label="Offer as % of that value" value={i.offerPct} onChange={set('offerPct')} suffix="%" hint="Land buyers often offer well under market. Your number." />
          <Field id="lt-offer" label="…or type your offer" value={i.offer} onChange={(v) => setI({ ...i, offer: v })} prefix="$" />
          <Field id="lt-closing" label="Closing and holding costs" value={i.closing} onChange={set('closing')} prefix="$" />
        </fieldset>
        <fieldset className="lt-box">
          <legend>2 · How you sell it</legend>
          <Field id="lt-sale" label="Sale price" value={i.sale} onChange={set('sale')} prefix="$" />
          <Field id="lt-down" label="Down payment (seller financing)" value={i.down} onChange={set('down')} prefix="$" />
          <Field id="lt-rate" label="Interest rate" value={i.rate} onChange={set('rate')} suffix="%" />
          <Field id="lt-years" label="Term" value={i.years} onChange={set('years')} suffix="years" />
        </fieldset>
        <div className="lt-box lt-results" aria-live="polite">
          <h4>The deal</h4>
          <Out label="Value from land sales" value={usd(r.market)} />
          <Out label="Your offer" value={usd(r.offer)} strong />
          <Out label="Offer per acre" value={usd(r.offerPerAcre)} />
          <Out label="All-in cost" value={usd(r.allIn)} />
          <h4>Sell for cash</h4>
          <Out label="Profit" value={usd(r.cashProfit)} tone={r.cashProfit == null ? null : r.cashProfit >= 0 ? 'good' : 'bad'} strong />
          <h4>Sell with seller financing</h4>
          <Out label="Amount financed" value={usd(r.financed)} />
          <Out label={`Monthly payment${r.months ? ` × ${r.months}` : ''}`} value={usd2(r.pay)} strong />
          <Out label="Total collected" value={usd(r.collected)} />
          <Out label="Profit over the note" value={usd(r.notesProfit)} tone={r.notesProfit == null ? null : r.notesProfit >= 0 ? 'good' : 'bad'} strong />
          <Out label="Money back in" value={r.payback == null ? '—' : r.payback === 0 ? 'at closing' : `${r.payback} months`} />
        </div>
      </div>
      {canSave ? (
        <SaveBar busy={busy} msg={msg} error={error} onSave={onSave}>Save this deal to the parcel</SaveBar>
      ) : null}
      <p className="lt-fine">Only the numbers you enter are used. This is a planning tool, not an appraisal, a loan offer or legal advice.</p>
    </div>
  )
}

/* ---------- farm acreage & income ---------- */

const FARM_MAP = {
  crop: 'cropland_acres', cropRent: 'cropland_rent_per_acre', pasture: 'pasture_acres', pastureRent: 'pasture_rent_per_acre',
  timber: 'timber_acres', otherAcres: 'other_acres', hunting: 'hunting_lease_income', otherIncome: 'other_ag_income',
  taxes: 'property_taxes', insurance: 'insurance_costs', upkeep: 'upkeep_costs', cap: 'cap_rate', price: 'purchase_price',
}

function FarmCalculator({ values, save, canSave }) {
  const [i, setI] = useState(() => Object.fromEntries(Object.entries(FARM_MAP).map(([k, f]) => [k, values?.[f]?.value ?? ''])))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const r = farmIncome(i)
  const set = (k) => (v) => setI({ ...i, [k]: v })
  async function onSave() {
    setBusy(true); setMsg(''); setError('')
    try {
      await save(Object.fromEntries(Object.entries(FARM_MAP).map(([k, f]) => [f, num(i[k])])))
      setMsg('Saved to this property')
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="lt-calc">
      <div className="lt-cols">
        <fieldset className="lt-box">
          <legend>1 · The acres</legend>
          <div className="lt-pair">
            <Field id="fr-crop" label="Cropland" value={i.crop} onChange={set('crop')} suffix="ac" />
            <Field id="fr-croprent" label="Rent per acre / yr" value={i.cropRent} onChange={set('cropRent')} prefix="$" />
          </div>
          <div className="lt-pair">
            <Field id="fr-pasture" label="Pasture" value={i.pasture} onChange={set('pasture')} suffix="ac" />
            <Field id="fr-pasturerent" label="Grazing rent / ac / yr" value={i.pastureRent} onChange={set('pastureRent')} prefix="$" />
          </div>
          <div className="lt-pair">
            <Field id="fr-timber" label="Timber" value={i.timber} onChange={set('timber')} suffix="ac" />
            <Field id="fr-other" label="Homesite, water, other" value={i.otherAcres} onChange={set('otherAcres')} suffix="ac" />
          </div>
        </fieldset>
        <fieldset className="lt-box">
          <legend>2 · Income and costs per year</legend>
          <Field id="fr-hunt" label="Hunting / recreation lease" value={i.hunting} onChange={set('hunting')} prefix="$" />
          <Field id="fr-oi" label="Other farm income" value={i.otherIncome} onChange={set('otherIncome')} prefix="$" />
          <Field id="fr-tax" label="Property taxes" value={i.taxes} onChange={set('taxes')} prefix="$" />
          <Field id="fr-ins" label="Insurance" value={i.insurance} onChange={set('insurance')} prefix="$" />
          <Field id="fr-up" label="Upkeep - fences, roads, water" value={i.upkeep} onChange={set('upkeep')} prefix="$" />
          <div className="lt-pair">
            <Field id="fr-price" label="Asking / purchase price" value={i.price} onChange={set('price')} prefix="$" />
            <Field id="fr-cap" label="Cap rate (for value)" value={i.cap} onChange={set('cap')} suffix="%" />
          </div>
        </fieldset>
        <div className="lt-box lt-results" aria-live="polite">
          <h4>What the land earns</h4>
          <Out label="Total acres entered" value={r.acres == null ? '—' : `${r.acres.toLocaleString()} ac`} />
          <Out label="Farm income per year" value={usd(r.income)} strong />
          <Out label="Costs per year" value={usd(r.income == null ? null : r.costs)} />
          <Out label="Net income per year" value={usd(r.income == null ? null : r.net)} tone={r.income == null ? null : r.net >= 0 ? 'good' : 'bad'} strong />
          <Out label="Net per acre" value={usd(r.netPerAcre)} />
          <h4>Against the price</h4>
          <Out label="Price per acre" value={usd(r.perAcre)} />
          <Out label="Return on price" value={r.ret == null ? '—' : `${r.ret.toFixed(2)}%`} strong />
          <Out label="Value from income" value={usd(r.value)} />
        </div>
      </div>
      {canSave ? <SaveBar busy={busy} msg={msg} error={error} onSave={onSave}>Save acres, income and costs to this property</SaveBar> : null}
      <p className="lt-fine">Only the numbers you enter are used. Market value from land sales stays separate from income value and from the ag-use tax value.</p>
    </div>
  )
}

/* ---------- the two tool panels ---------- */

function useParcelTools() {
  const [id, setId] = useState('')
  const pick = useCallback((v) => setId(v), [])
  const { d, error, save } = useDetails(id)
  // Tools re-read a parcel when another is picked or its details first arrive -
  // never after their own save (that would wipe what the person just saved).
  return { id, pick, d, error, save, key: id + (d ? ':ready' : ':wait') }
}

export function LandDealTools() {
  const [tab, setTab] = useState('calc')
  const t = useParcelTools()
  const vals = t.d?.tools?.values
  return (
    <section className="lp-panel lt" aria-labelledby="lt-land">
      <header className="lp-panel__head">
        <h2 id="lt-land">Work a land deal</h2>
        {t.id ? <Link className="lp-link" to={`/wholesale/evosense/property/${encodeURIComponent(t.id)}`}>Open property research →</Link> : null}
      </header>
      <ParcelPicker market="land" value={t.id} onChange={t.pick} emptyText="No land parcels in your workspace yet." />
      {t.error ? <p className="lt-err" role="alert">{t.error}</p> : null}
      <Tabs label="Land deal tools" value={tab} onChange={setTab}
            tabs={[['calc', 'Deal calculator'], ['dd', 'Due diligence & exit plan']]} />
      {tab === 'calc' ? <LandCalculator key={t.key} values={vals} save={t.save} canSave={!!t.d} /> : t.d ? (
        <Checklist key={t.key} rows={t.d.tools.land_checklist} values={vals} save={t.save} withExit exitPlans={t.d.tools.exit_plans} />
      ) : <p className="lp-empty">{t.id ? 'Loading this parcel…' : 'Pick a parcel to work its checklist.'}</p>}
    </section>
  )
}

export function FarmTools() {
  const [tab, setTab] = useState('income')
  const t = useParcelTools()
  const vals = t.d?.tools?.values
  return (
    <section className="lp-panel lt lt--farm" aria-labelledby="lt-farm">
      <header className="lp-panel__head">
        <h2 id="lt-farm">Work a farm or ranch</h2>
        {t.id ? <Link className="lp-link" to={`/wholesale/evosense/property/${encodeURIComponent(t.id)}`}>Open property research →</Link> : null}
      </header>
      <ParcelPicker market="farm" value={t.id} onChange={t.pick} emptyText="No farms or ranches in your workspace yet." />
      {t.error ? <p className="lt-err" role="alert">{t.error}</p> : null}
      <Tabs label="Farm tools" value={tab} onChange={setTab}
            tabs={[['income', 'Acreage & income'], ['check', 'Farm checklist']]} />
      {tab === 'income' ? <FarmCalculator key={t.key} values={vals} save={t.save} canSave={!!t.d} /> : t.d ? (
        <Checklist key={t.key} rows={t.d.tools.farm_checklist} values={vals} save={t.save} />
      ) : <p className="lp-empty">{t.id ? 'Loading this property…' : 'Pick a property to work its checklist.'}</p>}
    </section>
  )
}
