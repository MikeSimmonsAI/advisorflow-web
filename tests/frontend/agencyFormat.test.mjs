// node tests/frontend/agencyFormat.test.mjs — Max Life Command pure helpers + vertical selection.
import assert from 'node:assert/strict'
import {
  humanize, toneFor, sortAttention, routeForLink, apiQuery, filterLabel, relTime, metric, factText, pctWidth, groupByStage,
} from '../../frontend/src/pages/agency/agencyFormat.js'
import { AGENCY, agencyVerticalFor, hasAgencyFeature } from '../../frontend/src/verticals/agencyVertical.js'

let n = 0
const t = (name, fn) => { fn(); n++ }

t('humanize vocab + fallback', () => {
  assert.equal(humanize('family_protection'), 'Family Protection')
  assert.equal(humanize('requirements_requested'), 'Requirements requested')
  assert.equal(humanize('timed_out'), 'Timed out')
  assert.equal(humanize('some_new_key'), 'Some New Key')
  assert.equal(humanize(null), '')
})
t('tones', () => {
  assert.equal(toneFor('escalated'), 'red'); assert.equal(toneFor('issued'), 'green'); assert.equal(toneFor('offered'), 'amber')
})
t('attention sorted by severity, stable', () => {
  const out = sortAttention([{ severity: 'medium', id: 1 }, { severity: 'high', id: 2 }, { severity: 'medium', id: 3 }, { severity: 'high', id: 4 }])
  assert.deepEqual(out.map(x => x.id), [2, 4, 1, 3])
  assert.deepEqual(sortAttention(null), [])
})
t('links only route inside /agency', () => {
  assert.equal(routeForLink({ path: '/agency/prospects/abc' }), '/agency/prospects/abc')
  assert.equal(routeForLink('/agency/prospects?intent=high'), '/agency/prospects?intent=high')
  assert.equal(routeForLink('/agency/attention'), '/agency')
  assert.equal(routeForLink('https://evil.example/x'), null)
  assert.equal(routeForLink('/admin'), null)
  assert.equal(routeForLink(null), null)
})
t('apiQuery whitelists params', () => {
  const sp = new URLSearchParams('intent=high&evil=1&unassigned=true')
  assert.equal(apiQuery(sp, ['intent', 'unassigned'], { per_page: 100 }), '?intent=high&unassigned=true&per_page=100')
  assert.equal(apiQuery(new URLSearchParams(''), ['q']), '')
  assert.equal(filterLabel(sp, ['intent', 'unassigned']), 'Intent: High · Unassigned')
})
t('relTime', () => {
  const now = Date.parse('2026-10-01T12:00:00Z')
  assert.equal(relTime('2026-10-01T08:00:00Z', now), '4h ago')
  assert.equal(relTime('2026-10-02T12:00:00Z', now), 'in 1d')
  assert.equal(relTime(null, now), '—')
})
t('unavailable metric stays unavailable', () => {
  assert.equal(metric(null, ' min'), 'Not yet available'); assert.equal(metric(0, '%'), '0%')
})
t('factText', () => {
  assert.equal(factText({ spouse: true, children: 2 }), 'Spouse · Children: 2')
  assert.equal(factText(['family_protection']), 'Family Protection')
  assert.equal(factText([]), '—'); assert.equal(factText(false), 'No')
})
t('pctWidth clamps', () => { assert.equal(pctWidth(140), 100); assert.equal(pctWidth(-3), 0); assert.equal(pctWidth('x'), 0) })
t('groupByStage keeps order and unknown stages', () => {
  const g = groupByStage([{ stage: 'b' }, { stage: 'zz' }, { stage: 'a' }], ['a', 'b', 'c'])
  assert.deepEqual(g.map(x => [x.stage, x.items.length]), [['a', 1], ['b', 1], ['c', 0], ['zz', 1]])
})
t('vertical selected only by explicit feature', () => {
  assert.equal(agencyVerticalFor({ enabled_features: ['leads', 'insurance_agency'] }), AGENCY)
  assert.equal(agencyVerticalFor({ enabled_features: null, industry: 'insurance' }), null)
  assert.equal(agencyVerticalFor({ enabled_features: ['leads'], industry: 'insurance' }), null)
  assert.equal(hasAgencyFeature(null), false)
})
t('nav groups + feature keys', () => {
  assert.deepEqual(AGENCY.navGroups.map(g => g.label), ['Command', 'Client Acquisition', 'Case Management', 'Agency Growth', 'Intelligence', 'System'])
  for (const g of AGENCY.navGroups) for (const it of g.items) if (it.to.startsWith('/agency')) assert.equal(it.featureKey, 'insurance_agency', it.to)
})
console.log(`agencyFormat: ${n} passed`)

// ── S8 helpers ──────────────────────────────────────────────────────────────
{
  const m = await import('../../frontend/src/pages/agency/agencyFormat.js')
  const t2 = (name, fn) => { try { fn() } catch (e) { console.error('FAIL', name); throw e } }
  t2('splitList', () => {
    assert.deepEqual(m.splitList('a, b\nA\n\n c '), ['a', 'b', 'c'])
    assert.deepEqual(m.splitList(''), [])
  })
  t2('tri-state', () => {
    assert.equal(m.triState(''), null); assert.equal(m.triState('yes'), true); assert.equal(m.triState('no'), false)
    assert.equal(m.triValue(null), ''); assert.equal(m.triValue(false), 'no')
  })
  t2('profilePatch sends only changes; blank means not stated', () => {
    const orig = { household: { adults: 2, pets: true }, preferred_contact: 'sms', need_categories: ['retirement'],
      financial_goals: ['college'], stated_concerns: [], retirement_interest: true, business_owner_interest: null, living_benefits_interest: null }
    const f = m.profileForm(orig)
    assert.deepEqual(m.profilePatch(f, orig), {})
    f.household_children = '3'; f.retirement_interest = ''; f.stated_concerns = 'cost\ncost'
    const p = m.profilePatch(f, orig)
    assert.deepEqual(p, { household: { adults: 2, pets: true, children: 3 }, stated_concerns: ['cost'], retirement_interest: null })
    const g = m.profileForm(orig); g.household_adults = ''
    assert.deepEqual(m.profilePatch(g, orig).household, { pets: true })
    const empty = m.profileForm(null)
    assert.deepEqual(m.profilePatch(empty, null), {})
  })
  t2('localToIso', () => {
    assert.equal(m.localToIso(''), null); assert.equal(m.localToIso('garbage'), null)
    assert.match(m.localToIso('2026-10-02T09:30'), /^2026-10-0\dT\d\d:30:00Z$/)
  })
  t2('pageInfo', () => {
    assert.deepEqual(m.pageInfo({ total: 120, page: 2, per_page: 50 }), { total: 120, page: 2, pages: 3, from: 51, to: 100, hasPrev: true, hasNext: true })
    assert.equal(m.pageInfo({ total: 0 }).from, 0)
    assert.equal(m.pageInfo({ total: 0 }).hasNext, false)
  })
  t2('agencyLanding', () => {
    assert.equal(m.agencyLanding(true, '/'), '/agency')
    assert.equal(m.agencyLanding(true, '/workspace/abc'), '/agency')
    assert.equal(m.agencyLanding(true, '/agency/prospects'), null)
    assert.equal(m.agencyLanding(false, '/'), null)
  })
  const conv = AGENCY.navGroups.flatMap(g => g.items).find(i => i.label === 'Conversations')
  assert.equal(conv.to, '/agency/conversations')
  console.log('S8 agencyFormat helpers OK')
}
