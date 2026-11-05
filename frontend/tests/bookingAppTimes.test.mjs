// The legacy Vercel booking page (booking-app/index.html, advisorflow-booking.vercel.app)
// still serves links emailed before Aug 21 2026. Its time labels must read the hour
// and minute of the slot - not the day and hour (it showed 9:00 AM on the 5th as "5:09 AM").
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const html = readFileSync(new URL('../../booking-app/index.html', import.meta.url), 'utf8')
const src = html.slice(html.indexOf('function fmt12'), html.indexOf('function fmtDateLong'))
const fmt12 = new Function(`${src}; return fmt12`)()

test('slot labels show the slot time', () => {
  assert.equal(fmt12('2026-11-05T09:00:00'), '9:00 AM')
  assert.equal(fmt12('2026-11-05T09:30:00'), '9:30 AM')
  assert.equal(fmt12('2026-11-05T12:00:00'), '12:00 PM')
  assert.equal(fmt12('2026-11-05T16:30:00'), '4:30 PM')
  assert.equal(fmt12('2026-12-31T00:15:00'), '12:15 AM')
})
