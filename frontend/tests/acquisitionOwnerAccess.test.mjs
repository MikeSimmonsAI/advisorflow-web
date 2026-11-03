// #452 P0 owner access (2026-11-03): the owner can always reach the Acquisition
// Control Center from God Mode, the shell names the signed-in login from the
// server, and a remembered client never carries over to another login.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = p => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8')

test('God Mode has a visible way to the Acquisition Control Center and Program Access', () => {
  const shell = read('pages/GodShell.jsx')
  assert.match(shell, /navigate\('\/acquisition-ops'\)/)
  assert.match(shell, /navigate\('\/workspaces'\)/)
  assert.match(read('pages/god/godNav.js'), /label: 'Acquisition Control Center', path: '\/acquisition-ops'/)
})

test('owner vs operator presentation comes from the server, not a guess', () => {
  const ops = read('pages/acquisition/AcquisitionOps.jsx')
  assert.match(ops, /useLoad\('\/acquisition-ops\/clients'\)/)
  assert.match(ops, /clients\.me\?\.email/)                       // identity shown as the server states it
  assert.doesNotMatch(ops, /is_god\s*=\s*true/)                  // never forced on in the client
  assert.match(ops, /clients\?\.is_god \? null/)                  // owner: no client auto-selected
})

test('the remembered client is per login', () => {
  const ops = read('pages/acquisition/AcquisitionOps.jsx')
  assert.match(ops, /'aq_client:' \+ \(getCurrentUser\(\)\?\.id/)
})

test('a 403 is not treated as a sign-out; only 401 clears the session', () => {
  const client = read('api/client.js')
  const i401 = client.indexOf('if (res.status === 401)')
  assert.ok(i401 > 0)
  assert.doesNotMatch(client, /res\.status === 403[\s\S]{0,200}clearToken/)
})
