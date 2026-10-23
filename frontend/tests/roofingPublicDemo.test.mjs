// #299 - the public Roofing demo must stay disconnected from real data.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const DIR = new URL('../src/pages/roofing/demo/', import.meta.url).pathname
const files = readdirSync(DIR).filter((f) => /\.(jsx?|css)$/.test(f))
const src = Object.fromEntries(files.map((f) => [f, readFileSync(join(DIR, f), 'utf8')]))
// Code only: comments (which explain what the demo avoids) are not checked.
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const code = Object.entries(src).filter(([f]) => /\.jsx?$/.test(f)).map(([f, s]) => [f, strip(s)])

test('the demo never calls the Roofing API or any backend', () => {
  for (const [f, s] of code) {
    assert.ok(!/['"`]\/(roofing-ops|communications|intake|leads|auth|god)\//.test(s), `${f} references a private endpoint`)
    assert.ok(!/from '\.\.\/\.\.\/\.\.\/api\/client'|from '\.\.\/\.\.\/api\/client'|\bapi\.(get|post|patch|put|delete|upload)\(/.test(s), `${f} uses the API client`)
    assert.ok(!/\bfetch\(|XMLHttpRequest|localStorage|af_token/.test(s), `${f} fetches, or touches stored credentials`)
  }
})

test('the demo does not mount the private shell or protected routes', () => {
  for (const [f, s] of code) {
    assert.ok(!/ProtectedRoute|useRoofing\(|from '\.\.\/RoofingShell'\s*$/m.test(s.replace("import { BrandMark } from '../RoofingShell'", '')), `${f} uses the private shell`)
    assert.ok(!/to=\{?[`'"]\/program\/roofing/.test(s), `${f} links into the private workspace`)
  }
})

test('sample people are fictional: 555 numbers and example.com emails only', () => {
  const f = src['fixtures.js']
  const phones = f.match(/\(\d{3}\) \d{3}-\d{4}/g) || []
  assert.ok(phones.length > 0 && phones.every((p) => p.includes(') 555-')), 'non-555 phone number in fixtures')
  const emails = f.match(/[\w.]+@[\w.]+/g) || []
  assert.ok(emails.every((e) => e.endsWith('example.com')), 'non-example.com email in fixtures')
})

test('the routes are public and the homepage links to the demo', () => {
  const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(app, /<Route path="\/roofing\/demo\/\*" element=\{<RoofingDemo \/>\} \/>/)
  assert.ok(!/<Route path="\/roofing\/demo[^>]*ProtectedRoute/.test(app))
  const landing = readFileSync(new URL('../src/pages/roofing/RoofingLanding.jsx', import.meta.url), 'utf8')
  assert.match(landing, /to="\/roofing\/demo"/)
  assert.match(landing, /Explore the Roofing Demo/)
  assert.match(landing, /<DemoForm \/>/)          // the Book a Demo form is still there
})

test('every action that would change something shows a preview instead', () => {
  const all = code.map(([, s]) => s).join('\n')
  assert.ok((all.match(/preview\(/g) || []).length >= 12)
  assert.match(src['DemoApp.jsx'], /Nothing was sent, saved or booked/)
})
