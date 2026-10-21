// Lane F (#281) — rules behind the simplified Sales Workspace screens.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  REAL_STAGES, SIMPLE_COLUMNS, simpleColumnFor, entryStageFor, groupForSimpleBoard,
  cleanPhone, telHref, smsHref, CALL_OUTCOMES, stageAfterOutcome, buildOutcomeRequests,
  dueIsoFromYmd, ymdPlusDays, dueBucket, followUpList, greeting, firstName,
} from '../src/pages/sales/simpleSales.js'

test('every real stage maps to exactly one simple column', () => {
  for (const s of REAL_STAGES) {
    const owners = SIMPLE_COLUMNS.filter(c => c.stages.includes(s))
    assert.equal(owners.length, 1, s)
  }
  const all = SIMPLE_COLUMNS.flatMap(c => c.stages).sort()
  assert.deepEqual(all, [...REAL_STAGES].sort())
})

test('approved column order and labels', () => {
  assert.deepEqual(SIMPLE_COLUMNS.map(c => c.label),
    ['New Leads', 'Follow Up', 'Demo Scheduled', 'Won'])
})

test('lost and unknown stages are kept, never dropped', () => {
  assert.equal(simpleColumnFor('lost'), 'lost')
  assert.equal(simpleColumnFor('some_future_stage'), 'other')
  const g = groupForSimpleBoard({
    stages: [
      { key: 'prospect', opportunities: [{ id: 'a', stage: 'prospect' }] },
      { key: 'closing', opportunities: [{ id: 'b', stage: 'closing' }] },
      { key: 'x', opportunities: [{ id: 'c', stage: 'mystery' }] },
      { key: 'onboarding', opportunities: [{ id: 'd', stage: 'onboarding' }] },
    ],
    lost: [{ id: 'e', stage: 'lost' }, { id: 'a', stage: 'prospect' }],
  })
  assert.deepEqual(g.new.map(o => o.id), ['a'])          // duplicate ignored
  assert.deepEqual(g.demo.map(o => o.id), ['b'])
  assert.deepEqual(g.won.map(o => o.id), ['d'])
  assert.deepEqual(g.lost.map(o => o.id), ['e'])
  assert.deepEqual(g.other.map(o => o.id), ['c'])
})

test('moving into a column uses its first real stage', () => {
  assert.equal(entryStageFor('new'), 'prospect')
  assert.equal(entryStageFor('follow'), 'contacted')
  assert.equal(entryStageFor('demo'), 'demo_build')
  assert.equal(entryStageFor('won'), 'won')
  assert.equal(entryStageFor('nope'), null)
})

test('call and text links only for a real-looking phone', () => {
  assert.equal(cleanPhone('(469) 555-0100'), '4695550100')
  assert.equal(cleanPhone('+1 469-555-0100'), '+14695550100')
  assert.equal(telHref('469.555.0100'), 'tel:4695550100')
  assert.equal(smsHref('+1 (469) 555 0100'), 'sms:+14695550100')
  assert.equal(telHref(''), null)
  assert.equal(smsHref('ext 12'), null)
  assert.equal(telHref(null), null)
})

test('the six post-call choices, in the approved order', () => {
  assert.deepEqual(CALL_OUTCOMES.map(o => o.label),
    ['Connected', 'No Answer', 'Left Voicemail', 'Interested', 'Not Interested', 'Book a Meeting'])
})

test('only a real conversation on a Prospect moves it to Contacted', () => {
  assert.equal(stageAfterOutcome('prospect', 'connected'), 'contacted')
  assert.equal(stageAfterOutcome('prospect', 'interested'), 'contacted')
  assert.equal(stageAfterOutcome('prospect', 'no_answer'), null)
  assert.equal(stageAfterOutcome('prospect', 'voicemail'), null)
  assert.equal(stageAfterOutcome('prospect', 'not_interested'), null)
  assert.equal(stageAfterOutcome('discovery', 'connected'), null)
  assert.equal(stageAfterOutcome('closing', 'interested'), null)
})

