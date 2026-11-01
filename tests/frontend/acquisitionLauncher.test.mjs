// Issue #452 - the managed-acquisition client opens her portal, stays inside
// it, and her launcher card keeps her business's own identity.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { workspaceProgramDestination } from '../../frontend/src/auth/workspaceProgramDoor.js'
import { experienceRedirect } from '../../frontend/src/auth/programExperience.js'
import { programOf, programCards } from '../../frontend/src/pages/program/programAccessModel.js'

const ws = { type: 'workspace', organization_id: 'o1', organization_name: 'Shine Test Cleaning',
             industry: 'cleaning', program_route: '/program/acquisition', experience: 'acquisition' }

test('the server-set route opens the acquisition portal', () => {
  assert.equal(workspaceProgramDestination(ws), '/program/acquisition')
  assert.equal(workspaceProgramDestination({ ...ws, program_route: null, experience: 'platform' }), null)
})

test('the client is kept inside her portal; god is not redirected', () => {
  const ctx = { workspace_contexts: [ws] }
  assert.equal(experienceRedirect(ctx, '/dashboard', 'o1', 'o1', 'org_admin'), '/program/acquisition')
  assert.equal(experienceRedirect(ctx, '/program/acquisition/settings', 'o1', 'o1', 'org_admin'), null)
  assert.equal(experienceRedirect(ctx, '/dashboard', 'o1', 'o1', 'god_admin'), null)
})

test('an SCI workspace does not treat the acquisition portal as its own', () => {
  const ctx = { workspace_contexts: [{ ...ws, experience: 'sci', program_route: null }] }
  assert.equal(experienceRedirect(ctx, '/program/acquisition', 'o1', 'o1', 'org_admin'), '/program')
})

test('the card is a cleaning card titled with the business name', () => {
  assert.equal(programOf(ws), 'cleaning')
  const card = programCards({ workspace_contexts: [ws] }).visible[0]
  assert.equal(card.display, 'Shine Test Cleaning')
  assert.equal(card.program_route, '/program/acquisition')
})
