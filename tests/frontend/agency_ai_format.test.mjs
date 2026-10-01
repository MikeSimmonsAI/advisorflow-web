// node tests/frontend/agency_ai_format.test.mjs — S14 Ask EvoAI state + generated_by labelling.
import assert from 'node:assert/strict'
import { askState, generatedByLabel } from '../../frontend/src/pages/agency/agencyFormat.js'

assert.equal(askState(null), null)
assert.equal(askState({ supported: false, status: 'unsupported', items: [] }), 'unsupported')
assert.equal(askState({ supported: true, status: 'insufficient_information', items: [] }), 'insufficient')
assert.equal(askState({ supported: true, status: 'answered', items: [{ label: 'x' }] }), 'answered')
assert.equal(askState({ supported: true, status: 'answered', items: [] }), 'empty')

assert.equal(generatedByLabel({ generated_by: 'rules', ai: { status: 'not_requested' } }).label, 'Rules')
assert.equal(generatedByLabel({ generated_by: 'rules', ai: { status: 'not_requested' } }).note, null)
assert.equal(generatedByLabel({ generated_by: 'ai', ai: { status: 'verified' } }).label, 'AI-assisted, verified')
// an "ai" output without a verified status is never labelled AI
assert.equal(generatedByLabel({ generated_by: 'ai', ai: { status: 'rejected' } }).label, 'Rules')
assert.match(generatedByLabel({ generated_by: 'rules', ai: { status: 'rejected' } }).note, /verifier/)
assert.match(generatedByLabel({ generated_by: 'rules', ai: { status: 'unavailable' } }).note, /not available/)
assert.equal(generatedByLabel(undefined).label, 'Rules')
console.log('agency_ai_format: 12 assertions passed')
