// One delete behaviour for every leads screen (frontend/src/utils/deleteRecords.js).
import assert from 'node:assert/strict'
import { deleteLeadIds, deleteSummary } from '../../frontend/src/utils/deleteRecords.js'

const calls = []
const api = {
  delete: async (url) => {
    calls.push(url)
    if (url.endsWith('/bad')) { const e = new Error('This lead is the seller on an active wholesale deal: 1 Main St (negotiating).'); e.status = 409; throw e }
    return { deleted: true }
  },
}

const r = await deleteLeadIds(api, new Set(['a', 'bad', 'c']))
assert.deepEqual(calls, ['/leads/a', '/leads/bad', '/leads/c'])
assert.deepEqual(r.deleted, ['a', 'c'])
assert.equal(r.failed.length, 1)
assert.equal(r.failed[0].id, 'bad')
const s = deleteSummary(r)
assert.equal(s.ok, false)
assert.match(s.text, /^Deleted 2 of 3 leads\. 1 could not be deleted: This lead is the seller/)

const one = deleteSummary(await deleteLeadIds(api, ['bad']))
assert.match(one.text, /^Could not delete this lead: .*negotiating/)
assert.deepEqual(deleteSummary(await deleteLeadIds(api, ['x'])), { ok: true, text: 'Deleted 1 lead.' })
console.log('deleteRecords: 6 checks passed')
