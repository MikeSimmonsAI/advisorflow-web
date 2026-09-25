/**
 * PERMANENT BRAND-RESOLUTION GATE (frontend half) - reusable, not module-specific.
 *
 * A screen that works but wears the wrong tenant/platform brand is a FAILED
 * acceptance test. This pins how the shell chooses its brand:
 *
 *   brand domain            -> the host decides        (source 'host')
 *   non-brand host (local)  -> the SIGNED-IN WORKSPACE's platform decides
 *                                                      (source 'workspace')
 *   neither says anything   -> the historical default  (source 'default' - a
 *                              FALLBACK, which scripts/review/brand_gate.py
 *                              fails on any authenticated screen)
 *
 *     node tests/frontend/brandResolution.test.mjs
 */
import { shellThemeSource, shellTheme, productName, THEMES } from '../../frontend/src/theme.js'

let passed = 0
const failures = []
function check(name, fn) { try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) } }
function eq(a, b, what) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error((what || 'value') + ': expected ' + y + ', got ' + x)
}
function at(hostname) { globalThis.window = { location: { hostname } } }

const EVOSYS = { organization_id: 'org-1', platform: { slug: 'evosyspro', theme: 'evosyspro',
  products: { wholesale: 'EvoSys Wholesale' } } }
const BOOKA = { organization_id: 'org-2', platform: { slug: 'bookaboost', theme: 'bookaboost',
  products: { wholesale: null } } }

check('localhost + EvoSysPro workspace resolves EvoSysPro FROM THE WORKSPACE', () => {
  at('localhost')
  eq(shellThemeSource(EVOSYS), { theme: THEMES.EVOSYSPRO, source: 'workspace' })
  eq(shellTheme(EVOSYS), THEMES.EVOSYSPRO)
})

check('127.0.0.1 behaves exactly like localhost', () => {
  at('127.0.0.1')
  eq(shellThemeSource(EVOSYS), { theme: THEMES.EVOSYSPRO, source: 'workspace' })
})

check('localhost never silently becomes BookaBoost for an EvoSysPro workspace', () => {
  at('localhost')
  if (shellTheme(EVOSYS) === THEMES.BOOKABOOST) throw new Error('fell back to BookaBoost')
})

check('no workspace platform on localhost is reported as a FALLBACK, not hidden', () => {
  at('localhost')
  eq(shellThemeSource({ organization_id: 'org-3', platform: null }).source, 'default')
  eq(shellThemeSource(null).source, 'default')
})

check('an unknown platform theme is a fallback, never another brand', () => {
  at('localhost')
  eq(shellThemeSource({ platform: { slug: 'x', theme: 'not-a-theme' } }).source, 'default')
})

check('a brand domain still decides its own chrome (white-label intact)', () => {
  at('app.evosyspro.live')
  eq(shellThemeSource(BOOKA), { theme: THEMES.EVOSYSPRO, source: 'host' })
  at('app.bookaboost.live')
  eq(shellThemeSource(EVOSYS), { theme: THEMES.BOOKABOOST, source: 'host' })
})

check('a BookaBoost workspace on localhost resolves BookaBoost from its own platform', () => {
  at('localhost')
  eq(shellThemeSource(BOOKA), { theme: THEMES.BOOKABOOST, source: 'workspace' })
})

check('the product name is the brand\'s own, or null - never borrowed', () => {
  eq(productName(EVOSYS, 'wholesale'), 'EvoSys Wholesale')
  eq(productName(BOOKA, 'wholesale'), null)
  eq(productName(null, 'wholesale'), null)
  eq(productName(EVOSYS, 'other'), null)
})

if (failures.length) {
  console.error(failures.map((f) => 'FAIL ' + f).join('\n'))
  console.error(`brandResolution: ${passed} passed, ${failures.length} failed`)
  process.exit(1)
}
console.log(`brandResolution: ${passed} passed`)
