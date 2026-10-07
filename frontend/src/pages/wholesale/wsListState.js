/* Pure load-state logic shared by the Buyers and Properties lists — no React, no
 * DOM, no imports — so `node tests/frontend/wsListState.test.mjs` runs it.
 *
 *   1. LOAD STATES. loading (nothing yet) / ready / empty / error (nothing to
 *      show) / refreshing (rows kept) / stale (refresh failed, rows kept).
 *   2. STALE RESPONSES. Only the newest request may change the list, so typing
 *      in search cannot let a slow old answer overwrite a newer one.
 *   3. KNOWN VS ZERO. A count the server did not send is "—", never 0.
 *   4. TRUNCATION. A page shorter than the server total says so.
 *   5. NO INVENTED CLAIMS. Nothing here computes a score, ETA or percentage.
 */

export function initialList() {
  return { rows: null, total: null, error: '', supportCode: '', loading: true,
           latest: 0, failedRefresh: false }
}

export function loadStarted(state) {
  return { ...state, latest: state.latest + 1, loading: true, error: '',
           supportCode: '', failedRefresh: false }
}

/* A response from anything but the newest request is ignored outright. */
export function loadSucceeded(state, gen, rows, total) {
  if (gen !== state.latest) return state
  const list = Array.isArray(rows) ? rows : []
  return { ...state, rows: list, total: typeof total === 'number' ? total : null,
           loading: false, error: '', supportCode: '', failedRefresh: false }
}

/* A failed refresh keeps the last good rows and marks them possibly out of date. */
export function loadFailed(state, gen, message, code) {
  if (gen !== state.latest) return state
  return { ...state, loading: false, error: message || 'Something went wrong.',
           supportCode: code || '', failedRefresh: state.rows !== null }
}

export function listView(state) {
  if (state.rows === null) return state.loading ? 'loading' : 'error'
  if (state.loading) return 'refreshing'
  if (state.failedRefresh) return 'stale'
  return state.rows.length ? 'ready' : 'empty'
}

/* Support code the server supplied, if any. Never invented. */
export function supportCode(err) {
  if (!err) return ''
  const d = err.detail && typeof err.detail === 'object' ? err.detail : {}
  const code = d.support_code || d.request_id || err.support_code || err.request_id
  return typeof code === 'string' || typeof code === 'number' ? String(code) : ''
}

/* A count the server did not send is a dash, never 0. */
export function knownCount(n) {
  return typeof n === 'number' && Number.isFinite(n) ? n : '—'
}

export function truncationNote(state) {
  if (!state.rows || typeof state.total !== 'number') return ''
  return state.total > state.rows.length
    ? `Showing the first ${state.rows.length} of ${state.total}. Narrow the search to see the rest.` : ''
}

/* Why the retry/refresh control is unavailable, or '' when it is usable. */
export function retryDisabledReason(state) {
  return state.loading ? 'A load is already in progress.' : ''
}

/* Search text is sent trimmed; an all-space search is no search. */
export function cleanQuery(q) {
  return String(q ?? '').trim()
}

/* Buyer wording. The server stores active/inactive/opt-out as flags; an opted-out
 * buyer must never read as merely "Active". */
export function buyerStatusLabel(b) {
  if (!b) return 'Unknown'
  if (b.do_not_contact) return 'Opted out'
  if (b.is_active === false) return 'Inactive'
  if (b.is_active === true) return 'Active'
  return 'Unknown'
}

/* "Verified only" keeps rows with a verified cash or POF flag; an unknown flag
 * is not treated as verified. */
export function isVerifiedBuyer(b) {
  return !!(b && (b.cash_verified === true || b.proof_of_funds_on_file === true))
}

export function activeBuyerCount(rows) {
  return (rows || []).filter((b) => b.is_active && !b.do_not_contact).length
}

/* The row being edited/shown may vanish after a refresh or delete; look it up
 * safely instead of letting a drawer dereference undefined. */
export function findById(rows, id) {
  if (!rows || id === null || id === undefined) return null
  return rows.find((r) => r.id === id) || null
}
