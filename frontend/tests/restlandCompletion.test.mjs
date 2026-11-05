// Issue #507 completion: Restland theme scoped by the server, families browse,
// earlier conversations, appointment history. Plain `node --test`.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')
const css = read('src/pages/program/RestlandTheme.css')
const cmd = read('src/pages/program/command/Command.jsx')
const pc = read('src/pages/program/ProgramCenter.jsx')

test('theme attribute on both shells comes from the server profile', () => {
  assert.match(cmd, /data-program-theme=\{profile\.theme \|\| undefined\}/)
  assert.match(pc, /data-program-theme=\{profile\.theme \|\| undefined\}/)
})

test('every recolouring rule is scoped to the Restland theme', () => {
  const body = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const selectors = [...body.matchAll(/([^{}]+)\{[^{}]*\}/g)].map(m => m[1].trim())
  for (const sel of selectors) {
    for (const part of sel.split(',').map(x => x.trim())) {
      assert.ok(part.includes('[data-program-theme="restland"]') || part.startsWith('.rl-'), `unscoped: ${part}`)
    }
  }
  for (const c of ['#193c32', '#285347', '#35694f', '#f7f5ed', '#fffefa', '#c3a66a', '#24392f']) assert.ok(css.includes(c), c)
})

test('families browse with paging and a real error, never "no families" on failure', () => {
  assert.match(cmd, /offset: String\(page \* PAGE\)/)
  assert.match(cmd, /Could not load families/)
  assert.match(cmd, /if \(n === seq\.current\)/)
  assert.match(cmd, /useEffect\(\(\) => \{ setPage\(0\) \}, \[term, loc, show\]\)/)
})

test('appointments offer upcoming, past and all with an error state', () => {
  assert.match(cmd, /\/program\/appointments\?when=\$\{when\}/)
  assert.match(cmd, /Could not load appointments/)
  assert.match(cmd, /<EarlierConversations open=\{openFamily\} \/>/)
  assert.match(cmd, /EARLIER REPLY/)
})
