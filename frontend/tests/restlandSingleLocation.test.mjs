// Issue #507 - Restland runs the SCI program shell with ONE location.
// Plain `node --test`: model checks + static guards on the shell files.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { programCards, programOf } from '../src/pages/program/programAccessModel.js'

const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')
const W = (id, name, extra) => ({ type: 'workspace', organization_id: id, organization_name: name, ...extra })

test('a non-SCI program workspace keeps its own funeral-home card', () => {
  const restland = W('r1', 'Restland Cemetery and Funeral Home', { is_program: true, program_is_sci: false, industry: 'funeral', experience: 'sci' })
  assert.equal(programOf(restland), 'funeral')
  const [card] = programCards({ workspace_contexts: [restland] }).visible
  assert.equal(card.display, 'Restland Cemetery and Funeral Home')
  assert.notEqual(card.kind, 'SCI · FAMILY SERVICES')
})

test('SCI still opens as SCI (flag absent or true)', () => {
  assert.equal(programOf(W('s1', 'Service Corporation International', { is_program: true })), 'sci')
  assert.equal(programOf(W('s1', 'Service Corporation International', { is_program: true, program_is_sci: true })), 'sci')
})

test('the shell draws its name from the program profile, not hard-coded SCI copy', () => {
  const nav = read('src/pages/program/command/SciNav.jsx')
  assert.match(nav, /prof\.footer/)
  assert.match(nav, /prof\.pilot_label/)
  assert.doesNotMatch(nav, /<p>SCI outreach proof of concept/)
  const top = read('src/pages/program/command/Dashboard.jsx')
  assert.match(top, /prof\.live_label/)
  assert.doesNotMatch(top, /<h1>SCI Live/)
})

test('one location: no location filter, no location leaderboard, SCI-only panels gated', () => {
  const pc = read('src/pages/program/ProgramCenter.jsx')
  assert.match(pc, /realLocations\.length === 1/)
  const dash = read('src/pages/program/sci/Dashboard.jsx')
  assert.match(dash, /\{!single && <LocationPerformance/)
  const perf = read('src/pages/program/command/Performance.jsx')
  assert.match(perf, /\(d\.locations_all \|\| \[\]\)\.length > 1 && <section/)
  const send = read('src/pages/program/sci/Send.jsx')
  assert.match(send, /prof\.sci_only_panels && <CentralAlabamaPrepare/)
  for (const f of ['sci/Leads.jsx', 'sci/Send.jsx', 'sci/Assets.jsx']) {
    assert.match(read('src/pages/program/' + f), /homes\.length > 1 &&/, f)
  }
})
