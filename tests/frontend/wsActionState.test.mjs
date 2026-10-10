/**
 * Pure proof for deal-room action scoping and load ordering (no DOM, no React):
 *   node tests/frontend/wsActionState.test.mjs
 */
import fs from 'node:fs'
import {
  createActionTracker, panelOf, setOutcome, clearOutcome, outcomesFor, describeError,
} from '../../frontend/src/pages/wholesale/wsActionState.js'

let passed = 0
let failed = 0
function check(name, cond) {
  if (cond) passed += 1
  else { failed += 1; console.log('FAIL:', name) }
}

const t = createActionTracker()
check('panelOf splits key', panelOf('seller:send') === 'seller')
check('panelOf bare key', panelOf('stage') === 'stage')
check('panelOf empty is page', panelOf('') === 'page')
check('begin claims', t.begin('seller:send') === true)
check('duplicate same key refused synchronously', t.begin('seller:send') === false)
check('same panel different action allowed by tracker', t.begin('seller:save') === true)
check('panel pending true for seller', t.panelPending('seller'))
check('unrelated panel not pending', !t.panelPending('documents'))
check('unrelated action claimable', t.begin('documents:upload') === true)
t.end('seller:send'); t.end('seller:save')
check('panel clears after ends', !t.panelPending('seller'))
check('documents still pending', t.panelPending('documents'))
t.end('documents:upload')
check('re-claim after end', t.begin('seller:send') === true)
t.end('seller:send')
const u = createActionTracker()
u.begin('seller:x')
check('prefix collision: sell is not seller', !u.panelPending('sell'))

// load ordering
const g1 = t.nextLoad()
const g2 = t.nextLoad()
check('older load is stale', !t.isCurrentLoad(g1))
check('newest load is current', t.isCurrentLoad(g2))

// outcomes per panel
let o = {}
o = setOutcome(o, 'seller', 'error', 'bad')
o = setOutcome(o, 'documents', 'ok', 'saved')
check('seller keeps error after documents success', o.seller.kind === 'error')
check('outcomesFor only active panel', outcomesFor(o, 'documents').length === 1
  && outcomesFor(o, 'documents')[0].text === 'saved')
o = setOutcome(o, 'page', 'error', 'load failed')
check('page outcome shows everywhere', outcomesFor(o, 'documents').length === 2)
check('clear removes only that panel', !('seller' in clearOutcome(o, 'seller'))
  && 'documents' in clearOutcome(o, 'seller'))
check('clear missing returns same object', clearOutcome(o, 'nope') === o)

// operator words
check('code kept as reference', describeError('Buyer passed. [buyer_passed]')
  === 'Buyer passed. (reference: buyer_passed)')
check('bare code never alone', describeError('[buyer_passed]')
  === 'Something went wrong. (reference: buyer_passed)')
check('plain message untouched', describeError('Nope') === 'Nope')
check('empty message fallback', describeError(null) === 'Something went wrong.')

// source wiring: the deal room really uses the helper
const src = fs.readFileSync(
  new URL('../../frontend/src/pages/wholesale/WholesaleDeal.jsx', import.meta.url), 'utf8')
check('deal room imports tracker', src.includes("from './wsActionState'"))
check('no page-wide busy state', !src.includes('setBusy'))
check('stage select scoped', src.includes("disabled={panelBusy('stage')}"))
check('outcomes announced', src.includes('role="status"') && src.includes('role="alert"'))

console.log(`wsActionState: ${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
