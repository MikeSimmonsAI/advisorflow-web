/**
 * GOD MODE DESIGN SYSTEM — THE GUARD.
 *
 * This does not test that God Mode is pretty. It tests the four things that,
 * when they were untrue, produced a control plane nobody could read — and any
 * one of which could quietly become untrue again in a future change:
 *
 *   1. GOD MODE IS PERMANENTLY LIGHT and does not consult the appearance
 *      preference. A `[data-appearance="dark"]` rule reaching back into the God
 *      token sheet, or the toggle returning to the God shell, would reintroduce
 *      the rejected design through the side door.
 *
 *   2. EVERY TOKEN A GOD FILE USES IS ACTUALLY DECLARED. This is the bug that
 *      caused the damage: `--god-*` was referenced 105 times and declared
 *      nowhere, and `--go-*` was declared on a class six screens never render.
 *      An unresolvable var() does not fall back — it invalidates its whole
 *      declaration, which is how Create Customer ended up with input fields
 *      that had no border at all. A test that only checked colours would have
 *      passed the entire time.
 *
 *   3. NO HARD-CODED COLOUR LITERALS in the God component tree. ~890 inline
 *      hex and rgba values were what made this impossible to fix from a
 *      stylesheet in the first place. One new literal is one new screen that
 *      does not follow the system.
 *
 *   4. THE CUSTOMER BOUNDARY HOLDS. Nothing in the God sheet may declare or
 *      repaint a tenant/white-label token, and every selector must be anchored
 *      inside the God scope. EvoSys Pro, BookaBoost, Harmony Hustle and
 *      Atlantis branding is configuration and this file must not be able to
 *      touch it.
 *
 * Run: node --test frontend/tests/godTheme.test.mjs
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

const read = (p) => readFileSync(join(SRC, p), 'utf8')

/** Every file that renders inside the God shell. */
function godFiles() {
  const out = []
  for (const f of readdirSync(join(SRC, 'pages', 'god'))) {
    if (f.endsWith('.jsx') || f.endsWith('.js') || f.endsWith('.css')) out.push(join('pages', 'god', f))
  }
  for (const f of readdirSync(join(SRC, 'pages'))) {
    if (f.startsWith('God') && (f.endsWith('.jsx') || f.endsWith('.js'))) out.push(join('pages', f))
  }
  return out
}

const TOKENS = read(join('pages', 'god', 'godTokens.css'))
/** The sheet with comments removed. A rule this file bans is only a problem
 *  when it is CODE — the header comment explains what was removed and why, and
 *  a test that cannot tell those apart makes the explanation unwritable. */
const TOKEN_CODE = TOKENS.replace(/\/\*[\s\S]*?\*\//g, '')
const SHELL = read(join('pages', 'GodShell.jsx'))

// ── 1. PERMANENTLY LIGHT ────────────────────────────────────────────────────

test('the God token sheet has no dark appearance variant', () => {
  assert.equal(
    /\[data-appearance/.test(TOKEN_CODE), false,
    'godTokens.css references data-appearance. God Mode is permanently light; '
    + 'a dark block here brings the rejected design back.'
  )
})

test('the God scope pins color-scheme to light', () => {
  // Without this the native widgets - the open <select> list, the date picker,
  // the scrollbars - follow the document root, so a user whose TENANT app is
  // dark gets a black dropdown over a white God form.
  assert.match(TOKEN_CODE, /color-scheme:\s*light/)
  assert.equal(/color-scheme:\s*dark/.test(TOKEN_CODE), false)
})

test('the appearance toggle is not mounted in the God shell', () => {
  assert.equal(
    /AppearanceToggle/.test(SHELL), false,
    'GodShell renders the light/dark control. In a permanently-light God Mode '
    + 'that control switches between one state and itself.'
  )
})

test('no God file reads the appearance preference', () => {
  for (const f of godFiles()) {
    const body = read(f)
    assert.equal(
      /from ['"].*\/appearance['"]|getAppearancePreference|effectiveAppearance/.test(body), false,
      `${f} consults the appearance preference. God Mode does not have one.`
    )
  }
})

// ── 2. EVERY TOKEN RESOLVES ─────────────────────────────────────────────────

test('every --gm-/--go-/--god- token used anywhere in God Mode is declared', () => {
  // NOT anchored to line start: the pill families are declared three to a row,
  // and an anchored pattern would silently "find" only the first of each three
  // and then fail the sheet for tokens that are in fact declared.
  const declared = new Set([...TOKEN_CODE.matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1]))
  const missing = new Map()

  for (const f of godFiles()) {
    const body = read(f)
    for (const m of body.matchAll(/var\(\s*(--(?:gm|go|god)-[\w-]+)/g)) {
      if (!declared.has(m[1])) {
        if (!missing.has(m[1])) missing.set(m[1], new Set())
        missing.get(m[1]).add(f)
      }
    }
  }

  assert.deepEqual(
    [...missing.keys()], [],
    'Undeclared custom properties: an unresolvable var() invalidates its whole '
    + 'declaration, so these paint NOTHING rather than a fallback. '
    + [...missing.entries()].map(([k, v]) => `${k} (${[...v].join(', ')})`).join(' · ')
  )
})

test('the tokens are declared on a scope every God route actually renders', () => {
  // `.go-scope` alone was the original trap: six screens use `go-` classes and
  // never render that class. `.gm-shell` is on the shell itself, so no page can
  // fall off the palette by omitting a wrapper.
  const selector = TOKENS.slice(0, TOKENS.indexOf('{', TOKENS.indexOf('.gm-shell')))
  for (const cls of ['.gm-shell', '.gm-scope', '.go-scope']) {
    assert.ok(selector.includes(cls), `${cls} is not part of the token declaration`)
  }
  assert.match(SHELL, /className="gm-shell"/,
    'GodShell must carry gm-shell — it is the element the palette is declared on.')
})

// ── 3. NO HARD-CODED COLOURS ────────────────────────────────────────────────

test('no God component carries a hard-coded colour literal', () => {
  // Quoted colour values only: this is about values the code PAINTS with, not
  // about hexes mentioned in a comment explaining what used to be here.
  const LITERAL = /'(?:\s*#[0-9a-fA-F]{3,8}\s*|\s*rgba?\([^')]*\)\s*)'/g
  const offenders = []
  for (const f of godFiles()) {
    if (f.endsWith('godTokens.css')) continue          // the one file that MAY hold values
    const body = read(f)
    for (const m of body.matchAll(LITERAL)) offenders.push(`${f}: ${m[0]}`)
  }
  assert.deepEqual(offenders, [],
    'Hard-coded colours in God components. An inline literal cannot be '
    + 'overridden by any stylesheet, which is what made the old design '
    + 'unfixable from the outside. Use a --gm-* token.')
})

