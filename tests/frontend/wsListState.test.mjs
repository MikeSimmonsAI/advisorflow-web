import assert from 'node:assert/strict'
import {
  initialList, loadStarted, loadSucceeded, loadFailed, listView, supportCode,
  knownCount, truncationNote, retryDisabledReason, cleanQuery, buyerStatusLabel,
  isVerifiedBuyer, activeBuyerCount, findById,
} from '../../frontend/src/pages/wholesale/wsListState.js'

let n = 0
const t = (name, fn) => { fn(); n += 1; console.log('ok -', name) }

t('first load is loading, not empty', () => {
  assert.equal(listView(initialList()), 'loading')
})
t('failure with nothing loaded is error, not empty', () => {
  let s = loadStarted(initialList())
  s = loadFailed(s, s.latest, 'boom', 'abc')
  assert.equal(listView(s), 'error')
  assert.equal(s.supportCode, 'abc')
})
t('success with zero rows is empty; with rows is ready', () => {
  let s = loadStarted(initialList())
  s = loadSucceeded(s, s.latest, [], 0)
  assert.equal(listView(s), 'empty')
  s = loadStarted(s)
  s = loadSucceeded(s, s.latest, [{ id: 'a' }], 1)
  assert.equal(listView(s), 'ready')
})
t('stale response is ignored', () => {
  let s = loadStarted(initialList())
  const old = s.latest
  s = loadStarted(s)
  const before = s
  assert.equal(loadSucceeded(s, old, [{ id: 'x' }], 1), before)
  assert.equal(loadFailed(s, old, 'late'), before)
})
t('failed refresh keeps rows and reads stale', () => {
  let s = loadSucceeded(loadStarted(initialList()), 1, [{ id: 'a' }], 1)
  s = loadStarted(s)
  assert.equal(listView(s), 'refreshing')
  s = loadFailed(s, s.latest, 'down')
  assert.equal(listView(s), 'stale')
  assert.equal(s.rows.length, 1)
})
t('retry success clears stale', () => {
  let s = loadSucceeded(loadStarted(initialList()), 1, [{ id: 'a' }], 1)
  s = loadFailed(loadStarted(s), s.latest + 1, 'down')
  s = loadStarted(s)
  s = loadSucceeded(s, s.latest, [{ id: 'a' }, { id: 'b' }], 2)
  assert.equal(listView(s), 'ready')
  assert.equal(s.error, '')
})
t('support code only when supplied', () => {
  assert.equal(supportCode(null), '')
  assert.equal(supportCode(new Error('x')), '')
  assert.equal(supportCode({ detail: { support_code: 'EVO-12' } }), 'EVO-12')
  assert.equal(supportCode({ request_id: 77 }), '77')
  assert.equal(supportCode({ detail: 'plain string' }), '')
})
t('unknown count is a dash, zero is zero', () => {
  assert.equal(knownCount(undefined), '—')
  assert.equal(knownCount(null), '—')
  assert.equal(knownCount(NaN), '—')
  assert.equal(knownCount(0), 0)
})
t('truncation note only when page is short of the total', () => {
  const s = loadSucceeded(loadStarted(initialList()), 1, [{ id: 1 }, { id: 2 }], 5)
  assert.match(truncationNote(s), /first 2 of 5/)
  const full = loadSucceeded(loadStarted(initialList()), 1, [{ id: 1 }], 1)
  assert.equal(truncationNote(full), '')
  assert.equal(truncationNote(initialList()), '')
})
t('retry disabled reason only while loading', () => {
  assert.notEqual(retryDisabledReason(initialList()), '')
  assert.equal(retryDisabledReason(loadSucceeded(loadStarted(initialList()), 1, [], 0)), '')
})
t('whitespace search is no search', () => {
  assert.equal(cleanQuery('   '), '')
  assert.equal(cleanQuery(' ab '), 'ab')
  assert.equal(cleanQuery(null), '')
})
t('buyer status: opted out beats active, unknown stays unknown', () => {
  assert.equal(buyerStatusLabel({ do_not_contact: true, is_active: true }), 'Opted out')
  assert.equal(buyerStatusLabel({ is_active: false }), 'Inactive')
  assert.equal(buyerStatusLabel({ is_active: true }), 'Active')
  assert.equal(buyerStatusLabel({}), 'Unknown')
})
t('verified only needs an explicit true flag', () => {
  assert.equal(isVerifiedBuyer({ cash_verified: true }), true)
  assert.equal(isVerifiedBuyer({ proof_of_funds_on_file: true }), true)
  assert.equal(isVerifiedBuyer({ cash_verified: 'yes' }), false)
  assert.equal(isVerifiedBuyer({}), false)
})
t('active count excludes opted-out and inactive', () => {
  assert.equal(activeBuyerCount([{ is_active: true }, { is_active: true, do_not_contact: true }, { is_active: false }]), 1)
  assert.equal(activeBuyerCount(null), 0)
})
t('findById tolerates a vanished row', () => {
  assert.equal(findById([{ id: 'a' }], 'b'), null)
  assert.equal(findById(null, 'a'), null)
  assert.deepEqual(findById([{ id: 'a' }], 'a'), { id: 'a' })
})

console.log(`${n} passed`)
