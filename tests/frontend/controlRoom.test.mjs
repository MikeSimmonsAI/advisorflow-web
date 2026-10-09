// Control Room view helpers + wiring (read as text where behaviour needs a browser).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const V = await import('../../frontend/src/pages/god/controlRoomView.js')
const src = (p) => readFileSync(new URL('../../frontend/src/' + p, import.meta.url), 'utf8')
let n = 0
const ok = (c, m) => { assert.ok(c, m); n++ }
const eq = (a, b) => { assert.deepEqual(a, b); n++ }

// four visibly different states
const tones = ['Working', 'Complete', 'Blocked', 'Approval Needed'].map((s) => V.toneFor(s).fg)
eq(new Set(tones).size, 4)
eq(V.toneFor('???').key, 'idle')

// Needs Mike only for a true gate
ok(!V.showNeedsMike({ needs_mike: null }), 'no gate, no panel')
ok(!V.showNeedsMike({ state: { status: 'Blocked' }, needs_mike: null }), 'blocked is not needs-mike')
ok(V.showNeedsMike({ needs_mike: { decision: 'Authorize batch' } }))

// timeline newest first, input not mutated
const evs = [{ key: 'a' }, { key: 'b' }]
eq(V.timelineNewestFirst(evs).map((e) => e.key), ['b', 'a'])
eq(evs.map((e) => e.key), ['a', 'b'])

// duplicate submission guard
const g = V.makeSubmitGuard()
ok(g.begin('Fix the dashboard', 'next_priority'))
ok(!g.begin('fix  the dashboard', 'next_priority'), 'in-flight / same text refused')
g.end(true)
ok(!g.begin('Fix the dashboard', 'next_priority'), 'same text after success refused')
ok(g.begin('Fix the dashboard', 'after_current'), 'different mode allowed')
g.end(false)
ok(g.begin('Fix the dashboard', 'after_current'), 'failed send may be retried')
ok(!V.makeSubmitGuard().begin('   ', 'next_priority'), 'empty refused')

// missing credential -> SETUP REQUIRED state, composer disabled
ok(V.directionDisabledReason({ give_direction: { setup_required: true, message: 'SETUP REQUIRED: x' } }).startsWith('SETUP REQUIRED'))
eq(V.directionDisabledReason({ give_direction: { setup_required: false } }), null)

// wiring: route, nav, page contents, no dark-only styling, mobile breakpoint, no tokens in browser code
const app = src('App.jsx'), shell = src('pages/GodShell.jsx'), page = src('pages/god/ControlRoom.jsx')
ok(app.includes('path="/god/control-room"') && app.indexOf('/god/control-room') < app.indexOf('path="/god/*"'))
ok(shell.includes("path: '/god/control-room'"))
ok(page.includes('@media (max-width: 800px)'), 'responsive')
ok(page.includes('Technical details') && page.includes('Refresh now') && page.includes('POLL_MS'))
ok(!/GITHUB|ghp_|Bearer/.test(page), 'no credential handling in the browser')
ok(page.includes("'/god/relay/direction'") && !page.includes('RELAY:DIRECTIVE'), 'never builds a directive')

// ── Completed Work filters ──
const items = [{ id: 1, today: true, last7: true }, { id: 2, today: false, last7: true }, { id: 3, today: false, last7: false }]
eq(V.filterCompleted(items, 'today').map((i) => i.id), [1])
eq(V.filterCompleted(items, '7d').map((i) => i.id), [1, 2])
eq(V.filterCompleted(items, 'all').map((i) => i.id), [1, 2, 3])
eq(V.COMPLETED_FILTERS.map((f) => f.label), ['Today', '7 days', 'All'])
eq(V.filterCompleted(undefined, 'all'), [])

// ── Suggested Next: dismiss is local, needs-Mike cannot be queued ──
const mem = () => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v } } }
const st = mem()
eq(V.loadDismissed(st), [])
V.dismissSuggestion(st, 'a'); V.dismissSuggestion(st, 'a'); V.dismissSuggestion(st, 'b')
eq(V.loadDismissed(st), ['a', 'b'])
eq(V.visibleSuggestions([{ id: 'a' }, { id: 'c' }], V.loadDismissed(st)).map((s) => s.id), ['c'])
eq(V.suggestionActions({ needs_mike: true }, false), { canPackage: false, canQueue: false, canDismiss: true })
ok(!V.suggestionActions({}, true).canQueue, 'queue needs the write credential')
ok(V.suggestionActions({}, true).canPackage, 'drafting a package needs no credential')
eq(V.loadDismissed({ getItem: () => '{bad json' }), [])

