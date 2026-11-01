// OLP Land Partners / Farm & Ranch calculators: the math, against hand-worked numbers.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { landDeal, farmIncome, monthlyPayment, num } from '../src/pages/wholesale/olp/landMath.js'

const near = (a, b, d = 0.01) => assert.ok(Math.abs(a - b) <= d, `${a} != ${b}`)

test('num reads typed money, percent and commas; blank is missing, never zero', () => {
  assert.equal(num('$12,500'), 12500); assert.equal(num('7.5%'), 7.5); assert.equal(num(''), null); assert.equal(num(null), null)
  assert.equal(num('abc'), null)
})

test('monthly payment: standard amortization, zero-rate, and refusals', () => {
  near(monthlyPayment(20000, 9, 60), 415.17)            // $20k, 9%, 5 years
  near(monthlyPayment(12000, 0, 48), 250)
  assert.equal(monthlyPayment(0, 9, 60), null); assert.equal(monthlyPayment(10000, 9, 0), null)
})

test('land deal: offer from % of comparable value, cash profit, and the seller-financed note', () => {
  const r = landDeal({ acres: '10', ppa: '6,000', offerPct: '35', closing: '1,500', sale: '45,000', down: '4,500', rate: '9', years: '5' })
  assert.equal(r.market, 60000)
  assert.equal(r.offer, 21000)
  assert.equal(r.offerPerAcre, 2100)
  assert.equal(r.allIn, 22500)
  assert.equal(r.cashProfit, 22500)
  assert.equal(r.financed, 40500)
  assert.equal(r.months, 60)
  near(r.pay, 840.71)
  near(r.collected, 4500 + 840.71 * 60, 1)
  near(r.notesProfit, r.collected - 22500, 0.001)
  assert.equal(r.payback, Math.ceil((22500 - 4500) / r.pay))   // 22 months
})

test('land deal: a typed offer wins over the %; missing inputs give blanks, not guesses', () => {
  const r = landDeal({ acres: '10', ppa: '6000', offerPct: '35', offer: '18000' })
  assert.equal(r.offer, 18000)
  const e = landDeal({})
  for (const k of ['market', 'offer', 'allIn', 'cashProfit', 'pay', 'collected', 'notesProfit', 'payback']) assert.equal(e[k], null, k)
})

test('land deal: a down payment that covers the cost pays back at closing', () => {
  const r = landDeal({ offer: '5000', closing: '500', sale: '30000', down: '6000', rate: '10', years: '3' })
  assert.equal(r.payback, 0)
})

test('farm income: acres x rent, leases, costs, value from a cap rate and return on price', () => {
  const r = farmIncome({ crop: '120', cropRent: '150', pasture: '70', pastureRent: '30', timber: '10', hunting: '6000',
                         taxes: '1800', insurance: '900', upkeep: '2400', cap: '3', price: '850000' })
  assert.equal(r.income, 26100)
  assert.equal(r.costs, 5100)
  assert.equal(r.net, 21000)
  near(r.value, 700000)
  near(r.ret, 2.47)
  assert.equal(r.acres, 200)
  assert.equal(r.perAcre, 4250)
  assert.equal(r.netPerAcre, 105)
})

test('farm income: acres without a rent earn nothing - no rent is ever assumed', () => {
  assert.equal(farmIncome({ crop: '100' }).income, null)
  assert.equal(farmIncome({}).income, null)
})
