/* Pure offer-comparison and buyer-match truth — no React, no DOM, no imports —
 * so `node tests/frontend/wsOfferState.test.mjs` runs it.
 *
 *   1. AMOUNTS. Missing, invalid, zero and real are four different things. A
 *      missing amount never reads as $0, and a negative or non-numeric one is
 *      called invalid rather than formatted.
 *   2. STATUS WORDS. A known status gets operator words; an unknown one is shown
 *      as words plus "(reference: code)", never the bare enum.
 *   3. ORDER. Offers sort by recorded time then id, so ties are deterministic
 *      and the "last" offer never depends on server row order.
 *   4. POSITION. A withdrawn/expired/rejected offer of ours is not our standing
 *      position; the gap needs two usable numbers.
 *   5. MATCH TRUTH. Proof of funds is on file / not on file / unknown; a match
 *      with no scored factors says "not enough evidence", not a percentage's
 *      worth of confidence. Ties sort by name then id; nothing is recommended.
 */

export const US = 'us'
export const SELLER = 'seller'

export const OFFER_STATUS_LABEL = {
  draft: 'Draft — written down, not presented',
  approval_pending: 'Waiting on an approval',
  approved: 'Approved internally',
  presented: 'Presented to the seller',
  countered: 'Countered',
  accepted: 'Accepted',
  rejected: 'Rejected',
  expired: 'Expired',
  withdrawn: 'Withdrawn',
}

/* Statuses after which an offer is no longer a live position. */
const DEAD = ['rejected', 'expired', 'withdrawn']

export function classifyAmount(value) {
  if (value === null || value === undefined || value === '') return { kind: 'missing', n: null }
  if (typeof value === 'boolean') return { kind: 'invalid', n: null }
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return { kind: 'invalid', n: null }
  return { kind: n === 0 ? 'zero' : 'ok', n }
}

export function amountText(value) {
  const c = classifyAmount(value)
  if (c.kind === 'missing') return 'No amount recorded'
  if (c.kind === 'invalid') return 'Invalid amount'
  return '$' + c.n.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

export function offerStatusText(status) {
  if (status === null || status === undefined || status === '') return 'No status recorded'
  const key = String(status)
  if (OFFER_STATUS_LABEL[key]) return OFFER_STATUS_LABEL[key]
  const words = key.replace(/[_-]+/g, ' ').trim()
  return `${words.charAt(0).toUpperCase()}${words.slice(1)} (reference: ${key})`
}

export function isLive(offer) {
  return !!offer && !DEAD.includes(offer.status)
}

function time(o) {
  const t = Date.parse(o && o.created_at)
  return Number.isNaN(t) ? Infinity : t   // undated rows sort last, deterministically
}

export function sortOffers(offers) {
  return [...(offers || [])].sort((a, b) => {
    const d = time(a) - time(b)
    if (d !== 0 && !Number.isNaN(d)) return d
    return String(a.id).localeCompare(String(b.id), 'en', { numeric: true })
  })
}

export function offerPosition(offers) {
  const sorted = sortOffers(offers)
  const last = (dir) => [...sorted].reverse().find((o) => o.direction === dir) || null
  const ours = last(US)
  const theirs = last(SELLER)
  const a = ours ? classifyAmount(ours.amount) : { kind: 'missing' }
  const b = theirs ? classifyAmount(theirs.amount) : { kind: 'missing' }
  const usable = (c) => c.kind === 'ok' || c.kind === 'zero'
  let gap = null
  let gapNote = 'needs a number from both sides'
  if (ours && theirs && usable(a) && usable(b)) {
    if (!isLive(ours)) gapNote = 'our last offer is no longer open'
    else { gap = b.n - a.n; gapNote = gap > 0 ? 'they are asking more than we offered'
                                              : 'their number is at or below ours' }
  }
  const accepted = sorted.filter((o) => o.status === 'accepted')
  return { ours, theirs, gap, gapNote, accepted: accepted[accepted.length - 1] || null,
           oursLive: ours ? isLive(ours) : false }
}

/* "proof of funds": three states, never two. */
export function fundsTruth(m) {
  const v = m ? m.proof_of_funds_on_file : undefined
  if (v === true) return { key: 'on_file', label: 'Funds on file' }
  if (v === false) return { key: 'none', label: 'No funds on file' }
  return { key: 'unknown', label: 'Funds not checked' }
}

export function matchEvidence(m) {
  const factors = (m && Array.isArray(m.factors)) ? m.factors : []
  const scored = factors.filter((f) => f && (f.matched === true || f.matched === false))
  const hit = scored.filter((f) => f.matched === true).length
  if (!scored.length) {
    return { enough: false, scored: 0, hit: 0,
             text: 'Not enough evidence — no buy-box criteria were scored.' }
  }
  return { enough: true, scored: scored.length, hit,
           text: `${hit} of ${scored.length} scored criteria matched.` }
}

/* Highest score first; ties by name then id. Missing scores sort last and are
 * not turned into zero. No "best" flag is produced. */
export function sortMatches(matches) {
  const s = (m) => (typeof m.score === 'number' && Number.isFinite(m.score)) ? m.score : -Infinity
  return [...(matches || [])].sort((a, b) => {
    if (s(a) !== s(b)) return s(b) > s(a) ? 1 : -1
    const n = String(a.buyer_name || '').localeCompare(String(b.buyer_name || ''), 'en')
    return n !== 0 ? n : String(a.id).localeCompare(String(b.id), 'en', { numeric: true })
  })
}

/* Who the deal is currently with. Only what the payload states. */
export function currentBuyer(deal, matches) {
  const id = deal && (deal.assigned_buyer_id)
  if (id === null || id === undefined || id === '') return { state: 'none', text: 'No buyer selected' }
  const m = (matches || []).find((x) => String(x.buyer_id) === String(id))
  return m ? { state: 'selected', id, text: `Selected buyer: ${m.buyer_name || 'unnamed'}`, match: m }
           : { state: 'selected_unlisted', id, text: 'A buyer is selected but is not in the scored list.' }
}
