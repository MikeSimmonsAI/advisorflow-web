/**
 * The server decides which workspaces a person may enter. This helper only
 * chooses the appropriate presentation AFTER /auth/my-contexts authorized it.
 * Never use a workspace slug as permission: /roofing-ops checks tenant +
 * explicit roofing_program entitlement on every request.
 */
export function workspaceProgramDestination(entry) {
  if (!entry || entry.type && entry.type !== 'workspace') return null
  // The server sets program_route ONLY for a workspace whose Roofing program
  // is switched on. Never route by slug or name: a Roofing org that was not
  // set up opened a dead "isn't turned on" screen on every entry.
  if (entry.program_route === '/program/roofing') return '/program/roofing'
  // Set only for the managed-acquisition client (explicit client_acquisition).
  if (entry.program_route === '/program/acquisition') return '/program/acquisition'
  if (entry.is_program) return '/program'
  return null
}

/** Product choice is trusted only if the server explicitly returned it for
 * this exact authorized organization. A crafted query parameter cannot grant
 * product access or change the hosting customer workspace. */
export function productWorkspaceDestination(ctx, orgId, productKey) {
  const match = (ctx?.product_contexts || []).find(p =>
    p.organization_id === orgId && p.key === productKey && p.type === 'product')
  return match?.path || null
}
