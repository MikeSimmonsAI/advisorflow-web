// node --test frontend/tests/proposalMoney.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  parseCents, formatCents, validateAdjustment, totalAfter, validateCustomRate,
  refusalMessage, refusalKind,
} from '../src/utils/proposalMoney.js'

test('exact cents, no float drift', () => {
  assert.equal(parseCents('0.1') + parseCents('0.2'), 30)
  assert.equal(parseCents('1,497.00'), 149700)
  assert.equal(parseCents('-500'), -50000)
  assert.equal(parseCents('19.99') * 3, 5997)
  assert.equal(formatCents(149700), '$1,497.00')
  assert.equal(formatCents(-50050), '-$500.50')
  assert.equal(formatCents(5), '$0.05')
  assert.equal(formatCents(null), '—')
  assert.equal(formatCents(12.5), '—')
})

test('NaN-like, overflow, sub-cent and junk input refused (not rounded)', () => {
  const bads = ['NaN', 'Infinity', '-Infinity', '1e9', '12.345', 'abc', '', ' ', '$5', '1.', '--5',
    '10000000001', NaN, Infinity, null, undefined, {}]
  for (const bad of bads) {
    assert.equal(parseCents(bad), null, String(bad))
    assert.equal(validateAdjustment(bad).ok, false, String(bad))
  }
  assert.equal(validateAdjustment('-500').ok, true)
  assert.equal(validateAdjustment('0').cents, 0)
})

test('negative total refused like the server', () => {
  assert.equal(totalAfter(100000, -100001).ok, false)
  assert.deepEqual(totalAfter(100000, -100000), { ok: true, cents: 0, error: null })
  assert.equal(totalAfter(null, 5).ok, false)
})

test('custom rate: bounded, explicit, integer math', () => {
  const r = validateCustomRate({ unit: '250', min: '15', term: '13' })
  assert.equal(r.monthlyCents, 375000)
  assert.equal(r.commitmentCents, 4875000)
  assert.deepEqual(r.fields, { custom_unit_price: 250, custom_min_units: 15, custom_term_months: 13 })
  assert.equal(validateCustomRate({ unit: '0.1', min: '3', term: '' }).monthlyCents, 30)
  assert.equal(validateCustomRate({ unit: '10', min: '', term: '' }).fields.custom_min_units, 1)
  assert.equal(validateCustomRate({ unit: '10', min: '', term: '' }).fields.custom_term_months, null)
  const bads = [
    { unit: '-5', min: '1', term: '' }, { unit: 'NaN', min: '1', term: '' },
    { unit: '0', min: '1', term: '' }, { unit: '5', min: '0', term: '' },
    { unit: '5', min: '1.5', term: '' }, { unit: '5', min: '1000001', term: '' },
    { unit: '5', min: '1', term: '121' }, { unit: '5', min: '1', term: '-1' },
    { unit: '99999999', min: '1000000', term: '' },
  ]
  for (const bad of bads) assert.equal(validateCustomRate(bad).ok, false, JSON.stringify(bad))
})

test('refusals are labelled by kind and never look like success', () => {
  const m409 = refusalMessage({ status: 409, message: 'This proposal changed since you loaded it.' })
  assert.match(m409, /^Not saved - out of date\. This proposal changed/)
  assert.match(refusalMessage({ status: 403, message: 'Only a sales manager can x.' }), /not authorized.*Only a sales manager/)
  assert.match(refusalMessage({ status: 400, message: 'That adjustment would make the total negative.' }), /invalid.*negative/)
  assert.match(refusalMessage({ status: 422, message: 'bad' }), /invalid/)
  assert.match(refusalMessage({}), /refused/)
  assert.ok(refusalMessage({ status: 400, message: 'x'.repeat(5000) }).length < 400)
  assert.deepEqual([409, 403, 400, 422, 500].map(s => refusalKind({ status: s })),
    ['conflict', 'authority', 'invalid', 'invalid', 'error'])
})

test('panel wiring: cents rendering, version guard, no float totals, no success on refusal', () => {
  const src = readFileSync(new URL('../src/pages/sales/ProposalPanel.jsx', import.meta.url), 'utf8')
  assert.ok(!/Intl\.NumberFormat/.test(src))
  assert.ok(!/Number\(adj\)|Number\(askAdj\)|Number\(f\./.test(src))
  assert.ok(!/getElementById\('adj-input'\)/.test(src))
  assert.ok(!/monthly \* termN|unitN \* minN/.test(src))
  assert.equal((src.match(/expected_updated_at: current\.updated_at/g) || []).length, 1)
  // every proposal PATCH goes through the version-guarded helper
  assert.equal((src.match(/api\.patch\('\/sales\/proposals\/' \+ current\.id/g) || []).length, 1)
  // the success note is only set after the awaited call; the catch branch never sets one
  const act = src.slice(src.indexOf('async function act('), src.indexOf('const patchProposal'))
  assert.ok(act.indexOf('await fn()') < act.indexOf('setNote(okMsg)'))
  const catchPart = act.slice(act.indexOf('} catch'))
  assert.ok(!/setNote\((?!null)/.test(catchPart))
  assert.match(catchPart, /return false/)
})
