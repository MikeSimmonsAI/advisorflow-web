// UI-REDESIGN lane, issue #275 — Program Access redesign guards.
// Static + model checks that run with plain `node --test` (no bundler).
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { programCards } from '../src/pages/program/programAccessModel.js'

const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')
const portal = read('src/pages/program/ProgramAccessPortal.jsx')
const card = read('src/design/WorkspaceCard.jsx')
const art = read('src/design/art/WorkspaceArt.jsx')
const hero = read('src/design/art/PlatformArt.jsx')
const tokens = read('src/design/evTokens.css')

const W = (id, name, extra) => ({ type: 'workspace', organization_id: id, organization_name: name, ...extra })
const all = {
  workspace_contexts: [
    W('evo', 'EVO Integrated Solutions LLC', { industry: 'generic', experience: 'wholesale' }),
    W('ins', 'Max Life Demo Agency (DEMO)', { industry: 'insurance', experience: 'insurance' }),
    W('sci', 'Service Corporation International', { industry: 'funeral', is_program: true, experience: 'sci' }),
    W('roof', 'EvoSys Pro Roofing', { industry: 'roofing', program_route: '/program/roofing', experience: 'roofing' }),
    W('atl', 'Atlantis Light & Power', { industry: 'energy', experience: 'energy' }),
  ],
  product_contexts: [{ type: 'product', key: 'wholesale', organization_id: 'evo', path: '/workspace/evo?product=wholesale' }],
}

test('the five approved businesses render with their own identity, in the approved order', () => {
  const { visible } = programCards(all)
  assert.deepEqual(visible.map(c => c.variant), ['sci', 'energy', 'roofing', 'wholesale', 'insurance'])
  for (const c of visible) {
    assert.ok(c.kind && c.display && c.summary, c.display)
    assert.ok(Array.isArray(c.facts) && c.facts.length >= 2, 'info strip for ' + c.display)
  }
})

test('cards still come only from the server list — the redesign adds no access', () => {
  const one = { workspace_contexts: [all.workspace_contexts[4]], product_contexts: [] }
  assert.deepEqual(programCards(one).visible.map(c => c.organization_id), ['atl'])
  assert.deepEqual(programCards({}).visible, [])
  assert.deepEqual(programCards(null).visible, [])
})

test('the page renders cards from programCards(contexts) and never from a hard-coded list', () => {
  assert.match(portal, /programCards\(contexts\)/)
  assert.match(portal, /visible\.map\(item =>/)
  for (const name of ['Atlantis Light & Power', 'Service Corporation International', 'John Doe', 'EvoSys Pro Roofing'])
    assert.equal(portal.includes(name), false, 'hard-coded ' + name)
})

test('opening a card goes through the caller\'s onChoose — no routing decided in the card', () => {
  assert.match(portal, /onOpen=\{onChoose\}/)
  assert.match(card, /onClick=\{\(\) => onOpen\(item\)\}/)
  assert.equal(/navigate\(|window\.location/.test(card), false)
})

test('every business variant has local artwork and an accent token', () => {
  for (const v of ['sci', 'energy', 'roofing', 'wholesale', 'insurance', 'funeral', 'office', 'general']) {
    assert.match(art, new RegExp('\\b' + v + ':\\s*\\{ sky'), 'palette ' + v)
    assert.match(tokens, new RegExp('\\[data-accent="' + v + '"\\]'), 'accent ' + v)
  }
})

test('artwork depends on no third-party image URLs', () => {
  for (const src of [art, hero, card, portal]) {
    assert.equal(/https?:\/\//.test(src.replace(/http:\/\/www\.w3\.org\/2000\/svg/g, '')), false)
    assert.equal(/<img\b/.test(src), false)
  }
})

test('design tokens are scoped to .ev-ds and do not touch :root', () => {
  assert.equal(/:root/.test(tokens), false)
  const selectors = tokens.replace(/\/\*[\s\S]*?\*\//g, '').match(/^[^{}@\s][^{}]*\{/gm) || []
  for (const s of selectors) assert.ok(s.includes('.ev-'), 'unscoped selector: ' + s.trim())
})

test('the Grid/List toggle is wired to real state, and the indicator is kept', () => {
  assert.match(portal, /layout=\{view\}/)
  assert.match(portal, /aria-pressed=\{view === 'grid'\}/)
  assert.match(portal, /aria-pressed=\{view === 'list'\}/)
  assert.match(portal, /Authorized workspaces only/)
  assert.match(portal, /Choose your <span>business\.<\/span>/)
})

test('accent button colours meet WCAG AA (4.5:1) against white text', () => {
  const lum = (hex) => {
    const c = hex.match(/\w\w/g).map(x => parseInt(x, 16) / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  }
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05) }
  const strong = [...tokens.matchAll(/data-accent="(\w+)"\]\s*\{[^}]*--acc-strong: (#[0-9a-f]{6})/g)]
  assert.ok(strong.length >= 8)
  for (const [, v, hex] of strong) assert.ok(ratio(hex, '#ffffff') >= 4.5, v + ' ' + hex + ' ' + ratio(hex, '#ffffff').toFixed(2))
})

test('a new kind of business gets a full neutral card, under its own name, only if the server listed it', () => {
  const c = { workspace_contexts: [...all.workspace_contexts, W('solar', 'Bright Solar Co', { industry: 'solar' })],
              product_contexts: all.product_contexts }
  const { visible, others } = programCards(c)
  const card = visible.find(x => x.organization_id === 'solar')
  assert.ok(card, 'new business shown as a card')
  assert.equal(card.variant, 'general')
  assert.equal(card.display, 'Bright Solar Co')
  assert.ok(card.kind && card.summary && card.facts.length)
  assert.deepEqual(others, [])
  assert.equal(visible.at(-1).organization_id, 'solar', 'sorts after the named programs')
  assert.equal(programCards(all).visible.some(x => x.organization_id === 'solar'), false)
})
