import assert from 'node:assert/strict'
import {
  parseMoney, validateSend, sendInputKey, summarizeOutcome, isCurrentResult,
  appointmentLabel, appointmentSummary, validateAppointment,
} from '../../frontend/src/pages/wholesale/wsDispositionState.js'

let n = 0
const t = (name, fn) => { fn(); n += 1; console.log('ok -', name) }

t('money parses symbols, rejects text', () => {
  assert.deepEqual(parseMoney('$250,000'), { ok: true, value: 250000 })
  assert.equal(parseMoney('abc').ok, false)
  assert.deepEqual(parseMoney(''), { ok: true, value: null })
  assert.equal(parseMoney('-5').ok, false)
})
t('send needs a buyer and names the field', () => {
  const v = validateSend({ selected: [], askingPrice: '', channel: 'email', channels: [] })
  assert.equal(v.ok, false)
  assert.equal(v.firstInvalid, 'dp-buyers')
})
t('bad price blocks send, focus goes to price', () => {
  const v = validateSend({ selected: ['1'], askingPrice: 'x', channel: 'email', channels: [] })
  assert.equal(v.firstInvalid, 'dp-asking')
})
t('disabled channel blocks send with server detail', () => {
  const v = validateSend({ selected: ['1'], askingPrice: '1', channel: 'sms',
    channels: [{ channel: 'sms', enabled: false, detail: 'SMS off' }] })
  assert.equal(v.errors['dp-channel'], 'SMS off')
})
t('valid send builds body', () => {
  const v = validateSend({ selected: ['1'], askingPrice: '$9,000', channel: 'email',
    channels: [{ channel: 'email', enabled: true }] })
  assert.equal(v.ok, true)
  assert.equal(v.body.asking_price, 9000)
})
t('outcome counted from rows, not totals', () => {
  const s = summarizeOutcome({ sent: 5, results: [{ sent: true }, { sent: false }] })
  assert.equal(s.sent, 1)
  assert.equal(s.kind, 'partial')
  assert.equal(s.tone, 'warn')
  assert.equal(summarizeOutcome({ results: [] }).kind, 'none')
  assert.equal(summarizeOutcome({ results: [{ sent: false }] }).kind, 'none-sent')
  assert.equal(summarizeOutcome({ results: [{ sent: true }] }).tone, 'good')
})
t('result goes stale when inputs change', () => {
  const a = sendInputKey({ selected: ['2', '1'], askingPrice: '5', channel: 'email' })
  assert.equal(a, sendInputKey({ selected: ['1', '2'], askingPrice: ' 5', channel: 'email' }))
  assert.equal(isCurrentResult(a, sendInputKey({ selected: ['1'], askingPrice: '5', channel: 'email' })), false)
  assert.equal(isCurrentResult(null, a), false)
})
t('appointment labels never invent status', () => {
  assert.equal(appointmentLabel('weird'), 'Unknown status')
  assert.equal(appointmentLabel(null), 'No appointment recorded')
  assert.equal(appointmentSummary('none', '2026-01-01T10:00:00', null), 'No appointment')
  assert.match(appointmentSummary('scheduled', 'T', (x) => x), /Scheduled · T/)
  assert.match(appointmentSummary('cancelled', 'T', (x) => x), /was T/)
})
t('scheduled needs a time; none/requested must not have one; bad date rejected', () => {
  assert.equal(validateAppointment({ status: 'scheduled', at: '' }).firstInvalid, 'appointment_at')
  assert.equal(validateAppointment({ status: 'none', at: '2026-10-08T10:00' }).ok, false)
  assert.equal(validateAppointment({ status: 'requested', at: '2026-10-08T10:00' }).ok, false)
  assert.equal(validateAppointment({ status: 'scheduled', at: 'garbage' }).ok, false)
  const ok = validateAppointment({ status: 'scheduled', at: '2026-10-08T10:00' })
  assert.equal(ok.ok, true)
  assert.match(ok.iso, /^2026-10-0[78]T/)
  assert.equal(validateAppointment({ status: 'cancelled', at: '' }).ok, true)
})
console.log(`wsDispositionState: ${n} passed`)
