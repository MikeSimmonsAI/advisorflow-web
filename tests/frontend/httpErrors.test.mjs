/**
 * AN ANSWERED REQUEST NEVER SAYS "UNREACHABLE".
 *
 * Runs the real classifier under plain node:
 *     node tests/frontend/httpErrors.test.mjs
 * and is run by tests/test_backend_availability.py as part of the suite.
 */
import { httpErrorKind, fallbackHttpMessage }
  from '../../frontend/src/api/httpErrors.js'

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) }
}
function eq(a, b, what) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error((what || 'value') + ': expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a))
  }
}
function has(s, sub, what) {
  if (!String(s).includes(sub)) throw new Error((what || 'message') + ' ' + JSON.stringify(s) + ' lacks ' + JSON.stringify(sub))
}

check('each status class is its own kind', () => {
  eq(httpErrorKind(401), 'auth'); eq(httpErrorKind(403), 'auth')
  eq(httpErrorKind(404), 'not_found')
  eq(httpErrorKind(400), 'validation'); eq(httpErrorKind(409), 'validation')
  eq(httpErrorKind(422), 'validation')
  eq(httpErrorKind(402), 'entitlement')
  eq(httpErrorKind(429), 'rate_limited')
  eq(httpErrorKind(500), 'server'); eq(httpErrorKind(502), 'server')
  eq(httpErrorKind(503), 'server'); eq(httpErrorKind(504), 'server')
  eq(httpErrorKind(418), 'http')
})

check('no answered status is ever described as unreachable', () => {
  for (const s of [400, 401, 402, 403, 404, 409, 418, 422, 429, 500, 502, 503, 504]) {
    const m = fallbackHttpMessage(s, undefined).toLowerCase()
    if (m.includes('unreachable') || m.includes('reach the server') || m.includes('connection')) {
      throw new Error(s + ' says: ' + m)
    }
  }
})

check('a 500 names itself as a server error, with the status', () => {
  const m = fallbackHttpMessage(500, undefined)
  has(m, 'server hit an error'); has(m, '500')
})

check("a 422's field list is surfaced, not thrown away", () => {
  const detail = [
    { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' },
    { loc: ['body', 'phone'], msg: 'field required', type: 'missing' },
  ]
  eq(fallbackHttpMessage(422, detail),
     'email: value is not a valid email address (and 1 more)')
})

check('a single field error reads cleanly', () => {
  eq(fallbackHttpMessage(422, [{ loc: ['query', 'limit'], msg: 'must be ≤ 500' }]),
     'limit: must be ≤ 500')
})

check('a refusal reads as a refusal', () => {
  has(fallbackHttpMessage(403, undefined), "don't have access")
  has(fallbackHttpMessage(404, undefined), 'could not be found')
  has(fallbackHttpMessage(402, undefined), 'not enabled')
})

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed\n')
  for (const f of failures) console.error('  FAIL  ' + f)
  process.exit(1)
}
console.log(passed + ' passed')
