/**
 * EVOSYS PRO TEAM APP — PURE HELPERS (no DOM, no storage, no imports).
 * Tested under plain node: frontend/tests/teamApp.test.mjs
 *
 * Times arrive from the server as UTC ISO strings ("2026-11-16T14:05:00Z")
 * together with the IANA time zone the server chose for this person. Every
 * display goes through Intl with that zone, so a phone set to another zone
 * still shows EvoSys time, and daylight saving is handled by the platform's
 * own time-zone data rather than by arithmetic here.
 */

export const DEFAULT_TZ = 'America/Chicago'

/** The words used everywhere in the app. One list, so screens never drift. */
export const LABELS = {
  programs: 'Programs', chat: 'Chat', time: 'Time', account: 'Account',
  clockIn: 'Clock In', clockOut: 'Clock Out', myTime: 'My Time', teamTime: 'Team Time',
  salesTeam: 'Sales Team', announcements: 'Company Announcements',
  programAccess: 'Program Access', teamChat: 'Team Chat',
}

export function pad2(n) { return String(n).padStart(2, '0') }

/** 3725 -> "1h 02m"; 59 -> "0h 00m"; null -> "—". */
export function fmtHours(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '—'
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 3600)}h ${pad2(Math.floor((s % 3600) / 60))}m`
}

/** Live elapsed clock: 3725 -> "1:02:05". */
export function fmtElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  return `${Math.floor(s / 3600)}:${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`
}

export function parseUtc(iso) {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(d.getTime()) ? null : d
}

function parts(date, tz) {
  const f = new Intl.DateTimeFormat('en-US', {
    timeZone: tz || DEFAULT_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  })
  const o = {}
  for (const p of f.formatToParts(date)) o[p.type] = p.value
  return o
}

/** "10:24 AM" in the given zone. */
export function fmtTime(iso, tz) {
  const d = parseUtc(iso)
  if (!d) return ''
  return new Intl.DateTimeFormat('en-US', { timeZone: tz || DEFAULT_TZ, hour: 'numeric', minute: '2-digit' }).format(d)
}

/** "Mon, Nov 16" in the given zone. */
export function fmtDate(iso, tz) {
  const d = parseUtc(iso)
  if (!d) return ''
  return new Intl.DateTimeFormat('en-US', { timeZone: tz || DEFAULT_TZ, weekday: 'short', month: 'short', day: 'numeric' }).format(d)
}

/** "2026-11-16" (a local calendar date string) -> "Mon, Nov 16". */
export function fmtLocalDay(ymd) {
  if (!ymd) return ''
  const [y, m, d] = ymd.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
    .format(new Date(Date.UTC(y, m - 1, d)))
}

/** The local calendar date ("2026-11-16") of a UTC instant in a zone. */
export function localYmd(iso, tz) {
  const d = parseUtc(iso)
  if (!d) return ''
  const p = parts(d, tz)
  return `${p.year}-${p.month}-${p.day}`
}

/** Value for <input type="datetime-local"> showing a UTC instant in a zone. */
export function toLocalInput(iso, tz) {
  const d = parseUtc(iso)
  if (!d) return ''
  const p = parts(d, tz)
  return `${p.year}-${p.month}-${p.day}T${p.hour === '24' ? '00' : p.hour}:${p.minute}`
}

/** Chat list time: today -> "10:24 AM", otherwise "Nov 12". */
export function fmtListTime(iso, tz, nowIso) {
  const d = parseUtc(iso)
  if (!d) return ''
  const today = localYmd(nowIso || new Date().toISOString(), tz)
  if (localYmd(iso, tz) === today) return fmtTime(iso, tz)
  return new Intl.DateTimeFormat('en-US', { timeZone: tz || DEFAULT_TZ, month: 'short', day: 'numeric' }).format(d)
}

/** Add days to a "YYYY-MM-DD" date. */
export function addDays(ymd, n) {
  const [y, m, d] = ymd.split('-').map(Number)
  const t = new Date(Date.UTC(y, m - 1, d + n))
  return `${t.getUTCFullYear()}-${pad2(t.getUTCMonth() + 1)}-${pad2(t.getUTCDate())}`
}

/** Seconds elapsed since an ISO instant, measured against the SERVER's now
 *  (server_now + how long ago we received it), never the phone's clock alone. */
export function elapsedSince(startIso, serverNowIso, receivedAtMs, nowMs) {
  const start = parseUtc(startIso); const sn = parseUtc(serverNowIso)
  if (!start || !sn) return 0
  return Math.max(0, Math.floor((sn.getTime() + ((nowMs ?? Date.now()) - (receivedAtMs ?? Date.now())) - start.getTime()) / 1000))
}

export function initials(name) {
  return String(name || '').split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?'
}

/** The @word being typed at the caret, or null. "Hi @mi|" -> {query:'mi', start:3}. */
export function mentionQuery(text, caret) {
  const before = String(text || '').slice(0, caret ?? String(text || '').length)
  const m = /(^|\s)@([A-Za-z][\w.'-]*)?$/.exec(before)
  if (!m) return null
  return { query: (m[2] || '').toLowerCase(), start: before.length - (m[2] || '').length - 1 }
}

export function mentionMatches(people, query, limit = 5) {
  const q = (query || '').toLowerCase()
  return (people || []).filter(p => !q || String(p.name).toLowerCase().split(/\s+/).some(w => w.startsWith(q))
    || String(p.name).toLowerCase().startsWith(q)).slice(0, limit)
}

/** Insert "@Full Name " in place of the partial mention. */
export function insertMention(text, q, person) {
  const s = String(text || '')
  const end = q.start + 1 + q.query.length
  return s.slice(0, q.start) + '@' + person.name + ' ' + s.slice(end)
}

/** The people actually named in the final text (a removed mention is dropped). */
export function mentionedIds(text, picked) {
  const s = String(text || '')
  return [...new Set((picked || []).filter(p => s.includes('@' + p.name)).map(p => p.id))]
}

/** Split a message body into text and @mention pieces for display. */
export function bodyPieces(body, names) {
  const s = String(body || '')
  const list = (names || []).filter(Boolean).sort((a, b) => b.length - a.length)
  if (!list.length) return [{ text: s }]
  const esc = list.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp('@(' + esc.join('|') + ')', 'g')
  const out = []; let last = 0; let m
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) })
    out.push({ mention: m[1] })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ text: s.slice(last) })
  return out
}

/** Merge newly polled messages into the list without duplicates, in order. */
export function mergeMessages(current, incoming) {
  const byId = new Map((current || []).map(m => [m.id, m]))
  for (const m of incoming || []) byId.set(m.id, m)
  return [...byId.values()].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
}

export function badge(n) {
  if (!n || n < 1) return ''
  return n > 99 ? '99+' : String(n)
}

export function fileSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return Math.round(bytes / 1024) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

/** Which bottom tabs this person sees. Programs and Account always; Chat and
 *  Time only when the server turned them on. */
export function tabsFor(access) {
  const a = access || {}
  return [
    { key: 'programs', to: '/app/programs', label: LABELS.programs, icon: 'layers' },
    a.chat ? { key: 'chat', to: '/app/chat', label: LABELS.chat, icon: 'chat' } : null,
    a.time ? { key: 'time', to: '/app/time', label: LABELS.time, icon: 'clock' } : null,
    { key: 'account', to: '/app/account', label: LABELS.account, icon: 'user' },
  ].filter(Boolean)
}

/** A safe in-app path to return to after sign-in. */
export function safeAppPath(p) {
  return typeof p === 'string' && /^\/app(\/[\w\-/]*)?$/.test(p) ? p : '/app/programs'
}

export const TEAM_RIGHTS = [
  { key: 'team_chat', label: 'Chat', help: 'DMs, Sales Team and Company Announcements' },
  { key: 'team_time', label: 'Time', help: 'Clock In, Clock Out and My Time' },
  { key: 'team_announce', label: 'Post announcements', help: 'Publish Company Announcements' },
  { key: 'team_chat_manage', label: 'Manage chat rooms', help: 'Add and remove room members. No access to DMs.' },
  { key: 'team_time_manage', label: 'Team Time', help: 'Review and correct the time of assigned people' },
]
