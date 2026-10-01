/**
 * MOBILE SHELL — PURE HELPERS.
 *
 * Imports nothing, touches no DOM and no storage, so every rule in here runs
 * under plain node (tests/frontend/mobileHelpers.test.mjs). The screens in
 * this folder render what these functions decide.
 *
 * THREE RULES THE MOBILE SHELL INHERITS FROM THE DESKTOP APP:
 *   1. It invents no data. Every number and row comes from an existing API
 *      response; a missing value renders as "Not yet available", never 0.
 *   2. It decides nothing about permission. Consent, DNC, quiet hours and
 *      sender readiness are decided by the server's compose gate
 *      (GET /communications/compose-gate, POST /communications/send). The
 *      phone only DISPLAYS the server's refusal reasons.
 *   3. No customer is named here. A skin is chosen from the workspace's own
 *      branding row (industry, colours, platform offer), so two customers in
 *      the same vertical look like their vertical, and a customer with its
 *      own black-and-gold colours gets a dark, gold-accented personality
 *      without a line of code that knows who they are.
 */

export const NOT_AVAILABLE = 'Not yet available'

// ── Skins ────────────────────────────────────────────────────────────────────
//
// A SKIN IS A PERSONALITY, NOT A PALETTE SWAP. Each sets surface, type,
// corner, density and header treatment through data-mskin on the shell root
// (see mobile.css). The workspace's own brand colours are then layered on as
// --m-brand / --m-accent so two "luxe" customers still wear their own colours.
export const SKINS = {
  PLATFORM: 'platform',   // neutral light shell, platform accent
  LUXE: 'luxe',           // dark surfaces, serif display type, thin accent rules
  ENERGY: 'energy',       // the retail-energy vertical skin: light, navy, blue/cyan, bold header bar
  WHOLESALE: 'wholesale', // dense, square, high-contrast operator console
}

const WHOLESALE_INDUSTRIES = new Set(['wholesale_real_estate', 'wholesale', 'real_estate_wholesale'])
// Same key as auth/workspaceRules.js WHOLESALE_FEATURE (kept literal so this
// module imports nothing); tests/frontend/mobileShell.test.mjs pins equality.
export const WHOLESALE_FEATURE = 'wholesale_real_estate'

function normHex(hex) {
  if (typeof hex !== 'string') return null
  let h = hex.trim().replace(/^#/, '')
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null
  return '#' + h.toLowerCase()
}

/** Relative luminance 0..1 (WCAG), or null for an unusable colour. */
export function luminance(hex) {
  const h = normHex(hex)
  if (!h) return null
  const ch = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
    .map(c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)))
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

/**
 * Which personality this workspace wears on mobile.
 *
 * Order matters:
 *   1. the VERTICAL (branding.industry) — energy has an approved design;
 *   2. a WHOLESALE workspace. GET /branding/org normalizes industry through
 *      industry_templates.normalize, so "wholesale_real_estate" arrives as
 *      "real_estate" — which Harmony-style real-estate advisory shares. A
 *      real-estate workspace is a wholesale operation only when the server
 *      says its modules include `wholesale_real_estate` (the entitlement key
 *      auth/workspaceRules.js names WHOLESALE_FEATURE). A raw wholesale
 *      industry, or standing in the /m/wholesale module path, also counts;
 *   3. the workspace's OWN colours — a near-black primary is a dark brand,
 *      and gets the luxe personality with its accent as the highlight;
 *   4. otherwise the platform's neutral shell.
 */
export function mobileSkin(branding, { pathname = '' } = {}) {
  const industry = String((branding && branding.industry) || '').trim().toLowerCase()
  if (industry === 'energy') return SKINS.ENERGY
  const feats = branding && Array.isArray(branding.enabled_features) ? branding.enabled_features : []
  if (WHOLESALE_INDUSTRIES.has(industry)
      || (industry === 'real_estate' && feats.includes(WHOLESALE_FEATURE))
      || /^\/m\/wholesale(\/|$)/.test(pathname)) return SKINS.WHOLESALE
  const lum = luminance(branding && branding.brand_color_primary)
  if (lum !== null && lum < 0.06) return SKINS.LUXE
  return SKINS.PLATFORM
}

/**
 * CSS custom properties the shell root carries: the workspace's own colours,
 * or nothing (the skin's defaults apply). Never another workspace's colours —
 * an absent colour is absent.
 */
