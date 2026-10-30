// PROJECT OVERDRIVE Design A (#439): the owner-approved Wholesale rail.
// Run: node --test frontend/tests/wholesaleNav.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  wholesaleRail, activeRoute, groupOf, readClosedGroups, writeClosedGroups,
  WHOLESALE_NAV, PINNED_ROUTE, INBOX_ROUTE, MULTIFAMILY_ROUTE, COLLAPSE_KEY,
} from '../src/brand/wholesaleNav.js'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const layout = R('../src/components/Layout.jsx')
const app = R('../src/App.jsx')

// The Wholesale items exactly as Layout.jsx declares them (routes + flags).
const WS = (() => {
  const src = layout.slice(layout.indexOf("label: 'Acquisition · EvoSense'"), layout.indexOf("label: 'Operations',"))
  return [...src.matchAll(/\{ to: '([^']+)', label: '([^']+)', icon: '([^']+)'([^}]*)\}/g)]
    .map((m) => ({ to: m[1], label: m[2], icon: m[3], flags: m[4], end: /end: true/.test(m[4]) }))
})()
const asGroups = () => [{ label: 'A', items: WS.slice(0, 8) }, { label: 'B', items: WS.slice(8, 12) }, { label: 'C', items: WS.slice(12) }]
const rail = () => wholesaleRail(asGroups())
const flat = (r) => [r.pinned, ...r.groups.flatMap((g) => g.items)].filter(Boolean)

test('Layout still declares the Wholesale items this test reads', () => {
  assert.ok(WS.length >= 26, `found ${WS.length}`)
})

test('Acquisition Command is pinned above the groups, not inside one', () => {
  const r = rail()
  assert.equal(r.pinned.to, PINNED_ROUTE)
  assert.equal(r.pinned.label, 'Acquisition Command')
  assert.ok(!r.groups.some((g) => g.items.some((i) => i.to === PINNED_ROUTE)))
})

test('the five approved groups, in the approved order', () => {
  assert.deepEqual(rail().groups.map((g) => g.label), ['Acquisition', 'Buyers & Deals', 'Operations', 'Team & Field', 'Tools & Settings'])
})

test('each group holds the owner-approved screens in the approved order', () => {
  const by = Object.fromEntries(rail().groups.map((g) => [g.key, g.items.map((i) => i.label)]))
  assert.deepEqual(by.acquisition.slice(0, 5), ['Foreclosures', 'Discovery Inbox', 'Properties', 'Strategies', 'Multifamily'])
  assert.deepEqual(by.buyers, ['Buyer Intelligence', 'Deal Room', 'Investor Outreach', 'Buy Boxes', 'Dispositions'])
  assert.deepEqual(by.operations, ['Deal Operations', 'Appointments', 'Contracts & Closing', 'Funding Partners', 'Deal Calculator'])
  assert.deepEqual(by.team, ['Team Board', 'Canvassing', 'Callback Center', 'Team'])
  assert.deepEqual(by.tools, ['Providers & Controls', 'Exceptions', 'Pilot Controls', 'Wholesale Settings'])
})

test('every existing Wholesale destination stays on the rail exactly once - none added but Multifamily, none dropped', () => {
  const routes = flat(rail()).map((i) => i.to)
  assert.equal(new Set(routes).size, routes.length, 'no duplicates')
  assert.deepEqual(routes.filter((r) => r !== MULTIFAMILY_ROUTE).sort(), WS.map((i) => i.to).sort())
})

test('items keep their own permission and brand flags (the rail cannot widen access)', () => {
  const by = Object.fromEntries(flat(rail()).map((i) => [i.to, i]))
  for (const w of WS) assert.equal(by[w.to].flags, w.flags, w.to)
  assert.match(by['/wholesale/settings'].flags, /adminOnly: true/)
  assert.match(by['/wholesale/team'].flags, /workspaceAdminOnly: true/)
  assert.match(by['/wholesale/evosense/overlooked'].flags, /bookaboostOnly: true/)
  assert.match(by['/wholesale/land-partners'].flags, /olpOnly: true/)
})

test('Multifamily is the Discovery Inbox filtered to multifamily - same flags, and only when the inbox is on the rail', () => {
  const r = rail()
  const mf = flat(r).find((i) => i.to === MULTIFAMILY_ROUTE)
  const inbox = flat(r).find((i) => i.to === INBOX_ROUTE)
  assert.equal(mf.label, 'Multifamily')
  assert.equal(mf.flags, inbox.flags)
  assert.equal(mf.featureKey, inbox.featureKey)
  const noInbox = wholesaleRail([{ label: 'X', items: WS.filter((i) => i.to !== INBOX_ROUTE) }])
  assert.ok(!flat(noInbox).some((i) => i.to === MULTIFAMILY_ROUTE), 'no dead Multifamily link')
})

test('the server-side multifamily filter the Multifamily entry relies on is real', () => {
  const router = R('../../app/routers/evosense_router.py')
  assert.match(router, /ptype: Optional\[str\] = None/)
  assert.match(R('../../app/services/evosense/property_class.py'), /"multifamily"/)
  assert.match(R('../src/pages/wholesale/evosense/EvoInbox.jsx'), /\['ptype', 'Property category'/)
})

test('a screen nobody listed yet still appears (fallback), never lost', () => {
  const r = wholesaleRail([{ label: 'X', items: [...WS, { to: '/wholesale/new-thing', label: 'New thing' }] }])
  assert.ok(r.groups.find((g) => g.key === 'operations').items.some((i) => i.to === '/wholesale/new-thing'))
})

test('every rail route has a real React route (no dead links)', () => {
  const routes = [...app.matchAll(/<Route path="([^"]+)"/g)].map((m) => m[1])
  const matches = (pattern, path) => {
    const a = pattern.split('/'); const b = path.split('/')
    return a.length === b.length && a.every((seg, k) => seg.startsWith(':') || seg === b[k])
  }
  for (const i of flat(rail())) {
    const path = i.to.split('?')[0]
    assert.ok(routes.some((r) => matches(r, path)), `no route for ${path}`)
  }
})

test('exactly one entry is active, the most specific one', () => {
  const items = flat(rail())
  assert.equal(activeRoute(items, '/wholesale/evosense/inbox', '?ptype=multifamily'), MULTIFAMILY_ROUTE)
  assert.equal(activeRoute(items, '/wholesale/evosense/inbox', ''), INBOX_ROUTE)
  assert.equal(activeRoute(items, '/wholesale/evosense/inbox', '?ptype=land'), INBOX_ROUTE)
  assert.equal(activeRoute(items, '/wholesale/evosense', ''), PINNED_ROUTE)
  assert.equal(activeRoute(items, '/wholesale', ''), '/wholesale')
  assert.equal(activeRoute(items, '/wholesale/buyers/buy-boxes', ''), '/wholesale/buyers/buy-boxes')
  assert.equal(activeRoute(items, '/wholesale/buyers', ''), '/wholesale/buyers')
  assert.equal(activeRoute(items, '/wholesale/deal-room/investors', ''), '/wholesale/deal-room/investors')
  assert.equal(activeRoute(items, '/wholesale/deal-room/offers', ''), '/wholesale/deal-room')
})

test('detail screens light up the entry that owns them', () => {
  const items = flat(rail())
  assert.equal(activeRoute(items, '/wholesale/evosense/property/abc', ''), PINNED_ROUTE)
  assert.equal(activeRoute(items, '/wholesale/deals/42', ''), '/wholesale')
  assert.equal(activeRoute(items, '/somewhere-else', ''), null)
  assert.equal(groupOf(rail(), '/wholesale/closing'), 'operations')
})

test('closed groups are remembered per device and storage failures never throw', () => {
  const store = new Map()
  const fake = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }
  writeClosedGroups(fake, new Set(['team', 'tools']))
  assert.deepEqual([...readClosedGroups(fake)].sort(), ['team', 'tools'])
  store.set(COLLAPSE_KEY, '["bogus","team"]')
  assert.deepEqual([...readClosedGroups(fake)], ['team'])
  store.set(COLLAPSE_KEY, '{not json')
  assert.equal(readClosedGroups(fake).size, 0)
  const broken = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
  assert.equal(readClosedGroups(broken).size, 0)
  writeClosedGroups(broken, new Set(['team']))
  assert.equal(readClosedGroups(null).size, 0)
})

test('Layout uses the OVERDRIVE rail outside OLP and keeps the OLP rail in OLP mode', () => {
  assert.match(layout, /if \(inWholesale && !olpShell\) \{[\s\S]*wholesaleRail\(/)
  assert.match(layout, /olpShell \? olpGroups\(/)
  assert.match(layout, /\$\{inWholesale && !olpShell \? ' layout--wsnav' : ''\}/)
  assert.ok(WHOLESALE_NAV.length === 5)
})

test('group toggles are real buttons with aria-expanded, and the active link carries aria-current', () => {
  const block = layout.slice(layout.indexOf('if (inWholesale && !olpShell) {'), layout.indexOf('if (inWholesale) {', layout.indexOf('if (inWholesale && !olpShell) {') + 10))
  assert.match(block, /<button type="button" className="nav-section-toggle" aria-expanded=\{open\} aria-controls=\{gid\}/)
  assert.match(block, /aria-current=\{on \? 'page' : undefined\}/)
  assert.match(block, /onClick=\{closeSidebar\}/)
  assert.match(block, /visible\(rail\.pinned\)/)
  assert.match(block, /g\.items\.filter\(visible\)/)
})
