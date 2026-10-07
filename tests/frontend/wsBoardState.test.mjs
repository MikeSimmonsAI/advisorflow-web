/**
 * Pure state/contract proof for the buyer board (SYNTHETIC LOGIC; no DOM, no
 * React, no framework):  node tests/frontend/wsBoardState.test.mjs
 */
import fs from 'node:fs'
import {
  initialBoardState, loadStarted, loadSucceeded, loadFailed, boardView,
  createInFlight, parseRefusal, describeRefusal, resendOutcome, selectOutcome,
  allowedResponseStatuses, defaultResponseStatus, selectDisabledReason,
  currentSelection, SELECTION_REASONS, SEND_REASONS,
} from '../../frontend/src/pages/wholesale/wsBoardState.js'

let passed = 0
let failed = 0
function check(name, cond) {
  if (cond) passed += 1
  else { failed += 1; console.log('FAIL:', name) }
}

// ── load ordering ─────────────────────────────────────────────────────────
let s = initialBoardState()
check('initial view is loading', boardView(s) === 'loading')
s = loadStarted(s)
const g1 = s.latest
s = loadSucceeded(s, g1, { buyers: [{ outreach_id: 'a' }] })
check('first load ready', boardView(s) === 'ready' && s.board.buyers.length === 1)

// older request slower than newer one
s = loadStarted(s); const older = s.latest
check('refresh keeps rows and flags refreshing', s.refreshing && s.board !== null)
s = loadStarted(s); const newer = s.latest
s = loadSucceeded(s, newer, { buyers: [{ outreach_id: 'b' }] })
s = loadSucceeded(s, older, { buyers: [{ outreach_id: 'STALE' }] })
check('stale success cannot overwrite newer board', s.board.buyers[0].outreach_id === 'b')
s = loadFailed(s, older, 'old failure')
check('stale failure cannot set error', s.error === null)

// failed refresh retains rows
s = loadStarted(s); const failGen = s.latest
s = loadFailed(s, failGen, 'Network down')
check('failed refresh keeps rows and is stale', boardView(s) === 'stale' && s.board.buyers.length === 1 && s.error === 'Network down')
s = loadStarted(s); s = loadSucceeded(s, s.latest, { buyers: [] })
check('retry recovery clears the error', boardView(s) === 'ready' && s.error === null && !s.refreshing)

// first load failing is blocking
let f = loadStarted(initialBoardState())
f = loadFailed(f, f.latest, '')
check('first-load failure is an error view with a message', boardView(f) === 'error' && f.error.length > 0)
check('failed-first then retry succeeds', boardView(loadSucceeded(loadStarted(f), f.latest + 1, { buyers: [] })) === 'ready')

// ── in-flight latch ───────────────────────────────────────────────────────
const latch = createInFlight()
let runs = 0
let release
const gate = new Promise((r) => { release = r })
const first = latch.run(async () => { runs += 1; await gate; return 'done' })
const second = await latch.run(async () => { runs += 1; return 'dup' })
check('second click while pending is refused', second.ran === false && runs === 1 && latch.held)
release()
const r1 = await first
check('first completes and releases', r1.ran && r1.value === 'done' && !latch.held)
const thrower = await latch.run(async () => 1).then(() => latch.run(async () => { throw new Error('x') }).catch(() => 'threw'))
check('throw propagates and still releases', thrower === 'threw' && !latch.held)
check('latch reusable after release', (await latch.run(async () => 7)).value === 7)

