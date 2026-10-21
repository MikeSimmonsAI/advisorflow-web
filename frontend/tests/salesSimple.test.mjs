// Lane F (#281) — rules behind the simplified Sales Workspace screens.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  REAL_STAGES, SIMPLE_COLUMNS, simpleColumnFor, entryStageFor, groupForSimpleBoard,
  cleanPhone, telHref, smsHref, CALL_OUTCOMES, NOT_INTERESTED_CHOICES, stageAfterOutcome, buildOutcomeRequests,
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
    ['New Leads', 'Follow Up', 'Demo & Closing', 'Won'])
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
  assert.equal(r.note.summary, 'Call: No answer')
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

test('typed time in the team zone becomes the UTC booking time', async () => {
  const { wallToUtcNaive } = await import('../src/pages/sales/simpleSales.js')
  assert.equal(wallToUtcNaive('2026-10-23', '10:00', 'America/Chicago'), '2026-10-23T15:00:00')  // CDT
  assert.equal(wallToUtcNaive('2026-12-01', '10:00', 'America/Chicago'), '2026-12-01T16:00:00')  // CST
  assert.equal(wallToUtcNaive('2026-11-01', '09:30', 'America/New_York'), '2026-11-01T14:30:00') // day DST ends
  assert.equal(wallToUtcNaive('2026-10-23', '14:15', 'UTC'), '2026-10-23T14:15:00')
  assert.equal(wallToUtcNaive('', '10:00', 'UTC'), null)
  assert.equal(wallToUtcNaive('2026-10-23', '10:00', 'Not/AZone'), null)
})

// ── refinements (master prompt 2026-10-21 11:20) ─────────────────────────

test('Not Interested never schedules anything by itself', () => {
  const o = CALL_OUTCOMES.find(x => x.key === 'not_interested')
  assert.equal(o.next, null); assert.equal(o.days, null); assert.equal(o.decide, true)
  assert.deepEqual(NOT_INTERESTED_CHOICES.map(c => c.label), ['Close as Lost', 'Follow up later'])
  assert.throws(() => buildOutcomeRequests({ stage: 'contacted', outcome: 'not_interested' }), /Close as Lost or Follow up later/)
})

test('Not Interested → Close as Lost clears the next step and closes the deal', () => {
  const r = buildOutcomeRequests({ stage: 'discovery', outcome: 'not_interested', decision: 'lost',
    nextAction: 'should be ignored', dueYmd: '2027-01-01', note: 'Went with a competitor' })
  assert.equal(r.note.summary, 'Call: Not interested — closed as Lost — Went with a competitor')
  assert.deepEqual(r.patch, { stage: 'lost', loss_reason: 'Not interested (call)',
    next_action: null, next_action_due_at: null })
})

test('Not Interested → Follow up later needs a chosen action AND date', () => {
  assert.throws(() => buildOutcomeRequests({ stage: 'contacted', outcome: 'not_interested', decision: 'later', dueYmd: '2027-01-05' }), /next action/)
  assert.throws(() => buildOutcomeRequests({ stage: 'contacted', outcome: 'not_interested', decision: 'later', nextAction: 'Check budget' }), /date/)
  const r = buildOutcomeRequests({ stage: 'contacted', outcome: 'not_interested', decision: 'later',
    nextAction: 'Check budget after Q1', dueYmd: '2027-01-05' })
  assert.equal(r.note.summary, 'Call: Not interested now — follow up later')
  assert.deepEqual(r.patch, { next_action: 'Check budget after Q1', next_action_due_at: '2027-01-05T12:00:00' })
  assert.equal(r.patch.stage, undefined)                // real stage kept
})

test('Book a Meeting records agreement, never a booking', () => {
  const r = buildOutcomeRequests({ stage: 'prospect', outcome: 'book_meeting', nextAction: 'Book the meeting', dueYmd: '2026-10-21' })
  assert.match(r.note.summary, /not booked yet/)
  assert.doesNotMatch(r.note.summary, /booked —|Demo booked/)
  assert.equal(r.patch.stage, 'contacted')
  assert.equal(CALL_OUTCOMES.find(x => x.key === 'book_meeting').books, true)
})

test('Left Voicemail follow-up is optional: clearing the date sends none', () => {
  const r = buildOutcomeRequests({ stage: 'contacted', outcome: 'voicemail', nextAction: '', dueYmd: '' })
  assert.equal(r.patch, null)
})

test('due buckets follow the TEAM time zone, matching the server rule', () => {
  const lateChicago = new Date('2026-10-22T04:30:00Z')         // 11:30 PM Oct 21 in Chicago
  assert.equal(dueBucket('2026-10-21T12:00:00', lateChicago, 'America/Chicago'), 'today')
  assert.equal(dueBucket('2026-10-21T12:00:00', lateChicago, 'UTC'), 'overdue')
  assert.equal(dueBucket('2026-10-22T00:00:00', lateChicago, 'America/Chicago'), 'later')
  // a real instant: overdue once passed, else today if on the team's date
  assert.equal(dueBucket('2026-10-22T04:00:00', lateChicago, 'America/Chicago'), 'overdue')
  assert.equal(dueBucket('2026-10-22T04:45:00', lateChicago, 'America/Chicago'), 'today')
  assert.equal(dueBucket('2026-10-22T06:00:00', lateChicago, 'America/Chicago'), 'later')   // 1 AM Oct 22 local
})

test('Due Now = Overdue + Today, with no overlap and no duplicates', () => {
  const now = new Date('2026-10-21T15:00:00Z'); const tz = 'America/Chicago'
  const cards = [
    { id: 'a', status: 'open', next_action_due_at: '2026-10-20T12:00:00' },
    { id: 'b', status: 'open', next_action_due_at: '2026-10-21T12:00:00' },
    { id: 'c', status: 'open', next_action_due_at: '2026-10-21T14:00:00' },  // passed earlier today → overdue
    { id: 'd', status: 'open', next_action_due_at: '2026-10-21T20:00:00' },  // later today → today
    { id: 'e', status: 'open', next_action_due_at: '2026-10-23T12:00:00' },
    { id: 'f', status: 'lost', next_action_due_at: '2026-10-19T12:00:00' },
  ]
  const now_ = followUpList(cards, 'today', '', now, tz).map(c => c.id)
  const over = followUpList(cards, 'overdue', '', now, tz).map(c => c.id)
  const tod = followUpList(cards, 'today_only', '', now, tz).map(c => c.id)
  assert.deepEqual(over.sort(), ['a', 'c'])
  assert.deepEqual(tod.sort(), ['b', 'd'])
  assert.deepEqual([...now_].sort(), ['a', 'b', 'c', 'd'])
  assert.equal(new Set(now_).size, now_.length)
  assert.equal(over.length + tod.length, now_.length)
})

test('date suggestions use the team calendar', () => {
  const lateChicago = new Date('2026-10-22T04:30:00Z')
  assert.equal(ymdPlusDays(0, lateChicago, 'America/Chicago'), '2026-10-21')
  assert.equal(ymdPlusDays(1, lateChicago, 'America/Chicago'), '2026-10-22')
})
