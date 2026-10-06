/**
 * sciDoor — the pure decision behind the /sci front door.
 *
 * Input is the server-built /auth/my-contexts answer; nothing is derived in the
 * browser and nothing here grants access (the backend re-checks membership on
 * every request inside the workspace).
 *
 *   enter     the caller holds an SCI workspace membership -> select it, open Program Center
 *   god       platform owner with no SCI membership -> Platform Console, enter SCI via God Mode
 *   denied    anything else -> a clean refusal, never another workspace
 */
export const SCI_ORG_NAME = 'service corporation international'

export function findSciWorkspace(ctx) {
  const list = ctx && Array.isArray(ctx.workspace_contexts) ? ctx.workspace_contexts : []
  for (let i = 0; i < list.length; i++) {
    const w = list[i]
    if (w && String(w.organization_name || '').trim().toLowerCase() === SCI_ORG_NAME) return w
  }
  return null
}

export function decideSciDoor(ctx) {
  const w = findSciWorkspace(ctx)
  if (w) return { state: 'enter', organizationId: w.organization_id }
  const platform = ctx && Array.isArray(ctx.platform_contexts) ? ctx.platform_contexts : []
  if (platform.some(c => c && c.path === '/god')) return { state: 'god' }
  return { state: 'denied' }
}

/**
 * Post-login landing: a person whose ONLY customer workspace is SCI, and who
 * has no back office, lands in /sci. Everyone else keeps their existing route.
 */
export function shouldLandInSci(ctx) {
  if (!ctx || ctx.has_back_office) return false
  if (ctx.workspace_count !== 1) return false
  const def = ctx.default_context || {}
  if (def.type !== 'workspace') return false
  const w = findSciWorkspace(ctx)
  return !!w && w.organization_id === def.organization_id
}
