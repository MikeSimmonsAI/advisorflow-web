/**
 * PROGRAM EXPERIENCE - which shell a workspace opens, as the SERVER decided it
 * (/auth/my-contexts -> workspace_contexts[].experience). Pure, for tests.
 *
 * A program workspace never renders the general EvoSys platform screens:
 * production 2026-10-20 showed wholesale@ the platform dashboard, whose
 * sidebar carried SCI's "Family Service Center". The server refuses those
 * APIs too (app/services/program_experience.py); this keeps the app from
 * drawing a screen the server will refuse.
 */
export const PROGRAM_HOMES = {
  wholesale: '/wholesale/evosense',
  sci: '/program',
  roofing: '/program/roofing',
}
const SHARED_OK = ['/change-password', '/login', '/logout', '/workspaces']

export function activeWorkspaceEntry(ctx, selectedId, homeOrgId) {
  const list = ctx?.workspace_contexts || []
  return list.find(w => w.organization_id === selectedId) ||
         list.find(w => w.organization_id === homeOrgId) || null
}

function inside(experience, path) {
  if (experience === 'wholesale') return path === '/wholesale' || path.startsWith('/wholesale/')
  if (experience === 'sci') return path === '/program' || (path.startsWith('/program/') && !path.startsWith('/program/roofing')) || path === '/sci' || path.startsWith('/sci/')
  if (experience === 'roofing') return path === '/program/roofing' || path.startsWith('/program/roofing/')
  return true
}

/** Where a general-platform page should send this person instead, or null. */
export function experienceRedirect(ctx, path, selectedId, homeOrgId, role) {
  if (!ctx || role === 'god_admin' || role === 'super_admin') return null
  if (SHARED_OK.some(p => path === p || path.startsWith(p + '/'))) return null
  const entry = activeWorkspaceEntry(ctx, selectedId, homeOrgId)
  const exp = entry?.experience
  const home = PROGRAM_HOMES[exp]
  if (!home || inside(exp, path)) return null
  return home
}