test('outcome becomes one timeline note and one deal update', () => {
  const r = buildOutcomeRequests({
    stage: 'prospect', outcome: 'connected', note: 'Wants pricing',
    nextAction: 'Send pricing', dueYmd: '2026-10-23',
  })
  assert.deepEqual(r.note, { event_type: 'call_outcome',
    summary: 'Call: Connected — Wants pricing', detail: null })
  assert.deepEqual(r.patch, { next_action: 'Send pricing',
    next_action_due_at: '2026-10-23T12:00:00', stage: 'contacted' })
})

test('no-answer with no next step leaves the deal alone', () => {
  const r = buildOutcomeRequests({ stage: 'discovery', outcome: 'no_answer' })
  assert.equal(r.note.summary, 'Call: No Answer')
  assert.equal(r.patch, null)
})

test('a long note keeps its full text in detail', () => {
  const long = 'x'.repeat(400)
  const r = buildOutcomeRequests({ stage: 'contacted', outcome: 'voicemail', note: long })
  assert.equal(r.note.summary.length, 250)
  assert.equal(r.note.detail, long)
})

test('an outcome is required', () => {
  assert.throws(() => buildOutcomeRequests({ stage: 'prospect', outcome: '' }))
})

test('date helpers use the 12:00 UTC calendar-date anchor', () => {
  assert.equal(dueIsoFromYmd('2026-10-21'), '2026-10-21T12:00:00')
  assert.equal(dueIsoFromYmd('10/21/2026'), null)
  assert.equal(ymdPlusDays(2, new Date(2026, 9, 30, 15)), '2026-11-01')
})

test('due buckets read anchors as calendar dates', () => {
  const now = new Date(2026, 9, 21, 20, 0)   // Oct 21, 8pm local
  assert.equal(dueBucket('2026-10-21T12:00:00', now), 'today')
  assert.equal(dueBucket('2026-10-20T12:00:00', now), 'overdue')
  assert.equal(dueBucket('2026-10-22T00:00:00', now), 'later')
  assert.equal(dueBucket(null, now), 'none')
})

test('follow-up list: open only, overdue first, filters and search', () => {
  const now = new Date(2026, 9, 21, 9, 0)
  const cards = [
    { id: '1', status: 'open', company_name: 'Acme', next_action_due_at: '2026-10-21T12:00:00' },
    { id: '2', status: 'open', company_name: 'Beta', contact_name: 'Rosa', next_action_due_at: '2026-10-19T12:00:00' },
    { id: '3', status: 'won', company_name: 'Gamma', next_action_due_at: '2026-10-18T12:00:00' },
    { id: '4', status: 'open', company_name: 'Delta', next_action_due_at: '2026-10-25T12:00:00' },
    { id: '5', status: 'open', company_name: 'Echo' },
  ]
  assert.deepEqual(followUpList(cards, 'today', '', now).map(c => c.id), ['2', '1'])
  assert.deepEqual(followUpList(cards, 'overdue', '', now).map(c => c.id), ['2'])
  assert.deepEqual(followUpList(cards, 'all', '', now).map(c => c.id), ['2', '1', '4', '5'])
  assert.deepEqual(followUpList(cards, 'all', 'rosa', now).map(c => c.id), ['2'])
  assert.equal(followUpList(cards, 'today', '', now)[0].due_bucket, 'overdue')
})

test('greeting and first name never invent a person', () => {
  assert.equal(greeting(new Date(2026, 0, 1, 8)), 'Good morning')
  assert.equal(greeting(new Date(2026, 0, 1, 13)), 'Good afternoon')
  assert.equal(greeting(new Date(2026, 0, 1, 19)), 'Good evening')
  assert.equal(firstName('Kerry Allan'), 'Kerry')
  assert.equal(firstName(''), '')
})
