/* SAVED ON THE PHONE FIRST (build list #28).
 *
 * A rep is in a kitchen with one bar, or out past the last tower. Anything
 * they save in the field app - visit notes, walkthrough photos, door knocks,
 * how a visit went - is tried right away; when there is no signal (or the
 * answer does not come back in time) it is kept on the phone (IndexedDB) and
 * sent by itself when signal returns: when the phone says it is back online,
 * when the app is opened again, and every 30 seconds.
 *
 * Sent in the order it was saved. A change that may arrive twice is safe:
 * visit notes replace the whole record, the server ignores a photo it already
 * has, and a visit result carries the phone's own id (client_ref).
 *
 * Typing is also kept as a draft (localStorage) while the rep types, so a
 * closed app or a dead battery does not lose half-written notes, and the last
 * screens seen are kept for reading with no signal (getKept). */
import { api } from '../../../api/client'
import { shrinkImageFile } from '../../../api/shrinkImages'

const DB = 'evosys-field'
const STORE = 'outbox'
const WAIT_MS = { json: 20000, photo: 90000 }
const READ_WAIT_MS = 15000

export function newRef() {
  try { if (crypto?.randomUUID) return crypto.randomUUID() } catch { /* older phones */ }
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}

// ── the phone's store ───────────────────────────────────────────────────────
let dbp = null
function idb() {
  if (!dbp) {
    dbp = new Promise((resolve, reject) => {
      if (typeof indexedDB === 'undefined') { reject(new Error('no store')); return }
      const r = indexedDB.open(DB, 1)
      r.onupgradeneeded = () => { r.result.createObjectStore(STORE, { keyPath: 'key' }) }
      r.onsuccess = () => resolve(r.result)
      r.onerror = () => reject(r.error)
    }).catch((e) => { dbp = null; throw e })
  }
  return dbp
}
function run(mode, fn) {
  return idb().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode)
    const r = fn(t.objectStore(STORE))
    t.oncomplete = () => resolve(r ? r.result : undefined)
    t.onerror = () => reject(t.error || new Error('store'))
    t.onabort = () => reject(t.error || new Error('store'))
  }))
}
export const all = () => run('readonly', (s) => s.getAll()).then((xs) => (xs || []).sort((a, b) => a.at - b.at))
const get = (key) => run('readonly', (s) => s.get(key))
const put = (op) => run('readwrite', (s) => s.put(op))
const del = (key) => run('readwrite', (s) => s.delete(key))

// ── who is watching ─────────────────────────────────────────────────────────
const subs = new Set()
export function subscribe(fn) { subs.add(fn); return () => { subs.delete(fn) } }
async function changed() {
  let xs = []
  try { xs = await all() } catch { /* no store */ }
  subs.forEach((f) => { try { f(xs) } catch { /* a closed screen */ } })
}

// ── no signal, or no answer in time ─────────────────────────────────────────
export const noSignal = (e) => !!e && (e.kind === 'network' || e.kind === 'timeout')
const later = (e) => noSignal(e) || e?.status === 401 || e?.status === 408 || e?.status === 429 || (e?.status >= 500)

function inTime(p, ms) {
  let t
  const timer = new Promise((_, reject) => {
    t = setTimeout(() => { const e = new Error('No answer in time - weak signal.'); e.kind = 'timeout'; reject(e) }, ms)
  })
  return Promise.race([p, timer]).finally(() => clearTimeout(t))
}

function send(op) {
  if (op.kind === 'photo') {
    const fd = new FormData()
    fd.append('file', new File([op.blob], op.name || 'photo.jpg', { type: op.blob.type || 'image/jpeg' }), op.name || 'photo.jpg')
    if (op.room) fd.append('room', op.room)
    return inTime(api.upload(op.path, fd), WAIT_MS.photo)
  }
  return inTime(api[op.method.toLowerCase()](op.path, op.body), WAIT_MS.json)
}

