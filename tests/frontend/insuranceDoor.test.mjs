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

import { programCards } from '../../frontend/src/pages/program/programAccessModel.js'

test('retention workspace gets its own card identity; Max Life keeps the insurance card', () => {
  const ctx = { workspace_contexts: [
    { type: 'workspace', organization_id: 'ml', organization_name: 'Max Life Demo Agency (DEMO)', industry: 'insurance',
      experience: 'insurance', program_route: null, presentation: null },
    { type: 'workspace', organization_id: 'vg', organization_name: 'Vincent Greene Agency', industry: 'insurance',
      experience: 'insurance', program_route: '/program/insurance',
      presentation: { kind: 'insurance_retention', title: 'Vincent Greene Agency', eyebrow: 'Allstate agency project',
        subtitle: 'Renewal & Retention', description: 'Proactive renewal follow-up.', chips: ['Renewals', 'Client care', 'Team & locations'],
        button: 'Open agency', sample: true } },
  ] }
  const cards = programCards(ctx).visible
  const vg = cards.find(c => c.organization_id === 'vg')
  const ml = cards.find(c => c.organization_id === 'ml')
  assert.equal(vg.variant, 'retention')
  assert.equal(vg.display, 'Vincent Greene Agency')
  assert.equal(vg.kind, 'Allstate agency project')
  assert.equal(vg.subtitle, 'Renewal & Retention')
  assert.equal(vg.actionLabel, 'Open agency')
  assert.equal(vg.badge, 'Sample data')
  assert.deepEqual(vg.facts, ['Renewals', 'Client care', 'Team & locations'])
  assert.equal(ml.variant, 'insurance')
  assert.equal(ml.display, 'Insurance')
  assert.notEqual(vg.variant, ml.variant)
})
