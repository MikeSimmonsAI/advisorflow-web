/**
 * DRAFT SAVER - nothing an inspector types is lost on a bad connection.
 *
 * Every change is queued, mirrored to the phone's storage, and sent in the
 * background (one request at a time). A change leaves the queue ONLY when the
 * server has confirmed it. If a save fails, its changes go back into the
 * queue (newer typing wins over the failed copy) and are retried with
 * back-off, when the phone comes back online, and on the next change. After
 * a refresh, a crash or a closed tab, the copy kept on the phone is put back
 * in the queue and sent. "Saved" is only ever shown when the queue is empty
 * and nothing is in flight, and `settle()` tells the caller whether that is
 * true - completion waits for it.
 *
 * Pure (no React) so it can be tested with injected fakes.
 */
const RETRY_MS = [2000, 5000, 10000, 20000, 30000]

export function createDraftSaver({ id, patch, storage, onState, onSaved, delay = 700,
                                   schedule = (fn, ms) => setTimeout(fn, ms), cancel = (h) => clearTimeout(h) }) {
  const key = `rf-insp-draft:${id}`
  let pending = {}
  let sending = null      // the body of the request in flight - unconfirmed until the server answers
  let inflight = null
  let timer = null
  let attempt = 0
  let state = 'saved'
  let lastError = null

  const setState = (s, err) => { state = s; lastError = err || null; onState && onState(s, { error: lastError, pendingKeys: Object.keys(unconfirmed()) }) }
  // THE RECOVERY COPY IS EVERYTHING NOT YET CONFIRMED: the request in flight
  // AND whatever was typed after it, newer values winning. Closing the tab
  // mid-request therefore loses neither (PR #285 review).
  const unconfirmed = () => ({ ...(sending || {}), ...pending })
  const persist = () => {
    try {
      const all = unconfirmed()
      if (Object.keys(all).length) storage && storage.setItem(key, JSON.stringify({ fields: all, at: Date.now() }))
      else storage && storage.removeItem(key)
    } catch { /* storage full or blocked: the in-memory queue still holds it */ }
  }
  const arm = (ms) => { if (timer) cancel(timer); timer = schedule(() => { timer = null; return flush() }, ms) }

  function set(field, value) {
    pending = { ...pending, [field]: value }
    persist()
    if (state !== 'saving') setState('pending')
    arm(delay)
  }

  async function flush() {
    if (inflight) {                         // one request at a time; whatever was typed meanwhile goes next
      await inflight.catch(() => {})
      if (!Object.keys(pending).length) return state !== 'error'
    }
    const body = pending
    if (!Object.keys(body).length) { if (state !== 'error') setState('saved'); return state !== 'error' }
    pending = {}
    sending = body
    persist()
    setState('saving')
    inflight = Promise.resolve().then(() => patch(body))
    try {
      const doc = await inflight
      inflight = null
      sending = null                        // confirmed: only now does it leave the recovery copy
      attempt = 0
      onSaved && onSaved(doc, Object.keys(body), { ...pending })
      persist()
      if (Object.keys(pending).length) { setState('pending'); arm(delay) } else setState('saved')
      return true
    } catch (err) {
      inflight = null
      sending = null
      pending = { ...body, ...pending }     // newer typing wins over the failed copy
      persist()
      setState('error', err)
      if (err && (err.status === 400 || err.status === 403 || err.status === 409 || err.status === 422)) {
        return false                        // the server refused the value itself; retrying won't change that
      }
      arm(RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)])
      attempt += 1
      return false
    }
  }

  /** Send everything now. True only when the server holds every change. */
  async function settle(tries = 3) {
    if (timer) { cancel(timer); timer = null }
    for (let i = 0; i < tries; i += 1) {
      const ok = await flush()
      if (ok && !Object.keys(pending).length && !inflight) return true
      if (!ok && lastError && [400, 403, 409, 422].includes(lastError.status)) return false
    }
    return !Object.keys(pending).length && !inflight && state !== 'error'
  }

  /** Put back what this phone still holds from before a refresh / crash. */
  function restore() {
    try {
      const raw = storage && storage.getItem(key)
      if (!raw) return null
      const saved = JSON.parse(raw)
      if (!saved || !saved.fields || !Object.keys(saved.fields).length) return null
      pending = { ...saved.fields, ...pending }
      setState('pending')
      arm(0)
      return saved.fields
    } catch { return null }
  }

  function discard() { pending = {}; sending = null; persist() }   // the report was completed elsewhere
  function hasPending() { return Object.keys(pending).length > 0 || !!inflight || !!sending }
  function dispose() { if (timer) cancel(timer); timer = null }
  return { set, flush, settle, restore, discard, hasPending, dispose, get state() { return state }, get pending() { return { ...pending } }, get unconfirmed() { return unconfirmed() } }
}
