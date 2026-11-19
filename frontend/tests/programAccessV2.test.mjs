// Program Access V2 (Premium Command Center) - model, permission and source guards.
// Plain `node --test`, no bundler. Test data mirrors the owner's screenshot.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { programCards, salesToolCards } from '../src/pages/program/programAccessModel.js'
import {
  CATEGORIES, availableCategories, categoryOf, chooseFeatured, healthLabel, matchesQuery,
  photoKeyFor, roleLabel, summaryCounts, firstName,
} from '../src/pages/program/v2/commandCenterModel.js'

const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')
const fixtures = JSON.parse(readFileSync(new URL('../../docs/redesign/program-access-v2/qa/fixtures.json', import.meta.url), 'utf8'))
const cardKey = (item) => (item.type || 'workspace') + ':' + (item.organization_id || '') + ':' + (item.key || '')

function cardsFor(who) {
  const ctx = fixtures[who].contexts
  const { visible, others, previews } = programCards(ctx)
  return { cards: [...visible, ...salesToolCards(ctx, ctx.deal_builder)], others, previews, ctx }
}

test('the owner sees all 17 programs and all 3 prospect previews', () => {
  const { cards, previews } = cardsFor('owner')
  assert.deepEqual(cards.map(c => c.display).sort(), [
    'Acquisition Control Center', 'Atlantis Light & Power', 'Back Office', 'Blue Ridge Gold & Jewelry', 'Client Portals',
    'Commercial Cleaning Blueprint', 'Consumer Leads', 'Executive Deal Builder', 'Insurance', 'My Team',
    'Restland Cemetery and Funeral Home', 'Roofing', 'SCI', 'Salesperson Deal Builder', 'VOLT Marketing',
    'Vincent Greene Agency', 'Wholesale',
  ])
  assert.deepEqual(previews.map(p => p.display), [
    'Building Equity Investments LLC', 'Countryside Land Partners', 'VP Wraps Vehicle Protection'])
})

test('a salesperson never gets the Executive Deal Builder, owner tools or other businesses', () => {
  const { cards, previews } = cardsFor('salesperson')
  const names = cards.map(c => c.display)
  assert.equal(names.includes('Executive Deal Builder'), false)
  assert.equal(names.includes('Back Office'), false)
  assert.equal(names.includes('My Team'), false)
  assert.deepEqual(names.sort(), ['Commercial Cleaning Blueprint', 'Sales Onboarding', 'Salesperson Deal Builder'])
  assert.deepEqual(previews, [])
})

test('summary numbers are counted once each, from the same cards', () => {
  const o = cardsFor('owner')
  // 17 destinations (incl. Client Portals); 10 client businesses (VOLT + Consumer Leads are one org;
  // the Acquisition hub, Back Office and the sales tools are not clients);
  // previews are separate and never counted as workspaces or clients.
  assert.deepEqual(summaryCounts(o.cards, o.others, o.previews), { workspaces: 17, clients: 10, previews: 3 })
  const s = cardsFor('salesperson')
  assert.deepEqual(summaryCounts(s.cards, s.others, s.previews), { workspaces: 3, clients: 1, previews: 0 })
  assert.deepEqual(summaryCounts([], [], []), { workspaces: 0, clients: 0, previews: 0 })
})

test('every card is filed under exactly one real category, and empty categories are hidden', () => {
  const { cards } = cardsFor('owner')
  const keys = new Set(CATEGORIES.map(c => c.key))
  const by = {}
  for (const c of cards) {
    const k = categoryOf(c)
    assert.ok(keys.has(k) && k !== 'all', c.display + ' -> ' + k)
    ;(by[k] = by[k] || []).push(c.display)
  }
  assert.deepEqual(by.admin.sort(), ['Back Office'])
  assert.deepEqual(by.operations.sort(), ['Acquisition Control Center', 'Client Portals', 'Consumer Leads', 'VOLT Marketing'])
  assert.deepEqual(by.sales.sort(), ['Executive Deal Builder', 'My Team', 'Salesperson Deal Builder'])
  assert.equal(by.clients.length, 9)
  assert.deepEqual(availableCategories(cardsFor('salesperson').cards).map(c => c.key), ['all', 'sales', 'clients'])
})