async function keep(op) {
  try { await put(op) } catch {
    const e = new Error('No signal, and this phone could not keep a copy. Try again when you have signal.')
    throw e
  }
  changed()
}

/* Try now; with no signal keep it on the phone. Returns
 *   { queued: false, result }  - the server has it
 *   { queued: true }           - on the phone, it sends by itself
 * and throws only for a real refusal (the server said no) or when the phone
 * could not keep a copy.
 * op: { key, kind: 'json'|'photo', method, path, body, merge, file, room, label, appt, draftKey } */
export async function saveOrKeep(input) {
  const op = { ...input, kind: input.kind || 'json', at: Date.now() }
  if (op.kind === 'photo') {
    op.blob = await shrinkImageFile(op.file)
    op.name = op.blob.name || op.file?.name || 'photo.jpg'
    delete op.file
  } else if (op.merge) {
    // Two taps on the same door while out of signal: one change with both.
    const prev = await get(op.key).catch(() => null)
    if (prev && !prev.error) op.body = { ...prev.body, ...op.body }
  }
  let waiting = []
  try { waiting = await all() } catch { /* no store: just try */ }
  // Older work goes first, so a newer save never lands before an older one.
  const behind = waiting.some((w) => !w.error && w.key !== op.key)
  if (behind || (typeof navigator !== 'undefined' && navigator.onLine === false)) {
    await keep(op)
    flush()
    return { queued: true }
  }
  try {
    const result = await send(op)
    if (waiting.some((w) => w.key === op.key)) { await del(op.key).catch(() => {}); changed() }
    return { queued: false, result }
  } catch (e) {
    if (!later(e)) throw e
    await keep(op)
    return { queued: true }
  }
}

let flushing = null
export function flush() {
  if (flushing) return flushing
  flushing = (async () => {
    try {
      for (const op of await all()) {
        if (op.error) continue
        try {
          await send(op)
          await del(op.key)
          if (op.draftKey) dropDraft(op.draftKey)
          changed()
        } catch (e) {
          if (later(e)) break                       // still no signal: try again later
          await put({ ...op, error: e.message || 'The server did not take it.' })
          changed()
        }
      }
    } catch { /* no store */ } finally { flushing = null }
  })()
  return flushing
}

export async function retry(key) {
  const op = await get(key).catch(() => null)
  if (op) { delete op.error; await put(op); changed(); flush() }
}
export async function discard(key) { await del(key).catch(() => {}); changed() }

let started = false
export function startOutbox() {
  if (started || typeof window === 'undefined') return
  started = true
  window.addEventListener('online', () => flush())
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') flush() })
  setInterval(() => { if (navigator.onLine !== false) flush() }, 30000)
  flush()
}

// ── drafts: what the rep is typing, kept as they type ───────────────────────
const DRAFT = 'evosys-draft:'
export function readDraft(k) {
  try { const s = localStorage.getItem(DRAFT + k); return s ? JSON.parse(s) : null } catch { return null }
}
export function writeDraft(k, v) {
  try { localStorage.setItem(DRAFT + k, JSON.stringify({ ...v, at: Date.now() })) } catch { /* full or blocked */ }
}
export function dropDraft(k) {
  try { localStorage.removeItem(DRAFT + k) } catch { /* blocked */ }
}

// ── the last screens seen, for reading with no signal ───────────────────────
const KEPT = 'evosys-kept:'
export async function getKept(path) {
  try {
    const r = await inTime(api.get(path), READ_WAIT_MS)
    try { localStorage.setItem(KEPT + path, JSON.stringify(r)) } catch { /* full */ }
    return r
  } catch (e) {
    if (!noSignal(e)) throw e
    let c = null
    try { c = JSON.parse(localStorage.getItem(KEPT + path) || 'null') } catch { /* blocked */ }
    if (c) return { ...c, _offline: true }
    throw e
  }
}
// Signing out forgets these: api/client.js clearToken().
