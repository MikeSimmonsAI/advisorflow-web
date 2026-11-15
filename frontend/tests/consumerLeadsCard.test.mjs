import assert from 'node:assert/strict'
import test from 'node:test'
import { programCards } from '../src/pages/program/programAccessModel.js'

const volt = { type: 'volt_ops', organization_id: 'v1', organization_name: 'VOLT Marketing', access: 'owner', path: '/volt-ops/v1' }
const cl = { type: 'consumer_leads', organization_id: 'v1', organization_name: 'Consumer Leads', parent_name: 'VOLT Marketing',
  access: 'all', path: '/consumer-leads/v1' }

test('Consumer Leads gets its own card next to VOLT Marketing, only when the server grants it', () => {
  const { visible, others } = programCards({ volt_contexts: [volt], consumer_leads_contexts: [cl] })
  const all = [...visible, ...others]
  const v = all.find(c => c.type === 'volt_ops')
  const c = all.find(c => c.type === 'consumer_leads')
  assert.ok(v && c)
  assert.equal(c.display, 'Consumer Leads')
  assert.equal(c.path, '/consumer-leads/v1')
  assert.notEqual(c.organization_id, v.organization_id)          // distinct card keys
  assert.match(c.kind, /CONSUMER LEADS/)
  const none = programCards({ volt_contexts: [volt] })
  assert.ok(![...none.visible, ...none.others].some(x => x.type === 'consumer_leads'))
})

test('an agent card says it shows their assigned leads', () => {
  const { visible, others } = programCards({ consumer_leads_contexts: [{ ...cl, access: 'assigned' }] })
  const c = [...visible, ...others].find(x => x.type === 'consumer_leads')
  assert.equal(c.orgLabel, 'Your assigned leads')
})