// ── reason codes ──────────────────────────────────────────────────────────
const p = parseRefusal('That buyer passed on this deal. Record a new response from them first. [buyer_passed]')
check('parseRefusal splits label and code', p.code === 'buyer_passed' && !p.label.includes('['))
check('parseRefusal plain message has no code', parseRefusal('Boom').code === null)
check('describeRefusal keeps the stable code', describeRefusal('Locked. [selected_row_is_locked]').endsWith('(reference: selected_row_is_locked)'))
check('describeRefusal falls back to table words for code-only', describeRefusal('[buyer_opted_out]').startsWith('That buyer has opted out'))
check('describeRefusal never returns a bare code', !/^\[?[a-z_]+\]?$/.test(describeRefusal('[buyer_inactive]')))
for (const code of Object.keys(SELECTION_REASONS)) {
  check(`selection reason ${code} has words`, describeRefusal(`[${code}]`).includes('(reference: ' + code + ')') && SELECTION_REASONS[code].length > 10)
}
check('resend ok', resendOutcome({ sent: true }).ok)
const cool = resendOutcome({ sent: false, code: 'recently_sent', reason: 'raw server' })
check('cooldown uses operator words + code', !cool.ok && cool.code === 'recently_sent' && cool.message.includes('Wait a minute') && cool.message.includes('recently_sent'))
const unknown = resendOutcome({ sent: false, code: 'weird', reason: 'The provider said no.' })
check('unknown code falls back to the server reason', unknown.message.startsWith('The provider said no.'))
check('resend with nothing is still words', resendOutcome(null).message.length > 5)
for (const code of Object.keys(SEND_REASONS)) check(`send reason ${code} not raw`, !/^[a-z_]+$/.test(SEND_REASONS[code]))

// ── select outcomes ───────────────────────────────────────────────────────
const rows = [{ outreach_id: 'a', is_selected: true }, { outreach_id: 'b' }]
check('select replacing says released', selectOutcome({}, rows, 'b').message.includes('released'))
check('first select is plain', selectOutcome({}, [{ outreach_id: 'b' }], 'b').message === 'Buyer selected.')
const again = selectOutcome({ already_selected: true }, rows, 'a')
check('already-selected is an unchanged no-op', again.changed === false && again.message.includes('Nothing changed'))
check('re-selecting self is not "released"', !selectOutcome({}, rows, 'a').message.includes('released'))

// ── per-row rules mirror the server ───────────────────────────────────────
const sel = { is_selected: true, status: 'selected' }
check('selected row cannot be set to selected', !allowedResponseStatuses(sel).includes('selected'))
check('no row may be set to selected via the response form', !allowedResponseStatuses({}).includes('selected'))
check('selected row cannot be demoted to replied/sent', ['replied', 'sent', 'interested', 'no_response'].every((x) => !allowedResponseStatuses(sel).includes(x)))
check('selected row may back out or offer', ['passed', 'rejected', 'offer_submitted'].every((x) => allowedResponseStatuses(sel).includes(x)))
check('selected default is offer_submitted', defaultResponseStatus(sel) === 'offer_submitted')
check('plain default is replied', defaultResponseStatus({ status: 'sent' }) === 'sent' && defaultResponseStatus({ status: 'bogus' }) === 'replied')
check('select blocked for passed/rejected/opted-out', ['passed', 'rejected'].every((st) => selectDisabledReason({ status: st })) && selectDisabledReason({ do_not_contact: true }))
check('select allowed for interested', selectDisabledReason({ status: 'interested' }) === null)
check('currentSelection prefers status-consistent row', currentSelection([{ is_selected: true, status: 'offer_submitted', o: 1 }, { is_selected: true, status: 'selected', o: 2 }]).o === 2)
check('currentSelection none', currentSelection([{}]) === null)

// ── source contracts (static) ─────────────────────────────────────────────
const board = fs.readFileSync(new URL('../../frontend/src/pages/wholesale/wsBuyerBoard.jsx', import.meta.url), 'utf8')
check('board imports the pure module', board.includes("from './wsBoardState'"))
check('board never posts status "selected"', !/status:\s*'selected'/.test(board))
check('board mutations go through the latch', board.includes('inFlight.current.run'))
check('board loads with generations', board.includes('loadSucceeded(s, gen, data)') && board.includes('loadFailed(s, gen'))
check('Escape closes the confirmation and response form', (board.match(/e\.key === 'Escape'/g) || []).length >= 2)
check('focus returns after close', board.includes('selectTrigger.current') && board.includes('respondRef.current.focus()'))

console.log(`wsBoardState: ${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
