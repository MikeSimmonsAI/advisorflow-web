// The Deal Builders' "Back" action may only return to screens inside this app
// that can launch a builder. Everything else falls back to Program Access.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { safeReturn } from '../src/pages/builderReturn.js'

test('known launch screens are allowed, with a plain label', () => {
  assert.deepEqual(safeReturn('/workspaces'), { to: '/workspaces', label: 'Program Access' })
  assert.deepEqual(safeReturn('/sales/compensation'), { to: '/sales/compensation', label: 'Team Compensation' })
  assert.deepEqual(safeReturn('/sales/my-compensation?x=1'), { to: '/sales/my-compensation', label: 'My Compensation' })
  assert.deepEqual(safeReturn('/sales/opportunities/0addd3e4-8192-4d3f-a55f-21bf961bc408'),
    { to: '/sales/opportunities/0addd3e4-8192-4d3f-a55f-21bf961bc408', label: 'the deal' })
})

test('anything else falls back to Program Access', () => {
  for (const bad of [null, undefined, '', 'https://evil.example', '//evil.example', '/\\evil', 'javascript:alert(1)',
    '/god/pricing', '/sales/opportunities/../../god', '/sales/compensation/../x', '/workspaces/extra']) {
    assert.deepEqual(safeReturn(bad), { to: '/workspaces', label: 'Program Access' }, String(bad))
  }
})

test('both standalone builders draw the back bar outside the iframe', () => {
  const src = readFileSync(new URL('../src/pages/DealBuilders.jsx', import.meta.url), 'utf8')
  assert.equal((src.match(/<BuilderFrame title=/g) || []).length, 2)
  assert.match(src, /Back to \{back\.label\}/)
})
