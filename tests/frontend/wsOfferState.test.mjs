/**
 * Pure proof for offer comparison / buyer-match truth (no DOM, no React):
 *   node tests/frontend/wsOfferState.test.mjs
 */
import {
  classifyAmount, amountText, offerStatusText, sortOffers, offerPosition, isLive,
  fundsTruth, matchEvidence, sortMatches, currentBuyer,
} from '../../frontend/src/pages/wholesale/wsOfferState.js'

let passed = 0
let failed = 0
function check(name, cond) {
  if (cond) passed += 1
  else { failed += 1; console.log('FAIL:', name) }
}

check('null is missing', classifyAmount(null).kind === 'missing')
check('empty string is missing', classifyAmount('').kind === 'missing')
check('zero is zero, not missing', classifyAmount(0).kind === 'zero')
check('"0" is zero', classifyAmount('0').kind === 'zero')
check('negative is invalid', classifyAmount(-5).kind === 'invalid')
check('text is invalid', classifyAmount('abc').kind === 'invalid')
check('boolean is invalid', classifyAmount(true).kind === 'invalid')
check('real amount ok', classifyAmount('125000').kind === 'ok')
check('missing text never reads as $0', amountText(null) === 'No amount recorded')
check('invalid text says invalid', amountText('x') === 'Invalid amount')
check('zero prints $0', amountText(0) === '$0')
check('money formatted', amountText(125000) === '$125,000')

check('known status has words', offerStatusText('countered') === 'Countered')
check('unknown status keeps reference code',
  offerStatusText('weird_state') === 'Weird state (reference: weird_state)')
check('empty status explicit', offerStatusText(null) === 'No status recorded')
check('withdrawn is not live', !isLive({ status: 'withdrawn' }))
check('presented is live', isLive({ status: 'presented' }))

const offers = [
  { id: 3, direction: 'us', amount: 100, status: 'presented', created_at: '2026-01-03T00:00:00Z' },
  { id: 1, direction: 'us', amount: 90, status: 'draft', created_at: '2026-01-01T00:00:00Z' },
  { id: 2, direction: 'seller', amount: 150, status: 'countered', created_at: '2026-01-02T00:00:00Z' },
]
const sorted = sortOffers(offers)
check('offers sort by time', sorted.map((o) => o.id).join() === '1,2,3')
check('sort does not mutate input', offers[0].id === 3)
const tie = sortOffers([
  { id: 'b', created_at: '2026-01-01T00:00:00Z' }, { id: 'a', created_at: '2026-01-01T00:00:00Z' },
])
check('equal times tie-break by id', tie.map((o) => o.id).join() === 'a,b')
const undated = sortOffers([{ id: 1 }, { id: 2, created_at: '2026-01-01T00:00:00Z' }])
check('undated sorts last', undated[0].id === 2)

const pos = offerPosition(offers)
check('position finds our latest by time not array order', pos.ours.id === 3)
check('position finds their last', pos.theirs.id === 2)
check('gap computed from two usable numbers', pos.gap === 50)
check('gap note explains', pos.gapNote.includes('asking more'))
check('no offers → no gap and explicit note',
  offerPosition([]).gap === null && offerPosition([]).gapNote === 'needs a number from both sides')
const dead = offerPosition([
  { id: 1, direction: 'us', amount: 100, status: 'withdrawn', created_at: '2026-01-01' },
  { id: 2, direction: 'seller', amount: 150, status: 'countered', created_at: '2026-01-02' },
])
check('withdrawn offer of ours gives no gap', dead.gap === null && dead.gapNote.includes('no longer open'))
check('missing amount gives no gap',
  offerPosition([{ id: 1, direction: 'us', amount: null, created_at: '2026-01-01' },
                 { id: 2, direction: 'seller', amount: 5, created_at: '2026-01-02' }]).gap === null)
check('accepted offer surfaced',
  offerPosition([{ id: 1, direction: 'us', amount: 1, status: 'accepted', created_at: '2026-01-01' }]).accepted.id === 1)

check('funds true', fundsTruth({ proof_of_funds_on_file: true }).key === 'on_file')
check('funds false', fundsTruth({ proof_of_funds_on_file: false }).key === 'none')
check('funds missing is unknown, not "none"', fundsTruth({}).key === 'unknown')
check('no factors is insufficient evidence',
  !matchEvidence({ factors: [] }).enough && matchEvidence({}).text.startsWith('Not enough evidence'))
check('unscored factors are insufficient', !matchEvidence({ factors: [{ matched: null }] }).enough)
const ev = matchEvidence({ factors: [{ matched: true }, { matched: false }, { matched: null }] })
check('evidence counts scored only', ev.enough && ev.scored === 2 && ev.hit === 1)

const ms = sortMatches([
  { id: 1, buyer_name: 'Zed', score: 80 }, { id: 2, buyer_name: 'Amy', score: 80 },
  { id: 3, buyer_name: 'Bob', score: null }, { id: 4, buyer_name: 'Cy', score: 90 },
])
check('matches sort by score then name, missing last', ms.map((m) => m.id).join() === '4,2,1,3')
check('sort adds no recommendation flag', !ms.some((m) => 'recommended' in m || 'best' in m))

check('no assigned buyer', currentBuyer({ assigned_buyer_id: null }, []).state === 'none')
check('assigned buyer found',
  currentBuyer({ assigned_buyer_id: 7 }, [{ buyer_id: 7, buyer_name: 'Amy' }]).text === 'Selected buyer: Amy')
check('assigned buyer not in list says so',
  currentBuyer({ assigned_buyer_id: 9 }, []).state === 'selected_unlisted')

console.log(`wsOfferState: ${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
