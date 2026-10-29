// OLP branding of the Overlooked Properties / Opportunity Radar screen: guard rails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const R = (p) => readFileSync(new URL(p, import.meta.url)).toString()
const jsx = R('../src/pages/wholesale/evosense/OpportunityRadar.jsx')
const css = R('../src/pages/wholesale/evosense/OpportunityRadar.css')
const theme = R('../src/theme.js')
const access = R('../src/pages/program/BookaBoostProgramAccess.jsx')
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

test('OLP is chosen by the product-brand switch, and the #428 look stays available', () => {
  assert.match(jsx, /const IS_OLP = bookaboostProductBrand\(\) === 'olp'/)
  assert.match(jsx, /IS_OLP\s*\?\s*<OlpCard[\s\S]*?:\s*<Card /)
  assert.match(jsx, /\{BRAND\} · OPPORTUNITY INTELLIGENCE/)
})

test('OLP cards: no save / bookmark control, one action button, every photo labelled representative', () => {
  const card = strip(jsx.slice(jsx.indexOf('function OlpCard'), jsx.indexOf('function Drawer')))
  assert.equal((card.match(/<button/g) || []).length, 1)
  assert.ok(!/bookmark|save|watchlist/i.test(card))
  assert.match(card, /photo \? <span className="bbr-oc__rep">Representative photo<\/span>/)
  assert.match(card, /No photo on file/)
})

test('OLP cards show only facts the record holds - no invented beds, baths, size or value', () => {
  const card = jsx.slice(jsx.indexOf('function OlpCard'), jsx.indexOf('function Drawer'))
  assert.match(card, /f\.bedrooms != null \?/)
  assert.match(card, /f\.bathrooms != null \?/)
  assert.match(card, /f\.square_feet \?/)
  assert.match(card, /Value not on record/)
})

test('hero copy names markets, not nationwide coverage', () => {
  assert.match(jsx, /IS_OLP \? ' in residential, wholesale, land and commercial markets'/)
})

test('every OLP style is scoped to this page (.bbr--olp) or its OLP cards (.bbr-oc)', () => {
  const olp = strip(css.slice(css.indexOf('*/', css.indexOf('══ OLP')) + 2))
  const sels = [...olp.matchAll(/([^{};]+)\{[^{}]*\}/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.ok(sels.length > 40)
  for (const sel of sels) for (const part of sel.split(',')) {
    assert.ok(/bbr--olp|^\.bbr-oc/.test(part.trim()), 'unscoped selector: ' + part.trim())
  }
  assert.match(css, /\.content-area:has\(\.bbr--olp\)/)
})

test('the Wholesale rail uses the OLP horizontal logo only through the switch', () => {
  assert.match(theme, /wholesaleLogoUrl: bookaboostProductBrand\(\) === 'olp' \? OLP\.horizontal : '\/brands\/bookaboost-logo\.svg'/)
})

test('the Program Access tile uses the OLP icon; which programs appear is unchanged', () => {
  assert.match(access, /kind === 'wholesale' && IS_OLP/)
  assert.match(access, /<img src=\{OLP\.icon\}/)
  assert.match(access, /programCards\(contexts\)/)
})
