// Pure helpers for the Control Room (no React) so they can be tested with node.

export const POLL_MS = 20000

// Status -> visual tone. Four visibly different states; Needs Mike is the loudest.
export const TONES = {
  Working:           { key: 'working',  label: 'Working',         bg: '#eff6ff', fg: '#1d4ed8', bd: '#93c5fd' },
  Complete:          { key: 'complete', label: 'Complete',        bg: '#ecfdf5', fg: '#047857', bd: '#6ee7b7' },
  Blocked:           { key: 'blocked',  label: 'Blocked',         bg: '#fff7ed', fg: '#c2410c', bd: '#fdba74' },
  'Approval Needed': { key: 'needs',    label: 'Approval Needed', bg: '#fef2f2', fg: '#b91c1c', bd: '#f87171' },
  Idle:              { key: 'idle',     label: 'Idle',            bg: '#f3f4f6', fg: '#4b5563', bd: '#d1d5db' },
}

export const toneFor = (status) => TONES[status] || TONES.Idle

// The NEEDS MIKE panel exists only when the server reports a true approval gate.
export const showNeedsMike = (data) => !!(data && data.needs_mike && data.needs_mike.decision)

export const KIND_LABEL = {
  mike_direction: 'Mike', chatgpt_directive: 'ChatGPT', next_directive: 'ChatGPT',
  github_accepted: 'GitHub', claude_started: 'Claude', claude_progress: 'Claude',
  claude_complete: 'Claude', chatgpt_review: 'ChatGPT', approval_gate: 'Needs Mike',
}

// Newest first for reading; the server order (oldest first) is the audit order.
export const timelineNewestFirst = (events) => [...(events || [])].reverse()

// Duplicate-submission guard: same text+mode while a send is in flight or just done.
export function makeSubmitGuard() {
  let inFlight = false
  let last = ''
  return {
    begin(text, mode) {
      const sig = mode + '|' + text.trim().toLowerCase().replace(/\s+/g, ' ')
      if (inFlight || !text.trim() || sig === last) return false
      inFlight = true
      last = sig
      return true
    },
    end(ok) { inFlight = false; if (!ok) last = '' },
  }
}

export const directionDisabledReason = (data) =>
  data && data.give_direction && data.give_direction.setup_required
    ? (data.give_direction.message || 'SETUP REQUIRED') : null

export function errorMessage(e) {
  const d = e && e.detail
  return (d && d.message) || (e && e.message) || 'Something went wrong. Nothing was sent.'
}

// ── Home summary ────────────────────────────────────────────────────────────

export function homeCards(home) {
  const h = home || {}
  const n = (o) => (o && o.count) || 0
  const running = h.working_now && h.working_now.text
  const pkg = h.overnight && h.overnight.status && h.overnight.status !== 'None' ? h.overnight.status : null
  return [
    { key: 'working', label: 'Working now', value: running || 'Nothing running', tone: running ? 'working' : 'idle' },
    { key: 'completed', label: 'Completed today', value: String(n(h.completed_today)), sub: h.completed_today && h.completed_today.latest, tone: 'complete' },
    { key: 'suggested', label: 'Suggested next', value: String(n(h.suggested_next)), sub: h.suggested_next && h.suggested_next.top, tone: 'idle' },
    { key: 'overnight', label: 'Overnight package', value: pkg || 'No package', sub: h.overnight && h.overnight.name, tone: pkg === 'Needs Mike' ? 'needs' : 'idle' },
    { key: 'needs', label: 'Needs Mike', value: String(n(h.needs_mike)), sub: h.needs_mike && h.needs_mike.decision, tone: n(h.needs_mike) ? 'needs' : 'idle' },
  ]
}

// ── Completed Work filters (the server marks each item today / last7 in Central Time) ──

export const COMPLETED_FILTERS = [
  { id: 'today', label: 'Today' }, { id: '7d', label: '7 days' }, { id: 'all', label: 'All' },
]

export function filterCompleted(items, filter) {
  const list = items || []
  if (filter === 'today') return list.filter((i) => i.today)
  if (filter === '7d') return list.filter((i) => i.last7)
  return list
}

// ── Suggested Next: dismissals are a local preference, never an action ─────

const DISMISS_KEY = 'controlRoom.dismissed'
const PACKAGE_KEY = 'controlRoom.packageDraft'