test('the God component sheet holds no literals either', () => {
  const sheet = read(join('pages', 'god', 'GodStyles.jsx'))
  const css = sheet.slice(sheet.indexOf('const CSS = `'), sheet.lastIndexOf('`'))
                   .replace(/\/\*[\s\S]*?\*\//g, '')
  const hex = [...css.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map(m => m[0])
  assert.deepEqual(hex, [], `GodStyles.jsx has colour literals: ${hex.join(', ')}`)
})

// ── 4. THE CUSTOMER BOUNDARY ────────────────────────────────────────────────

test('the God sheet never declares a tenant or white-label token', () => {
  // These are the names index.css and appearance.css own, and they are what a
  // customer workspace paints from. Declaring one here - even accidentally -
  // would let the control plane repaint a customer's branding.
  const forbidden = [
    '--bg-base', '--bg-panel', '--bg-card', '--bg-field',
    '--text-primary', '--text-secondary', '--text-tertiary',
    '--signal-blue', '--signal-green', '--signal-red', '--signal-amber', '--signal-purple',
    '--border-subtle', '--border-strong', '--brand-platform-accent',
  ]
  for (const name of forbidden) {
    assert.equal(
      new RegExp(`(^|;)\\s*${name}\\s*:`, 'm').test(TOKEN_CODE), false,
      `godTokens.css declares ${name}, which belongs to the tenant/white-label palette.`
    )
  }
})

test('every selector in the God sheet is anchored inside the God scope', () => {
  const selectors = [...TOKEN_CODE.matchAll(/(^|\})\s*([^{}@]+)\{/g)].map(m => m[2].trim())
  for (const block of selectors) {
    for (const sel of block.split(',').map(s => s.trim()).filter(Boolean)) {
      assert.ok(
        /(^|\s|>)\.(gm-shell|gm-scope|go-scope)\b/.test(sel),
        `Selector "${sel}" is not anchored to the God scope — it can reach a customer workspace.`
      )
    }
  }
})

test('the tenant appearance system is untouched and still has both modes', () => {
  // God Mode dropping dark must not have removed the choice from the TENANT
  // app, where it is a real preference somebody may be relying on.
  const appearance = read(join('styles', 'appearance.css'))
  assert.match(appearance, /\[data-appearance="light"\]/)
  assert.match(appearance, /\[data-appearance="dark"\]/)
  const js = read('appearance.js')
  assert.match(js, /export const LIGHT/)
  assert.match(js, /export const DARK/)
})

test('the appearance control still exists for the tenant app', () => {
  // Removed from the God rail, NOT deleted from the product.
  const settings = read(join('pages', 'Settings.jsx'))
  assert.match(settings, /AppearanceToggle/)
})
