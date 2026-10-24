/**
 * LANE-E #272 P4: the acquisition engine's name per workspace.
 *
 *   EvoSys / every workspace whose server payload has no `engine` key -> "EvoSense"
 *   a second Wholesale tenant (server sends engine: null)              -> null
 *
 * and the shell / page wording that depends on it. EvoSys text is unchanged.
 *
 *     node tests/frontend/bookaboostBranding.test.mjs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { engineName, engineLabel, productName } from '../../frontend/src/theme.js'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
let passed = 0
const failures = []
function check(name, fn) { try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) } }
function eq(a, b, what) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error((what || 'value') + ': expected ' + y + ', got ' + x)
}

const EVOSYS = { platform: { slug: 'evosyspro', products: { wholesale: 'EvoSys Wholesale' } } }
const FIBER = { platform: { slug: 'bookaboost', products: { wholesale: null } } }
const BAB = { platform: { slug: 'bookaboost', products: { wholesale: 'BookaBoost Wholesale', engine: null } } }

check('EvoSys keeps EvoSense', () => eq(engineName(EVOSYS), 'EvoSense'))
check('a payload with no engine key keeps EvoSense (other customers unchanged)', () => eq(engineName(FIBER), 'EvoSense'))
check('no branding at all keeps EvoSense', () => eq(engineName(null), 'EvoSense'))
check('a second tenant has no engine name', () => eq(engineName(BAB), null))
check('engineLabel picks the neutral wording only for the second tenant', () => {
  eq(engineLabel(EVOSYS, 'Acquisition · EvoSense', 'Acquisition'), 'Acquisition · EvoSense')
  eq(engineLabel(BAB, 'Acquisition · EvoSense', 'Acquisition'), 'Acquisition')
})
check('product names: BookaBoost Wholesale is its own product, EvoSys unchanged', () => {
  eq(productName(BAB, 'wholesale'), 'BookaBoost Wholesale')
  eq(productName(EVOSYS, 'wholesale'), 'EvoSys Wholesale')
  eq(productName(FIBER, 'wholesale'), null)
})

// The wiring: the shell and the two heroes route their engine text through the helpers.
const read = (p) => readFileSync(join(REPO, ...p.split('/')), 'utf8')
check('Layout passes the engine to the product line and the section label', () => {
  const s = read('frontend/src/components/Layout.jsx')
  if (!s.includes('const WHOLESALE_ENGINE = engineName(branding)')) throw new Error('no WHOLESALE_ENGINE')
  if ((s.match(/engine=\{WHOLESALE_ENGINE\}/g) || []).length !== 2) throw new Error('product line not wired twice')
  if ((s.match(/navSectionLabel\(group\.label\)/g) || []).length !== 3) throw new Error('section labels not wired')
})
check('WholesaleShell only says "Powered by" when there is an engine name', () => {
  const s = read('frontend/src/components/WholesaleShell.jsx')
  if (s.includes('Powered by EvoSense</span>')) throw new Error('hard-coded Powered by EvoSense remains')
  if (!s.includes("engine = 'EvoSense'")) throw new Error('default engine is not EvoSense')
})
check('every on-screen EvoSense / EvoSys Field string in the Wholesale pages goes through ev()', () => {
  const base = join(REPO, 'frontend', 'src', 'pages', 'wholesale')
  const walk = (d) => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    return statSync(p).isDirectory() ? walk(p) : (/\.(jsx|js)$/.test(n) ? [p] : [])
  })
  const skip = [join(base, 'engine.js'), join(base, 'ds', 'ds.jsx')]   // the helper; WORLDS is never rendered
  const bad = []
  for (const f of walk(base)) {
    if (skip.includes(f)) continue
    for (const line of readFileSync(f, 'utf8').split('\n')) {
      const t = line.trim()
      if (t.startsWith('*') || t.startsWith('//') || t.startsWith('/*')) continue
      if (/\.startsWith\('EvoSense/.test(t)) continue            // compares a server message; shows nothing
      if (/title: 'EvoSys Field'/.test(t)) continue                 // fieldApp.js constant, chosen per workspace below it
      if (/EvoSense|EvoSys Field/.test(t) && !/ev\('/.test(t)) bad.push(f.slice(base.length) + ': ' + t.slice(0, 90))
    }
  }
  if (bad.length) throw new Error('unwrapped:\n      ' + bad.join('\n      '))
})
check('the signer page names the sender brand, not EvoSys', () => {
  const s = read('frontend/src/pages/portal/SignDocument.jsx')
  if (/>EvoSys e-signature</.test(s) || />EVOSYS E-SIGNATURE</.test(s)) throw new Error('hard-coded EvoSys heading')
  if (!s.includes("d.brand || 'EvoSys'")) throw new Error('no brand from the payload')
})

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed')
  for (const f of failures) console.error('  ✕ ' + f)
  process.exit(1)
}
console.log(passed + ' passed')
