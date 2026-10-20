import { workspaceProgramDestination } from '../src/auth/workspaceProgramDoor.js'
import assert from 'node:assert/strict'
import test from 'node:test'

test('internal Roofing org enters the dedicated app even before feature setup', () => {
  assert.equal(workspaceProgramDestination({
    type: 'workspace', organization_slug: 'evosys-pro-roofing', is_program: false,
  }), '/program/roofing')
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
