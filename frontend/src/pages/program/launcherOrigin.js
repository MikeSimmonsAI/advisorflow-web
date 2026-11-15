/**
 * WHICH PROGRAM LAUNCHER THIS TAB CAME FROM.
 *
 * Every workspace screen's "Program Access" / "Switch program" / Back link goes
 * to /workspaces (about thirty links across SCI, Roofing, Insurance, Sales,
 * Acquisition, VOLT...). When a program was opened from the EvoSys Pro mobile
 * app (/app -> Programs), those links must return to the app's Programs tab.
 *
 * So the ORIGIN is remembered once, at the moment a card is opened, and the
 * /workspaces route honours it. Nothing per screen changes, and a person who
 * enters from the regular web launcher (or a direct workspace link) never has
 * an origin set, so their links behave exactly as before.
 *
 * Rules:
 *   - Only exact, known in-app paths are ever stored or returned (ORIGINS).
 *   - sessionStorage: this tab only, gone when the tab or installed app closes.
 *   - Opening a card from the web launcher clears it; arriving back on the
 *     mobile Programs tab clears it.
 *   - Storage failures (private mode, blocked storage) mean "no origin".
 *
 * Imports nothing, so it runs under plain node for tests.
 */
export const MOBILE_PROGRAMS = '/app/programs'
const ORIGINS = new Set([MOBILE_PROGRAMS])
const KEY = 'af_launcher_origin'

function store(s) {
  if (s) return s
  try { return typeof window !== 'undefined' ? window.sessionStorage : null } catch { return null }
}

/** A launcher origin is accepted only when it is one of the known in-app paths. */
export function safeOrigin(raw) {
  return typeof raw === 'string' && ORIGINS.has(raw) ? raw : null
}

export function setLauncherOrigin(path, s) {
  const st = store(s)
  const ok = safeOrigin(path)
  try {
    if (!st) return
    if (ok) st.setItem(KEY, ok)
    else st.removeItem(KEY)
  } catch { /* storage blocked: behave as web */ }
}

export function clearLauncherOrigin(s) { setLauncherOrigin(null, s) }

/** Where /workspaces should send this tab instead, or null for the web launcher. */
export function launcherOrigin(s) {
  const st = store(s)
  try { return st ? safeOrigin(st.getItem(KEY)) : null } catch { return null }
}
