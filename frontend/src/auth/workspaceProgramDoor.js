/**
 * The server decides which workspaces a person may enter. This helper only
 * chooses the appropriate presentation AFTER /auth/my-contexts authorized it.
 * Never use a workspace slug as permission: /roofing-ops checks tenant +
 * explicit roofing_program entitlement on every request.
 */
export function workspaceProgramDestination(entry) {
  if (!entry || entry.type && entry.type !== 'workspace') return null
  // Future roofing companies are identified by the server's industry route.
  // The one internal provisioning workspace also needs a landing while its
  // GOD MODE feature activation has not yet run: show the dedicated feature
  // gate instead of misleading generic/SCI navigation.
  if (entry.program_route === '/program/roofing' ||
      entry.organization_slug === 'evosys-pro-roofing') return '/program/roofing'
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
