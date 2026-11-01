// PROJECT OVERDRIVE Design B (#439): the one-property Acquisition Command screen.
// Run: node --test frontend/tests/acquisitionCommand.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ACQ_TABS, HASH_TO_TAB, TAB_KEYS, kpis, ownershipChecks, readiness, tabFrom } from '../src/pages/wholesale/evosense/acqModel.js'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const screen = R('../src/pages/wholesale/evosense/EvoProperty.jsx')
const parts = R('../src/pages/wholesale/evosense/AcquisitionCommand.jsx')
const model = R('../src/pages/wholesale/evosense/acqModel.js')
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

// A payload shaped like GET /wholesale/evosense/properties/{id} (views.py), with
// made-up test values - NOT the approved mockup's figures.
const full = {
  property: { id: 'p1', address: '1 Test St', city: 'Testville', county: 'Dallas', state: 'TX', opportunity_score: 61, parcel_apn: 'T-1' },
  facts: { physical: { property_type: 'single_family' }, equity_pct: { value: 42, truth: 'ESTIMATE (tax value minus loans on record)' } },
  economics: {
    arv: { value: 210000, comp_count: 4, confidence: 'medium' },
    lines: [{ label: 'Repairs', value: 31000, truth_label: 'ESTIMATE' }, { label: 'Preliminary MAO', value: 116000, truth_label: 'CALCULATED' }],
  },
  signals: [
    { signal_type: 'TAX_DELINQUENT', scored: true, freshness: 'current', derived: false },
    { signal_type: 'ABSENTEE', scored: true, freshness: 'aging', derived: true },
    { signal_type: 'OLD_LIS', scored: false, freshness: 'stale', derived: false },
  ],
  owners: [{ id: 'o1', name: 'Test Owner', current: true, resolution: 'resolved', resolution_label: 'Matched to record', sources: ['DCAD'] }],
  conflicts: [], identity_reviews: [],
  opportunity: { confidence: { label: 'verified' }, workflow_state: { label: 'Research' } },
}
const empty = { property: { id: 'p2', address: '2 Test St' }, facts: {}, economics: {}, signals: [], owners: [], conflicts: [], identity_reviews: [] }

test('the nine approved tabs, in the approved order', () => {
  assert.deepEqual(ACQ_TABS.map((t) => t.label),
    ['Overview', 'Ownership', 'Distress Signals', 'Comps & Value', 'Underwriting', 'Buyers', 'Contacts', 'AI Assistant', 'Activity'])
})

test('?tab= deep links and old #anchors open the right tab; anything else opens Overview', () => {
  assert.equal(tabFrom('?tab=buyers', ''), 'buyers')
  assert.equal(tabFrom('?tab=nonsense', ''), 'overview')
  assert.equal(tabFrom('', '#deal'), 'underwriting')
  assert.equal(tabFrom('', '#owner'), 'contacts')
  assert.equal(tabFrom('', ''), 'overview')
  for (const t of Object.values(HASH_TO_TAB)) assert.ok(TAB_KEYS.includes(t), t)
})

test('the six numbers come from the record, with their source labels', () => {
  const k = kpis(full)
  assert.equal(k.distress.value, 61)
  assert.equal(k.signals.value, 2, 'stale / unscored signals are not counted')
  assert.match(k.signals.sub, /1 on a public record · 1 computed · 1 stale or unverified/)
  assert.equal(k.equity.value, '42%')
  assert.equal(k.arv.value, '$210,000')
  assert.match(k.arv.sub, /4 eligible closed sales · medium confidence/)
  assert.equal(k.repairs.value, '$31,000')
  assert.equal(k.mao.value, '$116,000')
  assert.match(k.mao.sub, /preliminary/)
})

test('unknown stays unknown - an empty record shows no numbers at all', () => {
  const k = kpis(empty)
  for (const key of ['distress', 'signals', 'equity', 'arv', 'repairs', 'mao']) assert.equal(k[key].value, null, key)
  assert.match(k.equity.sub, /unknown/)
  assert.match(k.arv.sub, /Insufficient comparable sales/)
  assert.match(k.mao.sub, /verified ARV/)
})

test('ownership checks are read from the record, and conflicts never pass', () => {
  assert.ok(ownershipChecks(full).every((c) => c.ok))
  const none = ownershipChecks(empty)
  assert.equal(none.find((c) => c.key === 'owner').ok, false)
  assert.match(none.find((c) => c.key === 'owner').detail, /nothing is guessed/)
  const conflicted = ownershipChecks({ ...full, owners: [{ ...full.owners[0], in_conflict: true }] })
  assert.equal(conflicted.find((c) => c.key === 'conflicts').ok, false)
  const review = ownershipChecks({ ...full, identity_reviews: [{ id: 'r' }] })
  assert.equal(review.find((c) => c.key === 'identity').ok, false)
})

test('readiness reflects evidence confidence, and says so when not assessed', () => {
  assert.deepEqual(readiness(full), { label: 'Verified evidence', stage: 'Research', tone: 'live' })
  assert.equal(readiness(empty).label, 'Evidence not assessed')
})

