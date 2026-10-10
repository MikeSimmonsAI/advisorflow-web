/* Pure state/sort/label logic for the Callback Center queue — no React, no DOM,
 * no imports — so `node tests/frontend/wsQueueState.test.mjs` runs it.
 *
 *   1. LOAD STATES. loading (nothing yet) / ready / empty / error (nothing to
 *      show) / refreshing (rows kept) / stale (refresh failed, rows kept).
 *   2. STALE RESPONSES. Only the newest request may change the queue.
 *   3. ORDER. Due time ascending, then created time, then id — same input,
 *      same order. Completed: newest completion first, then id.
 *   4. WORDS. Backend status values read as operator labels. An unknown value
 *      is shown as "Unknown (value)", never guessed into scheduled/completed.
 *   5. NO INVENTED CLAIMS. Nothing here computes a score, ETA or percentage.
 */

export const STATUS_LABELS = {
  due: 'Scheduled',
  completed: 'Completed',
  cancelled: 'Cancelled',
}

export function statusLabel(status) {
  if (status === null || status === undefined || status === '') return 'Unknown'
  return STATUS_LABELS[status] || `Unknown (${status})`
}

/* A still-open callback past its window is "Missed — still open"; the backend
 * keeps it open until a person completes or cancels it. */
export function itemLabel(item) {
  if (!item) return 'Unknown'
  if (item.status === 'due' && item.bucket === 'overdue') return 'Missed — still open'
  return statusLabel(item.status)
}

export function assigneeLabel(item) {
  if (item && item.assigned_to_name) return `Assigned to ${item.assigned_to_name}`
  if (item && item.assigned_to_id) return 'Assigned (name not available)'
  return 'Unassigned'
}

function ts(v) {
  const n = v ? Date.parse(v) : NaN
  return Number.isNaN(n) ? null : n
}

function cmpNullsLast(a, b) {
  if (a === b) return 0
  if (a === null) return 1
  if (b === null) return -1
  return a < b ? -1 : 1
}

function cmpId(a, b) {
  const x = String(a.id ?? ''), y = String(b.id ?? '')
  return x < y ? -1 : x > y ? 1 : 0
}

export function sortQueue(items, bucket) {
  const list = Array.isArray(items) ? items.slice() : []
  if (bucket === 'completed') {
    const neg = (v) => (ts(v) === null ? null : -ts(v))
    return list.sort((a, b) =>
      cmpNullsLast(neg(a.completed_at), neg(b.completed_at)) || cmpId(a, b))
  }
  return list.sort((a, b) =>
    cmpNullsLast(ts(a.due_at), ts(b.due_at))
    || cmpNullsLast(ts(a.created_at), ts(b.created_at))
    || cmpId(a, b))
}

export function initialQueue() {
  return { data: null, error: '', loading: true, latest: 0, failedRefresh: false }
}

export function loadStarted(state) {
  const latest = state.latest + 1
  return { ...state, latest, loading: true, error: '', failedRefresh: false }
}

/* A response from anything but the newest request is ignored outright. */
export function loadSucceeded(state, gen, data) {
  if (gen !== state.latest) return state
  return { ...state, data, loading: false, error: '', failedRefresh: false }
}

export function loadFailed(state, gen, message) {
  if (gen !== state.latest) return state
  return { ...state, loading: false, error: message || 'Something went wrong.',
           failedRefresh: state.data !== null }
}

export function queueView(state, bucket) {
  const rows = state.data && state.data.buckets && state.data.buckets[bucket]
  const n = Array.isArray(rows) ? rows.length : 0
  if (state.data === null) return state.loading ? 'loading' : 'error'
  if (state.loading) return 'refreshing'
  if (state.failedRefresh) return 'stale'
  return n ? 'ready' : 'empty'
}

/* Count on a bucket tab: the backend's number, or a dash if unknown — never 0
 * for "we don't know". */
export function bucketCount(state, bucket) {
  const c = state.data && state.data.counts && state.data.counts[bucket]
  return typeof c === 'number' ? c : '—'
}

/* Completed is capped by the backend; say so when the list is shorter than the
 * total instead of implying the list is complete. */
export function truncationNote(state, bucket) {
  const rows = state.data && state.data.buckets && state.data.buckets[bucket]
  const total = state.data && state.data.counts && state.data.counts[bucket]
  if (!Array.isArray(rows) || typeof total !== 'number') return ''
  return total > rows.length ? `Showing the ${rows.length} most recent of ${total}.` : ''
}
