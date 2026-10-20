import assert from 'node:assert/strict'
import test from 'node:test'
import { programCards } from '../src/pages/program/programAccessModel.js'
import { workspaceProgramDestination, productWorkspaceDestination } from '../src/auth/workspaceProgramDoor.js'

const host = 'ffb18ae7-evo'
const roofing = 'edf0030f-roof'
const sci = '0a884078-sci'
const energy = '856b3a49-energy'
const ctx = {
  workspace_contexts: [
    { type: 'workspace', organization_id:host, organization_name:'EVO Integrated Solutions LLC', organization_slug:'evo-integrated-solutions-llc', industry:'generic' },
    { type: 'workspace', organization_id:sci, organization_name:'Service Corporation International', organization_slug:'service-corporation-international', is_program:true, industry:'funeral' },
    { type: 'workspace', organization_id:energy, organization_name:'Atlantis Light & Power', organization_slug:'atlantis-light-power', industry:'energy' },
    { type: 'workspace', organization_id:roofing, organization_name:'EvoSys Pro Roofing', organization_slug:'evosys-pro-roofing', industry:'generic' },
  ],
  product_contexts: [{ type:'product', key:'wholesale', label:'Wholesale Operations', organization_id:host, path:'/wholesale/evosense' }],
}

test('shared admin sees four business experiences, not generic host org', () => {
  const { visible, others } = programCards(ctx)
  assert.deepEqual(visible.map(x => x.display), ['SCI', 'Atlantis Light & Power', 'Roofing', 'Wholesale'])
  assert.deepEqual(others, [])
})

test('unentitled product cannot be invented by the client', () => {
  const without = { workspace_contexts:ctx.workspace_contexts, product_contexts:[] }
  assert.equal(programCards(without).visible.some(x => x.type === 'product'), false)
  assert.equal(productWorkspaceDestination(without, host, 'wholesale'), null)
})

test('products only open from the exact authorized host workspace', () => {
  assert.equal(productWorkspaceDestination(ctx, host, 'wholesale'), '/wholesale/evosense')
  assert.equal(productWorkspaceDestination(ctx, sci, 'wholesale'), null)
  assert.equal(productWorkspaceDestination(ctx, roofing, 'wholesale'), null)
  assert.equal(productWorkspaceDestination(ctx, host, 'made-up'), null)
})

test('program routing preserves SCI while separating roofing', () => {
  assert.equal(workspaceProgramDestination(ctx.workspace_contexts[1]), '/program')
  assert.equal(workspaceProgramDestination(ctx.workspace_contexts[3]), '/program/roofing')
  assert.equal(workspaceProgramDestination(ctx.workspace_contexts[2]), null)
})

test('generic workspaces remain available when no named product uses them', () => {
  const without = { workspace_contexts:ctx.workspace_contexts, product_contexts:[] }
  assert.equal(programCards(without).others.length, 1)
  assert.equal(programCards(without).others[0].organization_id, host)
})
