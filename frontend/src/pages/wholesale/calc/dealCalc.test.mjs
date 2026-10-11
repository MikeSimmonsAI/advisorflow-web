// Run: node --test frontend/src/pages/wholesale/calc/dealCalc.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import * as C from './dealCalc.js'

const base = { arv: '200,000', repairMode: 'quick', sqft: '1500', rate: '25', contingencyPct: '10',
               investorPct: '70', fee: '10000', closingPct: '3', closingFlat: '0',
               sellingPct: '8', holdMonths: '4', holdMonthly: '750' }

test('MAO matches the server formula: ARV x % - repairs - closing - fee', () => {
  const r = C.calculate(base)
  assert.equal(r.repairs.total, 41250) // 1500*25 = 37,500 + 10%
  assert.equal(r.closing, 6000)
  assert.equal(r.mao, 140000 - 41250 - 6000 - 10000)
  assert.equal(r.offer, 82500) // rounded down to $500
})

test('investor bottom line: all-in and profit', () => {
  const r = C.calculate({ ...base, offer: '80000' })
  const allIn = 80000 + 10000 + 41250 + 6000 + 3000
  assert.equal(r.allIn, allIn)
  assert.equal(r.profit, 200000 - allIn - 16000)
})

test('seller asking above max is flagged with the gap; below max works', () => {
  assert.equal(C.calculate({ ...base, asking: '100000' }).tone, 'warn')
  assert.equal(C.calculate({ ...base, asking: '100000' }).gap, 100000 - 82750)
  assert.equal(C.calculate({ ...base, asking: '75000' }).tone, 'good')
})

test('no ARV or no repairs: no offer is invented', () => {
  const r = C.calculate({ ...base, arv: '' })
  assert.equal(r.ready, false)
  assert.deepEqual(r.missing, ['After-repair value (ARV)'])
  assert.equal(C.calculate({ ...base, sqft: '' }).ready, false)
})

test('itemized repairs sum the lines; blanks are ignored, not zero-filled', () => {
  const r = C.repairsOf({ repairMode: 'itemized', contingencyPct: 0,
                          items: [{ amount: '12,000' }, { amount: '' }, { amount: '3500' }] })
  assert.equal(r.total, 15500)
  assert.equal(C.repairsOf({ repairMode: 'itemized', items: [{ amount: '' }] }).total, null)
})

test('a deal that cannot work says so', () => {
  const r = C.calculate({ ...base, arv: '60000' })
  assert.equal(r.tone, 'bad')
  assert.ok(r.mao <= 0)
})
