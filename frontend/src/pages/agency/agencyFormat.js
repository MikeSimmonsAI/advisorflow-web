/**
 * MAX LIFE COMMAND — pure presentation helpers (no React, no fetch).
 *
 * Everything here is deterministic so it can run under plain node
 * (tests/frontend/agencyFormat.test.mjs). The SERVER owns every number and
 * every state; these functions only name, order and route what it returned.
 */

export const NEED_LABELS = {
  family_protection: 'Family Protection',
  living_benefits: 'Living Benefits',
  retirement: 'Retirement',
  business_owner: 'Business Owner',
  final_expense: 'Final Expense',
  mortgage_protection: 'Mortgage Protection',
}

export const APP_STATUS_LABELS = {
  draft: 'Draft',
  prepared: 'Application prepared',
  submitted: 'Submitted',
  requirements_requested: 'Requirements requested',
  awaiting_client: 'Awaiting client',
  awaiting_agent: 'Awaiting agent',
  underwriting: 'Underwriting',
  approved: 'Approved',
  modified: 'Modified',
  declined: 'Declined',
  withdrawn: 'Withdrawn',
  issued: 'Issued',
}

export const ASSIGNMENT_LABELS = {
  unassigned: 'Unassigned',
  offered: 'Offered',
  accepted: 'Accepted',
  declined: 'Declined',
  timed_out: 'Timed out',
  escalated: 'Escalated',
}

/** snake_case → "Title Case", with known vocabularies first. */
export function humanize(key) {
  if (key === null || key === undefined || key === '') return ''
  const k = String(key)
  if (NEED_LABELS[k]) return NEED_LABELS[k]
  if (APP_STATUS_LABELS[k]) return APP_STATUS_LABELS[k]
  if (ASSIGNMENT_LABELS[k]) return ASSIGNMENT_LABELS[k]
  return k.replace(/_/g, ' ').replace(/\s+/g, ' ').trim().replace(/\b\w/g, c => c.toUpperCase())
}

/** Tone for a pill: 'gold' | 'green' | 'red' | 'amber' | 'muted'. */
export function toneFor(value) {
  const v = String(value || '').toLowerCase()
  if (['high', 'escalated', 'timed_out', 'declined', 'missed', 'lapsed', 'stalled', 'overdue'].includes(v)) return 'red'
  if (['medium', 'offered', 'pending', 'awaiting_client', 'awaiting_agent', 'requirements_requested', 'underwriting', 'in_progress'].includes(v)) return 'amber'
  if (['accepted', 'confirmed', 'completed', 'issued', 'approved', 'in_force', 'done', 'active_agent'].includes(v)) return 'green'
  if (['low', 'withdrawn', 'cancelled', 'unassigned'].includes(v)) return 'muted'
  return 'gold'
}

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 }

/** Attention items, most severe first; stable within a severity. */
export function sortAttention(items) {
  return (Array.isArray(items) ? items : [])
    .map((it, i) => [it, i])
    .sort((a, b) => ((SEVERITY_ORDER[a[0].severity] ?? 3) - (SEVERITY_ORDER[b[0].severity] ?? 3)) || (a[1] - b[1]))
    .map(([it]) => it)
}

/**
 * Where a server link opens in the app.
 *
 * The backend returns paths in the shape the FRONTEND routes use
 * (/agency/prospects/{id}, /agency/agents/{user_id} …) and, for summary
 * counts, the exact API list path with its filter (/agency/prospects?intent=high).
 * Both are served by an /agency/* route here, so the path is used as-is —
 * but anything that is not under /agency/ is refused rather than followed.
 */
export function routeForLink(link) {
  const path = typeof link === 'string' ? link : link && link.path
  if (!path || typeof path !== 'string') return null
  if (!path.startsWith('/agency/')) return null
  if (path.startsWith('/agency/attention')) return '/agency'
  return path
}

