// BookaBoost private sign-in: the guard rails that must hold.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

const R = (p) => readFileSync(new URL(p, import.meta.url))
const jsx = R('../src/pages/login-bookaboost/BookaBoostLogin.jsx').toString()
const css = R('../src/pages/login-bookaboost/bookaboost-login.css').toString()
const login = R('../src/pages/Login.jsx').toString()
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
const code = strip(jsx)

test('never names or links God Mode, the Executive Suite or administration areas', () => {
  assert.ok(!/god|executive|super ?admin|owner desk|command center|back office/i.test(code), 'administration wording on the sign-in page')
  assert.ok(!/href=\{?["'`]\/(god|executive|admin)/.test(code))
})

test('no invented numbers, coverage, ROI, testimonials or certifications', () => {
  assert.ok(!/\d+(\.\d+)?\s?[MK]\b|\d+\s?%|\d+\s?[x×]\b|nationwide|all 50|ROI|guarantee|testimonial|SOC|HIPAA|ISO 27001/i.test(code))
})

test('no third-party sign-in button (none is configured for BookaBoost)', () => {
  assert.ok(!/google|microsoft|apple|oauth|sso/i.test(code))
})

test('no credentials or demo accounts are written into the page', () => {
  const emails = [...code.matchAll(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g)].map((m) => m[0])
  assert.deepEqual([...new Set(emails)].sort(), ['name@company.com', 'support@bookaboost.live', 'you@company.com'])
  assert.ok(!/defaultValue|value=["'][^"']+["']/.test(code), 'a field has a hard-coded value')
})

test('no other brand leaks in, and Opportunity Radar is not duplicated here', () => {
  assert.ok(!/evosys|evo ?sys|harmony|advisorflow|evosense/i.test(code + css))
  assert.ok(!/opportunity radar|OpportunityRadar/i.test(code))
})

test('the four capability tiles, in order', () => {
  const tiles = [...code.matchAll(/\['\w+', '([^']+)'/g)].map((m) => m[1])
  assert.deepEqual(tiles, ['Property Intelligence', 'Market Research', 'Buyer Matching', 'Deal Room'])
})

test('only the official BookaBoost logo is shown', () => {
  const srcs = [...code.matchAll(/src="([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(srcs, ['/brands/bookaboost-logo.svg'])
  assert.ok(existsSync(new URL('../public/brands/bookaboost-logo.svg', import.meta.url)))
  assert.match(R('../public/brands/bookaboost-logo.svg').toString(), /live BookaBoost website header/)
})

test('only the BookaBoost theme gets this page; credential handling stays in Login.jsx', () => {
  assert.match(login, /if \(theme === THEMES\.BOOKABOOST\) \{\s*return \(\s*<BookaBoostLogin[^>]*onSubmit=\{handleSubmit\}/)
  assert.match(login, /if \(theme === THEMES\.EVOSYSPRO\) \{\s*return \(\s*<EvosysLogin/, 'the EvoSys Pro sign-in must be untouched')
  assert.ok(!/\blogin\(|localStorage|sessionStorage|af_token|fetch\(/.test(code), 'the presentation component handles credentials itself')
  assert.match(code, /onSubmit\(e\)/, 'the form must hand off to Login.jsx')
})

test('the old pre-need marketing copy is gone from the BookaBoost sign-in', () => {
  assert.ok(!/Pre-Need Edition|4\.8×|Funeral-industry CRM/.test(strip(login)))
})

test('accessible form: labels, autofill hints, password toggle, announced errors', () => {
  assert.match(code, /htmlFor="bbl-email"/)
  assert.match(code, /htmlFor="bbl-password"/)
  assert.match(code, /autoComplete="username"/)
  assert.match(code, /autoComplete="current-password"/)
  assert.match(code, /aria-label=\{show \? 'Hide password' : 'Show password'\}/)
  assert.match(code, /role="alert"/)
  assert.match(code, /role="status"/)
  assert.match(code, /\/forgot-password/)
})

test('every style is scoped to the BookaBoost sign-in', () => {
  const body = strip(css).replace(/@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '')
  const sels = [...body.matchAll(/([^{}@]+)\{[^{}]*\}/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.ok(sels.length > 40)
  for (const s of sels) for (const part of s.split(',')) assert.match(part.trim(), /^\.bbl/, `unscoped selector: ${part.trim()}`)
})
