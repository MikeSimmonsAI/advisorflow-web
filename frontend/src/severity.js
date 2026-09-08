/**
 * SEVERITY — the client's copy of the platform vocabulary.
 *
 * THE SERVER IS AUTHORITATIVE. `app/services/severity.py` defines the five
 * values and the words for them, and every endpoint that has an opinion about
 * health sends `severity` and `severity_label` alongside its data. A screen
 * rendering server data must use the label the server sent.
 *
 * THIS FILE EXISTS FOR THE OTHER CASE: a surface whose backend still answers
 * in booleans — "is Twilio connected: true" — where the browser has to decide
 * which of the five words that is. Those decisions belong in one place rather
 * than in each component, and they must reach the same five words as the
 * server, or an advisor's page and the owner's page would describe the same
 * condition with different vocabulary.
 *
 * WHY FIVE AND NOT THREE. The distinction three-state models keep collapsing:
 *
 *   NOTHING YET   nothing has happened, so there is nothing to measure.
 *                 A brand-new customer who has sent no messages is here.
 *   CAN'T CHECK   we cannot see this. It may be perfect; it may be on fire.
 *
 * Neither is green. Rendering "we cannot see this" as healthy is the exact
 * failure the vocabulary exists to prevent.
 */

export const HEALTHY = 'healthy'
export const ATTENTION = 'attention'
export const ACTION_REQUIRED = 'action_required'
export const UNAVAILABLE = 'unavailable'
export const NO_DATA = 'no_data'

export const LABELS = {
  [HEALTHY]: 'Healthy',
  [ATTENTION]: 'Needs attention',
  [ACTION_REQUIRED]: 'Action required',
  [UNAVAILABLE]: "Can't check",
  [NO_DATA]: 'Nothing yet',
}

export const MEANINGS = {
  [HEALTHY]: 'Working normally.',
  [ATTENTION]: 'Working, but something here needs a decision soon.',
  [ACTION_REQUIRED]: 'Not working, or costing you money right now.',
  [UNAVAILABLE]: "We can't see this yet, so we won't guess. It is not a report "
    + 'that anything is wrong.',
  [NO_DATA]: 'Nothing has happened here yet, so there is nothing to measure.',
}

/** Worst first, so a list sorted by this leads with what needs doing. */
const ORDER = {
  [ACTION_REQUIRED]: 0,
  [ATTENTION]: 1,
  [UNAVAILABLE]: 2,
  [NO_DATA]: 3,
  [HEALTHY]: 4,
}

/** An unknown value resolves to CAN'T CHECK — never to healthy. A typo must
 *  not be able to paint a subsystem green. */
export function normalize(value) {
  return ORDER[value] === undefined ? UNAVAILABLE : value
}

export function rank(value) {
  return ORDER[normalize(value)]
}

/** The overall state of a set. Empty means NOTHING YET, which is not the same
 *  as a set whose members are all fine. */
export function worst(values) {
  const vals = (values || []).map(normalize)
  if (!vals.length) return NO_DATA
  return vals.sort((a, b) => rank(a) - rank(b))[0]
}

export function labelFor(value) {
  return LABELS[normalize(value)]
}
