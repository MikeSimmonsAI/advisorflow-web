// PROJECT OVERDRIVE Phase B (#439): the data provider cards and property intelligence.
// Run: node --test frontend/tests/providerNetwork.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as N from '../src/pages/wholesale/evosense/network/networkModel.js'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const cards = R('../src/pages/wholesale/evosense/network/ProviderNetwork.jsx')
const intel = R('../src/pages/wholesale/evosense/network/PropertyIntelligence.jsx')
const controls = R('../src/pages/wholesale/evosense/EvoControls.jsx')
const screen = R('../src/pages/wholesale/evosense/EvoProperty.jsx')
const layout = R('../src/components/Layout.jsx')
const backend = R('../../app/services/evosense/provider_network.py')

test('the eight owner-approved statuses, the same list the server uses', () => {
  assert.deepEqual(N.STATUSES, ['NOT CONFIGURED', 'READY TO TEST', 'CONNECTED', 'DEGRADED', 'RATE LIMITED',
    'IMPORT ONLY', 'UNAVAILABLE', 'ENTITLEMENT MISSING'])
  for (const s of N.STATUSES) assert.ok(backend.includes(`"${s}"`), s)
})

test('an unknown status is never shown as connected', () => {
  assert.equal(N.safeStatus('CONNECTED!!'), 'NOT CONFIGURED')
  assert.equal(N.safeStatus(undefined), 'NOT CONFIGURED')
  assert.equal(N.toneOf('weird'), 'quiet')
  assert.equal(N.toneOf('CONNECTED'), 'good')
  for (const s of N.STATUSES) assert.ok(N.MEANING[s], s)
})

test('Test Connection only where the server says a real test exists, and only for an admin', () => {
  assert.equal(N.canTest({ test: { available: true } }, true), true)
  assert.equal(N.canTest({ test: { available: true } }, false), false)
  assert.equal(N.canTest({ test: { available: false } }, true), false)
  assert.equal(N.canTest({ key: 'propstream' }, true), false)
  assert.match(cards, /card\.test && card\.test\.available \?/)
  assert.match(cards, /disabled=\{busy \|\| !isAdmin\}/)
})

test('usage and budget are worded from what was configured; "no limit" is said plainly', () => {
  assert.equal(N.usageLine({ usage: { calls: 1, cents: 25 } }), '1 charged request · $0.25 this month')
  assert.equal(N.usageLine({ usage: null }), null)
  const b = N.budgetLine({ key: 'attom', budget: { monthly_cents: null, daily_cents: null, remaining_cents: null, unit_cents: null } })
  assert.match(b, /no monthly limit set/); assert.match(b, /price per request not set/)
  assert.match(N.budgetLine({ key: 'tracerfy', budget: { monthly_cents: 5000, unit_cents: 10, lookups_daily_cap: 25 } }),
    /\$50\.00 a month.*\$0\.10 per owner found.*25 lookups a day max/)
})

test('a typed price becomes cents, or nothing when it is not a price', () => {
  assert.equal(N.centsFromDollars('0.25'), 25)
  assert.equal(N.centsFromDollars('$1,000'), 100000)
  assert.equal(N.centsFromDollars('0'), 0)
  for (const bad of ['', 'abc', '-1', '1.234', '1001']) assert.equal(N.centsFromDollars(bad), null, bad)
})

test('setup steps never print a setting name the server did not send', () => {
  const tenant = N.setupSteps({ key: 'attom', env: [] }).join(' ')
  assert.doesNotMatch(tenant, /ATTOM_API_KEY/)
  assert.match(tenant, /the platform credential/)
  const admin = N.setupSteps({ key: 'tracerfy', env: ['TRACERFY_API_TOKEN'] }).join(' ')
  assert.match(admin, /TRACERFY_API_TOKEN/)
  assert.match(N.setupSteps({ key: 'propstream' }).join(' '), /preview/i)
})

test('no credential value is read or rendered on the client', () => {
  for (const src of [cards, intel]) {
    assert.doesNotMatch(src, /import\.meta\.env\.[A-Z_]*(KEY|TOKEN)/)
    assert.doesNotMatch(src, /api_key|apiKey|Bearer /)
  }
})

test('Data providers is a tab on the existing Providers & Controls screen - no new menu item', () => {
  assert.match(controls, /const TABS = \['network', 'sources'/)
  assert.match(controls, /\{ key: 'network', label: 'Data providers' \}/)
  assert.match(controls, /<ProviderNetwork isAdmin=\{isAdmin\} \/>/)
  assert.doesNotMatch(layout, /Data providers|provider-network/)
})

test('the property screen gains the panels inside existing tabs (no new property page)', () => {
  const own = screen.slice(screen.indexOf("{tab === 'ownership' ? ("), screen.indexOf("{tab === 'signals' ? ("))
  assert.match(own, /<PropertyIntelligence propertyId=\{propertyId\} canBuy=\{canRunHunts\(\)\}/)
  const con = screen.slice(screen.indexOf("{tab === 'contacts' ? ("), screen.indexOf("{tab === 'ai' ? ("))
  assert.match(con, /<ContactIntelligence propertyId=\{propertyId\}/)
})

test('the ATTOM button appears only when the server says a lookup can run, and shows the routing first', () => {
  assert.match(intel, /\{a\.can_lookup \? \(/)
  assert.match(intel, /plan\.steps \|\| \[\]/)
  assert.match(intel, /api\.post\(`\/wholesale\/evosense\/properties\/\$\{propertyId\}\/attom`/)
  assert.match(intel, /disabled=\{busy \|\| !canBuy\}/)
})
