// MOBILE ALERTS OPEN MOBILE SCREENS (2026-10-02).
// The agency phone home linked every alert to its DESKTOP page; a prospect now
// opens /m/prospects/:id and a lead /m/contacts/:id. Anything without a mobile
// screen keeps its desktop link rather than becoming a dead one.
import assert from 'node:assert/strict'
import { mobilePathFor } from '../../frontend/src/mobile/mobileHelpers.js'

let n = 0
const eq = (a, b) => { assert.equal(a, b); n++ }
eq(mobilePathFor('/agency/prospects/abc-123'), '/m/prospects/abc-123')
eq(mobilePathFor('/agency/prospects/abc-123?tab=copilot'), '/m/prospects/abc-123')
eq(mobilePathFor('/leads/L9'), '/m/contacts/L9')
eq(mobilePathFor('/agency/applications/A1'), '/agency/applications/A1')
eq(mobilePathFor('/agency/policies/P1'), '/agency/policies/P1')
eq(mobilePathFor(''), '/m')
eq(mobilePathFor(null), '/m')
console.log(`mobileRoutes: ${n} passed`)
