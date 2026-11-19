// Service Operations (#452/#506): the portal only grows when the server says
// so; workers and buyers get their own simple views; nothing fakes a number.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = f => readFileSync(new URL(`../src/pages/acquisition/${f}`, import.meta.url), 'utf8')

test('menu items appear only when Service Operations is on and the role allows them', () => {
  const s = src('AcquisitionShell.jsx')
  assert.ok(s.includes("api.get('/service-ops/me')"))
  assert.ok(s.includes('ops?.ops_enabled && ops?.role ? OPS_NAV.filter(n => n.any.some(p => ops.perms.includes(p))) : []'),
    'no Service Operations menu when the feature is off')
  // 2026-11-18 design: the six main pages stay together; Service Operations
  // pages sit under "More tools" (only when switched on for this role).
  assert.ok(s.includes("const main = opsOnly ? [] : NAV") && s.includes('main.map('), 'existing menu kept intact')
  assert.ok(s.includes('opsNav.map(') && s.includes("t('nav_more')"), 'Service Operations under More tools')
})

test('field staff and buyers are routed to their own views, never the sales portal', () => {
  const s = src('AcquisitionShell.jsx')
  assert.ok(s.includes('ops?.is_buyer') && s.includes('`${BASE}/customer`'))
  assert.ok(s.includes("WORKER_ROLES = ['field_worker', 'team_lead']") && s.includes('`${BASE}/work`'))
  const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  for (const r of ['/program/acquisition/work"', '/program/acquisition/work/jobs/:id"', '/program/acquisition/customer"', 'path="ops/jobs/:id"'])
    assert.ok(app.includes(r), r)
})

test('worker view: only assigned jobs, private photos, checklist gate before completion', () => {
  const w = src('WorkerApp.jsx')
  assert.ok(w.includes("/service-ops/jobs?mine=true"))
  assert.ok(w.includes('fetchObjectUrl(`/service-ops/jobs/${jid}/photos/${a.id}`)'), 'photos fetched with auth, never a public URL')
  assert.ok(w.includes('disabled={busy || missing > 0}'), 'complete blocked until required items are ticked')
  assert.ok(w.includes('capture="environment"'), 'phone camera upload')
})

test('payments: reported is not paid; invites hand back a link and send nothing', () => {
  const o = src('ServiceOps.jsx')
  assert.ok(o.includes('Record (reported)') && o.includes('Confirm received'))
  assert.ok(o.includes('nothing was emailed') && o.includes('EvoSys didn’t email anyone'))
  assert.ok(o.includes('Suggestions only — nothing here acts on its own.'))
})

test('EvoSys switches live in the company area of the Acquisition Control Center and call the real API', () => {
  const a = src('ServiceOpsAdmin.jsx')
  assert.ok(a.includes('api.put(`/acquisition-ops/${orgId}/service-ops`'))
  assert.ok(a.includes('confirm_upcoming: true') && a.includes('Turn off, keep everything'))
  assert.ok(a.includes('cap.blocked_reason') && a.includes('cap.last_change'))
  const ops = src('AcquisitionOps.jsx')
  assert.ok(ops.includes("{ to: `${OPS}/service-ops`, label: 'Service Operations'"))
  const hub = src('HubPages.jsx')
  const dir = hub.slice(hub.indexOf('export function ClientDirectory'))
  assert.ok(dir.includes('/god/acquisition/service-ops/states') && dir.includes('nav(`${OPS}/service-ops`)'))
})

test('team page manages logins without God Mode: new link, turn off', () => {
  const o = src('ServiceOps.jsx')
  assert.ok(o.includes('/service-ops/team/${id}/setup-link') && o.includes('/service-ops/buyers/${b.id}/setup-link'))
  assert.ok(o.includes("Off — can’t sign in"))
})