export function brandVars(branding) {
  const out = {}
  const p = normHex(branding && branding.brand_color_primary)
  const a = normHex(branding && branding.brand_color_accent)
  if (p) out['--m-brand'] = p
  if (a) out['--m-accent'] = a
  // A dark primary needs light text on it; a light one needs dark text.
  const lum = luminance(p)
  if (lum !== null) out['--m-on-brand'] = lum < 0.4 ? '#ffffff' : '#0b1220'
  return out
}

/** The workspace's display name, from its own branding row. */
export function brandName(branding, identity) {
  return (identity && identity.display_name)
    || (branding && branding.brand_name)
    || null
}

// ── Time ─────────────────────────────────────────────────────────────────────

/**
 * Milliseconds for a server timestamp, or NaN.
 *
 * Several existing endpoints (/communications/replies, /work/tasks) serialize
 * naive UTC datetimes with no zone ("2026-10-01T10:53:53"), while others
 * (/pipeline/appointments) append "Z". `Date.parse` reads a zone-less ISO
 * date-time as LOCAL time, which made a task due two hours ago read "in 2h"
 * in Dallas. The server stores UTC, so a zone-less value is UTC.
 */
export function parseTs(iso) {
  if (!iso || typeof iso !== 'string') return NaN
  const s = iso.trim()
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) return Date.parse(s + 'Z')
  return Date.parse(s)
}

// ── Needs Attention ──────────────────────────────────────────────────────────

/**
 * The Home screen's priority list, merged from three EXISTING answers:
 *   replies  — GET /communications/replies?needs_attention=true
 *   tasks    — GET /work/tasks?status=open
 *   appts    — GET /pipeline/appointments
 *
 * Ranking: hot replies, then overdue tasks, then other attention replies,
 * then today's appointments, then tasks due today. Within a rank, oldest
 * waiting first for replies/tasks and soonest first for appointments.
 * Nothing is invented: an item with no timestamp sorts last in its rank.
 */
export function needsAttention({ replies = [], tasks = [], appointments = [] } = {}, now = new Date()) {
  const items = []
  const nowMs = now.getTime()
  const dayEnd = new Date(now); dayEnd.setHours(23, 59, 59, 999)
  for (const r of replies || []) {
    if (!r || !r.needs_attention && !r.is_hot) continue
    items.push({
      kind: 'reply', id: 'reply:' + r.id, rank: r.is_hot ? 0 : 2,
      at: r.received_at || null, title: r.contact_name || NOT_AVAILABLE,
      detail: r.body || '', hot: !!r.is_hot, leadId: r.lead_id,
      href: r.lead_id ? '/m/conversations/' + r.lead_id : null,
      badge: r.is_hot ? 'Hot' : (r.classification ? humanize(r.classification) : 'Reply'),
    })
  }
  for (const t of tasks || []) {
    if (!t || t.status !== 'open') continue
    const due = parseTs(t.due_at)
    const overdue = !!t.overdue || (!isNaN(due) && due < nowMs)
    const today = !isNaN(due) && due <= dayEnd.getTime()
    if (!overdue && !today) continue
    items.push({
      kind: 'task', id: 'task:' + t.id, rank: overdue ? 1 : 4, at: t.due_at || null,
      title: t.title, detail: t.lead_name || '', leadId: t.lead_id,
      href: t.lead_id ? '/m/contacts/' + t.lead_id : '/m/tasks',
      badge: overdue ? 'Overdue' : 'Due today',
    })
  }
  for (const a of appointments || []) {
    if (!a || a.status === 'cancelled' || !a.booked_time) continue
    const t = parseTs(a.booked_time)
    if (isNaN(t) || t < nowMs || t > dayEnd.getTime()) continue
    items.push({
      kind: 'appointment', id: 'appt:' + a.id, rank: 3, at: a.booked_time,
      title: a.lead_name || NOT_AVAILABLE, detail: a.appointment_type || 'Appointment',
      leadId: a.lead_id, href: a.lead_id ? '/m/contacts/' + a.lead_id : '/m/appointments',
      badge: 'Today',
    })
  }
  items.sort((x, y) => {
    if (x.rank !== y.rank) return x.rank - y.rank
    const xa = isNaN(parseTs(x.at)) ? Infinity : parseTs(x.at)
    const ya = isNaN(parseTs(y.at)) ? Infinity : parseTs(y.at)
    return xa === ya ? 0 : (xa < ya ? -1 : 1)
  })
  return items
}

// ── Composer refusal ─────────────────────────────────────────────────────────

