import assert from 'node:assert/strict'
import test from 'node:test'
import { experienceRedirect } from '../src/auth/programExperience.js'

const evo = 'evo', sci = 'sci', atl = 'atl', roof = 'roof'
const ctx = { workspace_contexts: [
  { organization_id: evo, experience: 'wholesale' },
  { organization_id: sci, experience: 'sci', is_program: true },
  { organization_id: atl, experience: 'energy' },
  { organization_id: roof, experience: 'roofing' },
] }

test('a Wholesale workspace never draws the general platform', () => {
  for (const p of ['/', '/overview', '/leads', '/campaigns', '/settings', '/ai-hub', '/reports', '/workspace/evo'])
    assert.equal(experienceRedirect(ctx, p, evo, evo, 'org_admin'), '/wholesale/evosense', p)
  for (const p of ['/wholesale', '/wholesale/evosense', '/wholesale/team', '/change-password', '/workspaces'])
    assert.equal(experienceRedirect(ctx, p, evo, evo, 'org_admin'), null, p)
})
test('home org is used when nothing is selected', () => {
  assert.equal(experienceRedirect(ctx, '/leads', null, evo, 'org_admin'), '/wholesale/evosense')
})
test('SCI and Roofing keep their own shells; Atlantis keeps its workspace screens', () => {
  assert.equal(experienceRedirect(ctx, '/leads', sci, evo, 'org_admin'), '/program')
  assert.equal(experienceRedirect(ctx, '/program/live', sci, evo, 'org_admin'), null)
  assert.equal(experienceRedirect(ctx, '/wholesale/evosense', sci, evo, 'org_admin'), '/program')
  assert.equal(experienceRedirect(ctx, '/leads', roof, evo, 'org_admin'), '/program/roofing')
  assert.equal(experienceRedirect(ctx, '/leads', atl, evo, 'org_admin'), null)
})
test('internal administration is never redirected', () => {
  assert.equal(experienceRedirect(ctx, '/leads', evo, evo, 'god_admin'), null)
  assert.equal(experienceRedirect(ctx, '/leads', evo, evo, 'super_admin'), null)
  assert.equal(experienceRedirect(null, '/leads', evo, evo, 'org_admin'), null)
})
