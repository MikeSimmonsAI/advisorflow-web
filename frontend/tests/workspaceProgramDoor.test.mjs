import { workspaceProgramDestination } from '../src/auth/workspaceProgramDoor.js'
import assert from 'node:assert/strict'
import test from 'node:test'

test('a Roofing org whose program is NOT switched on never opens the Roofing app by its name', () => {
  // prod 2026-10-20: routing by slug sent wholesale@ to a dead "isn't turned on" screen
  assert.equal(workspaceProgramDestination({
    type: 'workspace', organization_slug: 'evosys-pro-roofing', is_program: false, program_route: null,
  }), null)
})
test('any explicitly configured roofing-industry program uses Mission Control', () => {
  assert.equal(workspaceProgramDestination({
    type: 'workspace', organization_slug: 'josh-roofing', program_route: '/program/roofing',
  }), '/program/roofing')
})
test('SCI retains its existing Family Service Center route', () => {
  assert.equal(workspaceProgramDestination({
    type: 'workspace', organization_slug: 'service-corporation-international', is_program: true,
  }), '/program')
})
test('Atlantis and Wholesale keep their generic workspace homes', () => {
  assert.equal(workspaceProgramDestination({ type: 'workspace', organization_slug: 'atlantis-light-power' }), null)
  assert.equal(workspaceProgramDestination({ type: 'workspace', organization_slug: 'evo-wholesale' }), null)
})
test('never redirect unrelated server contexts or unlisted entries', () => {
  assert.equal(workspaceProgramDestination(null), null)
  assert.equal(workspaceProgramDestination({ type: 'platform', program_route: '/program/roofing' }), null)
  assert.equal(workspaceProgramDestination({ type: 'workspace', program_route: 'https://example.com/' }), null)
})
