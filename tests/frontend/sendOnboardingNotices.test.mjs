/**
 * THE POST-SEND DIALOG MUST NOT CONTRADICT ITSELF.
 *
 * Three fixtures, taken from the three shapes `send-onboarding` actually
 * returns. The first is the one that was wrong in production: Atlantis Light
 * & Power, message_sent_by_platform true, delivery "sent", and the dialog
 * still printing "Nothing has been sent." underneath "Invitation sent".
 *
 *     node tests/frontend/sendOnboardingNotices.test.mjs
 *
 * No bundler, no DOM, no framework — which is why the decision lives in
 * frontend/src/pages/god/sendOnboardingNotices.js and imports nothing.
 */
import {
  isSetupLink, postSendNotices, wasDelivered,
} from '../../frontend/src/pages/god/sendOnboardingNotices.js'

let passed = 0
const failures = []

function check(name, fn) {
  try { fn(); passed += 1 }
  catch (e) { failures.push(name + '\n    ' + e.message) }
}

function eq(actual, expected, what) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected)
  if (a !== b) throw new Error((what || 'value') + ': expected ' + b + ', got ' + a)
}

/* ── fixtures ─────────────────────────────────────────────────────────────── */

// 1. SETUP LINK + THE PLATFORM DELIVERED IT. The live Atlantis case.
const DELIVERED = {
  recipient: { name: 'Joshua Shronce', role: 'org_admin' },
  brand: { name: 'EvoSys Pro' },
  access_path: 'setup_link',
  onboarding_url_is_one_time: true,
  message_sent_by_platform: true,
  delivery: { state: 'sent', label: 'Sent', to: 'joshua@example.invalid',
              provider_message_id: 'msg_abc', error: null },
}

// 2. SETUP LINK + NOTHING SENT. The operator asked for a link only.
const GENERATED = {
  recipient: { name: 'Someone New', role: 'advisor' },
  brand: { name: 'EvoSys Pro' },
  access_path: 'setup_link',
  onboarding_url_is_one_time: true,
  message_sent_by_platform: false,
  delivery: { state: 'generated', label: 'Link generated — not sent',
              to: null, provider_message_id: null, error: null },
}

// 3. EXISTING LOGIN. No link was minted and no password was touched.
const EXISTING = {
  recipient: { name: 'Already Here', role: 'advisor' },
  brand: { name: 'EvoSys Pro' },
  access_path: 'existing_login',
  onboarding_url_is_one_time: false,
  message_sent_by_platform: false,
  delivery: { state: 'generated', label: 'Link generated — not sent',
              to: null, provider_message_id: null, error: null },
}

// A send that was attempted and refused. Not one of the three required cases,
// but the one an operator most needs to see, so it is pinned too.
const FAILED = {
  recipient: { name: 'Bad Address', role: 'advisor' },
  brand: { name: 'EvoSys Pro' },
  access_path: 'setup_link',
  onboarding_url_is_one_time: true,
  message_sent_by_platform: false,
  delivery: { state: 'failed', label: 'Send failed', to: 'nope@example.invalid',
              provider_message_id: null, error: 'The mail provider refused it.' },
}

/* ── 1. setup link + successful platform delivery ─────────────────────────── */

check('a delivered invitation never says nothing was sent', () => {
  // THE REGRESSION. This was true in production alongside heading
  // "Invitation sent" and a delivery block reading "Sent — to joshua@…".
  eq(postSendNotices(DELIVERED).manualSend, false)
})

check('a delivered invitation keeps the "Invitation sent" heading', () => {
  eq(postSendNotices(DELIVERED).heading, 'Invitation sent')
})

check('a delivered invitation still reports what the provider said', () => {
  eq(postSendNotices(DELIVERED).deliveryReport, true)
})

check('a delivered setup link still warns that it is one-time', () => {
  // The amber block carried two claims and only one stopped being true.
  eq(postSendNotices(DELIVERED).keepLink, true)
})

check('a delivered invitation still labels the link as one-time', () => {
  eq(postSendNotices(DELIVERED).linkLabel, 'One-time onboarding link')
})

check('a delivered invitation is not the existing-login shape', () => {
  eq(postSendNotices(DELIVERED).existingLogin, false)
})

/* ── 2. setup link + generated / not delivered ────────────────────────────── */

check('a link-only issue still says nothing has been sent', () => {
  const n = postSendNotices(GENERATED)
  eq(n.manualSend, true)
  eq(n.heading, 'Onboarding ready to send')
})

check('a link-only issue shows no provider report', () => {
  // "generated" is the absence of an attempt, not the result of one.
  eq(postSendNotices(GENERATED).deliveryReport, false)
})

check('a link-only issue does not double up the one-time warning', () => {
  // The manual-send block already says it; two blocks saying it is noise.
  eq(postSendNotices(GENERATED).keepLink, false)
})

check('a refused send says nothing was sent AND shows the failure', () => {
  const n = postSendNotices(FAILED)
  eq(n.manualSend, true)
  eq(n.deliveryReport, true)
  eq(n.heading, 'Onboarding ready to send')
})

/* ── 3. existing-login path ───────────────────────────────────────────────── */

check('an existing login gets the teal block and no link warnings', () => {
  const n = postSendNotices(EXISTING)
  eq(n.existingLogin, true)
  eq(n.manualSend, false)
  eq(n.keepLink, false)
})

check('an existing login is never described as a one-time link', () => {
  // Promising "shown once, cannot be retrieved" about a plain /launch URL
  // teaches the operator to re-issue onboarding to get a recoverable address
  // back — and re-issuing is what rewrites somebody's password.
  eq(postSendNotices(EXISTING).linkLabel, 'Their onboarding address')
})

check('an existing login still reports a delivery that was attempted', () => {
  const n = postSendNotices({ ...EXISTING, message_sent_by_platform: true,
    delivery: { state: 'sent', label: 'Sent', to: 'a@example.invalid' } })
  eq(n.heading, 'Invitation sent')
  eq(n.deliveryReport, true)
  eq(n.manualSend, false)
  eq(n.keepLink, false)
})

/* ── the invariant itself ─────────────────────────────────────────────────── */

check('the three notices are mutually exclusive in every shape', () => {
  for (const s of [DELIVERED, GENERATED, EXISTING, FAILED]) {
    const n = postSendNotices(s)
    const on = [n.existingLogin, n.manualSend, n.keepLink].filter(Boolean).length
    if (on > 1) throw new Error('two notices at once for ' + s.access_path
      + '/' + s.message_sent_by_platform)
  }
})

check('"nothing has been sent" and a delivered message are never both true', () => {
  for (const s of [DELIVERED, GENERATED, EXISTING, FAILED]) {
    const n = postSendNotices(s)
    if (n.manualSend && wasDelivered(s)) throw new Error('contradiction for ' + s.access_path)
  }
})

check('the helpers read the payload, not a guess', () => {
  eq(wasDelivered({ message_sent_by_platform: true }), true)
  eq(wasDelivered({ message_sent_by_platform: false }), false)
  eq(wasDelivered({}), false)
  eq(wasDelivered(null), false)
  eq(isSetupLink({ access_path: 'setup_link' }), true)
  eq(isSetupLink({ access_path: 'existing_login' }), false)
})

/* ── report ───────────────────────────────────────────────────────────────── */

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed\n')
  for (const f of failures) console.error('  FAIL  ' + f)
  process.exit(1)
}
console.log(passed + ' passed')
