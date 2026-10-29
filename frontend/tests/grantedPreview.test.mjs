// God-granted previews (deal_preview_grants) on Program Access.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { programCards, previewCards } from '../src/pages/program/programAccessModel.js'

const granted = { type: 'preview', grant_id: 'g1', title: 'Countryside Example Land', label: 'Authorized preview', path: '/previews/g1' }

test('a granted preview becomes one card that opens the signed-in path, never a demo link', () => {
  const cards = previewCards({ preview_contexts: [granted, { ...granted }] })
  assert.equal(cards.length, 1)
  const c = cards[0]
  assert.equal(c.display, 'Countryside Example Land')
  assert.equal(c.kind, 'AUTHORIZED PREVIEW')
  assert.equal(c.href, '/previews/g1')
  assert.equal(c.url, undefined)
  assert.equal(c.variant, 'preview')
})

test('granted previews sit with previews, never with workspaces', () => {
  const { visible, previews } = programCards({ preview_contexts: [granted] })
  assert.equal(visible.length, 0)
  assert.deepEqual(previews.map(p => p.display), ['Countryside Example Land'])
})

test('an entry with neither a link nor a grant path is ignored', () => {
  assert.deepEqual(previewCards({ preview_contexts: [{ type: 'preview', grant_id: 'g2', title: 'X' }] }), [])
})

test('the granted preview page uses the same sandboxed frame as the public demo page', () => {
  const strip = src => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const demo = strip(fs.readFileSync(new URL('../src/pages/portal/DemoSite.jsx', import.meta.url), 'utf8'))
  const page = fs.readFileSync(new URL('../src/pages/program/GrantedPreview.jsx', import.meta.url), 'utf8')
  const sandboxes = [...demo.matchAll(/sandbox=["']([^"']+)["']/g)].map(m => m[1])
  assert.deepEqual(sandboxes, ['allow-scripts'])
  assert.ok(!/allow-same-origin/.test(demo))
  assert.match(page, /import \{ DemoFrame \} from '\.\.\/portal\/DemoSite'/)
  assert.match(page, /\/program-access\/previews\//)
  assert.ok(!/iframe/.test(page.replace(/\/\*[\s\S]*?\*\//g, '')), 'no second, separately configured frame')
})
