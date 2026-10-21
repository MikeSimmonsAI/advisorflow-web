import test from 'node:test'
import assert from 'node:assert/strict'
import { percentage, percentText, barWidth, eventMix } from '../src/pages/program/command/rateModel.js'
test('opt-outs count as a fraction of all meaningful responses', () => {
  const mix = eventMix(8, 2)
  assert.deepEqual(mix, { replies: 8, optOuts: 2, total: 10, replyPct: 80, optOutPct: 20 })
  assert.equal(percentText(mix.optOutPct), '20%')
})
test('engagement by touch has same denominator: contacted families', () => {
  assert.equal(percentage(2, 20), 10)
  assert.equal(percentage(7, 20), 35)
  assert.equal(percentage(12, 20), 60)
  assert.equal(percentage(9, 10), 90)
  assert.equal(percentage(0, 10), 0)
})
test('unavailable denominators show dash, not fake zero', () => {
  assert.equal(percentage(0, 0), null)
  assert.equal(percentage(1, 0), null)
  assert.equal(percentage(0, null), null)
  assert.equal(percentText(percentage(1, 0)), '—')
  assert.equal(eventMix(0, 0).optOutPct, null)
  assert.equal(eventMix(null, 1), null)
  assert.equal(barWidth(null), '0%')
})
test('one decimal precision and visual bars stay bounded', () => {
  assert.equal(percentText(33.3), '33.3%')
  assert.equal(percentText(0), '0%')
  assert.equal(barWidth(110), '100%')
  assert.equal(barWidth(-1), '0%')
})