test('each business wears its own bundled photo, and every photo file exists', () => {
  const { cards, previews } = cardsFor('owner')
  const want = {
    SCI: 'sci', 'Restland Cemetery and Funeral Home': 'restland', 'Atlantis Light & Power': 'energy',
    Roofing: 'roofing', Wholesale: 'wholesale', 'Commercial Cleaning Blueprint': 'cleaning',
    'Blue Ridge Gold & Jewelry': 'gold', Insurance: 'insurance', 'Vincent Greene Agency': 'agency',
    'VOLT Marketing': 'volt', 'Consumer Leads': 'consumer', 'Acquisition Control Center': 'acquisition', 'Client Portals': 'cleaning',
    'Back Office': 'backoffice', 'Executive Deal Builder': 'exec-builder', 'Salesperson Deal Builder': 'sales-builder',
    'My Team': 'team', 'Building Equity Investments LLC': 'realestate', 'Countryside Land Partners': 'land',
    'VP Wraps Vehicle Protection': 'wraps',
  }
  for (const c of [...cards, ...previews]) {
    assert.equal(photoKeyFor(c), want[c.display], c.display)
    assert.ok(existsSync(new URL('../src/pages/program/v2/img/' + photoKeyFor(c) + '-card.webp', import.meta.url)), c.display)
  }
  // A new client of an industry with no photo of its own still gets one.
  assert.equal(photoKeyFor({ program: 'business', variant: 'general', industry: 'something-new' }), 'business')
})

test('every bundled photo has recorded provenance', () => {
  const credits = read('../docs/redesign/program-access-v2/PHOTO_CREDITS.md')
  const files = readdirSync(new URL('../src/pages/program/v2/img/', import.meta.url))
  const keys = [...new Set(files.map(f => f.replace(/-(card|wide|accent)\.webp$/, '')))]
  for (const k of keys) assert.match(credits, new RegExp('`' + k + '`'), 'credit for ' + k)
})

test('featured = first pinned card the server still lists, otherwise the first card', () => {
  const { cards } = cardsFor('owner')
  assert.equal(chooseFeatured(cards, [], cardKey).display, 'SCI')
  const ccb = cards.find(c => c.display === 'Commercial Cleaning Blueprint')
  assert.equal(chooseFeatured(cards, ['gone:x:', cardKey(ccb)], cardKey).display, 'Commercial Cleaning Blueprint')
  assert.equal(chooseFeatured(cards, ['gone:x:'], cardKey).display, 'SCI')
  assert.equal(chooseFeatured([], ['x'], cardKey), null)
})

test('platform health is shown only from a real readiness answer', () => {
  assert.equal(healthLabel({ status: 'ok' }).tone, 'ok')
  assert.equal(healthLabel({ status: 'ok' }).label, 'Live')
  assert.equal(healthLabel({ status: 'degraded' }).tone, 'warn')
  assert.equal(healthLabel({ status: 'failed' }).tone, 'bad')
  assert.equal(healthLabel(null), null)
  assert.equal(healthLabel({}), null)
})

test('names and roles come from the signed-in user, never a hard-coded owner', () => {
  assert.equal(firstName({ full_name: 'Jordan Rivera' }), 'Jordan')
  assert.equal(firstName({ email: 'x@y.z' }), '')
  assert.equal(roleLabel('god_admin'), 'Platform Owner')
  assert.equal(roleLabel('member'), 'Team Member')
  assert.equal(roleLabel('area_manager'), 'Area Manager')
})

test('search matches what a person reads on a card', () => {
  const { cards } = cardsFor('owner')
  const hits = cards.filter(c => matchesQuery(c, 'deal builder')).map(c => c.display).sort()
  assert.deepEqual(hits, ['Executive Deal Builder', 'Salesperson Deal Builder'])
  assert.equal(cards.filter(c => matchesQuery(c, 'zzzz')).length, 0)
})

