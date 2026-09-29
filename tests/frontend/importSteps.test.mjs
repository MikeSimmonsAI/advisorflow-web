// Step 6 -> Step 7 gating of the guided importer (frontend/src/pages/intake/importSteps.js).
import assert from 'node:assert/strict'
import { approveEnabled, approveBlockedReason, commitGate, orgNameMatches, reachableStep, resolveStep, stepAfterCommit, stepFor }
  from '../../frontend/src/pages/intake/importSteps.js'

const wf = (step, extra = {}) => ({ step, analyzed: true, classified: false, ...extra })
const ready = { status: 'ready_for_review', analysis: { status: {} }, workflow: wf(5), commit_gate: { can_commit: true } }
const staged = { status: 'staged', analysis: { status: {} }, workflow: wf(6), commit_gate: { can_commit: true } }
const failedCommit = { status: 'failed', analysis: { status: {} }, commit_mode: 'ready_only',
                       workflow: wf(5), commit_gate: { can_commit: true } }
const remapped = { status: 'mapping', analysis: { status: {} },
                   workflow: wf(2), commit_gate: { can_commit: false, reason: 'The field mapping or classification changed since the last analysis.' } }
const committing = { status: 'committing', analysis: { status: {} }, workflow: wf(7), commit_gate: { can_commit: false } }
const partial = { status: 'partially_committed', analysis: { status: {} }, workflow: wf(7), commit_gate: { can_commit: true } }
let n = 0
const t = (name, fn) => { fn(); n++; console.log('ok -', name) }

t('every successful decision (incl. Keep staged) moves to Step 7', () => {
  for (const m of ['stage_only', 'ready_only', 'ready_and_review']) assert.equal(stepAfterCommit(m), 7)
})
t('ready batch: Step 6 reachable and Approve enabled once the org name is typed', () => {
  assert.equal(resolveStep(ready, 6, true), 6)
  assert.equal(approveEnabled(ready, 'ready_only', '', 'QA Energy Co'), false)
  assert.equal(approveEnabled(ready, 'ready_only', '  qa energy co ', 'QA Energy Co'), true)
  assert.equal(approveEnabled(ready, 'stage_only', '', 'QA Energy Co'), true)
  assert.equal(approveEnabled(ready, 'ready_only', 'QA Energy Co', 'QA Energy Co', true), false, 'busy')
})
t('an unknown org name never enables a real import (empty === empty was true before)', () => {
  assert.equal(approveEnabled(ready, 'ready_only', '', ''), false)
})
t('staged batch: Step 7 reachable (Results), Step 6 still opens to import later', () => {
  assert.equal(reachableStep(staged), 7)
  assert.equal(resolveStep(staged, 7, true), 7)
  assert.equal(resolveStep(staged, 6, true), 6)
  assert.equal(approveEnabled(staged, 'ready_only', 'x', 'x'), true)
})
t('a failed COMMIT is retryable from Step 6 (server says can_commit)', () => {
  assert.equal(commitGate(failedCommit).ok, true)
  assert.equal(approveEnabled(failedCommit, 'ready_only', 'Org', 'Org'), true)
})
t('a re-mapped batch shows WHY it cannot be imported instead of a dead button', () => {
  const g = commitGate(remapped)
  assert.equal(g.ok, false)
  assert.match(g.reason, /mapping/)
  assert.equal(approveEnabled(remapped, 'stage_only', '', 'Org'), false)
})
t('committing / committed batches land on Step 7 whatever the URL says', () => {
  assert.equal(resolveStep(committing, 6, true), 7)
  assert.equal(resolveStep(committing, 2, true), 7)
  assert.equal(stepFor(committing), 7)
})
t('partially committed: Results by default, Approve still reachable for the rest', () => {
  assert.equal(resolveStep(partial, null, true), 7)
  assert.equal(resolveStep(partial, 6, true), 6)
})
t('older server without can_commit falls back to the status list', () => {
  const old = { status: 'ready_for_review', analysis: { status: {} } }
  assert.equal(commitGate(old).ok, true)
  assert.equal(commitGate({ status: 'mapping' }).ok, false)
  assert.equal(stepFor({ status: 'staged' }), 6)
})
t('still loading: no batch -> URL step or 0', () => {
  assert.equal(resolveStep(null, 6, true), 6)
  assert.equal(resolveStep(null, 0, true), 0)
  assert.equal(resolveStep(null, 0, false), 1)
})
t('the confirmation ignores case, spacing and stray punctuation; empty never matches', () => {
  const org = 'EVO Integrated Solutions LLC'
  assert.equal(orgNameMatches('evo integrated  solutions llc.', org), true)
  assert.equal(orgNameMatches(' EVO Integrated Solutions, LLC ', org), true)
  assert.equal(orgNameMatches('', org), false)
  assert.equal(orgNameMatches('EVO Integrated', org), false)
  assert.equal(orgNameMatches('anything', ''), false)
})
t('a disabled Import button always says why (empty box, mismatch)', () => {
  const b = { status: 'ready_for_review', commit_gate: { can_commit: true, reason: null } }
  const org = 'EVO Integrated Solutions LLC'
  assert.match(approveBlockedReason(b, 'ready_plus_review', '', org), /Type .*EVO Integrated Solutions LLC/)
  assert.match(approveBlockedReason(b, 'ready_plus_review', 'EVO', org), /doesn't match/)
  assert.equal(approveBlockedReason(b, 'ready_plus_review', 'evo integrated solutions llc', org), null)
  assert.equal(approveEnabled(b, 'ready_plus_review', 'evo integrated solutions llc', org), true)
  assert.equal(approveBlockedReason(b, 'stage_only', '', org), null)
})
console.log(`${n} passed`)
