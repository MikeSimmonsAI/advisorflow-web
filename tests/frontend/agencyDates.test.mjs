// Agency dates: calendar dates stay the date; timestamps are the viewer's local day.
import assert from 'node:assert/strict'
process.env.TZ = 'America/Chicago'
const { fmtDate, parseServerTime, relTime } = await import('../../frontend/src/pages/agency/agencyFormat.js')
let n = 0
const eq = (a, b) => { assert.equal(a, b); n++ }
eq(fmtDate('2026-10-03'), 'Oct 3, 2026')                       // date-only
eq(fmtDate('2026-10-03T12:00:00Z'), 'Oct 3, 2026')             // picker anchor
eq(fmtDate('2026-10-03T00:00:00'), 'Oct 3, 2026')              // naive midnight anchor
// The local-day checks need the process to actually be on Chicago time
// (process.env.TZ is honoured at runtime on Linux/macOS; not everywhere).
if (new Date('2026-10-03T02:15:00Z').getTimezoneOffset() === 300) {
  eq(fmtDate('2026-10-03T02:15:00Z'), 'Oct 2, 2026')           // 9:15pm Oct 2 in Chicago
  eq(fmtDate('2026-10-03T02:15:00'), 'Oct 2, 2026')            // naive = UTC on this platform
}
eq(fmtDate(null), '—')
eq(parseServerTime('2026-10-03T02:15:00').toISOString(), '2026-10-03T02:15:00.000Z')
eq(relTime('2026-10-01T08:00:00', Date.parse('2026-10-01T12:00:00Z')), '4h ago')
console.log(`agencyDates: ${n} passed`)