test('no figures from the approved mockup are embedded: no dollar amounts or sample people in the screen code', () => {
  for (const [name, src] of [['AcquisitionCommand.jsx', parts], ['acqModel.js', model], ['EvoProperty.jsx', screen]]) {
    const code = strip(src)
    assert.doesNotMatch(code, /\$\d{2,3},\d{3}/, `${name} has a hard-coded dollar figure`)
    assert.doesNotMatch(code, /['"]\d{2,3},\d{3}['"]/, `${name} has a hard-coded number string`)
  }
})

test('the header carries the approved fields and a prominent Launch Deal', () => {
  const head = parts.slice(parts.indexOf('export function AcqHeader'), parts.indexOf('export function AcqOverview'))
  for (const needle of ['p.address', 'p.county', 'property_type', 'parcel', 'Distress score', 'ScoreWhy', 'readiness(d)', 'Launch Deal', 'MeasureTags'])
    assert.ok(head.includes(needle), needle)
})

test('the seven approved actions are all present and each is wired to something real', () => {
  for (const [key, label] of [['verify-ownership', 'Verify Ownership'], ['analyze-deal', 'Analyze Deal'], ['find-buyers', 'Find Active Buyers'],
    ['enrich-contacts', 'Enrich Contacts'], ['schedule-follow-up', 'Schedule Follow-Up'], ['add-to-pipeline', 'Add to Pipeline'], ['launch-deal', 'Launch Deal']]) {
    assert.ok(screen.includes(`key: '${key}'`), key)
    assert.ok(screen.includes(label), label)
  }
  assert.match(screen, /key: 'enrich-contacts'[^\n]*\n[^\n]*onClick: enrich, disabled: busy \|\| !!enrichWhy, why: enrichWhy/)
  assert.match(screen, /api\.post\(`\/wholesale\/evosense\/properties\/\$\{propertyId\}\/enrich`/)
  assert.match(screen, /api\.post\(`\/wholesale\/evosense\/properties\/\$\{propertyId\}\/promote`/)
})

test('Launch Deal goes through the existing deal package approval workflow - it cannot publish by itself', () => {
  const launch = parts.slice(parts.indexOf('export function LaunchDealDrawer'))
  assert.match(launch, /<DealPackagePanel dealId=\{dealId\}/)
  assert.doesNotMatch(launch, /api\.post/)
  assert.match(launch, /Add to pipeline first/)
})

test('Investment underwriting reuses the server engine (and the shared calculator), never a second formula', () => {
  const uw = parts.slice(parts.indexOf('export function UnderwritingModes'), parts.indexOf('export function LaunchDealDrawer'))
  assert.match(uw, /<UnderwritingTab dealId=\{dealId\} goTo=\{goTo\} \/>/)
  assert.match(uw, /<DealCalculator compact prefill=\{prefillFromProperty\(d\)\} \/>/)
  assert.match(parts, /const MODES = \[\{ key: 'wholesale', label: 'Wholesale' \}, \{ key: 'investment', label: 'Investment' \}, \{ key: 'both', label: 'Both' \}\]/)
  assert.doesNotMatch(uw, /\* 0\.7|\* \.7|0\.70/)
})

test('every panel the screen had before is still on it (moved into tabs, not removed)', () => {
  for (const needle of ['<PublicRecord', '<ValueEstimate', '<OpportunityCard', '<PropertyTypePanel', '<CallOutcomePanel', '<FirstContactPlan',
    '<EnrichmentStatus', '<EvoActions', '<ReadinessPanel', 'title={ev(\'EvoSense intelligence\')}', 'title="Seller conversation"',
    'title="Seller facts"', 'title="Deal intelligence"', '<FieldVisitsPanel', "title={ev('Why EvoSense found it')}", 'title="Timeline"',
    '<EvoFollowUp', '<EvoPermission', 'title="Data intelligence"', 'title="Nurture"', 'title="Your judgment"', '<ManualComp',
    '<AcquisitionCost', '<DealCalculator', '<MatchResults', 'title="Promote to Deal Operations"', 'title="Raw source record"',
    '{bookDrawer}', 'identity-reviews/${r.id}', 'Conflicts — not overwritten', 'FieldPropertyCard']) {
    assert.ok(screen.includes(needle), `missing ${needle}`)
  }
})

test('the tab panel is accessible: tablist from the design system, labelled tabpanel', () => {
  assert.match(screen, /<TabBar items=\{tabItems\} value=\{tab\} onChange=\{selectTab\} label="Property sections" \/>/)
  assert.match(screen, /role="tabpanel" id=\{`panel-\$\{tab\}`\} aria-labelledby=\{`tab-\$\{tab\}`\}/)
})

test('a deal opens on the tab Launch Deal links to (?tab=sharing / buyers), unknown values ignored', () => {
  const deal = R('../src/pages/wholesale/WholesaleDeal.jsx')
  assert.match(deal, /TABS\.some\(\(\[k\]\) => k === params\.get\('tab'\)\) \? params\.get\('tab'\) : 'overview'/)
  assert.match(deal, /\['sharing', 'Sharing'\]/)
  assert.match(deal, /\['buyers', 'Buyer matching'\]/)
})
