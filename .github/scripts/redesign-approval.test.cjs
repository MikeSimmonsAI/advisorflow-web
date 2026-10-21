// node --test .github/scripts/redesign-approval.test.cjs
const assert = require('node:assert/strict')
const test = require('node:test')
const g = require('./redesign-approval.cjs')

const BODY = '## Affected screens\n/x\n## Screenshots\n...\n## Tests\n...\n## Build\nok\n'
const pr = (o = {}) => ({ number: 300, draft: false, body: BODY, labels: [], head: { ref: 'feature/x', sha: 'abc' }, ...o })

test('unrelated PRs always pass - other lanes are never blocked', () => {
  for (const ref of ['feature/sci-x', 'fix/workspace-identity', 'chatgpt/sci-pilot', 'chore/redesign-approval-gate'])
    for (const draft of [true, false])
      assert.equal(g.evaluate(pr({ head: { ref }, draft, body: '', labels: [{ name: 'lane:sci' }] })).ok, true, ref)
})

test('a redesign is recognised by label or by redesign/* branch', () => {
  assert.equal(g.isRedesign(pr({ labels: [{ name: 'lane:ui-redesign' }] })), true)
  assert.equal(g.isRedesign(pr({ head: { ref: 'redesign/sci-dashboard' } })), true)
  assert.equal(g.isRedesign(pr()), false)
})

test('a redesign without the owner label waits, naming the exact approval phrase', () => {
  const r = g.evaluate(pr({ head: { ref: 'redesign/a', sha: 'abc' } }))
  assert.equal(r.ok, false)
  assert.match(r.summary, /APPROVE REDESIGN PR #300 FOR MERGE/)
})

test('a redesign with evidence + owner label + not draft passes', () => {
  assert.equal(g.evaluate(pr({ head: { ref: 'redesign/a', sha: 'abc' }, labels: ['owner-approved'] })).ok, true)
})

test('drafts, missing evidence and gate edits all block a redesign', () => {
  const base = { head: { ref: 'redesign/a', sha: 'abc' }, labels: ['owner-approved'] }
  assert.equal(g.evaluate(pr({ ...base, draft: true })).ok, false)
  for (const [name] of g.SECTIONS) {
    const r = g.evaluate(pr({ ...base, body: BODY.replace(new RegExp('## ' + name + '[^\n]*'), '## other') }))
    assert.equal(r.ok, false, name)
    assert.match(r.summary, new RegExp(name))
  }
  const r = g.evaluate(pr(base), [], '', ['frontend/x.jsx', '.github/scripts/redesign-approval.cjs'])
  assert.equal(r.ok, false)
})

test('with an independent approver configured, the label alone is not enough', () => {
  const base = { head: { ref: 'redesign/a', sha: 'abc' }, labels: ['owner-approved'] }
  const rev = (login, state, commit_id) => ({ user: { login }, state, commit_id })
  assert.equal(g.evaluate(pr(base), [], 'Mike-Approver').ok, false)
  assert.equal(g.evaluate(pr(base), [rev('MikeSimmonsAI', 'APPROVED', 'abc')], 'Mike-Approver').ok, false, 'shared identity does not count')
  assert.equal(g.evaluate(pr(base), [rev('mike-approver', 'APPROVED', 'old')], 'Mike-Approver').ok, false, 'stale approval')
  assert.equal(g.evaluate(pr(base), [rev('mike-approver', 'APPROVED', 'abc')], 'Mike-Approver').ok, true)
  assert.equal(g.evaluate(pr(base), [rev('mike-approver', 'APPROVED', 'abc'), rev('mike-approver', 'CHANGES_REQUESTED', 'abc')], 'Mike-Approver').ok, false, 'latest review wins')
})

test('new commits after approval clear the approval', () => {
  const approved = pr({ head: { ref: 'redesign/a' }, labels: ['owner-approved'] })
  assert.equal(g.shouldClearApproval('pull_request', 'synchronize', approved), true)
  assert.equal(g.shouldClearApproval('pull_request', 'labeled', approved), false)
  assert.equal(g.shouldClearApproval('pull_request', 'synchronize', pr({ labels: ['owner-approved'] })), false, 'non-redesign untouched')
})
