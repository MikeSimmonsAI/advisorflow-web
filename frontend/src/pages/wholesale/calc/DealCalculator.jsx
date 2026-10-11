/* DEAL CALCULATOR - "what's the bottom line?" for one house, in one place.
 *
 *   1. What it's worth fixed up (ARV)
 *   2. What it costs to fix: quick (sq ft x condition), line by line, or one number,
 *      plus a contingency cushion
 *   3. Your numbers: investor %, closing costs, your fee
 *   4. The bottom line: the most you can offer the seller, how that compares
 *      with what the seller wants, and what the investor makes buying it from you
 *
 * Same offer formula the server uses for a deal's MAO. A what-if: nothing is
 * saved or sent. Defaults come from Wholesale Settings when they load.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import * as C from './dealCalc'
import './deal-calc.css'

function Money({ label, value, onChange, hint, id, suffix = '', prefix = '$', placeholder }) {
  return (
    <label className="dc-field" htmlFor={id}>
      <span className="dc-field__label">{label}</span>
      <span className="dc-field__input">
        {prefix ? <span className="dc-field__affix">{prefix}</span> : null}
        <input id={id} inputMode="decimal" value={value ?? ''} placeholder={placeholder}
               onChange={(e) => onChange(e.target.value)} />
        {suffix ? <span className="dc-field__affix">{suffix}</span> : null}
      </span>
      {hint ? <span className="dc-field__hint">{hint}</span> : null}
    </label>
  )
}

function Row({ k, v, strong, minus, plus }) {
  return (
    <div className={`dc-row${strong ? ' is-strong' : ''}`}>
      <span>{k}</span>
      <span>{v == null ? '—' : `${minus ? '− ' : plus ? '+ ' : ''}${C.money(v)}`}</span>
    </div>
  )
}

export default function DealCalculator({ prefill = {}, compact = false }) {
  const [x, setX] = useState(() => ({
    arv: prefill.arv ?? '', asking: prefill.asking ?? '', sqft: prefill.sqft ?? '',
    repairMode: 'quick', condition: 'poor', rate: '25', repairFlat: '',
    items: C.REPAIR_ITEMS.map((label) => ({ label, amount: '' })),
    contingencyPct: String(C.DEFAULTS.contingencyPct),
    investorPct: String(C.DEFAULTS.investorPct), fee: String(C.DEFAULTS.fee),
    closingPct: String(C.DEFAULTS.closingPct), closingFlat: String(C.DEFAULTS.closingFlat),
    sellingPct: String(C.DEFAULTS.sellingPct), holdMonths: String(C.DEFAULTS.holdMonths),
    holdMonthly: String(C.DEFAULTS.holdMonthly), offer: '',
  }))
  const [fromSettings, setFromSettings] = useState(false)
  const [copied, setCopied] = useState(false)
  const set = (k) => (v) => setX((s) => ({ ...s, [k]: v }))

  // Your own formula, from Wholesale Settings, once - a person's edits win after that.
  useEffect(() => {
    let live = true
    api.get('/wholesale/settings').then((s) => {
      if (!live || !s) return
      setX((cur) => ({
        ...cur,
        investorPct: s.investor_percentage != null ? String(s.investor_percentage) : cur.investorPct,
        fee: s.default_wholesale_fee != null ? String(s.default_wholesale_fee) : cur.fee,
        closingPct: s.transaction_cost_percent != null ? String(s.transaction_cost_percent) : cur.closingPct,
        closingFlat: s.transaction_cost_flat != null ? String(s.transaction_cost_flat) : cur.closingFlat,
      }))
      setFromSettings(true)
    }).catch(() => null)
    return () => { live = false }
  }, [])
  // A property's facts arriving later (or changing) fill blanks only.
  useEffect(() => {
    setX((cur) => ({ ...cur,
      arv: cur.arv === '' && prefill.arv != null ? String(prefill.arv) : cur.arv,
      asking: cur.asking === '' && prefill.asking != null ? String(prefill.asking) : cur.asking,
      sqft: cur.sqft === '' && prefill.sqft != null ? String(prefill.sqft) : cur.sqft }))
  }, [prefill.arv, prefill.asking, prefill.sqft])

  const res = useMemo(() => C.calculate(x), [x])
  const rep = res.repairs || {}

  async function copy() {
    try { await navigator.clipboard.writeText(C.summary(res, prefill.address)); setCopied(true); setTimeout(() => setCopied(false), 2000) }
    catch { setCopied(false) }
  }

  return (
    <div className={`dc${compact ? ' dc--compact' : ''}`}>
      <div className="dc-inputs">
        <section className="dc-card" aria-labelledby="dc-h1">
          <h3 className="dc-h" id="dc-h1"><span className="dc-step">1</span>The house</h3>
          <div className="dc-grid">
            <Money id="dc-arv" label="After-repair value (ARV)" value={x.arv} onChange={set('arv')}
                   hint={prefill.taxValue ? `Tax value ${C.money(prefill.taxValue)} is not an ARV - use sold comps.` : 'What it sells for fixed up, from sold comps.'} />
            <Money id="dc-ask" label="Seller's asking price" value={x.asking} onChange={set('asking')} hint="Leave blank if you don't know yet." />
          </div>
        </section>

        <section className="dc-card" aria-labelledby="dc-h2">
          <h3 className="dc-h" id="dc-h2"><span className="dc-step">2</span>Repairs</h3>
          <div className="dc-seg" role="group" aria-label="How to estimate repairs">
            {[['quick', 'Quick (by sq ft)'], ['itemized', 'Line by line'], ['flat', 'One number']].map(([k, l]) => (
              <button key={k} type="button" aria-pressed={x.repairMode === k} onClick={() => set('repairMode')(k)}>{l}</button>
            ))}
          </div>
          {x.repairMode === 'quick' ? (
            <div className="dc-grid">
              <Money id="dc-sqft" label="Living area" prefix="" suffix="sq ft" value={x.sqft} onChange={set('sqft')} />
              <label className="dc-field" htmlFor="dc-cond">
                <span className="dc-field__label">Condition</span>
                <select id="dc-cond" value={x.condition} onChange={(e) => {
                  const c = C.CONDITION_RATES.find(([k]) => k === e.target.value)
                  setX((s) => ({ ...s, condition: e.target.value, rate: c ? String(c[2]) : s.rate }))
                }}>
                  {C.CONDITION_RATES.map(([k, l, r]) => <option key={k} value={k}>{l} (${r}/sq ft)</option>)}
                </select>
              </label>
              <Money id="dc-rate" label="Rate" suffix="/ sq ft" value={x.rate} onChange={set('rate')} hint="Change it if your contractor says otherwise." />
            </div>
          ) : x.repairMode === 'itemized' ? (
            <div className="dc-items">
              {x.items.map((it, i) => (
                <Money key={it.label} id={`dc-item-${i}`} label={it.label} value={it.amount} placeholder="0"
                       onChange={(v) => setX((s) => ({ ...s, items: s.items.map((y, j) => (j === i ? { ...y, amount: v } : y)) }))} />
              ))}
            </div>
          ) : (
            <div className="dc-grid"><Money id="dc-rflat" label="Total repairs" value={x.repairFlat} onChange={set('repairFlat')} hint="From a walkthrough or a contractor bid." /></div>
          )}
          <div className="dc-grid">
            <Money id="dc-cont" label="Cushion for surprises" prefix="" suffix="%" value={x.contingencyPct} onChange={set('contingencyPct')} />
          </div>
          <div className="dc-subtotal">
            <Row k="Repairs" v={rep.base} /><Row k="Cushion" v={rep.contingency} plus /><Row k="Repairs total" v={rep.total} strong />
          </div>
        </section>

        <section className="dc-card" aria-labelledby="dc-h3">
          <h3 className="dc-h" id="dc-h3"><span className="dc-step">3</span>Your numbers
            {fromSettings ? <span className="dc-note">from Wholesale Settings</span> : null}</h3>
          <div className="dc-grid">
            <Money id="dc-pct" label="Investors pay up to" prefix="" suffix="% of ARV" value={x.investorPct} onChange={set('investorPct')} />
            <Money id="dc-fee" label="Your assignment fee" value={x.fee} onChange={set('fee')} />
            <Money id="dc-cpct" label="Closing costs" prefix="" suffix="% of ARV" value={x.closingPct} onChange={set('closingPct')} />
            <Money id="dc-cflat" label="Plus flat closing costs" value={x.closingFlat} onChange={set('closingFlat')} />
          </div>
          <details className="dc-more">
            <summary>Investor's costs after they buy (for their bottom line)</summary>
            <div className="dc-grid">
              <Money id="dc-hm" label="Months to fix and sell" prefix="" suffix="months" value={x.holdMonths} onChange={set('holdMonths')} />
              <Money id="dc-hmo" label="Holding cost per month" value={x.holdMonthly} onChange={set('holdMonthly')} hint="Taxes, insurance, utilities, loan interest." />
              <Money id="dc-sell" label="Cost to sell" prefix="" suffix="% of ARV" value={x.sellingPct} onChange={set('sellingPct')} hint="Agent and seller closing costs." />
            </div>
          </details>
        </section>
      </div>

      <aside className="dc-result" aria-live="polite" aria-label="Bottom line">
        <h3 className="dc-h"><span className="dc-step">4</span>Bottom line</h3>
        {!res.ready ? (
          <p className="dc-missing">Add {res.missing.join(' and ')} to see your max offer.</p>
        ) : (
          <>
            <div className={`dc-big is-${res.mao > 0 ? 'good' : 'bad'}`}>
              <span className="dc-big__k">Most you can offer the seller</span>
              <span className="dc-big__v">{C.money(res.mao)}</span>
            </div>
            <div className="dc-math">
              <Row k={`ARV × ${C.num(x.investorPct)}%`} v={res.investorBuy} />
              <Row k="Repairs" v={res.repairs.total} minus />
              <Row k="Closing costs" v={res.closing} minus />
              <Row k="Your fee" v={res.fee} minus />
              <Row k="Max offer (MAO)" v={res.mao} strong />
            </div>
            <p className={`dc-verdict is-${res.tone}`}>{res.verdict}</p>
            <Money id="dc-offer" label="Your offer to the seller" value={x.offer} onChange={set('offer')}
                   placeholder={String(res.offer)} hint="Blank = max offer rounded down to $500." />
            <div className="dc-math">
              <div className="dc-math__title">The investor buying from you</div>
              <Row k="Your contract price" v={res.offer} />
              <Row k="Your fee" v={res.fee} plus />
              <Row k="Repairs" v={res.repairs.total} plus />
              <Row k="Closing" v={res.closing} plus />
              <Row k="Holding" v={res.hold} plus />
              <Row k="Investor all-in" v={res.allIn} strong />
              <Row k="Sells for (ARV)" v={res.arv} />
              <Row k="Cost to sell" v={res.selling} minus />
              <Row k={`Investor profit${res.profitPct != null ? ` (${res.profitPct}%)` : ''}`} v={res.profit} strong />
            </div>
            <div className="dc-actions">
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={copy}>{copied ? 'Copied' : 'Copy summary'}</button>
            </div>
          </>
        )}
        <p className="dc-fine">Estimates for planning, not an appraisal. Nothing here is saved or sent.</p>
      </aside>
    </div>
  )
}
