/* SCI AND RESTLAND SHARE NOTHING (owner, Nov 17). Until the server says which
   program a screen belongs to - and if it never says - the screen shows no
   program's name, footer or panels. It used to fall back to SCI's. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/pages/program/useProgramProfile.js', import.meta.url), 'utf8')
const block = src.slice(src.indexOf('export const NEUTRAL_PROFILE'), src.indexOf('})', src.indexOf('export const NEUTRAL_PROFILE')) + 2)

test('the loading / failed fallback is neutral, not SCI', () => {
  assert.ok(block.length > 40, 'NEUTRAL_PROFILE is defined')
  assert.doesNotMatch(block, /SCI|Service Corporation|Restland/)
  assert.match(block, /sci_only_panels:\s*false/)
  assert.match(block, /is_sci:\s*false/)
})

test('nothing falls back to SCI_PROFILE', () => {
  const uses = src.split('\n').filter(l => /SCI_PROFILE/.test(l) && !/export const SCI_PROFILE/.test(l))
  assert.deepEqual(uses, [], 'SCI wording is used only when the server says the program IS SCI')
})
