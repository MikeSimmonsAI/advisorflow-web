import assert from 'node:assert/strict'
import test from 'node:test'
import { ACCENTS, addDays, bodyPieces, fmtTime, insertMention, localYmd, matchesProgram,
         mentionedIds, mentionMatches, mentionQuery, mergeMessages, programStyle, safeAppPath, tabsFor,
         toLocalInput } from '../src/teamapp/teamHelpers.js'
import { programCards, programInventory, programKey, salesToolCards } from '../src/pages/program/programAccessModel.js'

test('times display in the EvoSys zone, across daylight saving', () => {
  // 2026-11-01 06:30Z is 1:30 AM CDT; 07:30Z is 1:30 AM CST (clocks fell back).
  assert.equal(fmtTime('2026-11-01T06:30:00Z', 'America/Chicago'), '1:30 AM')
  assert.equal(fmtTime('2026-11-01T07:30:00Z', 'America/Chicago'), '1:30 AM')
  assert.equal(fmtTime('2026-11-16T16:24:00Z', 'America/Chicago'), '10:24 AM')
  assert.equal(localYmd('2026-11-17T04:30:00Z', 'America/Chicago'), '2026-11-16')
  assert.equal(toLocalInput('2026-11-10T18:00:00Z', 'America/Chicago'), '2026-11-10T12:00')
  assert.equal(addDays('2026-10-26', 7), '2026-11-02')
})

test('@mentions', () => {
  const people = [{ id: 'a', name: 'Mike Schlueter' }, { id: 'b', name: 'Rep One' }]
  const q = mentionQuery('Thanks @mi', 10)
  assert.deepEqual(q, { query: 'mi', start: 7 })
  assert.deepEqual(mentionMatches(people, 'mi').map(p => p.id), ['a'])
  assert.deepEqual(mentionMatches(people, 'sch').map(p => p.id), ['a'])
  const text = insertMention('Thanks @mi', q, people[0])
  assert.equal(text, 'Thanks @Mike Schlueter ')
  assert.deepEqual(mentionedIds(text, people), ['a'])
  assert.deepEqual(mentionedIds('Thanks', people), [])
  assert.equal(mentionQuery('email me@x', 10), null)
  const pieces = bodyPieces('Hi @Rep One and @Mike Schlueter!', ['Rep One', 'Mike Schlueter'])
  assert.deepEqual(pieces, [{ text: 'Hi ' }, { mention: 'Rep One' }, { text: ' and ' }, { mention: 'Mike Schlueter' }, { text: '!' }])
})

test('polled messages merge without duplicates, in order', () => {
  const a = { id: '1', created_at: '2026-11-16T10:00:00Z' }
  const b = { id: '2', created_at: '2026-11-16T10:01:00Z' }
  assert.deepEqual(mergeMessages([a], [b, a]).map(m => m.id), ['1', '2'])
})

test('tabs: Programs, Chat, Activity, Account - no Time', () => {
  assert.deepEqual(tabsFor({}).map(t => t.key), ['programs', 'activity', 'account'])
  assert.deepEqual(tabsFor({ chat: true, time: true }).map(t => t.key), ['programs', 'chat', 'activity', 'account'])
  assert.ok(!tabsFor({ chat: true }).some(t => /time/i.test(t.key + t.label + t.to)))
})

test('program look: approved accents, readable icon for every entry', () => {
  assert.equal(programStyle({ program: 'sci', display: 'SCI' }).accent, '#4F46E5')
  assert.equal(programStyle({ program: 'funeral', display: 'Restland Cemetery and Funeral Home' }).accent, '#087F68')
  assert.equal(programStyle({ program: 'business', display: 'Restland' }).accent, '#087F68')
  assert.equal(programStyle({ program: 'volt', display: 'VOLT Marketing' }).accent, '#C62839')
  assert.equal(programStyle({ program: 'cleaning', display: 'Commercial Cleaning Blueprint' }).accent, '#7C3AED')
  assert.equal(programStyle({ program: 'back_office', display: 'Back Office' }).accent, '#475569')
  assert.equal(programStyle({ type: 'deal_builder', key: 'sales', display: 'Salesperson Deal Builder' }).accent, '#2563EB')
  assert.equal(programStyle({ program: 'business', display: 'Sales Workspace' }).accent, '#2563EB')
  assert.equal(programStyle({ program: 'mystery', display: 'Something New' }).accent, ACCENTS.fallback)
  for (const p of ['sci', 'funeral', 'back_office', 'executive', 'volt', 'cleaning', 'roofing', 'energy', 'wholesale', 'insurance', 'jewelry', 'preview', 'business', 'x'])
    assert.ok(programStyle({ program: p, display: p }).icon)
})

test('search narrows the full list and clearing restores it', () => {
  const list = [{ display: 'SCI', kind: 'SCI · FAMILY SERVICES' }, { display: 'Restland', orgLabel: 'Restland Cemetery' }, { display: 'Back Office' }]
  assert.equal(list.filter(i => matchesProgram(i, 'rest')).length, 1)
  assert.equal(list.filter(i => matchesProgram(i, 'FAMILY')).length, 1)
  assert.equal(list.filter(i => matchesProgram(i, '')).length, 3)
  assert.equal(list.filter(i => matchesProgram(i, '   ')).length, 3)
})

