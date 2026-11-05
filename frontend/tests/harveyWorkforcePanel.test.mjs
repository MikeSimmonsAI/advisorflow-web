// #452 / #506 Harvey workforce panel: real server actions, compact layout,
// truthful labels, and a master view that never carries a remembered client.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = (f) => readFileSync(new URL(`../src/pages/acquisition/${f}`, import.meta.url), 'utf8')

test('each workforce control calls the server', () => {
  const s = src('HarveyWorkforce.jsx')
  for (const a of ["act('start'", "act('pause'", "act('resume'", "act('stop'", "act('emergency-stop'", "act('practice-test'"]) {
    assert.ok(s.includes(a), a)
  }
  assert.ok(s.includes('/god/acquisition/workforce/clients/'))
  assert.ok(s.includes('/god/acquisition/workforce/research?'))
  assert.ok(s.includes('/god/acquisition/workforce/ai-policy'))
  assert.ok(!/setInterval|@keyframes|--pulse/.test(s), 'no decorative live animation')
})

test('timestamps are short; the big Stat card is not used for times', () => {
  const s = src('HarveyWorkforce.jsx')
  assert.ok(!/import \{[^}]*\bStat\b/.test(s), 'no giant Stat tiles in the Harvey panel')
  assert.ok(s.includes('shortTime(h.last_pass_at)') && s.includes('Next: {shortTime(h.next_pass_at)}'))
  assert.ok(s.includes("hour: 'numeric', minute: '2-digit'"))
  assert.ok(s.includes('title={fullTime(h.last_pass_at)}'), 'full date only on hover')
})

test('scheduler, AI model and outreach are separate chips; template fallback is stated', () => {
  const s = src('HarveyWorkforce.jsx')
  for (const l of ['label="Scheduler"', 'label="Mode"', 'label="AI model"', 'label="Email sending"']) assert.ok(s.includes(l), l)
  assert.ok(s.includes('AI model unavailable — using deterministic template fallback.'))
})

test('the eight required metrics, from job outcomes, with no inflated research count', () => {
  const s = src('HarveyWorkforce.jsx')
  for (const l of ['Total prospects', 'Website research completed', 'Website research blocked', 'Verified contact emails',
    'Drafts pending approval', 'Messages actually sent', 'Replies received', 'Confirmed appointments']) assert.ok(s.includes(`label="${l}"`), l)
  assert.ok(s.includes('rc.website_read') && !s.includes('researched} / '), 'research = completed jobs, not researched_at')
})

test('compact responsive grid with no horizontal overflow', () => {
  const css = src('hub.css')
  assert.ok(/\.hw-grid \{[^}]*repeat\(4, minmax\(0, 1fr\)\)/.test(css), 'eight metrics = two even rows of four')
  assert.ok(/\.hw \{[^}]*overflow: hidden/.test(css))
  assert.ok(/@media \(max-width: 900px\) \{ \.hw-grid \{ grid-template-columns: repeat\(2/.test(css))
  assert.ok(/\.hw-metric b \{[^}]*font-size: 22px/.test(css), 'metric numbers are modest, not 30px+')
})

test('master pages never load a remembered client; TEST companies are marked', () => {
  const s = src('AcquisitionOps.jsx')
  assert.ok(s.includes('const orgId = onMasterPage ? null'))
  assert.ok(s.includes('All service companies (master view)'))
  assert.ok(s.includes("' (TEST)'"))
})

test('panel is mounted in the existing pages, not a new app', () => {
  assert.ok(src('HubPages.jsx').includes('<HarveyWorkforcePanel compact />'))
  assert.ok(src('AcquisitionOps.jsx').includes('{ov.master && <HarveyWorkforcePanel />}'))
})

test('a workforce draft is approved by the existing Send, with its id', () => {
  const s = src('AcquisitionOps.jsx')
  assert.ok(s.includes('draft_id: draft.id'))
  assert.ok(s.includes('/drafts/${draft.id}/reject'))
})

test('launch readiness shows YES/NO with reasons; authorizing is gated and confirmed', () => {
  const s = src('HarveyWorkforce.jsx')
  assert.ok(s.includes('/god/acquisition/workforce/launch-readiness'))
  assert.ok(s.includes("c.ready_to_launch === 'YES' && !c.authorized"), 'authorize only offered when YES')
  assert.ok(s.includes('confirm: true') && s.includes('<Confirm'), 'explicit confirmation')
  assert.ok(s.includes('/revoke'))
})