test('God Mode and People & Access appear only on the server\'s word', () => {
  const portal = read('src/pages/program/ProgramAccessPortal.jsx')
  assert.match(portal, /const isOwner = user\?\.role === 'god_admin'\s*\n\s*&& \(contexts\?\.platform_contexts \|\| \[\]\)\.some\(c => c && c\.path === '\/god'\)/)
  assert.match(portal, /const canManageTeam = !!contexts\?\.sales_team\?\.my_team/)
  const center = read('src/pages/program/v2/CommandCenter.jsx')
  assert.match(center, /\{isOwner && \(\s*<a className="pa2-nav-item pa2-nav-item--owner" href="\/god">/)
  assert.match(center, /\{\(isOwner \|\| canManageTeam\) && \(/)
  assert.match(center, /\{isOwner && \(\s*<a className="pa2-btn pa2-btn--ghost pa2-btn--lg" href="\/god\/access">/)
  assert.match(portal, /const myDayHref = contexts\?\.has_back_office/)
})

test('previews are real links and nothing opens a script pop-up', () => {
  const center = read('src/pages/program/v2/CommandCenter.jsx')
  assert.equal(/window\.open/.test(center), false)
  assert.match(center, /target="_blank" rel="noopener noreferrer"/)
})

test('V2 styles are scoped and use no third-party image URLs', () => {
  const css = read('src/pages/program/v2/commandCenter.css').replace(/\/\*[\s\S]*?\*\//g, '')
  const selectors = css.replace(/@media[^{]*\{/g, '').match(/^[^{}@\s][^{}]*\{/gm) || []
  for (const s of selectors) for (const part of s.replace('{', '').split(','))
    assert.match(part.trim(), /^\.pa2\b/, 'unscoped selector: ' + part.trim())
  for (const f of ['src/pages/program/v2/CommandCenter.jsx', 'src/pages/program/v2/photos.js', 'src/pages/program/v2/commandCenter.css'])
    assert.equal(/https?:\/\/(?!www\.w3\.org)/.test(read(f)), false, f)
})

test('card wording and status come from server facts, never a client name', async () => {
  const { shortLine, cardStatus, factLine } = await import('../src/pages/program/v2/commandCenterModel.js')
  const { cards } = cardsFor('owner')
  const by = Object.fromEntries(cards.map(c => [c.display, c]))
  assert.equal(shortLine(by.SCI), 'Service Corporation International')
  assert.equal(shortLine(by.Wholesale), 'Acquisition Command')
  assert.equal(shortLine(by['Back Office']), 'Sales Pipeline & Reports')
  assert.equal(shortLine(by['VOLT Marketing']), 'Recruiting & Enrollment')
  for (const c of cards) assert.equal(cardStatus(c).label, 'Active', c.display)
  assert.equal(cardStatus({ badge: 'Sample data' }).label, 'Sample data')
  assert.equal(cardStatus({ orgLabel: 'View only' }).label, 'View only')
  assert.equal(factLine(by['Commercial Cleaning Blueprint']), 'Prospects • Follow-up • Walkthroughs')
})

test('desktop cards are short and wide; four columns on desktop', () => {
  const css = read('src/pages/program/v2/commandCenter.css')
  assert.match(css, /\.pa2 \.pa2-card--lead \{ aspect-ratio: 2\.4 \/ 1;/)
  assert.match(css, /\.pa2 \.pa2-card--compact \{ min-height: 108px; \}/)
  assert.match(css, /\.pa2 \.pa2-grid--grid \{ grid-template-columns: repeat\(4, minmax\(0, 1fr\)\); \}/)
  assert.match(css, /\.pa2 \.pa2-feature \{[^}]*height: 185px;/)
})

test('Project Command Center (#667) is an owner tool: Administration, not a client, a plain link', async () => {
  const { programCards } = await import('../src/pages/program/programAccessModel.js')
  const owner = programCards({ ...fixtures.owner.contexts, project_checklist: true }).visible
  const pc = owner.find(c => c.type === 'project_checklist')
  assert.ok(pc, 'listed when the server says project_checklist')
  assert.equal(categoryOf(pc), 'admin')
  assert.equal(photoKeyFor(pc), 'onboarding')
  const plain = programCards({ ...fixtures.owner.contexts }).visible
  assert.equal(plain.some(c => c.type === 'project_checklist'), false, 'never without the server flag')
  assert.equal(summaryCounts(owner, [], []).clients, summaryCounts(plain, [], []).clients)
  assert.match(read('src/pages/program/ProgramAccessPortal.jsx'), /item\.type === 'project_checklist'\) \? item\.href/)
})

test('Demos page: every demo grouped by link and deal status, names cleaned', async () => {
  const { demoGroup, demoStatus, previewDisplayName, DEMO_FILTERS } = await import('../src/pages/program/v2/commandCenterModel.js')
  const items = fixtures.owner.demos
  const groups = Object.fromEntries(items.map(d => [previewDisplayName(d), demoGroup(d)]))
  assert.equal(groups['Countryside Land Partners'], 'lost')
  assert.equal(groups['VP Wraps Vehicle Protection'], 'lost')
  assert.equal(groups['Commercial Cleaning Blueprint'], 'lost')     // "— Daniel Almaguer" removed
  assert.equal(groups['Blue Ridge Gold & Jewelry'], 'open')
  assert.equal(groups['Example Expired Demo Co'], 'expired')
  assert.equal(demoStatus({ status: 'won' }).label, 'Won')
  assert.equal(demoStatus({ status: 'open', customer: true }).label, 'Customer')
  assert.deepEqual(DEMO_FILTERS.map(f => f.key), ['all', 'open', 'won', 'lost', 'expired'])
  const panel = read('src/pages/program/v2/DemosPanel.jsx')
  assert.match(panel, /\{d\.live && d\.url \? \(/)                    // only a live link is a link
  assert.equal(/window\.open/.test(panel), false)
  const center = read('src/pages/program/v2/CommandCenter.jsx')
  assert.match(center, /\{demos\.length > 0 && \(/)                   // Demos only when the server listed some
  assert.match(read('src/pages/program/ProgramAccessPortal.jsx'), /api\.get\('\/launcher\/demos'/)
})