test('mobile inventory = every desktop entry (cards, sales tools, others, previews), no cap', () => {
  const ws = Array.from({ length: 15 }, (_, i) => ({ organization_id: 'org' + i, organization_name: 'Business ' + i, industry: 'generic' }))
  const ctx = { workspace_contexts: ws, has_back_office: true, back_office_destinations: [{ path: '/sales' }],
                preview_contexts: [{ deal_id: 'd1', company_name: 'Preview Co', url: 'https://example.com' }],
                sales_team: { my_team: true } }
  const deal = { executive: true, sales: true }
  const inv = programInventory(ctx, deal, '/app/programs')
  const { visible, others, previews } = programCards(ctx)
  const desktopKeys = [...visible, ...salesToolCards(ctx, deal), ...others, ...previews].map(programKey)
  assert.deepEqual(inv.map(programKey), desktopKeys)
  assert.equal(inv.filter(i => (i.type || 'workspace') === 'workspace' && String(i.organization_id).startsWith('org')).length, 15)   // all 15, no cap
  assert.equal(inv.filter(i => i.type === 'deal_builder').length, 3)
  assert.ok(inv.filter(i => i.type === 'deal_builder' && /deal-builder/.test(i.href)).every(i => i.href.endsWith('?from=/app/programs')))
})

test('sign-in return path stays inside the app', () => {
  assert.equal(safeAppPath('/app/chat/abc'), '/app/chat/abc')
  assert.equal(safeAppPath('/god'), '/app/programs')
  assert.equal(safeAppPath('https://evil.example'), '/app/programs')
  assert.equal(safeAppPath(null), '/app/programs')
})

test('sales tool cards come only from the server answer', () => {
  assert.deepEqual(salesToolCards({}, null), [])
  const cards = salesToolCards({ sales_team: { my_team: true } }, { sales: true })
  assert.deepEqual(cards.map(c => c.key), ['sales', 'my_team'])
  assert.equal(cards[0].href, '/team-prices/sales-deal-builder?from=/workspaces')
  assert.equal(salesToolCards({}, { sales: true }, '/app/programs')[0].href, '/team-prices/sales-deal-builder?from=/app/programs')
})

// ── Return path: a workspace opened from the mobile app returns to /app/programs ──
import { clearLauncherOrigin, launcherOrigin, safeOrigin, setLauncherOrigin, MOBILE_PROGRAMS } from '../src/pages/program/launcherOrigin.js'
import { readFileSync } from 'node:fs'

function fakeStore() {
  const m = new Map()
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) }
}

test('launcher origin: only the mobile Programs path is ever stored or returned', () => {
  const s = fakeStore()
  assert.equal(launcherOrigin(s), null)                       // web entry / direct link: none
  setLauncherOrigin(MOBILE_PROGRAMS, s)
  assert.equal(launcherOrigin(s), '/app/programs')
  setLauncherOrigin('https://evil.example', s)                 // anything else clears it
  assert.equal(launcherOrigin(s), null)
  s.setItem('af_launcher_origin', '//evil.example')            // tampered storage is ignored
  assert.equal(launcherOrigin(s), null)
  setLauncherOrigin(MOBILE_PROGRAMS, s); clearLauncherOrigin(s)
  assert.equal(launcherOrigin(s), null)
  assert.equal(safeOrigin('/app/programs/../god'), null)
  const broken = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') }, removeItem() {} }
  setLauncherOrigin(MOBILE_PROGRAMS, broken)
  assert.equal(launcherOrigin(broken), null)                   // blocked storage behaves as web
})

test('the web launcher honours the origin; app cards set it; web cards clear it', () => {
  const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(app, /const fromApp = launcherOrigin\(\)\n\s+if \(fromApp\) return <Navigate to=\{fromApp\} replace \/>/)
  assert.match(app, /onChoose=\{\(item\) => openProgramItem\(item, navigate\)\}/)   // web: no origin -> cleared
  const dest = readFileSync(new URL('../src/pages/program/programDestinations.js', import.meta.url), 'utf8')
  assert.match(dest, /setLauncherOrigin\(opts\.origin \|\| null\)/)
  const programs = readFileSync(new URL('../src/teamapp/ProgramsScreen.jsx', import.meta.url), 'utf8')
  assert.match(programs, /openProgramItem\(item, navigate, \{ origin: MOBILE_PROGRAMS \}\)/)
  assert.match(programs, /clearLauncherOrigin\(\)/)
})

import { chatSections, livePinKeys, MAX_PINS, pinnedItems, SECOND, togglePinKey } from '../src/teamapp/teamHelpers.js'

