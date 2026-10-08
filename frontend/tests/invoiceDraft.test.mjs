// node --test frontend/tests/invoiceDraft.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  parseMoneyText, formatCents, validateLine, validateAdjustmentAmount, validateNewDraft,
  validateTransition, controlsFor, idempotencyKey, refusalMessage, refusalKind, describeEvent,
  NOT_SENT_NOTICE, TAX_NOTICE,
} from '../src/utils/invoiceDraft.js'

test('exact cents formatting and parsing', () => {
  assert.equal(parseMoneyText('0.1') + parseMoneyText('0.2'), 30)
  assert.equal(parseMoneyText('1,250.00'), 125000)
  assert.equal(parseMoneyText('19.99') * 3, 5997)
  assert.equal(formatCents(149700), '$1,497.00')
  assert.equal(formatCents(5), '$0.05')
  assert.equal(formatCents(-50050), '-$500.50')
  assert.equal(formatCents(12.5), '—')
  assert.equal(formatCents(undefined), '—')
})

test('line input refuses NaN, Infinity, negative, sub-cent, overflow, empty, bad qty', () => {
  const ok = { description: 'Setup', quantity: '2', unitPrice: '10.50' }
  assert.deepEqual(validateLine(ok).line, { description: 'Setup', quantity: 2, unit_price_cents: 1050 })
  for (const unitPrice of ['NaN', 'Infinity', '-1', '1.005', '1e3', '', 'abc', '10000000.01', '$5']) {
    assert.equal(validateLine({ ...ok, unitPrice }).ok, false, unitPrice)
  }
  for (const quantity of ['0', '-1', '1.5', '', 'NaN', '1000001', 'Infinity']) {
    assert.equal(validateLine({ ...ok, quantity }).ok, false, quantity)
  }
  for (const description of ['', '   ', null, undefined]) {
    assert.equal(validateLine({ ...ok, description }).ok, false, String(description))
  }
  assert.equal(validateLine({ ...ok, unitPrice: '10000000.00' }).ok, true)   // exactly the cap
})

test('discount/tax: blank is zero, junk and overflow refused', () => {
  assert.equal(validateAdjustmentAmount('', 'Tax').cents, 0)
  assert.equal(validateAdjustmentAmount('3.25', 'Tax').cents, 325)
  for (const bad of ['-1', 'NaN', 'Infinity', '1.234', 'x', '999999999999999999999']) {
    assert.equal(validateAdjustmentAmount(bad, 'Tax').ok, false, bad)
  }
})

test('new draft and transition input rules', () => {
  assert.equal(validateNewDraft({ customerName: '  ', memo: '' }).ok, false)
  assert.deepEqual(validateNewDraft({ customerName: ' Acme ', memo: ' hi ' }).body, { customer_name: 'Acme', memo: 'hi' })
  assert.equal(validateTransition('void', '  ').ok, false)
  assert.equal(validateTransition('void', 'duplicate').ok, true)
  assert.equal(validateTransition('approval_ready', '').ok, true)
})

test('controls come from the server view; locked states cannot edit; no provider actions', () => {
  const draft = { state: 'draft', editable: true, allowed_transitions: ['approval_ready', 'void'], refusal_reason: 'add at least one line first' }
  assert.deepEqual(controlsFor(draft), { canEdit: true, transitions: ['approval_ready', 'void'], blocker: 'add at least one line first' })
  const ready = { state: 'approval_ready', editable: false, allowed_transitions: ['draft', 'void'], refusal_reason: 'locked while approval_ready' }
  assert.equal(controlsFor(ready).canEdit, false)
  assert.deepEqual(controlsFor(ready).transitions, ['draft', 'void'])
  assert.equal(controlsFor(ready).blocker, null)
  assert.deepEqual(controlsFor({ state: 'void', editable: false, allowed_transitions: [] }).transitions, [])
  // an unexpected server transition (e.g. a future "send") is never rendered
  assert.deepEqual(controlsFor({ ...draft, allowed_transitions: ['send', 'finalize', 'paid', 'void'] }).transitions, ['void'])
  assert.deepEqual(controlsFor(null), { canEdit: false, transitions: [], blocker: null })
})

test('idempotency key is stable per action and bounded', () => {
  assert.equal(idempotencyKey('line_add', 'd1', 3, 'abc'), idempotencyKey('line_add', 'd1', 3, 'abc'))
  assert.notEqual(idempotencyKey('line_add', 'd1', 3, 'abc'), idempotencyKey('line_add', 'd1', 4, 'abc'))
  assert.ok(idempotencyKey('x', 'y'.repeat(300), 1, 't').length <= 120)
})

test('refusals are labelled and never look like success', () => {
  const e = (status, message) => ({ status, message })
  assert.match(refusalMessage(e(409, 'changed')), /out of date.*reloaded/)
  assert.match(refusalMessage(e(403, 'Admin only')), /not authorized/)
  assert.match(refusalMessage(e(503, 'not migrated')), /storage is unavailable/)
  assert.match(refusalMessage(e(400, 'discount cannot exceed the subtotal')), /invalid.*discount/)
  assert.match(refusalMessage(e(404, 'nope')), /not found/)
  for (const s of [400, 403, 404, 409, 422, 503, 500, undefined]) {
    const m = refusalMessage(e(s, ''))
    assert.ok(m.startsWith('Not saved'), m)
    assert.doesNotMatch(m, /success/i)
  }
  assert.equal(refusalKind(e(409)), 'conflict')
  assert.equal(refusalKind(e(503)), 'storage')
  assert.equal(refusalKind(e(403)), 'authority')
  assert.equal(refusalKind(e(400)), 'invalid')
})

test('audit events render, cents fields formatted, bad JSON tolerated', () => {
  const r = describeEvent({ at: 't', actor: 'a@x', action: 'adjustments_set', detail: '{"discount_cents": 500, "tax_cents": 7}' })
  assert.equal(r.what, 'adjustments set')
  assert.equal(r.detail, 'discount_cents: $5.00, tax_cents: $0.07')
  assert.equal(describeEvent({ action: 'x', detail: '{bad' }).detail, '{bad')
})

test('notices state the truth', () => {
  assert.match(NOT_SENT_NOTICE, /not sent, finalized, charged/)
  assert.match(TAX_NOTICE, /not calculated or attested/)
})
