import assert from 'node:assert/strict'
import test from 'node:test'
import { addDays, bodyPieces, elapsedSince, fmtElapsed, fmtHours, fmtTime, insertMention, localYmd,
         mentionedIds, mentionMatches, mentionQuery, mergeMessages, safeAppPath, tabsFor,
         toLocalInput } from '../src/teamapp/teamHelpers.js'
import { salesToolCards } from '../src/pages/program/programAccessModel.js'

test('durations', () => {
  assert.equal(fmtHours(0), '0h 00m')
  assert.equal(fmtHours(3725), '1h 02m')
  assert.equal(fmtHours(11 * 3600), '11h 00m')
  assert.equal(fmtElapsed(3725), '1:02:05')
})

test('times display in the EvoSys zone, across daylight saving', () => {
  // 2026-11-01 06:30Z is 1:30 AM CDT; 07:30Z is 1:30 AM CST (clocks fell back).
  assert.equal(fmtTime('2026-11-01T06:30:00Z', 'America/Chicago'), '1:30 AM')
  assert.equal(fmtTime('2026-11-01T07:30:00Z', 'America/Chicago'), '1:30 AM')
  assert.equal(fmtTime('2026-11-16T16:24:00Z', 'America/Chicago'), '10:24 AM')
  assert.equal(localYmd('2026-11-17T04:30:00Z', 'America/Chicago'), '2026-11-16')
  assert.equal(toLocalInput('2026-11-10T18:00:00Z', 'America/Chicago'), '2026-11-10T12:00')
  assert.equal(addDays('2026-10-26', 7), '2026-11-02')
})

test('elapsed uses the server clock, not the phone clock', () => {
  // Phone clock 10 minutes fast makes no difference: only time since receipt counts.
  const got = elapsedSince('2026-11-16T14:00:00Z', '2026-11-16T15:00:00Z', 1000, 61000)
  assert.equal(got, 3600 + 60)
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

test('tabs come from server access only', () => {
  assert.deepEqual(tabsFor({}).map(t => t.key), ['programs', 'account'])
  assert.deepEqual(tabsFor({ chat: true, time: true }).map(t => t.key), ['programs', 'chat', 'time', 'account'])
  assert.deepEqual(tabsFor({ time: true }).map(t => t.key), ['programs', 'time', 'account'])
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
