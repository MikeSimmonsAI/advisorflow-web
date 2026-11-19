import assert from 'node:assert/strict'
import test from 'node:test'
import { programCards } from '../src/pages/program/programAccessModel.js'
import { categoryOf, summaryCounts, shortLine } from '../src/pages/program/v2/commandCenterModel.js'

const cg = { type: 'client_growth', organization_id: 'client-growth', organization_name: 'EvoSys Pro', path: '/client-growth', access: 'staff' }

test('Client Growth gets its own card only when the server lists it', () => {
  const { visible, others } = programCards({ client_growth_contexts: [cg] })
  const c = [...visible, ...others].find(x => x.type === 'client_growth')
  assert.ok(c)
  assert.equal(c.display, 'Client Growth')
  assert.equal(c.summary, 'Discovery • Questionnaires • Proposals')
  assert.equal(c.actionLabel, 'Open workspace')
  assert.equal(c.path, '/client-growth')
  assert.equal(categoryOf(c), 'sales')
  assert.equal(shortLine(c), 'Discovery • Questionnaires • Proposals')
  const none = programCards({})
  assert.ok(![...none.visible, ...none.others].some(x => x.type === 'client_growth'))
})

test('Client Growth is not counted as a client organization', () => {
  const { visible } = programCards({ client_growth_contexts: [cg] })
  assert.equal(summaryCounts(visible, [], []).clients, 0)
})
