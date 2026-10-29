// EvoSys Pro sign-in (owner-approved design): the guard rails that must hold.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'

const R = (p) => readFileSync(new URL(p, import.meta.url))
const jsx = R('../src/pages/login-evosys/EvosysLogin.jsx').toString()
const css = R('../src/pages/login-evosys/evosys-login.css').toString()
const login = R('../src/pages/Login.jsx').toString()
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const code = strip(jsx)

test('the public page never names or links God Mode, the Executive Suite or admin areas', () => {
  assert.ok(!/god|executive|super ?admin|owner desk|command center/i.test(code), 'administration wording in the public page')
  assert.ok(!/href=\{?["'`]\/(god|executive|admin)/.test(code))
})

test('no invented statistics, testimonials, certifications or provider sign-in buttons', () => {
  assert.ok(!/\d+(\.\d+)?\s?[MK]\b|\d+%|99\.9|10x|10×|SOC|HIPAA|ISO 27001|testimonial/i.test(code))
  assert.ok(!/google|microsoft|apple|oauth|sso/i.test(code), 'a third-party sign-in control appeared')
})

test('no credentials are written into the page', () => {
  const emails = [...code.matchAll(/[\w.+-]+@[\w-]+\.[\w.]+/g)].map((m) => m[0])
  assert.deepEqual([...new Set(emails)], ['support@evosyspro.live', 'you@company.com'])
  assert.ok(!/defaultValue|value=["'][^"']+["']/.test(code), 'a field has a hard-coded value')
})

test('six industry cards, Wholesale first and featured, no generic Real Estate card, none of them links', () => {
  const names = [...code.matchAll(/name: '([^']+)'/g)].map((m) => m[1])
  assert.deepEqual(names, ['Wholesale', 'Roofing', 'Insurance', 'Energy', 'Funeral Services', 'Commercial Services'])
  assert.match(code, /key: 'wholesale'[^}]*featured: true/)
  assert.ok(!/Real Estate'/.test(code))
  const cards = code.slice(code.indexOf('<ul className="evl-cards">'), code.indexOf('</ul>', code.indexOf('<ul className="evl-cards">')))
  assert.ok(!/<a |<Link |<button/.test(cards), 'an industry card became a link')
})

test('the official logo is the public-site mark, byte for byte, and its on-dark rendition is the only one shown', () => {
  const md5 = (p) => createHash('md5').update(R(p)).digest('hex')
  assert.equal(md5('../public/brands/evosyspro-logo.png'), md5('../../public-site/assets/evosyspro-logo.png'))
  assert.ok(existsSync(new URL('../public/brands/evosyspro-logo-on-dark-440.png', import.meta.url)))
  const srcs = [...code.matchAll(/src="([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(srcs, ['/brands/evosyspro-logo-on-dark-440.png'])
})

test('only the EvoSys Pro host gets this page; credential handling stays in Login.jsx', () => {
  assert.match(login, /if \(theme === THEMES\.EVOSYSPRO\) \{\s*return \(\s*<EvosysLogin[^>]*onSubmit=\{handleSubmit\}/)
  assert.ok(!/\blogin\(|localStorage|af_token|fetch\(/.test(code), 'the presentation component handles credentials itself')
})

test('every style is scoped to the EvoSys sign-in', () => {
  const body = strip(css).replace(/@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '')
  const sels = [...body.matchAll(/([^{}]+)\{[^{}]*\}/g)].map((m) => m[1].trim())
  assert.ok(sels.length > 40)
  for (const s of sels) for (const part of s.split(',')) assert.match(part.trim(), /^\.evl/, `unscoped selector: ${part.trim()}`)
})