test('pins: at most two, kept in order, hidden once access is revoked', () => {
  const inv = [{ key: 'a' }, { key: 'b' }, { key: 'c' }]
  const keyOf = x => x.key
  assert.equal(MAX_PINS, 2)
  assert.deepEqual(togglePinKey([], 'a'), ['a'])
  assert.deepEqual(togglePinKey(['a'], 'b'), ['a', 'b'])
  assert.deepEqual(togglePinKey(['a', 'b'], 'c'), ['a', 'b'], 'a third pin is refused')
  assert.deepEqual(togglePinKey(['a', 'b'], 'a'), ['b'], 'unpin')
  assert.deepEqual(pinnedItems(inv, ['c', 'a'], keyOf).map(keyOf), ['c', 'a'])
  // "b" revoked: its pin disappears, the other stays.
  const after = inv.filter(x => x.key !== 'b')
  assert.deepEqual(pinnedItems(after, ['b', 'c'], keyOf).map(keyOf), ['c'])
  assert.deepEqual(livePinKeys(after, ['b', 'c', 'zz'], keyOf), ['c'])
})

test('chat inbox: featured rooms only when readable, search over name and preview', () => {
  const rooms = [
    { id: 1, slug: 'sales-team', kind: 'room', name: 'Sales Team', last: { text: 'Updated sales materials.' } },
    { id: 2, slug: 'announcements', kind: 'announcements', name: 'Company Announcements' },
    { id: 3, slug: null, kind: 'dm', name: 'Rep Two', last: { text: 'Appointment confirmed.', sender_name: 'Rep Two' } },
  ]
  const s = chatSections(rooms, '')
  assert.deepEqual(s.featured.map(r => r.id), [1, 2])
  assert.equal(s.messages.length, 3)
  assert.deepEqual(chatSections(rooms.slice(2), '').featured, [], 'no access -> no shortcut')
  assert.deepEqual(chatSections(rooms, 'confirmed').messages.map(r => r.id), [3])
  assert.deepEqual(chatSections(rooms, 'SALES').messages.map(r => r.id), [1])
})

test('Blue Ridge is navy with gold; approved identities unchanged', () => {
  const br = programStyle({ program: 'funeral', display: 'Blue Ridge Funeral Services' })
  assert.equal(br.accent, ACCENTS.blue_ridge)
  assert.equal(br.second, SECOND.blue_ridge)
  assert.equal(programStyle({ program: 'funeral', display: 'Restland' }).accent, '#087F68')
  assert.equal(programStyle({ program: 'sci', display: 'SCI' }).accent, '#4F46E5')
  assert.equal(programStyle({ program: 'volt', display: 'VOLT Marketing' }).accent, '#C62839')
  assert.equal(programStyle({ program: 'zzz', display: 'Unknown' }).accent, ACCENTS.fallback)
})

import { pinnedOrDesktop } from '../src/teamapp/teamHelpers.js'
import { availableCategories, categoryOf, photoKeyFor, shortLine } from '../src/pages/program/v2/commandCenterModel.js'

test('Pinned: phone pins first; otherwise desktop pins; otherwise desktop featured; never a preview', () => {
  const keyOf = x => x.k
  const inv = [{ k: 'a', type: 'workspace' }, { k: 'b', type: 'workspace' }, { k: 'p', type: 'preview', program: 'preview' }]
  assert.deepEqual(pinnedOrDesktop(inv, ['b'], ['a'], keyOf), { items: [inv[1]], fromDesktop: false })
  assert.deepEqual(pinnedOrDesktop(inv, [], ['b', 'a', 'x'], keyOf).items.map(keyOf), ['b', 'a'])
  assert.equal(pinnedOrDesktop(inv, [], ['b'], keyOf).fromDesktop, true)
  assert.deepEqual(pinnedOrDesktop(inv, [], [], keyOf).items.map(keyOf), ['a'], 'desktop featured = first workspace')
  assert.deepEqual(pinnedOrDesktop(inv, [], ['p'], keyOf).items.map(keyOf), ['a'], 'a preview is never pinned')
  assert.deepEqual(pinnedOrDesktop(inv, ['gone'], ['gone2'], keyOf).items.map(keyOf), ['a'], 'revoked pins fall through')
  assert.deepEqual(pinnedOrDesktop([], [], [], keyOf).items, [])
})

test('phone cards use desktop V2 identity: photo, short line, category', () => {
  const sci = { type: 'workspace', program: 'sci', display: 'SCI', orgLabel: 'Service Corporation International' }
  const sales = { type: 'workspace', program: 'evosys_sales', display: 'EvoSys Sales' }
  const db = { type: 'deal_builder', key: 'executive', display: 'Executive Deal Builder' }
  assert.equal(photoKeyFor(sci), 'sci'); assert.equal(shortLine(sci), 'Service Corporation International')
  assert.equal(shortLine(sales), 'Prospects & Outreach'); assert.equal(categoryOf(sales), 'sales')
  assert.equal(categoryOf(db), 'sales'); assert.equal(categoryOf(sci), 'clients')
  assert.deepEqual(availableCategories([sci, sales]).map(c => c.key), ['all', 'sales', 'clients'])
  assert.equal(programStyle(sales).icon, 'briefcase')
  assert.equal(programStyle({ type: 'project_checklist', key: 'projects' }).icon, 'check')
})
