/**
 * SHARED CALENDAR HELPERS, EXECUTED.   node tests/frontend/calendarState.test.mjs
 * Synthetic data only.
 */
import {
  assignLanes, compareAppts, defaultView, parseViewState, initialViewState,
  serializeViewState, loadViewState, saveViewState, CAL_STATE_KEY, ymd,
} from '../../frontend/src/pages/sales/calendarTime.js'

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) }
}
function eq(a, b) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error('expected ' + JSON.stringify(b) + ' got ' + JSON.stringify(a))
  }
}

check('same-time events sort by id regardless of input order', () => {
  const a = { id: 'a', starts_at: '2026-10-07T15:00:00' }
  const b = { id: 'b', starts_at: '2026-10-07T15:00:00' }
  const c = { id: 'c', starts_at: '2026-10-07T14:00:00' }
  eq([b, a, c].sort(compareAppts).map(x => x.id), ['c', 'a', 'b'])
  eq([a, c, b].sort(compareAppts).map(x => x.id), ['c', 'a', 'b'])
})

check('lane assignment is independent of input order for equal spans', () => {
  const mk = id => ({ appt: { id }, startMin: 600, endMin: 660 })
  const lanes = items => Object.fromEntries(
    assignLanes(items).map(i => [i.appt.id, i.lane]))
  eq(lanes([mk('x'), mk('y'), mk('z')]), lanes([mk('z'), mk('x'), mk('y')]))
  eq(lanes([mk('y'), mk('x')]), { x: 0, y: 1 })
})

check('narrow screens default to day, desktop to week', () => {
  eq(defaultView(390), 'day')
  eq(defaultView(1280), 'week')
  eq(defaultView(0), 'week')
})

check('saved view/day survive; invalid values are discarded', () => {
  const s = initialViewState(serializeViewState('agenda', new Date(2026, 9, 7, 12)), 390)
  eq(s.view, 'agenda')
  eq(ymd(s.anchor), '2026-10-07')
  eq(parseViewState('{"view":"bogus","day":"2026-13-45"}'), {})
  eq(parseViewState('not json'), {})
  eq(parseViewState(null), {})
  const d = initialViewState('{"view":"bogus"}', 390, new Date(2026, 9, 8, 12))
  eq(d.view, 'day')
  eq(ymd(d.anchor), '2026-10-08')
})

check('storage failures never throw', () => {
  const bad = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } }
  eq(loadViewState(bad), null)
  saveViewState('day', new Date(), bad)
  const mem = {}
  const ok = { getItem: k => mem[k] ?? null, setItem: (k, v) => { mem[k] = v } }
  saveViewState('month', new Date(2026, 9, 7, 12), ok)
  eq(JSON.parse(mem[CAL_STATE_KEY]), { view: 'month', day: '2026-10-07' })
})

if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}
console.log('calendarState: ' + passed + ' passed')
