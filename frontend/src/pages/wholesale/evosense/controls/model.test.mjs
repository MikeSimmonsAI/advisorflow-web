// Run: node --test frontend/src/pages/wholesale/evosense/controls/model.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import * as M from './model.js'

const NOW = Date.parse('2026-10-11T18:00:00Z')
const base = { capabilities: ['ASSESSOR'], cost: 'free', configured: true, enabled: true, state: 'HEALTHY',
               operational: true, connector_kind: 'real', coverage_area: { scope: 'county', counties: ['Tarrant'] } }

test('a newer success beats an older failure, and the failure stays visible as recovered', () => {
  const tad = { ...base, label: 'TAD', last_success_at: '2026-10-11T13:12:00Z',
                last_failure_at: '2026-10-10T09:00:00Z', last_failure_reason: 'HTTP 403 AUTH_FAILED' }
  const l = M.latestOf(tad, NOW)
  assert.equal(l.key, 'ok')
  assert.equal(l.earlier.detail, 'HTTP 403 AUTH_FAILED')
  assert.equal(l.earlier.recent, true)
  assert.equal(M.groupOf(tad, NOW), 'live')
  assert.equal(M.recentlyRecovered(tad, NOW), true)
  assert.deepEqual(M.attentionList([tad], NOW).recovered.map((p) => p.label), ['TAD'])
})

test('a failure newer than the last success is the latest result, even if the state says HEALTHY', () => {
  const lgbs = { ...base, label: 'Tax sales', last_success_at: '2026-10-09T00:00:00Z',
                 last_failure_at: '2026-10-11T01:00:00Z', last_failure_reason: 'certificate verify failed' }
  assert.equal(M.latestOf(lgbs, NOW).key, 'failed')
  assert.equal(M.groupOf(lgbs, NOW), 'attention')
  assert.deepEqual(M.attentionList([lgbs], NOW).current.map((p) => p.label), ['Tax sales'])
})

test('no calls yet is "not yet tested", never healthy or broken', () => {
  const p = { ...base, state: 'UNVERIFIED', operational: false }
  assert.equal(M.latestOf(p, NOW).key, 'untested')
  assert.equal(M.groupOf(p, NOW), 'untested')
  const off = { ...base, enabled: false, state: 'DISABLED', operational: false }
  assert.equal(M.latestOf(off, NOW).key, 'never')
  assert.equal(M.groupOf(off, NOW), 'disabled')
})

test('kinds stay apart: manual, sandbox, not purchased, not configured, evaluation only', () => {
  assert.equal(M.groupOf({ connector_kind: 'manual', state: 'MANUAL ONLY' }), 'manual')
  assert.equal(M.groupOf({ connector_kind: 'sandbox', state: 'DISABLED' }), 'sandbox')
  assert.equal(M.groupOf({ connector_kind: 'interface_only', state: 'NOT PURCHASED' }), 'unconfigured')
  assert.equal(M.groupOf({ connector_kind: 'real', state: 'NOT CONFIGURED', cost: 'paid' }), 'unconfigured')
  assert.equal(M.groupOf({ ...base, state: 'EVALUATION ONLY', operational: false }), 'evaluation')
  assert.equal(M.isPaid({ connector_kind: 'real', cost: 'paid' }), true)
  assert.equal(M.isPaid({ connector_kind: 'real', cost: 'free' }), false)
  assert.equal(M.isPaid({ connector_kind: 'interface_only', cost: 'paid (not purchased)' }), true)
})

test('county coverage: a city connector is not county-wide; sandbox never counts; anywhere-manual reported once', () => {
  const fw = { ...base, label: 'FW code', capabilities: ['CODE_VIOLATION'],
               coverage_area: { scope: 'city', counties: ['Tarrant'], scope_name: 'City of Fort Worth' },
               last_success_at: '2026-10-11T00:00:00Z' }
  const sand = { connector_kind: 'sandbox', capabilities: ['CODE_VIOLATION'], enabled: true, operational: true,
                 coverage_area: { scope: 'sandbox', counties: [] } }
  const man = { connector_kind: 'manual', state: 'MANUAL ONLY', capabilities: ['CODE_VIOLATION'], coverage_area: { scope: 'any', counties: [] } }
  const cov = M.coverageFor('CODE_VIOLATION', [fw, sand, man], ['Dallas', 'Tarrant'], NOW)
  assert.equal(cov.rows[0].cell, 'none')
  assert.equal(cov.rows[1].cell, 'live_city')
  assert.equal(cov.live, 0)
  assert.equal(cov.cityOnly, 1)
  assert.equal(cov.anyManual.length, 1)
  assert.equal(M.scopeOf(fw).label, 'City of Fort Worth only')
})

