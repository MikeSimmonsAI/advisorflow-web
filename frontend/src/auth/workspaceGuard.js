/**
 * WHAT THE WORKSPACE DOOR IS ALLOWED TO CONCLUDE.
 *
 * This file exists because the first version of the workspace guard was one
 * `.catch()`:
 *
 *     api.get('/auth/workspace/' + id)
 *       .then(ok)
 *       .catch(() => setState({ status: 'denied' }))
 *
 * That treats 401, 404, 500, a dropped connection and a timeout as the same
 * answer as 403. It is the mirror image of failing open, and it is just as
 * wrong: a guard that cannot tell "the server refused you" from "the server
 * did not answer" is not reporting authorization, it is reporting the weather.
 * An advisor whose request failed in transit was told, in plain words, that he
 * did not have access to his own workspace.
 *
 * THE RULE, both directions:
 *   - A transport, session or server failure is NEVER access.   (no fail-open)
 *   - A transport, session or server failure is NEVER a refusal. (no fail-shut)
 *   Those failures get their own state, and it says so out loud.
 *
 * Only the server decides. This module decides nothing about membership; it
 * decides only which of four things the browser has actually been TOLD, and it
 * is pure so a test can run every one of those cases without a browser.
 *
 * Backend enforcement is untouched and remains authoritative: every route
 * inside a workspace re-checks customer_org membership on its own, so even the
 * AUTHORIZED state here buys nothing the server has not separately granted on
 * each request.
 */

// ── the four states ─────────────────────────────────────────────────────────
// VERIFYING   nothing has been concluded yet. Render neither the workspace nor
//             a refusal.
// AUTHORIZED  the server listed this workspace among the caller's contexts, or
//             the enforcement endpoint accepted it.
// DENIED      the server authoritatively refused: HTTP 403 from the endpoint
//             whose entire job is to answer this question.
// UNVERIFIED  the question could not be answered. Not access, not refusal.
export const VERIFYING = 'verifying'
export const AUTHORIZED = 'authorized'
export const DENIED = 'denied'
export const UNVERIFIED = 'unverified'

// The only status that means "no". Deliberately a one-element list rather than
// an inequality: a future reader adding 404 or 409 to it has to type the
// number and, one hopes, justify it.
export const DENIAL_STATUSES = [403]

export function errorStatus(err) {
  if (!err) return null
  const s = err.status
  return typeof s === 'number' ? s : null
}

export function errorMessage(err) {
  if (!err) return ''
  return typeof err.message === 'string' ? err.message : ''
}

/**
 * Does the server's own context list name this workspace?
 *
 * /auth/my-contexts is built by workspace_access.authorized_contexts from
 * ACTIVE customer_org memberships, and it is already the list the login
 * redirect and the switcher navigate from. Reading it here is not a second
 * authorization authority - it is the same one, read once instead of twice.
 */
export function contextsListWorkspace(contexts, organizationId) {
  if (!contexts || !organizationId) return false
  const list = contexts.workspace_contexts
  if (!Array.isArray(list)) return false
  for (let i = 0; i < list.length; i++) {
    if (list[i] && list[i].organization_id === organizationId) return true
  }
  return false
}

/**
 * decideWorkspaceAccess — the whole decision, as data in and data out.
 *
 * input:
 *   organizationId  string from the URL
 *   contextsPhase   'idle' | 'loading' | 'ready' | 'error'   (/auth/my-contexts)
 *   contexts        the parsed response, when ready
 *   contextsError   the thrown error, when errored (may carry .status)
 *   confirmPhase    'idle' | 'loading' | 'ok' | 'error'  (/auth/workspace/{id})
 *   confirmError    the thrown error, when errored (may carry .status)
 *
 * returns { state, reason, status?, message? }
 */
export function decideWorkspaceAccess(input) {
  const opts = input || {}
  const organizationId = opts.organizationId
  const contextsPhase = opts.contextsPhase || 'idle'
  const confirmPhase = opts.confirmPhase || 'idle'

  // A URL naming no workspace is the one refusal the browser makes on its own,
  // and it refuses a request that asked for nothing.
  if (!organizationId) {
    return { state: DENIED, reason: 'no-workspace-named' }
  }

  // ── 1. the context list ──
  if (contextsPhase === 'idle' || contextsPhase === 'loading') {
    return { state: VERIFYING, reason: 'contexts-loading' }
  }

  if (contextsPhase === 'error') {
    const status = errorStatus(opts.contextsError)
    // 401 is not a refusal of this workspace - it is "prove who you are". The
    // API client is already redirecting to /login by the time this is read, so
    // keep waiting rather than flashing an error at somebody on their way out.
    if (status === 401) {
      return { state: VERIFYING, reason: 'reauthenticating', status: 401 }
    }
    return {
      state: UNVERIFIED,
      reason: 'contexts-unavailable',
      status: status,
      message: errorMessage(opts.contextsError),
    }
  }

  // ── 2. the server listed it ──
  if (contextsListWorkspace(opts.contexts, organizationId)) {
    return { state: AUTHORIZED, reason: 'listed-by-server' }
  }

  // ── 3. absent from the list: confirm with the enforcement endpoint ──
  //
  // Absence from a server-built list is already a server answer, but it is an
  // answer by OMISSION, and omission has more causes than refusal does: the
  // organization row is missing, the customer is suspended, the list is a few
  // seconds stale after an invitation was accepted. Telling somebody they have
  // no access on the strength of a gap costs one request to avoid, and that
  // request returns the only status this module treats as "no".
  if (confirmPhase === 'idle' || confirmPhase === 'loading') {
    return { state: VERIFYING, reason: 'confirming' }
  }
  if (confirmPhase === 'ok') {
    // The list was stale and the server says yes. The server wins.
    return { state: AUTHORIZED, reason: 'confirmed-by-server' }
  }

  const status = errorStatus(opts.confirmError)
  if (DENIAL_STATUSES.indexOf(status) !== -1) {
    return { state: DENIED, reason: 'server-refused', status: status }
  }
  if (status === 401) {
    return { state: VERIFYING, reason: 'reauthenticating', status: 401 }
  }
  // 404 (route missing - a frontend newer than the backend), 5xx, a network
  // failure carrying no status at all, a timeout. None of these is a refusal.
  return {
    state: UNVERIFIED,
    reason: 'confirmation-unavailable',
    status: status,
    message: errorMessage(opts.confirmError),
  }
}