/** Query string for an API list call from the page's URL params (whitelisted). */
export function apiQuery(searchParams, allowed, extra = {}) {
  const p = new URLSearchParams()
  const get = (k) => (searchParams && typeof searchParams.get === 'function' ? searchParams.get(k) : searchParams?.[k])
  for (const k of allowed) {
    const v = get(k)
    if (v !== null && v !== undefined && v !== '') p.set(k, v)
  }
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** Human label for the active filters on a list (so a drilled count says what it is). */
export function filterLabel(searchParams, allowed) {
  const get = (k) => (searchParams && typeof searchParams.get === 'function' ? searchParams.get(k) : searchParams?.[k])
  const parts = []
  for (const k of allowed) {
    const v = get(k)
    if (v === null || v === undefined || v === '') continue
    if (v === 'true') parts.push(humanize(k))
    else parts.push(`${humanize(k)}: ${humanize(v)}`)
  }
  return parts.join(' · ')
}

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** "4h ago" style; `now` injectable for tests. */
export function relTime(iso, now = Date.now()) {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const s = Math.round((now - t) / 1000)
  const fut = s < 0
  const a = Math.abs(s)
  let out
  if (a < 60) out = 'just now'
  else if (a < 3600) out = `${Math.floor(a / 60)}m`
  else if (a < 86400) out = `${Math.floor(a / 3600)}h`
  else out = `${Math.floor(a / 86400)}d`
  if (out === 'just now') return out
  return fut ? `in ${out}` : `${out} ago`
}

/** A metric the server could not compute stays visibly unavailable. */
export function metric(value, suffix = '') {
  if (value === null || value === undefined) return 'Not yet available'
  return `${value}${suffix}`
}

/** Render any fact value (string, number, bool, list, object) as text. */
export function factText(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.length ? value.map(v => (typeof v === 'string' ? humanize(v) : factText(v))).join(', ') : '—'
  if (typeof value === 'object') {
    const parts = Object.entries(value).map(([k, v]) => {
      if (v === true) return humanize(k)
      if (v === false) return `No ${humanize(k).toLowerCase()}`
      return `${humanize(k)}: ${factText(v)}`
    })
    return parts.length ? parts.join(' · ') : '—'
  }
  return String(value)
}

/** Workload bar width clamp. */
export function pctWidth(p) {
  const n = Number(p)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, n))
}

/** Group records by a key in a given order; unknown keys appended (never hidden). */
export function groupByStage(items, stages, key = 'stage') {
  const order = Array.isArray(stages) ? stages.slice() : []
  const groups = new Map(order.map(s => [s, []]))
  for (const it of Array.isArray(items) ? items : []) {
    const s = it[key]
    if (!groups.has(s)) groups.set(s, [])
    groups.get(s).push(it)
  }
  return [...groups.entries()].map(([stage, rows]) => ({ stage, items: rows }))
}

/** Error message from a thrown api error. */
export function errText(e, fallback = 'Something went wrong') {
  if (!e) return fallback
  if (typeof e === 'string') return e
  return e.message || e.detail || fallback
}

// ── S8: forms, profile edits, paging ────────────────────────────────────────

/** Lines/commas → trimmed, de-duplicated list (order kept). */
export function splitList(text) {
  const seen = new Set()
  const out = []
  for (const raw of String(text || '').split(/[\n,]/)) {
    const v = raw.trim()
    if (v && !seen.has(v.toLowerCase())) { seen.add(v.toLowerCase()); out.push(v) }
  }
  return out
}

/** '' | 'yes' | 'no' → null | true | false (tri-state: "not stated" stays null). */
export function triState(v) {
  if (v === 'yes' || v === true) return true
  if (v === 'no' || v === false) return false
  return null
}

/** true/false/null → 'yes' | 'no' | '' for a <select>. */
export function triValue(v) {
  return v === true ? 'yes' : v === false ? 'no' : ''
}

const LIST_FIELDS = ['financial_goals', 'stated_concerns', 'need_categories']
const BOOL_FIELDS = ['retirement_interest', 'business_owner_interest', 'living_benefits_interest']

/** Profile → editor form state (strings only). */
export function profileForm(profile) {
  const p = profile || {}
  const hh = (p.household && typeof p.household === 'object') ? p.household : {}
  return {
    household_adults: hh.adults == null ? '' : String(hh.adults),
    household_children: hh.children == null ? '' : String(hh.children),
    household_notes: hh.notes || '',
    preferred_contact: p.preferred_contact || '',
    need_categories: Array.isArray(p.need_categories) ? p.need_categories.slice() : [],
    financial_goals: (p.financial_goals || []).join('\n'),
    stated_concerns: (p.stated_concerns || []).join('\n'),
    retirement_interest: triValue(p.retirement_interest),
    business_owner_interest: triValue(p.business_owner_interest),
    living_benefits_interest: triValue(p.living_benefits_interest),
  }
}

