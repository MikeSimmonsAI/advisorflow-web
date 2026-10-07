/**
 * WHAT A NORMAL (NON-GOD) ACCOUNT SEES WHEN IT HAS ZERO, ONE OR MANY WORKSPACES.
 *
 * Imports nothing, so a plain `node` test runs the real decisions.
 *
 * Everything here reads the server's /auth/my-contexts answer and invents
 * nothing. It grants nothing either: every route re-checks membership.
 *
 * THE FACTS THE BROWSER CAN HOLD — AND THE RULE THAT THEY NEVER MERGE:
 *   loading   no answer yet            -> render nothing definite
 *   error     the question failed      -> say so, offer retry
 *   none      the server listed zero   -> honest "no workspace access" state
 *   single / multiple                  -> land / choose
 * Before this file, WorkspaceSelector rendered `null` forever for loading AND
 * error (no retry), and an empty "Choose a workspace" list for none.
 */

export const LANDING_LOADING = 'loading'
export const LANDING_ERROR = 'error'
export const LANDING_NONE = 'none'
export const LANDING_SINGLE = 'single'
export const LANDING_MULTIPLE = 'multiple'

export function workspacesOf(contexts) {
  const list = contexts && contexts.workspace_contexts
  if (!Array.isArray(list)) return []
  return list.filter(w => w && w.organization_id)
}

/** phase: 'idle'|'loading'|'ready'|'error' (same as useAuthorizedContexts). */
export function decideWorkspaceLanding(phase, contexts) {
  if (phase === 'error') return { kind: LANDING_ERROR }
  if (phase !== 'ready' || !contexts || typeof contexts !== 'object') {
    return { kind: LANDING_LOADING }
  }
  // A ready answer with no workspace_contexts array at all is a malformed
  // answer, not "zero workspaces". Unknown must not read as none.
  if (!Array.isArray(contexts.workspace_contexts)) return { kind: LANDING_ERROR }
  const ws = workspacesOf(contexts)
  if (ws.length === 0) {
    return {
      kind: LANDING_NONE,
      // Somewhere else they may legitimately go; never a customer workspace.
      hasBackOffice: !!contexts.has_back_office,
      hasExecutive: Array.isArray(contexts.executive_contexts) &&
                    contexts.executive_contexts.length > 0,
    }
  }
  if (ws.length === 1) return { kind: LANDING_SINGLE, workspace: ws[0] }
  return { kind: LANDING_MULTIPLE, workspaces: ws }
}

/**
 * Workspaces to offer as switch targets from inside `activeId`: the server's
 * list only (an id the server did not list is never offered), minus the one
 * being stood in. The in-workspace switcher previously rendered nothing for a
 * customer-only person holding several memberships.
 */
export function switchTargets(contexts, activeId) {
  return workspacesOf(contexts).filter(w => w.organization_id !== activeId)
}

/**
 * STALE-RESPONSE RULE FOR A WORKSPACE SWITCH.
 *
 * `scope` is the selected-workspace id captured when the request was issued;
 * `current` is the selection now. A response whose scope no longer matches the
 * selection belongs to the workspace the person has walked out of and must be
 * dropped — not stored, not rendered. Both empty matches.
 */
export function isStaleForWorkspace(scope, current) {
  return (scope || null) !== (current || null)
}
