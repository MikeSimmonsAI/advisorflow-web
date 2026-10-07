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

/* ── Single-record loads (Command's board) ────────────────────────────────────
 * Same rules as the list: newest request wins, a failed refresh keeps the last
 * good data and marks it possibly out of date. */
export function initialRecord() {
  return { data: null, error: '', supportCode: '', loading: true, latest: 0, failedRefresh: false }
}
export function recordStarted(state) {
  return { ...state, latest: state.latest + 1, loading: true, error: '', supportCode: '', failedRefresh: false }
}
export function recordSucceeded(state, gen, data) {
  if (gen !== state.latest) return state
  return { ...state, data: data ?? null, loading: false, error: '', supportCode: '', failedRefresh: false }
}
export function recordFailed(state, gen, message, code) {
  if (gen !== state.latest) return state
  return { ...state, loading: false, error: message || 'Something went wrong.',
           supportCode: code || '', failedRefresh: state.data !== null }
}
export function recordView(state) {
  if (state.data === null) return state.loading ? 'loading' : 'error'
  if (state.loading) return 'refreshing'
  return state.failedRefresh ? 'stale' : 'ready'
}

/* Deterministic order: primary key, then id, so equal rows never swap places.
 * Rows with no value for the key always sort last. */
export function sortByKeyThenId(rows, keyFn, dir = 1, idFn = (r) => r.id) {
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0)
  const blank = (k) => k === null || k === undefined || k === ''
  return [...(rows || [])].sort((a, b) => {
    const ka = keyFn(a), kb = keyFn(b)
    if (blank(ka) !== blank(kb)) return blank(ka) ? 1 : -1
    return (blank(ka) ? 0 : dir * cmp(ka, kb)) || cmp(String(idFn(a)), String(idFn(b)))
  })
}

/* ── Exceptions ───────────────────────────────────────────────────────────── */
const EXC_STATUS = { open: 'Open', assigned: 'Assigned', in_progress: 'In progress',
  escalated: 'Escalated to owner', resolved: 'Resolved', unable_to_verify: 'Unable to verify',
  needs_more_info: 'Needs more info' }
/* Operator wording for an exception status; an unrecognised one reads "Status unknown", not the raw enum. */
export function exceptionStatusLabel(s) {
  return EXC_STATUS[s] || 'Status unknown'
}
/* Anything but Complete needs a note; say so before the round trip. */
export function resolveBlockedReason(outcome, note) {
  if (outcome === 'complete') return ''
  return String(note ?? '').trim() ? '' : 'Add a note first — it is required for this outcome.'
}
/* The sweep result: an unreadable response is not "nothing new". */
export function sweepSummary(r) {
  const raised = r && typeof r.raised === 'object' && r.raised ? r.raised : null
  if (!raised) return 'The check finished, but the result was not readable. Refresh the list to see what is there.'
  const n = Object.values(raised).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  return n ? `${n} new exception${n === 1 ? '' : 's'} raised.` : 'Nothing new — the queue is up to date.'
}

/* ── Funding partners ─────────────────────────────────────────────────────── */
const PARTNER_NUM = [['min_loan', 'Min loan', 0, null], ['max_loan', 'Max loan', 0, null],
  ['max_ltv_pct', 'Max LTV %', 0, 100], ['max_ltc_pct', 'Max LTC %', 0, 100],
  ['min_credit_score', 'Min credit score', 300, 850], ['typical_close_days', 'Typical close (days)', 0, null]]
const hasText = (v) => String(v ?? '').trim() !== ''
/* Field-level problems for the partner form: { field: message }. Blank numbers are fine (sent as
 * null); text that is not a number is rejected, not silently saved as NaN. */
export function validatePartner(f) {
  const errs = {}
  if (!hasText(f.name)) errs.name = 'Enter the company or name.'
  for (const [k, label, lo, hi] of PARTNER_NUM) {
    if (!hasText(f[k])) continue
    const n = Number(String(f[k]).trim())
    if (!Number.isFinite(n)) errs[k] = `${label} must be a number.`
    else if (n < lo || (hi !== null && n > hi))
      errs[k] = hi === null ? `${label} cannot be below ${lo}.` : `${label} must be between ${lo} and ${hi}.`
  }
  if (!errs.min_loan && !errs.max_loan && hasText(f.min_loan) && hasText(f.max_loan) && Number(f.min_loan) > Number(f.max_loan))
    errs.max_loan = 'Max loan is below min loan.'
  return errs
}
/* Why Save is unavailable, or ''. */
export function partnerSaveBlockedReason(f, busy) {
  if (busy) return 'A save is already in progress.'
  return Object.values(validatePartner(f))[0] || ''
}
/* Track record, honestly: no record object = unknown; zero submissions = none sent. */
export function trackRecordText(t) {
  if (!t || typeof t !== 'object') return 'Track record not available'
  if (!t.deals_submitted) return 'no deals sent yet'
  return `${knownCount(t.deals_submitted)} sent · ${knownCount(t.approvals)} approved · ${knownCount(t.funded)} funded · ${knownCount(t.declines)} declined`
}

/* ── Command: per-deal rooms ──────────────────────────────────────────────────
 * rooms[id] is a room object (loaded), null (that fetch failed), or absent (not yet loaded). */
export function roomsLoadState(rooms, ids) {
  let failed = 0, pending = 0
  for (const id of ids) {
    if (!(id in rooms)) pending++
    else if (rooms[id] === null) failed++
  }
  return { failed, pending, loaded: ids.length - failed - pending, complete: failed === 0 && pending === 0 }
}
/* Total across rooms; unknown ('—') when any room failed or is pending, never a partial sum
 * shown as a total. No deals is a genuine 0. */
export function roomTotal(rooms, ids, countFn) {
  if (!ids.length) return 0
  if (!roomsLoadState(rooms, ids).complete) return '—'
  return ids.reduce((n, id) => n + countFn(rooms[id]), 0)
}
export function roomIdsToLoad(deals, max = 10) {
  return (deals || []).slice(0, max).map((d) => d.deal_id)
}

/* ── Settings / Operations ────────────────────────────────────────────────────
 * Why a pilot save is unavailable, or ''. A strategy the server could not list
 * must not be silently dropped from a pilot save. */
export function pilotSaveBlockedReason(strategyView, busy) {
  if (busy) return 'A save is already in progress.'
  if (strategyView === 'loading') return 'Strategies are still loading.'
  if (strategyView === 'error' || strategyView === 'stale')
    return 'The strategy list could not be loaded, so the linked strategy cannot be confirmed. Load it again first.'
  return ''
}
/* Options for a select backed by a list that may be missing or short: the id that is
 * currently saved is always present, so the select never shows "none" for a real link. */
export function withCurrentOption(items, currentId, label) {
  const list = Array.isArray(items) ? items : []
  if (currentId && !list.some((x) => String(x.id) === String(currentId)))
    return [{ id: currentId, name: label, status: 'unknown', unavailable: true }, ...list]
  return list
}
/* Distribution notice wording: unknown is stated, never read as "OFF". */
export function distributionLabel(d) {
  if (!d) return 'Automatic distribution status unavailable.'
  return `Automatic distribution: ${d.auto_distribution ? 'ON' : 'OFF'}.`
}
/* SMS readiness: only a loaded status may say "Ready"; unknown is not "Not sending". */
export function smsReadinessLabel(view, status) {
  if (!status) return view === 'loading' ? 'Checking…' : 'Status unavailable'
  return status.can_send ? 'Ready to send to opted-in sellers' : 'Not sending'
}
