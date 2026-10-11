/* Deal calculator - the math, kept apart from the screen so it can be tested.
 *
 * The offer formula is the SAME one the server uses for a deal's MAO
 * (app/services/wholesale_analysis.calculate_offer):
 *
 *   MAO = ARV x investor%  -  repairs  -  (ARV x closing% + closing flat)  -  your fee
 *
 * Then the investor's side, so the bottom line is visible for both of you:
 *
 *   investor all-in  = your contract price + your fee + repairs + closing + holding
 *   investor profit  = ARV - all-in - selling costs
 *
 * Every input is a person's what-if. Nothing here is saved or sent anywhere,
 * and a blank stays blank: no number is invented for a missing ARV.
 */

/** The platform's own rough repair bands, $ per square foot by condition
 *  (app/services/wholesale_repairs.REPAIR_PER_SQFT). Editable on screen. */
export const CONDITION_RATES = [
  ['good', 'Good - touch-ups', 5],
  ['fair', 'Fair - cosmetic (paint, floors)', 12],
  ['poor', 'Poor - kitchens, baths, systems', 25],
  ['distressed', 'Distressed - major / full rehab', 45],
]

export const REPAIR_ITEMS = [
  'Roof', 'HVAC', 'Foundation', 'Plumbing', 'Electrical', 'Kitchen', 'Bathrooms', 'Flooring',
  'Paint (inside & out)', 'Windows & doors', 'Yard & clean-out', 'Other',
]

export const DEFAULTS = {
  investorPct: 70, fee: 10000, closingPct: 3, closingFlat: 0,
  contingencyPct: 10, sellingPct: 8, holdMonths: 4, holdMonthly: 750,
}

export function num(v) {
  if (v === null || v === undefined) return null
  const s = String(v).replace(/[$,\s%]/g, '')
  if (s === '') return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

const r0 = (n) => (n == null ? null : Math.round(n))

/** Repairs from the inputs: quick (sq ft x rate), itemized, or one number; plus contingency. */
export function repairsOf(x) {
  let base = null
  if (x.repairMode === 'itemized') {
    const vals = (x.items || []).map((i) => num(i.amount)).filter((v) => v != null)
    base = vals.length ? vals.reduce((a, b) => a + b, 0) : null
  } else if (x.repairMode === 'flat') {
    base = num(x.repairFlat)
  } else {
    const sqft = num(x.sqft)
    const rate = num(x.rate)
    base = sqft != null && rate != null ? sqft * rate : null
  }
  if (base == null) return { base: null, contingency: null, total: null }
  const c = (num(x.contingencyPct) || 0) / 100
  return { base: r0(base), contingency: r0(base * c), total: r0(base * (1 + c)) }
}

export function money(n) {
  if (n == null || !Number.isFinite(n)) return '—'
  const s = Math.round(Math.abs(n)).toLocaleString('en-US')
  return (n < 0 ? '−$' : '$') + s
}

/** Everything the screen shows. Missing ARV or repairs -> no offer, with the reason. */
export function calculate(x) {
  const arv = num(x.arv)
  const rep = repairsOf(x)
  const pct = num(x.investorPct)
  const fee = num(x.fee) || 0
  const closing = arv == null ? null : arv * ((num(x.closingPct) || 0) / 100) + (num(x.closingFlat) || 0)
  const missing = []
  if (arv == null) missing.push('After-repair value (ARV)')
  if (rep.total == null) missing.push('Repairs')
  if (pct == null) missing.push('Investor %')
  if (missing.length) return { ready: false, missing, repairs: rep, closing: r0(closing) }

  const investorBuy = arv * (pct / 100)
  const mao = investorBuy - rep.total - closing - fee
  const offer = num(x.offer) != null ? num(x.offer) : Math.max(0, Math.floor(mao / 500) * 500)
  const hold = (num(x.holdMonths) || 0) * (num(x.holdMonthly) || 0)
  const selling = arv * ((num(x.sellingPct) || 0) / 100)
  const allIn = offer + fee + rep.total + closing + hold
  const profit = arv - allIn - selling
  const asking = num(x.asking)
  const gap = asking == null ? null : asking - mao // >0: seller wants more than your max

  let verdict
  let tone
  if (mao <= 0) {
    verdict = 'No deal at these numbers - repairs, costs and your fee eat the whole spread.'
    tone = 'bad'
  } else if (offer > mao) {
    verdict = `Your offer is ${money(offer - mao)} over your max. The investor's margin shrinks - lower the offer or your fee.`
    tone = 'bad'
  } else if (asking != null && gap > 0) {
    verdict = `The seller wants ${money(gap)} more than your max. Negotiate the price down, take a smaller fee, or pass.`
    tone = 'warn'
  } else if (asking != null) {
    verdict = `Works: the seller's price is ${money(-gap)} under your max.`
    tone = 'good'
  } else {
    verdict = `Offer the seller up to ${money(mao)}.`
    tone = 'good'
  }
  return {
    ready: true, arv, repairs: rep, closing: r0(closing), fee: r0(fee), investorBuy: r0(investorBuy),
    mao: r0(mao), offer: r0(offer), hold: r0(hold), selling: r0(selling), allIn: r0(allIn),
    profit: r0(profit), profitPct: allIn > 0 ? Math.round((profit / allIn) * 1000) / 10 : null,
    asking, gap: r0(gap), verdict, tone,
  }
}

/** A plain-text summary to paste into a text or email. */
export function summary(res, address) {
  if (!res.ready) return `Deal check${address ? ` - ${address}` : ''}: missing ${res.missing.join(', ')}.`
  return [
    `Deal check${address ? ` - ${address}` : ''}`,
    `ARV ${money(res.arv)} | Repairs ${money(res.repairs.total)} | Closing ${money(res.closing)} | Fee ${money(res.fee)}`,
    `Max offer to seller: ${money(res.mao)}${res.asking != null ? ` (seller asking ${money(res.asking)})` : ''}`,
    `At ${money(res.offer)}: investor all-in ${money(res.allIn)}, est. profit ${money(res.profit)}${res.profitPct != null ? ` (${res.profitPct}%)` : ''}`,
    res.verdict,
  ].join('\n')
}
