// Issue #499: an insurance workspace with the retention workspace switched on
// opens /program/insurance; nothing else is routed there by name or industry.
import test from 'node:test'
import assert from 'node:assert/strict'
import { workspaceProgramDestination } from '../../frontend/src/auth/workspaceProgramDoor.js'

test('retention-enabled insurance workspace opens the insurance shell', () => {
  assert.equal(workspaceProgramDestination({ type: 'workspace', program_route: '/program/insurance' }), '/program/insurance')
})

test('industry alone never routes to the insurance shell', () => {
  assert.equal(workspaceProgramDestination({ type: 'workspace', industry: 'insurance', program_route: null }), null)
})
