// Blue Ridge (Gold & Jewelry) workspace shell + Settings: guards for the owner's
// 2026-11-06 UI corrections. Source-level checks; the browser run is in
// docs/jewelry/ui-correction-2026-11-06/.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const shell = readFileSync(new URL('../src/pages/jewelry/JewelryShell.jsx', import.meta.url), 'utf8')
const settings = readFileSync(new URL('../src/pages/jewelry/Settings.jsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/pages/jewelry/jewelry.css', import.meta.url), 'utf8')

test('header has no "Sending locked · AI test mode" chip', () => {
  assert.ok(!/Sending locked/.test(shell))
  assert.ok(!/jw-chip-status/.test(shell + css))
})

test('Settings → Connections still states the true messaging / AI status', () => {
  assert.match(settings, /Messaging \(texts & email\)/)
  assert.match(settings, /Nothing is sent from this workspace/)
  assert.match(settings, /AI receptionist live channel/)
})

test('Back to Program Access is one plain link to /workspaces in the right-hand header area', () => {
  const header = shell.slice(shell.indexOf('<header className="jw-top">'), shell.indexOf('</header>'))
  const right = header.slice(header.indexOf('jw-top__right'))
  assert.match(right, /<BackToProgramAccess \/>/)
  assert.equal((header.match(/<BackToProgramAccess/g) || []).length, 1)
  assert.match(shell, /href="\/workspaces"/)
  assert.ok(!/navigate\(-1\)|history\.back/.test(shell))
})

test('menu button is not a .jw-btn (whose display:inline-flex used to beat display:none on desktop)', () => {
  const btn = shell.slice(shell.indexOf('jw-top__menu') - 80, shell.indexOf('jw-top__menu') + 20)
  assert.ok(!/jw-btn/.test(btn))
  assert.match(css, /\.jw-top \.jw-top__menu\{display:none/)
  assert.match(css, /@media \(max-width:860px\)\{[^}]*?[\s\S]*?\.jw-top \.jw-top__menu\{display:inline-flex\}/)
})

test('drawer closes on Escape, close button, backdrop and after choosing a page', () => {
  assert.match(shell, /e\.key === 'Escape'/)
  assert.match(shell, /aria-label="Close navigation"/)
  assert.match(shell, /jw-scrim" onClick=\{\(\) => setOpen\(false\)\}/)
  assert.match(shell, /NavLink key=\{it\.to\}[^>]*onClick=\{\(\) => setOpen\(false\)\}/)
})

test('business hours: one labelled input per day, rows that cannot overflow', () => {
  assert.match(settings, /<label htmlFor=\{`jw-hours-\$\{k\}`\}>/)
  assert.match(settings, /id=\{`jw-hours-\$\{k\}`\}/)
  assert.ok(!/jw-input--sm/.test(settings))
  assert.match(css, /\.jw-hours__row\{display:grid;grid-template-columns:96px minmax\(0,1fr\)/)
  assert.ok(!/minmax\(200px,1fr\)/.test(css))
  // the same payload keys are saved
  assert.match(settings, /business_hours: \{ \.\.\.f\.business_hours, \[k\]: e\.target\.value \}/)
})
