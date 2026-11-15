/**
 * PROGRAM ACCESS DESTINATIONS - shared by the web launcher (/workspaces) and
 * the EvoSys Pro mobile app (/app/programs).
 *
 * Two things live here so both screens follow ONE rule:
 *   salesToolCards  the Deal Builder / My Team / Sales Onboarding cards, built
 *                   from the server's /auth/my-contexts answer (`deal_builder`,
 *                   `sales_team`). Nothing here grants anything.
 *   openProgramItem what tapping a card does. Choosing a card is a context
 *                   choice, never an authorization grant: every destination
 *                   re-checks on the server.
 *
 * Moved verbatim from ProgramAccessPortal.jsx and App.jsx (WorkspaceSelector);
 * behaviour is unchanged.
 */
import { clearWorkspaceContext, setBrandContext, setWorkspaceContext } from '../../api/client'

// salesToolCards lives in programAccessModel.js (pure, node-testable) and is
// re-exported here so callers have one place to import destinations from.
export { salesToolCards } from './programAccessModel.js'

export function openProgramItem(item, navigate) {
  // A PREVIEW is a deal's existing public demo link, not a workspace: no
  // context is set and nothing is entered. It opens in a new tab so the
  // launcher stays where it was.
  if (item.type === 'preview') {
    // A God-granted preview opens inside this app (signed in, re-checked).
    if (item.grant_id && item.path) { navigate(item.path); return }
    if (item.url) window.open(item.url, '_blank', 'noopener,noreferrer')
    return
  }
  if (item.type === 'deal_builder' && item.href) { navigate(item.href); return }
  // Back Office / Executive Suite: leave any program workspace first, so no
  // stale workspace header follows the person into the back office.
  if (item.type === 'back_office') {
    clearWorkspaceContext()
    // The brand this Back Office sells travels with every /sales request; the
    // server honours it only for a brand this person already sells for.
    if (item.platform_id) setBrandContext(item.platform_id, item.platform_name || '')
    navigate(item.path || '/sales'); return
  }
  if (item.type === 'acquisition_ops') {
    clearWorkspaceContext()
    navigate('/acquisition-ops'); return
  }
  if (item.type === 'volt_ops' || item.type === 'consumer_leads') {
    clearWorkspaceContext()
    navigate(item.path); return
  }
  if (item.type === 'executive') {
    clearWorkspaceContext()
    if (item.platform_id) setBrandContext(item.platform_id, item.platform_name || '')
    navigate('/executive'); return
  }
  // Selection is a context choice, not an authorization grant. The
  // /workspace route confirms membership again and product routes enforce
  // their own named seat.
  setWorkspaceContext(item.organization_id)
  const target = item.type === 'product'
    ? '/workspace/' + item.organization_id + '?product=' + encodeURIComponent(item.key)
      + (item.view ? '&view=' + encodeURIComponent(item.view) : '')
    : '/workspace/' + item.organization_id
  navigate(target)
}
