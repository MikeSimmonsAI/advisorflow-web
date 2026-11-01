/* OLP land & farm calculator math - pure functions, unit-tested.
 * Only numbers a person enters are used; a missing input gives null. */
export const num = (v) => {
  if (v === '' || v === null || v === undefined) return null
  const n = Number(String(v).replace(/[$,%\s]/g, ''))
  return Number.isFinite(n) ? n : null
}

/** Monthly payment for a seller-financed note. Pure. */
export function monthlyPayment(principal, ratePct, months) {
  if (!(principal > 0) || !(months > 0)) return null
  const r = (ratePct || 0) / 100 / 12
  return r === 0 ? principal / months : (principal * r) / (1 - Math.pow(1 + r, -months))
}

/** The land deal, from the numbers entered. Pure; null where an input is missing. */
export function landDeal(i) {
  const acres = num(i.acres), ppa = num(i.ppa), pct = num(i.offerPct), closing = num(i.closing) || 0
  const sale = num(i.sale), down = num(i.down) || 0, rate = num(i.rate), years = num(i.years)
  const market = acres != null && ppa != null ? acres * ppa : null
  const offer = num(i.offer) != null ? num(i.offer) : (market != null && pct != null ? market * pct / 100 : null)
  const allIn = offer != null ? offer + closing : null
  const cashProfit = sale != null && allIn != null ? sale - allIn : null
  const financed = sale != null ? Math.max(sale - down, 0) : null
  const months = years != null ? Math.round(years * 12) : null
  const pay = financed != null && months ? monthlyPayment(financed, rate, months) : null
  const collected = pay != null ? down + pay * months : null
  const notesProfit = collected != null && allIn != null ? collected - allIn : null
  let payback = null
  if (pay != null && allIn != null) payback = down >= allIn ? 0 : Math.ceil((allIn - down) / pay)
  return { market, offer, offerPerAcre: offer != null && acres ? offer / acres : null, allIn, cashProfit,
           financed, months, pay, collected, notesProfit, payback: payback != null && months && payback > months ? null : payback }
}

/** Farm income, the same method the property page uses on the server. Pure. */
export function farmIncome(i) {
  const pairs = [['crop', 'cropRent'], ['pasture', 'pastureRent']].filter(([a, r]) => num(i[a]) != null && num(i[r]) != null)
  const flat = ['hunting', 'otherIncome'].filter((k) => num(i[k]) != null)
  if (!pairs.length && !flat.length) return { income: null }
  const income = pairs.reduce((s, [a, r]) => s + num(i[a]) * num(i[r]), 0) + flat.reduce((s, k) => s + num(i[k]), 0)
  const costs = ['taxes', 'insurance', 'upkeep'].filter((k) => num(i[k]) != null).reduce((s, k) => s + num(i[k]), 0)
  const net = income - costs
  const cap = num(i.cap), price = num(i.price)
  const acres = ['crop', 'pasture', 'timber', 'otherAcres'].reduce((s, k) => s + (num(i[k]) || 0), 0)
  return { income, costs, net, value: cap && net > 0 ? net / (cap / 100) : null,
           ret: price ? (net / price) * 100 : null, acres: acres || null,
           perAcre: acres && price ? price / acres : null, netPerAcre: acres ? net / acres : null }
}