// ── Overnight package: ordering, removal, state, no invented time ──
let p = V.renamePackage(V.emptyPackage(), 'Night run')
eq(V.packageState(p), 'Draft')
p = V.addObjective(p, 'First'); p = V.addObjective(p, 'Second', 'suggested'); p = V.addObjective(p, 'Third')
eq(p.objectives.map((o) => o.text), ['First', 'Second', 'Third'])
eq(V.packageState(p), 'Ready')
eq(V.addObjective(p, 'first').objectives.length, 3)                 // duplicate ignored
eq(V.addObjective(p, '   ').objectives.length, 3)                   // empty ignored
eq(V.moveObjective(p, 2, -1).objectives.map((o) => o.text), ['First', 'Third', 'Second'])
eq(V.moveObjective(p, 0, -1), p)                                    // edge: unchanged
eq(V.moveObjective(p, 2, 1), p)
eq(V.removeObjective(p, 1).objectives.map((o) => o.text), ['First', 'Third'])
eq(p.objectives.length, 3)                                          // pure
eq(V.objectivesForServer(V.moveObjective(p, 0, 1)), ['Second', 'First', 'Third'])
ok(V.canStartPackage(p, null) && !V.canStartPackage(p, 'SETUP REQUIRED') && !V.canStartPackage(V.emptyPackage(), null))
for (const s of ['Running', 'Completed', 'Blocked', 'Needs Mike']) eq(V.packageState(p, s), s)
eq(V.packageState(p, 'None'), 'Ready')
let full = V.emptyPackage(); for (let i = 0; i < 20; i++) full = V.addObjective(full, 'o' + i)
eq(full.objectives.length, V.MAX_OBJECTIVES)
const seq = V.packageSequence(p, { items: [{ gate: false }, { gate: true, gate_reasons: ['production'] }, { gate: false }] })
eq(seq.map((s) => [s.step, s.gate]), [[1, false], [2, true], [3, false]])
ok(!JSON.stringify(seq).match(/minute|hour|ETA|eta/), 'sequence invents no time claims')
const store = mem(); V.savePackageDraft(store, p)
eq(V.loadPackageDraft(store).objectives.length, 3)
eq(V.loadPackageDraft(mem()), V.emptyPackage())

// ── Home summary / dashboard counts ──
const cards = V.homeCards({
  working_now: { text: 'Build X' }, completed_today: { count: 3, latest: 'Y' }, suggested_next: { count: 2, top: 'Z' },
  overnight: { status: 'Running', name: 'Night' }, needs_mike: { count: 1, decision: 'Approve?' } })
eq(cards.map((c) => c.label), ['Working now', 'Completed today', 'Suggested next', 'Overnight package', 'Needs Mike'])
eq(cards.map((c) => c.value), ['Build X', '3', '2', 'Running', '1'])
eq(cards[4].tone, 'needs')
eq(V.homeCards({}).map((c) => c.value), ['Nothing running', '0', '0', 'No package', '0'])

// ── Monitoring setup states ──
eq(V.monitoringView({ state: 'ok' }), null)
eq(V.monitoringView(null), null)
const setupView = V.monitoringView({ state: 'setup_required', message: 'SETUP REQUIRED: x', read_credential_configured: false })
ok(setupView.kind === 'setup' && setupView.title.startsWith('Setup required') && !setupView.tokenSet)
eq(V.monitoringView({ state: 'degraded', message: 'm' }).kind, 'degraded')

// ── Staging banner ──
ok(V.stagingBanner({ environment: 'staging' }).label === 'STAGING / QA')
for (const e of [{ environment: 'production' }, { environment: 'demo' }, null, undefined, {}]) eq(V.stagingBanner(e), null)

// ── Wiring (source level; no browser available) ──
const banner = src('components/StagingBanner.jsx')
ok(banner.includes('fetchEnvironment') && banner.includes('stagingBanner') && !/hostname|location/.test(banner), 'banner asks the backend, not the URL')
ok(/import StagingBanner/.test(app) && app.indexOf('<StagingBanner />') > app.indexOf('<DemoBanner />') && app.indexOf('<StagingBanner />') < app.indexOf('<Routes>'), 'banner sits above every route')
for (const t of ['completed-work', 'suggested-next', 'overnight-package', 'morning-summary', 'monitoring-notice', 'package-safety'])
  ok(page.includes(`data-testid="${t}"`), 'section ' + t)
ok(page.includes("'/god/relay/package'") && page.includes("'/god/relay/package/review'"))
ok(!page.includes('RELAY:DIRECTIVE') && !/ghp_|github_pat|Authorization/.test(page + banner), 'no directive or credential in browser code')
ok(page.includes('Start Overnight Package') && page.includes('never to Claude') && page.includes('Give Direction stays available'))
ok(page.includes('Add to overnight package') && page.includes('Add to queue') && page.includes('Dismiss'))
ok(!/setInterval\([^)]*queueSuggestion|useEffect\([^)]*queueSuggestion/.test(page), 'suggestions never execute on their own')
// Issue #21: the first screen is the evidence-only relay panel; no "Completed today"
// home summary (it counted blocked/deferred runs) and no "Suggested next" heading.
ok(page.includes("import GodRelayControlRoom from './GodRelayControlRoom'") && page.includes('<GodRelayControlRoom onManualRefresh='), 'relay panel first')
ok(!page.includes('data-testid="home-summary"') && !page.includes('Suggested next') && !page.includes('Status updated from the relay'), 'no misleading summary or false success')
console.log(`controlRoom: ${n} passed`)