test('county universe follows the widest source and covers every county mentioned', () => {
  const a = { connector_kind: 'real', coverage_area: { scope: 'county', counties: ['Collin'] } }
  const b = { connector_kind: 'real', coverage_area: { scope: 'county', counties: ['Dallas', 'Tarrant', 'Collin'] } }
  const s = { connector_kind: 'sandbox', coverage_area: { scope: 'county', counties: ['Nowhere'] } }
  assert.deepEqual(M.countyUniverse([a, b, s]), ['Dallas', 'Tarrant', 'Collin'])
})

test('filters: search, county, capability, status', () => {
  const p = { ...base, label: 'Tarrant Appraisal District', last_success_at: '2026-10-11T00:00:00Z' }
  assert.equal(M.matchesProvider(p, { q: 'appraisal' }, NOW), true)
  assert.equal(M.matchesProvider(p, { county: 'Dallas' }, NOW), false)
  assert.equal(M.matchesProvider(p, { county: 'Tarrant', cap: 'ASSESSOR', status: 'live' }, NOW), true)
  assert.equal(M.matchesProvider(p, { status: 'attention' }, NOW), false)
  const nat = { ...base, coverage_area: { scope: 'national', counties: [] } }
  assert.equal(M.matchesProvider(nat, { county: 'Ellis' }, NOW), true)
})

test('paid exposure reads the server channel and the provider switches', () => {
  const ctl = { paused_paid_data: false, channels: { paused_paid_data: { state: 'NOTHING CONNECTED' } } }
  const x = M.paidExposure([{ connector_kind: 'real', cost: 'paid', enabled: false }, { connector_kind: 'real', cost: 'free', enabled: true }], ctl)
  assert.equal(x.paid.length, 1)
  assert.equal(x.on.length, 0)
  assert.equal(x.channel.state, 'NOTHING CONNECTED')
})

test('a statewide source reads as statewide, not as a county list', () => {
  const s = M.scopeOf({ coverage_area: { scope: "state", counties: [], scope_name: "Texas" } })
  assert.equal(s.key, 'state')
  assert.equal(s.label, 'Texas statewide')
})

test('a Texas statewide source lists all 254 counties but covers only those where it returned data', () => {
  const now = Date.parse('2026-10-22T03:00:00Z')
  const base = { connector_kind: 'real', enabled: true, configured: true, operational: true, state: 'HEALTHY',
                 healthy: true, last_success_at: '2026-10-22T02:00:00Z' }
  const providers = [
    { ...base, key: 'tarrant_tax_roll', label: 'Tarrant County tax roll', capabilities: ['TAX'],
      coverage_area: { scope: 'county', counties: ['Tarrant'] } },
    { ...base, key: 'lgbs_tax_sales', label: 'Tax sales (all Texas counties)', capabilities: ['TAX'],
      coverage_area: { scope: 'state', counties: [], scope_name: 'Texas', verified_counties: ['Harris', 'Tarrant'] } },
  ]
  assert.equal(M.TEXAS_COUNTIES.length, 254)
  const counties = M.countyUniverse(providers)
  assert.equal(counties.length, 254)                         // the whole state is listed...
  assert.equal(counties[0], 'Tarrant')                       // county-specific first
  const cov = M.coverageFor('TAX', providers, counties, now)
  assert.equal(cov.rows.find((r) => r.county === 'Harris').best.key, 'lgbs_tax_sales')
  assert.equal(cov.rows.find((r) => r.county === 'Tarrant').sources.length, 2)
  assert.equal(cov.rows.find((r) => r.county === 'Bexar').cell, 'none')   // ...but declared scope is not coverage
  assert.equal(cov.live, 2)
  assert.equal(M.scopeOf(providers[1]).label, 'Texas statewide · data in 2 counties')
})
