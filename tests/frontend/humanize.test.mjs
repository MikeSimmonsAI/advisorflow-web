// Readable labels instead of raw enums / ISO stamps (frontend/src/utils/humanize.js).
import assert from 'node:assert/strict'
import { humanizeKey, readableTimestamps, replaceKeys } from '../../frontend/src/utils/humanize.js'

assert.equal(humanizeKey('contract_signed'), 'Contract Signed')
assert.equal(humanizeKey('awaiting-client'), 'Awaiting Client')
assert.equal(humanizeKey('new'), 'New')
assert.equal(humanizeKey('Proposal Sent'), 'Proposal Sent')
assert.equal(humanizeKey(null), '—')
assert.equal(humanizeKey('', 'n/a'), 'n/a')

const out = readableTimestamps('Starts 2026-10-02T15:40:49Z.', 'en-US')
assert.ok(!/T15:40/.test(out), out)
assert.ok(/Oct 2, 2026/.test(out), out)
assert.ok(out.endsWith('.'), out)
assert.equal(readableTimestamps('Due 2026-09-30.'), 'Due 2026-09-30.')
assert.equal(readableTimestamps('no dates here'), 'no dates here')
assert.equal(readableTimestamps(null), null)

const L = { awaiting_client: 'Awaiting client' }
assert.equal(replaceKeys('awaiting_client for 12 days (threshold 7).', L), 'Awaiting client for 12 days (threshold 7).')
assert.equal(replaceKeys('unknown_key stays', L), 'unknown_key stays')
assert.equal(replaceKeys(undefined, L), undefined)
console.log('humanize ok')
