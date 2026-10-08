// Pure helpers for the Control Room launch board (portfolio) UI.
// Evidence and status only: no percentages, no guessed "Working", no invented completion.

export const LANES = ['active', 'backlog', 'archived']
export const LANE_LABELS = { active: 'Active', backlog: 'Backlog', archived: 'Archived' }
export const EVIDENCE_KINDS = ['source', 'test', 'deployment', 'verification']
export const EVIDENCE_STATES = ['none', 'claimed', 'verified']
export const BOARD_URL = '/god/launch-board'

export function failedBoard(reason) {
  return { ok: false, reason: reason || 'Launch board unavailable', lanes: { active: [], backlog: [], archived: [] }, queue: [], summary: null, storage: null }
}

/** A reply is only a board if it has all three lanes as arrays; anything else is a failure. */
export function normalizeBoard(raw) {
  const lanes = raw && typeof raw === 'object' ? raw.lanes : null
  if (!lanes || !LANES.every(l => Array.isArray(lanes[l])) || !Array.isArray(raw.queue)) {
    return failedBoard('Launch board reply was malformed')
  }
  return { ok: true, reason: '', lanes, queue: raw.queue, summary: raw.summary || null, storage: raw.storage || null }
}

/** User-facing text for a failed call; never a success message. */
export function errorText(e) {
  const d = e && (e.detail || e.message)
  return typeof d === 'string' && d ? d : 'Request failed'
}

export function laneCounts(board) {
  return LANES.reduce((o, l) => ({ ...o, [l]: (board.lanes[l] || []).length }), {})
}

/** Launch gates: one per evidence kind. A gate is met only when verified WITH a ref. */
export function launchGates(project) {
  const ev = (project && project.evidence) || {}
  return EVIDENCE_KINDS.map(kind => {
    const e = ev[kind] || { state: 'none', ref: null }
    return { kind, state: e.state, ref: e.ref || null, met: e.state === 'verified' && !!e.ref }
  })
}

export function gatesSummary(project) {
  const g = launchGates(project)
  const met = g.filter(x => x.met).length
  return { met, total: g.length, allMet: met === g.length, missing: g.filter(x => !x.met).map(x => x.kind) }
}

/** Live worker status and last completed task are two separate facts. */
export function statusLines(project) {
  const lc = project && project.last_completed
  return {
    working: project && project.working_status ? project.working_status : 'no live evidence',
    lastCompleted: lc ? `${lc.summary || '(no summary)'} @ ${lc.at}` : 'nothing completed yet',
  }
}

/** Live status comes only from fresh relay evidence naming this project; else "no live evidence". */
export function liveStatusFor(project, relayState, working) {
  const w = relayState && relayState.worker
  return working && w && w.project && w.project === project.name ? (w.display || 'Working') : 'no live evidence'
}

export function productText(project) {
  const done = project && project.product_state === 'complete'
  const tasks = (project && project.tasks_completed) || 0
  return done ? 'Product complete (human-marked, all evidence verified)'
    : `${tasks} task${tasks === 1 ? '' : 's'} done — product not complete`
}

/** Which lane moves a project offers. Activation needs approval (the server enforces it too). */
export function laneActions(project) {
  const a = []
  if (project.lane !== 'active' && project.approved) a.push('active')
  if (project.lane !== 'backlog') a.push('backlog')
  if (project.lane !== 'archived') a.push('archived')
  return a
}

export function parsePriority(text) {
  const n = Number(String(text).trim())
  return Number.isInteger(n) && n >= 1 ? n : null
}

/** Build the request body for an action; returns null when the input is not valid. */
export function buildAction(action, input = {}) {
  switch (action) {
    case 'lane': return LANES.includes(input.lane) ? { lane: input.lane } : null
    case 'priority': { const p = parsePriority(input.priority); return p ? { priority: p } : null }
    case 'approve': case 'revoke': return {}
    case 'evidence': {
      const ref = String(input.ref || '').trim()
      if (!EVIDENCE_KINDS.includes(input.kind) || !EVIDENCE_STATES.includes(input.state)) return null
      if (input.state !== 'none' && !ref) return null
      return { kind: input.kind, state: input.state, ref }
    }
    case 'task': return String(input.summary || '').trim() ? { summary: input.summary.trim() } : null
    case 'product': return { complete: !!input.complete }
    default: return null
  }
}

// ── Write protection (expected_version + Idempotency-Key) ─────────────────────
// Every write carries the version of the project as the page last loaded it, so a
// change made elsewhere since then is refused (409) instead of silently overwritten,
// and a unique Idempotency-Key, so a double click or a retried request is applied
// once. A key is minted per user action and reused only for that action's retry.
let _keySeq = 0
export function newRequestKey(now = Date.now(), rand = Math.random) {
  _keySeq += 1
  const r = Math.floor(rand() * 1e9).toString(36)
  return `lb-${now.toString(36)}-${_keySeq}-${r}`.slice(0, 120)
}

/** Adds expected_version when the project carries a version (DB store). */
export function withExpectedVersion(body, project) {
  if (!body) return body
  const v = project && Number.isInteger(project.version) ? project.version : null
  return v == null ? body : { ...body, expected_version: v }
}

export function writeHeaders(key) {
  return { headers: { 'Idempotency-Key': key } }
}

/** A 409 means the board moved on: reload, never claim success. */
export function isStale(e) {
  return !!e && (e.status === 409 || e.statusCode === 409)
}
