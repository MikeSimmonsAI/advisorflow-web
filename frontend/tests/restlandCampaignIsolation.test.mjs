import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { defaultCampaign } from '../src/pages/program/sci/campaignDefault.js'

const RL = [
  { id: '1', key: 'existing_family_follow_up', name: 'Existing Family Follow-up' },
  { id: '2', key: 'restland_family_file_review', name: 'Restland — Family File Review' },
  { id: '3', key: 'restland_family_file_review_return', name: 'Restland — Family File Review — Return' },
]
const SCI = [
  { id: 'a', key: 'central_alabama_ms', name: 'Central Alabama 2025–2026 MS' },
  { id: 'b', key: 'veteran_planning_guide', name: 'Veteran Planning Guide' },
]

test('Restland opens on Family File Review; deep links still win', () => {
  assert.equal(defaultCampaign(RL).key, 'restland_family_file_review')
  assert.equal(defaultCampaign(RL, 'restland_family_file_review_return').key, 'restland_family_file_review_return')
  assert.equal(defaultCampaign(RL, 'central_alabama_ms').key, 'restland_family_file_review')   // not here: ignored
  assert.equal(defaultCampaign(RL, '', RL[2]).key, 'restland_family_file_review_return')       // keeps a reload's choice
})

test('SCI keeps its own default and its own campaign', () => {
  assert.equal(defaultCampaign(SCI).key, 'central_alabama_ms')
  assert.equal(defaultCampaign(SCI, 'veteran_planning_guide').key, 'veteran_planning_guide')
  assert.equal(defaultCampaign([]), null)
})

test("the import wizard names the program from its profile, never a hard-coded SCI", () => {
  const src = readFileSync(new URL('../src/pages/intake/ImportWizard.jsx', import.meta.url), 'utf8')
  const visible = src.split('\n').filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l) && /SCI/.test(l))
    .filter(l => !/SCI_(LEADS|FROZEN|REFUSAL|KEPT|KIND_LABEL|PROFILE)|sci-|_sci|sciSteps|sciStatus|is_sci|const sourceLabel/.test(l))
  assert.deepEqual(visible, [])
  assert.ok(src.includes("'Import Leads into ' + P"))
  assert.ok(src.includes("loadProgramProfile"))
  assert.ok(src.includes("sourceLabel(batch.source)"))                  // an old batch's stored label too
})
