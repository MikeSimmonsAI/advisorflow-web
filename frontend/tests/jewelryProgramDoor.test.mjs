import { workspaceProgramDestination } from '../src/auth/workspaceProgramDoor.js'
import { experienceRedirect } from '../src/auth/programExperience.js'
import { programOf, programCards } from '../src/pages/program/programAccessModel.js'
import assert from 'node:assert/strict'
import test from 'node:test'

const br = { type: 'workspace', organization_id: 'o1', organization_name: 'Blue Ridge Gold & Jewelry',
             industry: 'jewelry', program_route: '/program/jewelry', experience: 'jewelry', is_program: false }

test('a server-routed gold & jewelry workspace opens the Gold & Jewelry app', () => {
  assert.equal(workspaceProgramDestination(br), '/program/jewelry')
})
test('never by name: a workspace called "Gold" with no program_route is not routed there', () => {
  assert.equal(workspaceProgramDestination({ ...br, program_route: null, experience: 'platform' }), null)
})
test('the launcher shows it as a Gold & Jewelry business workspace under its own name', () => {
  assert.equal(programOf(br), 'jewelry')
  const { visible } = programCards({ workspace_contexts: [br] })
  assert.equal(visible[0].display, 'Blue Ridge Gold & Jewelry')
  assert.match(visible[0].kind, /GOLD & JEWELRY/)
})
test('a jewelry login on a platform page is sent to its own program home', () => {
  const ctx = { workspace_contexts: [br] }
  assert.equal(experienceRedirect(ctx, '/dashboard', 'o1', 'o1', 'org_admin'), '/program/jewelry')
  assert.equal(experienceRedirect(ctx, '/program/jewelry/customers', 'o1', 'o1', 'org_admin'), null)
  assert.equal(experienceRedirect(ctx, '/workspaces', 'o1', 'o1', 'org_admin'), null)
})
