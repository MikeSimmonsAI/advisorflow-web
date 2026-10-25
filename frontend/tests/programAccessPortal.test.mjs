import assert from 'node:assert/strict'
import test from 'node:test'
import { programCards, previewCards, previewName, businessKind } from '../src/pages/program/programAccessModel.js'
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
    // program_route comes from the SERVER, only when roofing_program is switched on
    { type: 'workspace', organization_id:roofing, organization_name:'EvoSys Pro Roofing', organization_slug:'evosys-pro-roofing', industry:'roofing', program_route:'/program/roofing' },
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

test('a Roofing workspace whose program is not switched on is not offered as the Roofing app', () => {
  const off = { ...ctx.workspace_contexts[3], industry: 'generic', program_route: null }
  const c = { workspace_contexts: [ctx.workspace_contexts[0], ctx.workspace_contexts[1], ctx.workspace_contexts[2], off],
              product_contexts: ctx.product_contexts }
  const { visible, others } = programCards(c)
  assert.equal(visible.some(x => x.display === 'Roofing'), false)
  // still offered, under its own name in the neutral look - not as the Roofing app
  const card = visible.find(x => x.organization_id === roofing)
  assert.equal(card.variant, 'general')
  assert.equal(card.display, 'EvoSys Pro Roofing')
  assert.deepEqual(others, [])
  assert.equal(workspaceProgramDestination(off), null)
})

test('generic workspaces remain available when no named product uses them', () => {
  const without = { workspace_contexts:ctx.workspace_contexts, product_contexts:[] }
  const general = programCards(without).visible.filter(x => x.variant === 'general')
  assert.deepEqual(general.map(x => x.organization_id), [host])
  assert.equal(general[0].display, 'EVO Integrated Solutions LLC')
})


test('a general client keeps its real name and its own kind of business', () => {
  const c = { workspace_contexts: [
    { type: 'workspace', organization_id: 'ccb', organization_name: 'Commercial Cleaning Blueprint', organization_slug: 'commercial-cleaning-blueprint', industry: 'cleaning' },
    { type: 'workspace', organization_id: 'x', organization_name: 'Some New Client', organization_slug: 'snc', industry: 'something-new' },
  ] }
  const { visible } = programCards(c)
  const ccb = visible.find(x => x.organization_id === 'ccb')
  assert.equal(ccb.display, 'Commercial Cleaning Blueprint')
  assert.equal(ccb.kind, 'COMMERCIAL CLEANING')
  // An industry the launcher has no label for still gets a full card with its own name.
  const other = visible.find(x => x.organization_id === 'x')
  assert.equal(other.display, 'Some New Client')
  assert.equal(other.kind, businessKind('unknown'))
})

test('previews are separate, labelled, one per deal, and never invented', () => {
  const pc = { preview_contexts: [
    { type: 'preview', opportunity_id: 'o1', company_name: 'Countryside Land Partners', contact_name: 'Samuel St.Germain', stage_label: 'Demo / Proposal', proposal_sent: true, demo_slot: 'platform', url: 'https://app.example.test/demo/abc' },
    { type: 'preview', opportunity_id: 'o1', company_name: 'Countryside Land Partners', url: 'https://app.example.test/demo/dup' },
    { type: 'preview', opportunity_id: 'o2', company_name: 'Commercial Cleaning Blueprint — Daniel Almaguer', contact_name: 'Daniel Almaguer', stage_label: 'Demo Build', demo_slot: 'platform', url: 'https://app.example.test/demo/def' },
    { type: 'preview', opportunity_id: 'o3', company_name: 'No Link Co' },
  ] }
  const { visible, previews } = programCards(pc)
  assert.equal(visible.length, 0, 'a preview is never a workspace card')
  assert.deepEqual(previews.map(p => p.display), ['Commercial Cleaning Blueprint', 'Countryside Land Partners'])
  assert.ok(previews.every(p => p.kind.startsWith('PREVIEW')))
  assert.deepEqual(previews.find(p => p.opportunity_id === 'o1').facts, ['Demo / Proposal', 'Proposal sent', 'Platform demo'])
  assert.equal(previews.find(p => p.opportunity_id === 'o1').url, 'https://app.example.test/demo/abc')
  assert.deepEqual(previewCards({}), [])
})

test('preview name drops only an exact contact suffix', () => {
  assert.equal(previewName({ company_name: 'Acme — Jo Smith', contact_name: 'Jo Smith' }), 'Acme')
  assert.equal(previewName({ company_name: 'Acme — Sales', contact_name: 'Jo Smith' }), 'Acme — Sales')
  assert.equal(previewName({ company_name: 'Acme' }), 'Acme')
})