function sameJSON(a, b) { return JSON.stringify(a ?? null) === JSON.stringify(b ?? null) }

/**
 * Only the fields that CHANGED, in the server's shape. Blank = "not stated"
 * (null / []), never a guessed default. Household keys the editor does not
 * know about are kept as they were recorded.
 */
export function profilePatch(form, original) {
  const o = original || {}
  const out = {}
  const hhOrig = (o.household && typeof o.household === 'object') ? o.household : {}
  const hh = { ...hhOrig }
  const num = (s) => { const n = parseInt(String(s).trim(), 10); return Number.isFinite(n) && n >= 0 ? n : null }
  const setOrDrop = (k, v) => { if (v === null || v === '' || v === undefined) delete hh[k]; else hh[k] = v }
  setOrDrop('adults', String(form.household_adults ?? '').trim() === '' ? null : num(form.household_adults))
  setOrDrop('children', String(form.household_children ?? '').trim() === '' ? null : num(form.household_children))
  setOrDrop('notes', String(form.household_notes || '').trim() || null)
  const hhNext = Object.keys(hh).length ? hh : null
  if (!sameJSON(hhNext, o.household && Object.keys(o.household).length ? o.household : null)) out.household = hhNext
  const pc = form.preferred_contact || null
  if ((o.preferred_contact || null) !== pc) out.preferred_contact = pc
  const lists = {
    need_categories: Array.isArray(form.need_categories) ? form.need_categories : [],
    financial_goals: splitList(form.financial_goals),
    stated_concerns: splitList(form.stated_concerns),
  }
  for (const k of LIST_FIELDS) if (!sameJSON(lists[k], o[k] || [])) out[k] = lists[k]
  for (const k of BOOL_FIELDS) {
    const v = triState(form[k])
    if ((o[k] ?? null) !== v) out[k] = v
  }
  return out
}

/** <input type="datetime-local"> value (local time) → ISO-8601 UTC "Z", or null. */
export function localToIso(v) {
  if (!v) return null
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? null : d.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

/** Pager numbers from a {total, page, per_page} list answer. */
export function pageInfo(data) {
  const total = Math.max(0, Number(data?.total) || 0)
  const per = Math.max(1, Number(data?.per_page) || 50)
  const page = Math.max(1, Number(data?.page) || 1)
  const pages = Math.max(1, Math.ceil(total / per))
  const from = total ? (page - 1) * per + 1 : 0
  const to = Math.min(total, page * per)
  return { total, page, pages, from, to, hasPrev: page > 1, hasNext: page < pages }
}

/** Where an agency workspace lands: the Command Center, never the generic Overview. */
export function agencyLanding(isAgency, pathname) {
  if (!isAgency) return null
  const p = String(pathname || '/')
  if (p === '/' || /^\/workspace\/[^/]+\/?$/.test(p)) return '/agency'
  return null
}

/**
 * Who produced a brief / copilot output. Rules always compute the content; AI
 * may only rephrase it, and only a verifier-passed rephrase is labelled AI.
 * `ai.status` (not_requested | verified | rejected | unavailable | error)
 * explains why rules text is showing after an AI request.
 */
export function generatedByLabel(out) {
  const status = out && out.ai && out.ai.status
  if (out && out.generated_by === 'ai' && status === 'verified') {
    return { label: 'AI-assisted, verified', tone: 'gold', note: 'Rephrased by AI from the facts below; checked for new numbers, names, products and claims.' }
  }
  const why = {
    rejected: 'AI rephrase rejected by the verifier — showing the rules version.',
    unavailable: 'AI is not available here — showing the rules version.',
    error: 'AI request failed — showing the rules version.',
  }[status]
  return { label: 'Rules', tone: 'muted', note: why || null }
}

/** Ask EvoAI answer state: answered | empty | insufficient | unsupported. */
export function askState(answer) {
  if (!answer) return null
  if (answer.supported === false || answer.status === 'unsupported') return 'unsupported'
  if (answer.status === 'insufficient_information') return 'insufficient'
  return answer.items && answer.items.length ? 'answered' : 'empty'
}
