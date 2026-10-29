// BookaBoost Program Access: chosen by the server's brand answer, scoped
// styling, the real logo, and the EvoSys launcher left as it was.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { isBookaBoostBrand, programCards } from '../src/pages/program/programAccessModel.js'

const read = p => fs.readFileSync(new URL(p, import.meta.url), 'utf8')

test('the BookaBoost look is used only when the server says the brand is BookaBoost', () => {
  assert.equal(isBookaBoostBrand({ brand: { slug: 'bookaboost', theme: 'bookaboost', name: 'BookaBoost' } }), true)
  assert.equal(isBookaBoostBrand({ brand: { slug: 'evosyspro', theme: 'evosyspro' } }), false)
  assert.equal(isBookaBoostBrand({ brand: null }), false)
  assert.equal(isBookaBoostBrand({}), false)
  assert.equal(isBookaBoostBrand(null), false)
})

test('the brand answer changes no card', () => {
  const base = { product_contexts: [{ type: 'product', key: 'wholesale', organization_id: 'o1', path: '/wholesale/evosense' }],
                 workspace_contexts: [{ type: 'workspace', organization_id: 'o1', organization_name: 'BookaBoost Wholesale', experience: 'wholesale' }],
                 has_back_office: true, back_office_path: '/sales' }
  const a = programCards(base)
  const b = programCards({ ...base, brand: { slug: 'bookaboost', theme: 'bookaboost' } })
  assert.deepEqual(a, b)
  assert.deepEqual(a.visible.map(c => c.display), ['Wholesale', 'Back Office'])
})

test('Program Access dispatches on the brand and keeps the EvoSys launcher body', () => {
  const src = read('../src/pages/program/ProgramAccessPortal.jsx')
  assert.match(src, /if \(isBookaBoostBrand\(props\.contexts\)\) return <BookaBoostProgramAccess \{\.\.\.props\} \/>/)
  assert.match(src, /return <EvoSysProgramAccess \{\.\.\.props\} \/>/)
  assert.match(src, /<span>EvoSys <b>Pro<\/b><\/span><small>Program Access<\/small>/)
})

test('BookaBoost screens never name another brand and every style is scoped', () => {
  const jsx = read('../src/pages/program/BookaBoostProgramAccess.jsx')
  assert.ok(!/EvoSys|EvoSense/.test(jsx.replace(/\/\*[\s\S]*?\*\//g, '')), 'no other brand name in the BookaBoost launcher')
  const css = read('../src/pages/program/bookaboostProgramAccess.css').replace(/\/\*[\s\S]*?\*\//g, '')
  const selectors = css.split('}').map(b => b.split('{')[0].trim()).filter(s => s && !s.startsWith('@') && !/^(from|to)$/.test(s))
  for (const s of selectors) for (const part of s.split(',')) {
    assert.match(part.trim(), /^(html:root \.bbpa |)\.bbpa/, 'unscoped selector: ' + part.trim())
  }
})

test('the logo files are the live BookaBoost website mark, exactly', () => {
  // Copied from the header of the live site (bookaboost.live, .nav-logo-mark).
  const shapes = [
    '<rect width="38" height="38" rx="9" fill="#c9973d"/>',
    '<rect x="9" y="10" width="13" height="5" rx="2.5" fill="white"/>',
    '<rect x="9" y="17" width="10" height="5" rx="2.5" fill="white"/>',
    '<rect x="9" y="24" width="13" height="4" rx="2" fill="white"/>',
    '<circle cx="27" cy="26" r="6" fill="#1c1007"/>',
    '<path d="M24.5 26l1.5 1.5 3-3" stroke="#c9973d" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  ]
  for (const f of ['bookaboost-mark.svg', 'bookaboost-logo.svg', 'bookaboost-logo-light.svg']) {
    const svg = read('../public/brands/' + f)
    assert.ok(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'), f)
    for (const sh of shapes) assert.ok(svg.includes(sh), f + ' is missing ' + sh)
  }
  for (const f of ['bookaboost-logo.svg', 'bookaboost-logo-light.svg']) {
    assert.match(read('../public/brands/' + f), />Booka<tspan fill="#c9973d">Boost<\/tspan><\/text>/)
  }
})
