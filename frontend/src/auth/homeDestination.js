/**
 * WHERE "/" SENDS A SIGNED-IN PERSON, FROM THE SERVER'S CONTEXT ANSWER ONLY.
 *
 * Imports nothing, so a plain `node` test runs the real decision.
 *
 * THE DEFECT THIS CLOSES: HomeRedirect handled executive / workspace /
 * workspace_selector defaults and sent a back-office person to /sales only when
 * they held ZERO workspaces. A legitimate dual-role user (brand sales + a
 * customer workspace membership, NULL users.organization_id) has default_context
 * type "platform" and workspace_count 1, matched none of those, and fell through
 * to the tenant Overview - a customer screen with no tenant behind it - with no
 * way to reach either context except by typing a URL.
 *
 * Landing is a REDIRECT, never a grant: /sales and /workspace/:id re-check
 * membership server-side. A dual-role user lands in the back office and the
 * ContextSwitcher there offers the workspace(s) from the same server answer.
 * A sales-only user is never sent to a customer workspace.
 *
 * Returns { to } for a redirect, or { to: null } meaning "render the tenant
 * home" (god keeps it, as before; so do legacy tenant users).
 */
export function decideHomeDestination(ctx, user) {
  if (!ctx || typeof ctx !== 'object') return { to: null }
  const def = ctx.default_context || {}
  const isGod = !!user && user.role === 'god_admin'

  if (def.type === 'executive') return { to: '/executive' }
  if (def.type === 'workspace' && def.organization_id) {
    return { to: '/workspace/' + def.organization_id }
  }
  if (def.type === 'workspace_selector') return { to: '/workspaces' }
  // Any non-god person the server says holds back-office access lands there,
  // whether or not they also hold workspaces. God's home stays as it was.
  if (!isGod && ctx.has_back_office) return { to: '/sales' }
  return { to: null }
}
