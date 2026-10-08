// node --test frontend/tests/compensationProjection.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  LEGEND, TILE_LABELS, EMPTY_TEXT, usd, errorMessage, emptyState, projectionQuery, stageOptions,
} from '../src/utils/compensationProjection.js'

const m = (display, count = 0) => ({ cents: 0, display, count })
const base = (over = {}) => ({
  earned: { on_hold: m('0.00'), payable: m('0.00'), paid: m('0.00') },
  pending: { deal_count: 0 }, deals: [], excluded: [], blockers: [], ...over,
})

test('legend separates every required bucket and never calls a forecast earned', () => {
  const labels = LEGEND.map(x => x.label)
  for (const l of ['Earned', 'On hold', 'Payable', 'Paid', 'Pending approval',
    'Unweighted forecast', 'Weighted forecast', 'Excluded / blockers']) assert.ok(labels.includes(l), l)
  for (const x of LEGEND.filter(x => x.kind === 'forecast')) {
    assert.match(x.text, /not money owed/)
    assert.doesNotMatch(x.label, /earned|payable|payroll|paid/i)
  }
  for (const k of ['gross', 'weightedGross', 'commission', 'weightedCommission', 'pending']) {
    assert.doesNotMatch(TILE_LABELS[k], /^Earned|payroll|payable|paid/i, k)
  }
})

test('money is prefixed server display text; missing is a dash, never $0', () => {
  assert.equal(usd({ cents: 123456, display: '1,234.56' }), '$1,234.56')
  assert.equal(usd({ cents: null, display: null }), '—')
  assert.equal(usd(null), '—')
  assert.equal(usd({ cents: 5 }), '—')
})

test('errors keep status and server detail, never empty or success', () => {
  assert.match(errorMessage({ status: 400, detail: 'brand_sales_org_id is required.' }), /400.*brand_sales_org_id is required/)
  assert.match(errorMessage({ status: 403, detail: 'No compensation scope.' }), /403.*No compensation scope/)
  assert.match(errorMessage({ status: 422, detail: 'bad cents' }), /422.*inconsistent.*bad cents/)
  assert.match(errorMessage({ status: 503, message: 'down' }), /503.*down.*No figures/)
  assert.match(errorMessage({ message: 'Failed to fetch' }), /Could not reach.*No figures/)
  assert.match(errorMessage({ status: 403 }), /403/)
  assert.match(errorMessage(undefined), /Could not reach/)
})

test('empty vs excluded vs data', () => {
  assert.equal(emptyState(null), 'none')
  assert.equal(emptyState(base()), 'no_records')
  assert.equal(emptyState(base({ excluded: [{ label: 'x' }] })), 'blocked')
  assert.equal(emptyState(base({ blockers: ['no plan'] })), 'blocked')
  assert.equal(emptyState(base({ deals: [{}] })), 'data')
  assert.equal(emptyState(base({ pending: { deal_count: 2 } })), 'data')
  assert.equal(emptyState(base({ earned: { on_hold: m('1.00', 1), payable: m('0.00'), paid: m('0.00') } })), 'data')
  assert.match(EMPTY_TEXT.no_records, /No matching records/)
  assert.match(EMPTY_TEXT.blocked, /not a \$0 forecast/)
})

test('query and stage ordering are deterministic', () => {
  assert.equal(projectionQuery('', ''), '')
  assert.equal(projectionQuery('b 1', 'Won/Open'), '?brand_sales_org_id=b%201&stage=Won%2FOpen')
  assert.deepEqual(stageOptions([{ stage: 'b' }, { stage: 'a' }, { stage: 'b' }]), ['a', 'b'])
  assert.deepEqual(stageOptions(undefined), [])
})
