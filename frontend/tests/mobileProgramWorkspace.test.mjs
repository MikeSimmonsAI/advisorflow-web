import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { ago, countLabel, findProgram, mobileKind, mobileProgramPath, opensDirect, programId, programRequest,
         programTabs, whenLabel } from '../src/teamapp/programMobile.js'
import { safeAppPath } from '../src/teamapp/teamHelpers.js'
import { launcherOrigin, safeOrigin, setLauncherOrigin } from '../src/pages/program/launcherOrigin.js'
import { programInventory, programKey } from '../src/pages/program/programAccessModel.js'

const CTX = {
  workspace_contexts: [
    { organization_id: 'org-sci', organization_name: 'Service Corporation International', is_program: true },
    { organization_id: 'org-rl', organization_name: 'Restland Cemetery and Funeral Home', is_program: true,
      program_is_sci: false, industry: 'funeral' },
    { organization_id: 'org-roof', organization_name: 'Roof Co', program_route: '/program/roofing' },
  ],
  has_back_office: true, back_office_path: '/sales', back_office_destinations: [],
}
const ALL = programInventory(CTX, null, '/app/programs')
const byOrg = id => ALL.find(i => i.organization_id === id)

test('every program has a link that survives sign-in and finds the same entry again', () => {
  for (const item of ALL) {
    const p = mobileProgramPath(item)
    assert.match(p, /^\/app\/p\/[A-Za-z0-9_-]+$/)
    assert.equal(safeAppPath(p), p)                                    // the ?next= round trip keeps it
    assert.equal(safeAppPath(p + '/replies'), p + '/replies')
    assert.equal(programKey(findProgram(ALL, programId(item))), programKey(item))
  }
})

test('a link to a program this login cannot open finds nothing', () => {
  const stranger = programId({ type: 'workspace', organization_id: 'org-not-mine' })
  assert.equal(findProgram(ALL, stranger), null)
  assert.equal(findProgram(ALL, '%%%garbled'), null)
  assert.equal(findProgram([], programId(byOrg('org-sci'))), null)
})

test('what each program gets on the phone', () => {
  assert.equal(mobileKind(byOrg('org-sci')), 'program')
  assert.equal(mobileKind(byOrg('org-rl')), 'program')                 // Restland: same program shell
  assert.equal(mobileKind(byOrg('org-roof')), 'other')
  assert.equal(mobileKind(ALL.find(i => i.type === 'back_office')), 'sales')
  assert.deepEqual(programTabs(byOrg('org-sci')).map(t => t.label), ['Dashboard', 'Replies', 'More'])
  assert.deepEqual(programTabs(byOrg('org-roof')).map(t => t.label), ['Dashboard', 'More'])   // no dead Replies tab
  assert.equal(opensDirect({ type: 'preview' }), true)
  assert.equal(opensDirect({ type: 'deal_builder' }), true)
  assert.equal(opensDirect(byOrg('org-sci')), false)
})

test('each program asks for its own workspace', () => {
  assert.deepEqual(programRequest(byOrg('org-rl')), { skipRedirect: true, asCustomer: 'org-rl', workspaceId: 'org-rl' })
})

test('Open Full Workspace returns to the phone page it came from, and only to a real one', () => {
  const page = mobileProgramPath(byOrg('org-sci'), 'more')
  assert.equal(safeOrigin(page), page)
  assert.equal(safeOrigin(mobileProgramPath(byOrg('org-sci'))), mobileProgramPath(byOrg('org-sci')))
  assert.equal(safeOrigin('/app/programs'), '/app/programs')
  for (const bad of ['/app/p/x/settings', '/app/p/../god', 'https://evil.example/app/p/x', '/god', '/app/p/a b'])
    assert.equal(safeOrigin(bad), null)
  const mem = new Map()
  const s = { setItem: (k, v) => mem.set(k, v), getItem: k => mem.get(k) ?? null, removeItem: k => mem.delete(k) }
  setLauncherOrigin(page, s)
  assert.equal(launcherOrigin(s), page)
})

test('card count and times', () => {
  assert.equal(countLabel(0), null)
  assert.equal(countLabel(3), '3')
  assert.equal(countLabel(250), '99+')
  const now = new Date('2026-11-16T15:00:00Z')                          // 9:00 AM Central
  assert.equal(whenLabel('2026-11-16T20:30:00Z', 'America/Chicago', now), 'Today 2:30 PM')
  assert.equal(whenLabel('2026-11-17T15:00:00Z', 'America/Chicago', now), 'Tomorrow 9:00 AM')
  assert.equal(ago('2026-11-16T14:55:00Z', now.getTime()), '5m')
  assert.equal(ago('2026-11-16T12:00:00Z', now.getTime()), '3h')
})

test('the app routes each phone page and hides the tabs only inside a conversation', () => {
  const app = readFileSync(new URL('../src/teamapp/TeamApp.jsx', import.meta.url), 'utf8')
  for (const r of ['p/:pid', 'p/:pid/replies', 'p/:pid/replies/:leadId', 'p/:pid/more'])
    assert.ok(app.includes('path="' + r + '"'), r)
  assert.ok(app.includes('\\/app\\/p\\/[^/]+\\/replies\\/[^/]+'))
  const ws = readFileSync(new URL('../src/teamapp/ProgramWorkspace.jsx', import.meta.url), 'utf8')
  assert.ok(ws.includes("'/program/command/reply'"))                    // the existing send path, unchanged
  assert.ok(ws.includes('Open Full Workspace'))
})