function readJson(storage, key, fallback) {
  try { const v = JSON.parse(storage.getItem(key)); return v == null ? fallback : v } catch { return fallback }
}
const writeJson = (storage, key, v) => { try { storage.setItem(key, JSON.stringify(v)) } catch { /* private mode */ } }

export const loadDismissed = (storage) => readJson(storage, DISMISS_KEY, [])
export function dismissSuggestion(storage, id) {
  const next = Array.from(new Set([...loadDismissed(storage), id]))
  writeJson(storage, DISMISS_KEY, next)
  return next
}
export const visibleSuggestions = (list, dismissed) => (list || []).filter((s) => !(dismissed || []).includes(s.id))

// A suggestion that needs Mike cannot be queued or packaged: only Mike can answer
// that gate, so it never turns into work for Claude.
export const suggestionActions = (s, directionBlocked) => ({
  canPackage: !s.needs_mike,
  canQueue: !s.needs_mike && !directionBlocked,
  canDismiss: true,
})

// ── Overnight Package builder (pure; the list lives in the browser until Start) ──

export const MAX_OBJECTIVES = 12
export const MAX_OBJECTIVE_CHARS = 300
export const emptyPackage = () => ({ name: '', objectives: [] })

export function addObjective(pkg, text, source = 'custom') {
  const t = (text || '').trim()
  if (!t || t.length > MAX_OBJECTIVE_CHARS || pkg.objectives.length >= MAX_OBJECTIVES) return pkg
  if (pkg.objectives.some((o) => o.text.toLowerCase() === t.toLowerCase())) return pkg
  return { ...pkg, objectives: [...pkg.objectives, { text: t, source }] }
}

export function moveObjective(pkg, index, delta) {
  const to = index + delta
  if (index < 0 || index >= pkg.objectives.length || to < 0 || to >= pkg.objectives.length) return pkg
  const list = [...pkg.objectives]
  const [item] = list.splice(index, 1)
  list.splice(to, 0, item)
  return { ...pkg, objectives: list }
}

export const removeObjective = (pkg, index) =>
  ({ ...pkg, objectives: pkg.objectives.filter((_, i) => i !== index) })

export const renamePackage = (pkg, name) => ({ ...pkg, name: (name || '').slice(0, 80) })

export const savePackageDraft = (storage, pkg) => writeJson(storage, PACKAGE_KEY, pkg)
export function loadPackageDraft(storage) {
  const p = readJson(storage, PACKAGE_KEY, null)
  return p && Array.isArray(p.objectives)
    ? { name: String(p.name || ''), objectives: p.objectives.filter((o) => o && o.text) }
    : emptyPackage()
}

// Draft until it has a name and an objective, then Ready. Once started, the
// server's status (Running, Completed, Blocked, Needs Mike) wins.
export function packageState(pkg, serverStatus) {
  if (serverStatus && serverStatus !== 'None') return serverStatus
  return pkg.name.trim() && pkg.objectives.length ? 'Ready' : 'Draft'
}

export const canStartPackage = (pkg, directionBlocked) =>
  !directionBlocked && !!pkg.name.trim() && pkg.objectives.length > 0

// Order only. No durations: nothing here can honestly promise how long a step takes.
export function packageSequence(pkg, safety) {
  const items = (safety && safety.items) || []
  return pkg.objectives.map((o, i) => {
    const s = items[i]
    return { step: i + 1, text: o.text, gate: !!(s && s.gate), gate_reasons: (s && s.gate_reasons) || [] }
  })
}

export const objectivesForServer = (pkg) => pkg.objectives.map((o) => o.text)

// ── Monitoring setup states ─────────────────────────────────────────────────

export function monitoringView(m) {
  if (!m || m.state === 'ok') return null
  const setup = m.state === 'setup_required'
  return { kind: setup ? 'setup' : 'degraded',
           title: setup ? 'Setup required: relay monitoring' : 'Relay monitoring is delayed',
           message: m.message, tokenSet: !!m.read_credential_configured }
}

// ── Platform STAGING banner ─────────────────────────────────────────────────

// Shown only when the BACKEND says staging (same source as the demo banner),
// never from the hostname. Production and unknown answers render nothing.
export function stagingBanner(env) {
  if (!env || env.environment !== 'staging') return null
  return { label: 'STAGING / QA', text: 'Test environment. Not production. No real SCI customers or messages.' }
}
