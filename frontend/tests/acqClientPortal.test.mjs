// P0 2026-11-15: "Open Client Portal" for an EvoSys Acquisition admin (not God).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = f => readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8')

// The selection logic itself, lifted out of client.js and run as-is.
function loadOverride(store) {
  const s = read('api/client.js')
  const body = s.slice(s.indexOf('export function getAcqClientView()'), s.indexOf('export function clearOrgContext()'))
    .replace(/export function/g, 'function')
  const localStorage = { getItem: k => (k in store ? store[k] : null) }
  // eslint-disable-next-line no-new-func
  return new Function('localStorage', `const ACQ_CLIENT_KEY='af_acq_client_view';const ACQ_PORTAL='/program/acquisition';${body};return acqClientOverride`)(localStorage)
}

test('the admin view is sent only from the client portal screens', () => {
  const f = loadOverride({ af_acq_client_view: JSON.stringify({ orgId: 'org-a', orgName: 'A' }) })
  assert.equal(f('/program/acquisition'), 'org-a')
  assert.equal(f('/program/acquisition/ops/team'), 'org-a')
  for (const p of ['/wholesale', '/acquisition-ops/clients', '/god', '/program/acquisitionX', '/workspaces'])
    assert.equal(f(p), null, p)
  assert.equal(f('/program/acquisition', { asCustomer: 'org-b' }), null, 'an explicit customer wins')
  assert.equal(loadOverride({})('/program/acquisition'), null, 'nothing selected, nothing sent')
})

test('Open Client Portal: owner through God Mode, Acquisition admin through its own entry', () => {
  const h = read('pages/acquisition/HubPages.jsx')
  assert.ok(h.includes('if (clients?.global_owner) await m.enterCustomer(c.organization_id, c.name)'))
  assert.ok(h.includes('else await m.enterAcquisitionClient(c.organization_id, c.name)'))
  const e = read('pages/god/enterCustomer.js')
  assert.ok(e.includes("api.post('/acquisition-ops/' + orgId + '/client-portal', {})"))
  assert.ok(e.includes('r.memberships_before !== r.memberships_after'), 'refuses if a membership was granted')
  assert.ok(e.includes("api.post('/acquisition-ops/client-portal/exit'"))
})

test('the portal says it is an admin view and offers the way back', () => {
  const s = read('pages/acquisition/AcquisitionShell.jsx')
  assert.ok(s.includes('Return to Acquisition Control Center'))
  assert.ok(s.includes("window.location.href = '/acquisition-ops/clients'"))
  assert.ok(/clearObservationContext\(\)\r?\n  clearAcqClientView\(\)\r?\n\}/.test(read('api/client.js')), 'sign-out clears it')
})
