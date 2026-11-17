/**
 * EVOSYS PRO MOBILE - THE PROGRAM WORKSPACE (pure rules, node-testable).
 *
 * Tapping a program in the app opens a light phone workspace for it:
 *   /app/p/<pid>                 Dashboard (default)
 *   /app/p/<pid>/replies         Replies (programs with a reply queue)
 *   /app/p/<pid>/replies/<lead>  one conversation, answered in place
 *   /app/p/<pid>/more            More: Open Full Workspace and the rest
 *
 * <pid> is the launcher's own programKey(item), base64url-encoded so the link
 * survives sign-in (safeAppPath only lets plain URL characters through) and a
 * home-screen bookmark. It is only a NAME for the entry: the entry itself is
 * looked up again in this login's /auth/my-contexts answer, so a link to a
 * program this person cannot open finds nothing, and every request is
 * re-authorized on the server.
 *
 * Imports only pure modules.
 */
import { programKey } from '../pages/program/programAccessModel.js'

export const PROGRAM_BASE = '/app/p/'

function b64url(s) {
  const bytes = new TextEncoder().encode(String(s))
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function unb64url(s) {
  try {
    const t = String(s || '').replace(/-/g, '+').replace(/_/g, '/')
    const bin = atob(t + '==='.slice((t.length + 3) % 4))
    return new TextDecoder().decode(Uint8Array.from(bin, c => c.charCodeAt(0)))
  } catch { return null }
}

export function programId(item) { return b64url(programKey(item)) }

export function mobileProgramPath(item, tab = '') {
  return PROGRAM_BASE + programId(item) + (tab ? '/' + tab : '')
}

/** The inventory entry a link names, or null (not this login's, or garbled). */
export function findProgram(all, pid) {
  const key = unb64url(pid)
  if (!key) return null
  return (all || []).find(item => programKey(item) === key) || null
}

/** A deal's demo and the Deal Builder tools are not program workspaces: they open as before. */
export function opensDirect(item) {
  return !item || item.type === 'preview' || item.type === 'deal_builder' || item.type === 'project_checklist'
}

/**
 * Which phone workspace an entry gets:
 *   program  a program workspace (SCI, Restland): numbers, Needs a Reply, appointments
 *   sales    the Back Office (sales): follow-ups due, next appointment, demo decisions
 *   other    every other program: its full workspace one tap away
 */
export function mobileKind(item) {
  if (!item) return 'other'
  if ((item.type || 'workspace') === 'workspace' && item.is_program) return 'program'
  if (item.type === 'back_office') return 'sales'
  return 'other'
}

/** Tabs that have a real purpose for this entry. */
export function programTabs(item) {
  const k = mobileKind(item)
  const tabs = [{ key: '', label: 'Dashboard' }]
  if (k === 'program') tabs.push({ key: 'replies', label: 'Replies' })
  tabs.push({ key: 'more', label: 'More' })
  return tabs
}

/** Request options for an entry's own workspace: the server re-derives and checks it. */
export function programRequest(item) {
  const org = item && item.organization_id
  return { skipRedirect: true, asCustomer: org || null, workspaceId: org || null }
}

/** "3" / "99+" for a card count; null when there is nothing to answer. */
export function countLabel(n) {
  const v = Number(n) || 0
  return v <= 0 ? null : (v > 99 ? '99+' : String(v))
}

/** The appointment's local wall time in the program's time zone. */
export function whenLabel(iso, tz = 'America/Chicago', now = new Date()) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const day = x => x.toLocaleDateString('en-US', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' })
  const time = d.toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' })
  const tomorrow = new Date(now.getTime() + 86400000)
  if (day(d) === day(now)) return 'Today ' + time
  if (day(d) === day(tomorrow)) return 'Tomorrow ' + time
  return d.toLocaleDateString('en-US', { timeZone: tz, weekday: 'short', month: 'short', day: 'numeric' }) + ' ' + time
}

/** "5m" / "3h" / "2d" since a reply came in. */
export function ago(iso, now = Date.now()) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.max(0, Math.round((now - t) / 60000))
  if (m < 60) return m + 'm'
  const h = Math.round(m / 60)
  return h < 24 ? h + 'h' : Math.round(h / 24) + 'd'
}

/** A send attempt's idempotency key, fixed for one draft so a retry never double-sends. */
export function draftKey() {
  return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
}
