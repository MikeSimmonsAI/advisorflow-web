/* PROJECT OVERDRIVE Phase B (#439) - the provider readiness cards' pure rules.
 * The server decides every status (app/services/evosense/provider_network.py);
 * this file only words and colours what it said, so it can be tested with
 * node --test. It never upgrades a status. */

export const STATUSES = ['NOT CONFIGURED', 'READY TO TEST', 'CONNECTED', 'DEGRADED', 'RATE LIMITED',
  'IMPORT ONLY', 'UNAVAILABLE', 'ENTITLEMENT MISSING']

export const TONE = {
  'NOT CONFIGURED': 'quiet', 'READY TO TEST': 'attention', CONNECTED: 'good', DEGRADED: 'attention',
  'RATE LIMITED': 'attention', 'IMPORT ONLY': 'info', UNAVAILABLE: 'danger', 'ENTITLEMENT MISSING': 'danger',
}

/** What the status means, in plain words. */
export const MEANING = {
  'NOT CONFIGURED': 'Not connected yet. Nothing is called.',
  'READY TO TEST': 'The credential is in place. Run Test Connection to confirm it works.',
  CONNECTED: 'A real answer came back from the provider.',
  DEGRADED: 'The last attempt failed. It will be tried again when someone asks.',
  'RATE LIMITED': 'The provider asked us to slow down. Calls resume shortly.',
  'IMPORT ONLY': 'No live connection - data comes in from exported files.',
  UNAVAILABLE: 'The provider refused or keeps failing. Check the account.',
  'ENTITLEMENT MISSING': 'The account does not include this data. Check the plan with the provider.',
}

/** An unknown status from the server is shown as NOT CONFIGURED, never as connected. */
export function safeStatus(s) {
  return STATUSES.includes(s) ? s : 'NOT CONFIGURED'
}

export function toneOf(s) {
  return TONE[safeStatus(s)]
}

/** Test Connection is offered only where the server says a real, safe test exists. */
export function canTest(card, isAdmin) {
  return !!(card && card.test && card.test.available && isAdmin)
}

export function money(c) {
  if (c === null || c === undefined) return null
  return '$' + (Number(c) / 100).toFixed(2)
}

/** Usage line, e.g. "3 charged requests · $1.25 this month". */
export function usageLine(card) {
  const u = card && card.usage
  if (!u) return null
  const parts = [`${u.calls || 0} charged ${u.calls === 1 ? 'request' : 'requests'}`, `${money(u.cents || 0)} this month`]
  if (u.billable_lookups_30d !== undefined) parts.push(`${u.billable_lookups_30d} paid list lookups in 30 days`)
  return parts.join(' · ')
}

/** Budget line from what the workspace actually configured; "no limit set" is said plainly. */
export function budgetLine(card) {
  const b = card && card.budget
  if (!b) return null
  const parts = []
  if (b.paused) parts.push('paid data is paused')
  parts.push(b.monthly_cents == null ? 'no monthly limit set' : `${money(b.monthly_cents)} a month`)
  if (b.daily_cents != null) parts.push(`${money(b.daily_cents)} a day`)
  if (b.remaining_cents != null) parts.push(`${money(b.remaining_cents)} left`)
  if (card.key === 'attom') parts.push(b.unit_cents == null ? 'price per request not set' : `${money(b.unit_cents)} per request`)
  if (card.key === 'tracerfy') {
    if (b.unit_cents != null) parts.push(`${money(b.unit_cents)} per owner found`)
    if (b.lookups_daily_cap != null) parts.push(`${b.lookups_daily_cap} lookups a day max`)
  }
  return parts.join(' · ')
}

/** Dollars typed by a person -> cents, or null when it is not a valid price. */
export function centsFromDollars(text) {
  const s = String(text ?? '').replace(/[$,\s]/g, '')
  if (s === '' || !/^\d+(\.\d{1,2})?$/.test(s)) return null
  const c = Math.round(Number(s) * 100)
  return c >= 0 && c <= 100000 ? c : null
}

/** Setup steps for the connection day, per provider. Setting names are shown
 *  only when the server sent them (platform admins); everyone else reads
 *  "the platform credential". */
export function setupSteps(card) {
  const env = (card && card.env && card.env.length) ? card.env.join(', ') : null
  const cred = env ? `the server setting ${env}` : 'the platform credential'
  if (!card) return []
  if (card.key === 'propstream') {
    return ['In PropStream, build a list and export it as CSV (include skip-traced phones if you bought them there).',
      'Open Discovery Inbox, choose Import list, and pick the file.',
      'Read the preview (nothing is saved yet), then import. The result shows new, matched, held for review, rejected and conflicts.']
  }
  if (card.key === 'attom') {
    return [`A platform admin adds the ATTOM API key as ${cred} on the backend service, then redeploys.`,
      'Press Test Connection here (one ATTOM request).',
      'Turn ATTOM on for this workspace and enter the price per request from your contract (0 if your plan is flat-rate).',
      'Open a property, go to Ownership, and use Get ATTOM property data.']
  }
  return [`A platform admin adds the Tracerfy API token as ${cred} on the backend service, then redeploys.`,
    'Press Test Connection here (reads your balance; no lookup, no credits).',
    'Check the paid lookup limits in Wholesale Settings (0 means off).',
    'Use Enrich Contacts on a property, or Get phones & emails on a list.']
}
