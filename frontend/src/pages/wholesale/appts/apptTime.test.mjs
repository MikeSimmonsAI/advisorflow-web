import test from 'node:test'
import assert from 'node:assert/strict'
import { centralToIso, isoToCentral, centralDate, dayLabel } from './apptTime.js'

test('summer time is 5 hours behind UTC', () => {
  assert.equal(centralToIso('2026-10-12', '14:30'), '2026-10-12T14:30:00-05:00')
})

test('winter time is 6 hours behind UTC', () => {
  assert.equal(centralToIso('2026-12-15', '09:00'), '2026-12-15T09:00:00-06:00')
})

test('round trip back to Central', () => {
  assert.deepEqual(isoToCentral('2026-10-13T04:30:00Z'), { day: '2026-10-12', time: '23:30' })
})

test('late evening Central is still the same Central day', () => {
  assert.equal(centralDate(new Date('2026-10-13T03:00:00Z')), '2026-10-12')
})

test('day label', () => {
  assert.equal(dayLabel('2026-10-12'), 'Mon, Oct 12')
})
