/* Pure action-scoping and request-ordering logic for the deal room — no React,
 * no DOM, no imports — so `node tests/frontend/wsActionState.test.mjs` runs it.
 *
 *   1. SCOPED PENDING. A mutation names an action key "panel:action". Only that
 *      panel (and that exact action) reads as busy; another panel stays usable.
 *   2. NO DOUBLE SUBMIT. The same key cannot start twice. The check is on a
 *      plain object, not on component state, so two clicks in one tick see it.
 *   3. PER-ACTION OUTCOME. Each panel keeps its own success/error line, so a
 *      failure in one panel is not erased by a success in another.
 *   4. LOAD ORDERING. Only the newest deal-room load may replace the room.
 *   5. OPERATOR WORDS. "label [code]" shows the label with the code as a
 *      support reference, never the bare code.
 */

export const DEFAULT_PANEL = 'page'

export function panelOf(key) {
  const s = String(key || '')
  const i = s.indexOf(':')
  return i === -1 ? (s || DEFAULT_PANEL) : s.slice(0, i)
}

export function createActionTracker() {
  const pending = Object.create(null)   // key -> true
  let loadGen = 0
  return {
    isPending(key) { return pending[key] === true },
    /* Any action in this panel — what a panel's own buttons disable on. */
    panelPending(panel) {
      return Object.keys(pending).some((k) => panelOf(k) === panel)
    },
    pendingKeys() { return Object.keys(pending) },
    /* Claim a key. false = already running; the caller must do nothing. */
    begin(key) {
      if (pending[key]) return false
      pending[key] = true
      return true
    },
    end(key) { delete pending[key] },
    /* Load ordering: take a token, then ask whether it is still current. */
    nextLoad() { loadGen += 1; return loadGen },
    isCurrentLoad(gen) { return gen === loadGen },
  }
}

/* Outcomes by panel: { [panel]: { kind: 'ok'|'error', text } } */
export function setOutcome(outcomes, panel, kind, text) {
  return { ...outcomes, [panel]: { kind, text } }
}

export function clearOutcome(outcomes, panel) {
  if (!(panel in outcomes)) return outcomes
  const next = { ...outcomes }
  delete next[panel]
  return next
}

/* What a panel shows: its own outcome, plus the page-level one. */
export function outcomesFor(outcomes, panel) {
  return [outcomes[DEFAULT_PANEL], panel !== DEFAULT_PANEL ? outcomes[panel] : null]
    .filter(Boolean)
}

/* Parse "words [code]" → text a person reads with a trailing support reference. */
export function describeError(message) {
  const s = String(message == null ? '' : message).trim()
  const m = s.match(/^(.*?)\s*\[([a-z][a-z0-9_]*)\]\s*$/s)
  if (!m) return s || 'Something went wrong.'
  const words = m[1].trim()
  return words ? `${words} (reference: ${m[2]})`
               : `Something went wrong. (reference: ${m[2]})`
}
