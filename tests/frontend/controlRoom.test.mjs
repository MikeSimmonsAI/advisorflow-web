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
console.log(`controlRoom: ${n} passed`)
