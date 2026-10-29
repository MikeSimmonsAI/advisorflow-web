/* WHERE A SIGN-IN LANDS - shared by the password form and Sign in with Google,
 * so both open exactly the same place. The SERVER decides (/auth/my-contexts),
 * not a role label. Moved verbatim out of Login.jsx (#430).
 *
 * /auth/my-contexts returns a default_context built from real memberships: the
 * back office when they have one, their single workspace when that is all they
 * hold, a selector when they hold several. A failure falls back to the old
 * behaviour rather than stranding somebody at a blank page.
 */
import { fetchMyContexts, setWorkspaceContext, clearWorkspaceContext } from '../api/client'

export async function signInDestination(user, nextPath, priorWorkspace) {
  let dest = user?.role === 'god_admin' ? '/god' : '/'
  try {
    const ctx = await fetchMyContexts()
    const def = ctx && ctx.default_context
    if (def && def.path) {
      dest = def.path
      // A product home (wholesale@ -> Wholesale) runs inside its host
      // workspace, so that workspace is selected too.
      if ((def.type === 'workspace' || def.type === 'product') && def.organization_id) {
        setWorkspaceContext(def.organization_id)
      } else {
        clearWorkspaceContext()
      }
    }
    // BACK TO WHERE THEY WERE after an expired session - into the same
    // workspace when they still have it, otherwise the default above.
    // Never return into the Roofing app for a workspace whose Roofing
    // program is not switched on (the server's program_route says so):
    // that was a dead "isn't turned on" screen after every re-login.
    const priorEntry = (ctx?.workspace_contexts || []).find(w => w.organization_id === priorWorkspace)
    const deadRoofing = nextPath && nextPath.startsWith('/program/roofing') &&
      !(priorEntry && priorEntry.program_route === '/program/roofing')
    if (nextPath && !deadRoofing) {
      const still = priorWorkspace && (ctx?.contexts || []).some(c => c.type === 'workspace' && c.organization_id === priorWorkspace)
      if (still) { setWorkspaceContext(priorWorkspace); dest = nextPath }
      else if (!priorWorkspace) dest = nextPath
    }
  } catch (ctxErr) {
    // Keep the legacy destination. A context lookup that fails must not
    // turn a successful sign-in into an error screen.
  }
  return dest
}
