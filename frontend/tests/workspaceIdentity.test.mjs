// BUSINESS IDENTITY IS NOT A FEATURE SWITCH (2026-10-21) - frontend half.
// The Insurance (Max Life) presentation follows the business, not the switch.
import assert from 'node:assert/strict'
import test from 'node:test'
import { agencyVerticalFor, hasAgencyFeature, identityAllows } from '../src/verticals/agencyVertical.js'

const B = (industry, features) => ({ industry, enabled_features: features })
const INS = 'insurance_agency'

test('Restland with the Insurance capability keeps its own look', () => {
  const restland = B('funeral', ['leads', INS, 'roofing_program', 'wholesale_real_estate'])
  assert.equal(hasAgencyFeature(restland), true, 'capability is still there')
  assert.equal(agencyVerticalFor(restland), null, 'but it does not become an insurance agency')
})

test('an insurance agency with the capability gets the agency presentation', () => {
  assert.ok(agencyVerticalFor(B('insurance', ['leads', INS])))
})

test('orgs with no industry of their own behave as before', () => {
  for (const ind of [undefined, null, '', 'generic', 'other', ' Generic '])
    assert.ok(agencyVerticalFor(B(ind, [INS])), String(ind))
})

test('no specific non-insurance business is ever shown as an agency', () => {
  for (const ind of ['funeral', 'cemetery', 'energy', 'cleaning', 'fiber', 'real_estate', 'roofing'])
    for (const f of [[INS], [INS, 'roofing_program'], [INS, 'wholesale_real_estate']])
      assert.equal(agencyVerticalFor(B(ind, f)), null, ind + ' ' + f)
})

test('without the capability nothing changes', () => {
  for (const ind of ['insurance', 'generic', 'funeral']) {
    assert.equal(agencyVerticalFor(B(ind, ['leads'])), null)
    assert.equal(agencyVerticalFor(B(ind, null)), null, 'legacy NULL list is not an agency')
  }
})

test('identityAllows matches the server rule', () => {
  assert.equal(identityAllows(B('roofing'), 'roofing'), true)
  assert.equal(identityAllows(B('funeral'), 'roofing'), false)
  assert.equal(identityAllows(B('generic'), 'roofing'), true)
  assert.equal(identityAllows(null, 'insurance'), true)
})