/**
 * The reasons a message may not be sent, as the SERVER stated them.
 *
 * Accepts either the gate object from GET /communications/compose-gate
 * ({allowed, reasons:[{code,label}]}) or an Error thrown by api.post for a
 * 409 from POST /communications/send (err.detail = {message, reasons}).
 * Returns [] only when the server said allowed. An unreadable answer is a
 * refusal ("could not be confirmed"), never a silent allow.
 */
export function refusalReasons(gateOrError) {
  if (!gateOrError) return [{ code: 'UNKNOWN', label: 'Sending could not be confirmed.' }]
  if (gateOrError instanceof Error || gateOrError.detail !== undefined || gateOrError.status !== undefined) {
    const d = gateOrError.detail
    if (d && Array.isArray(d.reasons) && d.reasons.length) return d.reasons.map(normReason)
    const msg = (d && typeof d === 'object' && d.message) || (typeof d === 'string' && d) || gateOrError.message
    return [{ code: 'SEND_REFUSED', label: msg || 'The message was not sent.' }]
  }
  if (gateOrError.allowed === true) return []
  if (Array.isArray(gateOrError.reasons) && gateOrError.reasons.length) return gateOrError.reasons.map(normReason)
  return [{ code: 'UNKNOWN', label: 'Sending could not be confirmed.' }]
}

function normReason(r) {
  if (typeof r === 'string') return { code: 'SEND_REFUSED', label: r }
  return { code: (r && r.code) || 'SEND_REFUSED', label: (r && (r.label || r.message)) || 'Not allowed.' }
}

/** Can the quick-reply box be used at all? Only when the server said so. */
export function canCompose(gate, { observing = false } = {}) {
  if (observing) return false
  return !!(gate && gate.allowed === true)
}

// ── Presentation ─────────────────────────────────────────────────────────────

export function humanize(key) {
  if (!key) return ''
  const s = String(key).replace(/[_-]+/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** "just now", "5m", "3h", "2d", or a short date. null-safe. */
export function relTime(iso, now = new Date()) {
  if (!iso) return ''
  const t = parseTs(iso)
  if (isNaN(t)) return ''
  const diff = Math.round((now.getTime() - t) / 1000)
  const abs = Math.abs(diff)
  const fut = diff < 0
  let s
  if (abs < 60) return fut ? 'in <1m' : 'just now'
  if (abs < 3600) s = Math.floor(abs / 60) + 'm'
  else if (abs < 86400) s = Math.floor(abs / 3600) + 'h'
  else if (abs < 86400 * 7) s = Math.floor(abs / 86400) + 'd'
  else return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return fut ? 'in ' + s : s
}

/**
 * Group appointments by local calendar day: [{key, label, items}] in time
 * order. Cancelled ones stay (the server returned them) but are flagged.
 */
export function groupByDay(appointments, now = new Date()) {
  const todayKey = dayKey(now)
  const tmr = new Date(now); tmr.setDate(tmr.getDate() + 1)
  const tomorrowKey = dayKey(tmr)
  const groups = new Map()
  const sorted = [...(appointments || [])].filter(a => a && a.booked_time)
    .sort((a, b) => parseTs(a.booked_time) - parseTs(b.booked_time))
  for (const a of sorted) {
    const d = new Date(parseTs(a.booked_time))
    const key = dayKey(d)
    if (!groups.has(key)) {
      const label = key === todayKey ? 'Today' : key === tomorrowKey ? 'Tomorrow'
        : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
      groups.set(key, { key, label, items: [] })
    }
    groups.get(key).items.push(a)
  }
  return [...groups.values()]
}

function dayKey(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
}

/** Workspace choices from GET /auth/my-contexts, active one marked. */
export function workspaceChoices(contexts, activeId) {
  const list = (contexts && contexts.workspace_contexts) || []
  return list.map(ws => ({
    id: ws.organization_id,
    name: ws.organization_name || NOT_AVAILABLE,
    role: ws.role || null,
    active: !!activeId && ws.organization_id === activeId,
  }))
}

/** Badge text for a count the server returned; '' for zero/unknown. */
export function badgeCount(n) {
  if (typeof n !== 'number' || !isFinite(n) || n <= 0) return ''
  return n > 99 ? '99+' : String(n)
}

// First name for the greeting. Seeded / sandbox accounts are named
// "DEMO Owner Morgan Hale" or "Erin Admin (QA)"; the label is not a name.
const NAME_LABELS = new Set(['demo', 'test', 'qa', 'sandbox', '(qa)', '(demo)', '(test)', 'owner', 'admin'])
export function greetingName(full) {
  const words = String(full || '').split(/\s+/).filter(w => w && !NAME_LABELS.has(w.toLowerCase()))
  return words[0] || null
}

