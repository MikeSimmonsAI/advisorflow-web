import assert from 'node:assert/strict'
import test from 'node:test'
import { programCards, programOf } from '../src/pages/program/programAccessModel.js'
import { programEntry, experienceRedirect } from '../src/auth/programExperience.js'

const W = (id, name, extra) => ({ type: 'workspace', organization_id: id, organization_name: name, ...extra })
const all = {
  workspace_contexts: [
    W('evo', 'EVO Integrated Solutions LLC', { industry: 'generic', experience: 'wholesale' }),
    W('ins', 'Max Life Demo Agency (DEMO)', { industry: 'insurance', experience: 'insurance' }),
    W('rest', 'Restland Cemetery and Funeral Home', { industry: 'funeral', experience: 'platform' }),
    W('sci', 'Service Corporation International', { industry: 'funeral', is_program: true, experience: 'sci' }),
    W('roof', 'EvoSys Pro Roofing', { industry: 'roofing', program_route: '/program/roofing', experience: 'roofing' }),
    W('atl', 'Atlantis Light & Power', { industry: 'energy', experience: 'energy' }),
  ],
  product_contexts: [{ type: 'product', key: 'wholesale', organization_id: 'evo', path: '/workspace/evo?product=wholesale' }],
}

test('the launcher shows the six programs in one fixed order', () => {
  const { visible, others } = programCards(all)
  assert.deepEqual(visible.map(c => c.display),
    ['SCI', 'Atlantis Light & Power', 'Roofing', 'Wholesale', 'Restland Cemetery and Funeral Home', 'Insurance'])
  assert.deepEqual(visible.map(c => c.variant), ['sci', 'energy', 'roofing', 'wholesale', 'funeral', 'insurance'])
  assert.deepEqual(others, [])
  for (const c of visible) assert.ok(c.kind && c.summary && c.symbol, c.display)
})

test('Restland is its own program, never SCI', () => {
  assert.equal(programOf(all.workspace_contexts[2]), 'funeral')
  assert.equal(programOf(all.workspace_contexts[3]), 'sci')
})

test('only workspaces the server listed become cards', () => {
  const one = { workspace_contexts: [all.workspace_contexts[5]], product_contexts: [] }
  assert.deepEqual(programCards(one).visible.map(c => c.organization_id), ['atl'])
  assert.deepEqual(programCards({}).visible, [])
})

test('Insurance opens Max Life Command directly; Atlantis opens its own Overview', () => {
  assert.equal(programEntry(all.workspace_contexts[1]), '/agency')
  assert.equal(programEntry(all.workspace_contexts[5]), null)
  assert.equal(programEntry(all.workspace_contexts[2]), null)
  // an Atlantis page is never bounced into another program
  assert.equal(experienceRedirect(all, '/', 'atl', 'evo', 'org_admin'), null)
})
