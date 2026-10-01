// Overview KPI grammar (frontend/src/terminology.js): a Title Case business noun
// reads lower case mid-sentence. "Service Calls" used to render as
// "service Calls this week". terminology.js imports React and the API client,
// so the three pure functions are lifted out of the source and evaluated alone.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../../frontend/src/terminology.js', import.meta.url), 'utf8')
function grab(name) {
  const start = src.search(new RegExp('^(export )?function ' + name + '\\(', 'm'))
  assert.ok(start >= 0, name + ' not found')
  const end = src.indexOf('\n}', start)
  return src.slice(start, end + 2).replace(/^export /, '')
}
const NEUTRAL_VOCABULARY = { appointments: 'Appointments' }
// eslint-disable-next-line no-new-func
const mod = new Function('NEUTRAL_VOCABULARY',
  grab('singular') + '\n' + grab('lower') + '\n' + grab('metricLabels') +
  '\nreturn { singular, lower, metricLabels }')(NEUTRAL_VOCABULARY)

assert.equal(mod.lower('Service Calls'), 'service calls')
assert.equal(mod.lower('Appointments'), 'appointments')
assert.equal(mod.lower('Rate Reviews'), 'rate reviews')
assert.equal(mod.lower('HVAC Visits'), 'HVAC visits')
assert.equal(mod.lower('iPhone Repairs'), 'iPhone repairs')
assert.equal(mod.lower(''), '')

const L = mod.metricLabels('Service Calls')
assert.equal(L.weeklyLabel, 'service calls this week')
assert.equal(L.bookedSub, 'Booked service calls')
assert.equal(L.appointments, 'Service Calls')
assert.equal(L.bookingRate, 'Service Call rate')
assert.equal(mod.metricLabels().weeklyLabel, 'appointments this week')

console.log('metricLabels ok')
