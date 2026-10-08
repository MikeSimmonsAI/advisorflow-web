/* Pure helpers for the manager approval queue page. No React, no network.
 *
 * Money is rendered from the server's integer-cent {cents, display} blocks and
 * is never computed here; a missing block is a dash, never $0.
 */

export function usd(m) {
  return m && typeof m.display === 'string' ? '$' + m.display : '—'
}

export const STATUS_TEXT = {
  pending: 'Pending', approved: 'Approved', denied: 'Denied',
  withdrawn: 'Withdrawn', stale: 'Stale',
}

/** Request-body for the versioned decision. Version always comes from the row. */
export function decisionBody(item, approve, note) {
  const n = typeof note === 'string' ? note.trim() : ''
  return { approve: approve === true, expected_version: item.version, note: n || null }
}

/** Why a row cannot be approved from this screen, or null when it can. */
export function approveBlockedReason(item) {
  if (!item) return 'Request unavailable.'
  if (item.status !== 'pending') return 'This request is no longer pending.'
  if (!item.actionable) return item.blocker_text || 'This request cannot be answered right now.'
  return null
}

/** Message for a failed call; 409 means the row must be reloaded, never "done". */
export function errorMessage(e) {
  const status = e && (e.status || (e.response && e.response.status))
  const detail = e && (e.detail || (e.response && e.response.data && e.response.data.detail) || e.message)
  const text = typeof detail === 'string' && detail ? detail : 'The request failed.'
  if (status === 409) return { reload: true, text: text + ' The queue has been reloaded.' }
  if (status === 403) return { reload: false, text: 'You do not have manager access for this brand.' }
  if (status === 404) return { reload: true, text: 'That request was not found.' }
  return { reload: false, text }
}

/** Summary line that never turns an unavailable count into zero. */
export function countsLine(q) {
  if (!q || q.pending_count == null) return 'Counts unavailable.'
  return q.pending_count + ' pending · ' + q.actionable_count + ' actionable · ' + q.blocked_count + ' blocked'
}
