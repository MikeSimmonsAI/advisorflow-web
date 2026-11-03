// #452 Harvey AI workforce panel: every control calls a real server action,
// it lives inside the existing Control Center / Worker Health pages, and the
// draft approval is the existing Send button.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = (f) => readFileSync(new URL(`../src/pages/acquisition/${f}`, import.meta.url), 'utf8')

test('each workforce control calls the server', () => {
  const s = src('HarveyWorkforce.jsx')
  for (const a of ["act('start'", "act('pause'", "act('resume'", "act('stop'", "act('emergency-stop'", "act('practice-test'"]) {
    assert.ok(s.includes(a), a)
  }
  assert.ok(s.includes('/god/acquisition/workforce'))
  assert.ok(s.includes('/god/acquisition/workforce/clients/'))
  assert.ok(!/setInterval|@keyframes|--pulse/.test(s), 'no decorative live animation')
})

test('panel is mounted in the existing pages, not a new app', () => {
  assert.ok(src('HubPages.jsx').includes('<HarveyWorkforcePanel compact />'))
  assert.ok(src('AcquisitionOps.jsx').includes('{ov.master && <HarveyWorkforcePanel />}'))
})

test('a workforce draft is approved by the existing Send, with its id', () => {
  const s = src('AcquisitionOps.jsx')
  assert.ok(s.includes('draft_id: draft.id'))
  assert.ok(s.includes('/drafts/${draft.id}/reject'))
})
