// OLP (Overlooked Properties, powered by BookaBoost) sign-in: guard rails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

const R = (p) => readFileSync(new URL(p, import.meta.url))
const jsx = R('../src/pages/login-olp/OLPLogin.jsx').toString()
const css = R('../src/pages/login-olp/olp-login.css').toString()
const login = R('../src/pages/Login.jsx').toString()
const brand = R('../src/brand/productBrand.js').toString()
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
const code = strip(jsx)

test('OLP leads and BookaBoost is the endorsement', () => {
  assert.match(brand, /short: 'OLP'/)
  assert.match(brand, /name: 'Overlooked Properties'/)
  assert.match(brand, /endorsement: 'Powered by BookaBoost'/)
  assert.match(code, /OLP\.endorsement/)
})

test('never names or links God Mode, the Executive Suite or administration areas', () => {
  assert.ok(!/god|executive|super ?admin|owner desk|command center|back office/i.test(code))
  assert.ok(!/href=\{?["'`]\/(god|executive|admin)/.test(code))
})

test('no invented numbers, coverage, ROI, badges or trademark claims', () => {
  assert.ok(!/\d+(\.\d+)?\s?[MK]\b|\d+\s?%|\d+\s?[x×]\b|nationwide|every market|all 50|ROI|guarantee|testimonial|SOC|HIPAA|ISO 27001|™|®/i.test(code))
})

test('no third-party sign-in button and no old pre-need wording', () => {
  assert.ok(!/google|microsoft|apple|oauth|sso/i.test(code))
  assert.ok(!/pre-need|funeral|cemetery/i.test(code))
})

test('no credentials or demo accounts in the page', () => {
  const emails = [...code.matchAll(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g)].map((m) => m[0])
  assert.deepEqual([...new Set(emails)].sort(), ['name@company.com', 'support@bookaboost.live', 'you@company.com'])
  assert.ok(!/defaultValue|value=["'][^"']+["']/.test(code))
})

test('no other brand leaks in', () => {
  assert.ok(!/evosys|evo ?sys|harmony|advisorflow|evosense/i.test(code + css))
})

test('four capability tiles in order, and none of them is a link or button', () => {
  const caps = code.slice(code.indexOf('const CAPABILITIES'), code.indexOf('const MARKETS'))
  const tiles = [...caps.matchAll(/\['\w+', '([^']+)'/g)].map((m) => m[1])
  assert.deepEqual(tiles, ['Opportunity Radar', 'Property Intelligence', 'Buyer Matching', 'Deal Room'])
  const ul = code.slice(code.indexOf('<ul className="olp-caps"'), code.indexOf('</ul>', code.indexOf('<ul className="olp-caps"')))
  assert.ok(!/<a |<Link |<button|onClick/.test(ul))
})

test('the five markets', () => {
  assert.match(code, /\['Residential', 'Wholesale & distressed', 'Land', 'Farms & ranches', 'Commercial'\]/)
})

test('the owner-supplied OLP logo files are the ones used, and they are plain vector art', () => {
  assert.match(brand, /logo: '\/brands\/olp\/OLP_logo_primary_dark\.svg'/)
  for (const f of ['OLP_logo_primary_dark', 'OLP_logo_primary_light', 'OLP_logo_horizontal_dark', 'OLP_logo_horizontal_light', 'OLP_icon']) {
    const svg = R(`../public/brands/olp/${f}.svg`).toString()
    assert.ok(!/<script|javascript:|href=|<foreignObject|<image/i.test(svg), `${f}.svg must be static vector art`)
    assert.ok(!/™|®|&#8482;|&#174;/.test(svg), `${f}.svg must not claim a trademark`)
  }
  assert.ok(!/src="\/brands\//.test(code), 'logo paths come from the brand file only')
})

test('Login.jsx keeps credentials; OLP only for the BookaBoost theme; the approved pages are still there', () => {
  assert.match(login, /if \(theme === THEMES\.BOOKABOOST && bookaboostProductBrand\(\) === 'olp'\) \{\s*return \(\s*<OLPLogin[^>]*onSubmit=\{handleSubmit\}/)
  assert.match(login, /if \(theme === THEMES\.BOOKABOOST\) \{\s*return \(\s*<BookaBoostLogin/)
  assert.match(login, /if \(theme === THEMES\.EVOSYSPRO\) \{\s*return \(\s*<EvosysLogin/)
  assert.ok(!/\blogin\(|localStorage|sessionStorage|af_token|fetch\(/.test(code))
  assert.match(code, /onSubmit\(e\)/)
})

test('the build switch: OLP by default, plain BookaBoost on request', async () => {
  const mod = await import('../src/brand/productBrand.js')
  assert.equal(mod.bookaboostProductBrand({}), 'olp')
  assert.equal(mod.bookaboostProductBrand({ VITE_BOOKABOOST_PRODUCT_BRAND: 'bookaboost' }), 'bookaboost')
  assert.equal(mod.bookaboostProductBrand({ VITE_BOOKABOOST_PRODUCT_BRAND: 'anything-else' }), 'olp')
})

test('accessible form: labels, autofill hints, password toggle, announced errors', () => {
  for (const re of [/htmlFor="olp-email"/, /htmlFor="olp-password"/, /autoComplete="username"/, /autoComplete="current-password"/,
                    /aria-label=\{show \? 'Hide password' : 'Show password'\}/, /role="alert"/, /role="status"/, /\/forgot-password/]) {
    assert.match(code, re)
  }
})

test('every style is scoped to the OLP sign-in', () => {
  const body = strip(css).replace(/@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '')
  const sels = [...body.matchAll(/([^{}@]+)\{[^{}]*\}/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.ok(sels.length > 40)
  for (const s of sels) for (const part of s.split(',')) assert.match(part.trim(), /^\.olp/, `unscoped selector: ${part.trim()}`)
})
