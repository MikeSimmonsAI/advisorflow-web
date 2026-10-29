// OLP platform design (owner decisions 1-6, Oct 29): guard rails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { olpGroups, olpHeaderFor, OLP_NAV, OLP_LABELS } from '../src/brand/olpNav.js'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const layout = R('../src/components/Layout.jsx')
const css = R('../src/brand/olp-workspace.css')
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '')

// The Wholesale groups exactly as Layout.jsx declares them (routes + flags).
const WS = (() => {
  const src = layout.slice(layout.indexOf("label: 'Acquisition · EvoSense'"), layout.indexOf("label: 'Operations',"))
  return [...src.matchAll(/\{ to: '([^']+)', label: '([^']+)'([^}]*)\}/g)].map((m) => ({ to: m[1], label: m[2], flags: m[3] }))
})()
const asGroups = () => [{ label: 'A', items: WS.slice(0, 6) }, { label: 'B', items: WS.slice(6, 10) }, { label: 'C', items: WS.slice(10) }]

test('Layout still declares the Wholesale menu items this test reads', () => {
  assert.ok(WS.length >= 24, `found ${WS.length}`)
})

test('the OLP rail keeps every Wholesale item exactly once - nothing added, removed or duplicated', () => {
  const out = olpGroups(asGroups())
  const routes = out.flatMap((g) => g.items.map((i) => i.to))
  assert.deepEqual([...routes].sort(), WS.map((i) => i.to).sort())
  assert.equal(new Set(routes).size, routes.length)
})

test('three plain groups in the owner order; radar first; settings last', () => {
  const out = olpGroups(asGroups())
  assert.deepEqual(out.map((g) => g.label), ['Opportunity Intelligence', 'Market Operations', 'Account & Settings'])
  assert.deepEqual(out[0].items.map((i) => i.to), ['/wholesale/evosense/overlooked', '/wholesale/buyers', '/wholesale/buyers/buy-boxes', '/wholesale/deal-room', '/wholesale/deal-room/investors'])
  assert.deepEqual(out[2].items.map((i) => i.to), ['/wholesale/evosense/controls', '/wholesale/pilot', '/wholesale/team', '/wholesale/settings'])
})

test('menu says "Opportunity Radar"; permission flags travel with every item', () => {
  const out = olpGroups(asGroups())
  const all = out.flatMap((g) => g.items)
  assert.equal(all.find((i) => i.to === '/wholesale/evosense/overlooked').label, 'Opportunity Radar')
  assert.equal(all.find((i) => i.to === '/wholesale/settings').label, 'Workspace Settings')
  for (const item of all) {
    const src = WS.find((w) => w.to === item.to)
    assert.equal(item.flags, src.flags, `flags changed for ${item.to}`)
  }
  assert.equal(OLP_LABELS['/wholesale/evosense/overlooked'], 'Opportunity Radar')
})

test('an item the map does not know still appears (under Market Operations), never lost', () => {
  const out = olpGroups([...asGroups(), { label: 'X', items: [{ to: '/wholesale/new-screen', label: 'New' }] }])
  assert.ok(out.find((g) => g.label === 'Market Operations').items.some((i) => i.to === '/wholesale/new-screen'))
  assert.ok(OLP_NAV.every((g) => g.routes.every((r) => r.startsWith('/wholesale'))))
})

test('header variants: hero for the major workspaces, detail for records, compact for the rest', () => {
  for (const p of ['/wholesale/evosense/overlooked', '/wholesale/evosense', '/wholesale/buyers', '/wholesale/deal-room', '/wholesale']) assert.equal(olpHeaderFor(p), 'hero', p)
  for (const p of ['/wholesale/evosense/property/abc', '/wholesale/deals/12', '/wholesale/deal-room/investors/9']) assert.equal(olpHeaderFor(p), 'detail', p)
  for (const p of ['/wholesale/buyers/buy-boxes', '/wholesale/settings', '/wholesale/foreclosures', '/wholesale/deal-room/investors']) assert.equal(olpHeaderFor(p), 'compact', p)
})

test('OLP is on only for the BookaBoost theme with the OLP product brand, and only inside Wholesale', () => {
  assert.match(layout, /const olpBrand = shellResolution\.theme === THEMES\.BOOKABOOST && bookaboostProductBrand\(\) === 'olp'/)
  assert.match(layout, /const olpShell = olpBrand && inWholesale/)
  assert.match(layout, /\$\{inDealRoom && !olpShell \? ' layout--dealroom' : ''\}/)
  assert.match(layout, /const foldable = inDealRoom && !olpShell/)
  assert.match(layout, /olpShell \? olpGroups\(groups\.filter\(isWs\)\) : groups\.filter\(isWs\)/)
  assert.match(layout, /root\.removeAttribute\('data-product-brand'\)/)
})

test('every rule in the OLP theme is scoped to html[data-product-brand="olp"] - no global override', () => {
  const body = strip(css).replace(/@media[^{]+\{/g, '{')
  const sels = [...body.matchAll(/([^{}]+)\{[^{}]*\}/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.ok(sels.length > 120, `only ${sels.length} rules`)
  for (const s of sels) {
    for (const part of s.split(/,(?![^(]*\))/)) {
      assert.match(part.trim(), /^html\[data-product-brand="olp"\]/, `unscoped selector: ${part.trim().slice(0, 80)}`)
    }
  }
})

test('status colours keep their meaning: success green, warning amber, danger red', () => {
  assert.match(css, /--evo-success: #3fbf8a/)
  assert.match(css, /--evo-warning: #f0a83a/)
  assert.match(css, /--evo-danger: #ff6b78/)
  assert.ok(!/#2563eb|#1d4ed8|#1f6fe5/i.test(strip(css)), 'a blue primary slipped into the OLP theme')
})

test('refined logo is in the app; the owner originals are kept byte-for-byte', () => {
  const sum = (p) => createHash('sha256').update(readFileSync(new URL(p, import.meta.url))).digest('hex')
  const listed = Object.fromEntries(R('../../docs/redesign/olp/SHA256SUMS.txt').trim().split('\n').map((l) => l.split(/\s+/)).map(([h, f]) => [f, h]))
  for (const f of Object.keys(listed)) assert.equal(sum(`../../docs/redesign/olp/original/${f}`), listed[f], `original ${f} changed`)
  for (const f of ['OLP_logo_primary_dark', 'OLP_logo_primary_light', 'OLP_logo_horizontal_dark', 'OLP_logo_horizontal_light']) {
    const svg = R(`../public/brands/olp/${f}.svg`)
    assert.match(svg, /Owner-approved refinement/)
    assert.ok(!/<script|javascript:|href=|<foreignObject|™|®/i.test(svg))
  }
  assert.equal(sum('../public/brands/olp/OLP_icon.svg'), listed['OLP_icon.svg'], 'the icon has no endorsement line and stays as delivered')
})
