// LeadDetail opens the tab named in ?tab= (frontend/src/utils/leadDetailTabs.js).
import assert from 'node:assert/strict'
import { leadDetailTabFromSearch, LEAD_DETAIL_TABS } from '../../frontend/src/utils/leadDetailTabs.js'

assert.deepEqual(LEAD_DETAIL_TABS, ['conversation', 'calls', 'timeline'])
assert.equal(leadDetailTabFromSearch('?tab=timeline'), 'timeline')
assert.equal(leadDetailTabFromSearch('?tab=calls'), 'calls')
assert.equal(leadDetailTabFromSearch('?tab=conversation'), 'conversation')
assert.equal(leadDetailTabFromSearch('?tab=history'), 'timeline')
assert.equal(leadDetailTabFromSearch('?tab=Full-History'), 'timeline')
assert.equal(leadDetailTabFromSearch('?foo=1&tab=TIMELINE'), 'timeline')
assert.equal(leadDetailTabFromSearch(''), 'conversation')
assert.equal(leadDetailTabFromSearch(undefined), 'conversation')
assert.equal(leadDetailTabFromSearch('?tab=nonsense'), 'conversation')
assert.equal(leadDetailTabFromSearch('?tab=nonsense', 'calls'), 'calls')
assert.equal(leadDetailTabFromSearch(new URLSearchParams('tab=call')), 'calls')
console.log('leadDetailTabs ok')
