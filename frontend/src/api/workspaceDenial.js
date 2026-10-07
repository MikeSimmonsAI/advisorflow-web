/**
 * WHAT THE CLIENT DOES WHEN THE SERVER REFUSES THE SELECTED WORKSPACE — pure,
 * imports nothing, so the exact production decision runs under plain node.
 *
 * The server (app/deps.py) answers an X-Workspace-Id the caller does not hold
 * with 403 and ONE sentence, identical for "does not exist" and "belongs to
 * someone else". The client must then:
 *
 *   1. drop the selection, its branding, its location and its in-flight reads,
 *   2. NOT fall back to the previously selected workspace (A) - the person
 *      asked for B and was refused; showing A under B's name is the exact lie
 *      the server stopped telling,
 *   3. ignore any response that was in flight FOR B and lands afterwards,
 *   4. leave /auth/my-contexts reachable so the person can choose again.
 */

export const WORKSPACE_DENIED_DETAIL = 'That workspace is not available to this account.'

export function isWorkspaceDenial(status, detail) {
  return status === 403 && detail === WORKSPACE_DENIED_DETAIL
}

/**
 * A response is stale when the request carried a workspace id and the current
 * selection is no longer that id (switched away, or cleared by a denial).
 * Requests sent with no workspace are never stale on this account, and /auth/*
 * is never dropped - recovery must always be able to land.
 */
export function isStaleWorkspaceResponse(sentWorkspaceId, currentWorkspaceId, path) {
  if (!sentWorkspaceId) return false
  if (String(path || '').startsWith('/auth/')) return false
  return (currentWorkspaceId || null) !== sentWorkspaceId
}

/**
 * Apply the denial through the caller's own state functions, in the order the
 * production client uses. `effects` = { clearBranding, clearWorkspaceLocation,
 * resetInFlightGets, clearWorkspaceContext }. Returns true when it acted.
 */
export function applyWorkspaceDenial(status, detail, effects) {
  if (!isWorkspaceDenial(status, detail)) return false
  effects.clearBranding()
  effects.clearWorkspaceLocation()
  effects.resetInFlightGets()
  effects.clearWorkspaceContext()
  return true
}
