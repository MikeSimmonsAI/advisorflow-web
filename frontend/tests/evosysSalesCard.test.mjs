// EvoSys BookIt gets its own Program Access card (owner request 2026-11-16),
// including for the owner login whose first destination is the console.
import test from 'node:test'
import assert from 'node:assert/strict'
import { programCards, isEvoSysSales } from '../src/pages/program/programAccessModel.js'

const evo = { type: 'back_office', brand_sales_org_id: '365a', label: 'EvoSys Pro Sales', platform_id: 'p1',
              platform_name: 'EvoSys Pro', platform_slug: 'evosyspro', role: 'sales_manager', path: '/sales' }

test('the EvoSys sales team shows as an EvoSys BookIt card that opens its own program', () => {
  const r = programCards({ has_back_office: true, back_office_path: '/sales', back_office_destinations: [evo] })
  const c = r.visible.find(x => x.type === 'back_office' && x.program === 'evosys_sales')
  assert.equal(c.display, 'EvoSys BookIt')
  assert.equal(c.path, '/program/evosys-sales')
  assert.equal(c.kind, 'EVOSYS PRO · BOOKIT')
})

test('the owner login (console first) still gets the EvoSys BookIt card, never a console card', () => {
  const r = programCards({ has_back_office: true, back_office_path: '/god', back_office_destinations: [evo] })
  const c = r.visible.filter(x => x.type === 'back_office')
  assert.equal(c.length, 2)
  assert.deepEqual(c.map(x => x.path).sort(), ['/program/evosys-business-development', '/program/evosys-sales'])
  assert.ok(c.every(x => x.path !== '/god'))
})

test('no sales membership, no card; the console alone never becomes one', () => {
  assert.equal(programCards({ has_back_office: true, back_office_path: '/god', back_office_destinations: [] })
    .visible.filter(x => x.type === 'back_office').length, 0)
  assert.equal(programCards({}).visible.length, 0)
})

test('other brands and the demonstration team keep their Back Office naming', () => {
  assert.equal(isEvoSysSales({ label: 'BookaBoost Sales', platform_slug: 'bookaboost' }), false)
  assert.equal(isEvoSysSales({ label: 'EvoSys Pro Sales (Demonstration)', platform_slug: 'evosyspro' }), false)
  const r = programCards({ has_back_office: true, back_office_path: '/sales', back_office_destinations: [
    evo, { ...evo, brand_sales_org_id: 'bb', label: 'BookaBoost Sales', platform_name: 'BookaBoost', platform_slug: 'bookaboost', platform_id: 'p2' }] })
  assert.deepEqual(r.visible.filter(x => x.type === 'back_office').map(x => x.display).sort(), ['Back Office', 'EvoSys BookIt', 'EvoSys Business Development'])
})

test('side by side: EvoSys Business Development is a second card on the same team', () => {
  const r = programCards({ has_back_office: true, back_office_path: '/sales', back_office_destinations: [evo] })
  const c = r.visible.filter(x => x.type === 'back_office')
  assert.deepEqual(c.map(x => x.display).sort(), ['EvoSys BookIt', 'EvoSys Business Development'])
  const bd = c.find(x => x.program === 'evosys_bd')
  assert.equal(bd.path, '/program/evosys-business-development')
  assert.equal(bd.summary, 'Add contacts, run outreach, and book business development calls.')
  assert.notEqual(bd.organization_id, c.find(x => x.program === 'evosys_sales').organization_id)
})
